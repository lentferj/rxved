# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
#
# rxved is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# rxved is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""Names read off the instrument, kept between runs.

A USER bank is the user's own work. It is also the one bank whose printed
list is guaranteed to be wrong about it -- the catalog says what the factory
shipped, and a USER bank that has been saved into says something else
entirely. So for USER (and the two other writable banks) the hardware is the
only authority, and pressing `r` to read it is the only way to be right.

The problem was what happened next: those names lived in `Catalog._live`,
which is a plain dict in a process that exits. Every restart fell back to
the printed list, so the browser confidently showed the factory USER bank
for a machine full of the user's own patches, and pressing `r` fixed the
screen for exactly as long as the session lasted. Names that are expensive
to get and true today should not evaporate overnight.

So they are written here, in the platform's data directory beside the
favourites database, and read back at startup -- which means the browser
shows the right names *immediately*, before anything has talked to the
synth, and the hardware read that follows only confirms them.

Deliberately not in the catalog file. That file is generated from Roland's
own documentation, the printed-versus-live distinction is the whole point of
having two name sources, and a cache of one instrument's current contents
does not belong in a document describing what the manual says. Overwriting
one with the other would destroy the disagreement the `*` marker exists to
show.

Format is ``{"USER:001": "Analogue!", ...}``, flat and keyed the same way
`Catalog` keys its live layer. A missing file is normal and means nothing
has been read yet; a corrupt one is worth a warning rather than an
exception, because names are a convenience and refusing to open the browser
over one would be the wrong trade.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, Optional

from rxved.favorites import data_dir

__all__ = ["FILE_NAME", "default_path", "load", "save", "key"]

#: The filename, in the data directory beside ``favorites.db``.
FILE_NAME = "live-names.json"


def key(bank_id: str, number: int) -> str:
    """The storage key for one slot. Same shape `Catalog` uses internally."""
    return f"{bank_id}:{number:03d}"


def default_path() -> str:
    """Where the hardware-read names live, per platform.

    Overridable with ``--live-names`` on both front ends, for the same
    reason ``--favorites`` is: so the file can be pointed somewhere a
    `git clean` will not reach, inspected, or thrown away.
    """
    return os.path.join(data_dir(), FILE_NAME)


def load(path: Optional[str] = None) -> Dict[str, str]:
    """Previously-read names, keyed ``"BANK:NNN"``. Empty if there are none."""
    target = path or default_path()
    try:
        with open(target, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # Not worth refusing to start over. The alternative is a browser
        # that will not open because a cache it can rebuild in three
        # seconds is malformed -- and it *can* be rebuilt, because the
        # synth is right there.
        print(
            f"rxved: ignoring {target}: {exc}. It is a cache of names read "
            f"from the synth; delete it and press r to read them again.",
            file=sys.stderr,
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    # Keep only what the rest of the program can use: a str key and a str
    # value. Anything else came from a hand-edit or a future version.
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def save(names: Dict[str, str], path: Optional[str] = None) -> None:
    """Write the live layer out. Best effort -- a cache is not required.

    Read-modify-write rather than replace, so a `--live-names` file that
    also holds something else keeps it. Losing the whole file because one
    bank could not be read would be a poor way to lose one bank's names.
    """
    target = path or default_path()
    existing = load(target)
    existing.update(names)
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        # Write-then-rename: a crash or a full disk mid-write leaves the
        # previous file intact rather than a half-written one, which json
        # would then refuse to parse.
        temp = target + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(existing, handle, indent=1, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp, target)
    except OSError:
        # Same reasoning as `_update_config`: the names are still on screen,
        # they just will not survive a restart.
        pass
