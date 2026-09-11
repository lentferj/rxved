# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
#
# rxved is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.
#
# rxved is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""Performance backups: the undo for the only destructive thing rxved does.

Writing a performance into a user slot replaces what was there, and no power
cycle brings it back. So the slot is read first and written here, and
:func:`load` puts it back.

The format is JSON with the blocks hex-encoded, rather than a ``.syx`` file,
for one reason: a backup that cannot be identified is not much of a backup.
JSON carries the slot number, the performance's own name, the date, and the
device ID it came from, so a directory of these can be read by a person
deciding which one to restore. A ``.syx`` dump is a wall of bytes that says
none of that.

**Nothing here talks to MIDI.** It serialises and deserialises; the bridge
does the round trips. That keeps backup files testable without hardware and
keeps the rule that workers do MIDI and nothing else does.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Dict, List, Optional

__all__ = [
    "FORMAT",
    "BackupError",
    "encode",
    "decode",
    "save",
    "load",
    "list_backups",
    "default_dir",
    "performance_name",
]

#: Bumped only if the on-disk shape changes incompatibly. Read back and
#: checked, so a future rxved refuses a file it would misread rather than
#: restoring nonsense into a user slot.
FORMAT = 1


class BackupError(RuntimeError):
    """A backup file is missing, malformed, or not what was asked for."""


def default_dir(data_dir: str) -> str:
    """Where backups live, under the same per-platform data directory the
    favourites database uses -- **not** the checkout, which `git clean`
    empties."""
    return os.path.join(data_dir, "backups")


def performance_name(blocks: Dict[str, bytes]) -> str:
    """The performance's own name, from Common offsets ``00 00``-``00 0B``.

    Best-effort: a backup of a slot whose name bytes are junk is still a
    perfectly good backup, so this never raises.
    """
    common = blocks.get("common", b"")
    if len(common) < 12:
        return ""
    return "".join(
        chr(byte) if 32 <= byte <= 126 else " " for byte in common[:12]
    ).strip()


def encode(blocks: Dict[str, bytes], *, slot: Optional[int],
           device_id: Optional[int] = None,
           source: str = "") -> dict:
    """Blocks -> the JSON-able dict that gets written."""
    return {
        "format": FORMAT,
        "kind": "performance",
        "slot": slot,
        "name": performance_name(blocks),
        "device_id": device_id,
        "source": source,
        "saved": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "blocks": {name: data.hex() for name, data in sorted(blocks.items())},
    }


def decode(payload: dict) -> Dict[str, bytes]:
    """The written dict -> blocks, refusing anything it would misread."""
    if not isinstance(payload, dict):
        raise BackupError("backup is not a JSON object")
    if payload.get("kind") != "performance":
        raise BackupError(
            f"backup holds {payload.get('kind')!r}, not a performance")
    version = payload.get("format")
    if version != FORMAT:
        raise BackupError(
            f"backup is format {version!r}, and this rxved reads only "
            f"{FORMAT}. Refusing to guess at the difference rather than "
            f"restoring something wrong into a user slot.")
    raw = payload.get("blocks")
    if not isinstance(raw, dict) or not raw:
        raise BackupError("backup carries no blocks")
    out: Dict[str, bytes] = {}
    for name, hexed in raw.items():
        try:
            out[name] = bytes.fromhex(hexed)
        except (TypeError, ValueError) as exc:
            raise BackupError(f"block {name} is not valid hex: {exc}") from exc
    return out


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _stamp(name: str, slot: Optional[int]) -> str:
    when = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    where = f"slot{slot:02d}" if slot else "temp"
    tidy = _UNSAFE.sub("_", name).strip("_")
    return f"{where}-{when}" + (f"-{tidy}" if tidy else "") + ".json"


def save(blocks: Dict[str, bytes], directory: str, *,
         slot: Optional[int], device_id: Optional[int] = None,
         source: str = "") -> str:
    """Write a backup and return its path. Never overwrites: the filename
    carries a timestamp, and a collision within one second gets a suffix."""
    os.makedirs(directory, exist_ok=True)
    payload = encode(blocks, slot=slot, device_id=device_id, source=source)
    path = os.path.join(directory, _stamp(payload["name"], slot))
    counter = 1
    while os.path.exists(path):
        root, ext = os.path.splitext(path)
        path = f"{root}-{counter}{ext}"
        counter += 1
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
        handle.write("\n")
    return path


def load(path: str) -> Dict[str, bytes]:
    """Read a backup file back into blocks."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise BackupError(f"no such backup: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BackupError(f"{path} is not valid JSON: {exc}") from exc
    return decode(payload)


def describe(path: str) -> dict:
    """Header fields of a backup, for listing. Blocks are not decoded."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"cannot read {path}: {exc}") from exc
    return {
        "path": path,
        "slot": payload.get("slot"),
        "name": payload.get("name", ""),
        "saved": payload.get("saved", ""),
        "format": payload.get("format"),
        "blocks": len(payload.get("blocks", {})),
    }


def list_backups(directory: str) -> List[dict]:
    """Every backup in a directory, newest first. Unreadable files are
    listed with an ``error`` rather than dropped -- a backup you cannot read
    is exactly the thing you want to be told about."""
    if not os.path.isdir(directory):
        return []
    out: List[dict] = []
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".json"):
            continue
        path = os.path.join(directory, entry)
        try:
            out.append(describe(path))
        except BackupError as exc:
            out.append({"path": path, "error": str(exc), "saved": ""})
    out.sort(key=lambda row: row.get("saved", ""), reverse=True)
    return out
