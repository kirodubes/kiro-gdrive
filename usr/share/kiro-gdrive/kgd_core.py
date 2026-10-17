"""Toolkit-free core for kiro-gdrive: Google sign-in, rclone remotes and systemd mounts."""

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import log

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "kiro-gdrive")
# A private rclone config keeps our remotes and tokens out of the user's own ~/.config/rclone/rclone.conf.
RCLONE_CONF = os.path.join(CONFIG_DIR, "rclone.conf")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token.json")
# Must match the mount path in usr/lib/systemd/user/kiro-gdrive@.service.
MOUNT_ROOT = os.path.expanduser("~/GoogleDrive")
SCOPE = "drive.readonly"
_SCOPE_URL = "https://www.googleapis.com/auth/drive.readonly"

_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_ID_PARAM_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
# Labels double as rclone remote names, systemd instance names and directory names,
# so they are kept to characters all three accept without escaping.
_LABEL_BAD_RE = re.compile(r"[^A-Za-z0-9._-]+")


class Result:
    """Outcome of a kiro-gdrive operation."""

    def __init__(self, ok, message=""):
        self.ok = ok
        self.message = message


class Share:
    """One mounted (or mountable) Drive folder."""

    def __init__(self, label, folder_id, active, enabled):
        self.label = label
        self.folder_id = folder_id
        self.active = active
        self.enabled = enabled
        self.path = mount_path(label)
        self.mounted = os.path.ismount(self.path)


def _run(args, timeout=60):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "operation timed out"
    except FileNotFoundError:
        return 127, "", f"{args[0]} not found"


def _rclone(*args, timeout=60):
    return _run(["rclone", "--config", RCLONE_CONF, *args], timeout)


def _systemctl(*args):
    return _run(["systemctl", "--user", *args], 30)


def _unit(label):
    return f"kiro-gdrive@{label}.service"


def _last_line(text):
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "unknown error"


_RATE_LIMITED = "Google is rate-limiting this app's OAuth client; wait a minute and try again"


def _rclone_error(text):
    if "rateLimitExceeded" in text or "userRateLimitExceeded" in text:
        return _RATE_LIMITED
    if "notFound" in text or "Error 404" in text:
        return "the folder does not exist, or this Google account cannot open it"
    # Drop rclone's "2026/10/17 08:26:26 NOTICE: " log prefix.
    return re.sub(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d \w+\s*:\s*", "", _last_line(text))


def _unknown(label):
    if label in _dump():
        return None
    return Result(False, f"No share called '{label}'")


def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)


def mount_path(label):
    """Return the directory a share is mounted on."""
    return os.path.join(MOUNT_ROOT, label)


def have_rclone():
    """Return True when the rclone binary is available."""
    return shutil.which("rclone") is not None


_TERMINALS = ("alacritty", "kitty", "foot", "xterm")
_INSTALL_COMMAND = "sudo pacman -S --needed rclone fuse3"


def install_rclone():
    """Install rclone in a visible terminal, where the user sees the command and types the sudo password."""
    term = next((t for t in _TERMINALS if shutil.which(t)), None)
    if term is None:
        return Result(False, f"No terminal found; install rclone with: {_INSTALL_COMMAND}")
    script = (
        'echo "Kiro Google Drive is running this command on your system:"; echo; '
        f'echo "    {_INSTALL_COMMAND}"; echo; '
        f'{_INSTALL_COMMAND}; '
        'echo; read -rp "Press enter to close. "'
    )
    try:
        subprocess.run([term, "-e", "bash", "-c", script], timeout=1800)
    except (OSError, subprocess.SubprocessError) as err:
        return Result(False, f"Could not start {term}: {err}")
    if have_rclone():
        return Result(True, "rclone installed")
    return Result(False, "rclone is still not installed")


def load_client():
    """Return (client_id, client_secret); a user override in CONFIG_DIR beats the shipped file."""
    for path in (os.path.join(CONFIG_DIR, "client.json"), os.path.join(BASE_DIR, "client.json")):
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if data.get("client_id"):
            return data["client_id"], data.get("client_secret", "")
    return "", ""


