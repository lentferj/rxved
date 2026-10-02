# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
# The read-modify-write config store is ported from the sibling s3ked
# project's s3k/bridge.py, which ports it from eosed (eos/bridge.py), which
# ports it from k2kremote (k2kremote/midi_bridge.py):
#   Copyright (C) 2026  k2kremote contributors       - GPL-2.0-or-later
#   Copyright (C) 2026  eosed contributors           - GPL-2.0-or-later
#   Copyright (C) 2026  s3ked contributors           - GPL-2.0-or-later
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

"""Local ``config.toml``: which ports answered last, and on which channel.

Split out of :mod:`xv.bridge` so transport and settings stop sharing a
file. Read-modify-write, never a blind overwrite, so unrelated keys survive
each other's saves.
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "load_last_ports",
    "save_last_ports",
    "load_device_id",
    "save_device_id",
    "load_channel",
    "save_channel",
]

#: Local settings, gitignored. Alongside the sibling projects' convention.
DEFAULT_CONFIG_PATH = "config.toml"


#: Set by the first save that had to leave an unreadable config alone.
_warned_unreadable = False


# --- config.toml ------------------------------------------------------------
# Read-modify-write, never a blind overwrite, so unrelated keys survive each
# other's saves. Ported from s3ked, including the reason it is careful:
# collapsing "missing" and "unparseable" turns the next save into a blind
# overwrite of a file this code never understood, so one stray bracket costs
# the user every other setting in it, silently.


def _read_config(path: str) -> Tuple[dict, str]:
    """``(settings, status)`` where status is ok / missing / unreadable."""
    import os
    import tomllib

    if not os.path.exists(path):
        return {}, "missing"
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return {}, "unreadable"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # written by a build running under a non-UTF-8 locale; decode
        # leniently so hand-edited keys survive, and let the next save repair
        text = raw.decode("cp1252", errors="replace")
    try:
        return tomllib.loads(text), "ok"
    except ValueError:
        return {}, "unreadable"


def _read_config_dict(path: str) -> dict:
    return _read_config(path)[0]


_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _toml_string(value: str) -> str:
    """One TOML basic string, escaped.

    Writing a value straight between quotes is fine until it contains one.
    A MIDI port name is an arbitrary string -- ALSA client names are
    whatever the device reports -- so a quote in one produces a file that
    is not TOML. ``_update`` then refuses to overwrite a file it cannot
    parse, which is the right refusal and also means the cache never heals:
    every later run reads nothing and writes nothing until somebody deletes
    it by hand.

    TOML basic strings interpret the usual backslash escapes as well, so a
    literal tab or newline is written as an escape rather than embedded.
    """
    out = ['"']
    for char in value:
        if char in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append("\\u%04X" % ord(char))
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _update_config(path: str, **changes) -> None:
    global _warned_unreadable

    data, status = _read_config(path)
    if status == "unreadable":
        if not _warned_unreadable:
            _warned_unreadable = True
            print(
                f"rxved: {path} could not be parsed, so settings are not "
                f"being saved. Fix or delete it; nothing has been "
                f"overwritten.",
                file=sys.stderr,
            )
        return
    data.update(changes)
    _write_config_dict(data, path)


def _write_config_dict(data: dict, path: str) -> None:
    lines = ["# rxved local config - gitignored, safe to delete."]
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            lines.append(f"{key} = {_toml_string(value)}")
        else:
            lines.append(f"{key} = {value}")
    try:
        # encoding= is not optional: without it Python uses the locale codec,
        # and TOML is UTF-8 by spec. Both ends must say so.
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass  # the cache is a convenience, not required for correctness


def load_last_ports(path: str = DEFAULT_CONFIG_PATH) -> Optional[Tuple[str, str]]:
    """The send/receive pair that answered last time, if any."""
    data = _read_config_dict(path)
    send_port = data.get("send_port")
    recv_port = data.get("recv_port")
    if isinstance(send_port, str) and isinstance(recv_port, str):
        return send_port, recv_port
    return None


def save_last_ports(
    send_port: str, recv_port: str, path: str = DEFAULT_CONFIG_PATH
) -> None:
    _update_config(path, send_port=send_port, recv_port=recv_port)


def load_device_id(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    """The remembered device ID, as the **panel** numbers it: 17-32.

    Two guards, both about this project's oldest trap. `isinstance(True,
    int)` is True, so a hand-edited ``device_id = true`` would come back
    as 1 -- and 1 is not even a panel number, which is the point: the
    panel range is 17-32 and the wire range is 0x10-0x1F, they overlap,
    and a value from the wrong one is answered with silence. Anything
    outside 17-32 is refused here rather than handed to
    :func:`device_id_byte`, which takes panel numbers only.
    """
    value = _read_config_dict(path).get("device_id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 17 <= value <= 32 else None


def save_device_id(device_id: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    _update_config(path, device_id=int(device_id))


def load_channel(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    """The channel last sent on, zero-based, or None.

    Two guards where there were none. `isinstance(True, int)` is True, so
    a hand-edited ``channel = true`` came back as ``True`` and `0xC0 |
    True` is `0xC1` -- MIDI channel 2 when channel 1 was asked for. And
    there was no range check at all, so ``channel = 99`` came back as 99
    and produced a status byte belonging to another message entirely.

    A wrong channel is not an error anywhere: the instrument plays
    nothing, or something else does.
    """
    value = _read_config_dict(path).get("channel")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 15 else None


def save_channel(channel: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    _update_config(path, channel=int(channel))
