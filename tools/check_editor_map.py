#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
#
# Reads Roland's XV-2020 Editor script file (XV-2020EditorScript.xml), which
# is the editor's own machine-readable parameter map.
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

"""Cross-check rxved's byte offsets against Roland's own editor script.

Every offset in :mod:`xv.bridge` was transcribed by hand from a PDF whose
parameter map is set in two columns, and reading that PDF one line at a time
has already produced one confidently wrong claim (RESOLUTION_NOTES §12b).
This is the independent second source that would have caught it on the spot.

The editor ships ``Script/XV-2020EditorScript.xml``: 50 ``<structType>``
definitions giving, for every parameter, its offset within its block, its
size, its range and its default; plus ``<struct>`` instances binding those
types to base addresses. It is the same map as the manual's, written by the
people who wrote the firmware, in a form that cannot be mis-columned.

**It is a cross-check, not a source.** Where the two disagree the answer is
to go and read the manual's own block carefully, not to believe whichever
was consulted last -- and disagreements are reported, never applied.

Usage::

    python3 tools/check_editor_map.py
    python3 tools/check_editor_map.py --script /path/to/XV-2020EditorScript.xml
    python3 tools/check_editor_map.py --dump PerformancePart
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xv import bridge as b  # noqa: E402

DEFAULT_SCRIPT = (
    "/home/lentferj/.wine64_roland/drive_c/Program Files (x86)/Roland/"
    "XVEditor/Script/XV-2020EditorScript.xml"
)


def load(path: str) -> ET.Element:
    """Parse the script. It declares Shift_JIS, which ElementTree refuses.

    Decoded here rather than handed to the parser as bytes, and the
    declaration rewritten to match what is actually being passed in --
    leaving it saying Shift_JIS while passing str is how you get a parser
    that is right until somebody's patch name has a multi-byte character in
    it.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    text = raw.decode("shift_jis", errors="replace")
    text = text.replace('encoding="Shift_JIS"', 'encoding="utf-8"', 1)
    return ET.fromstring(text)


def _hex_bytes(text: Optional[str]) -> Optional[List[int]]:
    if not text:
        return None
    try:
        return [int(part, 16) for part in text.split()]
    except ValueError:
        return None


def struct_types(root: ET.Element) -> Dict[str, dict]:
    """``{type: {"size": n, "values": {name: {...}}}}``.

    A ``<value>``'s ``<address>`` is its offset **within** the block, in the
    same hex notation the manual uses.
    """
    out: Dict[str, dict] = {}
    for node in root.findall(".//structType"):
        name = node.findtext("type")
        if not name:
            continue
        values: Dict[str, dict] = {}
        for value in node.findall("value"):
            vname = value.findtext("name")
            offset = _hex_bytes(value.findtext("address"))
            if not vname or offset is None:
                continue
            # Offsets are written as an address in the same hex notation
            # the manual uses -- "00 1B", two bytes. Within one block the
            # high byte is always zero, so the offset is the last byte; the
            # whole thing is kept when it is not, rather than silently
            # truncated.
            values[vname] = {
                "offset": offset[-1] if (len(offset) == 1 or
                                         not any(offset[:-1])) else offset,
                "size": _hex_bytes(value.findtext("size")),
                "range": value.findtext("range"),
                "default": value.findtext("default"),
                "type": value.findtext("type"),
            }
        out[name] = {"size": node.findtext("size"), "values": values}
    return out


def _size(text: Optional[str]) -> Optional[int]:
    """A block size, hex, in either notation the script uses.

    Written "31" in most blocks and "00 0F" in Setup, both hex: 0x31 is 49
    and 0x0F is 15, which are the manual's Total Sizes. Reading either as
    decimal gives a plausible number and a short read, and a short read of a
    block that is later written back is how a performance gets corrupted.
    """
    if not text:
        return None
    parts = str(text).split()
    try:
        if len(parts) == 1:
            return int(parts[0], 16)
        # Address-shaped: base 128, as everywhere else in this protocol.
        value = 0
        for part in parts:
            value = value * 128 + int(part, 16)
        return value
    except ValueError:
        return None


