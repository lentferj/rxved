# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
# The Bank Select / Program Change map is transcribed as data from Roland's
# own XV-2020 Owner's Manual (Roland Corporation, 2003): the "Selecting
# Patches / Rhythm Sets / Performances" tables on pp. 40-41 and the
# BANK SELECT / PROGRAM NUMBER / GROUP table in the MIDI Implementation
# chapter, p. 136. See docs/RESOLUTION_NOTES.md §2.
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

"""What Bank Select MSB, LSB and Program Change select on an XV-2020.

**Partly verified against hardware, on 2026-09-11.** What was exercised
against a real XV-2020 and what was not is set out in DISCLAIMER.md and
docs/RESOLUTION_NOTES.md -- read which is which before trusting a byte
offset.

The whole point of rxved's first screen is this table, so it is worth being
precise about the three numbering traps in it.

**The manual counts programs from 1; MIDI counts them from 0.** The tables on
p. 40 say "Program number 001-128", and step 6 of the same procedure says
"Send a Program Change with a value of 18" to reach patch 18. Those cannot
both be the byte on the wire, and the byte is the 0-based one -- the MIDI
spec has no program 128. :class:`Slot` therefore carries ``number`` (what the
machine displays, 1-based) and ``program_change`` (what goes on the wire,
0-based) as **separate** attributes, and never converts silently between
them. Displaying a 0-based number next to the machine's own display is the
mistake this split exists to prevent.

**The MSB says what kind of thing, not which bank.** ``85`` is a
Performance, ``86`` a Rhythm Set, ``87`` a Patch; the LSB then picks USER
(``0``) or a preset bank (``64``, ``65``, ...). GM is the exception and
breaks the pattern twice over: it lives at its own MSBs (``120`` rhythm,
``121`` patch) and there the **LSB is a variation number, not a bank** -- GM2
capital sounds are LSB 0 and each variation is a further LSB at the *same*
program change. That is why :data:`GM_PATCH` is not simply a 256-entry bank.

**Preset Rhythm A/B are given two different sizes by the same manual.**
p. 40 says 001-004; the p. 136 table says "001 - 002" in the number column
and "001 - 004" in the group column of the same row. rxved uses 4, matching
p. 40 and matching the Rhythm Set List (which prints four named kits per
preset group, pp. 130-133). Recorded rather than quietly resolved: this is
exactly the kind of thing a hardware session should settle -- see TODO.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

__all__ = [
    "Kind",
    "Bank",
    "Slot",
    "BANKS",
    "SRX_CARDS",
    "SRX_SOURCE",
    "SrxCard",
    "srx_card",
    "SRX_BANKS",
    "PATCH_BANKS",
    "RHYTHM_BANKS",
    "PERFORMANCE_BANKS",
    "MSB_PERFORMANCE",
    "MSB_RHYTHM",
    "MSB_PATCH",
    "MSB_SRX_RHYTHM",
    "MSB_SRX_PATCH",
    "MSB_GM_RHYTHM",
    "MSB_GM_PATCH",
    "GM_RHYTHM_PROGRAMS",
    "bank",
    "bank_ids",
    "slots",
    "slot",
    "lookup",
]


# --- what the MSBs mean -----------------------------------------------------

MSB_PERFORMANCE = 85
MSB_RHYTHM = 86
MSB_PATCH = 87
MSB_SRX_RHYTHM = 92
MSB_SRX_PATCH = 93
MSB_GM_RHYTHM = 120
MSB_GM_PATCH = 121

#: The nine GM2 rhythm sets do **not** sit at consecutive program changes.
#: They are the GM2 drum-kit program numbers, and the gaps are part of the
#: standard: STANDARD, ROOM, POWER, ELECTRONIC, TR-808, JAZZ, BRUSH,
#: ORCHESTRA, SFX (OM pp. 133-134, "GM (GM2 Group)"). Listing them 1-9 and
#: multiplying by 8 gets eight of the nine right and is wrong about
#: ELECTRONIC/TR-808, which are adjacent.
#:
#: 1-based, as printed. The wire value is one less.
GM_RHYTHM_PROGRAMS: Tuple[int, ...] = (1, 9, 17, 25, 26, 33, 41, 49, 57)


class Kind:
    """What a bank holds. Plain strings, because they are shown to the user."""

    PATCH = "patch"
    RHYTHM = "rhythm"
    PERFORMANCE = "performance"


@dataclass(frozen=True)
class Bank:
    """One selectable bank: a fixed MSB/LSB pair and a range of programs."""

    #: Stable, terse identifier used in the config file, the favourites
    #: database and on the command line. Never localised, never renumbered:
    #: a favourites row that outlives a rename is worse than no favourite.
    id: str
    #: What the machine's own display calls it.
    label: str
    kind: str
    msb: int
    lsb: int
    #: How many slots, and what the first one is called on the display.
    count: int
    first_number: int = 1
    #: True for banks the user can overwrite. rxved is read-only today; this
    #: drives nothing but the display, and is the flag a future editor would
    #: gate writes on.
    writable: bool = False
    #: Set for banks whose contents depend on a fitted SRX expansion board.
    #: The XV-2020 takes up to two SRX boards (OM p. 7, "you can install up
    #: to two SRX Series Wave Expansion Boards"), and these exist in the MIDI
    #: map whether or not anything is in either slot.
    expansion: bool = False

    def numbers(self) -> range:
        """The numbers this bank's display shows, 1-based unless stated."""
        return range(self.first_number, self.first_number + self.count)

    def __len__(self) -> int:
        return self.count


