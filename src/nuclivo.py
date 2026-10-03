#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Nuclivo – a GTK4/libadwaita front end for the official Proton Drive CLI."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk

try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit
except (ImportError, ValueError):
    WebKit = None

import datetime as dt
import hashlib
import json
import os
os.umask(0o077)
from nuclivo_security import private_dir, save_json, safe_name, is_proton
from nuclivo_i18n import get_language, set_language, system_language, tr as _
import urllib.parse
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

APP_ID = "io.github.oswaltx.nuclivo"
CLI = os.path.expanduser("~/.local/bin/proton-drive")
CACHE = os.path.join(GLib.get_user_cache_dir(), "nuclivo")
CONFIG = os.path.join(GLib.get_user_config_dir(), "nuclivo")
SYNC_CONFIG = os.path.join(CONFIG, "sync.json")
SYNC_UNIT = "nuclivo-sync.service"
WEB_URL = "https://drive.proton.me"

try:
    with open(os.path.join(CONFIG, "settings.json")) as _settings_file:
        _saved_language = json.load(_settings_file).get("language")
except (OSError, ValueError, AttributeError):
    _saved_language = None
set_language(_saved_language or system_language())

_browse_pool = ThreadPoolExecutor(3)
_transfer_pool = ThreadPoolExecutor(2)
_thumb_pool = ThreadPoolExecutor(2)

MONTHS = [_("Januar"), _("Februar"), _("März"), _("April"), _("Mai"), _("Juni"), _("Juli"),
          _("August"), _("September"), _("Oktober"), _("November"), _("Dezember")]
PROTON_DOC_TYPES = ("application/vnd.proton.doc", "application/vnd.proton.sheet")


# ---------------------------------------------------------------- CLI access

def _parse_json(text):
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        for i, ch in enumerate(text):
            if ch in "[{":
                try:
                    return json.JSONDecoder().raw_decode(text[i:])[0]
                except ValueError:
                    continue
    return None