def _flat(values: Dict[str, dict]) -> Dict[int, str]:
    """``{offset: parameter name}``, for offsets that are a single byte."""
    out: Dict[int, str] = {}
    for name, spec in values.items():
        offset = spec["offset"]
        if isinstance(offset, int):
            out.setdefault(offset, name)
    return out


#: What rxved believes, and which editor struct to check it against. Only
#: the offsets rxved actually reads or writes -- this is a check on rxved,
#: not an inventory of the editor.
CHECKS: Tuple[Tuple[str, str, Dict[int, str]], ...] = (
    ("PerformancePart", "Performance Part (10 00 <20+n-1> 00)", {
        0x00: "receive channel",
        0x01: "receive switch",
        0x04: "bank select MSB",
        0x05: "bank select LSB",
        0x06: "program change",
        0x07: "part level",
        0x08: "part pan",
        0x09: "part coarse tune",
        0x0A: "part fine tune",
        0x0B: "part mono/poly",
        0x0D: "part pitch bend range",
        0x15: "part octave shift",
        0x16: "part velocity sensitivity",
        0x17: "keyboard range lower",
        0x18: "keyboard range upper",
        0x1B: "mute switch",
        0x1C: "dry send level",
        0x1D: "chorus send level",
        0x1E: "reverb send level",
        0x1F: "output assign",
        0x20: "output MFX select",
    }),
    ("PerformanceMIDI", "Performance MIDI (10 00 <10+ch> 00)", {
        0x00: "receive program change",
        0x01: "receive bank select",
        0x02: "receive bender",
        0x03: "receive poly pressure",
        0x04: "receive channel pressure",
        0x05: "receive modulation",
        0x06: "receive volume",
        0x07: "receive pan",
        0x08: "receive expression",
        0x09: "receive hold 1",
        0x0A: "phase lock",
        0x0B: "velocity curve type",
    }),
    ("PerformanceCommon", "Performance Common (10 00 00 00)", {
        0x0C: "solo part select",
    }),
    ("Setup", "Setup (01 00 00 00)", {
        0x00: "sound mode",
        0x04: "performance bank MSB",
        0x05: "performance bank LSB",
        0x06: "performance program",
        0x07: "patch bank MSB",
        0x08: "patch bank LSB",
        0x09: "patch program",
    }),
    ("SystemCommon", "System Common (02 00 00 00)", {
        0x09: "performance control channel",
        0x0B: "patch receive channel",
    }),
)