# --- the banks --------------------------------------------------------------
#
# Written out one per line rather than generated from a range, so each row can
# be checked against the printed table without running anything.

PATCH_BANKS: Tuple[Bank, ...] = (
    Bank("USER", "USER", Kind.PATCH, MSB_PATCH, 0, 128, writable=True),
    Bank("PST-A", "PRESET A", Kind.PATCH, MSB_PATCH, 64, 128),
    Bank("PST-B", "PRESET B", Kind.PATCH, MSB_PATCH, 65, 128),
    Bank("PST-C", "PRESET C", Kind.PATCH, MSB_PATCH, 66, 128),
    Bank("PST-D", "PRESET D", Kind.PATCH, MSB_PATCH, 67, 128),
)

#: GM2 patches: 256 sounds reached as 128 program changes x an LSB variation
#: number. Modelled as one bank per variation depth rather than one flat bank
#: of 256, because that is what the wire actually does -- and because the
#: catalog's own "LSB / PC" columns (OM pp. 128-129) are written that way.
#: Variation 0 is the GM2 capital sound and is always present; the higher
#: variations are sparse, and which program changes have one is a property of
#: the catalog, not of this table.
GM_PATCH: Tuple[Bank, ...] = tuple(
    Bank(
        f"GM-{lsb}" if lsb else "GM",
        f"GM2 variation {lsb}" if lsb else "GM2",
        Kind.PATCH,
        MSB_GM_PATCH,
        lsb,
        128,
    )
    # 0-8 covers every variation the XV-2020's own GM patch list uses; the
    # deepest entry printed is LSB 9 ("Burst Noise"), so the range runs to 9.
    for lsb in range(0, 10)
)

RHYTHM_BANKS: Tuple[Bank, ...] = (
    Bank("R-USER", "USER", Kind.RHYTHM, MSB_RHYTHM, 0, 4, writable=True),
    Bank("R-PST-A", "PRESET A", Kind.RHYTHM, MSB_RHYTHM, 64, 4),
    Bank("R-PST-B", "PRESET B", Kind.RHYTHM, MSB_RHYTHM, 65, 4),
)

PERFORMANCE_BANKS: Tuple[Bank, ...] = (
    Bank("P-USER", "USER", Kind.PERFORMANCE, MSB_PERFORMANCE, 0, 64,
         writable=True),
    Bank("P-PST-A", "PRESET A", Kind.PERFORMANCE, MSB_PERFORMANCE, 64, 32),
    Bank("P-PST-B", "PRESET B", Kind.PERFORMANCE, MSB_PERFORMANCE, 65, 32),
)

