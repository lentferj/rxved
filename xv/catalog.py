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

"""Names for the slots :mod:`xv.banks` can address.

:mod:`xv.banks` knows that ``PST-B 029`` is MSB 87 / LSB 65 / PC 28. It does
not know that anything is called anything. This module is where names come
from, and there are two sources with very different characters:

**The manual's printed lists** (OM pp. 124-134) name all 640 internal
patches, both preset rhythm groups and all three performance banks. They are
complete, instant, and correct for a machine at factory settings.

**The device itself** names only what it can be asked about. The address map
gives addresses for User Patches, User Performances and User Rhythms, so
those read directly. It gives **no address for the preset banks** -- they are
ROM and simply are not in the map -- so the only way to read a preset name
off the hardware is to select it and then read the Temporary Patch area,
which changes what the machine is playing. That is a side effect, so rxved
never does it as part of ordinary browsing.

The two disagree exactly where it matters: after the user has saved anything
into USER, the printed USER list is a record of what the machine shipped
with, not of what is in it now. So :class:`Catalog` keeps the two apart --
:meth:`Catalog.name` reports the printed name, :meth:`Catalog.live_name` the
one read from hardware, and :meth:`Catalog.display_name` prefers live when
there is one. A browser that quietly showed a factory name for a slot the
user has overwritten would be actively misleading about the one bank they
can change.

**The generated catalog file is not distributed with rxved.** It is built
from the owner's manual by ``tools/extract_catalog.py`` and written to
``xv/data/catalog.json``, which is gitignored -- the sibling eosed and s3ked
projects do not commit manufacturer content either. Everything here works
without it: banks, numbers, MSB, LSB and program change are rxved's own
arithmetic, and only the names go missing. See README.md.
"""

from __future__ import annotations

import json
import sys
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from xv import banks

__all__ = [
    "Entry",
    "Catalog",
    "DEFAULT_CATALOG_PATH",
    "CATEGORIES",
    "CATEGORY_NAMES",
    "UNCATEGORISED",
    "UNNAMED",
    "load",
    "empty",
]

#: Where ``tools/extract_catalog.py`` writes, and where :func:`load` looks.
DEFAULT_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "data", "catalog.json")

#: The patch categories, in the order the XV-2020's own category byte
#: indexes them (1-based), as code -> what the machine's display calls it.
#: Transcribed from the "Choosing Patches by Category" table, OM p. 37.
#:
#: The order is load-bearing: ``tools/extract_catalog.py`` indexes this to
#: turn the editor binary's category byte into a code, so reordering it
#: would silently relabel every preset patch.
CATEGORIES = (
    ("PNO", "AC.PIANO"),
    ("EP", "EL.PIANO"),
    ("KEY", "KEYBOARDS"),
    ("BEL", "BELL"),
    ("MLT", "MALLET"),
    ("ORG", "ORGAN"),
    ("ACD", "ACCORDION"),
    ("HRM", "HARMONICA"),
    ("AGT", "AC.GUITAR"),
    ("EGT", "EL.GUITAR"),
    ("DGT", "DIST.GUITAR"),
    ("BS", "BASS"),
    ("SBS", "SYNTH BASS"),
    ("STR", "STRINGS"),
    ("OCH", "ORCHESTRA"),
    ("HIT", "HIT&STAB"),
    ("WND", "WIND"),
    ("FLT", "FLUTE"),
    ("BRS", "AC.BRASS"),
    ("SBR", "SYNTH BRASS"),
    ("SAX", "SAX"),
    ("HLD", "HARD LEAD"),
    ("SLD", "SOFT LEAD"),
    ("TEK", "TECHNO SYNTH"),
    ("PLS", "PULSATING"),
    ("FX", "SYNTH FX"),
    ("SYN", "OTHER SYNTH"),
    ("BPD", "BRIGHT PAD"),
    ("SPD", "SOFT PAD"),
    ("VOX", "VOX"),
    ("PLK", "PLUCKED"),
    ("ETH", "ETHNIC"),
    ("FRT", "FRETTED"),
    ("PRC", "PERCUSSION"),
    ("SFX", "SOUND FX"),
    ("BTS", "BEAT&GROOVE"),
    ("DRM", "DRUMS"),
    ("CMB", "COMBINATION"),
)

#: Code -> display name, for anything that has to show one.
CATEGORY_NAMES = dict(CATEGORIES)

#: Shown in a category filter for slots the catalog has no category for --
#: every SRX slot today, since the expansion lists have not been extracted.
UNCATEGORISED = "(none)"

#: What a slot is called when nothing knows its name. Not an empty string:
#: an empty cell in the browser reads as a rendering fault, and this reads as
#: a missing catalog, which is what it is.
UNNAMED = "--"


