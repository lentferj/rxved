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

from typing import List, Sequence, Tuple

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
    "MFX_TYPES",
    "MFX_CONTROL_SOURCES",
    "MFX_HEADER",
    "CHORUS_TYPES",
    "CHORUS_OUTPUT_SELECT",
    "CHORUS_HEADER",
    "REVERB_TYPES",
    "REVERB_HEADER",
    "mfx_control_source",
    "note_name",
    "encode_int2x4",
    "decode_int2x4",
    "encode_int4x4",
    "decode_int4x4",
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


def encode_int2x4(value: int) -> List[int]:
    """Roland's ``int2x4``: one value as two bytes of four bits each.

    The address map prints these ``0000 aaaa / 0000 bbbb``, high nibble
    first, so a byte that is one parameter's neighbour is another
    parameter's high half. That is why Part Portamento Time occupies two
    offsets and cannot be written a byte at a time (OM p. 149); Part
    Portamento Time and the System, Patch and Rhythm tempos are the
    parameters of this type rxved touches.
    """
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{value} does not fit in two nibbles")
    return [(value >> 4) & 0x0F, value & 0x0F]


def decode_int2x4(data: Sequence[int]) -> int:
    """The inverse of :func:`encode_int2x4`."""
    if len(data) < 2:
        raise ValueError(f"int2x4 needs two bytes, got {len(data)}")
    return ((data[0] & 0x0F) << 4) | (data[1] & 0x0F)


def encode_int4x4(value: int) -> List[int]:
    """Roland's ``int4x4``: a 16-bit value as four bytes of four bits each.

    Every effect parameter -- MFX, Chorus and Reverb -- is this type, and
    the map prints the range as ``12768 -- 52768`` beside the display value
    ``-20000 -- +20000``: the wire value is the display value plus 32768,
    and the four nibbles run high to low. Nothing writes one yet; it lives
    here so the encoding is beside its sibling rather than reinvented when
    the effects editor is written.
    """
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"{value} does not fit in four nibbles")
    return [(value >> shift) & 0x0F for shift in (12, 8, 4, 0)]


def decode_int4x4(data: Sequence[int]) -> int:
    """The inverse of :func:`encode_int4x4`."""
    if len(data) < 4:
        raise ValueError(f"int4x4 needs four bytes, got {len(data)}")
    value = 0
    for byte in data[:4]:
        value = (value << 4) | (byte & 0x0F)
    return value


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

#: MFX Type (offset ``00 00`` of the MFX block), 0-40. 0 is OFF; the rest
#: are the manual's algorithms in its own order (OM pp. 82-91).
MFX_TYPES = {
    0: "OFF",
    1: "STEREO EQ",
    2: "OVERDRIVE",
    3: "DISTORTION",
    4: "PHASER",
    5: "SPECTRUM",
    6: "ENHANCER",
    7: "AUTO WAH",
    8: "ROTARY",
    9: "COMPRESSOR",
    10: "LIMITER",
    11: "HEXA-CHORUS",
    12: "TREMOLO CHORUS",
    13: "SPACE-D",
    14: "STEREO CHORUS",
    15: "STEREO FLANGER",
    16: "STEP FLANGER",
    17: "STEREO DELAY",
    18: "MODULATION DELAY",
    19: "TRIPLE TAP DELAY",
    20: "QUADRUPLE TAP DELAY",
    21: "TIME CONTROL DELAY",
    22: "2 VOICE PITCH SHIFTER",
    23: "FB PITCH SHIFTER",
    24: "REVERB",
    25: "GATED REVERB",
    26: "OVERDRIVE -> CHORUS",
    27: "OVERDRIVE -> FLANGER",
    28: "OVERDRIVE -> DELAY",
    29: "DISTORTION -> CHORUS",
    30: "DISTORTION -> FLANGER",
    31: "DISTORTION -> DELAY",
    32: "ENHANCER -> CHORUS",
    33: "ENHANCER -> FLANGER",
    34: "ENHANCER -> DELAY",
    35: "CHORUS -> DELAY",
    36: "FLANGER -> DELAY",
    37: "CHORUS -> FLANGER",
    38: "CHORUS/DELAY",
    39: "FLANGER/DELAY",
    40: "CHORUS/FLANGER",
}


