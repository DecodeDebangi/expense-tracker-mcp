---
name: backup-expenses
description: Back up the expense database to Google Drive. Run only when the user asks.
disable-model-invocation: true
---

Run this command once, from the repository root:

```bash
"$(git rev-parse --show-toplevel)/.venv/bin/expense-tracker-admin" drive-backup
```

The command makes a fresh backup, uploads it with rclone and checks the upload against a checksum.

**Rules**
- Never read, print or attach the database or backup files. They hold private financial data, and only the command's output belongs in this conversation.
- Don't upload through a Google Drive connector.
- Don't run `rclone config` or change rclone settings yourself. Signing in needs the user's browser.

**Reporting**
- On success, give the file name and expense count from the output, and say the upload was verified. Pass on any warning.
- If rclone says the remote isn't found or the login has expired, ask the user to run this in their own terminal, then try again:
  `rclone config create expense-drive drive scope=drive.file`
- For any other failure, show the error and say the backup didn't reach Google Drive.