def cli_sync(args, json_out=True, timeout=None):
    """Run the CLI and return (ok, data, error_message)."""
    cmd = [CLI, *args] + (["--json"] if json_out else [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except FileNotFoundError:
        return False, None, f"Proton Drive CLI nicht gefunden ({CLI})"
    except subprocess.TimeoutExpired:
        return False, None, "Zeitüberschreitung"
    data = _parse_json(p.stdout) if json_out else p.stdout
    err = (p.stderr.strip() or p.stdout.strip()).splitlines()
    err = err[-1] if err else ""
    ok = p.returncode == 0
    if isinstance(data, dict) and data.get("failedItems"):
        ok = False
        fails = data.get("failures") or []
        err = "; ".join(str(f.get("error", f)) if isinstance(f, dict) else str(f)
                        for f in fails[:3]) or err
    if isinstance(data, list) and data and all(isinstance(x, dict) and "ok" in x for x in data):
        bad = [x for x in data if not x.get("ok")]
        if bad:
            ok = False
            err = str(bad[0].get("error", err))
    if ok and json_out and data is None and p.stdout.strip():
        err = "Unerwartete Ausgabe der CLI"
    return ok, data, err


def cli(args, callback, json_out=True, pool=_browse_pool):
    """Run the CLI in a worker thread and call callback(ok, data, err) on the main loop."""
    fut = pool.submit(cli_sync, args, json_out)

    def done(f):
        try:
            res = f.result()
        except Exception as e:  # noqa: BLE001
            res = (False, None, str(e))
        GLib.idle_add(lambda: callback(*res) and False)

    fut.add_done_callback(done)


def is_auth_error(err):
    e = (err or "").lower()
    return any(k in e for k in ("not logged", "log in", "login", "unauthori", "session", "auth"))


def esc(name):
    return name.replace("\\", "\\\\").replace("/", "\\/")


def esc_local(path):
    """The CLI glob-expands local paths; escape glob metacharacters."""
    out = path
    for ch in "[]{}*?":
        out = out.replace(ch, "\\" + ch)
    return out


# ---------------------------------------------------------------- formatting

def parse_time(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def fmt_time(t):
    if not t:
        return ""
    now = dt.datetime.now().astimezone()
    if t.date() == now.date():
        return _("Heute, {time}").format(time=t.strftime("%H:%M"))
    if t.date() == (now - dt.timedelta(days=1)).date():
        return _("Gestern, {time}").format(time=t.strftime("%H:%M"))
    if t.year == now.year:
        return f"{t.day}. {MONTHS[t.month - 1][:3]}."
    return f"{t.day}. {MONTHS[t.month - 1][:3]}. {t.year}"


def fmt_size(n):
    return GLib.format_size(n) if n is not None else ""


def kind_of(node):
    if node.type in ("folder", "album"):
        return "folder"
    m = node.media_type or ""
    if m in PROTON_DOC_TYPES:
        return "sheet" if m.endswith("sheet") else "doc"
    if m.startswith("image/"):
        return "image"
    if m.startswith("video/"):
        return "video"
    if m.startswith("audio/"):
        return "audio"
    if m == "application/pdf":
        return "pdf"
    if any(k in m for k in ("zip", "tar", "compressed", "7z", "rar")):
        return "archive"
    if m.startswith("text/") or "document" in m or "word" in m:
        return "text"
    return "file"


KIND_ICONS = {
    "folder": "folder-symbolic", "image": "image-x-generic-symbolic",
    "video": "video-x-generic-symbolic", "audio": "audio-x-generic-symbolic",
    "pdf": "x-office-document-symbolic", "doc": "x-office-document-symbolic",
    "sheet": "x-office-spreadsheet-symbolic", "archive": "package-x-generic-symbolic",
    "text": "text-x-generic-symbolic", "file": "text-x-generic-symbolic",
}


# ---------------------------------------------------------------- models

class Node(GObject.Object):
    def __init__(self, raw, parent_path, use_uid=False):
        super().__init__()
        self.raw = raw
        self.uid = raw.get("uid", "")
        name = raw.get("name") or {}
        self.name_ok = bool(name.get("ok"))
        self.name = name.get("value") if self.name_ok else "(Name nicht lesbar)"
        self.type = raw.get("type", "file")
        self.media_type = raw.get("mediaType")
        self.size = raw.get("totalStorageSize")
        rev = raw.get("activeRevision") or {}
        self.size = rev.get("claimedSize") or self.size
        self.mtime = parse_time(raw.get("modificationTime"))
        self.shared = bool(raw.get("isShared") or raw.get("isSharedByUrl"))
        self.owner = (raw.get("ownedBy") or {}).get("email", "")
        self.is_folder = self.type in ("folder", "album")
        segment = self.uid if (use_uid or not self.name_ok) else esc(self.name)
        self.path = parent_path.rstrip("/") + "/" + segment
        self.kind = kind_of(self)


class Photo(GObject.Object):
    def __init__(self, uid, capture, name=None, media_type=None, source="/photos"):
        super().__init__()
        self.uid = uid
        self.capture = parse_time(capture)
        self.name = name
        self.media_type = media_type
        self.source = source
        self.texture = None
        self.failed = False

    @property
    def is_video(self):
        return (self.media_type or "").startswith("video/")


class Transfer(GObject.Object):
    def __init__(self, title, icon):
        super().__init__()
        self.title = title
        self.icon = icon
        self.state = "running"
        self.detail = _("Läuft …")


# ---------------------------------------------------------------- styles

CSS = """
.file-tile { border-radius: 10px; min-width: 38px; min-height: 38px; }
.file-tile image { color: white; }
.tile-folder  { background: linear-gradient(135deg, #6d4aff, #8a6bff); }
.tile-image   { background: linear-gradient(135deg, #c061cb, #dc8add); }
.tile-video   { background: linear-gradient(135deg, #e01b24, #f66151); }
.tile-audio   { background: linear-gradient(135deg, #26a269, #57e389); }
.tile-pdf     { background: linear-gradient(135deg, #e66100, #ffa348); }
.tile-doc     { background: linear-gradient(135deg, #1c71d8, #62a0ea); }
.tile-sheet   { background: linear-gradient(135deg, #2ec27e, #8ff0a4); }
.tile-archive { background: linear-gradient(135deg, #c88800, #f6d32d); }
.tile-text, .tile-file { background: linear-gradient(135deg, #5e5c64, #9a9996); }
.nuclivo-list { background: transparent; }
.nuclivo-list > row { border-radius: 12px; margin: 1px 8px; padding: 0; }
.nuclivo-list > row:hover { background: alpha(currentColor, 0.06); }
.file-name { font-weight: 600; }
.shared-badge { color: @accent_color; }
.pathbar { padding: 4px 10px; }
.pathbar button { padding: 2px 8px; min-height: 26px; font-weight: 500; }
.pathbar .crumb-sep { opacity: 0.4; }
.drop-highlight { background: alpha(@accent_bg_color, 0.12); border: 2px dashed @accent_color; border-radius: 16px; margin: 8px; }
.photo-cell { border-radius: 10px; background: alpha(currentColor, 0.07); }
.photo-cell picture { border-radius: 10px; }
.photo-grid > child { padding: 3px; border-radius: 12px; }
.photo-grid > child:hover { background: alpha(@accent_bg_color, 0.25); }
.album-card { border-radius: 16px; padding: 18px; background: alpha(currentColor, 0.05); }
.album-card:hover { background: alpha(@accent_bg_color, 0.15); }
.album-icon { border-radius: 14px; min-width: 64px; min-height: 64px; color: white;
              background: linear-gradient(135deg, #6d4aff, #c061cb); }
.sync-hero { border-radius: 20px; padding: 24px; }
.sync-ok { background: linear-gradient(135deg, alpha(#2ec27e, 0.18), alpha(#6d4aff, 0.12)); }
.sync-off { background: alpha(currentColor, 0.06); }
.log-view { font-family: monospace; font-size: 0.85em; border-radius: 12px; padding: 12px; }
.transfer-count { background: @accent_bg_color; color: @accent_fg_color; border-radius: 99px;
                  font-size: 0.7em; font-weight: 700; padding: 0 5px; min-width: 10px; }
"""


# ---------------------------------------------------------------- helpers

def tile_for(kind, size=38, icon_size=18):
    box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    box.add_css_class("file-tile")
    box.add_css_class(f"tile-{kind}")
    box.set_size_request(size, size)
    box.set_hexpand(False)
    box.set_vexpand(False)
    img = Gtk.Image(icon_name=KIND_ICONS.get(kind, "text-x-generic-symbolic"),
                    pixel_size=icon_size, hexpand=True, halign=Gtk.Align.CENTER)
    box.append(img)
    return box


def menu_item(label, action, target):
    item = Gio.MenuItem.new(label, None)
    item.set_action_and_target_value(action, GLib.Variant.new_string(target))
    return item


def show_menu(menu, parent, x=None, y=None):
    pop = Gtk.PopoverMenu.new_from_model(menu)
    pop.set_has_arrow(x is None)
    pop.set_parent(parent)
    if x is not None:
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
    pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
    pop.popup()


def copy_text(widget, text):
    widget.get_clipboard().set(text)


def downloads_dir():
    return GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~")


def open_local(path):
    Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)


def entry_dialog(win, heading, body, initial, ok_label, callback):
    dlg = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial, activates_default=True)
    dlg.set_extra_child(entry)
    dlg.add_response("cancel", _("Abbrechen"))
    dlg.add_response("ok", ok_label)
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def on_resp(d, resp):
        text = entry.get_text().strip()
        if resp == "ok" and text:
            callback(text)

    dlg.connect("response", on_resp)
    dlg.present(win)
    if initial and "." in initial:
        entry.select_region(0, initial.rfind("."))
    else:
        entry.select_region(0, -1)
    entry.grab_focus()


def confirm_dialog(win, heading, body, ok_label, callback, destructive=True):
    dlg = Adw.AlertDialog(heading=heading, body=body)
    dlg.add_response("cancel", _("Abbrechen"))
    dlg.add_response("ok", ok_label)
    dlg.set_response_appearance(
        "ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
    dlg.set_close_response("cancel")
    dlg.connect("response", lambda d, r: r == "ok" and callback())
    dlg.present(win)


def status_page(icon, title, desc="", button=None, cb=None):
    sp = Adw.StatusPage(icon_name=icon, title=title, description=desc, vexpand=True)
    if button:
        b = Gtk.Button(label=button, halign=Gtk.Align.CENTER)
        b.add_css_class("pill")
        b.add_css_class("suggested-action")
        b.connect("clicked", lambda *_: cb())
        sp.set_child(b)
    return sp


# ---------------------------------------------------------------- browser

SECTIONS = {
    "my-files": ("/my-files", _("Meine Dateien"), "folder-symbolic"),
    "shared-by-me": ("/shared-by-me", _("Von mir geteilt"), "send-to-symbolic"),
    "shared-with-me": ("/shared-with-me", _("Mit mir geteilt"), "folder-publicshare-symbolic"),
    "devices": ("/devices", _("Geräte"), "computer-symbolic"),
    "trash": ("/trash", _("Papierkorb"), "user-trash-symbolic"),
}


class BrowserPage(Adw.Bin):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.path = "/my-files"
        self.root = "/my-files"
        self.crumbs = [("/my-files", _("Meine Dateien"))]
        self.cache = {}
        self.items = {}
        self.sort_key = "name"
        self.load_token = 0
        self.folder_uids = {}

        self.store = Gio.ListStore(item_type=Node)
        self.sorter = Gtk.CustomSorter.new(self._compare)
        sorted_model = Gtk.SortListModel(model=self.store, sorter=self.sorter)
        self.filter = Gtk.CustomFilter.new(self._filter)
        filtered = Gtk.FilterListModel(model=sorted_model, filter=self.filter)
        self.selection = Gtk.NoSelection(model=filtered)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        self.listview = Gtk.ListView(model=self.selection, factory=factory,
                                     single_click_activate=True)
        self.listview.add_css_class("nuclivo-list")
        self.listview.connect("activate", self._on_activate)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(Adw.ClampScrollable(child=self.listview, maximum_size=1100,
                                               tightening_threshold=900))

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=12)
        spinner_box.append(Adw.Spinner(width_request=48, height_request=48, halign=Gtk.Align.CENTER))
        lbl = Gtk.Label(label=_("Wird geladen …"))
        lbl.add_css_class("dim-label")
        spinner_box.append(lbl)
        self.stack.add_named(spinner_box, "loading")
        self.stack.add_named(scroller, "list")
        self.empty_page = status_page("folder-open-symbolic", _("Dieser Ordner ist leer"),
                                      _("Zieh Dateien hierher, um sie hochzuladen."))
        self.stack.add_named(self.empty_page, "empty")
        self.error_page = status_page("dialog-warning-symbolic", _("Fehler"), "",
                                      _("Erneut versuchen"), self.reload)
        self.stack.add_named(self.error_page, "error")
        self.login_page = status_page("system-lock-screen-symbolic", _("Nicht angemeldet"),
                                      _("Melde dich im Browser bei Proton an."),
                                      _("Anmelden"), self.win.login)
        self.stack.add_named(self.login_page, "login")
        self.nomatch_page = status_page("system-search-symbolic", _("Keine Treffer"))
        self.stack.add_named(self.nomatch_page, "nomatch")

        # header
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title=_("Meine Dateien"))
        header.set_title_widget(self.title)
        self.up_btn = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=_("Übergeordneter Ordner"))
        self.up_btn.connect("clicked", lambda *_: self.go_up())
        header.pack_start(self.up_btn)

        add_menu = Gio.Menu()
        up = Gio.Menu()
        up.append(_("Dateien hochladen …"), "win.upload-files")
        up.append(_("Ordner hochladen …"), "win.upload-folders")
        add_menu.append_section(None, up)
        new = Gio.Menu()
        new.append(_("Neuer Ordner …"), "win.new-folder")
        new.append(_("Neues Dokument"), "win.new-doc")
        new.append(_("Neue Tabelle"), "win.new-sheet")
        add_menu.append_section(None, new)
        self.add_btn = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=add_menu,
                                      tooltip_text=_("Hinzufügen"))
        self.add_btn.add_css_class("suggested-action")
        header.pack_start(self.add_btn)

        self.empty_trash_btn = Gtk.Button(label=_("Papierkorb leeren"))
        self.empty_trash_btn.add_css_class("destructive-action")
        self.empty_trash_btn.connect("clicked", lambda *_: self.empty_trash())
        header.pack_start(self.empty_trash_btn)

        sort_menu = Gio.Menu()
        sort_menu.append(_("Name"), "win.sort::name")
        sort_menu.append(_("Zuletzt geändert"), "win.sort::date")
        sort_menu.append(_("Größe"), "win.sort::size")
        header.pack_end(Gtk.MenuButton(icon_name="view-sort-descending-symbolic",
                                       menu_model=sort_menu, tooltip_text=_("Sortieren")))
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=_("Suchen"))
        header.pack_end(self.search_btn)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Aktualisieren"))
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_end(refresh)

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("In diesem Ordner suchen"), hexpand=True)
        self.search_entry.connect("search-changed", lambda *_: self._refilter())
        self.search_bar = Gtk.SearchBar(child=Adw.Clamp(child=self.search_entry, maximum_size=500))
        self.search_bar.connect_entry(self.search_entry)
        self.search_btn.bind_property("active", self.search_bar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL)
        self.search_bar.set_key_capture_widget(self.win)

        self.pathbar = Gtk.Box(spacing=2)
        self.pathbar.add_css_class("pathbar")
        path_scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           hscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        path_scroller.set_child(self.pathbar)

        # drop target
        overlay = Gtk.Overlay(child=self.stack)
        self.drop_hint = Gtk.Box(can_target=False, visible=False)
        self.drop_hint.add_css_class("drop-highlight")
        hint = Adw.StatusPage(icon_name="document-send-symbolic", title=_("Zum Hochladen loslassen"),
                              hexpand=True)
        self.drop_hint.append(hint)
        overlay.add_overlay(self.drop_hint)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("enter", self._drop_enter)
        drop.connect("leave", lambda *_: self.drop_hint.set_visible(False))
        drop.connect("drop", self._on_drop)
        overlay.add_controller(drop)

        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.add_top_bar(path_scroller)
        tv.add_top_bar(self.search_bar)
        tv.set_content(overlay)
        self.set_child(tv)

    # ---- section/navigation
    def open_section(self, key):
        root, label, _ = SECTIONS[key]
        self.root = root
        self.crumbs = [(root, label)]
        self.search_btn.set_active(False)
        self.navigate(root)

    def navigate(self, path, label=None):
        if label is not None:
            self.crumbs.append((path, label))
        self.path = path
        is_trash = self.root == "/trash"
        writable = self.root == "/my-files" or (self.root == "/devices" and len(self.crumbs) > 1)
        self.add_btn.set_visible(writable)
        self.empty_trash_btn.set_visible(is_trash)
        self.up_btn.set_sensitive(len(self.crumbs) > 1)
        self.title.set_title(self.crumbs[-1][1])
        self.title.set_subtitle("")
        self._update_pathbar()
        self.empty_page.set_title(_("Der Papierkorb ist leer") if is_trash else _("Dieser Ordner ist leer"))
        self.empty_page.set_description("" if not writable else _("Zieh Dateien hierher, um sie hochzuladen."))
        if path in self.cache:
            self._show(self.cache[path])
            self.load(silent=True)
        else:
            self.load()

    def go_up(self):
        if len(self.crumbs) > 1:
            self.crumbs.pop()
            self.navigate(self.crumbs[-1][0])

    def go_to_crumb(self, idx):
        self.crumbs = self.crumbs[: idx + 1]
        self.navigate(self.crumbs[-1][0])

    def _update_pathbar(self):
        while (child := self.pathbar.get_first_child()):
            self.pathbar.remove(child)
        for i, (_, label) in enumerate(self.crumbs):
            if i:
                sep = Gtk.Label(label="›")
                sep.add_css_class("crumb-sep")
                self.pathbar.append(sep)
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            if i == len(self.crumbs) - 1:
                b.add_css_class("accent")
            b.connect("clicked", lambda _b, idx=i: self.go_to_crumb(idx))
            self.pathbar.append(b)

    # ---- loading
    def reload(self):
        self.load(silent=self.path in self.cache)

    def load(self, silent=False):
        self.load_token += 1
        token, path = self.load_token, self.path
        if not silent:
            self.stack.set_visible_child_name("loading")
        self.title.set_subtitle(_("Aktualisiere …") if silent else "")

        def done(ok, data, err):
            if token != self.load_token:
                return
            self.title.set_subtitle("")
            if not ok or not isinstance(data, list):
                if is_auth_error(err):
                    self.stack.set_visible_child_name("login")
                elif not silent:
                    self.error_page.set_description(GLib.markup_escape_text(err or _("Unbekannter Fehler")))
                    self.stack.set_visible_child_name("error")
                else:
                    self.win.toast(_("Aktualisieren fehlgeschlagen: {error}").format(error=err))
                return
            use_uid = path in ("/trash", "/shared-with-me")
            names = [((x.get("name") or {}).get("value")) for x in data]
            dupes = {n for n in names if names.count(n) > 1}
            nodes = []
            for raw in data:
                n = (raw.get("name") or {}).get("value")
                nodes.append(Node(raw, path, use_uid or n in dupes))
            self.cache[path] = nodes
            self._show(nodes)

        cli(["filesystem", "list", path], done)

    def folder_uid(self):
        """UID of the folder being shown (children know their parent)."""
        nodes = self.cache.get(self.path) or []
        return self.folder_uids.get(self.path) or (nodes[0].raw.get("parentUid") if nodes else None)

    def _show(self, nodes):
        self.items = {n.uid: n for n in nodes}
        self.store.splice(0, self.store.get_n_items(), nodes)
        count = len(nodes)
        if count:
            folders = sum(1 for n in nodes if n.is_folder)
            parts = []
            if folders:
                parts.append(_("{count} Ordner").format(count=folders))
            if count - folders:
                parts.append(_("{count} Dateien").format(count=count - folders))
            self.title.set_subtitle(" · ".join(parts))
        self._refilter()

    def _refilter(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        if not self.store.get_n_items():
            self.stack.set_visible_child_name("empty")
        elif not self.selection.get_n_items():
            self.stack.set_visible_child_name("nomatch")
        else:
            self.stack.set_visible_child_name("list")

    def set_sort(self, key):
        self.sort_key = key
        self.sorter.changed(Gtk.SorterChange.DIFFERENT)

    def _filter(self, node):
        q = self.search_entry.get_text().strip().lower()
        return not q or q in node.name.lower()

    def _compare(self, a, b, *_):
        if a.is_folder != b.is_folder:
            return -1 if a.is_folder else 1
        if self.sort_key == "date":
            ta, tb = a.mtime or dt.datetime.min.astimezone(), b.mtime or dt.datetime.min.astimezone()
            return (ta < tb) - (ta > tb)
        if self.sort_key == "size":
            sa, sb = a.size or 0, b.size or 0
            return (sa < sb) - (sa > sb)
        na, nb = a.name.casefold(), b.name.casefold()
        return (na > nb) - (na < nb)

    # ---- rows
    def _setup_row(self, _f, li):
        row = Gtk.Box(spacing=14, margin_top=7, margin_bottom=7, margin_start=10, margin_end=6)
        tile_slot = Adw.Bin()
        row.append(tile_slot)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        name = Gtk.Label(xalign=0, ellipsize=3)
        name.add_css_class("file-name")
        sub = Gtk.Label(xalign=0, ellipsize=3)
        sub.add_css_class("dim-label")
        sub.add_css_class("caption")
        texts.append(name)
        texts.append(sub)
        row.append(texts)
        badge = Gtk.Image(icon_name="send-to-symbolic", tooltip_text=_("Geteilt"))
        badge.add_css_class("shared-badge")
        row.append(badge)
        more = Gtk.Button(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Aktionen"))
        more.add_css_class("flat")
        more.add_css_class("circular")
        row.append(more)
        row._w = (tile_slot, name, sub, badge, more)
        row._node = None
        more.connect("clicked", lambda b: row._node and show_menu(self.menu_for(row._node), b))
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda g, n, x, y: row._node and show_menu(self.menu_for(row._node), row, x, y))
        row.add_controller(click)
        li.set_child(row)

    def _bind_row(self, _f, li):
        node = li.get_item()
        row = li.get_child()
        tile_slot, name, sub, badge, _ = row._w
        row._node = node
        tile_slot.set_child(tile_for(node.kind))
        name.set_text(node.name)
        bits = []
        if not node.is_folder and node.size is not None:
            bits.append(fmt_size(node.size))
        if node.mtime:
            bits.append(fmt_time(node.mtime))
        if self.root == "/shared-with-me" and node.owner:
            bits.append(node.owner)
        sub.set_text(" · ".join(bits))
        badge.set_visible(node.shared)

    def _on_activate(self, _lv, pos):
        node = self.selection.get_item(pos)
        if node is None:
            return
        if self.root == "/trash":
            show_menu(self.menu_for(node), self.listview)
        elif node.is_folder:
            self.search_entry.set_text("")
            self.folder_uids[node.path] = node.uid
            self.navigate(node.path, node.name)
        else:
            self.win.open_node(node)

    def menu_for(self, node):
        menu = Gio.Menu()
        if self.root == "/trash":
            menu.append_item(menu_item(_("Wiederherstellen"), "win.restore", node.uid))
            danger = Gio.Menu()
            danger.append_item(menu_item(_("Endgültig löschen"), "win.delete", node.uid))
            menu.append_section(None, danger)
            return menu
        top = Gio.Menu()
        top.append_item(menu_item(_("Öffnen"), "win.open", node.uid))
        top.append_item(menu_item(_("Herunterladen"), "win.download", node.uid))
        top.append_item(menu_item(_("Herunterladen nach …"), "win.download-to", node.uid))
        menu.append_section(None, top)
        mid = Gio.Menu()
        if self.root != "/shared-with-me":
            mid.append_item(menu_item(_("Teilen …"), "win.share", node.uid))
        mid.append_item(menu_item(_("Umbenennen …"), "win.rename", node.uid))
        mid.append_item(menu_item(_("Verschieben nach …"), "win.move", node.uid))
        mid.append_item(menu_item(_("Kopieren nach …"), "win.copy", node.uid))
        mid.append_item(menu_item(_("Eigenschaften"), "win.info", node.uid))
        menu.append_section(None, mid)
        low = Gio.Menu()
        if self.root == "/shared-with-me":
            low.append_item(menu_item(_("Freigabe verlassen"), "win.leave", node.uid))
        else:
            low.append_item(menu_item(_("In den Papierkorb"), "win.trash", node.uid))
        menu.append_section(None, low)
        return menu

    # ---- drag and drop
    def _drop_enter(self, *_):
        if self.add_btn.get_visible():
            self.drop_hint.set_visible(True)
            return Gdk.DragAction.COPY
        return 0

    def _on_drop(self, _t, value, _x, _y):
        self.drop_hint.set_visible(False)
        if not self.add_btn.get_visible():
            return False
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if paths:
            self.win.upload(paths, self.path)
        return True

    def invalidate(self, path=None):
        self.cache.pop(path or self.path, None)
        if (path or self.path) == self.path:
            self.load(silent=self.store.get_n_items() > 0)

    def empty_trash(self):
        def go():
            def done(ok, _d, err):
                self.win.toast(_("Papierkorb geleert") if ok else _("Fehler: {error}").format(error=err))
                self.invalidate("/trash")
            cli(["filesystem", "empty-trash"], done)
        confirm_dialog(self.win, _("Papierkorb leeren?"),
                       _("Alle Elemente im Papierkorb werden endgültig gelöscht."), _("Leeren"), go)


# ---------------------------------------------------------------- folder picker

class FolderPicker(Adw.Dialog):
    def __init__(self, win, title, action_label, callback, exclude_uid=None):
        super().__init__(title=title, content_width=460, content_height=560)
        self.win, self.callback, self.exclude = win, callback, exclude_uid
        self.crumbs = [("/my-files", _("Meine Dateien"))]
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.back = Gtk.Button(icon_name="go-previous-symbolic")
        self.back.connect("clicked", lambda *_: self._back())
        header.pack_start(self.back)
        self.wtitle = Adw.WindowTitle(title=title)
        header.set_title_widget(self.wtitle)
        tv.add_top_bar(header)
        self.stack = Gtk.Stack()
        self.stack.add_named(Adw.Spinner(), "loading")
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-activated", self._on_row)
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_child(Adw.Clamp(child=self.listbox, margin_top=12, margin_bottom=12,
                               margin_start=12, margin_end=12))
        self.stack.add_named(sc, "list")
        self.stack.add_named(status_page("folder-symbolic", _("Keine Unterordner")), "empty")
        tv.set_content(self.stack)
        bottom = Gtk.Box(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        btn = Gtk.Button(label=action_label, hexpand=True)
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.connect("clicked", self._choose)
        bottom.append(btn)
        tv.add_bottom_bar(bottom)
        self.set_child(tv)
        self._load()

    def _load(self):
        path, label = self.crumbs[-1]
        self.wtitle.set_subtitle(label)
        self.back.set_sensitive(len(self.crumbs) > 1)
        self.stack.set_visible_child_name("loading")

        def done(ok, data, err):
            while (r := self.listbox.get_row_at_index(0)):
                self.listbox.remove(r)
            if not ok or not isinstance(data, list):
                self.win.toast(_("Fehler: {error}").format(error=err))
                return
            nodes = sorted((Node(x, path) for x in data if x.get("uid") != self.exclude),
                           key=lambda n: n.name.casefold())
            for n in nodes:
                row = Adw.ActionRow(title=GLib.markup_escape_text(n.name), activatable=True)
                row.add_prefix(tile_for("folder", 32, 16))
                row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                row._node = n
                self.listbox.append(row)
            self.stack.set_visible_child_name("list" if nodes else "empty")

        cli(["filesystem", "list", "-t", "folder", path], done)

    def _on_row(self, _lb, row):
        self.crumbs.append((row._node.path, row._node.name))
        self._load()

    def _back(self):
        self.crumbs.pop()
        self._load()

    def _choose(self, *_):
        self.close()
        self.callback(self.crumbs[-1][0])


# ---------------------------------------------------------------- dialogs

class InfoDialog(Adw.Dialog):
    def __init__(self, win, node):
        super().__init__(title=_("Eigenschaften"), content_width=440)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_bottom=6)
        head.append(tile_for(node.kind, 72, 36))
        title = Gtk.Label(label=node.name, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-2")
        head.append(title)
        g0 = Adw.PreferencesGroup()
        g0.add(head)
        page.add(g0)
        g = Adw.PreferencesGroup()
        rev = node.raw.get("activeRevision") or {}
        rows = [
            ("Typ", _("Ordner") if node.is_folder else (node.media_type or "Datei")),
            (_("Größe"), fmt_size(node.size) if node.size is not None else "–"),
            (_("Geändert"), node.mtime.strftime("%d.%m.%Y, %H:%M") if node.mtime else "–"),
            (_("Erstellt"), (parse_time(node.raw.get("creationTime")) or dt.datetime.now()).strftime("%d.%m.%Y, %H:%M")),
            (_("Besitzer"), node.owner or "–"),
            (_("Geteilt"), "Ja" if node.shared else "Nein"),
            ("Pfad", node.path),
        ]
        sha = (rev.get("claimedDigests") or {}).get("sha1")
        if sha:
            rows.append(("SHA-1", sha))
        for k, v in rows:
            r = Adw.ActionRow(title=k, subtitle=GLib.markup_escape_text(str(v)), subtitle_selectable=True)
            r.add_css_class("property")
            g.add(r)
        page.add(g)
        tv.set_content(page)
        self.set_child(tv)


class ShareDialog(Adw.Dialog):
    def __init__(self, win, node):
        super().__init__(title=_("Teilen"), content_width=480, content_height=640)
        self.win, self.node = win, node
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar(title_widget=Adw.WindowTitle(title=_("Teilen"), subtitle=node.name)))
        self.stack = Gtk.Stack()
        self.stack.add_named(Adw.Spinner(), "loading")
        self.page = Adw.PreferencesPage()
        self.stack.add_named(self.page, "page")
        tv.set_content(self.stack)
        self.set_child(tv)
        self.groups = []
        self.refresh()

    def refresh(self):
        self.stack.set_visible_child_name("loading")
        cli(["sharing", "status", self.node.path], self._build)

    def _run(self, args, msg):
        self.stack.set_visible_child_name("loading")

        def done(ok, _d, err):
            self.win.toast(msg if ok else _("Fehler: {error}").format(error=err))
            self.win.browser.cache.clear()
            self.refresh()
        cli(args, done)

    def _build(self, ok, data, err):
        for g in self.groups:
            self.page.remove(g)
        self.groups = []
        data = data if ok and isinstance(data, dict) else {}
        if not ok and err and "not shared" not in err.lower():
            self.win.toast(_("Status unbekannt: {error}").format(error=err))

        # public link
        g = Adw.PreferencesGroup(title=_("Öffentlicher Link"),
                                 description=_("Jeder mit dem Link kann zugreifen."))
        url = data.get("urlAccess")
        role = Adw.ComboRow(title=_("Berechtigung"), model=Gtk.StringList.new(["Ansehen", _("Bearbeiten")]))
        pw = Adw.ActionRow(title=_("Link-Passwort"), subtitle=_("Passwortgeschützte Links bitte in Proton Drive im Browser verwalten."))
        exp = Adw.EntryRow(title=_("Läuft ab am (JJJJ-MM-TT, optional)"))
        if url:
            link = Adw.ActionRow(title="Link", subtitle=GLib.markup_escape_text(url.get("url", "")),
                                 subtitle_selectable=True)
            copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Kopieren"))
            copy.add_css_class("flat")
            copy.connect("clicked", lambda *_: (copy_text(self, url.get("url", "")),
                                                self.win.toast(_("Link kopiert"))))
            link.add_suffix(copy)
            g.add(link)
            n = url.get("numberOfInitializedDownloads")
            if n is not None:
                g.add(Adw.ActionRow(title="Downloads", subtitle=str(n)))
            role.set_selected(1 if url.get("role") == "editor" else 0)
        for r in (role, pw, exp):
            g.add(r)

        def set_url(*_):
            args = ["sharing", "set-url", "--role", ["viewer", "editor"][role.get_selected()]]
            if exp.get_text().strip():
                args += ["--expiration", exp.get_text().strip()]
            self._run(args + [self.node.path], _("Link gespeichert"))

        b = Adw.ButtonRow(title=_("Link aktualisieren") if url else _("Link erstellen"),
                          start_icon_name="insert-link-symbolic")
        b.add_css_class("suggested-action")
        b.connect("activated", set_url)
        g.add(b)
        if url:
            rm = Adw.ButtonRow(title=_("Link löschen"), start_icon_name="user-trash-symbolic")
            rm.add_css_class("destructive-action")
            rm.connect("activated", lambda *_: self._run(["sharing", "remove-url", self.node.path],
                                                         _("Link gelöscht")))
            g.add(rm)
        self.page.add(g)
        self.groups.append(g)

        # people
        g2 = Adw.PreferencesGroup(title=_("Personen"))
        people = []
        for kind in ("members", "protonInvitations", "nonProtonInvitations"):
            for p in data.get(kind) or []:
                email = (p.get("inviteeEmail") or p.get("email") or p.get("memberEmail")
                         or (p.get("invitee") or {}).get("email") or "?")
                people.append((email, p.get("role", ""), kind != "members"))
        roles_de = {"viewer": "Ansehen", "editor": _("Bearbeiten"), "admin": _("Verwalten")}
        for email, r, pending in people:
            row = Adw.ActionRow(title=GLib.markup_escape_text(email),
                                subtitle=roles_de.get(r, r) + (" · Einladung offen" if pending else ""))
            row.add_prefix(Adw.Avatar(size=32, text=email, show_initials=True))
            x = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Entfernen"))
            x.add_css_class("flat")
            x.connect("clicked", lambda _b, e=email: self._run(
                ["sharing", "remove", "-e", e, self.node.path], _("{email} entfernt").format(email=e)))
            row.add_suffix(x)
            g2.add(row)
        email = Adw.EntryRow(title=_("E-Mail-Adresse"))
        prole = Adw.ComboRow(title=_("Berechtigung"),
                             model=Gtk.StringList.new(["Ansehen", _("Bearbeiten"), _("Verwalten")]))
        msg = Adw.EntryRow(title=_("Nachricht (optional)"))
        g2.add(email)
        g2.add(prole)
        g2.add(msg)

        def invite(*_):
            addr = email.get_text().strip()
            if not addr:
                return
            args = ["sharing", "invite", "-u", addr, "-r", ["viewer", "editor", "admin"][prole.get_selected()]]
            if msg.get_text().strip():
                args += ["-m", msg.get_text().strip()]
            self._run(args + [self.node.path], _("{email} eingeladen").format(email=addr))

        inv = Adw.ButtonRow(title=_("Einladen"), start_icon_name="contact-new-symbolic")
        inv.connect("activated", invite)
        g2.add(inv)
        if people or url:
            stop = Adw.ButtonRow(title=_("Alle Freigaben beenden"), start_icon_name="action-unavailable-symbolic")
            stop.add_css_class("destructive-action")
            stop.connect("activated", lambda *_: self._run(["sharing", "remove", "-a", self.node.path],
                                                           _("Freigaben beendet")))
            g2.add(stop)
        self.page.add(g2)
        self.groups.append(g2)
        self.stack.set_visible_child_name("page")


# ---------------------------------------------------------------- photos

SETTINGS = os.path.join(CONFIG, "settings.json")
PHOTO_STATE = os.path.join(GLib.get_user_state_dir(), "nuclivo", "photo-state.json")
SYNC_STATUS = os.path.join(GLib.get_user_state_dir(), "nuclivo", "sync-status.json")
DETAILS_PATH = os.path.join(CACHE, "photo-details.json")
THUMB_DIR = os.path.join(CACHE, "thumbs")
PHOTOS_LOCAL_DEFAULT = "~/Bilder/Proton Fotos"
# Grid batches currently loading; the background prefetcher yields while this is > 0.
_grid_activity = [0]


def load_json_file(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json_file(path, data):
    save_json(path, data)


def load_settings():
    return load_json_file(SETTINGS, {})


def save_settings(**kw):
    s = load_settings()
    s.update(kw)
    save_json_file(SETTINGS, s)


def thumb_path(uid):
    return os.path.join(THUMB_DIR, hashlib.sha1(uid.encode()).hexdigest() + ".jpg")


def thumb_failed(uid):
    return os.path.exists(thumb_path(uid) + ".fail")


def local_photo_paths():
    """uid -> local file, for photos the sync daemon keeps offline."""
    have = load_json_file(PHOTO_STATE, {}).get("have") or {}
    return {u: p for u, p in have.items() if p}


def make_thumb(src, uid):
    try:
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(src, 360, 360, True)
        pix = pix.apply_embedded_orientation() or pix
        pix.savev(thumb_path(uid), "jpeg", ["quality"], ["82"])
        return True
    except GLib.Error:
        open(thumb_path(uid) + ".fail", "w").close()
        return False


def fetch_thumbs(photos, local=None):
    """Create thumbnails for photos; uses offline copies when available, else downloads.
    Returns the number of bytes downloaded."""
    private_dir(THUMB_DIR)
    local = local if local is not None else local_photo_paths()
    remote = []
    for p in photos:
        src = local.get(p.uid)
        if src and os.path.exists(src):
            if not make_thumb(src, p.uid):
                p.failed = True
        else:
            remote.append(p)
    if not remote:
        return 0
    tmp = tempfile.mkdtemp(dir=CACHE)
    downloaded = 0
    try:
        # Photos without names (no details yet) are fetched one per call so they can be mapped.
        groups = [remote] if all(p.name for p in remote) else [[p] for p in remote]
        for group in groups:
            sub = tempfile.mkdtemp(dir=tmp)
            _ok, data, _err = cli_sync(["photo", "download", "-c", "rename",
                                        *[f"{p.source}/{p.uid}" for p in group], sub])
            if isinstance(data, dict):
                downloaded += data.get("transferredBytes") or 0
            files = os.listdir(sub)
            for p in group:
                match = p.name if p.name in files else (files[0] if len(group) == 1 and files else None)
                if not match or not make_thumb(os.path.join(sub, match), p.uid):
                    p.failed = True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return downloaded


class PhotoGrid(Adw.Bin):
    """Grid of photo thumbnails, loaded lazily in batches."""
    BATCH = 8

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.store = Gio.ListStore(item_type=Photo)
        self.pending = []
        self.inflight = 0
        self.generation = 0
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        factory.connect("unbind", self._unbind)
        self.grid = Gtk.GridView(model=Gtk.NoSelection(model=self.store), factory=factory,
                                 max_columns=12, min_columns=2, single_click_activate=True)
        self.grid.add_css_class("photo-grid")
        self.grid.connect("activate", self._activate)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.set_child(self.grid)
        self.set_child(sc)
        private_dir(THUMB_DIR)
        # Only fetch thumbnails while this grid is on screen.
        self.connect("map", lambda *_: self._pump())

    def set_photos(self, photos):
        self.generation += 1
        for p in photos:
            self.refresh_texture(p)
        self.store.splice(0, self.store.get_n_items(), photos)
        self.pending = [p for p in photos if p.texture is None and not p.failed and not p.is_video]
        self._pump()

    @staticmethod
    def refresh_texture(p):
        if p.texture is None:
            t = thumb_path(p.uid)
            if os.path.exists(t):
                try:
                    p.texture = Gdk.Texture.new_from_filename(t)
                except GLib.Error:
                    p.failed = True
            elif thumb_failed(p.uid):
                p.failed = True

    def repaint(self):
        """Pick up thumbnails created elsewhere (e.g. by the prefetcher)."""
        for i in range(self.store.get_n_items()):
            p = self.store.get_item(i)
            if p.texture is None:
                self.refresh_texture(p)
                frame = getattr(p, "_frame", None)
                if p.texture is not None and frame is not None and getattr(frame, "_photo", None) is p:
                    self._paint(frame, p)
        self.pending = [p for p in self.pending if p.texture is None and not p.failed]

    def _setup(self, _f, li):
        frame = Gtk.Overlay()
        frame.add_css_class("photo-cell")
        frame.set_size_request(150, 150)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        frame.set_child(pic)
        icon = Gtk.Image(pixel_size=32, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        icon.add_css_class("dim-label")
        frame.add_overlay(icon)
        frame._w = (pic, icon)
        li.set_child(frame)

    def _bind(self, _f, li):
        photo = li.get_item()
        frame = li.get_child()
        frame._photo = photo
        self._paint(frame, photo)
        photo._frame = frame

    def _unbind(self, _f, li):
        photo = li.get_item()
        if getattr(photo, "_frame", None) is li.get_child():
            photo._frame = None

    def _paint(self, frame, photo):
        pic, icon = frame._w
        pic.set_paintable(photo.texture)
        if photo.texture:
            icon.set_visible(False)
        else:
            icon.set_visible(True)
            icon.set_from_icon_name("video-x-generic-symbolic" if photo.is_video else
                                    "image-missing-symbolic" if photo.failed else "image-x-generic-symbolic")
        frame.set_tooltip_text(photo.capture.strftime("%d.%m.%Y, %H:%M") if photo.capture else None)

    def _pump(self):
        while self.get_mapped() and self.inflight < 2 and self.pending:
            batch, names = [], set()
            rest = []
            while self.pending and len(batch) < self.BATCH:
                p = self.pending.pop(0)
                if p.texture is not None:
                    continue
                if p.name and p.name in names:
                    rest.append(p)
                    continue
                names.add(p.name)
                batch.append(p)
            self.pending = rest + self.pending
            if batch:
                self.inflight += 1
                _grid_activity[0] += 1
                gen = self.generation
                fut = _thumb_pool.submit(fetch_thumbs, batch)
                fut.add_done_callback(lambda f, g=gen, b=batch: GLib.idle_add(self._fetched, b, g))

    def _fetched(self, batch, gen):
        self.inflight -= 1
        _grid_activity[0] -= 1
        for p in batch:
            if not p.failed and os.path.exists(thumb_path(p.uid)):
                p.texture = Gdk.Texture.new_from_filename(thumb_path(p.uid))
            elif not p.failed:
                p.failed = True
            frame = getattr(p, "_frame", None)
            if frame is not None and getattr(frame, "_photo", None) is p:
                self._paint(frame, p)
        if gen == self.generation:
            self._pump()
        return False

    def _activate(self, _g, pos):
        photo = self.store.get_item(pos)
        self.win.open_photo(photo)


class Prefetcher(GObject.Object):
    """Loads thumbnails for the whole library in the background, using about half the bandwidth."""
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}
    BATCH = 20

    def __init__(self):
        super().__init__()
        self.photos = []
        self.total = 0
        self.done = 0
        self.running = False
        self.paused = False
        self.finished = False
        self.rate = 0.0       # observed download speed in bytes/s
        self._stop = threading.Event()
        self._thread = None

    def set_photos(self, photos):
        self.photos = [p for p in photos if not p.is_video]
        self.total = len(self.photos)
        self._count()
        self.emit("changed")
        if load_settings().get("prefetch") and not self.running and not self.finished:
            self.start()

    def _count(self):
        self.done = sum(1 for p in self.photos
                        if os.path.exists(thumb_path(p.uid)) or thumb_failed(p.uid))
        self.finished = self.total > 0 and self.done >= self.total

    def start(self):
        if self.running or not self.photos:
            return
        self._stop.clear()
        self.paused = False
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.emit("changed")

    def stop(self):
        self._stop.set()
        self.running = False
        self.emit("changed")

    def toggle_pause(self):
        self.paused = not self.paused
        self.emit("changed")

    def _notify(self):
        GLib.idle_add(lambda: self.emit("changed") and False)

    def _sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end and not self._stop.is_set():
            time.sleep(0.5)

    def _run(self):
        local = local_photo_paths()
        todo = [p for p in self.photos if not os.path.exists(thumb_path(p.uid)) and not thumb_failed(p.uid)]
        # Offline copies first: no network needed.
        todo.sort(key=lambda p: p.uid not in local)
        while todo and not self._stop.is_set():
            if self.paused or _grid_activity[0] > 0:
                self._sleep(1)
                continue
            batch, names, rest = [], set(), []
            for p in todo:
                if len(batch) < self.BATCH and p.name and p.name not in names and \
                        not os.path.exists(thumb_path(p.uid)):
                    batch.append(p)
                    names.add(p.name)
                else:
                    rest.append(p)
            if not batch:
                if any(not p.name for p in rest):
                    self._sleep(10)  # waiting for photo names (details) to arrive
                    rest = [p for p in rest if not os.path.exists(thumb_path(p.uid))]
                    if all(not p.name for p in rest):
                        todo = rest
                        continue
                todo = [p for p in rest if not os.path.exists(thumb_path(p.uid))]
                continue
            todo = rest
            t0 = time.time()
            nbytes = fetch_thumbs(batch, local)
            elapsed = time.time() - t0
            self.done += len(batch)
            if nbytes:
                self.rate = nbytes / max(elapsed, 0.1)
            self._notify()
            if nbytes:
                # Throttle: idle as long as the batch took, so on average half the line stays free.
                self._sleep(elapsed)
        self.running = False
        if not self._stop.is_set():
            self._count()
            self.finished = self.done >= self.total
        self._notify()


class PhotosPage(Adw.Bin):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.photos = []
        self.details = load_json_file(DETAILS_PATH, {})
        self.months = []
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title=_("Fotos"))
        header.set_title_widget(self.title)
        up = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("Fotos hochladen"))
        up.add_css_class("suggested-action")
        up.connect("clicked", lambda *_: self.win.upload_photos())
        header.pack_start(up)
        menu = Gio.Menu()
        menu.append(_("Vorschauen im Hintergrund laden"), "win.prefetch")
        menu.append(_("Fotos offline verfügbar"), "win.photos-offline")
        sec = Gio.Menu()
        sec.append(_("Vorschau-Cache leeren"), "win.clear-thumbs")
        menu.append_section(None, sec)
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text=_("Optionen")))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Aktualisieren"))
        refresh.connect("clicked", lambda *_: self.load())
        header.pack_end(refresh)
        self.month_list = Gtk.StringList()
        self.month_dd = Gtk.DropDown(model=self.month_list, tooltip_text="Monat")
        self.month_dd.connect("notify::selected", lambda *_: self._show_month())
        header.pack_end(self.month_dd)
        self.grid = PhotoGrid(win)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.Spinner(), "loading")
        self.stack.add_named(self.grid, "grid")
        self.stack.add_named(status_page("image-x-generic-symbolic", _("Keine Fotos")), "empty")
        self.err = status_page("dialog-warning-symbolic", _("Fehler"), "", _("Erneut versuchen"), self.load)
        self.stack.add_named(self.err, "error")

        # progress bar at the bottom: thumbnail prefetch + offline download by the sync daemon
        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                    margin_top=8, margin_bottom=8, margin_start=14, margin_end=14)
        self.pf_row, self.pf_label, self.pf_bar, self.pf_btn = self._progress_row(
            "folder-download-symbolic", self._pf_button)
        self.off_row, self.off_label, self.off_bar, self.off_btn = self._progress_row("drive-harddisk-symbolic", None)
        self.progress_box.append(self.pf_row)
        self.progress_box.append(self.off_row)

        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.set_content(self.stack)
        tv.add_bottom_bar(self.progress_box)
        self.set_child(tv)
        self.loaded = False

        self.prefetcher = win.prefetcher
        self.prefetcher.connect("changed", lambda *_: self._update_progress())
        self.connect("map", lambda *_: self._poll_offline())
        self._update_progress()

    def _progress_row(self, icon, on_button):
        row = Gtk.Box(spacing=10)
        row.append(Gtk.Image(icon_name=icon))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        label = Gtk.Label(xalign=0, ellipsize=3)
        label.add_css_class("caption")
        bar = Gtk.ProgressBar()
        col.append(label)
        col.append(bar)
        row.append(col)
        btn = None
        if on_button:
            btn = Gtk.Button(valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.add_css_class("circular")
            btn.connect("clicked", lambda *_: on_button())
            row.append(btn)
        return row, label, bar, btn

    def _pf_button(self):
        self.prefetcher.toggle_pause()

    def _update_progress(self):
        pf = self.prefetcher
        show_pf = pf.running and pf.total > 0
        self.pf_row.set_visible(show_pf)
        if show_pf:
            frac = pf.done / pf.total
            self.pf_bar.set_fraction(frac)
            rate = f" · ~{GLib.format_size(int(pf.rate))}/s" if pf.rate and not pf.paused else ""
            state = _("pausiert") if pf.paused else (_("wartet, sichtbare Fotos zuerst") if _grid_activity[0] else
                                                     _("gedrosselt auf ~50 % der Leitung"))
            self.pf_label.set_text(_("Vorschauen: {done} von {total}").format(done=f"{pf.done:,}".replace(",", "."), total=f"{pf.total:,}".replace(",", ".")) +
                                   f" · {state}{rate}")
            self.pf_btn.set_icon_name("media-playback-start-symbolic" if pf.paused else
                                      "media-playback-pause-symbolic")
            self.pf_btn.set_tooltip_text(_("Fortsetzen") if pf.paused else _("Pausieren"))
        self.grid.repaint()
        self._sync_bottom_visibility()
        self.win.update_photo_activity()

    def _poll_offline(self):
        st = load_json_file(SYNC_STATUS, {}).get("photos", {})
        prog = st.get("progress")
        self.off_row.set_visible(bool(prog))
        if prog:
            done, total = prog
            self.off_bar.set_fraction(done / max(total, 1))
            self.off_label.set_text(_("Fotos werden offline gespeichert: {done} von {total}").format(done=f"{done:,}".replace(",", "."), total=f"{total:,}".replace(",", ".")) +
                                    _(" · gedrosselt auf ~50 %"))
        self._sync_bottom_visibility()
        if self.get_mapped():
            GLib.timeout_add_seconds(5, lambda: self._poll_offline() and False)
        return False

    def _sync_bottom_visibility(self):
        self.progress_box.set_visible(self.pf_row.get_visible() or self.off_row.get_visible())

    def ensure_loaded(self, show=True):
        if not self.loaded:
            self.load(show)

    def load(self, show=True):
        self.loaded = True
        self.stack.set_visible_child_name("loading")

        def done(ok, data, err):
            if not ok or not isinstance(data, list):
                self.err.set_description(GLib.markup_escape_text(err or ""))
                self.stack.set_visible_child_name("error")
                return
            self.photos = []
            for x in data:
                d = self.details.get(x["nodeUid"], {})
                self.photos.append(Photo(x["nodeUid"], x.get("captureTime"), d.get("n"), d.get("m")))
            self.title.set_subtitle(_("{count} Fotos").format(count=f"{len(self.photos):,}".replace(",", ".")))
            self._group()
            self.prefetcher.set_photos(self.photos)
            self._refresh_details()

        cli(["photo", "timeline"], done)

    def _group(self):
        months = {}
        for p in self.photos:
            key = (p.capture.year, p.capture.month) if p.capture else (0, 0)
            months.setdefault(key, []).append(p)
        self.months = sorted(months.items(), reverse=True)
        sel = self.month_dd.get_selected()
        self.month_list.splice(0, self.month_list.get_n_items(),
                               [f"{MONTHS[m - 1]} {y} · {len(ps)}" if y else _("Ohne Datum · {count}").format(count=len(ps))
                                for (y, m), ps in self.months])
        if self.months:
            self.month_dd.set_selected(min(sel, len(self.months) - 1) if sel != Gtk.INVALID_LIST_POSITION else 0)
            self._show_month()
        else:
            self.stack.set_visible_child_name("empty")

    def _show_month(self):
        i = self.month_dd.get_selected()
        if i == Gtk.INVALID_LIST_POSITION or i >= len(self.months):
            return
        self.grid.set_photos(self.months[i][1])
        self.stack.set_visible_child_name("grid")

    def library_size(self):
        """(count, bytes or None) from the details cache."""
        sizes = [d.get("s") for d in self.details.values()]
        total = sum(s for s in sizes if s) if sizes and all(s is not None for s in sizes) else None
        return len(self.details), total

    def _refresh_details(self):
        """Names are needed to batch thumbnail downloads; fetching them is slow, so cache them."""
        if self.photos and all(p.name for p in self.photos) and \
                all("s" in d for d in self.details.values()):
            return

        def work():
            ok, data, _ = cli_sync(["photo", "timeline", "-d"])
            if ok and isinstance(data, list):
                det = {x["uid"]: {"n": (x.get("name") or {}).get("value"), "m": x.get("mediaType"),
                                  "s": x.get("totalStorageSize")}
                       for x in data if x.get("uid")}
                save_json_file(DETAILS_PATH, det)
                return det
            return None

        def done(f):
            det = f.result()
            if det:
                GLib.idle_add(self._apply_details, det)

        _browse_pool.submit(work).add_done_callback(done)

    def _apply_details(self, det):
        self.details = det
        for p in self.photos:
            d = det.get(p.uid)
            if d:
                p.name, p.media_type = d["n"], d["m"]
        self._show_month()
        self.prefetcher.set_photos(self.photos)
        return False


class SetupDialog(Adw.Dialog):
    """First-run choices for photos."""

    def __init__(self, win):
        super().__init__(title=_("Willkommen"), content_width=520, can_close=False)
        self.win = win
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
        page = Adw.PreferencesPage()
        hero = Adw.StatusPage(icon_name="folder-remote-symbolic", title=_("Willkommen bei Nuclivo"),
                              description=_("Zwei Fragen zu deinen Fotos. Beides kannst du später "
                                            "jederzeit oben rechts im Fotos-Menü ändern."))
        hero.add_css_class("compact")
        g0 = Adw.PreferencesGroup()
        g0.add(hero)
        page.add(g0)
        count, size = win.photos.library_size()
        count_txt = _("alle {count} Fotos").format(count=f"{count:,}".replace(",", ".")) if count else _("alle Fotos")
        size_txt = f" (≈ {GLib.format_size(size)})" if size else ""
        g = Adw.PreferencesGroup()
        self.prefetch = Adw.SwitchRow(
            title=_("Vorschauen im Hintergrund laden"),
            subtitle=_("Damit Monate sofort erscheinen. Braucht nur wenig Platz, lädt aber einmalig jedes Foto. "
                       "Gedrosselt auf ~50 % der Leitung, nur solange Nuclivo offen ist."))
        self.prefetch.set_active(True)
        self.offline = Adw.SwitchRow(
            title=_("Fotos offline verfügbar machen"),
            subtitle=_("Speichert {count}{size} in {folder} und hält sie aktuell. "
                       "Vorschauen entstehen dann direkt aus diesen Dateien.").format(
                           count=count_txt, size=size_txt, folder=PHOTOS_LOCAL_DEFAULT))
        g.add(self.prefetch)
        g.add(self.offline)
        page.add(g)
        g2 = Adw.PreferencesGroup()
        go = Adw.ButtonRow(title=_("Los geht’s"))
        go.add_css_class("suggested-action")
        go.connect("activated", self._done)
        g2.add(go)
        page.add(g2)
        tv.set_content(page)
        self.set_child(tv)

    def _done(self, *_):
        save_settings(setup_done=True)
        self.win.set_prefetch(self.prefetch.get_active())
        if self.offline.get_active():
            self.win.set_photos_offline(True)
        self.force_close()


class AlbumsPage(Adw.Bin):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.loaded = False
        self.nav = Adw.NavigationView()
        header = Adw.HeaderBar()
        new = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("Neues Album"))
        new.add_css_class("suggested-action")
        new.connect("clicked", lambda *_: entry_dialog(self.win, _("Neues Album"), "", "", _("Erstellen"),
                                                       self._create))
        header.pack_start(new)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Aktualisieren"))
        refresh.connect("clicked", lambda *_: self.load())
        header.pack_end(refresh)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12,
                                max_children_per_line=6, min_children_per_line=1, homogeneous=True,
                                margin_top=18, margin_bottom=18, margin_start=18, margin_end=18,
                                valign=Gtk.Align.START)
        self.flow.connect("child-activated", lambda _f, c: self.open_album(c._album))
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.Spinner(), "loading")
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_child(self.flow)
        self.stack.add_named(sc, "list")
        self.stack.add_named(status_page("folder-pictures-symbolic", _("Keine Alben")), "empty")
        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.set_content(self.stack)
        self.nav.add(Adw.NavigationPage(child=tv, title=_("Alben"), tag="albums"))
        self.set_child(self.nav)

    def ensure_loaded(self):
        if not self.loaded:
            self.load()

    def load(self):
        self.loaded = True
        self.stack.set_visible_child_name("loading")

        def done(ok, data, err):
            while (c := self.flow.get_child_at_index(0)):
                self.flow.remove(c)
            if not ok or not isinstance(data, list):
                self.win.toast(_("Alben: {error}").format(error=err))
                self.stack.set_visible_child_name("empty")
                return
            for a in sorted(data, key=lambda a: (a.get("name") or {}).get("value", "").casefold()):
                node = Node(a, "/albums")
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
                card.add_css_class("album-card")
                icon = Gtk.Box(halign=Gtk.Align.CENTER)
                icon.add_css_class("album-icon")
                icon.append(Gtk.Image(icon_name="folder-pictures-symbolic", pixel_size=30, hexpand=True))
                icon.set_hexpand(False)
                card.append(icon)
                lbl = Gtk.Label(label=node.name, ellipsize=3, max_width_chars=18)
                lbl.add_css_class("heading")
                card.append(lbl)
                sub = Gtk.Label(label=fmt_time(node.mtime))
                sub.add_css_class("dim-label")
                sub.add_css_class("caption")
                card.append(sub)
                child = Gtk.FlowBoxChild(child=card)
                child._album = node
                click = Gtk.GestureClick(button=3)
                click.connect("pressed", lambda g, n, x, y, nd=node, c=child: self._album_menu(nd, c, x, y))
                child.add_controller(click)
                self.flow.append(child)
            self.stack.set_visible_child_name("list" if data else "empty")

        cli(["album", "list"], done)

    def _album_menu(self, node, widget, x, y):
        m = Gio.Menu()
        m.append_item(menu_item(_("Umbenennen …"), "win.album-rename", node.path))
        m.append_item(menu_item(_("Album löschen"), "win.album-delete", node.path))
        show_menu(m, widget, x, y)

    def _create(self, name):
        cli(["album", "create", name], lambda ok, d, e: (self.win.toast(_("Album erstellt") if ok else _("Fehler: {error}").format(error=e)),
                                                          self.load()))

    def open_album(self, node):
        grid = PhotoGrid(self.win)
        stack = Gtk.Stack()
        stack.add_named(Adw.Spinner(), "loading")
        stack.add_named(grid, "grid")
        stack.add_named(status_page("image-x-generic-symbolic", _("Album ist leer")), "empty")
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title=node.name)
        header.set_title_widget(title)
        tv.add_top_bar(header)
        tv.set_content(stack)
        self.nav.push(Adw.NavigationPage(child=tv, title=node.name))

        def done(ok, data, err):
            if not ok or not isinstance(data, list):
                self.win.toast(_("Fehler: {error}").format(error=err))
                stack.set_visible_child_name("empty")
                return
            photos = [Photo(x.get("uid") or x.get("nodeUid"),
                            x.get("captureTime") or (x.get("photo") or {}).get("captureTime")
                            or x.get("creationTime"),
                            (x.get("name") or {}).get("value"), x.get("mediaType"),
                            source="/photos") for x in data]
            title.set_subtitle(_("{count} Fotos").format(count=len(photos)))
            grid.set_photos(photos)
            stack.set_visible_child_name("grid" if photos else "empty")

        cli(["album", "photos", "-d", node.path], done)


