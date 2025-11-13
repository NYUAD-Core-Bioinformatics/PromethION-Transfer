#!/usr/bin/env python3
import os
import glob
import shutil
import logging
import subprocess
import datetime
from pathlib import Path

# === Environment Variables ===
SOURCE = "/data"
DEST_HOST = "gencoreseq@jubail.abudhabi.nyu.edu"
DEST_PATH = "/archive/gencoreseq/p2"
SSH_KEY = "/home/prom/prom-file-automation-do-not-delete/keys/gen_id_rsa"
SUMMARY_FILE = "final_summary*.txt"
LOG_DIR = "/data/prom_script_logging"


#.env loader file
def load_env_file(path):
    if not os.path.exists(path): return
    for line in open(path):
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_env_file("/home/prom/prom-file-automation-do-not-delete/.env")


#Mail parameters
EMAIL_ENABLED = True
SMTP_USER = os.environ.get('MAIL_USERNAME')
SMTP_PASS = os.environ.get('MAIL_PASSWORD')
SMTP_HOST = os.environ.get('MAIL_SERVER')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 465))
EMAIL_FROM = os.environ.get('EMAIL_FROM')
EMAIL_TO = os.environ.get('EMAIL_TO')



# === Copy Settings ===
DELETE_AFTER_COPY = True
DONE_MARKER = ".rsync_done"
RSYNC = shutil.which("rsync")

# === Logging Setup ===
def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logfile = os.path.join(LOG_DIR, f"rsync_log_{datetime.datetime.now():%Y%m%d-%H%M%S}.log")
    logging.basicConfig(
        filename=logfile,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    logging.info(f"Log file: {logfile}")
    return logfile

# === Email Reporting ===
def email_report(subject, message):
    if not EMAIL_ENABLED:
        logging.info("Email notifications disabled. Skipping email send.")
        return
    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(message)
        msg["From"] = f"PromethION Notification <{EMAIL_FROM}>"
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

        logging.info(f"Email sent successfully: {subject}")

    except Exception as e:
        logging.error(f"Email error: {e}")

# === Find Run Directories ===
def find_rundir():
    results = []
    for d in glob.glob(f"{SOURCE}/*/*/*"):
        if os.path.isdir(d):
            results.append(d)
    return results

# === Create Done Marker ===
def run_mark_done(dest_run_dir):
    Path(dest_run_dir, DONE_MARKER).touch()

# === Rsync Execution (single attempt) ===
def run_rsync(src_dir, dest_parent):
    owner = src_dir.split("/")[-3]
    remote_owner_path = f"/archive/gencoreseq/p2/{owner}"
    ssh_opts = '-o ConnectTimeout=20 -o BatchMode=yes -o StrictHostKeyChecking=no'
    cmd = (
        f'{RSYNC} -avP '
        f'-e "ssh -i {SSH_KEY} {ssh_opts}" '
        f'--rsync-path="mkdir -p {remote_owner_path} && rsync" '
        f'"{src_dir}" "{DEST_HOST}:{dest_parent}"'
    )
    logging.info(f"Running: {cmd}")
    print(f"➡️  Running: {owner}")
    print(f"➡️  Running: {cmd}")

    proc = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )

    if proc.returncode == 0:
        logging.info(f"Rsync succeeded for {src_dir}")
        print(f"✅ Success: {src_dir}")
        return True
    else:
        logging.error(
            f"Rsync failed (exit code {proc.returncode}): "
            f"{proc.stderr.decode(errors='ignore').strip()}"
        )
        print(f"❌ Failed: {src_dir}")
        return False

# === Main ===
def main():
    logfile = setup_logging()
    start = datetime.datetime.now()

    processed = skipped = failed = 0
    processed_runs = []
    failed_runs = []

    for run_dir in find_rundir():
        owner, project, run_id = run_dir.split("/")[-3:]

        # Skip if summary file not found
        if not glob.glob(os.path.join(run_dir, SUMMARY_FILE)):
            skipped += 1
            logging.info(f"Skip: No {SUMMARY_FILE} -> {run_dir}")
            continue

        # Destination path (owner/project)
        dest_parent = os.path.join(DEST_PATH, owner, project)
        dest_run_dir = os.path.join(dest_parent, run_id)

        if run_rsync(run_dir, dest_parent):
            processed += 1
            processed_runs.append(run_dir)
            run_mark_done(run_dir)
            logging.info(f"Done marker created: {run_dir}/{DONE_MARKER}")

            if DELETE_AFTER_COPY:
                shutil.rmtree(run_dir)
                logging.info(f"Deleted source: {run_dir}")
        else:
            failed += 1
            failed_runs.append(run_dir)
            logging.error(f"FAIL - PromethION Transfer for {run_id}")

    # === Final Summary ===
    duration = (datetime.datetime.now() - start).total_seconds()
    duration_sec=round((datetime.datetime.now()- start).total_seconds() / 60, 2)
    summary = (
        "Run Backup Job Summary\n"
        "============================================\n"
        f"Runs successfully backed up   : {processed}\n"
        f"Directories skipped           : {skipped}\n"
        f"Runs Failed (Rsync Error)     : {failed}\n"
        "--------------------------------------------\n"
        f"Duration of rsync operation   : {duration:.1f} minutes\n"
        f"Local Transfer log path       : {logfile}\n"
    )

    if processed_runs:
        summary += "\n✅ Successful Runs:\n" + "\n".join(f" - {r}" for r in processed_runs)
    if failed_runs:
        summary += "\n❌ Failed Runs:\n" + "\n".join(f" - {r}" for r in failed_runs)

    logging.info("\n" + summary)
    print("\n" + summary)

    # === Send a single summary email.
    if processed > 0 or failed > 0:
        if failed == 0:
            subject = f"SUCCESS: PromethION Transfer"
        else:
            subject = f"FAILURE: PromethION Transfer"
        email_report(subject, summary)
    else:
        logging.info("No new transfers processed — email notification skipped.")
        print("No new transfers processed — no email sent.")

if __name__ == "__main__":
    main()