# --- SRX expansion ----------------------------------------------------------
#
# The XV-2020's own manual is no use here. It prints the LSB column for both
# "EXP-A" and "EXP-B" as "0-" -- declining to say -- and defers to the card:
# "The SRX series corresponding to each Bank Select are to see the SRX series
# owner's manual" (p. 136). rxved first modelled that as EXP-A = LSB 0 and
# EXP-B = LSB 1, which was a guess and was wrong; then rebuilt it from five
# individual card manuals, which was right about those five and wrong about
# the shape.
#
# **The authority is Roland's own series-wide table**: Supplemental Note
# SN 132 v3.00 (Summer 2007), "Selecting Internal and SRX-Series Sounds Via
# MIDI", which lists every board's MSB, LSB and program-change range in one
# place. :data:`SRX_CARDS` is transcribed from it, and it supersedes the
# per-card readings -- it corrected two of them (SRX-02 has 50 patches, not
# a full 128; SRX-08 has 21 rhythm sets, not the 12 a truncated column
# suggested).
#
# What that table shows:
#
# * The LSB is not a bank letter and not a fixed stride per card. Each board
#   gets as many consecutive LSBs as its patch count needs at 128 per LSB.
#   SRX-01 needs one (41 patches), SRX-05 three (312), SRX-07 four (475).
# * A board's rhythm LSB is its **first** patch LSB, under MSB 92 instead of
#   93. Boards with no rhythm sets -- 02, 04, 10, 11, 12, 97, 98 -- simply
#   have no row in that half of the table.
# * The allocation is *nearly* sequential and **has a hole**: SRX-11 ends at
#   LSB 24 and SRX-12 starts at 26, so LSB 25 belongs to nothing. Then 97
#   and 98 sit far out at LSB 97 and 98.
#
# That hole is why this is a transcribed table and not an arithmetic
# function over the board number. Any formula fitted to the low boards gets
# SRX-12 wrong, and a wrong LSB here does not fail loudly -- it selects a
# different board's patch, or nothing at all.
#
# "EXP-A"/"EXP-B" in the XV-2020's own table are its two expansion *slots* --
# the machine takes up to two boards (OM p. 7) and calls them Wave Expansion
# Board A and B (OM p. 46, p. 94). An earlier note here claimed it had one
# slot and that A/B were halves of a single board's range; that was wrong on
# both counts.
#
# It changes nothing about the table below, and it is worth being clear why.
# The LSB identifies the **board**, not the slot it sits in -- SN 132 is
# explicit: "Expansion-board sounds are properly found and selected no matter
# where your expansion boards are installed." So an SRX-07 answers on LSB
# 11-14 in either slot, and rxved never needs to know which slot holds what.
# EXP-A/EXP-B matter only at the front panel.
#
# rxved can still find an unlisted board by probing, which stays useful for
# anything Roland added after SN 132 -- see
# :meth:`xv.bridge.XvBridge.probe_srx`.


@dataclass(frozen=True)
class SrxCard:
    """An SRX expansion card's Bank Select allocation."""

    id: str
    label: str
    #: First of the four consecutive patch LSBs at :data:`MSB_SRX_PATCH`.
    patch_lsb_base: int
    #: Total patches on the card, spread 128 per LSB.
    patch_count: int
    #: The single rhythm LSB at :data:`MSB_SRX_RHYTHM` -- always the card's
    #: first patch LSB. ``None`` for a card with no rhythm sets at all; the
    #: SRX-02 is a piano card and has none, and giving it an empty rhythm
    #: bank would put a row in the browser that can never hold anything.
    rhythm_lsb: Optional[int]
    rhythm_count: int
    #: Whether an XV-2020 can use this board at all.
    #:
    #: SN 132 is a MIDI-selection guide covering the whole SRX series across
    #: every host that takes one, so it lists a board's Bank Select whether
    #: or not any particular machine can play it. The late "Special SRX
    #: Board" pair are the case where that matters: SRX-98's own manual says
    #: "The SRX-98 is compatible with following Roland products. **No other
    #: products can be used.**" and then names Fantom-X, JUNO-G, Fantom-S,
    #: Fantom (FA-76) and XV-5080/5050 -- the XV-2020 does not appear in that
    #: list, or anywhere else in the manual.
    #:
    #: A board marked False keeps its row here, because the row is correct
    #: for the hosts that can use it, but contributes no bank to
    #: :data:`BANKS`: offering a browser slot that this machine can never
    #: address is worse than leaving it out.
    xv2020: bool = True

    @property
    def patch_lsbs(self) -> range:
        """Every LSB this card's patches occupy.

        Derived from the patch count rather than assumed to be four: SRX-02
        uses one and SRX-05 uses three, so a fixed span would claim LSBs
        belonging to the next card in the series.
        """
        pages = (self.patch_count + 127) // 128
        return range(self.patch_lsb_base, self.patch_lsb_base + pages)

    def patch_banks(self) -> List["Bank"]:
        """One :class:`Bank` per LSB page, the last one short."""
        out: List[Bank] = []
        remaining = self.patch_count
        page = 0
        while remaining > 0:
            size = min(128, remaining)
            out.append(
                Bank(
                    id=f"{self.id}-{page + 1}",
                    label=f"{self.label} {page * 128 + 1}-"
                          f"{page * 128 + size}",
                    kind=Kind.PATCH,
                    msb=MSB_SRX_PATCH,
                    lsb=self.patch_lsb_base + page,
                    count=size,
                    expansion=True,
                )
            )
            remaining -= size
            page += 1
        return out

    def rhythm_bank(self) -> Optional["Bank"]:
        """The card's rhythm bank, or ``None`` if it has no rhythm sets."""
        if self.rhythm_lsb is None or not self.rhythm_count:
            return None
        return Bank(
            id=f"{self.id}-R", label=f"{self.label} rhythm", kind=Kind.RHYTHM,
            msb=MSB_SRX_RHYTHM, lsb=self.rhythm_lsb, count=self.rhythm_count,
            expansion=True,
        )


