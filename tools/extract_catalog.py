#!/usr/bin/env python3
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

"""Build ``xv/data/catalog.json`` from Roland's own files.

**The output of this tool is not distributed with rxved**, and neither is any
of its input. Both are Roland's; you run this against your own copies, on
your own machine, and the result is gitignored. rxved works without it -- see
xv/catalog.py.

Two sources, chosen per bank by which one is actually authoritative:

**The XV Editor binary is the better source and is preferred wherever it
reaches.** Roland's own editor embeds the preset patch names as fixed-width
12-byte records -- the same width the device stores them in -- so they come
out byte-exact, with the curly apostrophes intact that a PDF text layer turns
into mojibake ("Drifting'Comb", not "DriftingOComb"). The GM tables are better
still: each record carries its own Bank Select MSB, LSB and program change,
so that mapping is read rather than inferred.

**The PDFs cover what the binary does not.** The USER bank's factory contents
(the editor reads those from the device, so it embeds no copy) and the
performance and rhythm-set lists.

What this tool will **not** do is guess. Where the binary and a manual
disagree, it says so and keeps the binary; where a table cannot be located at
all, that bank is left out of the catalog rather than half-filled, because a
browser showing 60% of a bank's names with no indication which 40% are
missing is worse than one showing none.

Usage::

    python3 tools/extract_catalog.py --editor <XV-2020Editor.exe> \\
        [--patch-list xv2020patch.pdf] [--manual XV-2020_OM.pdf] \\
        [-o xv/data/catalog.json]

Typical locations on this machine are the defaults; pass yours if they
differ.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

# --- record layouts in the editor binary ------------------------------------
#
# Found by locating a known name and reading outwards; every boundary below is
# cross-checked against the printed lists before use, and the tool re-checks
# them at run time rather than trusting these offsets blindly.

#: Preset patch record: 12-byte space-padded name, NUL, category byte.
PRESET_RECORD = 14
#: 512 of them, contiguous: PST-A, PST-B, PST-C, PST-D, 128 each, in order.
PRESET_COUNT = 512
PRESET_BANKS = ("PST-A", "PST-B", "PST-C", "PST-D")

#: GM record: Bank Select MSB, LSB, program change (0-based), then the same
#: 12-byte name and NUL. This is the layout that makes the binary worth
#: preferring -- the wire mapping is data here, not something to derive.
GM_RECORD = 16
GM_PATCH_MSB = 121
GM_RHYTHM_MSB = 120

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xv import banks  # noqa: E402
from xv import catalog as cat  # noqa: E402

#: Patch categories, in the order the category byte indexes them (1-based).
#: The canonical list lives in xv/catalog.py, because the browser needs it
#: too -- keeping a second copy here is how the two would drift and every
#: preset patch would end up relabelled by one.
CATEGORIES = tuple(code for code, _name in cat.CATEGORIES)

DEFAULT_EDITOR = (
    "/home/lentferj/.wine64_roland/drive_c/Program Files (x86)/Roland/"
    "XVEditor/XV-2020Editor.exe"
)
DEFAULT_PATCH_LIST = "/home/lentferj/Dokumente/SYNTHS/XV2020/xv2020patch.pdf"
DEFAULT_MANUAL = (
    "/home/lentferj/Seafile/Bibliothek/Handbücher/"
    "Audio-Daws_and_Plugins/Synthesizer/XV-2020_OM.pdf"
)
DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "xv", "data", "catalog.json",
)


# --- the binary -------------------------------------------------------------


def _name_at(data: bytes, offset: int, length: int = 12) -> Optional[str]:
    chunk = data[offset:offset + length]
    if len(chunk) < length:
        return None
    if not all(32 <= b <= 126 for b in chunk):
        return None
    return chunk.decode("ascii").rstrip()


def find_preset_table(data: bytes) -> int:
    """Offset of the 512-record preset patch table.

    Located by structure, not by a hardcoded address: the table is the only
    run of at least 512 consecutive ``[12 printable][NUL][byte]`` records in
    the file. Searching for it means this keeps working against a different
    build of the editor, and fails loudly rather than silently reading
    whatever happens to sit at a stale offset.
    """
    def ok(off: int) -> bool:
        return data[off + 12] == 0 and _name_at(data, off) is not None

    best: Optional[int] = None
    best_count = 0
    index = 0
    limit = len(data) - PRESET_RECORD
    while index < limit:
        if not ok(index):
            index += 1
            continue
        start = index
        count = 0
        while index < limit and ok(index):
            count += 1
            index += PRESET_RECORD
        if count > best_count:
            best, best_count = start, count
    if best is None or best_count < PRESET_COUNT:
        raise SystemExit(
            f"error: no {PRESET_COUNT}-record preset patch table in the "
            f"editor binary (longest run found: {best_count}). This tool "
            f"knows the 2006 XV-2020 Editor build; a different one may lay "
            f"its tables out differently."
        )
    return best


def find_gm_table(data: bytes, msb: int, expect: int) -> int:
    """Offset of a GM table, identified by its records' own MSB byte."""
    def ok(off: int) -> bool:
        return (
            data[off] == msb
            and data[off + 15] == 0
            and _name_at(data, off + 3) is not None
        )

    index = 0
    limit = len(data) - GM_RECORD
    while index < limit:
        if not ok(index):
            index += 1
            continue
        start = index
        count = 0
        while index < limit and ok(index):
            count += 1
            index += GM_RECORD
        if count >= expect:
            return start
    raise SystemExit(
        f"error: no run of {expect} GM records with MSB {msb} in the editor "
        f"binary."
    )


