"""GTK4 window for kiro-gdrive: account, add-a-link row and the list of shares."""

import os
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

import kgd_core  # noqa: E402
import log  # noqa: E402


_INTRO = (
    "A shared Google Drive folder normally only opens in a web browser. Kiro Google Drive puts it in your home "
    "folder instead, so you can open, copy and play its files from your file manager like any other folder."
)

_WHY = (
    "<b>Why do I need to sign in?</b>\n"
    "Google only lets programs read Drive files on behalf of a Google account, even for folders shared with "
    "“anyone with the link”. Any Google account that can open the link will do.\n\n"
    "<b>Why does a browser open?</b>\n"
    "You sign in on Google's own page, so this app never sees your password. Choose your account and click "
    "<i>Allow</i>; Google then hands this app a key that can only read files.\n\n"
    "<b>What can it do with that access?</b>\n"
    "Only look and download. It cannot change, upload or delete anything on your Drive. Google describes this as "
    "“See and download all your Google Drive files”, but the app only opens the folders you add here. "
    "Nothing is sent anywhere else: files go straight from Google to your computer.\n\n"
    "<b>Could someone turn it into write access?</b>\n"
    "No. Read-only is part of the key itself, and Google enforces it on its own servers, so changing this app "
    "cannot upgrade the key. Write access would need a new sign-in in which Google plainly asks to "
    "“see, edit, create and delete” your files, and you would have to click <i>Allow</i> yourself.\n\n"
    "<b>“Google hasn't verified this app”?</b>\n"
    "Google shows this warning for small apps until they finish its review. Click <i>Advanced</i>, then "
    "<i>Go to</i> the app to continue.\n\n"
    "<b>Where is the key kept?</b>\n"
    "In <tt>~/.config/kiro-gdrive/</tt>, readable only by you. <i>Sign out</i> deletes it and tells Google to "
    "cancel it. You can also remove access at any time under Google Account → Security → "
    "Third-party connections."
)


