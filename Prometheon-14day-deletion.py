#!/usr/bin/env python3

import datetime
import glob
import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path


# === Paths and retention ===
SOURCE = "/data"
DEST_HOST = "gencoreseq@jubail.abudhabi.nyu.edu"
DEST_PATH = "/archive/gencoreseq/p2"
SSH_KEY = "/home/prom/prom-file-automation-do-not-delete/keys/gen_id_rsa"
SUMMARY_FILE = "final_summary*.txt"
LOG_DIR = "/data/prom_script_logging"

# The retention period starts only after rsync succeeds.
RETENTION_DAYS = 14
TRANSFER_MARKER = ".promethion_rsync_completed"


# === .env loader ===
def load_env_file(path):
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


load_env_file("/home/prom/prom-file-automation-do-not-delete/.env")


# === Email settings ===
EMAIL_ENABLED = True
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_TO = [
    address.strip()
    for address in os.environ.get("EMAIL_TO", "").split(",")
    if address.strip()
]


RSYNC = shutil.which("rsync")


# === Logging ===
def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logfile = os.path.join(
        LOG_DIR,
        f"rsync_log_{datetime.datetime.now():%Y%m%d-%H%M%S}.log",
    )
    logging.basicConfig(
        filename=logfile,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("Log file: %s", logfile)
    return logfile


# === Email reporting ===
def email_report(subject, message):
    if not EMAIL_ENABLED:
        logging.info("Email notifications disabled")
        return

    if not all([SMTP_USER, SMTP_PASS, SMTP_HOST, EMAIL_FROM, EMAIL_TO]):
        logging.error("Email is enabled, but one or more email settings are missing")
        return

    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(message)
        msg["From"] = f"CTP PromethION Notification <{EMAIL_FROM}>"
        msg["To"] = ", ".join(EMAIL_TO)
        msg["Subject"] = subject

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

        logging.info("Email sent successfully: %s", subject)
    except Exception:
        logging.exception("Email error")


# === Run discovery ===
def find_rundirs():
    pattern = os.path.join(SOURCE, "*", "*", "*")
    return sorted(path for path in glob.glob(pattern) if os.path.isdir(path))


# === Transfer marker and deletion safety ===
def marker_path(run_dir):
    return Path(run_dir) / TRANSFER_MARKER


def create_transfer_marker(run_dir):
    marker = marker_path(run_dir)
    completed_at = datetime.datetime.now(datetime.timezone.utc)
    marker.write_text(
        f"Successful rsync completed at {completed_at.isoformat()}\n",
        encoding="utf-8",
    )
    logging.info("Created transfer marker: %s", marker)


def marker_age_days(run_dir):
    age_seconds = max(0, time.time() - marker_path(run_dir).stat().st_mtime)
    return age_seconds / 86400


def safely_delete_run(run_dir):
    source_path = Path(SOURCE).resolve()
    original_run_path = Path(run_dir)

    if original_run_path.is_symlink():
        raise RuntimeError(f"Refusing to delete symbolic link: {original_run_path}")

    resolved_run_path = original_run_path.resolve(strict=True)

    try:
        relative_parts = resolved_run_path.relative_to(source_path).parts
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing to delete path outside {source_path}: {resolved_run_path}"
        ) from exc

    # The target must be exactly /data/<owner>/<project>/<run>.
    if len(relative_parts) != 3:
        raise RuntimeError(
            f"Refusing to delete unexpected path depth: {resolved_run_path}"
        )

    marker = resolved_run_path / TRANSFER_MARKER
    if not marker.is_file() or marker.is_symlink():
        raise RuntimeError(
            f"Refusing to delete directory without a valid marker: {resolved_run_path}"
        )

    if marker_age_days(resolved_run_path) < RETENTION_DAYS:
        raise RuntimeError(
            f"Refusing to delete before {RETENTION_DAYS} full days: {resolved_run_path}"
        )

    shutil.rmtree(resolved_run_path)
    logging.info("Safely deleted run directory: %s", resolved_run_path)


# === Rsync execution ===
def run_rsync(src_dir, dest_parent):
    owner = Path(src_dir).parts[-3]
    remote_owner_path = f"{DEST_PATH}/{owner}"

    ssh_command = (
        f"ssh -i {shlex.quote(SSH_KEY)} "
        "-o ConnectTimeout=20 "
        "-o BatchMode=yes "
        "-o StrictHostKeyChecking=no"
    )
    remote_rsync_command = (
        f"mkdir -p {shlex.quote(remote_owner_path)} && rsync"
    )

    command = [
        RSYNC,
        "-avP",
        "-e",
        ssh_command,
        f"--rsync-path={remote_rsync_command}",
        src_dir,
        f"{DEST_HOST}:{dest_parent}",
    ]

    logging.info("Running rsync for: %s", src_dir)
    print(f"Transferring: {src_dir}")

    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
    )

    if proc.returncode == 0:
        logging.info("Rsync succeeded for %s", src_dir)
        print(f"Transfer successful: {src_dir}")
        return True

    logging.error(
        "Rsync failed for %s (exit code %s): %s",
        src_dir,
        proc.returncode,
        proc.stderr.strip(),
    )
    print(f"Transfer failed: {src_dir}")
    return False


