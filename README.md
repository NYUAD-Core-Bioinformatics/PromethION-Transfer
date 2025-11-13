### PromethION Transfer Automation

This repository describes how to configure the PromethION data transfer automation service on a Linux system.
The transfer script runs as a cron job every 3 days at 12:00 AM, performing automated rsync-based backups of PromethION run directories.

#### Cron Job Configuration

Add the following cron entry to schedule the backup script:

```
0 0 */3 * * /usr/bin/python3 /home/prom/prom-file-automation-do-not-delete/Prometheon-backup-automation.py
```

#### Email Notification Setup

Update the email account credentials in the .env file before running the script.

To disable the email notification, then set ```MAIL_ENABLED = False``` in the script.


#### Sample Success Email

```
Run Backup Job Summary
============================================
Runs successfully backed up   : 2
Directories skipped           : 15
Runs failed (rsync errors)    : 0
--------------------------------------------
Duration of rsync operation   : 229.55 minutes
Local transfer log path       : /data/prom_script_logging/rsync_log_20251113-095202.log


Successfully Backed-Up Runs:
 - /data/ohtan_drna/CTX_ID6/20251005_1040_P2I-00128-B_PBG20200_d21978d7
 - /data/ohtan_drna/CTX_ID5/20251005_1040_P2I-00128-A_PBG25027_ef97245b
```

#### File Deletion 

Once a run directory is successfully copied to the remote HPC $ARCHIVE storage, the source directory is deleted from the local system.

In the example email summary above, only the successfully backed-up run directories will be deleted, such as:

```
Successfully Backed-Up Runs:
 - /data/ohtan_drna/CTX_ID6/20251005_1040_P2I-00128-B_PBG20200_d21978d7
 - /data/ohtan_drna/CTX_ID5/20251005_1040_P2I-00128-A_PBG25027_ef97245b
```

Note: If you do not want the file deletion feature, then set
```DELETE_AFTER_COPY = False```.

#### Transfer Performance

- The average transfer rate is approximately 393 GB per hour,
- Equivalent to roughly 109 MB/s,
- Using rsync over SSH.