# ---------------------------------------------------------------- sync

class SyncPage(Adw.Bin):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.cfg = {}
        self.dynamic = []
        self._updating = False
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=_("Synchronisation")))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Aktualisieren"))
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_end(refresh)
        self.page = Adw.PreferencesPage()

        # hero
        self.hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.hero.add_css_class("sync-hero")
        self.hero_icon = Gtk.Image(pixel_size=56)
        self.hero_title = Gtk.Label()
        self.hero_title.add_css_class("title-2")
        self.hero_sub = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.hero_sub.add_css_class("dim-label")
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=8)
        self.sync_btn = Gtk.Button(label=_("Jetzt synchronisieren"))
        self.sync_btn.add_css_class("pill")
        self.sync_btn.add_css_class("suggested-action")
        self.sync_btn.connect("clicked", lambda *_: self.sync_now())
        self.power_btn = Gtk.Button()
        self.power_btn.add_css_class("pill")
        self.power_btn.connect("clicked", lambda *_: self._toggle_service())
        btns.append(self.sync_btn)
        btns.append(self.power_btn)
        for w in (self.hero_icon, self.hero_title, self.hero_sub, btns):
            self.hero.append(w)
        g = Adw.PreferencesGroup()
        g.add(self.hero)
        self.page.add(g)

        # account
        self.acct_group = Adw.PreferencesGroup(
            title=_("Konto"), description=_("Der Datei-Sync nutzt rclone und braucht eine eigene Anmeldung."))
        self.acct_row = Adw.ActionRow(title="rclone-Verbindung")
        self.acct_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self.acct_btn.connect("clicked", lambda *_: RcloneLoginDialog(self.win, self.refresh).present(self.win))
        self.acct_row.add_suffix(self.acct_btn)
        self.acct_group.add(self.acct_row)
        self.page.add(self.acct_group)

        # pairs
        self.pairs_group = Adw.PreferencesGroup(
            title=_("Ordner"), description=_("Diese Ordner werden in beide Richtungen abgeglichen."))
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Ordner hinzufügen"))
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self.add_pair())
        self.pairs_group.set_header_suffix(add)
        self.page.add(self.pairs_group)

        # photos
        pg = Adw.PreferencesGroup(title=_("Fotos"),
                                  description=_("Proton-Fotos mit einem lokalen Ordner abgleichen. "
                                                "Löschungen werden nie übertragen."))
        self.photo_offline = Adw.SwitchRow(title=_("Alle Fotos offline verfügbar"),
                                           subtitle=_("Lädt die ganze Mediathek, gedrosselt auf ~50 % der Leitung"))
        self.photo_offline.connect("notify::active", lambda r, _p: None if self._updating
                                   else self.win.set_photos_offline(r.get_active()))
        pg.add(self.photo_offline)
        self.photo_enable = Adw.SwitchRow(title=_("Fotos synchronisieren"))
        self.photo_dir = Adw.ActionRow(title=_("Lokaler Ordner"))
        pick = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Ändern"))
        pick.add_css_class("flat")
        pick.connect("clicked", lambda *_: self.pick_photo_dir())
        self.photo_dir.add_suffix(pick)
        self.photo_down = Adw.SwitchRow(title=_("Neue Fotos aus Proton herunterladen"),
                                        subtitle=_("Sortiert nach Jahr/Monat"))
        self.photo_existing = Adw.SwitchRow(title=_("Auch alle bisherigen Fotos herunterladen"),
                                            subtitle=_("Sonst nur Fotos ab dem Einschalten"))
        self.photo_up = Adw.SwitchRow(title=_("Neue Fotos aus dem Ordner hochladen"))
        for r in (self.photo_enable, self.photo_dir, self.photo_down, self.photo_existing, self.photo_up):
            pg.add(r)
        for r, key in ((self.photo_enable, "enabled"), (self.photo_down, "download"),
                       (self.photo_existing, "download_existing"), (self.photo_up, "upload")):
            r.connect("notify::active", self._photo_toggled, key)
        self.photo_rows = (self.photo_dir, self.photo_down, self.photo_existing, self.photo_up)
        self.page.add(pg)

        # interval
        ig = Adw.PreferencesGroup(title=_("Abfrage"))
        self.interval = Adw.SpinRow.new_with_range(1, 120, 1)
        self.interval.set_title(_("Änderungen in Proton prüfen alle … Minuten"))
        self.interval.set_subtitle(_("Lokale Änderungen werden sofort übertragen"))
        self.interval.connect("notify::value", self._interval_changed)
        ig.add(self.interval)
        self.page.add(ig)

        lg = Adw.PreferencesGroup(title=_("Protokoll"))
        self.log = Gtk.Label(xalign=0, yalign=0, wrap=True, wrap_mode=2, selectable=True)
        self.log.add_css_class("log-view")
        self.log.add_css_class("card")
        lg.add(self.log)
        self.page.add(lg)
        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.set_content(self.page)
        self.set_child(tv)
        self.connect("map", lambda *_: self._start_autorefresh())
        self._timer = None

    # ---- config
    def _load_cfg(self):
        try:
            with open(SYNC_CONFIG) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_cfg(self):
        save_json_file(SYNC_CONFIG, self.cfg)
        subprocess.run(["systemctl", "--user", "reload", SYNC_UNIT], capture_output=True)

    def _systemctl(self, *args):
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)

    def _start_autorefresh(self):
        self.refresh()
        if self._timer is None:
            def tick():
                if not self.get_mapped():
                    self._timer = None
                    return False
                self.refresh()
                return True
            self._timer = GLib.timeout_add_seconds(5, tick)

    def refresh(self):
        def work():
            state = self._systemctl("is-active", SYNC_UNIT).stdout.strip()
            log = subprocess.run(["journalctl", "--user", "-u", SYNC_UNIT, "-n", "30", "--no-pager",
                                  "-o", "cat"], capture_output=True, text=True).stdout
            remotes = subprocess.run([os.path.expanduser("~/.local/bin/rclone"), "listremotes"],
                                     capture_output=True, text=True).stdout
            status = {}
            try:
                with open(os.path.join(GLib.get_user_state_dir(), "nuclivo", "sync-status.json")) as f:
                    status = json.load(f)
            except (OSError, ValueError):
                pass
            return state, log, "nuclivo-proton:" in remotes, status

        _browse_pool.submit(work).add_done_callback(lambda f: GLib.idle_add(self._apply, *f.result()))

    def _status_suffix(self, st):
        if st.get("running"):
            return _("Synchronisiert gerade …"), None
        if st.get("error"):
            return _("Fehler: {error}").format(error=st['error']), "dialog-error-symbolic"
        if st.get("last"):
            return _("Zuletzt {time}").format(time=fmt_time(parse_time(st['last']))), "object-select-symbolic"
        return _("Wartet auf ersten Abgleich"), None

    def _apply(self, state, log, connected, status):
        self.cfg = self._load_cfg()
        self._updating = True
        for r in self.dynamic:
            self.pairs_group.remove(r)
        self.dynamic = []
        home = os.path.expanduser("~")
        for p in self.cfg.get("pairs", []):
            text, icon = self._status_suffix(status.get(p["local"], {}))
            r = Adw.ActionRow(title=GLib.markup_escape_text(p["local"].replace(home, "~") + "  ⇄  " +
                                                            p["remote"].replace("/my-files", "Drive")),
                              subtitle=GLib.markup_escape_text(text), subtitle_lines=2)
            r.add_prefix(tile_for("folder", 32, 16))
            if status.get(p["local"], {}).get("running"):
                r.add_suffix(Adw.Spinner())
            elif icon:
                r.add_suffix(Gtk.Image(icon_name=icon))
            ob = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Öffnen"))
            ob.add_css_class("flat")
            ob.connect("clicked", lambda _b, d=p["local"]: open_local(os.path.expanduser(d)))
            rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Nicht mehr synchronisieren"))
            rm.add_css_class("flat")
            rm.connect("clicked", lambda _b, pp=p: self.remove_pair(pp))
            r.add_suffix(ob)
            r.add_suffix(rm)
            self.pairs_group.add(r)
            self.dynamic.append(r)
        if not self.cfg.get("pairs"):
            r = Adw.ActionRow(title=_("Noch keine Ordner"), subtitle=_("Mit + einen Ordner hinzufügen"))
            self.pairs_group.add(r)
            self.dynamic.append(r)

        ph = self.cfg.get("photos", {})
        self.photo_offline.set_active(self.win.photos_offline())
        prog = status.get("photos", {}).get("progress")
        self.photo_offline.set_subtitle(
            _("Wird gespeichert: {done} von {total} · gedrosselt").format(
                done=f"{prog[0]:,}".replace(",", "."),
                total=f"{prog[1]:,}".replace(",", ".")) if prog else
            _("Lädt die ganze Mediathek, gedrosselt auf ~50 % der Leitung"))
        self.photo_enable.set_active(bool(ph.get("enabled")))
        self.photo_down.set_active(ph.get("download", True))
        self.photo_existing.set_active(bool(ph.get("download_existing")))
        self.photo_up.set_active(bool(ph.get("upload")))
        pdir = ph.get("local") or "~/Bilder/Proton Fotos"
        text, status_icon = self._status_suffix(status.get("photos", {})) if ph.get("enabled") else ("", None)
        self.photo_dir.set_subtitle(GLib.markup_escape_text(pdir.replace(home, "~") + (f" · {text}" if text else "")))
        for r in self.photo_rows:
            r.set_sensitive(bool(ph.get("enabled")))
        self.photo_existing.set_sensitive(bool(ph.get("enabled")) and ph.get("download", True))
        self.interval.set_value(self.cfg.get("poll_minutes", 5))
        self._updating = False

        self.acct_row.set_subtitle(_("Verbunden") if connected else _("Nicht verbunden"))
        self.acct_btn.set_label(_("Neu verbinden") if connected else _("Verbinden"))
        for c in ("suggested-action",):
            self.acct_btn.remove_css_class(c)
        if not connected:
            self.acct_btn.add_css_class("suggested-action")

        active = state in ("active", "activating", "reloading")
        anything = bool(self.cfg.get("pairs")) or bool(ph.get("enabled"))
        running = any(s.get("running") for s in status.values())
        for c in ("sync-ok", "sync-off"):
            self.hero.remove_css_class(c)
        self.hero.add_css_class("sync-ok" if active and anything else "sync-off")
        self.power_btn.set_label(_("Pausieren") if active else _("Starten"))
        self.power_btn.set_visible(anything)
        self.sync_btn.set_visible(active and anything)
        self.sync_btn.set_sensitive(not running)
        if not anything:
            self.hero_icon.set_from_icon_name("view-refresh-symbolic")
            self.hero_title.set_text(_("Noch nicht eingerichtet"))
            self.hero_sub.set_text(_("Füge unten einen Ordner hinzu oder schalte den Foto-Sync ein."))
        elif not active:
            self.hero_icon.set_from_icon_name("media-playback-pause-symbolic")
            self.hero_title.set_text(_("Pausiert"))
            self.hero_sub.set_text(_("Der Sync-Dienst läuft gerade nicht."))
        elif running:
            self.hero_icon.set_from_icon_name("view-refresh-symbolic")
            self.hero_title.set_text(_("Synchronisiert …"))
            self.hero_sub.set_text(_("Änderungen werden gerade abgeglichen."))
        elif any(s.get("error") for s in status.values()):
            self.hero_icon.set_from_icon_name("dialog-warning-symbolic")
            self.hero_title.set_text(_("Live-Sync läuft, mit Fehlern"))
            self.hero_sub.set_text(_("Details stehen unten im Protokoll."))
        else:
            self.hero_icon.set_from_icon_name("object-select-symbolic")
            self.hero_title.set_text(_("Alles synchron"))
            self.hero_sub.set_text(_("Lokale Änderungen werden sofort übertragen, Proton wird alle {minutes} Minuten geprüft.").format(minutes=self.cfg.get("poll_minutes", 5)))
        self.log.set_text("\n".join(log.strip().splitlines()[-30:]) or _("Noch keine Einträge."))
        return False

    # ---- editing
    def add_pair(self):
        d = Gtk.FileDialog(title=_("Lokalen Ordner wählen"))

        def got_local(dlg, res):
            try:
                local = dlg.select_folder_finish(res).get_path()
            except GLib.Error:
                return
            name = os.path.basename(local.rstrip("/"))

            def got_remote(remote):
                pairs = self.cfg.setdefault("pairs", [])
                if any(p["local"] == local for p in pairs):
                    self.win.toast(_("Dieser Ordner wird schon synchronisiert"))
                    return
                pairs.append({"local": local, "remote": remote})
                self._save_cfg()
                self._ensure_running()
                self.win.toast(_("Ordner hinzugefügt – erster Abgleich startet"))
                self.refresh()

            def choose_remote():
                picker = FolderPicker(self.win, _("Ziel für „{name}“ in Drive").format(name=name),
                                      _("Diesen Ordner verwenden"), got_remote)
                picker.present(self.win)

            dlg = Adw.AlertDialog(heading=_("Ziel in Proton Drive"),
                                  body=_("Wohin soll „{name}“ synchronisiert werden?").format(name=name))
            dlg.add_response("cancel", _("Abbrechen"))
            dlg.add_response("pick", _("Bestehenden Ordner wählen …"))
            dlg.add_response("new", _("Neu: Meine Dateien/{name}").format(name=name))
            dlg.set_response_appearance("new", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_prefer_wide_layout(False)
            dlg.set_close_response("cancel")
            dlg.connect("response", lambda _d, r: choose_remote() if r == "pick" else
                        got_remote(f"/my-files/{esc(name)}") if r == "new" else None)
            dlg.present(self.win)

        d.select_folder(self.win, None, got_local)

    def remove_pair(self, pair):
        def go():
            self.cfg["pairs"] = [p for p in self.cfg.get("pairs", []) if p["local"] != pair["local"]]
            self._save_cfg()
            self.refresh()
        confirm_dialog(self.win, _("Nicht mehr synchronisieren?"),
                       _("Die Dateien bleiben lokal und in Proton Drive erhalten."), _("Entfernen"), go)

    def _photo_toggled(self, row, _p, key):
        if self._updating:
            return
        ph = self.cfg.setdefault("photos", {"download": True})
        ph[key] = row.get_active()
        if key == "enabled" and row.get_active():
            ph.setdefault("local", "~/Bilder/Proton Fotos")
            self._ensure_running()
        self._save_cfg()
        self.refresh()

    def pick_photo_dir(self):
        d = Gtk.FileDialog(title=_("Ordner für Fotos"))

        def cb(dlg, res):
            try:
                path = dlg.select_folder_finish(res).get_path()
            except GLib.Error:
                return
            self.cfg.setdefault("photos", {})["local"] = path
            self._save_cfg()
            self.refresh()
        d.select_folder(self.win, None, cb)

    def _interval_changed(self, row, _p):
        if self._updating:
            return
        self.cfg["poll_minutes"] = int(row.get_value())
        self._save_cfg()

    def _ensure_running(self):
        self._systemctl("enable", "--now", SYNC_UNIT)

    def _toggle_service(self):
        active = self._systemctl("is-active", SYNC_UNIT).stdout.strip() == "active"
        if active:
            self._systemctl("disable", "--now", SYNC_UNIT)
        else:
            self._systemctl("enable", "--now", SYNC_UNIT)
        GLib.timeout_add(600, lambda: self.refresh() and False)

    def sync_now(self):
        self._systemctl("kill", "-s", "USR1", SYNC_UNIT)
        self.win.toast(_("Synchronisation gestartet"))
        GLib.timeout_add_seconds(2, lambda: self.refresh() and False)


class RcloneLoginDialog(Adw.Dialog):
    """Delegate credential entry to rclone's interactive terminal prompt."""
    def __init__(self, win, on_done):
        super().__init__(title=_("Sync-Anmeldung einrichten"), content_width=480)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description=_("Führe im Terminal ~/.local/bin/rclone config aus. "
              "Erstelle einen Remote namens nuclivo-proton mit Typ protondrive. "
              "Gib deine Zugangsdaten ausschließlich an den interaktiven Eingabeaufforderungen ein. "
              "rclone speichert Passwörter wiederherstellbar in seiner Konfiguration; "
              "schütze diese Datei und teile sie niemals."))
        button = Adw.ButtonRow(title=_("Befehl kopieren"))
        button.connect("activated", lambda *_: copy_text(self, "~/.local/bin/rclone config"))
        group.add(button)
        done = Adw.ButtonRow(title=_("Einrichtung abgeschlossen"))
        done.connect("activated", lambda *_: (self.close(), on_done()))
        group.add(done)
        page.add(group)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(page)
        self.set_child(toolbar)