def read_preset_banks(data: bytes) -> Dict[str, List[dict]]:
    """PST-A through PST-D, 128 entries each, with category tags."""
    base = find_preset_table(data)
    out: Dict[str, List[dict]] = {}
    for bank_index, bank_id in enumerate(PRESET_BANKS):
        rows = []
        for number in range(1, 129):
            offset = base + ((bank_index * 128) + number - 1) * PRESET_RECORD
            name = _name_at(data, offset)
            if name is None:
                raise SystemExit(
                    f"error: {bank_id} record {number} is not a valid name"
                )
            category_byte = data[offset + 13]
            row: Dict[str, object] = {"n": number, "name": name}
            if 1 <= category_byte <= len(CATEGORIES):
                row["category"] = CATEGORIES[category_byte - 1]
            rows.append(row)
        out[bank_id] = rows
    return out


#: One record in the editor's GM table carries the wrong Bank Select LSB.
#:
#: The table is in program order with a program's variations grouped after
#: it, and the LSB byte says which variation each record is. For one program
#: two consecutive records claim the *same* LSB, which is impossible -- two
#: sounds cannot answer the same Bank Select triple. The XV-2020 Owner's
#: Manual settles it: its GM patch list gives that program three variations,
#: LSB 0, 1 and 2, so the second of the pair is LSB 2 and the editor's byte
#: is simply wrong.
#:
#: Keyed on (claimed LSB, program change) -> the LSBs the successive records
#: at that key really have, in table order. Keyed on position rather than on
#: the name so that nothing here is a copy of Roland's list, and so that a
#: build that fixes the byte produces no second record and never reaches the
#: correction at all.
GM_LSB_FIXES: Dict[Tuple[int, int], Tuple[int, ...]] = {
    (1, 118): (1, 2),
}


def read_gm_patches(data: bytes) -> Dict[str, List[dict]]:
    """The 256 GM2 patches, filed under the bank their own LSB names.

    Each record states its LSB, so the split into GM (variation 0) and
    GM-1..GM-9 is read off the data rather than assumed -- except where the
    editor states it wrongly; see :data:`GM_LSB_FIXES`.

    A GM2 sound is *reached* by its Bank Select triple, so two sounds landing
    on one triple is not a cosmetic duplicate: one of them becomes
    unreachable, and in the catalog one name silently overwrites the other.
    That is a hard error here rather than something to notice later on
    screen.
    """
    base = find_gm_table(data, GM_PATCH_MSB, 256)
    out: Dict[str, List[dict]] = {}
    seen: Dict[Tuple[int, int], int] = {}
    for index in range(256):
        offset = base + index * GM_RECORD
        lsb = data[offset + 1]
        program = data[offset + 2]
        name = _name_at(data, offset + 3)
        if name is None:
            raise SystemExit(f"error: GM record {index} is not a valid name")

        repeat = seen.get((lsb, program), 0)
        seen[(lsb, program)] = repeat + 1
        corrected = GM_LSB_FIXES.get((lsb, program))
        if corrected is not None and repeat < len(corrected):
            lsb = corrected[repeat]
        elif repeat:
            raise SystemExit(
                f"error: GM record {index} is the {repeat + 1}th to claim "
                f"Bank Select MSB {GM_PATCH_MSB} LSB {lsb} PC {program}. "
                f"Two patches cannot share one Bank Select triple, so the "
                f"editor's LSB byte is wrong here and one name would be lost."
                f" Check the LSB column of the Owner's Manual GM patch list "
                f"for PC {program + 1} and add the correction to "
                f"GM_LSB_FIXES."
            )

        bank_id = f"GM-{lsb}" if lsb else "GM"
        # The catalog is keyed on the number the display shows, which is the
        # program change plus one -- see xv/banks.py on why the two are never
        # conflated.
        out.setdefault(bank_id, []).append({"n": program + 1, "name": name})
    for rows in out.values():
        rows.sort(key=lambda row: row["n"])
    return out


def read_gm_rhythm(data: bytes) -> Dict[str, List[dict]]:
    """The nine GM2 drum kits, at the program changes the records state."""
    base = find_gm_table(data, GM_RHYTHM_MSB, 9)
    rows = []
    for index in range(9):
        offset = base + index * GM_RECORD
        program = data[offset + 2]
        name = _name_at(data, offset + 3)
        if name is None:
            raise SystemExit(f"error: GM rhythm record {index} is invalid")
        # Filed by position in the bank (1-9), because that is how
        # xv.banks.slots() numbers this bank -- its program changes are not
        # contiguous, so the display number and the PC are different things.
        rows.append({"n": index + 1, "name": name, "pc": program + 1})
    rows.sort(key=lambda row: row["pc"])
    for position, row in enumerate(rows, start=1):
        row["n"] = position
        del row["pc"]
    return {"R-GM": rows}


# --- expansion-board patch lists --------------------------------------------

