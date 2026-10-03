#!/usr/bin/env python3
"""Nuclivo sync daemon.

* Folder pairs: two-way sync with `rclone bisync`, triggered by local inotify
  events (debounced) and by a periodic poll that picks up remote changes.
* Photos (optional): new photos from the Proton Photos timeline are downloaded
  into a local folder; new local files in that folder can be uploaded.
  Deletions are never propagated for photos.

Config: ~/.config/nuclivo/sync.json. SIGUSR1 triggers a full sync, SIGHUP reloads the config.
"""
import json
import logging
import os
os.umask(0o077)
import tempfile
from nuclivo_security import save_json, safe_name, photo_target, remote_path
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import shutil

import pyinotify
from nuclivo_i18n import set_language, system_language, tr as _

HOME = os.path.expanduser("~")
CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME", f"{HOME}/.config"), "nuclivo", "sync.json")
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", f"{HOME}/.local/state"), "nuclivo")
STATUS = os.path.join(STATE_DIR, "sync-status.json")
PHOTO_STATE = os.path.join(STATE_DIR, "photo-state.json")
RCLONE = os.path.join(HOME, ".local/bin/rclone")
CLI = os.path.join(HOME, ".local/bin/proton-drive")
REMOTE = "nuclivo-proton"
DETAILS = os.path.join(os.environ.get("XDG_CACHE_HOME", f"{HOME}/.cache"), "nuclivo", "photo-details.json")
TMP_NAME = ".nuclivo-tmp"
THROTTLE = 1.0  # pause factor after each photo batch (1.0 = ~50 % of the line on average)
DEBOUNCE = 8
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff", ".dng",
             ".raw", ".mp4", ".mov", ".m4v", ".3gp", ".webm", ".avif"}

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
log = logging.getLogger("nuclivo-sync")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Syncer:
    def __init__(self):
        self.lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.wake = threading.Event()
        self.dirty = {}          # key -> time of last local change
        self.busy = set()
        self.force_all = True
        self.reload()

    def reload(self):
        settings = load_json(os.path.join(os.path.dirname(CONFIG), "settings.json"), {})
        set_language(settings.get("language") or system_language())
        self.cfg = load_json(CONFIG, {})
        self.pairs = [p for p in self.cfg.get("pairs", []) if p.get("local") and p.get("remote")]
        self.photos = self.cfg.get("photos") or {}
        self.poll = max(1, int(self.cfg.get("poll_minutes", 5))) * 60
        log.info(_("Konfiguration geladen: %d Ordnerpaar(e), Fotos %s"),
                 len(self.pairs), _("an") if self.photos.get("enabled") else _("aus"))

    def set_status(self, key, **kw):
        with self.status_lock:
            st = load_json(STATUS, {})
            st.setdefault(key, {}).update(kw)
            save_json(STATUS, st)

    # ------------------------------------------------------------ folder pairs
    def sync_pair(self, pair):
        local = os.path.expanduser(pair["local"])
        os.makedirs(local, exist_ok=True)
        st = load_json(STATUS, {}).get(pair["local"], {})
        if st.get("initialized") and st.get("remote") != pair["remote"]:
            raise ValueError("Sync-Ziel geändert oder alter Zustand: manueller Abgleich erforderlich")
        args = [RCLONE, "bisync", local, remote_path(pair["remote"]),
                "--resilient", "--recover", "--max-lock", "5m",
                "--conflict-resolve", "newer", "--conflict-loser", "num",
                "--create-empty-src-dirs", "--compare", "size,modtime",
                "--max-delete", str(pair.get("max_delete_percent", 30)),
                "--exclude", ".~lock.*", "--exclude", "*.part", "--exclude", ".goutputstream-*",
                "--stats-one-line", "-v"]
        first = not st.get("initialized")
        if first:
            subprocess.run([RCLONE, "mkdir", remote_path(pair["remote"])], capture_output=True)
            args.append("--resync")
            log.info(_("Erster Abgleich (resync): %s <-> %s"), local, pair["remote"])
        self.set_status(pair["local"], running=True)
        t0 = time.time()
        p = subprocess.run(args, capture_output=True, text=True)
        tail = [l for l in (p.stdout + p.stderr).splitlines() if l.strip()][-6:]
        if p.returncode == 0:
            log.info(_("✓ %s synchronisiert (%.0fs)"), pair["local"], time.time() - t0)
            self.set_status(pair["local"], running=False, last=now_iso(), error=None, initialized=True, remote=pair["remote"])
        else:
            msg = next((l for l in reversed(tail) if "ERROR" in l or "Failed" in l), tail[-1] if tail else "?")
            log.error("✗ %s: %s", pair["local"], msg)
            for l in tail:
                log.error("   %s", l)
            if "--resync" in msg or "must run --resync" in msg:
                # Do not automatically overwrite a baseline after state loss.
                log.error(_("Bisync-Zustand verloren: manuellen Wiederherstellungsabgleich durchführen"))
            self.set_status(pair["local"], running=False, error=msg[:300])

    # ------------------------------------------------------------ photos
    def photo_dir(self):
        return os.path.expanduser(self.photos.get("local") or "~/Bilder/Proton Fotos")

    def cli(self, *args):
        p = subprocess.run([CLI, *args, "--json"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        try:
            data = json.loads(p.stdout) if p.stdout.strip() else None
        except ValueError:
            data = None
        return p.returncode == 0, data, (p.stderr.strip() or p.stdout.strip())[-300:]

    def photo_details(self):
        """uid -> {"n": name, "m": mediaType, "s": size}; shared cache with the app."""
        ok, data, _ = self.cli("photo", "timeline", "-d")
        if not ok or not isinstance(data, list):
            return load_json(DETAILS, {})
        det = {x["uid"]: {"n": (x.get("name") or {}).get("value"), "m": x.get("mediaType"),
                          "s": x.get("totalStorageSize")} for x in data if x.get("uid")}
        save_json(DETAILS, det)
        return det

    def sync_photos(self):
        root = self.photo_dir()
        tmp_root = None
        os.makedirs(root, exist_ok=True)
        state = load_json(PHOTO_STATE, {})
        # have: uid -> local path (None if unknown); skipped: photos that existed before the sync was enabled
        have = state.get("have")
        if have is None:  # migrate old format
            have = {u: None for u in state.get("known", [])} if state.get("initialized") else {}
            if state.get("initialized") and not self.photos.get("download_existing"):
                state["skipped"], have = list(have), {}
        skipped = set(state.get("skipped", []))
        local_seen = state.get("local", {})
        self.set_status("photos", running=True, progress=None)
        err = None

        def save():
            state.update(have=have, skipped=sorted(skipped), local=local_seen, initialized=True)
            state.pop("known", None)
            save_json(PHOTO_STATE, state)

        ok, timeline, e = self.cli("photo", "timeline")
        if not ok or not isinstance(timeline, list):
            err = f"Timeline: {e}"
        else:
            if not state.get("initialized") and not self.photos.get("download_existing"):
                skipped = {x["nodeUid"] for x in timeline}
                log.info(_("Foto-Sync gestartet: %d vorhandene Fotos werden übersprungen"), len(skipped))
            known = set(have) | (set() if self.photos.get("download_existing") else skipped)
            new = [x for x in timeline if x["nodeUid"] not in known] if self.photos.get("download", True) else []
            if new:
                log.info(_("%d Foto(s) werden heruntergeladen"), len(new))
                details = load_json(DETAILS, {})
                if any(x["nodeUid"] not in details for x in new):
                    details = self.photo_details()
                # Each batch must have unique file names so downloads can be mapped back to their uid.
                chunks, remaining = [], new
                while remaining:
                    chunk, names, rest = [], set(), []
                    for x in remaining:
                        n = (details.get(x["nodeUid"]) or {}).get("n") or x["nodeUid"]
                        if len(chunk) < 20 and n not in names:
                            chunk.append(x)
                            names.add(n)
                        else:
                            rest.append(x)
                    chunks.append(chunk)
                    remaining = rest
                done = 0
                for ci, chunk in enumerate(chunks):
                    if tmp_root:
                        shutil.rmtree(tmp_root, ignore_errors=True)
                    tmp_root = tempfile.mkdtemp(prefix=TMP_NAME + "-", dir=root)
                    t0 = time.time()
                    ok, res, e = self.cli("photo", "download", "-c", "rename",
                                          *[f"/photos/{x['nodeUid']}" for x in chunk], tmp_root)
                    elapsed = time.time() - t0
                    files = set(os.listdir(tmp_root))
                    unmatched = [x for x in chunk if (details.get(x["nodeUid"]) or {}).get("n") not in files]
                    for x in chunk:
                        uid = x["nodeUid"]
                        name = (details.get(uid) or {}).get("n")
                        if name not in files:
                            if len(unmatched) == 1 and len(files) == 1:
                                name = next(iter(files))
                            else:
                                continue
                        files.discard(name)
                        safe_name(name)
                        source = os.path.join(tmp_root, name)
                        if os.path.islink(source) or not os.path.isfile(source):
                            raise ValueError("Downloaded photo is not a regular file")
                        target = photo_target(root, x.get("captureTime"))
                        dest = os.path.join(target, name)
                        stem, ext = os.path.splitext(name)
                        n = 1
                        while os.path.lexists(dest):
                            dest = os.path.join(target, f"{stem} ({n}){ext}")
                            n += 1
                        shutil.move(os.path.join(tmp_root, name), dest)
                        have[uid] = dest
                        # Downloaded files must not be uploaded again.
                        local_seen[dest] = [os.path.getsize(dest), int(os.path.getmtime(dest))]
                    if not ok or (res or {}).get("failedItems"):
                        err = f"Download: {e or (res or {}).get('failures')}"
                        log.error(_("Foto-Download fehlgeschlagen: %s"), err)
                    done += len(chunk)
                    self.set_status("photos", progress=[done, len(new)])
                    save()
                    if ci + 1 < len(chunks):
                        # Throttle: pause as long as the batch took, so on average ~50 % of the line stays free.
                        time.sleep(elapsed * THROTTLE)
                shutil.rmtree(tmp_root, ignore_errors=True)
                self.set_status("photos", progress=None)
            save()

        if self.photos.get("upload"):
            pending = []
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(TMP_NAME) and not os.path.islink(os.path.join(dirpath, d))]
                for name in files:
                    if name.startswith(".") or os.path.splitext(name)[1].lower() not in PHOTO_EXT:
                        continue
                    fp = os.path.join(dirpath, name)
                    if os.path.islink(fp):
                        continue
                    try:
                        sig = [os.path.getsize(fp), int(os.path.getmtime(fp))]
                    except OSError:
                        continue
                    if local_seen.get(fp) != sig and time.time() - sig[1] > 5:
                        pending.append((fp, sig))
            if pending:
                log.info(_("%d lokale Foto(s) werden hochgeladen"), len(pending))
            for i in range(0, len(pending), 20):
                chunk = pending[i:i + 20]
                escaped = [fp.translate(str.maketrans({c: "\\" + c for c in "[]{}*?"})) for fp, signature in chunk]
                ok, res, e = self.cli("photo", "upload", "-c", "skip", *escaped)
                if ok and not (res or {}).get("failedItems"):
                    for fp, sig in chunk:
                        local_seen[fp] = sig
                else:
                    err = f"Upload: {e or (res or {}).get('failures')}"
                    log.error(_("Foto-Upload fehlgeschlagen: %s"), err)
                save()
            if pending and not err:
                # Uploaded photos show up in the timeline; record them so they are not downloaded again.
                ok, timeline, timeline_error = self.cli("photo", "timeline")
                if ok and isinstance(timeline, list):
                    known = set(have) | skipped
                    recent = [x["nodeUid"] for x in timeline if x["nodeUid"] not in known]
                    if len(recent) <= len(pending):
                        for uid in recent:
                            have[uid] = None
                        save()

        save()
        self.set_status("photos", running=False, last=now_iso(), error=err)
        if not err:
            log.info(_("✓ Fotos synchronisiert"))

    # ------------------------------------------------------------ scheduling
    def mark_dirty(self, key):
        if key in self.busy:
            return  # our own writes during a sync
        self.dirty[key] = time.time()
        self.wake.set()

    def run(self):
        last_poll = 0
        while True:
            self.wake.wait(timeout=5)
            self.wake.clear()
            now = time.time()
            due = set()
            if self.force_all or now - last_poll >= self.poll:
                due = {p["local"] for p in self.pairs}
                if self.photos.get("enabled"):
                    due.add("photos")
                last_poll = now
                self.force_all = False
            for key, t in list(self.dirty.items()):
                if now - t >= DEBOUNCE:
                    due.add(key)
                    del self.dirty[key]
            for key in due:
                self.busy.add(key)
                try:
                    if key == "photos":
                        if self.photos.get("enabled"):
                            self.sync_photos()
                    else:
                        pair = next((p for p in self.pairs if p["local"] == key), None)
                        if pair:
                            self.sync_pair(pair)
                except Exception as e:  # noqa: BLE001
                    log.exception(_("Fehler bei %s"), key)
                    self.set_status(key, running=False, error=str(e)[:300])
                finally:
                    # inotify events from our own writes arrive slightly late
                    threading.Timer(3, self.busy.discard, args=(key,)).start()


class Handler(pyinotify.ProcessEvent):
    def my_init(self, syncer, roots):
        self.syncer = syncer
        self.roots = roots

    def process_default(self, event):
        path = event.pathname
        if (os.path.basename(path).startswith((".~lock", ".goutputstream")) or path.endswith(".part")
                or f"/{TMP_NAME}" in path):
            return
        for key, root in self.roots:
            if path == root or path.startswith(root + os.sep):
                self.syncer.mark_dirty(key)
                return


def main():
    # A previous run may have been killed mid-sync; clear stale "running" flags.
    st = load_json(STATUS, {})
    for v in st.values():
        v.update(running=False, progress=None)
    save_json(STATUS, st)
    syncer = Syncer()
    wm = pyinotify.WatchManager()
    mask = (pyinotify.IN_CLOSE_WRITE | pyinotify.IN_CREATE | pyinotify.IN_DELETE |
            pyinotify.IN_MOVED_FROM | pyinotify.IN_MOVED_TO | pyinotify.IN_ATTRIB)
    watches = {}

    def setup_watches():
        for wd in list(watches.values()):
            wm.rm_watch(list(wd.values()), quiet=True)
        watches.clear()
        roots = [(p["local"], os.path.expanduser(p["local"])) for p in syncer.pairs]
        if syncer.photos.get("enabled") and syncer.photos.get("upload"):
            roots.append(("photos", syncer.photo_dir()))
        for key, root in roots:
            os.makedirs(root, exist_ok=True)
            watches[key] = wm.add_watch(root, mask, rec=True, auto_add=True, quiet=True)
        handler.roots = roots
        log.info(_("Überwache %d Ordner auf Änderungen"), len(roots))

    handler = Handler(syncer=syncer, roots=[])
    notifier = pyinotify.ThreadedNotifier(wm, handler)
    notifier.daemon = True
    notifier.start()
    setup_watches()

    def on_usr1(*_):
        log.info(_("Manueller Sync angefordert"))
        syncer.force_all = True
        syncer.wake.set()

    def on_hup(*_):
        syncer.reload()
        setup_watches()
        syncer.force_all = True
        syncer.wake.set()

    signal.signal(signal.SIGUSR1, on_usr1)
    signal.signal(signal.SIGHUP, on_hup)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    syncer.run()


if __name__ == "__main__":
    main()
