# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

kiro-gdrive mounts shared Google Drive folders **read-only** from a share link. It is a GTK4 Python app with a CLI
over the same core, driving `rclone` and a systemd user unit. It is per-user (no sudo, no pkexec; never add root
escalation) and ships in `nemesis_repo`.

- **Entry point**: `usr/share/kiro-gdrive/kiro-gdrive.py`. No args opens the GUI, a subcommand runs the CLI. GTK
  is imported only on the GUI path so the CLI works over SSH.
- **Launcher**: `usr/bin/kiro-gdrive`; `usr/bin/kgd` is a symlink to it.
- **Mount unit**: `usr/lib/systemd/user/kiro-gdrive@.service`
- **Desktop entry**: `usr/share/applications/kiro-gdrive.desktop`

## Architecture

```
usr/share/kiro-gdrive/
├── kiro-gdrive.py   # entry: CLI dispatch or Gtk.Application
├── kgd_core.py      # toolkit-free core: sign-in, rclone remotes, systemd mounts
├── kgd_cli.py       # argparse front-end
├── kgd_gui.py       # GTK4 window: account row, add row, share list
├── client.json      # shipped Google OAuth client (Desktop app)
├── log.py           # logging helpers (same as fish-tweak-tool)
└── kgd.css
```

`kgd_core` returns `Result(ok, message)` from every action and never raises into the UI. The GUI runs actions with
`kgd_core.run_async` (daemon thread) and returns to the main loop with `GLib.idle_add`.

## Do not revert

- **Private rclone config.** Every rclone call goes through `_rclone()`, which passes
  `--config ~/.config/kiro-gdrive/rclone.conf`. Never write to the user's `~/.config/rclone/rclone.conf`.
- **Read-only everywhere**: `--drive-scope drive.readonly` at sign-in, `scope drive.readonly` on each remote,
  `--read-only` on the mount. `login()` checks the granted scope via Google's tokeninfo endpoint and revokes and
  refuses anything wider.
- **Labels** are `[A-Za-z0-9._-]` only (`clean_label`). They are rclone remote names, systemd instance names
  (`%i`) and mount directory names at once, so no escaping is needed anywhere.
- **`MOUNT_ROOT` in `kgd_core.py` and `%h/GoogleDrive` in the unit must match.**
- **No `network-online.target` in the user unit**: the user manager can't see that system target.
  `Restart=on-failure` covers a login that races the network.
- **New-file feed:** `kiro-gdrive-check.timer` (daily, enabled for every user via the
  `etc/systemd/user/timers.target.wants/` symlink, like kiro-news) runs `kiro-gdrive check` → `check_new()`. It
  diffs `rclone lsjson -R` against `~/.cache/kiro-gdrive/seen.json` and writes **one item per share per run** to
  `~/.cache/kiro-gdrive/feed.json`, because kiro-news only notifies for a feed's newest item. kiro-news reads it
  through `etc/kiro-news/sources.d/kiro-gdrive.conf` (`~/` path: needs the kiro-news version that expands `~`). A
  share's first check is a silent baseline. Checksum and `.sig` files are listed in an item but never headline it.
- `mount()` and `set_autostart()` run `systemctl --user daemon-reload` first, so a freshly installed unit is found
  without a re-login.

## OAuth client

- `client.json` holds the kiro-gdrive Google OAuth client, of type **Desktop app**. Google treats a Desktop-app
  secret as non-confidential, so shipping it is normal (rclone ships its own the same way).
- A user's `~/.config/kiro-gdrive/client.json` overrides the shipped one.
- If both are empty, rclone's built-in client is used, which rclone retires during 2026.
- The shipped client lives in Google Cloud project **`kiro-gdrive`** (id `kiro-gdrive-511912`), created 2026-10-17.
  Until then `client.json` shipped empty, so every sign-in silently used rclone's client. Its Audience page shows
  the user count ("N users / 100 user cap"). Privacy policy: https://kiroproject.be/kiro-gdrive-privacy.html.
  Google never shows the secret again: keep the downloaded JSON safe, or add a new secret on the client.
- `drive.readonly` is a Google *restricted* scope. Until the app passes Google verification, users see an
  "unverified app" screen and at most 100 users can sign in.
- The consent screen must be **"In production"**, not "Testing". Google gives a Testing-status app's refresh tokens
  a 7-day lifetime, which would silently break mount-at-login every week. In production but unverified is fine:
  that only means the warning screen and the 100-user cap.

## Development

- `ruff check .` must pass. Max line length 120.
- Public functions get a one-line docstring; `_private` ones don't need one.
- Unused GTK callback params are named `_widget`. Never `subprocess.call()` from a GUI callback.
- To test from a checkout, link the unit:
  `ln -s "$PWD/usr/lib/systemd/user/kiro-gdrive@.service" ~/.config/systemd/user/`, then run
  `systemctl --user daemon-reload`.
- Sign-in over SSH: forward `-L 53682:127.0.0.1:53682`, run `kgd login --no-browser`, and open the printed URL
  on the desktop.