#: The XV-2020 stores a patch name in twelve bytes and cannot display more
#: (OM p. 149). So a "name" longer than that is not a name -- it is a parse
#: that ran past the end of its column and swallowed the next one. Checking
#: it turns a whole class of silent column-alignment bugs into a miss, which
#: the completeness check then reports.
NAME_WIDTH = 12


#: Table furniture that a number can sit next to on a page break, and which
#: would otherwise be taken for a patch name -- and worse, occupy that
#: number, pushing every later entry one place along. SRX-06's listing did
#: exactly that: its page-two header became patch 271, so 271 onwards all
#: named the patch before them. Caught only because that board has a second
#: source to disagree with.
_NOT_NAMES = frozenset({
    "no. name", "no.", "name", "voices", "category", "patch", "rhythm",
    "patch list", "patch listing", "kit", "no. patch name",
})


def _ok(name: str) -> bool:
    return (0 < len(name) <= NAME_WIDTH
            and name.strip().lower() not in _NOT_NAMES)


#: One row of an SRX patch list: number, name, voices, CATEGORY.
#:
#: The number-to-name separator is ``\s+`` rather than ``\s{2,}`` because
#: the columns are packed differently per board -- SRX-05 leaves a single
#: space there in its second and third columns. Name-to-voices stays at two
#: or more: relaxing that as well makes "Acid Bass 2   1   SYNTH BASS"
#: ambiguous, and it would read the 2 as the voice count.
#: Map the full category names the expansion lists print back to the codes
#: the XV-2020 itself uses, so one filter covers internal and SRX patches.
_CATEGORY_CODES = {name: code for code, name in cat.CATEGORIES}

#: The categories as a regex alternation, longest first so that "SYNTH BASS"
#: is preferred over the "BASS" inside it.
_CATEGORY_ALTERNATION = "|".join(
    re.escape(name) for name in sorted(_CATEGORY_CODES, key=len, reverse=True)
)

#: The real category names are built into the pattern rather than checked
#: after matching, and that distinction is the whole difference between this
#: working and not.
#:
#: Validating afterwards cannot work: ``finditer`` consumes the text a match
#: covers, so rejecting a bad match does not make the engine try a better one
#: -- it moves past. SRX-07 row 40 reads "Clav 1 SRX   2   KEYBOARDS", and
#: the shortest parse is name "Clav", voices 1, category "SRX". Rejecting
#: that lost the row entirely. With the categories in the pattern, "SRX" is
#: simply not a category, so the engine backtracks to name "Clav 1 SRX",
#: voices 2, category "KEYBOARDS" on its own.
#: Phrase-loop patches carry a recommended tempo in its own column between
#: the name and the voice count -- "453  Pursuit 90   (90)   2  BEAT&GROOVE"
#: -- which the manual's own footnote explains. Optional, because only the
#: BEAT&GROOVE patches have one.
_SRX_ROW = re.compile(
    r"(?<![\d(])(\d{1,3})\s+((?:\S| (?! ))+?)\s+(?:\(\d+\)\s+)?"
    r"(\d+(?: ?\(\d+\))?)\s+"
    r"(" + _CATEGORY_ALTERNATION + r")(?=\s|$)"
)

#: The same row where the board packs the name hard against the voice count.
#: Only ever tried for a number the strict pattern missed, and anchored on
#: that exact number, so the ambiguity it would otherwise introduce cannot
#: bite.
#: A heading that introduces a patch list for a particular host family.
#: Both spellings appear: the owner's manuals write "For Fantom series/XV
#: series/...", the Faxback sheets write "XV-Series Patch List:".
#: Case-sensitive, and "For" must be followed by a capital. It used to be
#: case-insensitive, which made every line of prose beginning "for" a
#: heading -- and SRX-04's footnote, "for some of the patches. As a result,
#: if your sound generator...", duly ended its patch list halfway, at 64 of
#: 128. The board's Faxback sheet had already given a verified 128, which is
#: the only reason the truncation was noticed rather than believed.
_LIST_HEADING = re.compile(r"^\s*(?:For\s+[A-Z]|.*Patch List\s*:)")

#: What marks a heading as introducing *our* list. Case-sensitive, and
#: nothing but "XV".
#:
#: It used to also accept Fantom, JUNO-G and MX-200, on the reasoning that
#: the XV heading names those alongside it. That is true of most boards and
#: false where it matters: SRX-12's manual prints "For Fantom-X series/
#: Fantom-S series/JUNO-G" **first** and "For Fantom (FA-76)/XV series/
#: MX-200" second, so the loose pattern matched the Fantom-X list and would
#: have read the wrong table for a board whose XV list is not the first one.
_OURS = re.compile(r"XV")