def parse_link(text):
    """Return the folder ID from a Drive folder link or bare ID; raise ValueError otherwise."""
    text = text.strip()
    if "/file/d/" in text:
        raise ValueError("That is a link to a single file; only folders can be mounted")
    for regex in (_FOLDER_RE, _ID_PARAM_RE):
        match = regex.search(text)
        if match:
            return match.group(1)
    if _BARE_ID_RE.match(text):
        return text
    raise ValueError("Not a Google Drive folder link")


def clean_label(text):
    """Return text reduced to a safe share label."""
    return _LABEL_BAD_RE.sub("-", text.strip()).strip("-.")


# ── Account ──────────────────────────────────────────────────────────────


def is_signed_in():
    """Return True when a Google token is stored."""
    return os.path.isfile(TOKEN_FILE)


def _load_token():
    try:
        with open(TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _save_token(token):
    _ensure_config_dir()
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token)


def _extract_token(output):
    # rclone prints the token between "--->" and "<---End paste"; newer versions may base64 it.
    match = re.search(r"--->\s*(.+?)\s*<---", output, re.S)
    payload = (match.group(1) if match else output).strip()
    try:
        data = json.loads(payload)
    except ValueError:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    if isinstance(data.get("token"), str):
        data = json.loads(data["token"])
    if "refresh_token" not in data:
        raise ValueError("no refresh token in rclone output")
    return json.dumps(data)


def _granted_scopes(token):
    access = json.loads(token)["access_token"]
    url = "https://oauth2.googleapis.com/tokeninfo?access_token=" + urllib.parse.quote(access)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return set(json.load(resp).get("scope", "").split())


def _revoke(token):
    try:
        refresh = json.loads(token)["refresh_token"]
        data = urllib.parse.urlencode({"token": refresh}).encode()
        urllib.request.urlopen("https://oauth2.googleapis.com/revoke", data=data, timeout=15).close()
        return True
    except Exception:
        return False


def login(no_browser=False):
    """Sign in to Google through rclone and store a read-only token."""
    if not have_rclone():
        return Result(False, "rclone is not installed")
    cmd = ["rclone", "authorize", "drive"]
    cmd += [arg for arg in load_client() if arg]
    cmd += ["--drive-scope", SCOPE]
    if no_browser:
        cmd.append("--auth-no-open-browser")
    # stderr stays on the terminal: that is where rclone prints the sign-in URL.
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return Result(False, "Sign-in timed out")
    if proc.returncode != 0:
        return Result(False, "Sign-in failed or was cancelled")
    try:
        token = _extract_token(proc.stdout)
    except (ValueError, KeyError):
        return Result(False, "Could not read the token rclone returned")
    try:
        scopes = _granted_scopes(token)
    except Exception:
        scopes = set()
    if scopes and scopes != {_SCOPE_URL}:
        _revoke(token)
        return Result(False, f"Google granted more than read-only access ({' '.join(sorted(scopes))}); token revoked")
    _save_token(token)
    return Result(True, "Signed in (read-only)")


def logout():
    """Remove every share, revoke the Google token and forget it."""
    for share in list_shares():
        remove(share.label)
    token = _load_token()
    revoked = _revoke(token) if token else False
    try:
        os.remove(TOKEN_FILE)
    except FileNotFoundError:
        pass
    if token and not revoked:
        return Result(True, "Signed out; Google did not confirm the revoke, check Third-party connections")
    return Result(True, "Signed out")


# ── Shares ───────────────────────────────────────────────────────────────


def _dump():
    rc, out, _err = _rclone("config", "dump")
    if rc != 0:
        return {}
    try:
        return json.loads(out)
    except ValueError:
        return {}


def _folder_name(label, folder_id):
    # The remote's token was refreshed by the access check in add(), so it is safe to borrow here.
    try:
        access = json.loads(_dump()[label]["token"])["access_token"]
    except (KeyError, ValueError):
        return ""
    url = f"https://www.googleapis.com/drive/v3/files/{folder_id}?fields=name&supportsAllDrives=true"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access}"})
    # One retry: a busy OAuth client hits Google's per-minute quota (403/429) in short bursts.
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.load(resp).get("name", "")
        except urllib.error.HTTPError as err:
            log.debug_print(f"folder name lookup failed (attempt {attempt}): {err!r}")
            if err.code not in (403, 429):
                return ""
            time.sleep(3)
        except Exception as err:
            log.debug_print(f"folder name lookup failed: {err!r}")
            return ""
    return ""