@dataclass(frozen=True)
class Entry:
    """One named slot, as printed in the manual."""

    bank_id: str
    number: int
    name: str
    #: Three-letter category tag ("PNO", "SBS", ...), for internal patches
    #: only; the GM and performance lists print no category.
    category: Optional[str] = None
    #: Voices used by this patch, where the list prints it. Worth carrying:
    #: the XV-2020 is 64-voice and an 8-voice patch costs eight of them, so
    #: this is the number that predicts whether a performance will steal.
    voices: Optional[int] = None

    @property
    def key(self) -> str:
        return f"{self.bank_id}:{self.number:03d}"


class Catalog:
    """Printed names, plus whatever has been read back from the device.

    Deliberately not a plain dict. The two name sources have to stay
    distinguishable all the way to the screen -- see the module docstring --
    and a single mapping cannot do that.
    """

    def __init__(
        self, entries: Iterable[Entry] = (), *, source: str = "", generated: str = ""
    ) -> None:
        self._entries: Dict[str, Entry] = {}
        for entry in entries:
            self._entries[entry.key] = entry
        self._live: Dict[str, str] = {}
        #: Where these names came from, for the UI to cite.
        self.source = source
        self.generated = generated

    # --- printed names ------------------------------------------------------

    def entry(self, bank_id: str, number: int) -> Optional[Entry]:
        return self._entries.get(f"{bank_id}:{number:03d}")

    def name(self, bank_id: str, number: int) -> Optional[str]:
        """The name the manual prints for this slot, if the catalog has it."""
        found = self.entry(bank_id, number)
        return found.name if found is not None else None

    def set_name(
        self,
        bank_id: str,
        number: int,
        name: str,
        *,
        category: Optional[str] = None,
        voices: Optional[int] = None,
    ) -> None:
        """Set a slot's printed name, for a catalog built from a scan.

        A scan supplies names only, so category and voices are carried over
        from whatever the slot already had; passing them replaces that.
        """
        key = f"{bank_id}:{number:03d}"
        existing = self._entries.get(key)
        self._entries[key] = Entry(
            bank_id=bank_id,
            number=number,
            name=name,
            category=(
                category
                if category is not None
                else (existing.category if existing is not None else None)
            ),
            voices=(
                voices
                if voices is not None
                else (existing.voices if existing is not None else None)
            ),
        )

    def has(self, bank_id: str) -> bool:
        """Whether any slot in this bank is named."""
        prefix = f"{bank_id}:"
        return any(key.startswith(prefix) for key in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    # --- names read from the hardware ---------------------------------------

    def set_live_name(self, bank_id: str, number: int, name: str) -> None:
        """Record a name read from the device for this slot."""
        self._live[f"{bank_id}:{number:03d}"] = name

    def live_name(self, bank_id: str, number: int) -> Optional[str]:
        return self._live.get(f"{bank_id}:{number:03d}")

    def clear_live(self, bank_id: Optional[str] = None) -> None:
        """Forget device-read names, for one bank or all of them."""
        if bank_id is None:
            self._live.clear()
            return
        prefix = f"{bank_id}:"
        for key in [k for k in self._live if k.startswith(prefix)]:
            del self._live[key]

    # --- what the browser shows ---------------------------------------------

    def display_name(self, bank_id: str, number: int) -> str:
        """The best name available, hardware winning over print."""
        return self.live_name(bank_id, number) or self.name(bank_id, number) or UNNAMED

    def is_live(self, bank_id: str, number: int) -> bool:
        """Whether :meth:`display_name` is reporting a hardware read.

        The browser marks these, because for a USER slot the difference
        between "the manual says this shipped as X" and "the machine says
        this is X" is the whole question.
        """
        return f"{bank_id}:{number:03d}" in self._live

    def differs(self, bank_id: str, number: int) -> bool:
        """A slot whose hardware name is not the one the manual prints."""
        live = self.live_name(bank_id, number)
        printed = self.name(bank_id, number)
        return live is not None and printed is not None and live != printed

    def printed_list_void(self, bank_id: str, numbers: Iterable[int]) -> bool:
        """Whether the printed list has stopped describing this bank at all.

        The `*` marker means "the machine and the book disagree here", and it
        is worth one glance exactly when it is rare: somebody saved over
        slot 42 and the marker says so.

        Once **nothing** in the bank agrees, it is not rare, it is the whole
        bank, and the marker says nothing a glance can use. That is the
        normal state of a USER bank somebody actually works in -- measured on
        real hardware, 128 of 128 slots differing, with only slot 128 left
        holding the factory `INIT PATCH`. Marking all 128 is arithmetically
        true and completely silent.

        The rule is deliberately "no slot agrees", not a ratio: the printed
        list describes a bank as it shipped, so the moment the machine
        disagrees everywhere, that description is history and there is
        nothing left to compare against. It also needs no threshold and no
        minimum sample size, so it behaves the same on a 4-slot R-USER as on
        a 128-slot USER.

        The names themselves are not lost -- they are still in the catalog,
        and the browser shows the printed one for whichever slot is
        highlighted. Only the per-row marker goes.
        """
        compared = 0
        for number in numbers:
            if self.name(bank_id, number) is None:
                continue
            if self.live_name(bank_id, number) is None:
                # Not read from the synth: no evidence either way.
                return False
            compared += 1
            if self.name(bank_id, number) == self.live_name(bank_id, number):
                return False
        return compared > 0

    def category(self, bank_id: str, number: int) -> str:
        """This slot's category code, or :data:`UNCATEGORISED`."""
        entry = self.entry(bank_id, number)
        if entry is None or not entry.category:
            return UNCATEGORISED
        return entry.category

    def categories_in(self, slots) -> Dict[str, int]:
        """``{category: count}`` over the given ``(bank_id, number)`` pairs.

        Counted over what the user is actually looking at rather than over
        the whole catalog, so a filter never offers a category that would
        select nothing -- and so the counts tell you what you are about to
        narrow to.
        """
        counts: Dict[str, int] = {}
        for bank_id, number in slots:
            code = self.category(bank_id, number)
            counts[code] = counts.get(code, 0) + 1
        order = {code: i for i, (code, _) in enumerate(CATEGORIES)}
        return dict(sorted(counts.items(), key=lambda kv: order.get(kv[0], len(order))))

    # --- searching ----------------------------------------------------------

    def search(
        self, needle: str, *, kind: Optional[str] = None
    ) -> List[Tuple[str, int, str]]:
        """``(bank_id, number, name)`` for every slot matching ``needle``.

        Case-insensitive substring, over both printed and live names, so a
        slot the user has renamed on the machine is findable under its new
        name without a catalog rebuild.
        """
        lowered = needle.lower()
        wanted = None
        if kind is not None:
            wanted = set(banks.bank_ids(kind))
        out: List[Tuple[str, int, str]] = []
        seen = set()
        for key in list(self._entries) + list(self._live):
            if key in seen:
                continue
            seen.add(key)
            bank_id, _, number_text = key.partition(":")
            if wanted is not None and bank_id not in wanted:
                continue
            number = int(number_text)
            shown = self.display_name(bank_id, number)
            if lowered in shown.lower():
                out.append((bank_id, number, shown))
        out.sort(key=lambda row: (row[0], row[1]))
        return out


def empty() -> Catalog:
    """A catalog that knows no names. What you get with no data file."""
    return Catalog()


def load(path: Optional[str] = None) -> Catalog:
    """Read the generated catalog, or return an empty one.

    A missing file is not an error: rxved is useful without names -- the
    bank, number, MSB, LSB and program change are all computed, not looked
    up -- and the file is deliberately not shipped. A file that exists but
    cannot be parsed *is* worth complaining about, and raises, because that
    means the extractor produced something wrong and silently browsing 900
    unnamed slots would be a poor way to find out.
    """
    target = path or DEFAULT_CATALOG_PATH
    if not os.path.exists(target):
        return empty()
    with open(target, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    entries = []
    skipped = 0
    for bank_id, rows in raw.get("banks", {}).items():
        for row in rows:
            # One bad row must not cost the whole catalog. A missing
            # catalog is already normal here -- the program runs on numbers
            # alone and a connected unit can supply every name -- so a
            # corrupt one taking the program down with it is the odd
            # behaviour, not skipping the row.
            try:
                entries.append(
                    Entry(
                        bank_id=bank_id,
                        number=int(row["n"]),
                        name=row["name"],
                        category=row.get("category"),
                        voices=row.get("voices"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                skipped += 1
    if skipped:
        print(
            f"warning: {target} has {skipped} unreadable row(s), skipped; "
            f"regenerate it with tools/extract_catalog.py",
            file=sys.stderr,
        )
    return Catalog(
        entries,
        source=raw.get("source", ""),
        generated=raw.get("generated", ""),
    )


def dump(
    catalog: Catalog,
    path: str,
    *,
    source: str = "",
    generated: str = "",
    note: str = "",
) -> None:
    """Write a catalog back out in the format :func:`load` reads."""
    by_bank: Dict[str, List[dict]] = {}
    for entry in catalog._entries.values():  # noqa: SLF001 - same module
        row: Dict[str, object] = {"n": entry.number, "name": entry.name}
        if entry.category:
            row["category"] = entry.category
        if entry.voices:
            row["voices"] = entry.voices
        by_bank.setdefault(entry.bank_id, []).append(row)
    for rows in by_bank.values():
        rows.sort(key=lambda r: r["n"])
    payload: Dict[str, object] = {
        "source": source or catalog.source,
        "generated": generated or catalog.generated,
    }
    if note:
        payload["note"] = note
    payload["banks"] = by_bank
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