def _xv_section(text: str) -> str:
    """Just the patch list meant for the XV series.

    These documents carry **more than one** patch list -- one for the
    Fantom/XV/JUNO-G family and others for the RD series, MC-909 and G-70 --
    and they are not the same patches. SRX-05 number 298 is "OldSkool FX" in
    the first and "Noise Cycle" in the second, and SRX-04's sheet prints an
    "RD-700 Patch List" straight after the XV one.

    Taking whichever list came first happens to be right for every document
    here, and is not a property worth relying on: nothing says Roland always
    printed the XV list first, and a sheet that did not would be read
    silently and wrongly.

    **This cannot help when the lists are printed side by side**, as SRX-11's
    manual prints them -- both headings land on one line and both tables
    share every line after it, so no line-based split separates them. There
    the only thing keeping the XV reading is that its column is the
    left-hand one and the row parser reads left to right. For SRX-11 the two
    lists are identical anyway, so nothing turns on it; for a board where
    they differ and are set side by side, this would need columns, not lines.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if _LIST_HEADING.match(line) and _OURS.search(line):
            start = index
            break
    if start is None:
        return text
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if _LIST_HEADING.match(line) and not _OURS.search(line):
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


#: One row of a names-only "Patch Listing": a number, optionally a full
#: stop, then the name. Same no-double-space rule as the manual parser --
#: these listings set several tables side by side, and without it a name
#: would run into the next column.
#: The number must start a column -- line start, or after the two-space gap
#: that separates columns. Allowing it anywhere lets a digit *inside* a name
#: begin a row: "106.   12 String" was read as number 12, name "String", and
#: five SRX-09 names lost their leading number that way. Found only when that
#: board's manual turned up to disagree; SRX-01 and SRX-04 were exposed to
#: the same thing with nothing to catch it.
_SRX_LIST_ROW = re.compile(
    r"(?:^|(?<=\s\s))\s*(\d{1,3})\.?\s+((?:\S| (?! ))+?)(?=\s{2,}|$)")


def read_srx_list(card: str, path: str) -> Dict[str, List[dict]]:
    """One board's patches from a names-only listing. No categories.

    The Faxback-style "Patch Listing" sheets carry names and nothing else --
    no voice count, no category, no Bank Select. They are the only source for
    boards whose owner's manual is not to hand.

    **First occurrence of a number wins**, which is what makes SRX-01 work:
    its sheet prints the patch table and the rhythm table side by side, both
    numbered from 1, and the patch table is the left-hand one. Rhythm numbers
    beyond the board's patch count fall outside the range check.

    **Prefer the owner's manual wherever there is one.** These sheets are
    less reliable than they look, and the failure is not detectable from the
    sheet alone: SRX-06's numbers a page-break artefact as patch 271, so the
    179 names after it each land on the patch before them, and the sheet
    still parses as a complete 1..449. That was caught only because SRX-06
    also has a manual to disagree with. A board read from a listing has names
    that are probably right, no categories, and nothing to check them
    against -- the tool says so at the end of a run.
    """
    definition = banks.srx_card(card)
    total = definition.patch_count
    text = _xv_section(_fix_pdf_text(pdf_text(path)))

    found: Dict[int, str] = {}
    for line in text.splitlines():
        for match in _SRX_LIST_ROW.finditer(line):
            number = int(match.group(1))
            name = match.group(2).strip()
            if 1 <= number <= total and number not in found and _ok(name):
                found[number] = name

    missing = [n for n in range(1, total + 1) if n not in found]
    if missing:
        print(f"warning: {card}: {len(missing)} of {total} patches were not "
              f"parsed ({missing[:8]}...). Those slots stay unnamed.",
              file=sys.stderr)

    out: Dict[str, List[dict]] = {}
    for number, name in sorted(found.items()):
        page, slot = divmod(number - 1, 128)
        out.setdefault(f"{card}-{page + 1}", []).append(
            {"n": slot + 1, "name": name})
    for rows in out.values():
        rows.sort(key=lambda r: r["n"])
    return out


#: The Bank Select line that opens a board's Rhythm Set List. Patches are
#: MSB 93, rhythm sets MSB 92, so this is what tells the two tables apart in
#: a manual that prints both.
_RHYTHM_BANK_LINE = re.compile(r"BANK SELECT\s+MSB\s*:\s*92", re.IGNORECASE)

#: What follows a Rhythm Set List and must not be read as part of it. The
#: key-assign charts are the dangerous one: they are full of small numbers
#: beside short names and would happily supply 79 plausible "kits".
_AFTER_RHYTHM = re.compile(
    r"Key Assign|Wave List|Patch List|^\s*For\s+[A-Z]", re.IGNORECASE)


def read_srx_rhythm(card: str, path: str) -> Dict[str, List[dict]]:
    """One board's rhythm-set names, from the Rhythm Set List in its manual.

    Same row shape as a names-only patch listing -- a number and a name, no
    voice count, no category -- but found by its own Bank Select line: rhythm
    sets are MSB 92 where patches are 93, which is what separates the two
    tables in a manual that prints both.

    The section has to be bounded at the far end as well. What follows a
    Rhythm Set List is the Rhythm Set Key Assign chart, which is nothing but
    small numbers beside short names and would supply as many convincing
    "kits" as the count demanded.
    """
    definition = banks.srx_card(card)
    total = definition.rhythm_count
    if not total or definition.rhythm_lsb is None:
        return {}

    lines = _fix_pdf_text(pdf_text(path)).splitlines()
    start = next((i for i, line in enumerate(lines)
                  if _RHYTHM_BANK_LINE.search(line)), None)
    if start is None:
        print(f"warning: {card}: no Rhythm Set List found in "
              f"{os.path.basename(path)}; its {total} kits stay unnamed.",
              file=sys.stderr)
        return {}
    end = next((i for i in range(start + 1, len(lines))
                if _AFTER_RHYTHM.search(lines[i])), len(lines))

    found: Dict[int, str] = {}
    for line in lines[start:end]:
        for match in _SRX_LIST_ROW.finditer(line):
            number = int(match.group(1))
            name = match.group(2).strip()
            if 1 <= number <= total and number not in found and _ok(name):
                found[number] = name

    missing = [n for n in range(1, total + 1) if n not in found]
    if missing:
        print(f"warning: {card}: {len(missing)} of {total} rhythm sets were "
              f"not parsed ({missing[:8]}...).", file=sys.stderr)
    if not found:
        return {}
    return {f"{card}-R": [{"n": n, "name": found[n]} for n in sorted(found)]}


def read_srx_names(card: str, path: str) -> Dict[str, List[dict]]:
    """One board's patches from a hand-written table.

    For a board with no document to parse -- only a screen capture, or a page
    somebody typed up. Lines are ``number, name, category`` separated by tabs
    or two or more spaces; the category is optional and given as the full
    name the lists print ("AC.BRASS"), not the three-letter code.

    Worth being clear about what this is: everything else in the catalog is
    read out of a file Roland shipped, and most of it is cross-checked
    against a second one. A board added this way has been through a human
    eye, which is the least reliable step in the whole pipeline. The count
    still has to come to the board's documented patch total -- that much is
    checked -- but a wrong *name* on the right number will pass silently.
    """
    definition = banks.srx_card(card)
    total = definition.patch_count
    found: Dict[int, Tuple[str, Optional[str]]] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            fields = [f.strip() for f in re.split(r"\t|\s{2,}", line.strip())]
            if len(fields) < 2:
                raise SystemExit(
                    f"error: {path}:{lineno}: expected 'number<tab>name"
                    f"[<tab>CATEGORY]', got {line.strip()!r}")
            try:
                number = int(fields[0])
            except ValueError:
                raise SystemExit(
                    f"error: {path}:{lineno}: {fields[0]!r} is not a number"
                ) from None
            if not 1 <= number <= total:
                raise SystemExit(
                    f"error: {path}:{lineno}: {card} has {total} patches, so "
                    f"{number} is out of range")
            if number in found:
                raise SystemExit(
                    f"error: {path}:{lineno}: patch {number} appears twice")
            name = fields[1]
            if not _ok(name):
                raise SystemExit(
                    f"error: {path}:{lineno}: {name!r} is not a usable name "
                    f"(the device stores {NAME_WIDTH} characters)")
            category = fields[2] if len(fields) > 2 else None
            if category and category not in _CATEGORY_CODES:
                raise SystemExit(
                    f"error: {path}:{lineno}: {category!r} is not an XV-2020 "
                    f"category. Use a full name such as 'AC.BRASS'.")
            found[number] = (name, category)

    missing = [n for n in range(1, total + 1) if n not in found]
    if missing:
        print(f"warning: {card}: {len(missing)} of {total} patches are absent "
              f"from {os.path.basename(path)} ({missing[:8]}...). Those slots "
              f"stay unnamed.", file=sys.stderr)

    out: Dict[str, List[dict]] = {}
    for number, (name, category) in sorted(found.items()):
        page, slot = divmod(number - 1, 128)
        row: Dict[str, object] = {"n": slot + 1, "name": name}
        if category:
            row["category"] = _CATEGORY_CODES[category]
        out.setdefault(f"{card}-{page + 1}", []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r["n"])
    return out


def read_srx(card: str, path: str) -> Dict[str, List[dict]]:
    """One expansion board's patches, split across its Bank Select LSBs.

    A board's manual numbers its patches 1..N straight through, but selecting
    one needs the LSB page it falls on -- SRX-07's patch 300 is LSB 13, slot
    44. :mod:`xv.banks` holds that split, and the patch count it states is
    also the check that this parse is complete.
    """
    definition = banks.srx_card(card)
    total = definition.patch_count
    whole = _fix_pdf_text(pdf_text(path))

    def parse(text: str) -> Dict[int, Tuple[str, str]]:
        out: Dict[int, Tuple[str, str]] = {}
        for line in text.splitlines():
            for match in _SRX_ROW.finditer(line):
                number = int(match.group(1))
                name = match.group(2).strip()
                category = match.group(4).strip()
                if not (1 <= number <= total) or number in out:
                    continue
                if _ok(name) and category in _CATEGORY_CODES:
                    out[number] = (name, category)
        return out

    # Scope to the XV list first: SRX-12 prints another host's list ahead of
    # its own, and reading that one would be silently wrong.
    #
    # But some manuals label no host at all. SRX-03 sets its XV and RD tables
    # side by side under a bare "Patch List (1)", so the only heading naming
    # XV is the *rhythm* section further down, and scoping to that finds no
    # patches whatever. An empty result means the scoping missed, not that
    # the board is empty, so fall back to the whole document -- where the XV
    # column, being the left-hand one, still wins on first occurrence.
    #
    # Only on *empty*, deliberately. "Whichever yields more" would take
    # SRX-12's 105-patch Fantom-X list over its 50-patch XV one.
    found = parse(_xv_section(whole))
    if not found:
        found = parse(whole)

    missing = [n for n in range(1, total + 1) if n not in found]
    if missing:
        # Two very different causes, and the shape tells them apart. A
        # scattering of gaps is a parse that missed rows. A clean tail --
        # everything from some point to the end -- is usually the document:
        # a board can offer fewer patches to the XV series than to a Fantom,
        # and its XV list then simply stops early. SRX-12 does exactly that,
        # listing 105 for Fantom-X and 50 for XV.
        tail = missing == list(range(missing[0], total + 1))
        if tail:
            print(f"note: {card}: the XV list stops at {missing[0] - 1}, "
                  f"though the board has {total} patches. That is usually "
                  f"the document, not the parse -- a board can expose fewer "
                  f"patches to the XV series than to other hosts. The "
                  f"remaining slots stay unnamed.", file=sys.stderr)
        else:
            print(f"warning: {card}: {len(missing)} of {total} patches were "
                  f"not parsed ({missing[:8]}...). Those slots stay unnamed; "
                  f"the rest are unaffected.", file=sys.stderr)

    out: Dict[str, List[dict]] = {}
    for number, (name, category) in sorted(found.items()):
        page, slot = divmod(number - 1, 128)
        bank_id = f"{card}-{page + 1}"
        row: Dict[str, object] = {"n": slot + 1, "name": name}
        code = _CATEGORY_CODES.get(category)
        if code:
            row["category"] = code
        elif category:
            print(f"note: {card}: no code for category {category!r}",
                  file=sys.stderr)
        out.setdefault(bank_id, []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r["n"])
    return out


# --- the PDFs ---------------------------------------------------------------


def pdf_text(path: str) -> str:
    """Extract a PDF's text layer with ``pdftotext -layout``."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            check=True, capture_output=True,
        )
    except FileNotFoundError:
        raise SystemExit(
            "error: pdftotext is not installed; it is in poppler-utils."
        ) from None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"error: pdftotext failed on {path}: {exc}") from None
    return result.stdout.decode("utf-8", errors="replace")


