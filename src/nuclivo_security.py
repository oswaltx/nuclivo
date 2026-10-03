# SPDX-License-Identifier: GPL-3.0-only
"""Filesystem and URL boundaries shared by the GUI and sync daemon."""
import datetime
import json
import os
import re
import tempfile
import urllib.parse


def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.path.islink(path):
        raise ValueError("Private directory must not be a symbolic link")
    os.chmod(path, 0o700)
    return path


def save_json(path, data):
    parent = private_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix=".nuclivo-", dir=parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def safe_name(name):
    if (not isinstance(name, str) or not name or name in (".", "..")
            or "/" in name or "\\" in name or "\x00" in name):
        raise ValueError("Unsafe filename from remote service")
    return name


def photo_target(root, capture):
    month = (capture or "")[:7]
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        try:
            datetime.date.fromisoformat(month + "-01")
        except ValueError:
            month = ""
    else:
        month = ""
    parts = month.split("-") if month else ["Unbekannt"]
    target = root
    for part in parts:
        target = os.path.join(target, part)
        if os.path.islink(target):
            raise ValueError("Photo destination must not be a symbolic link")
        os.makedirs(target, exist_ok=True)
    return target


def is_proton(uri):
    try:
        parsed = urllib.parse.urlsplit(uri or "")
        return (parsed.scheme == "https" and parsed.port in (None, 443)
                and not parsed.username and not parsed.password
                and parsed.hostname in {"proton.me", "account.proton.me", "drive.proton.me", "docs.proton.me"})
    except ValueError:
        return False


def remote_path(remote):
    if not isinstance(remote, str) or not (remote == "/my-files" or remote.startswith("/my-files/")):
        raise ValueError("Only My files folders can be synchronized")
    rel = remote[len("/my-files"):].strip("/")
    # CLI escaped names have different semantics from rclone paths.
    if "\\" in rel or any(p in (".", "..", "") for p in rel.split("/")) and rel:
        raise ValueError("Unsupported remote folder path")
    return "nuclivo-proton:" + rel