def _copy_remote(src, dst):
    # rclone has no "config rename"; re-create under the new name with the current (refreshed) token.
    conf = dict(_dump().get(src, {}))
    remote_type = conf.pop("type", "")
    if not remote_type:
        return False
    args = ["config", "create", dst, remote_type]
    for key, value in conf.items():
        args += [key, value]
    args += ["config_refresh_token=false", "--non-interactive"]
    rc, _out, err = _rclone(*args)
    if rc != 0:
        log.debug_print(f"copy {src} -> {dst} failed: {_last_line(err)}")
    return rc == 0


def list_shares():
    """Return every configured share with its systemd and mount state."""
    shares = []
    for label, conf in sorted(_dump().items()):
        _rc, active, _err = _systemctl("is-active", _unit(label))
        _rc, enabled, _err = _systemctl("is-enabled", _unit(label))
        shares.append(Share(label, conf.get("root_folder_id", ""), active.strip() == "active",
                            enabled.strip() == "enabled"))
    return shares


def add(link, label="", mount=True):
    """Configure a share from a Drive link and (by default) mount it now and at every login."""
    if not have_rclone():
        return Result(False, "rclone is not installed")
    try:
        folder_id = parse_link(link)
    except ValueError as err:
        return Result(False, str(err))
    token = _load_token()
    if not token:
        return Result(False, "Sign in first")
    existing = _dump()
    wanted = clean_label(label)
    temp = wanted or f"share-{folder_id[:8]}"
    if temp in existing:
        return Result(False, f"A share called '{temp}' already exists")

    _ensure_config_dir()
    args = ["config", "create", temp, "drive", "scope", SCOPE, "root_folder_id", folder_id, "token", token]
    client_id, client_secret = load_client()
    if client_id:
        args += ["client_id", client_id, "client_secret", client_secret]
    args += ["config_refresh_token=false", "--non-interactive"]
    rc, _out, err = _rclone(*args)
    if rc != 0:
        return Result(False, f"rclone config failed: {_rclone_error(err)}")

    rc, _out, err = _rclone("lsf", "--max-depth", "1", f"{temp}:", timeout=120)
    if rc != 0:
        _rclone("config", "delete", temp)
        return Result(False, f"Cannot open that folder: {_rclone_error(err)}")

    final = temp
    if not wanted:
        name = clean_label(_folder_name(temp, folder_id))
        if name and name not in existing and _copy_remote(temp, name):
            _rclone("config", "delete", temp)
            final = name

    if mount:
        result = set_autostart(final, True, now=True)
        if not result.ok:
            return Result(False, f"Added '{final}', but {result.message}")
        return Result(True, f"Added and mounted '{final}' at {mount_path(final)}")
    return Result(True, f"Added '{final}'")


def _start_failure(label):
    # systemctl only says "see journalctl"; the useful reason is the unit's last log line.
    _rc, out, _err = _run(["journalctl", "--user", "-u", _unit(label), "-n", "1", "-o", "cat", "--no-pager"], 10)
    reason = _rclone_error(out) if out.strip() else "see journalctl --user -u " + _unit(label)
    return f"mount failed: {reason} (it retries every 10 seconds)"


def mount(label):
    """Mount a share now."""
    if (error := _unknown(label)) is not None:
        return error
    _systemctl("daemon-reload")
    rc, _out, _err = _systemctl("start", _unit(label))
    if rc != 0:
        return Result(False, _start_failure(label))
    return Result(True, f"Mounted at {mount_path(label)}")


def unmount(label):
    """Unmount a share now."""
    if (error := _unknown(label)) is not None:
        return error
    rc, _out, err = _systemctl("stop", _unit(label))
    if rc != 0:
        return Result(False, _last_line(err))
    return Result(True, f"Unmounted '{label}'")