#: The patch-listing PDF was distilled from Word in 2003 and its text layer
#: renders the typographic apostrophe as a capital O-tilde. Left alone this
#: puts "Saw nO 202" in the catalog. Only needed for that document; the
#: editor binary has the real byte.
_PDF_FIXUPS = {"Õ": "'", "’": "'", "“": '"', "”": '"'}


def _fix_pdf_text(text: str) -> str:
    for wrong, right in _PDF_FIXUPS.items():
        text = text.replace(wrong, right)
    return text


# "1.    Name Here" but "100. Name Here" -- the column is padded to a fixed
# width, so a three-digit number leaves a single space. Requiring two
# silently dropped every entry from 100 up, a quarter of the bank.
# re.MULTILINE is load-bearing: without it "$" is end-of-*string*, so the
# lookahead only terminated a name at a run of spaces. Every entry that ends
# its line -- the whole rightmost column, 31 of 128 -- fell out silently.
_ENTRY = re.compile(r"(\d{1,3})\.\s+(\S.*?)(?=\s{2,}|$)", re.MULTILINE)


def read_user_bank(path: str) -> Dict[str, List[dict]]:
    """The USER bank's factory contents, from the patch-listing PDF.

    Worth remembering what this actually is: what shipped in USER, not what
    is in it now. rxved reads the live USER names from the device and shows
    those in preference -- see xv/catalog.py. This is the fallback for when
    the hardware is not connected.
    """
    text = _fix_pdf_text(pdf_text(path))
    section = text.split("USER:", 1)
    if len(section) < 2:
        raise SystemExit(f"error: no 'USER:' section in {path}")
    # Stop at the next bank heading; the document runs USER, PR-A .. PR-D.
    body = re.split(r"\bPR-A:", section[1], 1)[0]
    found: Dict[int, str] = {}
    for match in _ENTRY.finditer(body):
        number = int(match.group(1))
        name = match.group(2).strip()
        if 1 <= number <= 128 and number not in found:
            found[number] = name
    missing = [n for n in range(1, 129) if n not in found]
    if missing:
        print(
            f"warning: USER bank incomplete in {os.path.basename(path)} -- "
            f"{len(missing)} of 128 missing ({missing[:6]}...); leaving the "
            f"bank out rather than shipping a partial one",
            file=sys.stderr,
        )
        return {}
    return {"USER": [{"n": n, "name": found[n]} for n in range(1, 129)]}


