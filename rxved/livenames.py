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

import os
from typing import Dict, Optional

from vinsynlib import livenames as _livenames
from vinsynlib.catalog import split_key
from vinsynlib.livenames import FILE_NAME, key

from rxved.favorites import data_dir

__all__ = ["FILE_NAME", "default_path", "load", "save", "key", "apply_to"]


def default_path() -> str:
    """Where the hardware-read names live, per platform.

    Overridable with ``--live-names`` on both front ends, for the same
    reason ``--favorites`` is: so the file can be pointed somewhere a
    `git clean` will not reach, inspected, or thrown away.
    """
    return os.path.join(data_dir(), FILE_NAME)


def load(path: Optional[str] = None) -> Dict[str, str]:
    """Previously-read names, keyed ``"BANK:NNN"``. Empty if there are none.

    A thin binding: the library's reader wants an explicit path, and both
    front ends default to the sidecar beside the favourites database, so the
    default is supplied here rather than at every call site. Everything
    else -- the lenient decode, the warning on a malformed cache, dropping
    entries that are not strings -- is the library's and lives in one place.
    """
    return _livenames.load(path or default_path())


def apply_to(catalog, path: Optional[str] = None) -> int:
    """Lay the stored names over `catalog`. Returns how many were applied.

    Both front ends need this before they draw anything, and the parsing is
    the part worth sharing: a key that does not parse is skipped rather than
    guessed at, because a name attached to the wrong slot is worse than a
    missing one -- it is a plausible wrong answer.

    Kept here rather than delegated because the library's version records a
    *kind* alongside the name, and this instrument's catalog has none -- its
    banks are patches, rhythm sets and performances, not two kinds sharing
    one id. The key grammar is the library's all the same.
    """
    applied = 0
    for stored, name in load(path).items():
        try:
            bank_id, _kind, number = split_key(stored)
        except ValueError:
            continue
        if bank_id:
            catalog.set_live_name(bank_id, number, name)
            applied += 1
    return applied


def save(names: Dict[str, str], path: Optional[str] = None) -> None:
    """Write the live layer out. Best effort -- a cache is not required.

    The library merges rather than replaces, so a ``--live-names`` file that
    also holds something else keeps it, and writes through a temporary plus
    ``os.replace`` so a crash mid-write leaves the previous file intact.
    Only the default path is added here.
    """
    _livenames.save(path or default_path(), names)
