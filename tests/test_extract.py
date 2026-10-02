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

"""The catalog extractor's guards against losing a name.

Nothing here reads Roland's editor binary -- the record layout is all the
tests need, and they build their own tables from invented names, per
CLAUDE.md.

What is being defended is narrow but was a real fault: the editor's GM table
stamps the wrong Bank Select LSB on one record, so two patches claimed one
triple, and the catalog -- keyed on bank and number -- kept whichever came
last. No count anywhere went down, because the row was still written; it
only vanished on load. Both guards exist to make that loud.
"""

import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL = os.path.join(os.path.dirname(_HERE), "tools", "extract_catalog.py")


def _tool():
    spec = importlib.util.spec_from_file_location("extract_catalog", _TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract = _tool()


def _gm_record(lsb: int, program: int, name: str) -> bytes:
    """One record of the editor's GM table: MSB, LSB, PC, 12-byte name, NUL."""
    assert len(name) <= 12
    return (
        bytes((extract.GM_PATCH_MSB, lsb, program))
        + name.ljust(12).encode("ascii")
        + b"\x00"
    )


def _gm_table(records) -> bytes:
    """A findable GM table: padding, 256 records, padding.

    find_gm_table() locates the table by structure, so the padding has to be
    something it will not mistake for a record.
    """
    assert len(records) == 256
    pad = b"\xff" * 64
    return pad + b"".join(records) + pad


def _plain_table():
    """256 records, one per program, all variation 0. No collisions."""
    return [_gm_record(0, pc, f"Slot {pc:03d}") for pc in range(128)] + [
        _gm_record(1, pc, f"Alt {pc:03d}") for pc in range(128)
    ]


class TestGmDuplicateLsb:
    def test_a_clean_table_splits_by_the_lsb_each_record_states(self):
        out = extract.read_gm_patches(_gm_table(_plain_table()))
        assert sorted(out) == ["GM", "GM-1"]
        assert len(out["GM"]) == 128
        assert len(out["GM-1"]) == 128

    def test_an_unknown_collision_is_refused(self):
        records = _plain_table()
        # Two records claiming one triple, at a program with no correction.
        records[200] = _gm_record(1, 60, "Second 060")
        with pytest.raises(SystemExit) as caught:
            extract.read_gm_patches(_gm_table(records))
        message = str(caught.value)
        assert "LSB 1" in message and "PC 60" in message
        # It must say what to do, because the answer is in a document and
        # cannot be guessed from the binary.
        assert "GM_LSB_FIXES" in message

    def test_the_documented_collision_is_corrected_not_dropped(self):
        lsb, program = next(iter(extract.GM_LSB_FIXES))
        corrected = extract.GM_LSB_FIXES[(lsb, program)]
        records = _plain_table()
        for position, record in enumerate(records):
            if record[1] == lsb and record[2] == program:
                first = position
                break
        records[first + 1] = _gm_record(lsb, program, "Shadowed")
        out = extract.read_gm_patches(_gm_table(records))
        # Both survive, on the LSBs the manual gives, so both are reachable.
        landed = [
            bank
            for bank, rows in out.items()
            if any(row["n"] == program + 1 for row in rows)
        ]
        expected = {f"GM-{value}" if value else "GM" for value in corrected}
        assert expected <= set(landed)
        assert "Shadowed" in [row["name"] for rows in out.values() for row in rows]

    def test_every_record_survives_the_correction(self):
        """The count is the point: 256 records in, 256 names out."""
        lsb, program = next(iter(extract.GM_LSB_FIXES))
        records = _plain_table()
        first = next(
            position
            for position, record in enumerate(records)
            if record[1] == lsb and record[2] == program
        )
        records[first + 1] = _gm_record(lsb, program, "Shadowed")
        out = extract.read_gm_patches(_gm_table(records))
        assert sum(len(rows) for rows in out.values()) == 256
        keys = [f"{bank}:{row['n']:03d}" for bank, rows in out.items() for row in rows]
        assert len(set(keys)) == 256, "a name would be lost on load"


class TestCheckNoDuplicates:
    def test_a_repeated_slot_is_refused(self):
        with pytest.raises(SystemExit) as caught:
            extract.check_no_duplicates(
                {"PST-A": [{"n": 7, "name": "One"}, {"n": 7, "name": "Two"}]}
            )
        assert "PST-A" in str(caught.value)
        assert "007" in str(caught.value)

    def test_distinct_slots_pass(self):
        extract.check_no_duplicates(
            {
                "PST-A": [{"n": 1, "name": "One"}, {"n": 2, "name": "Two"}],
                "GM-1": [{"n": 1, "name": "Three"}],
            }
        )

    def test_the_same_number_in_two_banks_is_not_a_duplicate(self):
        """Banks are separate keyspaces -- GM-1 001 and GM-2 001 coexist."""
        extract.check_no_duplicates(
            {
                "GM-1": [{"n": 1, "name": "One"}],
                "GM-2": [{"n": 1, "name": "Two"}],
            }
        )