def read_performances(path: str) -> Dict[str, List[dict]]:
    """The three performance banks, from the owner's manual.

    The manual prints USER and Preset-A with identical contents (the machine
    ships with USER holding a copy of Preset-A) and Preset-B beside them, in
    a four-column layout that ``pdftotext -layout`` preserves well enough to
    read by column position.
    """
    text = _fix_pdf_text(pdf_text(path))
    lines = text.splitlines()
    # Anchored on the table's own column header rather than on the phrase
    # "Performance List", whose first occurrence in the document is the entry
    # in the table of contents -- 9000 lines before the table itself.
    header = re.compile(r"\s*USER\s+Preset-A\s+Preset-B\s*$")
    start = None
    for index, line in enumerate(lines):
        if header.match(line):
            start = index
            break
    if start is None:
        print("warning: no Performance List in the manual; skipping",
              file=sys.stderr)
        return {}
    body = lines[start:start + 220]
    # Rows look like:  001   Name        033   Name       001   Name   001  Name
    # i.e. up to four (number, name) pairs per line. Columns one and two are
    # the USER bank's two halves, three is Preset-A, four is Preset-B.
    pair = re.compile(r"(\d{3})\s{2,}(\S[^\s](?:[^\s]|\s(?!\s))*)")
    columns: Dict[int, Dict[int, str]] = {0: {}, 1: {}, 2: {}, 3: {}}
    for line in body:
        matches = list(pair.finditer(line))
        if len(matches) < 2:
            continue
        for column, match in enumerate(matches[:4]):
            number = int(match.group(1))
            name = match.group(2).strip()
            columns[column].setdefault(number, name)
    user = dict(columns[0])
    user.update(columns[1])
    preset_a = columns[2]
    preset_b = columns[3]
    out: Dict[str, List[dict]] = {}
    for bank_id, rows, expect in (
        ("P-USER", user, 64), ("P-PST-A", preset_a, 32),
        ("P-PST-B", preset_b, 32),
    ):
        have = [n for n in range(1, expect + 1) if n in rows]
        if len(have) != expect:
            print(
                f"warning: {bank_id} incomplete ({len(have)}/{expect}); "
                f"leaving it out",
                file=sys.stderr,
            )
            continue
        out[bank_id] = [{"n": n, "name": rows[n]} for n in range(1, expect + 1)]
    return out


