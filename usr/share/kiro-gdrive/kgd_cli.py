"""Command-line front-end for kiro-gdrive."""

import argparse

import kgd_core
import log


def _report(result):
    if result.ok:
        log.log_success(result.message)
        return 0
    log.log_error(result.message)
    return 1


def _cmd_list(_args):
    shares = kgd_core.list_shares()
    if not shares:
        log.log_info("No shares yet. Add one with: kiro-gdrive add LINK")
        return 0
    for share in shares:
        state = "mounted" if share.mounted else ("starting" if share.active else "not mounted")
        login = "at login" if share.enabled else "manual"
        print(f"  {share.label:<28} {state:<12} {login:<9} {share.path}")
    return 0


def _cmd_status(args):
    log.log_info(f"Google account: {'signed in' if kgd_core.is_signed_in() else 'not signed in'}")
    client_id, _secret = kgd_core.load_client()
    log.log_info(f"OAuth client:   {'kiro-gdrive' if client_id else 'rclone built-in (retiring in 2026)'}")
    log.log_info(f"Config:         {kgd_core.RCLONE_CONF}")
    return _cmd_list(args)


def build_parser():
    """Return the argparse parser for all subcommands."""
    parser = argparse.ArgumentParser(prog="kiro-gdrive", description="Mount Google Drive share links read-only.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="sign in to Google (read-only)")
    p.add_argument("--no-browser", action="store_true", help="print the sign-in URL instead of opening it")
    p.set_defaults(func=lambda a: _report(kgd_core.login(no_browser=a.no_browser)))

    p = sub.add_parser("logout", help="remove all shares and revoke the Google token")
    p.set_defaults(func=lambda _a: _report(kgd_core.logout()))

    p = sub.add_parser("add", help="add a Drive folder link and mount it")
    p.add_argument("link")
    p.add_argument("--label", default="", help="name for the share (default: the folder's Drive name)")
    p.add_argument("--no-mount", action="store_true", help="configure only, do not mount")
    p.set_defaults(func=lambda a: _report(kgd_core.add(a.link, a.label, mount=not a.no_mount)))

    p = sub.add_parser("list", help="list shares")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("status", help="account, client and shares")
    p.set_defaults(func=_cmd_status)

    for name, func, text in (
        ("mount", kgd_core.mount, "mount a share now"),
        ("unmount", kgd_core.unmount, "unmount a share now"),
        ("remove", kgd_core.remove, "unmount and forget a share"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("label")
        p.set_defaults(func=lambda a, f=func: _report(f(a.label)))

    for name, enabled in (("enable", True), ("disable", False)):
        p = sub.add_parser(name, help=f"{'mount' if enabled else 'stop mounting'} a share at login")
        p.add_argument("label")
        p.set_defaults(func=lambda a, e=enabled: _report(kgd_core.set_autostart(a.label, e)))

    return parser


def main(argv):
    """Parse argv and run the chosen subcommand; return the exit code."""
    args = build_parser().parse_args(argv)
    if args.command not in ("list", "status") and not kgd_core.have_rclone():
        log.log_error("rclone is not installed: sudo pacman -S rclone")
        return 1
    return args.func(args)
