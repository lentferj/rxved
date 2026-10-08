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

"""Lookup tables and display conversions for performance parameters.

Separate from :mod:`xv.bridge` for one reason: bridge imports rtmidi at
module scope, and the TUI needs these tables to draw a screen in ``--demo``
mode on a host with no MIDI stack at all. Keeping a second copy in the app
was the alternative, and a second copy of a table indexed by a wire byte is
how every value ends up off by one.

Nothing here talks to MIDI or holds state.
"""

from typing import Tuple

__all__ = [
    "PERFORMANCE_BLOCKS",
    "TEMPORARY_PERFORMANCE",
    "USER_PERFORMANCE_SLOTS",
    "user_performance_base",
    "SIGNED_PART_FIELDS",
    "MONO_POLY",
    "ON_OFF_PATCH",
    "OUTPUT_ASSIGN",
    "OUTPUT_ASSIGN_ON_XV2020",
    "OUTPUT_MFX",
    "note_name",
]


#: Part fields the synth stores biased by 64: the wire byte is 64 higher
#: than the number the manual prints. Listed rather than converted in place,
#: because this project's rule is that a displayed number and a wire byte
#: are never silently the same thing -- the same rule as program numbers in
#: :mod:`xv.banks`.
SIGNED_PART_FIELDS = frozenset(
    {
        "pan",
        "coarse",
        "fine",
        "octave",
        "velocity_sens",
        "cutoff_offset",
        "resonance_offset",
        "attack_offset",
        "decay_offset",
        "release_offset",
        "vibrato_rate",
        "vibrato_depth",
        "vibrato_delay",
    }
)

#: Part Mono/Poly (offset ``00 0B``).
MONO_POLY = {0: "MONO", 1: "POLY", 2: "PATCH"}

#: Part Legato Switch and Part Portamento Switch (offsets ``00 0C`` and
#: ``00 0E``), which share Mono/Poly's three-value shape.
ON_OFF_PATCH = {0: "OFF", 1: "ON", 2: "PATCH"}

#: Note names for the keyboard-range columns. The XV-2020 counts C-1 as note
#: 0, so note 60 is C4 (OM p. 73's range column).
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(number: int) -> str:
    """``60`` -> ``"C4"``. The keyboard-range columns are unreadable as
    bare integers and perfectly readable as notes."""
    if not 0 <= number <= 127:
        return str(number)
    return f"{_NOTE_NAMES[number % 12]}{number // 12 - 1}"


#: Part Output Assign (offset ``00 1F``), 0-13. The manual prints the whole
#: XV-series list; entries it marks ``<*>`` are **ignored when the XV-2020
#: receives them** (OM p. 146), because this box has one stereo output pair
#: and one MFX where its bigger siblings have eight and three. They are
#: named here rather than omitted: a performance written on an XV-5080 can
#: arrive carrying one, and "output 6" explains a silent part where a blank
#: does not.
OUTPUT_ASSIGN = {
    0: "MFX",
    1: "A",
    2: "B*",
    3: "C*",
    4: "D*",
    5: "1",
    6: "2",
    7: "3*",
    8: "4*",
    9: "5*",
    10: "6*",
    11: "7*",
    12: "8*",
    13: "PATCH",
}

#: Values of Part Output Assign that this model actually honours.
OUTPUT_ASSIGN_ON_XV2020 = frozenset({0, 1, 5, 6, 13})

#: Part Output MFX Select (offset ``00 20``). The XV-2020 has one MFX, so
#: only MFXA is real; the other two are ``<*>`` entries.
OUTPUT_MFX = {0: "MFXA", 1: "MFXB*", 2: "MFXC*"}


#: A performance, block by block: ``(name, (sub_hi, sub_lo), size)``.
#:
#: Sizes are the "Total Size" each section of the parameter address map
#: prints (OM pp. 147-149), and every one was checked against its own last
#: offset -- ``size - 1`` in each case, which is the arithmetic that catches
#: a mis-paired heading. They were read by cropping the PDF's two columns
#: apart rather than from a ``-layout`` dump, after that dump's interleaving
#: produced a confidently wrong claim about the Mute Switch; see
#: RESOLUTION_NOTES §12b.
#:
#: 36 blocks, 1309 bytes. This is the whole of a performance as the machine
#: stores it -- and note what that does *not* include: the manual is
#: explicit that saving a performance saves "only the Performance settings",
#: not the patches its parts point at (OM p. 92).
PERFORMANCE_BLOCKS: Tuple[Tuple[str, Tuple[int, int], int], ...] = (
    (
        ("common", (0x00, 0x00), 53),
        ("mfx", (0x02, 0x00), 145),
        ("chorus", (0x04, 0x00), 52),
        ("reverb", (0x06, 0x00), 83),
    )
    + tuple((f"midi{channel + 1}", (0x10 + channel, 0x00), 12) for channel in range(16))
    + tuple((f"part{part + 1}", (0x20 + part, 0x00), 49) for part in range(16))
)

#: Address prefix of the temporary performance -- the edit buffer.
TEMPORARY_PERFORMANCE = (0x10, 0x00)

#: How many user performance slots the XV-2020 has.
USER_PERFORMANCE_SLOTS = 64


def user_performance_base(slot: int) -> Tuple[int, int]:
    """Address prefix of User Performance ``slot`` (1-64).

    ``20 00 00 00`` is User Performance 01 and ``20 3F 00 00`` is 64 (OM
    p. 146), so the second byte is the slot **minus one** -- the same
    off-by-one this project keeps visible everywhere else.
    """
    if not 1 <= slot <= USER_PERFORMANCE_SLOTS:
        raise ValueError(
            f"user performance slot {slot} is outside 1-{USER_PERFORMANCE_SLOTS}"
        )
    return (0x20, slot - 1)