def read_rhythm_sets(path: str) -> Dict[str, List[dict]]:
    """The eight internal rhythm sets, from the manual's Rhythm Set List.

    The kit names are the column headings of that list -- the row beneath a
    line of ``001  002  003  004`` -- because the list itself is a matrix of
    which sample sits on which key, one column per kit.

    Not taken from the editor binary, although the names are in there: the
    linker pools identical string constants, and three of the eight kit names
    are also performance names, so the rhythm table's own entries are shared
    with the performance table's and the run is not contiguous. Reading order
    off a pooled table would be guesswork.
    """
    text = _fix_pdf_text(pdf_text(path))
    lines = text.splitlines()
    groups = {"User Group": "R-USER", "Preset A Group": "R-PST-A",
              "Preset B Group": "R-PST-B"}
    out: Dict[str, List[dict]] = {}
    for index, line in enumerate(lines):
        label = line.strip()
        if label not in groups:
            continue
        # The heading is followed by the number row, then the name row.
        for lookahead in range(index + 1, min(index + 5, len(lines))):
            if not re.match(r"\s*001\s+002\s+003\s+004\s*$",
                            lines[lookahead]):
                continue
            names_line = lines[lookahead + 1]
            names = re.sub(r"^\s*Note No\.\s*", "", names_line)
            # Kit names are separated by runs of two or more spaces.
            parts = [p.strip() for p in re.split(r"\s{2,}", names.strip())
                     if p.strip()]
            if len(parts) == 4:
                out[groups[label]] = [
                    {"n": n, "name": name}
                    for n, name in enumerate(parts, start=1)
                ]
            break
    for bank_id in groups.values():
        if bank_id not in out:
            print(f"warning: {bank_id} not found in the Rhythm Set List",
                  file=sys.stderr)
    return out


# --- cross-checking ---------------------------------------------------------


def crosscheck_presets(from_binary: Dict[str, List[dict]],
                       manual_path: Optional[str]) -> None:
    """Report where the manual's printed preset names differ from the binary.

    Differences are expected and mostly cosmetic -- the PDF text layer
    mangles apostrophes -- but a difference that is *not* cosmetic would mean
    one of the two sources has been misread, and that is worth seeing rather
    than averaging away.
    """
    if manual_path is None:
        return
    # Whitespace-collapsed substring search. The printed lists are laid out
    # in columns, so a name can be followed by any amount of padding, and
    # tokenising the text instead would never match the majority of names --
    # most of them contain a space.
    haystack = re.sub(r"\s+", " ", _fix_pdf_text(pdf_text(manual_path)))
    mismatched = []
    for bank_id, rows in from_binary.items():
        for row in rows:
            needle = re.sub(r"\s+", " ", row["name"])
            if needle not in haystack:
                mismatched.append(f"{bank_id} {row['n']:03d} {row['name']!r}")
    if mismatched:
        print(
            f"note: {len(mismatched)} of "
            f"{sum(len(r) for r in from_binary.values())} names from the "
            f"binary were not found anywhere in the manual's text layer. "
            f"That is usually the PDF's fault, not the binary's. First few: "
            f"{', '.join(mismatched[:5])}",
            file=sys.stderr,
        )


# --- entry point ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract_catalog.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--editor", default=DEFAULT_EDITOR,
                        help="path to XV-2020Editor.exe")
    parser.add_argument("--patch-list", default=DEFAULT_PATCH_LIST,
                        help="path to the XV-2020 patch listing PDF")
    parser.add_argument("--manual", default=DEFAULT_MANUAL,
                        help="path to the XV-2020 owner's manual PDF")
    parser.add_argument(
        "--srx", action="append", default=[], metavar="CARD=PDF",
        help="an expansion board's owner's manual, e.g. "
             "--srx SRX-07=SRX-07_OM.pdf (repeatable)")
    parser.add_argument(
        "--srx-list", action="append", default=[], metavar="CARD=PDF",
        help="a names-only Patch Listing for a board whose owner's manual "
             "you do not have. No categories come from these.")
    parser.add_argument(
        "--srx-names", action="append", default=[], metavar="CARD=FILE",
        help="a hand-written 'number<tab>name<tab>CATEGORY' table, for a "
             "board with no document to parse")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--no-crosscheck", action="store_true")
    return parser