def mfx_control_source(value: int) -> str:
    """MFX Control Source: OFF, CC01-31, CC33-95, BEND, AFTER, SYS1-4.

    The map prints the range as 0-101 and the list exactly that way (OM
    p. 146): 0 is OFF, 1-31 are CC01-CC31, 32-94 are CC33-CC95 -- CC32,
    Bank Select, is skipped -- and 95-100 are the six non-CC sources. The
    top of the range is unnamed, and shown as the bare number.
    """
    if value == 0:
        return "OFF"
    if 1 <= value <= 31:
        return f"CC{value:02d}"
    if 32 <= value <= 94:
        return f"CC{value + 1:02d}"
    return {
        95: "BEND",
        96: "AFTER",
        97: "SYS1",
        98: "SYS2",
        99: "SYS3",
        100: "SYS4",
    }.get(value, str(value))


#: The MFX Control Source list, 0-101, as the screen's lookup.
MFX_CONTROL_SOURCES = {value: mfx_control_source(value) for value in range(102)}

#: Chorus Type and Reverb Type (offset ``00 00`` of each block). The
#: *performance* chorus and reverb on this model are OFF/on only; the patch
#: blocks carry the fuller algorithm lists (OM p. 146 against p. 79).
CHORUS_TYPES = {0: "OFF", 1: "CHORUS"}
REVERB_TYPES = {0: "OFF", 1: "REVERB"}

#: Chorus Output Select (offset ``00 03``).
CHORUS_OUTPUT_SELECT = {0: "MAIN", 1: "REV", 2: "MAIN+REV"}

#: One effect block's head, as ``(offset, label, low, high, names, bias)``.
#: ``bias`` is added to the displayed value to get the wire byte, so the
#: MFX control sensitivities read -63..+63 the way the manual prints them.
#: Only the head: the dozens of per-algorithm parameters that follow mean
#: nothing without knowing the algorithm, and are the next piece of work.
MFX_HEADER = (
    (0x00, "Type", 0, 40, MFX_TYPES, 0),
    (0x01, "Dry Send", 0, 127, None, 0),
    (0x02, "Chorus Send", 0, 127, None, 0),
    (0x03, "Reverb Send", 0, 127, None, 0),
    (0x04, "Output Assign", 0, 3, OUTPUT_ASSIGN, 0),
    (0x05, "Control 1 Source", 0, 101, MFX_CONTROL_SOURCES, 0),
    (0x06, "Control 1 Sens", -63, 63, None, 64),
    (0x07, "Control 2 Source", 0, 101, MFX_CONTROL_SOURCES, 0),
    (0x08, "Control 2 Sens", -63, 63, None, 64),
    (0x09, "Control 3 Source", 0, 101, MFX_CONTROL_SOURCES, 0),
    (0x0A, "Control 3 Sens", -63, 63, None, 64),
    (0x0B, "Control 4 Source", 0, 101, MFX_CONTROL_SOURCES, 0),
    (0x0C, "Control 4 Sens", -63, 63, None, 64),
)

CHORUS_HEADER = (
    (0x00, "Type", 0, 1, CHORUS_TYPES, 0),
    (0x01, "Level", 0, 127, None, 0),
    (0x02, "Output Assign", 0, 3, OUTPUT_ASSIGN, 0),
    (0x03, "Output Select", 0, 2, CHORUS_OUTPUT_SELECT, 0),
)

REVERB_HEADER = (
    (0x00, "Type", 0, 1, REVERB_TYPES, 0),
    (0x01, "Level", 0, 127, None, 0),
    (0x02, "Output Assign", 0, 3, OUTPUT_ASSIGN, 0),
)


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