def set_autostart(label, enabled, now=False):
    """Turn mounting at login on or off; with now=True also start/stop it immediately."""
    if (error := _unknown(label)) is not None:
        return error
    _systemctl("daemon-reload")
    args = ["enable" if enabled else "disable"]
    if now:
        args.append("--now")
    rc, _out, err = _systemctl(*args, _unit(label))
    if rc != 0:
        return Result(False, _start_failure(label) if enabled and now else _last_line(err))
    return Result(True, f"Mount at login {'on' if enabled else 'off'} for '{label}'")


def remove(label):
    """Unmount a share and forget it; the mount directory goes only if it is empty."""
    if (error := _unknown(label)) is not None:
        return error
    _systemctl("disable", "--now", _unit(label))
    rc, _out, err = _rclone("config", "delete", label)
    if rc != 0:
        return Result(False, _last_line(err))
    try:
        os.rmdir(mount_path(label))
    except OSError:
        pass
    return Result(True, f"Removed '{label}'")


# ── New-file feed for kiro-news ──────────────────────────────────────────

CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "kiro-gdrive")
SEEN_FILE = os.path.join(CACHE_DIR, "seen.json")
# Read by kiro-news through /etc/kiro-news/sources.d/kiro-gdrive.conf.
FEED_FILE = os.path.join(CACHE_DIR, "feed.json")
_FEED_KEEP = 20
# Checksum and signature files arrive alongside a release; they are listed but never headline it.
_COMPANION_RE = re.compile(r"\.(sig|md5|sha1|sha256|sha512|pkglist\.txt)$")


def _html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _read_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def _feed_item(label, new_paths, stamp):
    main = [p for p in new_paths if not _COMPANION_RE.search(p)] or new_paths
    headline = os.path.basename(main[0])
    more = len(main) - 1
    title = f"New in {label}: {headline}" + (f" (+{more} more)" if more else "")
    files = "".join(f"<li>{_html(p)}</li>" for p in sorted(new_paths))
    return {
        "id": f"gdrive-{label}-{stamp}",
        "title": title,
        "url": f"file://{mount_path(label)}",
        "date": time.strftime("%Y-%m-%d"),
        "summary": f"<p>New in the shared Google Drive folder <strong>{_html(label)}</strong>:</p><ul>{files}</ul>",
    }


def check_new():
    """Compare every share with the last check and add one kiro-news item per share that has new files."""
    if not have_rclone() or not is_signed_in():
        return Result(True, "Nothing to check")
    seen = _read_json(SEEN_FILE, {})
    feed = _read_json(FEED_FILE, {"items": []})
    stamp = time.strftime("%Y%m%d%H%M%S")
    reports = []
    labels = sorted(_dump())
    for label in labels:
        rc, out, err = _rclone("lsjson", "-R", "--files-only", "--no-mimetype", "--fast-list", f"{label}:",
                               timeout=300)
        if rc != 0:
            reports.append(f"{label}: {_rclone_error(err)}")
            continue
        try:
            current = {entry["Path"]: entry.get("ModTime", "") for entry in json.loads(out)}
        except (ValueError, KeyError):
            reports.append(f"{label}: unreadable listing")
            continue
        if label not in seen:
            # First check of a share is a silent baseline: everything in it is "old" news.
            reports.append(f"{label}: baseline of {len(current)} files")
        else:
            new_paths = [path for path, mtime in current.items() if seen[label].get(path) != mtime]
            if new_paths:
                feed["items"].insert(0, _feed_item(label, new_paths, stamp))
            reports.append(f"{label}: {len(new_paths)} new")
        seen[label] = current
    for label in [label for label in seen if label not in labels]:
        del seen[label]
    feed["items"] = feed["items"][:_FEED_KEEP]
    _write_json(SEEN_FILE, seen)
    _write_json(FEED_FILE, feed)
    return Result(True, "; ".join(reports) or "No shares")


def run_async(func, on_done, *args, **kwargs):
    """Run a core function in a daemon thread and call on_done(result) with its return value."""

    def worker():
        on_done(func(*args, **kwargs))

    threading.Thread(target=worker, daemon=True).start()