def check_no_duplicates(banks_out: Dict[str, List[dict]]) -> None:
    """Refuse to write a catalog in which one slot is named twice.

    xv.catalog keys its entries on bank and number, so a repeated slot does
    not show up as a duplicate -- the later name simply replaces the earlier
    one and the earlier sound becomes unnamed. Nothing downstream can notice
    that, which is why it is checked at the only point that can: here, where
    the readers' output is still a list.
    """
    for bank_id, rows in sorted(banks_out.items()):
        seen: Dict[int, int] = {}
        for position, row in enumerate(rows):
            number = row["n"]
            if number in seen:
                raise SystemExit(
                    f"error: {bank_id} names slot {number:03d} twice, at "
                    f"rows {seen[number]} and {position}. One of the two "
                    f"names would be lost. Fix the reader that produced "
                    f"this bank rather than dropping a row."
                )
            seen[number] = position


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    banks_out: Dict[str, List[dict]] = {}
    sources: List[str] = []

    if not os.path.exists(args.editor):
        raise SystemExit(
            f"error: no editor binary at {args.editor}. It is the only source "
            f"for the preset and GM banks; pass --editor with your copy."
        )
    with open(args.editor, "rb") as handle:
        data = handle.read()
    banks_out.update(read_preset_banks(data))
    banks_out.update(read_gm_patches(data))
    banks_out.update(read_gm_rhythm(data))
    sources.append(f"Roland XV-2020 Editor ({os.path.basename(args.editor)})")

    if os.path.exists(args.patch_list):
        banks_out.update(read_user_bank(args.patch_list))
        sources.append(f"XV-2020 Patch Listing "
                       f"({os.path.basename(args.patch_list)})")
    else:
        print(f"note: no patch listing at {args.patch_list}; the USER bank's "
              f"factory names will be missing (rxved reads USER from the "
              f"device anyway)", file=sys.stderr)

    if os.path.exists(args.manual):
        banks_out.update(read_performances(args.manual))
        banks_out.update(read_rhythm_sets(args.manual))
        sources.append(f"XV-2020 Owner's Manual "
                       f"({os.path.basename(args.manual)})")
        if not args.no_crosscheck:
            crosscheck_presets(
                {k: v for k, v in banks_out.items() if k.startswith("PST-")},
                args.manual,
            )
    else:
        print(f"note: no manual at {args.manual}; performance and rhythm-set "
              f"names will be missing", file=sys.stderr)

    for item in args.srx:
        card, _, path = item.partition("=")
        if not path:
            raise SystemExit(f"error: --srx wants CARD=PDF, got {item!r}")
        banks.srx_card(card)          # fail now on an unknown board
        if not os.path.exists(path):
            raise SystemExit(f"error: no such file: {path}")
        banks_out.update(read_srx(card, path))
        # The same manual carries the board's Rhythm Set List when it has
        # one, so there is nothing extra for the caller to pass.
        banks_out.update(read_srx_rhythm(card, path))
        sources.append(f"{card} Owner's Manual ({os.path.basename(path)})")

    for item in args.srx_list:
        card, _, path = item.partition("=")
        if not path:
            raise SystemExit(f"error: --srx-list wants CARD=PDF, got {item!r}")
        banks.srx_card(card)
        if not os.path.exists(path):
            raise SystemExit(f"error: no such file: {path}")
        rows = read_srx_list(card, path)
        # A manual, if one was also given, wins: it carries categories.
        for bank_id, entries in rows.items():
            banks_out.setdefault(bank_id, entries)
        sources.append(f"{card} Patch Listing ({os.path.basename(path)})")

    for item in args.srx_names:
        card, _, path = item.partition("=")
        if not path:
            raise SystemExit(f"error: --srx-names wants CARD=FILE, got "
                             f"{item!r}")
        banks.srx_card(card)
        if not os.path.exists(path):
            raise SystemExit(f"error: no such file: {path}")
        for bank_id, entries in read_srx_names(card, path).items():
            banks_out.setdefault(bank_id, entries)
        sources.append(f"{card} ({os.path.basename(path)}, transcribed)")

    check_no_duplicates(banks_out)

    payload = {
        "source": "; ".join(sources),
        "generated": datetime.date.today().isoformat(),
        "note": (
            "Generated locally by tools/extract_catalog.py from Roland's own "
            "files. Not distributed with rxved."
        ),
        "banks": banks_out,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False)
        handle.write("\n")

    total = sum(len(rows) for rows in banks_out.values())
    print(f"wrote {args.output}: {total} names across {len(banks_out)} banks")
    if args.srx_list:
        from_listing = sorted(
            item.partition("=")[0] for item in args.srx_list
            if item.partition("=")[0] not in
            {i.partition("=")[0] for i in args.srx}
        )
        if from_listing:
            print(f"\nnote: {', '.join(from_listing)} came from a names-only "
                  f"Patch Listing. Those boards have no categories, and "
                  f"nothing cross-checks their numbering -- see read_srx_list. "
                  f"Use the owner's manual instead if you can get it.")
    for bank_id in sorted(banks_out):
        print(f"  {bank_id:<8} {len(banks_out[bank_id]):>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
