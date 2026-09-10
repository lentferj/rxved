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

"""The name catalog, and the print-versus-hardware distinction.

Names used here are invented, per CLAUDE.md: fixtures never carry a
manufacturer's patch names.
"""

import json

import pytest

from xv import catalog as cat


@pytest.fixture
def catalog():
    return cat.Catalog(
        [
            cat.Entry("USER", 1, "Velvet Bell", category="BEL"),
            cat.Entry("USER", 2, "Rusty Pad", category="SPD"),
            cat.Entry("PST-A", 1, "Glass Lead", category="SLD", voices=4),
        ],
        source="test",
    )


class TestPrintedNames:
    def test_reads_a_name(self, catalog):
        assert catalog.name("USER", 1) == "Velvet Bell"

    def test_missing_slots_are_none_not_an_error(self, catalog):
        assert catalog.name("USER", 99) is None

    def test_display_name_falls_back_to_a_visible_placeholder(self, catalog):
        """Not an empty string -- that reads as a rendering fault."""
        assert catalog.display_name("USER", 99) == cat.UNNAMED

    def test_has_is_per_bank(self, catalog):
        assert catalog.has("USER")
        assert not catalog.has("PST-D")

    def test_carries_category_and_voices(self, catalog):
        entry = catalog.entry("PST-A", 1)
        assert entry.category == "SLD"
        assert entry.voices == 4


class TestLiveNames:
    """The distinction the browser is built around."""

    def test_hardware_wins_over_print(self, catalog):
        catalog.set_live_name("USER", 1, "Iron Drone")
        assert catalog.name("USER", 1) == "Velvet Bell"      # unchanged
        assert catalog.display_name("USER", 1) == "Iron Drone"

    def test_is_live_marks_only_what_was_read(self, catalog):
        catalog.set_live_name("USER", 1, "Iron Drone")
        assert catalog.is_live("USER", 1)
        assert not catalog.is_live("USER", 2)

    def test_differs_needs_both_and_a_disagreement(self, catalog):
        catalog.set_live_name("USER", 1, "Iron Drone")
        catalog.set_live_name("USER", 2, "Rusty Pad")   # same as printed
        catalog.set_live_name("PST-D", 7, "Amber Hum")  # nothing printed
        assert catalog.differs("USER", 1)
        assert not catalog.differs("USER", 2)
        assert not catalog.differs("PST-D", 7)

    def test_a_live_name_for_an_uncatalogued_slot_still_shows(self, catalog):
        catalog.set_live_name("PST-D", 7, "Amber Hum")
        assert catalog.display_name("PST-D", 7) == "Amber Hum"

    def test_clear_live_is_scoped(self, catalog):
        catalog.set_live_name("USER", 1, "Iron Drone")
        catalog.set_live_name("PST-A", 1, "Paper Comb")
        catalog.clear_live("USER")
        assert not catalog.is_live("USER", 1)
        assert catalog.is_live("PST-A", 1)

    def test_clear_live_with_no_bank_clears_everything(self, catalog):
        catalog.set_live_name("USER", 1, "Iron Drone")
        catalog.clear_live()
        assert not catalog.is_live("USER", 1)


class TestSearch:
    def test_is_case_insensitive_substring(self, catalog):
        assert len(catalog.search("bell")) == 1

    def test_covers_live_names_too(self, catalog):
        """A slot renamed on the machine is findable without a rebuild."""
        catalog.set_live_name("PST-D", 7, "Quiet Grain")
        hits = catalog.search("grain")
        assert hits == [("PST-D", 7, "Quiet Grain")]

    def test_filters_by_kind(self, catalog):
        catalog.set_live_name("P-USER", 1, "Velvet Stack")
        assert catalog.search("velvet", kind="performance") == [
            ("P-USER", 1, "Velvet Stack")]
        assert [h[0] for h in catalog.search("velvet", kind="patch")] == ["USER"]

    def test_results_are_sorted(self, catalog):
        hits = catalog.search("e")
        assert hits == sorted(hits)


class TestLoading:
    def test_a_missing_file_is_an_empty_catalog_not_an_error(self, tmp_path):
        """rxved is useful without names; the file is deliberately unshipped."""
        loaded = cat.load(str(tmp_path / "nope.json"))
        assert len(loaded) == 0
        assert not loaded

    def test_a_corrupt_file_raises(self, tmp_path):
        """The extractor produced something wrong; browsing on would hide it."""
        path = tmp_path / "catalog.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(Exception):
            cat.load(str(path))

    def test_round_trips_through_dump_and_load(self, catalog, tmp_path):
        path = str(tmp_path / "catalog.json")
        cat.dump(catalog, path, source="test", generated="2026-09-11")
        loaded = cat.load(path)
        assert len(loaded) == len(catalog)
        assert loaded.name("USER", 1) == "Velvet Bell"
        assert loaded.entry("PST-A", 1).voices == 4
        assert loaded.generated == "2026-09-11"

    def test_dump_writes_sorted_banks(self, catalog, tmp_path):
        path = tmp_path / "catalog.json"
        cat.dump(catalog, str(path))
        payload = json.loads(path.read_text(encoding="utf-8"))
        numbers = [row["n"] for row in payload["banks"]["USER"]]
        assert numbers == sorted(numbers)