class MainWindow(Gtk.ApplicationWindow):
    """Main application window."""

    def __init__(self, app, base_dir):
        super().__init__(application=app, title="Kiro Google Drive")
        self.set_default_size(820, 720)
        self._load_css(base_dir)
        self._busy = False
        self._build()
        self._refresh()

    # ── Layout ───────────────────────────────────────────────────────────

    def _load_css(self, base_dir):
        css_path = os.path.join(base_dir, "kgd.css")
        if not os.path.isfile(css_path):
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(css_path)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build(self):
        header = Gtk.HeaderBar()
        self._spinner = Gtk.Spinner()
        header.pack_start(self._spinner)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh")
        refresh.connect("clicked", lambda _widget: self._refresh())
        header.pack_end(refresh)
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{side}")(16)
        self.set_child(root)

        intro = Gtk.Label(xalign=0, wrap=True, label=_INTRO)
        root.append(intro)

        self._why = Gtk.Expander(label="Why sign in, and why does a browser open?")
        why_text = Gtk.Label(xalign=0, wrap=True, use_markup=True, label=_WHY, margin_top=8, margin_start=12)
        why_text.add_css_class("kgd-why")
        self._why.set_child(why_text)
        root.append(self._why)

        account = Gtk.Box(spacing=12)
        account.add_css_class("kgd-card")
        self._account_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self._account_button = Gtk.Button()
        self._account_button.connect("clicked", self._on_account_clicked)
        account.append(self._account_label)
        account.append(self._account_button)
        root.append(account)

        add_row = Gtk.Box(spacing=8)
        self._link_entry = Gtk.Entry(hexpand=True, placeholder_text="Paste a Google Drive folder link")
        self._link_entry.connect("activate", self._on_add_clicked)
        self._label_entry = Gtk.Entry(placeholder_text="Name (optional)", width_chars=18)
        self._label_entry.connect("activate", self._on_add_clicked)
        self._add_button = Gtk.Button(label="Add and mount")
        self._add_button.add_css_class("suggested-action")
        self._add_button.connect("clicked", self._on_add_clicked)
        add_row.append(self._link_entry)
        add_row.append(self._label_entry)
        add_row.append(self._add_button)
        root.append(add_row)

        self._message = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self._message.add_css_class("kgd-message")
        root.append(self._message)

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.set_placeholder(Gtk.Label(label="No shares yet. Paste a folder link above.", margin_top=24,
                                             margin_bottom=24))
        scroller = Gtk.ScrolledWindow(vexpand=True, child=self._list)
        root.append(scroller)

        footer = Gtk.Label(xalign=0, wrap=True)
        footer.add_css_class("dim-label")
        footer.set_text(f"Shares mount read-only under {kgd_core.MOUNT_ROOT}")
        root.append(footer)

    def _share_row(self, share):
        row = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)

        dot = Gtk.Label(label="●")
        dot.add_css_class("kgd-on" if share.mounted else "kgd-off")
        dot.set_tooltip_text("Mounted" if share.mounted else "Not mounted")
        row.append(dot)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        name = Gtk.Label(label=share.label, xalign=0)
        name.add_css_class("heading")
        path = Gtk.Label(label=share.path, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        path.add_css_class("dim-label")
        text.append(name)
        text.append(path)
        row.append(text)

        at_login = Gtk.CheckButton(label="At login", active=share.enabled, valign=Gtk.Align.CENTER)
        at_login.connect("toggled", self._on_autostart_toggled, share.label)
        row.append(at_login)

        switch = Gtk.Switch(active=share.mounted, valign=Gtk.Align.CENTER)
        switch.set_tooltip_text("Mounted")
        switch.connect("state-set", self._on_mount_switched, share.label)
        row.append(switch)

        open_button = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        open_button.set_tooltip_text("Open folder")
        open_button.set_valign(Gtk.Align.CENTER)
        open_button.set_sensitive(share.mounted)
        open_button.connect("clicked", self._on_open_clicked, share.path)
        row.append(open_button)

        remove_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove_button.set_tooltip_text("Unmount and forget (nothing is deleted on Google Drive)")
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.connect("clicked", self._on_remove_clicked, share.label)
        row.append(remove_button)
        return row

    # ── State ────────────────────────────────────────────────────────────

    def _set_busy(self, busy, message=None):
        self._busy = busy
        if busy:
            self._spinner.start()
        else:
            self._spinner.stop()
        for widget in (self._account_button, self._add_button, self._list):
            widget.set_sensitive(not busy)
        if message is not None:
            self._show(message)

    def _show(self, message, error=False):
        self._message.set_text(message)
        if error:
            self._message.add_css_class("kgd-error")
        else:
            self._message.remove_css_class("kgd-error")

    def _refresh(self):
        signed_in = kgd_core.is_signed_in()
        have_rclone = kgd_core.have_rclone()
        if not have_rclone:
            self._account_label.set_text("rclone is needed to mount Google Drive folders, and it is not installed.")
            self._account_button.set_label("Install rclone")
        elif signed_in:
            self._account_label.set_text("Signed in to Google (read-only access)")
            self._account_button.set_label("Sign out")
        else:
            self._account_label.set_text("Not signed in. Sign in once with the Google account that can open the links.")
            self._account_button.set_label("Sign in with Google")
        self._add_button.set_sensitive(signed_in and have_rclone and not self._busy)
        self._why.set_expanded(not signed_in)

        def worker():
            shares = kgd_core.list_shares()
            GLib.idle_add(self._fill_list, shares)

        threading.Thread(target=worker, daemon=True).start()

    def _fill_list(self, shares):
        while (child := self._list.get_first_child()) is not None:
            self._list.remove(child)
        for share in shares:
            self._list.append(self._share_row(share))
        return False

    def _run(self, busy_message, func, *args, clear_entries=False, **kwargs):
        if self._busy:
            return
        self._set_busy(True, busy_message)

        def done(result):
            GLib.idle_add(self._finished, result, clear_entries)

        kgd_core.run_async(func, done, *args, **kwargs)

    def _finished(self, result, clear_entries):
        self._set_busy(False)
        (log.log_success if result.ok else log.log_error)(result.message)
        self._show(result.message, error=not result.ok)
        if result.ok and clear_entries:
            self._link_entry.set_text("")
            self._label_entry.set_text("")
        self._refresh()
        return False

    # ── Callbacks ────────────────────────────────────────────────────────

    def _on_account_clicked(self, _widget):
        if not kgd_core.have_rclone():
            self._run("Installing rclone: type your password in the terminal window…", kgd_core.install_rclone)
        elif kgd_core.is_signed_in():
            self._run("Signing out and unmounting all shares…", kgd_core.logout)
        else:
            self._run("Your browser now shows Google's sign-in page. Choose your account, click Allow, "
                      "then come back here (this window waits up to 10 minutes)…", kgd_core.login)

    def _on_add_clicked(self, _widget):
        link = self._link_entry.get_text().strip()
        if not link:
            self._show("Paste a Google Drive folder link first", error=True)
            return
        try:
            kgd_core.parse_link(link)
        except ValueError as err:
            self._show(str(err), error=True)
            return
        self._run("Checking the folder and mounting it…", kgd_core.add, link, self._label_entry.get_text(),
                  clear_entries=True)

    def _on_mount_switched(self, _widget, state, label):
        if state:
            self._run(f"Mounting '{label}'…", kgd_core.mount, label)
        else:
            self._run(f"Unmounting '{label}'…", kgd_core.unmount, label)
        # Returning True keeps the switch where it was; _refresh() redraws it from the real mount state.
        return True

    def _on_autostart_toggled(self, widget, label):
        self._run("Updating mount at login…", kgd_core.set_autostart, label, widget.get_active())

    def _on_open_clicked(self, _widget, path):
        subprocess.Popen(["xdg-open", path], start_new_session=True)

    def _on_remove_clicked(self, _widget, label):
        self._run(f"Removing '{label}'…", kgd_core.remove, label)