# ---------------------------------------------------------------- Proton Docs

_web_session = None


def web_session():
    """One persistent WebKit session, so the Proton login survives restarts."""
    global _web_session
    if _web_session is None:
        base = os.path.join(GLib.get_user_data_dir(), "nuclivo", "web")
        private_dir(base)
        private_dir(os.path.join(CACHE, "web"))
        _web_session = WebKit.NetworkSession.new(os.path.join(base, "data"), os.path.join(CACHE, "web"))
        _web_session.get_cookie_manager().set_persistent_storage(
            os.path.join(base, "cookies.sqlite"), WebKit.CookiePersistentStorage.SQLITE)
    return _web_session


def docs_url(node):
    volume, _, link = node.uid.partition("~")
    kind = "sheet" if node.media_type == "application/vnd.proton.sheet" else "doc"
    q = urllib.parse.urlencode({"mode": "open", "volumeId": volume, "linkId": link})
    return f"https://docs.proton.me/{kind}?{q}"


def docs_create_url(kind, folder_uid):
    volume, _, parent = folder_uid.partition("~")
    q = urllib.parse.urlencode({"mode": "create", "volumeId": volume, "parentLinkId": parent})
    return f"https://docs.proton.me/{kind}?{q}"




class WebPane(Gtk.Overlay):
    """WebKit view for Proton web apps; links outside proton.me open in the default browser."""

    def __init__(self, app, related=None):
        super().__init__()
        self.app = app
        if related is not None:
            self.view = WebKit.WebView(related_view=related)
        else:
            self.view = WebKit.WebView(network_session=web_session())
        self.view.get_settings().set_javascript_can_open_windows_automatically(True)
        self.view.connect("decide-policy", self._policy)
        self.view.connect("create", self._create)
        self.view.connect("notify::estimated-load-progress", self._progress)
        self.set_child(self.view)
        self.bar = Gtk.ProgressBar(valign=Gtk.Align.START)
        self.bar.add_css_class("osd")
        self.add_overlay(self.bar)

    def load(self, url):
        self.view.load_uri(url)

    def _progress(self, view, _p):
        frac = view.get_estimated_load_progress()
        self.bar.set_fraction(frac)
        self.bar.set_visible(frac < 1.0)

    def _policy(self, view, decision, dtype):
        if dtype == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not is_proton(uri) and uri != "about:blank":
                if urllib.parse.urlsplit(uri).scheme == "https":
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                decision.ignore()
                return True
        return False

    def _create(self, view, action):
        uri = action.get_request().get_uri()
        if uri and not is_proton(uri):
            if urllib.parse.urlsplit(uri).scheme == "https":
                Gio.AppInfo.launch_default_for_uri(uri, None)
            return None
        # Proton popups (e.g. sign-in helpers) get their own window.
        return DocsWindow(self.app, None, "Proton", related=view).pane.view