# === Main ===
def main():
    logfile = setup_logging()
    start = datetime.datetime.now()

    if not RSYNC:
        message = "rsync was not found in PATH; no directories were processed"
        logging.error(message)
        print(message)
        email_report("FAILURE: PromethION Transfer", message)
        return 1

    transferred = 0
    waiting = 0
    deleted = 0
    skipped = 0
    failed = 0
    transfer_attempted = False

    transferred_runs = []
    waiting_runs = []
    deleted_runs = []
    failed_runs = []

    for run_dir in find_rundirs():
        run_path = Path(run_dir)
        owner, project, run_id = run_path.parts[-3:]
        marker = marker_path(run_dir)

        # A marker means this directory was already transferred successfully.
        if marker.is_file() and not marker.is_symlink():
            age_days = marker_age_days(run_dir)

            if age_days >= RETENTION_DAYS:
                try:
                    safely_delete_run(run_dir)
                    deleted += 1
                    deleted_runs.append(run_dir)
                    print(f"Deleted after {age_days:.1f} days: {run_dir}")
                except Exception:
                    failed += 1
                    failed_runs.append(run_dir)
                    logging.exception("Unable to safely delete %s", run_dir)
                    print(f"Deletion failed safety checks: {run_dir}")
            else:
                waiting += 1
                waiting_runs.append(run_dir)
                remaining = RETENTION_DAYS - age_days
                logging.info(
                    "Retention pending for %s: %.2f days remaining",
                    run_dir,
                    remaining,
                )
                print(
                    f"Retaining {run_dir}; approximately "
                    f"{remaining:.1f} days remaining"
                )

            # Never rsync again after a valid marker exists.
            continue

        # A run is ready for transfer only after its final summary appears.
        if not glob.glob(os.path.join(run_dir, SUMMARY_FILE)):
            skipped += 1
            logging.info("Skip: no %s in %s", SUMMARY_FILE, run_dir)
            continue

        dest_parent = os.path.join(DEST_PATH, owner, project)

        # Email reporting is tied only to discovering an unmarked run that has
        # a final summary and therefore causes an rsync attempt.
        transfer_attempted = True

        if run_rsync(run_dir, dest_parent):
            try:
                # The 14-day retention clock begins here.
                create_transfer_marker(run_dir)
                transferred += 1
                transferred_runs.append(run_dir)
            except Exception:
                failed += 1
                failed_runs.append(run_dir)
                logging.exception(
                    "Rsync succeeded but marker creation failed for %s", run_dir
                )
        else:
            failed += 1
            failed_runs.append(run_dir)
            logging.error("FAIL - PromethION transfer for %s", run_id)

    duration = round(
        (datetime.datetime.now() - start).total_seconds() / 60,
        2,
    )

    summary = (
        "Run Backup Job Summary\n"
        "============================================\n"
        f"New runs successfully backed up : {transferred}\n"
        f"Runs awaiting 14-day deletion   : {waiting}\n"
        f"Run directories deleted         : {deleted}\n"
        f"Directories without summary     : {skipped}\n"
        f"Failed operations               : {failed}\n"
        "--------------------------------------------\n"
        f"Job duration                    : {duration:.1f} minutes\n"
        f"Local log path                  : {logfile}\n"
    )

    if transferred_runs:
        summary += "\nSuccessful new transfers:\n" + "\n".join(
            f" - {run}" for run in transferred_runs
        )
    if deleted_runs:
        summary += "\nDeleted run directories:\n" + "\n".join(
            f" - {run}" for run in deleted_runs
        )
    if waiting_runs:
        summary += "\nRuns in retention period:\n" + "\n".join(
            f" - {run}" for run in waiting_runs
        )
    if failed_runs:
        summary += "\nFailed operations:\n" + "\n".join(
            f" - {run}" for run in failed_runs
        )

    logging.info("\n%s", summary)
    print(f"\n{summary}")

    # Email only when at least one new, unmarked run containing a final summary
    # caused an rsync attempt. Waiting, skipped, and deletion-only cron runs do
    # not generate email.
    if transfer_attempted:
        subject = (
            "SUCCESS: PromethION Transfer"
            if not failed_runs
            else "FAILURE: PromethION Transfer"
        )
        email_report(subject, summary)
    else:
        logging.info("No new run containing a final summary; email skipped")
        print("No new run containing a final summary - no email sent.")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

