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
from textual.widgets import DataTable

from rxved.app import CategoryScreen, ReportScreen, RxvedApp
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


class TestTheThreeNumbers:
    """The columns this program exists to print, in the order asked for.

    Order is PC, LSB, MSB -- what a sequencer's MIDI track asks for, since
    that is where these get typed. The order is checked, but so is the
    pairing of each label to its cell: relabelling the columns without
    moving the cells would leave the screen confidently showing an MSB
    under "PC", which is the one failure this project cannot have.
    """

    async def test_the_columns_are_pc_lsb_msb(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#slot-table", DataTable)
            labels = [str(column.label) for column in table.columns.values()]
            assert labels == ["#", "name", "PC", "LSB", "MSB", "cat", "fav"]

    async def test_each_number_is_under_its_own_label(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#slot-table", DataTable)
            slot = app._current_slots[0]
            row = table.get_row(slot.key)
            keys = [str(key.value) for key in table.columns]
            cells = dict(zip(keys, row))
            assert cells["pc"] == str(slot.program_change)
            assert cells["lsb"] == str(slot.lsb)
            assert cells["msb"] == str(slot.msb)
            # The display number is one more than the wire byte, and the two
            # are never the same column -- see xv/banks.py.
            assert cells["num"] == f"{slot.number:03d}"
            assert slot.number == slot.program_change + 1


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


class TestChannels:
    """Patches and performances go out on two different channels.

    The real synth this was written against receives patches on channel 1 and
    performances on channel 15 — so a browser that sends everything on one
    channel selects no performance at all, silently. The demo bridge answers
    with the factory defaults (1 and 16) rather than one channel for both,
    precisely so this cannot pass by accident.
    """

    async def test_channels_are_read_at_startup(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            assert app.bridge.channels is not None
            assert app.bridge.channels.patch_display == 1
            assert app.bridge.channels.performance_display == 16

    async def test_a_patch_goes_out_on_the_patch_channel(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.bridge.channel_log == [0]

    async def test_a_performance_goes_out_on_the_performance_channel(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            index = banks.bank_ids().index("P-USER")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.bridge.selected_log[0].kind == banks.Kind.PERFORMANCE
            # 15, not 0 — the whole point.
            assert app.bridge.channel_log == [15]


class TestChannelSelector:
    """Bank Select and PC are per channel, so the channel is part of the act.

    The demo synth is in PERFORM mode with parts 1 and 2 sharing channel 1,
    which is the shape the real machine has and the shape that a naive
    one-part-per-channel model gets wrong.
    """

    async def test_the_send_channel_starts_where_the_synth_listens(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            assert app.channel_is_from_device
            assert app.target_channel == app.bridge.channels.patch_receive

    async def test_brackets_step_the_channel(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            start = app.target_channel
            await pilot.press("right_square_bracket")
            await pilot.pause(0.2)
            assert app.target_channel == (start + 1) % 16
            await pilot.press("left_square_bracket")
            await pilot.pause(0.2)
            assert app.target_channel == start

    async def test_the_channel_wraps_rather_than_going_out_of_range(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            app.target_channel = 15
            await pilot.press("right_square_bracket")
            await pilot.pause(0.2)
            assert app.target_channel == 0

    async def test_changing_channel_reads_back_from_the_synth(self, app):
        """The read-back is the whole point of the selector."""
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            app.bridge.state = None
            await pilot.press("right_square_bracket")
            await pilot.pause(0.4)
            assert app.bridge.state is not None

    async def test_changing_channel_sends_no_midi(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            for _ in range(6):
                await pilot.press("right_square_bracket")
            await pilot.pause(0.3)
            assert app.bridge.selected_log == []

    async def test_a_patch_goes_out_on_the_chosen_channel(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            app.target_channel = 6
            await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.bridge.channel_log[0] == 6

    async def test_a_performance_ignores_the_chosen_channel(self, app):
        """It only responds on the Performance Control Channel."""
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            app.target_channel = 6
            index = banks.bank_ids().index("P-USER")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.bridge.channel_log[0] == 15


class TestKeyLegend:
    """The legend wraps; it never truncates. See tests/test_legend.py."""

    async def test_the_app_renders_the_legend_not_a_footer(self, app):
        from textual.widgets import DataTable, Footer

        from rxved.app import KeyHints

        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            assert app.query(KeyHints)
            assert not app.query(Footer)


class TestSweepsPutTheSynthBack:
    """A scan or probe must not leave the synth somewhere the user did not ask for.

    rxved's own SRX probe sweeps Bank Select LSB 0-63 at MSB 93 and used to
    stop wherever it finished — which on the author's machine left it on
    MSB 93 / LSB 63, a triple no board in Roland's table even uses. Found by
    reading the browser's own status line after a probe.
    """

    async def test_a_scan_restores_the_previous_patch(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            index = banks.bank_ids().index("R-USER")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            app.action_scan_bank()
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause(0.5)
            assert app.bridge.raw_log, "scan left the synth where it stopped"

    async def test_a_probe_restores_the_previous_patch(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.action_probe_srx()
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause(0.6)
            assert app.bridge.raw_log, "probe left the synth on its last LSB"


class TestDeviceInfo:
    """`i` crashed the app: its worker touched the SQLite store off-thread."""

    async def test_i_opens_the_report_instead_of_crashing(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("i")
            await pilot.pause(0.5)
            assert isinstance(app.screen, ReportScreen)

    async def test_the_report_carries_the_device_and_local_state(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("USER", 1, name="Velvet Bell")
            await pilot.press("i")
            await pilot.pause(0.5)
            body = app.screen._body
            assert "device ID" in body
            assert "favourites" in body
            assert "sound mode" in body
            assert "rxved sends on" in body

    async def test_a_silent_synth_is_reported_not_raised(self, app):
        async def nothing(*_a, **_k):
            return None

        app.bridge.identify = lambda **_k: None
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("i")
            await pilot.pause(0.5)
            assert "no Identity Reply" in app.last_status
            assert app.last_status_refused


class TestCursorStaysPut:
    """Marking a favourite must not move the cursor.

    It did: `f` rebuilt the bank table, and DataTable.clear() snaps the
    cursor to row 0 and *posts* a RowHighlighted. The `_filling` guard was
    already back to False when that arrived, so the handler dutifully loaded
    bank 0 — and the user's selection jumped to slot 001 of USER on every
    single toggle.
    """

    async def test_favouriting_keeps_the_slot_cursor(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("tab")
            for _ in range(12):
                await pilot.press("down")
            await pilot.pause()
            before = app.query_one("#slot-table").cursor_row
            assert before == 12
            await pilot.press("f")
            await pilot.pause(0.3)
            assert app.query_one("#slot-table").cursor_row == before

    async def test_favouriting_keeps_the_bank(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            index = banks.bank_ids().index("PST-B")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            await pilot.press("tab")
            for _ in range(5):
                await pilot.press("down")
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause(0.3)
            assert app._current_bank == "PST-B"
            assert app.query_one("#slot-table").cursor_row == 5

    async def test_the_favourite_actually_lands_on_the_right_slot(self, app):
        """The jump was only the visible half; the wrong row could be marked."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("tab")
            for _ in range(7):
                await pilot.press("down")
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause(0.3)
            assert ("USER", 8) in app.favorites
            assert ("USER", 1) not in app.favorites

    async def test_toggling_twice_returns_to_where_it_started(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("tab")
            for _ in range(3):
                await pilot.press("down")
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause(0.2)
            await pilot.press("f")
            await pilot.pause(0.2)
            assert app.query_one("#slot-table").cursor_row == 3
            assert ("USER", 4) not in app.favorites

    async def test_changing_channel_keeps_the_cursor(self, app):
        """It refilled the whole table just to redraw the title."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("tab")
            for _ in range(9):
                await pilot.press("down")
            await pilot.pause()
            await pilot.press("right_square_bracket")
            await pilot.pause(0.3)
            assert app.query_one("#slot-table").cursor_row == 9

    async def test_reading_names_keeps_the_cursor(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("tab")
            for _ in range(20):
                await pilot.press("down")
            await pilot.pause()
            app.action_read_bank()
            await pilot.pause(0.5)
            assert app.query_one("#slot-table").cursor_row == 20
            assert app._current_bank == "USER"


class TestFavouritesView:
    """`F` filters the real table, in two steps, and stays playable.

    The point of a favourites list is to play the things on it, so these
    views are the same DataTable with fewer rows — not a read-only report.
    Everything that works in the full list has to keep working.
    """

    @staticmethod
    async def _mark(app, pilot, rows):
        """Favourite the given cursor offsets in the current bank."""
        await pilot.press("tab")
        previous = 0
        for row in rows:
            for _ in range(row - previous):
                await pilot.press("down")
            previous = row
            await pilot.press("f")
            await pilot.pause(0.1)

    async def test_first_step_shows_only_this_banks_favourites(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await self._mark(app, pilot, [2, 5, 9])
            await pilot.press("F")
            await pilot.pause(0.3)
            assert app.view_mode == "bank-favourites"
            assert app.query_one("#slot-table").row_count == 3
            assert [s.number for s in app._current_slots] == [3, 6, 10]

    async def test_second_step_shows_favourites_from_every_bank(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("USER", 1)
            app.favorites.add("PST-B", 29)
            app.favorites.add("GM", 7)
            app._fill_slots(app._current_bank)
            await pilot.press("F")
            await pilot.pause(0.2)
            await pilot.press("F")
            await pilot.pause(0.3)
            assert app.view_mode == "all-favourites"
            assert {s.bank_id for s in app._current_slots} == {
                "USER", "PST-B", "GM"}

    async def test_a_third_press_returns_to_the_full_bank(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("USER", 1)
            for _ in range(3):
                await pilot.press("F")
                await pilot.pause(0.2)
            assert app.view_mode == "all"
            assert app.query_one("#slot-table").row_count == 128

    async def test_a_slot_can_be_selected_from_the_filtered_list(self, app):
        """The whole reason this is a filter and not a report."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("PST-B", 29)
            await pilot.press("F")
            await pilot.pause(0.2)
            await pilot.press("F")
            await pilot.pause(0.3)
            assert app.view_mode == "all-favourites"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.bridge.selected_log[-1].bank_id == "PST-B"
            assert app.bridge.selected_log[-1].number == 29

    async def test_the_bank_column_appears_only_across_banks(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("PST-B", 29)
            table = app.query_one("#slot-table")
            assert str(table.columns["num"].label) == "#"
            await pilot.press("F")
            await pilot.pause(0.2)
            await pilot.press("F")
            await pilot.pause(0.3)
            assert str(table.columns["num"].label) == "bank / #"

    async def test_un_favouriting_removes_the_row_from_a_filtered_view(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await self._mark(app, pilot, [1, 4])
            await pilot.press("F")
            await pilot.pause(0.3)
            assert app.query_one("#slot-table").row_count == 2
            await pilot.press("f")
            await pilot.pause(0.3)
            assert app.query_one("#slot-table").row_count == 1

    async def test_two_banks_may_hold_the_same_slot_number(self, app):
        """Row keys are slot.key, not the number — duplicates would collide."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("USER", 29)
            app.favorites.add("PST-B", 29)
            await pilot.press("F")
            await pilot.pause(0.2)
            await pilot.press("F")
            await pilot.pause(0.3)
            assert app.query_one("#slot-table").row_count == 2

    async def test_an_empty_view_explains_itself(self, app):
        """F is always three steps; an empty one says so rather than jumping."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("F")
            await pilot.pause(0.3)
            assert app.view_mode == "bank-favourites"
            assert app.query_one("#slot-table").row_count == 0
            assert "no favourites in this bank" in app.last_status

    async def test_the_step_is_the_same_whatever_is_favourited(self, app):
        """One keypress must land in the same view regardless of data."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("PST-B", 29)     # nothing in the current bank
            await pilot.press("F")
            await pilot.pause(0.3)
            assert app.view_mode == "bank-favourites"

    async def test_a_favourite_for_an_unknown_bank_is_skipped(self, app):
        """A favourites file can outlive a bank table entry."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("USER", 1)
            app.favorites.add("SRX-99-1", 7)
            app.view_mode = "all-favourites"
            rows = app._visible_slots(app._current_bank)
            assert [s.bank_id for s in rows] == ["USER"]


class TestCategoryFilter:
    """`C` narrows by category, on top of whatever view is showing."""

    @staticmethod
    def _catalog():
        return cat.Catalog([
            cat.Entry("USER", 1, "Velvet Bell", category="BEL"),
            cat.Entry("USER", 2, "Rusty Pad", category="SPD"),
            cat.Entry("USER", 3, "Glass Pad", category="SPD"),
            cat.Entry("USER", 4, "Iron Bass", category="SBS"),
        ])

    async def test_it_narrows_the_full_bank(self, app):
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = {"SPD"}
            app._fill_slots("USER")
            await pilot.pause()
            assert [s.number for s in app._current_slots] == [2, 3]

    async def test_several_categories_at_once(self, app):
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = {"SPD", "SBS"}
            app._fill_slots("USER")
            await pilot.pause()
            assert [s.number for s in app._current_slots] == [2, 3, 4]

    async def test_it_stacks_on_the_favourites_view(self, app):
        """The point: 'soft pads I have favourited', not one or the other."""
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            for number in (1, 2, 4):
                app.favorites.add("USER", number)
            app.view_mode = "bank-favourites"
            app.categories = {"SPD"}
            app._fill_slots("USER")
            await pilot.pause()
            # favourited AND a soft pad: 2 only. 3 is a pad but not a
            # favourite; 1 and 4 are favourites but not pads.
            assert [s.number for s in app._current_slots] == [2]

    async def test_an_empty_set_means_no_filter(self, app):
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = set()
            app._fill_slots("USER")
            await pilot.pause()
            assert len(app._current_slots) == 128

    async def test_slots_with_no_category_are_selectable_as_such(self, app):
        """Every SRX slot today — the expansion lists are not extracted."""
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = {cat.UNCATEGORISED}
            app._fill_slots("USER")
            await pilot.pause()
            # 1-4 are catalogued, the rest are not.
            assert len(app._current_slots) == 124

    async def test_the_picker_offers_only_what_is_present(self, app):
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("USER", 2)
            app.favorites.add("USER", 4)
            app.view_mode = "bank-favourites"
            app._fill_slots("USER")
            await pilot.pause()
            app.action_pick_categories()
            await pilot.pause(0.2)
            assert isinstance(app.screen, CategoryScreen)
            assert set(app.screen._counts) == {"SPD", "SBS"}

    async def test_the_picker_still_lists_a_filtered_away_category(self, app):
        """Otherwise the only way back would be to clear the filter blind."""
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = {"SPD"}
            app._fill_slots("USER")
            await pilot.pause()
            app.action_pick_categories()
            await pilot.pause(0.2)
            assert "SBS" in app.screen._counts
            assert "BEL" in app.screen._counts

    async def test_enter_applies_the_choice(self, app):
        """Pressing enter must both close the picker and set the filter.

        It did neither: the focused DataTable ate Enter for row selection,
        so the picker could only be left with escape, which discards. The
        earlier version of this test pressed enter, then asserted the filter
        was empty -- which is its starting value, so it passed whether the
        key worked or not.
        """
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.action_pick_categories()
            await pilot.pause(0.2)
            table = app.screen.query_one("#cats", DataTable)
            row = list(app.screen._counts).index("SPD")
            table.move_cursor(row=row)
            await pilot.press("space")
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert not isinstance(app.screen, CategoryScreen), \
                "enter did not close the picker"
            assert app.categories == {"SPD"}
            assert [s.number for s in app._current_slots] == [2, 3]

    async def test_selecting_everything_is_stored_as_no_filter(self, app):
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = {"SPD"}       # start from a real filter, so
            app._fill_slots("USER")        # clearing it is an observable change
            await pilot.pause()
            app.action_pick_categories()
            await pilot.pause(0.2)
            await pilot.press("a")
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert not isinstance(app.screen, CategoryScreen)
            assert app.categories == set()
            assert len(app._current_slots) == 128

    async def test_escape_leaves_the_filter_alone(self, app):
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = {"SPD"}
            app._fill_slots("USER")
            await pilot.pause()
            app.action_pick_categories()
            await pilot.pause(0.2)
            await pilot.press("escape")
            await pilot.pause(0.3)
            assert app.categories == {"SPD"}

    async def test_the_filter_shows_in_the_title(self, app):
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = {"SPD", "SBS"}
            app._fill_slots("USER")
            await pilot.pause()
            assert "SBS/SPD" in app.sub_title
