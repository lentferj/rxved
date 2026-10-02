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

"""rxved's offsets against Roland's own editor script.

**Skipped where the script is not installed**, which is most machines: it
ships with the XV-2020 Editor for Windows and is not redistributable. That
makes this a weaker test than the rest of the suite, and it is still worth
having -- every offset it checks was transcribed by hand from a two-column
PDF, and reading that PDF one line at a time already produced one wrong
answer (RESOLUTION_NOTES §12b).

The parsing tests below need no script and always run.
"""

import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL = os.path.join(os.path.dirname(_HERE), "tools", "check_editor_map.py")


def _tool():
    spec = importlib.util.spec_from_file_location("check_editor_map", _TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _tool()

_HAVE_SCRIPT = os.path.exists(check.DEFAULT_SCRIPT)
_needs_script = pytest.mark.skipif(
    not _HAVE_SCRIPT, reason="Roland's editor script is not installed on this machine"
)


class TestSizeNotation:
    """The script writes block sizes in hex, in two notations.

    "31" is 0x31 = 49 and "00 0F" is 15. Reading either as decimal gives a
    plausible number and a short read -- and a short read of a block that is
    later written back is how a performance gets corrupted.
    """

    def test_single_token_is_hex(self):
        assert check._size("31") == 49
        assert check._size("0C") == 12
        assert check._size("35") == 53

    def test_two_tokens_are_base_128(self):
        assert check._size("00 0F") == 15
        assert check._size("01 11") == 145

    def test_nothing_is_none(self):
        assert check._size("") is None
        assert check._size(None) is None
        assert check._size("nonsense") is None


class TestOffsetNotation:
    def test_a_two_byte_offset_collapses_when_the_high_byte_is_zero(self):
        assert check._hex_bytes("00 1B") == [0, 0x1B]

    def test_hex_not_decimal(self):
        assert check._hex_bytes("10 00 20 00") == [0x10, 0, 0x20, 0]


@_needs_script
class TestAgreesWithRoland:
    """Where the script is installed, rxved must agree with it exactly."""

    def test_every_checked_offset_and_range_matches(self, capsys):
        root = check.load(check.DEFAULT_SCRIPT)
        problems = check.check(root)
        captured = capsys.readouterr().out
        assert problems == 0, captured

    def test_the_mute_switch_is_where_it_was_eventually_found(self):
        """The offset this project first said did not exist at all."""
        root = check.load(check.DEFAULT_SCRIPT)
        part = check.struct_types(root)["PerformancePart"]
        offsets = check._flat(part["values"])
        assert offsets[0x1B] == "muteSwitch"

    def test_the_block_sizes_are_the_ones_a_store_writes(self):
        from xv import bridge as b

        root = check.load(check.DEFAULT_SCRIPT)
        types = check.struct_types(root)
        ours = {name: size for name, _sub, size in b.PERFORMANCE_BLOCKS}
        assert check._size(types["PerformancePart"]["size"]) == ours["part1"]
        assert check._size(types["PerformanceMIDI"]["size"]) == ours["midi1"]
        assert check._size(types["PerformanceCommon"]["size"]) == (ours["common"])