def check(root: ET.Element) -> int:
    """Compare and report. Returns the number of disagreements."""
    types = struct_types(root)
    problems = 0

    for type_name, label, ours in CHECKS:
        spec = types.get(type_name)
        print(f"\n{label}")
        print(f"  editor struct: {type_name}", end="")
        if spec is None:
            print("  -- NOT FOUND in the script")
            problems += 1
            continue
        theirs = _flat(spec["values"])
        print(f"  ({len(theirs)} parameters, size {spec['size']})")
        for offset in sorted(ours):
            mine = ours[offset]
            match = theirs.get(offset)
            if match is None:
                print(f"    {offset:#04x}  {mine:<28} "
                      f"-- editor has NOTHING at this offset")
                problems += 1
            else:
                print(f"    {offset:#04x}  {mine:<28} editor: {match}")

    # Ranges, which decide what a write is allowed to send. These were typed
    # in by hand from the manual's value column, and a range that is too
    # wide lets a refused value out onto the wire while one that is too
    # narrow refuses a setting the synth accepts.
    print("\nWrite ranges")
    types_by_offset = {
        "PerformancePart": (b.XvBridge.WRITABLE_PART_OFFSETS, "part"),
        "PerformanceMIDI": (b.XvBridge.WRITABLE_CHANNEL_OFFSETS, "channel"),
    }
    for type_name, (allowlist, label) in types_by_offset.items():
        spec = types.get(type_name)
        if spec is None:
            continue
        theirs = {}
        for name, value in spec["values"].items():
            offset = value["offset"]
            if isinstance(offset, int) and value["range"]:
                theirs.setdefault(offset, (name, value["range"]))
        for offset in sorted(allowlist):
            mine_label, low, high = allowlist[offset]
            found = theirs.get(offset)
            if found is None:
                print(f"  {label} {offset:#04x} {mine_label:<26} "
                      f"editor gives no range")
                continue
            name, raw = found
            try:
                their_low, their_high = (int(x) for x in raw.split(","))
            except ValueError:
                print(f"  {label} {offset:#04x} {mine_label:<26} "
                      f"editor range {raw!r} unparsed")
                continue
            if (low, high) == (their_low, their_high):
                print(f"  {label} {offset:#04x} {mine_label:<26} "
                      f"{low}-{high} == editor")
            else:
                print(f"  {label} {offset:#04x} {mine_label:<26} "
                      f"rxved {low}-{high} != editor {their_low}-"
                      f"{their_high}  <-- DISAGREE")
                problems += 1

    # Block sizes, which decide how much a performance copy reads and writes.
    print("\nPerformance block sizes")
    ours_sizes = {name: size for name, _sub, size in b.PERFORMANCE_BLOCKS}
    ours_sizes["setup"] = 15          # what read_setup asks for
    for type_name, key in (("PerformanceCommon", "common"),
                           ("PerformanceMIDI", "midi1"),
                           ("PerformancePart", "part1"),
                           ("Setup", "setup")):
        spec = types.get(type_name)
        if spec is None or not spec.get("size"):
            print(f"  {key:<8} editor gives no size")
            continue
        theirs = _size(spec["size"])
        mine = ours_sizes[key]
        if theirs is None:
            print(f"  {key:<8} rxved {mine:>4}   editor {spec['size']!r} "
                  f"(unparsed)")
        elif theirs == mine:
            print(f"  {key:<8} rxved {mine:>4} == editor {theirs}")
        else:
            print(f"  {key:<8} rxved {mine:>4} != editor {theirs}  "
                  f"<-- DISAGREE")
            problems += 1

    return problems


def dump(root: ET.Element, type_name: str) -> None:
    types = struct_types(root)
    spec = types.get(type_name)
    if spec is None:
        raise SystemExit(
            f"error: no structType {type_name!r}. Known: "
            f"{', '.join(sorted(types))}")
    print(f"{type_name}  (size {spec['size']})")
    rows = sorted(spec["values"].items(),
                  key=lambda kv: (kv[1]["offset"]
                                  if isinstance(kv[1]["offset"], int)
                                  else 999))
    for name, value in rows:
        offset = value["offset"]
        shown = f"{offset:#04x}" if isinstance(offset, int) else str(offset)
        print(f"  {shown:<6} {name:<32} range={value['range']!s:<14} "
              f"default={value['default']!s:<6} {value['type'] or ''}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--script", default=DEFAULT_SCRIPT)
    parser.add_argument("--dump", metavar="STRUCTTYPE",
                        help="print one struct's parameters and stop")
    parser.add_argument("--list", action="store_true",
                        help="list the struct types and stop")
    args = parser.parse_args(argv)

    if not os.path.exists(args.script):
        raise SystemExit(
            f"error: no editor script at {args.script}. It ships with the "
            f"XV-2020 Editor for Windows, under Script/.")
    root = load(args.script)

    if args.list:
        for name in sorted(struct_types(root)):
            print(name)
        return 0
    if args.dump:
        dump(root, args.dump)
        return 0

    problems = check(root)
    print()
    if problems:
        print(f"{problems} disagreement(s). Read the manual's own block "
              f"before changing anything -- the editor is a cross-check, "
              f"not an authority, and this tool never edits rxved.")
        return 1
    print("rxved agrees with Roland's editor script on every offset checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