def web_header(pane, fallback_title, on_back=None, on_detach=None):
    header = Adw.HeaderBar()
    title = Adw.WindowTitle(title=fallback_title)
    header.set_title_widget(title)
    view = pane.view

    def upd_title(*_):
        t = view.get_title() or fallback_title
        title.set_title(t.removesuffix(" | Proton Docs").removesuffix(" - Proton Docs"))
    view.connect("notify::title", upd_title)
    view.connect("notify::uri", lambda v, _p: title.set_subtitle(
        urllib.parse.urlparse(v.get_uri() or "").hostname or ""))
    if on_back:
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=_("Zurück zu den Dateien"))
        back.connect("clicked", lambda *_: on_back())
        header.pack_start(back)
    reload = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Neu laden"))
    reload.connect("clicked", lambda *_: view.reload())
    header.pack_start(reload)
    ext = Gtk.Button(icon_name="send-to-symbolic", tooltip_text=_("Im Browser öffnen"))
    ext.connect("clicked", lambda *_: view.get_uri() and Gio.AppInfo.launch_default_for_uri(view.get_uri(), None))
    header.pack_end(ext)
    if on_detach:
        det = Gtk.Button(icon_name="window-new-symbolic", tooltip_text=_("In eigenem Fenster öffnen"))
        det.connect("clicked", lambda *_: on_detach())
        header.pack_end(det)
    return header


