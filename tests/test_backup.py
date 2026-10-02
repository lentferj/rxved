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

"""Performance backups -- the undo for the only destructive thing rxved does.

Names here are invented, per CLAUDE.md.
"""

import json

import pytest

from xv import backup as bk
from xv import bridge as b


def _blocks(name="Woven Drift"):
    out = {
        n: bytes((i + s) & 0x7F for i in range(size))
        for s, (n, _a, size) in enumerate(b.PERFORMANCE_BLOCKS)
    }
    out["common"] = name.ljust(12).encode("ascii") + out["common"][12:]
    return out


class TestBlockMap:
    def test_a_performance_is_thirty_six_blocks(self):
        assert len(b.PERFORMANCE_BLOCKS) == 36

    def test_the_sizes_are_the_manuals(self):
        sizes = {n: s for n, _a, s in b.PERFORMANCE_BLOCKS}
        assert sizes["common"] == 53
        assert sizes["mfx"] == 145
        assert sizes["chorus"] == 52
        assert sizes["reverb"] == 83
        assert sizes["midi1"] == 12
        assert sizes["part1"] == 49

    def test_slot_one_is_address_offset_zero(self):
        """20 00 00 00 is User Performance 01, not 02 -- the same off-by-one
        this project keeps visible everywhere else."""
        assert b.user_performance_base(1) == (0x20, 0)
        assert b.user_performance_base(64) == (0x20, 63)

    def test_slots_outside_the_range_are_refused(self):
        for slot in (0, 65, -1):
            with pytest.raises(ValueError):
                b.user_performance_base(slot)


class TestRoundTrip:
    def test_encode_decode_is_lossless(self):
        blocks = _blocks()
        assert bk.decode(bk.encode(blocks, slot=3)) == blocks

    def test_the_name_is_carried_so_a_file_can_be_identified(self):
        payload = bk.encode(_blocks("Amber Hollow"), slot=3)
        assert payload["name"] == "Amber Hollow"
        assert payload["slot"] == 3

    def test_save_and_load(self, tmp_path):
        blocks = _blocks()
        path = bk.save(blocks, str(tmp_path), slot=9)
        assert bk.load(path) == blocks

    def test_saving_twice_never_overwrites(self, tmp_path):
        first = bk.save(_blocks(), str(tmp_path), slot=9)
        second = bk.save(_blocks("Other"), str(tmp_path), slot=9)
        assert first != second
        assert bk.load(first) != bk.load(second)

    def test_listing_is_newest_first_and_keeps_unreadable_files(self, tmp_path):
        bk.save(_blocks(), str(tmp_path), slot=1)
        (tmp_path / "broken.json").write_text("{not json")
        rows = bk.list_backups(str(tmp_path))
        assert len(rows) == 2
        assert any("error" in row for row in rows)


class TestRefusals:
    """A backup that would be misread must not be restored into user memory."""

    def test_a_future_format_is_refused(self, tmp_path):
        payload = bk.encode(_blocks(), slot=1)
        payload["format"] = bk.FORMAT + 1
        path = tmp_path / "f.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(bk.BackupError, match="format"):
            bk.load(str(path))

    def test_the_wrong_kind_is_refused(self):
        with pytest.raises(bk.BackupError, match="not a performance"):
            bk.decode({"format": bk.FORMAT, "kind": "patch", "blocks": {}})

    def test_bad_hex_is_refused(self):
        with pytest.raises(bk.BackupError, match="hex"):
            bk.decode(
                {"format": bk.FORMAT, "kind": "performance", "blocks": {"common": "zz"}}
            )

    def test_an_empty_backup_is_refused(self):
        with pytest.raises(bk.BackupError, match="no blocks"):
            bk.decode({"format": bk.FORMAT, "kind": "performance", "blocks": {}})


class TestPartialWritesAreRefused:
    """Half a performance written into a slot is worse than none."""

    def test_a_missing_block_is_refused(self):
        from rxved.demo import DemoBridge

        blocks = _blocks()
        del blocks["part9"]
        with pytest.raises(ValueError, match="part9"):
            DemoBridge().write_performance_blocks(b.user_performance_base(1), blocks)

    def test_the_demo_stores_and_reads_back(self):
        from rxved.demo import DemoBridge

        demo = DemoBridge()
        previous, mismatched = demo.store_temporary_to_slot(5)
        assert mismatched == []
        stored = demo.read_performance_blocks(b.user_performance_base(5))
        assert stored == demo.read_performance_blocks(b.TEMPORARY_PERFORMANCE)
        # ...and what was there is returned, so it can be backed up.
        assert previous != stored
