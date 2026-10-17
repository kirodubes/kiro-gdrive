#!/usr/bin/env python3
"""kiro-gdrive: mount Google Drive share links read-only, from a GTK4 window or the terminal."""

# ── Force Python UTF-8 mode on a non-UTF-8 locale ─────────────────────────
# Under a latin-1 locale Python encodes stdout and subprocess output with latin-1,
# so any non-ASCII glyph raises. Re-exec in UTF-8 mode only when needed; the
# re-exec'd process is UTF-8 already, so this cannot loop.
import codecs
import os
import sys

if codecs.lookup(sys.getfilesystemencoding()).name != "utf-8":
    os.environ["PYTHONUTF8"] = "1"
    os.execv(sys.executable, [sys.executable, "-X", "utf8", *sys.argv])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import log  # noqa: E402


def _gui(argv):
    # GTK is imported only here so the CLI works over SSH without a display.
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    import kgd_gui

    class KiroGdriveApp(Gtk.Application):
        """GTK4 application entry point for kiro-gdrive."""

        def __init__(self):
            super().__init__(application_id="com.kiro.kiro-gdrive")
            self.connect("activate", self.on_activate)

        def on_activate(self, _app):
            window = self.get_active_window() or kgd_gui.MainWindow(self, BASE_DIR)
            window.present()

    if "--debug" in argv:
        log.DEBUG = True
    log.log_section("kiro-gdrive")
    return KiroGdriveApp().run(None)


def main():
    argv = sys.argv[1:]
    if os.environ.get("KGD_DEBUG"):
        log.DEBUG = True
    if argv and not argv[0].startswith("-"):
        import kgd_cli

        sys.exit(kgd_cli.main(argv))
    if argv and argv[0] in ("-h", "--help"):
        import kgd_cli

        kgd_cli.build_parser().print_help()
        print("\nRun without arguments to open the window.")
        sys.exit(0)
    sys.exit(_gui(argv))


if __name__ == "__main__":
    main()
