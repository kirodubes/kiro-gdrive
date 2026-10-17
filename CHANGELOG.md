# Changelog

All notable changes to kiro-gdrive are documented here. Newest first.

## 2026.10.17

### What Changed
- Initial package: mount shared Google Drive folders read-only from a share link, through a GTK4 window or the
  `kiro-gdrive` / `kgd` command line. A share mounts at `~/GoogleDrive/<name>` and remounts at every login.
- Sign-in asks Google only for `drive.readonly`. After sign-in the granted scope is checked, and a token with
  more than read-only access is revoked and refused.

### Technical Details
- `kgd_core.py` is toolkit-free and holds all logic. Every action returns `Result(ok, message)` and never raises
  into the UI. The GUI runs actions in daemon threads and hands results back with `GLib.idle_add`.
- Uses a private rclone config (`~/.config/kiro-gdrive/rclone.conf`, passed with `--config`), so the user's own
  rclone remotes are never touched.
- Sign-in runs `rclone authorize drive [client_id client_secret] --drive-scope drive.readonly`. The account
  token is stored once in `token.json` (0600) and copied into each remote.
- Each share is a `kiro-gdrive@<label>.service` user unit (`Type=notify`, `rclone mount --read-only
  --vfs-cache-mode full`). Mount at login is `systemctl --user enable`.
- Labels are reduced to `[A-Za-z0-9._-]` because they double as rclone remote names, systemd instance names and
  directory names.
- With no label given, the share is renamed to the folder's Drive name, fetched from the Drive API with the
  remote's freshly refreshed access token.
- The OAuth client comes from `client.json`: a user file in `~/.config/kiro-gdrive/` wins over the shipped one.
  If both are empty, rclone's built-in client is used, which rclone retires during 2026.
- rclone has no `config rename`, so the rename re-creates the remote under the new name with its refreshed token
  and deletes the temporary one (`_copy_remote`).
- The unit heals itself: `ExecStartPre=-fusermount3 -uz` clears a dead FUSE mount left by a crashed rclone
  ("Transport endpoint is not connected"), and `ExecStop` ignores errors. On the test box, a SIGKILLed mount came
  back on its own through `Restart=on-failure`.
- rclone's shared OAuth client regularly hits Google's per-minute quota (`rateLimitExceeded`). The core reports
  that in plain words, retries the folder-name lookup once, and shows the unit's last journal line when a mount
  fails instead of systemd's generic "see journalctl".
- `KGD_DEBUG=1` turns on debug output for CLI runs.

### Files Modified
- `usr/bin/kiro-gdrive`, `usr/bin/kgd` (symlink)
- `usr/share/kiro-gdrive/kiro-gdrive.py`, `kgd_core.py`, `kgd_cli.py`, `kgd_gui.py`, `log.py`, `kgd.css`,
  `client.json`
- `usr/lib/systemd/user/kiro-gdrive@.service`
- `usr/share/applications/kiro-gdrive.desktop`
- `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `LICENSE`, `up.sh`, `setup.sh`, `kiro.jpg`, `.gitignore`, `ruff.toml`
