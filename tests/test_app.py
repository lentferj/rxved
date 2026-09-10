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

"""The browser, driven headlessly against the demo synth.

The one behaviour worth a UI test above all others is that **moving the
cursor sends nothing**. Everything else here is convenience; that one is the
difference between a tool you can leave open next to a running sequencer and
one you cannot.
"""

import pytest

from rxved.app import RxvedApp
from rxved.demo import DemoBridge
from rxved.favorites import Favorites
from xv import banks
from xv import catalog as cat

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path):
    catalog = cat.Catalog([
        cat.Entry("USER", 1, "Velvet Bell"),
        cat.Entry("USER", 2, "Rusty Pad"),
    ])
    bridge = DemoBridge()
    favorites = Favorites(str(tmp_path / "f.db"))
    application = RxvedApp(bridge, favorites=favorites, catalog=catalog)
    yield application
    favorites.close()


class TestStartup:
    async def test_starts_and_shows_both_tables(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#bank-table").row_count == len(
                banks.bank_ids())
            assert app.query_one("#slot-table").row_count == 128

    async def test_first_bank_is_selected(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._current_bank == banks.bank_ids()[0]


class TestCursorIsSilent:
    """The rule the whole design rests on."""

    async def test_moving_in_the_slot_table_sends_nothing(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            for _ in range(10):
                await pilot.press("down")
            await pilot.pause()
            assert app.bridge.selected_log == []

    async def test_changing_bank_sends_nothing(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            for _ in range(5):
                await pilot.press("down")
            await pilot.pause()
            assert app.bridge.selected_log == []

    async def test_enter_is_what_selects(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert len(app.bridge.selected_log) == 1
            assert app.bridge.selected_log[0].number == 2


class TestBankNavigation:
    async def test_moving_down_the_bank_list_repopulates_the_slots(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            assert app._current_bank == banks.bank_ids()[1]

    async def test_a_short_bank_shows_its_real_length(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            index = banks.bank_ids().index("R-USER")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            assert app._current_bank == "R-USER"
            assert app.query_one("#slot-table").row_count == 4


class TestFavorites:
    async def test_f_toggles(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("f")
            await pilot.pause()
            assert ("USER", 1) in app.favorites
            await pilot.press("f")
            await pilot.pause()
            assert ("USER", 1) not in app.favorites

    async def test_favouriting_stores_the_displayed_name(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("f")
            await pilot.pause()
            assert app.favorites.get("USER", 1).name == "Velvet Bell"

    async def test_an_unnamed_slot_stores_no_placeholder(self, app):
        """"--" is a rendering choice, not a name to persist."""
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            for _ in range(5):
                await pilot.press("down")
            await pilot.press("f")
            await pilot.pause()
            assert app.favorites.get("USER", 6).name == ""

    async def test_tags_are_refused_before_favouriting(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("t")
            await pilot.pause()
            assert "not a favourite" in app.last_status
            assert app.last_status_refused


class TestReadingNames:
    async def test_r_reads_a_user_bank(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_read_bank()
            await pilot.pause(0.3)
            assert app.catalog.is_live("USER", 1)

    async def test_a_read_marks_names_that_disagree_with_print(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_read_bank()
            await pilot.pause(0.3)
            # The demo's invented names are nothing like the fixture's.
            assert app.catalog.differs("USER", 1)

    async def test_r_refuses_a_rom_bank_and_says_why(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            index = banks.bank_ids().index("PST-A")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            app.action_read_bank()
            await pilot.pause()
            assert "no address" in app.last_status
            assert app.last_status_refused
            assert app.bridge.selected_log == []

    async def test_reading_relabels_favourites(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell")
            app.action_read_bank()
            await pilot.pause(0.3)
            assert app.favorites.get("USER", 1).name != "Velvet Bell"


class TestScanAsksFirst:
    async def test_scan_does_not_start_without_confirmation(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_scan_bank()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert app.bridge.selected_log == []

    async def test_scan_runs_once_confirmed(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            index = banks.bank_ids().index("R-USER")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            app.action_scan_bank()
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause(0.3)
            assert len(app.bridge.selected_log) == 4
            assert app.catalog.is_live("R-USER", 1)

    async def test_srx_probe_asks_first_too(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_probe_srx()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert app.bridge.selected_log == []