#: Every SRX board, transcribed from Roland Supplemental Note SN 132 v3.00,
#: "Selecting Internal and SRX-Series Sounds Via MIDI" (2007), which gives
#: the MSB, LSB and program-change range for the whole series in one table.
#: Nothing here is extrapolated -- see the note above on why it cannot be.
SRX_CARDS: Tuple[SrxCard, ...] = (
    SrxCard("SRX-01", "SRX-01", 0, 41, rhythm_lsb=0, rhythm_count=79),
    SrxCard("SRX-02", "SRX-02", 1, 50, rhythm_lsb=None, rhythm_count=0),
    SrxCard("SRX-03", "SRX-03", 2, 128, rhythm_lsb=2, rhythm_count=12),
    SrxCard("SRX-04", "SRX-04", 3, 128, rhythm_lsb=None, rhythm_count=0),
    SrxCard("SRX-05", "SRX-05", 4, 312, rhythm_lsb=4, rhythm_count=34),
    SrxCard("SRX-06", "SRX-06", 7, 449, rhythm_lsb=7, rhythm_count=5),
    SrxCard("SRX-07", "SRX-07", 11, 475, rhythm_lsb=11, rhythm_count=11),
    SrxCard("SRX-08", "SRX-08", 15, 448, rhythm_lsb=15, rhythm_count=21),
    SrxCard("SRX-09", "SRX-09", 19, 414, rhythm_lsb=19, rhythm_count=12),
    SrxCard("SRX-10", "SRX-10", 23, 100, rhythm_lsb=None, rhythm_count=0),
    SrxCard("SRX-11", "SRX-11", 24, 42, rhythm_lsb=None, rhythm_count=0),
    # LSB 25 is assigned to nothing; SRX-12 starts at 26. Transcribed, not
    # a typo -- see the note above.
    # 105 is SN 132's figure and is what the MIDI map addresses. The board's
    # own manual lists 105 patches for the Fantom-X/S and JUNO-G but only
    # **50** for the XV series -- the 55 it leaves out are the velocity-
    # switched "/Bite ... Pk4Mt" patches. Kept at 105 because SN 132 is the
    # authority on what the Bank Select map reaches and it names the XV-2020
    # among the hosts; whether an XV-2020 actually sounds patches 51-105 is
    # untested, and there is no board here to test it with.
    SrxCard("SRX-12", "SRX-12", 26, 105, rhythm_lsb=None, rhythm_count=0),
    # The two "Special SRX Board" promotional releases, and the only two
    # rows here an XV-2020 cannot use.
    #
    # SRX-98 "Analog Essentials" (2006) is settled by its own manual, quoted
    # above. SRX-97 "Jon Lord's Rock Organ" (2007) is the same series and no
    # listing found for it names the XV-2020 either -- but its manual is not
    # to hand, so that is inference rather than a quotation, and it is
    # marked as such here rather than presented as established.
    SrxCard("SRX-97", "SRX-97", 97, 12, rhythm_lsb=None, rhythm_count=0,
            xv2020=False),
    SrxCard("SRX-98", "SRX-98", 98, 78, rhythm_lsb=None, rhythm_count=0,
            xv2020=False),
)

#: The single source every row above came from, for the README's attribution
#: table and for anyone checking a number against the original.
SRX_SOURCE = ("Roland Supplemental Note SN 132 v3.00 (Summer 2007), "
              "\u201cSelecting Internal and SRX-Series Sounds Via MIDI\u201d")

_CARDS_BY_ID: Dict[str, SrxCard] = {c.id: c for c in SRX_CARDS}


def srx_card(card_id: str) -> SrxCard:
    try:
        return _CARDS_BY_ID[card_id]
    except KeyError:
        raise LookupError(
            f"no SRX card {card_id!r} on record; rxved has the allocation for "
            f"{', '.join(sorted(_CARDS_BY_ID))}. Other cards can still be "
            f"browsed -- their LSBs are found by probing the device."
        ) from None


SRX_BANKS: Tuple[Bank, ...] = tuple(
    b for card in SRX_CARDS if card.xv2020
    for b in (*card.patch_banks(), card.rhythm_bank())
    if b is not None
)