class DocsWindow(Adw.Window):
    def __init__(self, app, url, title, related=None):
        super().__init__(application=app, title=title, default_width=1100, default_height=820)
        self.pane = WebPane(app, related)
        if related is not None:
            self.pane.view.connect("ready-to-show", lambda *_: self.present())
            self.pane.view.connect("close", lambda *_: self.close())
        tv = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.RAISED)
        tv.add_top_bar(web_header(self.pane, title))
        tv.set_content(self.pane)
        self.set_content(tv)
        if url:
            self.pane.load(url)


class DocsPage(Adw.Bin):
    """Proton Docs/Sheets shown inside the main window."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.pane = WebPane(win.get_application())
        tv = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.RAISED)
        tv.add_top_bar(web_header(self.pane, "Proton Docs", on_back=self.leave, on_detach=self.detach))
        tv.set_content(self.pane)
        self.set_child(tv)
        self.return_to = "browser"

    def open(self, url, title, return_to="browser"):
        self.return_to = return_to
        self.pane.load(url)

    def leave(self):
        self.pane.view.load_uri("about:blank")
        self.win.content.set_visible_child_name(self.return_to)
        # A document may have been created or renamed meanwhile.
        if self.return_to == "browser":
            self.win.browser.invalidate()

    def detach(self):
        uri = self.pane.view.get_uri()
        if uri and uri != "about:blank":
            DocsWindow(self.win.get_application(), uri, self.pane.view.get_title() or "Proton Docs").present()
        self.leave()


# ---------------------------------------------------------------- window

class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Nuclivo", default_width=1180, default_height=760)
        self.set_size_request(360, 400)
        self.transfers = Gio.ListStore(item_type=Transfer)

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=280)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        # sidebar
        sb_header = Adw.HeaderBar()
        sb_header.set_title_widget(Adw.WindowTitle(title="Nuclivo"))
        menu = Gio.Menu()
        menu.append(_("In Proton Drive (Web) öffnen"), "win.open-web")
        menu.append(_("Abmelden"), "win.logout")
        language_menu = Gio.Menu()
        language_menu.append("Deutsch", "win.language-de")
        language_menu.append("English", "win.language-en")
        menu.append_submenu(_("Sprache"), language_menu)
        menu.append(_("Über Nuclivo"), "win.about")
        sb_header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                          tooltip_text=_("Hauptmenü")))
        self.transfer_btn = self._build_transfer_button()
        sb_header.pack_start(self.transfer_btn)

        self.sidebar = Gtk.ListBox()
        self.sidebar.add_css_class("navigation-sidebar")
        self.sidebar.connect("row-selected", self._on_sidebar)
        entries = [("my-files", None), ("shared-by-me", None), ("shared-with-me", None), ("devices", None),
                   ("photos", (_("Fotos"), "image-x-generic-symbolic")),
                   ("albums", (_("Alben"), "folder-pictures-symbolic")),
                   ("trash", None),
                   ("sync", (_("Synchronisation"), "view-refresh-symbolic"))]
        for key, meta in entries:
            label, icon = (SECTIONS[key][1], SECTIONS[key][2]) if meta is None else meta
            box = Gtk.Box(spacing=12, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            if key == "photos":
                self.photo_spinner = Adw.Spinner(visible=False, tooltip_text=_("Vorschauen werden geladen"))
                box.append(self.photo_spinner)
            row = Gtk.ListBoxRow(child=box)
            row._key = key
            self.sidebar.append(row)
        self.sidebar.set_header_func(self._sidebar_header)
        sb_scroll = Gtk.ScrolledWindow(vexpand=True)
        sb_scroll.set_child(self.sidebar)
        sb_tv = Adw.ToolbarView()
        sb_tv.add_top_bar(sb_header)
        sb_tv.set_content(sb_scroll)
        self.split.set_sidebar(Adw.NavigationPage(child=sb_tv, title="Nuclivo"))

        # content
        self.prefetcher = Prefetcher()
        self.browser = BrowserPage(self)
        self.photos = PhotosPage(self)
        self.albums = AlbumsPage(self)
        self.sync = SyncPage(self)
        self.content = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        for name, w in (("browser", self.browser), ("photos", self.photos),
                        ("albums", self.albums), ("sync", self.sync)):
            self.content.add_named(w, name)
        self.docs = DocsPage(self) if WebKit is not None else None
        if self.docs:
            self.content.add_named(self.docs, "docs")
        self.content_page = Adw.NavigationPage(child=self.content, title=_("Meine Dateien"))
        self.split.set_content(self.content_page)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 680sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self._install_actions()
        self.sidebar.select_row(self.sidebar.get_row_at_index(0))
        settings = load_settings()
        if not settings.get("setup_done"):
            GLib.timeout_add(600, lambda: SetupDialog(self).present(self) and False)
        elif settings.get("prefetch"):
            self.photos.ensure_loaded(show=False)

    # ---- sidebar
    def _sidebar_header(self, row, before):
        titles = {"photos": _("Fotos"), "trash": None}
        if row._key in ("photos", "trash") and before is not None:
            sep = Gtk.Separator(margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
            row.set_header(sep)
        else:
            row.set_header(None)
        return titles

    def _on_sidebar(self, _lb, row):
        if row is None:
            return
        key = row._key
        if key in SECTIONS:
            self.content.set_visible_child_name("browser")
            self.browser.open_section(key)
            self.content_page.set_title(SECTIONS[key][1])
        elif key == "photos":
            self.content.set_visible_child_name("photos")
            self.photos.ensure_loaded()
            self.content_page.set_title(_("Fotos"))
        elif key == "albums":
            self.content.set_visible_child_name("albums")
            self.albums.ensure_loaded()
            self.content_page.set_title(_("Alben"))
        elif key == "sync":
            self.content.set_visible_child_name("sync")
            self.sync.refresh()
            self.content_page.set_title(_("Synchronisation"))
        self.split.set_show_content(True)

    # ---- transfers
    def _build_transfer_button(self):
        self.transfer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.transfer_list.add_css_class("boxed-list")
        self.transfer_list.bind_model(self.transfers, self._transfer_row)
        self.transfer_list.set_placeholder(Gtk.Label(label=_("Keine Übertragungen"), margin_top=18,
                                                     margin_bottom=18, css_classes=["dim-label"]))
        sc = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=420,
                                hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.set_child(self.transfer_list)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=340)
        box.append(sc)
        clear = Gtk.Button(label=_("Erledigte entfernen"))
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self._clear_transfers())
        box.append(clear)
        pop = Gtk.Popover(child=box)
        icon_stack = Gtk.Stack()
        icon_stack.add_named(Gtk.Image(icon_name="folder-download-symbolic"), "idle")
        icon_stack.add_named(Adw.Spinner(), "busy")
        self.transfer_icon = icon_stack
        btn = Gtk.MenuButton(child=icon_stack, popover=pop, tooltip_text=_("Übertragungen"))
        return btn

    def _transfer_row(self, t):
        row = Adw.ActionRow(title=GLib.markup_escape_text(t.title), subtitle=GLib.markup_escape_text(t.detail))
        row.set_title_lines(1)
        if t.state == "running":
            row.add_prefix(Adw.Spinner())
        else:
            img = Gtk.Image(icon_name="object-select-symbolic" if t.state == "done" else "dialog-error-symbolic")
            img.add_css_class("success" if t.state == "done" else "error")
            row.add_prefix(img)
        if getattr(t, "local", None) and t.state == "done":
            b = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Ordner öffnen"))
            b.add_css_class("flat")
            b.connect("clicked", lambda *_: open_local(t.local))
            row.add_suffix(b)
        return row

    def _clear_transfers(self):
        keep = [t for t in self.transfers if t.state == "running"]
        self.transfers.splice(0, self.transfers.get_n_items(), keep)

    def _update_transfer_icon(self):
        busy = any(t.state == "running" for t in self.transfers)
        self.transfer_icon.set_visible_child_name("busy" if busy else "idle")

    def start_transfer(self, title, icon, args, on_done=None, local=None):
        t = Transfer(title, icon)
        t.local = local
        self.transfers.insert(0, t)
        self._update_transfer_icon()

        def done(ok, data, err):
            t.state = "done" if ok else "failed"
            if ok and isinstance(data, dict):
                n, b = data.get("transferredItems", 0), data.get("transferredBytes", 0)
                skipped = data.get("skippedItems", 0)
                t.detail = _("{count} Element(e), {size}").format(count=n, size=fmt_size(b)) + (_(", {count} übersprungen").format(count=skipped) if skipped else "")
            else:
                t.detail = err or (_("Fertig") if ok else _("Fehlgeschlagen"))
            pos = next((i for i in range(self.transfers.get_n_items()) if self.transfers.get_item(i) is t), None)
            if pos is not None:
                self.transfers.splice(pos, 1, [t])
            self._update_transfer_icon()
            if on_done:
                on_done(ok, data, err)

        cli(args, done, pool=_transfer_pool)
        return t

    # ---- actions
    def _install_actions(self):
        def add(name, cb, param=True):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s") if param else None)
            a.connect("activate", lambda _a, v: cb(v.get_string()) if param else cb())
            self.add_action(a)

        def node_action(fn):
            return lambda uid: (n := self.browser.items.get(uid)) and fn(n)

        add("open", node_action(self.open_node))
        add("download", node_action(lambda n: self.download(n, downloads_dir())))
        add("download-to", node_action(self.download_to))
        add("rename", node_action(self.rename))
        add("move", node_action(lambda n: self.move_copy(n, "move")))
        add("copy", node_action(lambda n: self.move_copy(n, "copy")))
        add("share", node_action(lambda n: ShareDialog(self, n).present(self)))
        add("info", node_action(lambda n: InfoDialog(self, n).present(self)))
        add("trash", node_action(self.trash))
        add("restore", node_action(self.restore))
        add("delete", node_action(self.delete))
        add("leave", node_action(self.leave_share))
        add("album-rename", self.album_rename)
        add("album-delete", self.album_delete)
        add("upload-files", self.pick_upload_files, False)
        add("upload-folders", self.pick_upload_folders, False)
        add("new-folder", self.new_folder, False)
        add("new-doc", lambda: self.new_document("doc"), False)
        add("new-sheet", lambda: self.new_document("sheet"), False)
        add("open-web", lambda: Gio.AppInfo.launch_default_for_uri(WEB_URL, None), False)
        add("about", self.about, False)
        add("logout", self.logout, False)
        add("language-de", lambda: self.set_language_choice("de"), False)
        add("language-en", lambda: self.set_language_choice("en"), False)

        settings = load_settings()
        self.prefetch_action = Gio.SimpleAction.new_stateful(
            "prefetch", None, GLib.Variant.new_boolean(bool(settings.get("prefetch"))))
        self.prefetch_action.connect("activate", lambda a, _v: self.set_prefetch(not a.get_state().get_boolean()))
        self.add_action(self.prefetch_action)
        self.offline_action = Gio.SimpleAction.new_stateful(
            "photos-offline", None, GLib.Variant.new_boolean(self.photos_offline()))
        self.offline_action.connect("activate", lambda a, _v: self.set_photos_offline(not a.get_state().get_boolean()))
        self.add_action(self.offline_action)
        add("clear-thumbs", self.clear_thumbs, False)

        sort = Gio.SimpleAction.new_stateful("sort", GLib.VariantType.new("s"), GLib.Variant.new_string("name"))
        sort.connect("activate", lambda a, v: (a.set_state(v), self.browser.set_sort(v.get_string())))
        self.add_action(sort)

        app = self.get_application()
        app.set_accels_for_action("win.new-folder", ["<Ctrl><Shift>n"])
        app.set_accels_for_action("win.upload-files", ["<Ctrl>u"])

    # ---- photo options
    def update_photo_activity(self):
        pf = self.prefetcher
        self.photo_spinner.set_visible(pf.running and not pf.paused)

    def set_language_choice(self, language):
        if language != get_language():
            save_settings(language=language)
            self.toast(_("Bitte Nuclivo neu starten, um die Sprache zu wechseln."))

    def set_prefetch(self, on):
        save_settings(prefetch=on)
        self.prefetch_action.set_state(GLib.Variant.new_boolean(on))
        if on:
            self.prefetcher.finished = False
            if self.prefetcher.photos:
                self.prefetcher.start()
            else:
                self.photos.ensure_loaded(show=False)
            self.toast(_("Vorschauen werden im Hintergrund geladen"))
        else:
            self.prefetcher.stop()

    @staticmethod
    def photos_offline():
        ph = load_json_file(SYNC_CONFIG, {}).get("photos", {})
        return bool(ph.get("enabled") and ph.get("download", True) and ph.get("download_existing"))

    def set_photos_offline(self, on):
        cfg = load_json_file(SYNC_CONFIG, {})
        ph = cfg.setdefault("photos", {})
        if on:
            ph.update(enabled=True, download=True, download_existing=True)
            ph.setdefault("local", PHOTOS_LOCAL_DEFAULT)
        else:
            ph["download_existing"] = False
            ph["enabled"] = False
        save_json_file(SYNC_CONFIG, cfg)
        if on:
            subprocess.run(["systemctl", "--user", "enable", "--now", SYNC_UNIT], capture_output=True)
        subprocess.run(["systemctl", "--user", "reload", SYNC_UNIT], capture_output=True)
        self.offline_action.set_state(GLib.Variant.new_boolean(on))
        self.toast(_("Fotos werden offline gespeichert (gedrosselt)") if on else
                   _("Foto-Sync aus – bereits gespeicherte Fotos bleiben erhalten"))
        self.sync.refresh()

    def clear_thumbs(self):
        def go():
            self.prefetcher.stop()
            shutil.rmtree(THUMB_DIR, ignore_errors=True)
            private_dir(THUMB_DIR)
            for p in self.photos.photos:
                p.texture, p.failed = None, False
            self.photos._show_month()
            self.toast(_("Vorschau-Cache geleert"))
        confirm_dialog(self, _("Vorschau-Cache leeren?"),
                       _("Alle gespeicherten Vorschaubilder werden gelöscht und bei Bedarf neu geladen."),
                       _("Leeren"), go)

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(msg), timeout=3))

    def toast_action(self, msg, label, cb):
        t = Adw.Toast(title=GLib.markup_escape_text(msg), button_label=label, timeout=8)
        t.connect("button-clicked", lambda *_: cb())
        self.toasts.add_toast(t)

    def open_node(self, node):
        if node.is_folder:
            self.browser.folder_uids[node.path] = node.uid
            self.browser.navigate(node.path, node.name)
            return
        if node.media_type in PROTON_DOC_TYPES:
            self.open_docs(docs_url(node), node.name)
            return
        target = os.path.join(CACHE, "open", hashlib.sha1(node.uid.encode()).hexdigest()[:16])
        try:
            local = os.path.join(target, safe_name(node.name))
        except ValueError:
            self.toast(_("Unsicherer Dateiname: Öffnen abgebrochen"))
            return
        private_dir(target)
        if os.path.islink(local):
            self.toast(_("Symbolischer Link: Öffnen abgebrochen"))
            return
        if os.path.exists(local) and node.size and os.path.getsize(local) == node.size:
            open_local(local)
            return
        private_dir(target)
        self.toast(_("„{name}“ wird geöffnet …").format(name=node.name))

        def done(ok, _d, err):
            if ok and os.path.exists(local):
                open_local(local)
            elif ok:
                files = [n for n in os.listdir(target) if os.path.isfile(os.path.join(target, n)) and not os.path.islink(os.path.join(target, n))]
                if files:
                    open_local(os.path.join(target, files[0]))
        self.start_transfer(node.name, "document-open-symbolic",
                            ["filesystem", "download", "-f", "remove", "-d", "merge", node.path, target], done)

    def open_docs(self, url, title):
        if self.docs is None:
            Gio.AppInfo.launch_default_for_uri(url, None)
            return
        self.docs.open(url, title)
        self.content.set_visible_child_name("docs")
        self.split.set_show_content(True)

    def new_document(self, kind):
        path = self.browser.path

        def go(uid):
            if uid:
                self.open_docs(docs_create_url(kind, uid), _("Neues Dokument") if kind == "doc" else _("Neue Tabelle"))
            else:
                self.toast(_("Ordner konnte nicht ermittelt werden"))

        uid = self.browser.folder_uid()
        if uid:
            go(uid)
        else:
            cli(["filesystem", "info", path], lambda ok, d, e: go(d.get("uid") if ok and isinstance(d, dict) else None))

    def open_photo(self, photo):
        target = os.path.join(CACHE, "open", hashlib.sha1(photo.uid.encode()).hexdigest()[:16])
        private_dir(target)
        files = [n for n in os.listdir(target) if os.path.isfile(os.path.join(target, n)) and not os.path.islink(os.path.join(target, n))]
        if files:
            open_local(os.path.join(target, files[0]))
            return
        private_dir(target)
        self.toast(_("Foto wird geladen …"))

        def done(ok, _d, err):
            files = [n for n in os.listdir(target) if os.path.isfile(os.path.join(target, n)) and not os.path.islink(os.path.join(target, n))]
            if files:
                open_local(os.path.join(target, files[0]))
            elif ok:
                self.toast(_("Foto konnte nicht geladen werden"))
        self.start_transfer(photo.name or "Foto", "image-x-generic-symbolic",
                            ["photo", "download", "-c", "replace", f"{photo.source}/{photo.uid}", target], done)

    def download(self, node, folder):
        self.start_transfer(node.name, "folder-download-symbolic",
                            ["filesystem", "download", "-f", "rename", "-d", "merge", node.path, folder],
                            lambda ok, d, e: self.toast(_("„{name}“ heruntergeladen").format(name=node.name) if ok
                                                       else _("Download fehlgeschlagen: {error}").format(error=e)),
                            local=folder)

    def download_to(self, node):
        d = Gtk.FileDialog(title=_("Zielordner wählen"), initial_folder=Gio.File.new_for_path(downloads_dir()))

        def cb(dlg, res):
            try:
                f = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            self.download(node, f.get_path())
        d.select_folder(self, None, cb)

    def upload(self, paths, parent):
        names = ", ".join(os.path.basename(p) for p in paths[:2]) + (" …" if len(paths) > 2 else "")
        self.start_transfer(names, "document-send-symbolic",
                            ["filesystem", "upload", "-f", "create-new-revision", "-d", "merge",
                             *[esc_local(p) for p in paths], parent],
                            lambda ok, d, e: (self.toast(_("Hochgeladen") if ok else _("Upload fehlgeschlagen: {error}").format(error=e)),
                                              self.browser.invalidate(parent)))

    def pick_upload_files(self):
        d = Gtk.FileDialog(title=_("Dateien hochladen"))
        parent = self.browser.path

        def cb(dlg, res):
            try:
                files = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            self.upload([f.get_path() for f in files if f.get_path()], parent)
        d.open_multiple(self, None, cb)

    def pick_upload_folders(self):
        d = Gtk.FileDialog(title=_("Ordner hochladen"))
        parent = self.browser.path

        def cb(dlg, res):
            try:
                files = dlg.select_multiple_folders_finish(res)
            except GLib.Error:
                return
            self.upload([f.get_path() for f in files if f.get_path()], parent)
        d.select_multiple_folders(self, None, cb)

    def upload_photos(self):
        d = Gtk.FileDialog(title=_("Fotos hochladen"))
        flt = Gtk.FileFilter(name=_("Bilder und Videos"))
        flt.add_mime_type("image/*")
        flt.add_mime_type("video/*")
        store = Gio.ListStore(item_type=Gtk.FileFilter)
        store.append(flt)
        d.set_filters(store)

        def cb(dlg, res):
            try:
                files = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            paths = [f.get_path() for f in files if f.get_path()]
            self.start_transfer(_("{count} Foto(s)").format(count=len(paths)), "image-x-generic-symbolic",
                                ["photo", "upload", "-c", "rename", *[esc_local(p) for p in paths]],
                                lambda ok, dd, e: (self.toast(_("Fotos hochgeladen") if ok else _("Fehler: {error}").format(error=e)),
                                                   self.photos.load()))
        d.open_multiple(self, None, cb)

    def new_folder(self):
        parent = self.browser.path

        def make(name):
            cli(["filesystem", "create-folder", parent, name],
                lambda ok, d, e: (self.toast(_("Ordner „{name}“ erstellt").format(name=name) if ok else _("Fehler: {error}").format(error=e)),
                                  self.browser.invalidate(parent)))
        entry_dialog(self, _("Neuer Ordner"), "", "", _("Erstellen"), make)

    def rename(self, node):
        parent = self.browser.path

        def go(name):
            if name == node.name:
                return
            cli(["filesystem", "rename", node.path, name],
                lambda ok, d, e: (self.toast(_("Umbenannt") if ok else _("Fehler: {error}").format(error=e)), self.browser.invalidate(parent)))
        entry_dialog(self, _("Umbenennen"), "", node.name, _("Umbenennen"), go)

    def move_copy(self, node, mode):
        parent = self.browser.path
        verb = _("Verschieben") if mode == "move" else _("Kopieren")

        def go(target):
            if mode == "move" and target == parent:
                return
            cli(["filesystem", mode, node.path, target],
                lambda ok, d, e: (self.toast(_("„{name}“ {action}").format(name=node.name, action=_("verschoben") if mode == "move" else _("kopiert"))
                                             if ok else _("Fehler: {error}").format(error=e)),
                                  self.browser.cache.pop(target, None), self.browser.invalidate(parent)))
        FolderPicker(self, _("{action} nach").format(action=verb), _("Hierher {action}").format(action=verb.lower()), go,
                     exclude_uid=node.uid).present(self)

    def trash(self, node):
        parent = self.browser.path

        def undo(*_):
            cli(["filesystem", "restore", f"/trash/{node.uid}"],
                lambda ok, d, e: (self.toast(_("Wiederhergestellt") if ok else _("Fehler: {error}").format(error=e)),
                                  self.browser.invalidate(parent)))

        def done(ok, _d, err):
            if ok:
                t = Adw.Toast(title=GLib.markup_escape_text(_("„{name}“ im Papierkorb").format(name=node.name)),
                              button_label=_("Rückgängig"), timeout=6)
                t.connect("button-clicked", undo)
                self.toasts.add_toast(t)
                self.browser.cache.pop("/trash", None)
            else:
                self.toast(_("Fehler: {error}").format(error=err))
            self.browser.invalidate(parent)
        cli(["filesystem", "trash", node.path], done)

    def restore(self, node):
        cli(["filesystem", "restore", node.path],
            lambda ok, d, e: (self.toast(_("Wiederhergestellt") if ok else _("Fehler: {error}").format(error=e)),
                              self.browser.cache.clear(), self.browser.invalidate("/trash")))

    def delete(self, node):
        confirm_dialog(self, _("Endgültig löschen?"), _("„{name}“ lässt sich danach nicht wiederherstellen.").format(name=node.name),
                       _("Löschen"), lambda: cli(["filesystem", "delete", node.path],
                                              lambda ok, d, e: (self.toast(_("Gelöscht") if ok else _("Fehler: {error}").format(error=e)),
                                                                self.browser.invalidate("/trash"))))

    def leave_share(self, node):
        confirm_dialog(self, _("Freigabe verlassen?"), _("Du verlierst den Zugriff auf „{name}“.").format(name=node.name), _("Verlassen"),
                       lambda: cli(["sharing", "leave", node.path],
                                   lambda ok, d, e: (self.toast(_("Freigabe verlassen") if ok else _("Fehler: {error}").format(error=e)),
                                                     self.browser.invalidate())))

    def album_rename(self, path):
        old = path.rsplit("/", 1)[-1]
        entry_dialog(self, _("Album umbenennen"), "", old, _("Umbenennen"),
                     lambda name: cli(["album", "update", "-n", name, path],
                                      lambda ok, d, e: (self.toast(_("Umbenannt") if ok else _("Fehler: {error}").format(error=e)),
                                                        self.albums.load())))

    def album_delete(self, path):
        confirm_dialog(self, _("Album löschen?"), _("Die Fotos bleiben in deiner Foto-Mediathek erhalten."), _("Löschen"),
                       lambda: cli(["album", "delete", "-s", path],
                                   lambda ok, d, e: (self.toast(_("Album gelöscht") if ok else _("Fehler: {error}").format(error=e)),
                                                     self.albums.load())))

    def login(self):
        self.toast(_("Anmeldung im Browser wird geöffnet …"))
        cli(["auth", "login"], lambda ok, d, e: (self.toast(_("Angemeldet") if ok else _("Anmeldung fehlgeschlagen: {error}").format(error=e)),
                                                 self.browser.reload()), json_out=False)

    def logout(self):
        confirm_dialog(self, _("Abmelden?"), _("Die Proton Drive CLI wird auf diesem Gerät abgemeldet. "
                       "Der Sync-Dienst funktioniert davon unabhängig weiter."), _("Abmelden"),
                       lambda: cli(["auth", "logout"], lambda ok, d, e: (self.toast(_("Abgemeldet")),
                                                                         self.browser.reload()), json_out=False))

    def about(self):
        about = Adw.AboutDialog(application_name="Nuclivo", application_icon="folder-remote",
                                version="0.1", developer_name=_("Gebaut mit Claude"),
                                comments=_("Oberfläche für die offizielle Proton Drive CLI. "
                                           "Kein offizielles Proton-Produkt."),
                                license_type=Gtk.License.GPL_3_0_ONLY)
        about.present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("activate", self._activate)

    def _activate(self, *_):
        win = self.get_active_window()
        if not win:
            provider = Gtk.CssProvider()
            provider.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            private_dir(CACHE)
            win = Window(self)
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