#: GM2 rhythm sets. A bank of nine, but at the program changes named in
#: :data:`GM_RHYTHM_PROGRAMS`, not at 1-9 -- so this one bank cannot be
#: described by ``first_number`` and ``count`` and is special-cased in
#: :func:`slots`.
GM_RHYTHM = Bank("R-GM", "GM2", Kind.RHYTHM, MSB_GM_RHYTHM, 0,
                 len(GM_RHYTHM_PROGRAMS))

BANKS: Tuple[Bank, ...] = (
    PATCH_BANKS + GM_PATCH
    + tuple(b for b in SRX_BANKS if b.kind == Kind.PATCH)
    + RHYTHM_BANKS + (GM_RHYTHM,)
    + tuple(b for b in SRX_BANKS if b.kind == Kind.RHYTHM)
    + PERFORMANCE_BANKS
)

_BY_ID: Dict[str, Bank] = {b.id: b for b in BANKS}


def bank(bank_id: str) -> Bank:
    """The bank with this id, or ``LookupError``."""
    try:
        return _BY_ID[bank_id]
    except KeyError:
        raise LookupError(
            f"no bank {bank_id!r}; have {', '.join(sorted(_BY_ID))}"
        ) from None


def bank_ids(kind: Optional[str] = None) -> List[str]:
    """Bank ids, in display order, optionally filtered to one kind."""
    return [b.id for b in BANKS if kind is None or b.kind == kind]


# --- slots ------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    """One addressable sound: where it lives and how to select it.

    ``number`` is what the XV-2020's display shows (1-based).
    ``program_change`` is the byte that goes on the wire (0-based). They are
    kept apart deliberately; see the module docstring.
    """

    bank: Bank
    number: int
    program_change: int

    @property
    def bank_id(self) -> str:
        return self.bank.id

    @property
    def kind(self) -> str:
        return self.bank.kind

    @property
    def msb(self) -> int:
        return self.bank.msb

    @property
    def lsb(self) -> int:
        return self.bank.lsb

    @property
    def key(self) -> str:
        """A stable identity for this slot, for the favourites database.

        Bank id plus display number rather than the raw MSB/LSB/PC triple:
        the triple is what the wire wants, but two of its three components
        are already implied by the bank, and a favourites row keyed on the
        wire bytes would be unreadable in the database file.
        """
        return f"{self.bank.id}:{self.number:03d}"

    def select_messages(self, channel: int = 0) -> List[List[int]]:
        """The three MIDI messages that select this slot, in required order.

        Bank Select MSB (CC#0), Bank Select LSB (CC#32), then Program
        Change. The order matters and the manual's own procedure states it
        (p. 40, steps 4-6): the XV-2020 latches the bank on the *Program
        Change*, so a PC sent before the LSB selects from whatever bank was
        latched before.
        """
        if not 0 <= channel <= 15:
            raise ValueError(f"MIDI channel {channel} is outside 0-15")
        return [
            [0xB0 | channel, 0, self.msb],
            [0xB0 | channel, 32, self.lsb],
            [0xC0 | channel, self.program_change],
        ]

    def __str__(self) -> str:
        return f"{self.bank.id} {self.number:03d}"


def slots(bank_id: str) -> List[Slot]:
    """Every slot in a bank, in display order."""
    target = bank(bank_id)
    if target is GM_RHYTHM:
        # The one bank whose program changes are not contiguous.
        return [
            Slot(target, index + 1, program - 1)
            for index, program in enumerate(GM_RHYTHM_PROGRAMS)
        ]
    return [
        Slot(target, number, number - 1) for number in target.numbers()
    ]


def slot(bank_id: str, number: int) -> Slot:
    """One slot by bank and display number (1-based)."""
    for candidate in slots(bank_id):
        if candidate.number == number:
            return candidate
    target = bank(bank_id)
    raise LookupError(
        f"{bank_id} has no slot {number}; it holds {target.count} "
        f"({target.first_number}-{target.first_number + target.count - 1})"
    )


def lookup(msb: int, lsb: int, program_change: int) -> Optional[Slot]:
    """The slot a given MSB/LSB/PC triple selects, or ``None``.

    ``program_change`` is the **wire** value, 0-based -- the same thing a
    MIDI monitor prints. Returns ``None`` for a triple no bank claims rather
    than raising, since this is the natural way to interpret whatever
    arrives from a device or a sequencer, most of which is not addressed to
    us.
    """
    for candidate in BANKS:
        if candidate.msb != msb or candidate.lsb != lsb:
            continue
        for entry in slots(candidate.id):
            if entry.program_change == program_change:
                return entry
    return None
