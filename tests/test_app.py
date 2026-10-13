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

import time

import pytest
from textual.widgets import DataTable, Input, Static

from textual.coordinate import Coordinate

from rxved.app import (
    CategoryScreen,
    ChoiceScreen,
    CommonScreen,
    EffectsScreen,
    MultiScreen,
    PatchPickerScreen,
    PerformanceScreen,
    RatingFilter,
    RatingFilterScreen,
    ReportScreen,
    RxvedApp,
    StoreScreen,
    TextPromptScreen,
)
from rxved.screens import ConfirmScreen
from rxved.scan import ScanOfferScreen, ScanScreen
from rxved.demo import DemoBridge
from rxved.favorites import Favorites
from xv import banks
from xv import catalog as cat
from xv import params
from xv.config import load_ui_state, save_ui_state

pytestmark = pytest.mark.asyncio


async def settle(pilot, seconds: float = 5.0) -> None:
    """Wait until the app's bridge is idle, rather than for a fixed time.

    ``pilot.pause(0.3)`` was the idiom here, and it is a wall-clock guess at
    how long a worker thread takes. That is a coin flip rather than a test:
    the suite runs under coverage, which roughly doubles it, and the read
    tests below wait on a worker doing 128 round trips against the demo
    synth. Three of them failed intermittently once coverage was on, with
    an assertion that looked like a real bug and was not.

    ``_busy`` is set on the main thread before a worker starts and cleared
    in the worker's ``finally``, so polling it waits on the actual
    condition. The timeout stays, so a worker that never finishes still
    fails the test rather than hanging it.

    One extra pause after ``_busy`` clears, because that is not the same as
    "the result is on screen": the workers clear the flag in a ``finally``
    and *then* post their result to the main thread with ``call_from_thread``.
    Without this the helper returns in the window between the two and the
    assertion below reads the pre-worker state.
    """
    waited = 0.0
    while pilot.app._busy and waited < seconds:
        await pilot.pause(0.05)
        waited += 0.05
    await pilot.pause(0.05)


@pytest.fixture
def app(tmp_path):
    catalog = cat.Catalog(
        [
            cat.Entry("USER", 1, "Velvet Bell"),
            cat.Entry("USER", 2, "Rusty Pad"),
        ]
    )
    bridge = DemoBridge()
    favorites = Favorites(str(tmp_path / "f.db"))
    # backup_dir is not optional in tests: the store worker writes real
    # files, and without this the suite leaves performance backups in the
    # user's own data directory. It did.
    application = RxvedApp(
        bridge,
        favorites=favorites,
        catalog=catalog,
        backup_dir=str(tmp_path / "backups"),
        # The startup bank read would overwrite the catalog this fixture
        # hands over -- the tests below are about the printed names -- and
        # write the result into the real data directory. Both are why it is
        # off here; TestStartupBankRead covers it deliberately.
        live_names_path=str(tmp_path / "live-names.json"),
        read_banks_at_startup=False,
    )
    yield application
    favorites.close()


@pytest.fixture
def patch_mode_app(tmp_path):
    """An app whose demo synth is in PATCH mode.

    `scan_bank` can only work where a program change moves the current
    patch, so the scan tests need this; the default fixture is in PERFORM
    mode on purpose, to exercise the multitimbral paths.
    """
    bridge = DemoBridge(patch_mode=True)
    favorites = Favorites(str(tmp_path / "f.db"))
    application = RxvedApp(
        bridge,
        favorites=favorites,
        catalog=cat.Catalog([cat.Entry("PST-A", 1, "Iron Drone")]),
        backup_dir=str(tmp_path / "backups"),
        live_names_path=str(tmp_path / "live-names.json"),
        read_banks_at_startup=False,
    )
    yield application
    favorites.close()


class TestStartup:
    async def test_starts_and_shows_both_tables(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#bank-table").row_count == len(banks.bank_ids())
            assert app.query_one("#slot-table").row_count == 128

    async def test_first_bank_is_selected(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._current_bank == banks.bank_ids()[0]


class TestStartupBankRead:
    """The writable banks are re-read when the window opens.

    USER is the bank that is about to be wrong: the catalog says what the
    factory shipped, and a USER bank somebody has saved into says something
    else. Only the instrument can tell the difference, so the browser asks
    it -- silently, in the background, because `read_user_bank` is RQ1/DT1
    against addresses in the parameter map and selects nothing.
    """

    async def _settled(self, pilot, app):
        """Wait for the background read, rather than sleeping a guessed time.

        A fixed pause is a test that passes on a fast machine and fails
        under `make check`, which runs the suite with coverage on and takes
        half again as long. This asks the worker manager instead.
        """
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

    def _app(self, tmp_path, bridge=None, **kwargs):
        bridge = bridge if bridge is not None else DemoBridge()
        favorites = Favorites(str(tmp_path / "f.db"))
        application = RxvedApp(
            bridge,
            favorites=favorites,
            catalog=cat.Catalog([cat.Entry("USER", 1, "Grand XV")]),
            backup_dir=str(tmp_path / "backups"),
            live_names_path=str(tmp_path / "live-names.json"),
            **kwargs,
        )
        return application, favorites

    async def test_it_reads_the_writable_banks(self, tmp_path):
        application, favorites = self._app(tmp_path)
        async with application.run_test() as pilot:
            await self._settled(pilot, application)
            assert application.catalog.is_live("USER", 1)
            assert application.catalog.is_live("R-USER", 1)
            assert application.catalog.is_live("P-USER", 1)
        favorites.close()

    async def test_it_selects_nothing(self, tmp_path):
        """The whole reason this can run unattended at startup."""
        application, favorites = self._app(tmp_path)
        async with application.run_test() as pilot:
            await self._settled(pilot, application)
            assert application.bridge.selected_log == []
        favorites.close()

    async def test_hardware_beats_the_printed_list(self, tmp_path):
        """A USER bank full of the user's own patches is not the factory one."""
        application, favorites = self._app(tmp_path)
        async with application.run_test() as pilot:
            await self._settled(pilot, application)
            assert application.catalog.display_name("USER", 1) != "Grand XV"
            assert application.catalog.differs("USER", 1)
        favorites.close()

    async def test_it_persists_what_it_read(self, tmp_path):
        """So the next run opens with the right names already up.

        This is the bug: the names used to live only in the process, so
        every restart fell back to the factory list.
        """
        from rxved import livenames

        path = str(tmp_path / "live-names.json")
        application, favorites = self._app(tmp_path)
        async with application.run_test() as pilot:
            await self._settled(pilot, application)
        favorites.close()

        stored = livenames.load(path)
        assert stored, "nothing was written"
        expected = application.bridge.read_user_bank("USER")  # cheap on the demo
        for number, name in expected.items():
            assert stored[f"USER:{number:03d}"] == name

    async def test_a_keypress_during_the_read_is_refused(self, tmp_path):
        """The background read holds `_busy`, so nothing else may start.

        Deterministic rather than timed: `_busy` is set directly instead of
        waiting for a real read to happen to still be in flight, which is a
        race that passes on a fast machine and fails under coverage.
        """
        application, favorites = self._app(tmp_path, read_banks_at_startup=False)
        async with application.run_test() as pilot:
            await pilot.pause()
            application._busy = True
            application.action_read_names()
            await pilot.pause()
            assert application.last_status_refused, "the read was not refused"
            assert not application.catalog.is_live("USER", 1)
        favorites.close()

    async def test_a_refusal_survives_the_progress_ticks(self, tmp_path):
        """A refusal outlives the worker's running total.

        The startup read of the three writable banks posts a tick per patch
        and takes about three seconds against real hardware, and those ticks
        used to drive the same status line as everything else -- so a refusal
        keyed inside that window was overwritten milliseconds later. `*` on a
        slot that was not a favourite came back as total silence, which is
        the one thing a refusal must never do. This is that bug, pinned.

        A slow bridge rather than a sleep in the test: the ticks have to
        arrive from a real worker thread while the keypress is already
        standing, which is the ordering the bug needed.
        """

        class SlowBridge(DemoBridge):
            """About six seconds for USER, the way a real XV-2020 is over USB."""

            def read_user_bank(self, bank_id, *, on_progress=None, timeout=None):
                out = {}
                entries = banks.slots(bank_id)
                for index, entry in enumerate(entries):
                    time.sleep(0.05)
                    out[entry.number] = self.user_patch_name(entry.number)
                    if on_progress is not None:
                        on_progress(index + 1, len(entries))
                return out

        application, favorites = self._app(
            tmp_path, bridge=SlowBridge(), read_banks_at_startup=True
        )
        try:
            async with application.run_test() as pilot:
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                assert not application.last_status_refused
                await pilot.press("asterisk")  # USER 1 is not a favourite
                await pilot.pause()
                assert application.last_status_refused, "the key was not refused"
                refused = str(application.query_one("#status")._Static__content)
                # Long enough for at least one progress tick to have been
                # posted, and the read is still running when this asserts.
                await pilot.pause(0.6)
                assert application.last_status_refused, "a tick cleared the refusal"
                assert (
                    str(application.query_one("#status")._Static__content) == refused
                ), "a tick overwrote the refusal"
        finally:
            favorites.close()

    async def test_the_startup_read_holds_busy_while_it_runs(self, tmp_path):
        """Otherwise a keypress would open a second exchange on one port."""
        application, favorites = self._app(tmp_path)
        async with application.run_test() as pilot:
            await pilot.pause(0.02)
            seen_busy = application._busy
            await self._settled(pilot, application)
            assert seen_busy, "the read ran without marking the app busy"
            assert not application._busy, "busy was never released"
        favorites.close()

    async def test_it_can_be_turned_off(self, tmp_path):
        """The knob the shared fixture uses, and the reason it is injectable."""
        application, favorites = self._app(tmp_path, read_banks_at_startup=False)
        async with application.run_test() as pilot:
            await self._settled(pilot, application)
            assert not application.catalog.is_live("USER", 1)
        favorites.close()


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
            # The favourite marker leads the row and the rating follows the
            # name; PC, LSB and MSB stay in the order an MPC's MIDI track
            # asks for them, which is the whole point of this test.
            assert labels == ["♥", "#", "name", "★", "PC", "LSB", "MSB", "cat"]
            assert labels[labels.index("PC") : labels.index("PC") + 3] == [
                "PC",
                "LSB",
                "MSB",
            ]

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


class TestTheRatingColumn:
    """The `★` column, between the name and the wire numbers.

    The rating was set with `*` and undone with `z` from the day it landed,
    but the only place it was ever *shown* was the detail line, so a rating
    you could not see while scrolling was a rating you did not use. `*`
    itself is not this glyph: two columns over it already means "this name
    disagrees with the catalogue".
    """

    @staticmethod
    def _cells(app):
        table = app.query_one("#slot-table", DataTable)
        row = table.get_row(app._current_slots[0].key)
        keys = [str(key.value) for key in table.columns]
        return dict(zip(keys, row))

    async def _rate_to(self, app, pilot, stars):
        await pilot.press("tab")
        await pilot.press("asterisk")
        await pilot.pause()
        app.screen.query_one("#choices").cursor_coordinate = Coordinate(stars, 0)
        await pilot.pause()
        await pilot.press("enter")
        await settle(pilot)

    async def test_a_rated_favourite_shows_the_stars(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", rating=4)
            app._refresh_current_bank()
            await pilot.pause()
            cells = self._cells(app)
            # Value *and* scale: three-of-three and three-of-five must not
            # read the same.
            assert cells["rating"] == "★★★★·"

    async def test_an_unrated_slot_is_blank(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell")
            app._refresh_current_bank()
            await pilot.pause()
            assert self._cells(app)["rating"] == ""

    async def test_asterisk_fills_the_cell_in_place(self, app):
        """Without moving the cursor: `clear()` posts a RowHighlighted, and a
        rating should not scroll the list out from under you."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell")
            app.favorites.add("USER", 2, name="Rusty Pad")
            app._refresh_current_bank()
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("down")  # row 1, slot 2
            await pilot.pause()
            before = app.query_one("#slot-table").cursor_row
            assert before == 1
            await self._rate_to(app, pilot, 3)
            table = app.query_one("#slot-table", DataTable)
            assert table.cursor_row == before
            assert table.get_row("USER:002")[3] == "★★★··"

    async def test_unfavouriting_blanks_the_rating(self, app):
        """Not a stale cell: `remove` keeps the row and `get` stops seeing
        it, so a rating left showing would be a rating on a slot with no
        heart."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", rating=5)
            app._refresh_current_bank()
            await pilot.pause()
            assert self._cells(app)["rating"] == "★★★★★"
            await pilot.press("tab")
            await pilot.press("f")
            await settle(pilot)
            assert self._cells(app)["rating"] == ""

    async def test_refavouriting_brings_the_rating_back(self, app):
        """`toggle` restores what the slot was carrying -- the whole point of
        `remove` keeping the row -- so the cell has to read it back."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", rating=3)
            app._refresh_current_bank()
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("f")
            await settle(pilot)
            assert self._cells(app)["rating"] == ""
            await pilot.press("f")
            await settle(pilot)
            assert self._cells(app)["rating"] == "★★★··"


class TestRatingFilter:
    """`V` -- narrow the list by rating, exactly or at least.

    The third filter, after the favourites view and the categories, and it
    *stacks* on both rather than replacing either. "3 and up" means one thing
    inside a favourites view and another inside a whole bank, and a filter
    whose meaning changed with the view would be a keypress you cannot
    predict.
    """

    async def _apply(self, app, pilot, row, column):
        await pilot.press("V")
        await pilot.pause()
        assert isinstance(app.screen, RatingFilterScreen)
        table = app.screen.query_one("#ratings", DataTable)
        table.cursor_coordinate = Coordinate(row, column)
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("enter")
        await settle(pilot)

    async def _shown(self, app):
        return [s.number for s in app._current_slots]

    async def test_V_offers_both_widths_side_by_side(self, app):
        async with app.run_test() as pilot:
            await pilot.press("V")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RatingFilterScreen)
            table = screen.query_one("#ratings", DataTable)
            labels = [str(column.label) for column in table.columns.values()]
            assert labels == ["=", "≥", "rating"]
            # Six rows: no rating, then one to five stars.
            assert [str(key.value) for key in table.rows] == [
                str(stars) for stars in range(6)
            ]
            assert str(table.get_row("3")[2]) == "★★★··"
            assert str(table.get_row("0")[2]) == "no rating"

    async def test_at_least_narrows_to_the_rated_and_up(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            for number, rating in ((1, 5), (2, 3), (3, 3)):
                app.favorites.add("USER", number, name=f"n{number}", rating=rating)
            app._refresh_current_bank()
            await pilot.pause()
            assert await self._shown(app) == list(range(1, 129))
            await self._apply(app, pilot, 3, 1)  # ≥ ★★★
            assert await self._shown(app) == [1, 2, 3]

    async def test_exactly_zero_is_the_unrated_favourites(self, app):
        """The question this filter answers most often, and the reason it
        stacks: inside a favourites view, "=0" is what I have not rated
        yet. In a whole bank it would be every slot nobody rated, which is
        a different and far less interesting answer."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", rating=5)
            app.favorites.add("USER", 2, name="Rusty Pad")
            await pilot.press("tab")
            await pilot.press("v")  # this bank's favourites
            await settle(pilot)
            assert await self._shown(app) == [1, 2]
            await self._apply(app, pilot, 0, 0)  # = no rating
            assert await self._shown(app) == [2]

    async def test_it_stacks_on_the_category_filter(self, app, tmp_path):
        catalog = cat.Catalog(
            [
                cat.Entry("USER", 1, "Velvet Bell", "KEY"),
                cat.Entry("USER", 2, "Rusty Pad", "KEY"),
                cat.Entry("USER", 3, "Tin Whistle", "ORG"),
            ]
        )
        bridge = DemoBridge()
        favourites = Favorites(str(tmp_path / "f.db"))
        application = RxvedApp(
            bridge,
            favorites=favourites,
            catalog=catalog,
            backup_dir=str(tmp_path / "backups"),
            live_names_path=str(tmp_path / "live-names.json"),
            read_banks_at_startup=False,
        )
        try:
            async with application.run_test() as pilot:
                await pilot.pause()
                for number, rating in ((1, 4), (2, 3), (3, 5)):
                    application.favorites.add(
                        "USER", number, name=f"n{number}", rating=rating
                    )
                application._refresh_current_bank()
                await pilot.pause()
                application.action_pick_categories()
                await pilot.pause()
                cats = application.screen.query_one("#cats", DataTable)
                # KEY only: Tin Whistle is ORG and goes.
                cats.cursor_coordinate = Coordinate(0, 0)
                await pilot.pause()
                await pilot.press("space")
                await pilot.pause()
                await pilot.press("enter")
                await settle(pilot)
                # Tin Whistle is rated five and would survive the rating filter
                # on its own -- that it is gone is the stack, not the filter.
                assert await self._shown(application) == [1, 2]
                # And ≥ ★★★★ on top of that leaves only Velvet Bell.
                await self._apply(application, pilot, 4, 1)
                assert await self._shown(application) == [1]
        finally:
            favourites.close()

    async def test_rerating_under_a_live_filter_drops_the_row(self, app):
        """The row has stopped matching, so it leaves.

        `_mark_favorite` only knew how to drop a row from inside a favourites
        view. A rating filter makes rows leave from the all-slots view too --
        un-favourite a rated slot there and its rating goes with it -- and a
        row left sitting in a filtered list is a row the filter does not
        describe.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", rating=4)
            app._refresh_current_bank()
            await pilot.pause()
            await self._apply(app, pilot, 1, 1)  # ≥ ★
            assert await self._shown(app) == [1]
            await pilot.press("tab")
            await pilot.press("f")
            await settle(pilot)
            assert await self._shown(app) == []

    async def test_clearing_brings_everything_back(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", rating=4)
            app._refresh_current_bank()
            await pilot.pause()
            await self._apply(app, pilot, 4, 1)
            assert await self._shown(app) == [1]
            await pilot.press("V")
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.rating_filter is None
            assert await self._shown(app) == list(range(1, 129))

    async def test_escape_leaves_the_filter_alone(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", rating=4)
            app._refresh_current_bank()
            await pilot.pause()
            await self._apply(app, pilot, 4, 1)
            assert await self._shown(app) == [1]
            await pilot.press("V")
            await pilot.pause()
            await pilot.press("escape")
            await settle(pilot)
            assert await self._shown(app) == [1]

    async def test_at_least_nothing_is_not_offered(self, app):
        """At least *nothing* matches everything, which is not a filter.

        Offered, it would be a control whose only effect is to print
        `rating ≥0` in the subtitle. Exactly-nothing stays: it is a real
        question.
        """
        async with app.run_test() as pilot:
            await pilot.press("V")
            await pilot.pause()
            table = app.screen.query_one("#ratings", DataTable)
            table.cursor_coordinate = Coordinate(0, 1)
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            assert app.screen._current is None
            assert str(table.get_row("0")[1]) == " "

    async def test_the_rating_column_is_five_wide_whatever_is_rated(self, app):
        """Fitted to its content, the column changes shape with the data.

        With nothing rated it collapses to one cell -- the width of its own
        `★` header -- and with something rated it is five. So the same column
        is a different width depending on data that has nothing to do with how
        wide a column should be, and the row you just rated jumps sideways as
        it appears. Pinned at five instead: that is what a rating costs,
        whether or not one is showing.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#slot-table", DataTable)
            assert table.columns.get("rating").width == 5
            assert "★" in table.render_line(0).text
            # And a rated row renders the full scale in it, not clipped.
            app.favorites.add("USER", 1, name="Velvet Bell", rating=4)
            app._refresh_current_bank()
            await pilot.pause()
            assert table.columns.get("rating").width == 5
            assert "★★★★·" in table.render_line(1).text

    async def test_the_subtitle_names_the_filter(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", rating=4)
            app._refresh_current_bank()
            await pilot.pause()
            await self._apply(app, pilot, 4, 1)
            assert "rating ≥4" in app.sub_title
            await self._apply(app, pilot, 4, 0)
            assert "rating =4" in app.sub_title
            assert "≥" not in app.sub_title

    async def test_a_rating_change_takes_the_row_out_of_the_filter(self, app):
        """Rating can un-match a row, and a stale row makes the list a lie.

        `f` had the "did the row stop matching?" check and `*` did not: rate a
        3-star down to nothing under a ">= 2" filter and the row sat there
        until you cycled the view, which is the one gesture nobody thinks to
        make to find out why their list is wrong.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="one", rating=2)
            app.favorites.add("USER", 2, name="two", rating=5)
            app._refresh_current_bank()
            await pilot.pause()
            await self._apply(app, pilot, 2, 1)  # >= **
            assert [s.number for s in app._current_slots] == [1, 2]

            # Cursor on USER 001, which is rated 2 and so matches.
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("asterisk")
            await pilot.pause()
            app.screen.query_one("#choices").cursor_coordinate = Coordinate(0, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)

            assert [s.number for s in app._current_slots] == [2]
            assert "1 of 128" in app.sub_title
            assert "no longer matches" in app.last_status
            # And the cursor held its position rather than following the row.
            assert app.query_one("#slot-table").cursor_row == 0

    async def test_a_rating_change_that_still_matches_updates_in_place(self, app):
        """The other half: no rebuild when the row still belongs, so the
        cursor does not jump on an ordinary rating edit."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="one", rating=2)
            app._refresh_current_bank()
            await pilot.pause()
            await self._apply(app, pilot, 2, 1)  # >= **
            assert [s.number for s in app._current_slots] == [1]
            await pilot.press("tab")
            await pilot.pause()
            row = app.query_one("#slot-table").cursor_row
            await pilot.press("asterisk")
            await pilot.pause()
            app.screen.query_one("#choices").cursor_coordinate = Coordinate(5, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            # 5 still matches ">= 2", so the row stayed and the cell changed.
            assert [s.number for s in app._current_slots] == [1]
            assert app.query_one("#slot-table").get_row("USER:001")[3] == "★★★★★"
            assert app.query_one("#slot-table").cursor_row == row
            assert "no longer matches" not in app.last_status

    async def test_an_exact_match_also_drops_the_row(self, app):
        """Going *up* can un-match too: `= **` stops matching a 3-star."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="one", rating=2)
            app._refresh_current_bank()
            await pilot.pause()
            await self._apply(app, pilot, 2, 0)  # = **
            assert [s.number for s in app._current_slots] == [1]
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("asterisk")
            await pilot.pause()
            app.screen.query_one("#choices").cursor_coordinate = Coordinate(3, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert [s.number for s in app._current_slots] == []

    async def test_a_restored_filter_says_so_and_counts_against_the_bank(
        self, app, tmp_path
    ):
        """The one that gets asked after a restart.

        A rating filter is remembered between runs and a send channel is not,
        so coming back three days later the list is short and nothing on
        screen says why. The subtitle has to lead with the filter and count
        what is shown against what the view would hold unfiltered -- "2 of 128
        patches" reads as "I am looking at part of something", where "2
        patches" reads as "a small bank".
        """
        catalog = cat.Catalog([cat.Entry("USER", 1, "Velvet Bell")])
        favourites = Favorites(str(tmp_path / "f.db"))
        favourites.add("USER", 1, name="Velvet Bell", rating=4)
        favourites.add("USER", 2, name="Rusty Pad", rating=5)
        config = str(tmp_path / "config.toml")
        save_ui_state(config, rating_stars=3, rating_at_least=True)

        def built(config_path=None):
            return RxvedApp(
                DemoBridge(),
                favorites=favourites,
                catalog=catalog,
                backup_dir=str(tmp_path / "backups"),
                live_names_path=str(tmp_path / "live-names.json"),
                read_banks_at_startup=False,
                config_path=config_path if config_path is not None else config,
            )

        try:
            application = built()
            async with application.run_test() as pilot:
                await pilot.pause()
                assert application.rating_filter == RatingFilter(3, at_least=True)
                subtitle = application.sub_title
                # The filter is the first thing on the line, ahead of the bank
                # and of the channel.
                assert subtitle.startswith("★ rating ≥3"), subtitle
                assert "2 of 128 patches" in subtitle, subtitle
                assert subtitle.index("ch 1") > subtitle.index("rating ≥3")

            # And with no filter at all there is no "of", so the denominator
            # is noise only when it means something.
            unfiltered = built(str(tmp_path / "config2.toml"))
            async with unfiltered.run_test() as pilot:
                await pilot.pause()
                assert unfiltered.rating_filter is None
                assert "of 128" not in unfiltered.sub_title
                assert "★" not in unfiltered.sub_title
        finally:
            favourites.close()

    async def test_the_filter_comes_back_after_a_restart(self, app, tmp_path):
        catalog = cat.Catalog([cat.Entry("USER", 1, "Velvet Bell")])
        favourites = Favorites(str(tmp_path / "f.db"))
        favourites.add("USER", 1, name="Velvet Bell", rating=4)
        config = str(tmp_path / "config.toml")

        def built():
            return RxvedApp(
                DemoBridge(),
                favorites=favourites,
                catalog=catalog,
                backup_dir=str(tmp_path / "backups"),
                live_names_path=str(tmp_path / "live-names.json"),
                read_banks_at_startup=False,
                config_path=config,
            )

        try:
            first = built()
            async with first.run_test() as pilot:
                await pilot.pause()
                await self._apply(first, pilot, 4, 1)
                assert first.rating_filter == RatingFilter(stars=4, at_least=True)
                assert "rating ≥4" in first.sub_title

            second = built()
            async with second.run_test() as pilot:
                await pilot.pause()
                assert second.rating_filter == RatingFilter(stars=4, at_least=True)
                assert [s.number for s in second._current_slots] == [1]
        finally:
            favourites.close()

    async def test_clearing_the_filter_stops_it_coming_back(self, app, tmp_path):
        """Clearing must reach the file, not only the memory.

        The filter is remembered between runs, which is the point of it -- so
        a clear that only unset the in-memory copy left ``rating_stars = 3``
        in ``config.toml`` and the filter was back, active, on the next
        launch, with nothing on screen saying why. ``save_ui_state`` used
        ``None`` to mean "not passed", and the settings writer reads ``None``
        as "unset this key", so the two collided and removing the key was
        impossible.
        """
        catalog = cat.Catalog([cat.Entry("USER", 1, "Velvet Bell")])
        favourites = Favorites(str(tmp_path / "f.db"))
        favourites.add("USER", 1, name="Velvet Bell", rating=3)
        config = str(tmp_path / "config.toml")
        save_ui_state(config, rating_stars=3, rating_at_least=False)
        assert load_ui_state(config)["rating_stars"] == 3, "fixture did not persist"

        def built():
            return RxvedApp(
                DemoBridge(),
                favorites=favourites,
                catalog=catalog,
                backup_dir=str(tmp_path / "backups"),
                live_names_path=str(tmp_path / "live-names.json"),
                read_banks_at_startup=False,
                config_path=config,
            )

        try:
            first = built()
            async with first.run_test() as pilot:
                await pilot.pause()
                assert first.rating_filter == RatingFilter(3, at_least=False)
                # x clears the filter, enter applies the clearing.
                await pilot.press("V")
                await pilot.pause()
                await pilot.press("x")
                await pilot.pause()
                await pilot.press("enter")
                await settle(pilot)
                assert first.rating_filter is None
                assert "rating" not in first.sub_title
            assert "rating_stars" not in load_ui_state(config), (
                "clearing left the key in config.toml"
            )

            # A fresh app on the same file must come up unfiltered.
            second = built()
            async with second.run_test() as pilot:
                await pilot.pause()
                assert second.rating_filter is None
                assert "★" not in second.sub_title
                assert len(second._current_slots) == 128
        finally:
            favourites.close()

    async def test_saving_the_view_leaves_the_other_keys_alone(self, app, tmp_path):
        """The sentinel that makes clearing possible must not turn every
        ``None`` into a deletion: saving the view still leaves the bank
        position, the cursor and any filter exactly where they were."""
        config = str(tmp_path / "config.toml")
        save_ui_state(config, bank_index=2, slot_cursor=7, rating_stars=3)
        save_ui_state(config, view_mode="all-favourites")
        saved = load_ui_state(config)
        assert saved["bank_index"] == 2, "saving the view wiped the bank position"
        assert saved["slot_cursor"] == 7, "saving the view wiped the cursor"
        assert saved["rating_stars"] == 3, "saving the view wiped the filter"
        # And removing the filter does not disturb the position either.
        save_ui_state(config, rating_stars=None, rating_at_least=None)
        saved = load_ui_state(config)
        assert "rating_stars" not in saved
        assert saved["bank_index"] == 2

    async def test_a_boolean_in_the_file_is_not_a_one_star_filter(self, app, tmp_path):
        """`isinstance(True, int)` is True, so a TOML `true` in the stars key
        would load as one star -- the same trap the channel setting has, on
        the one key where it would look like a filter rather than a wrong
        MIDI channel."""
        from xv.config import load_ui_state, save_ui_state

        path = str(tmp_path / "config.toml")
        save_ui_state(path, rating_stars=True, rating_at_least=True)  # type: ignore[arg-type]
        assert "rating_stars" not in load_ui_state(path)
        save_ui_state(path, rating_stars=9)
        assert "rating_stars" not in load_ui_state(path)
        save_ui_state(path, rating_stars=2, rating_at_least=True)
        assert load_ui_state(path)["rating_stars"] == 2
        assert load_ui_state(path)["rating_at_least"] is True

    async def test_right_steps_into_the_at_least_column(self, app):
        """What the user could not do: pick "3 and up".

        The table was built with ``cursor_type="row"``, and
        ``DataTable.action_cursor_right`` only moves the cursor for a "cell"
        or "column" cursor -- otherwise it calls
        ``super().action_scroll_right()`` and just scrolls. So right scrolled
        a table that was never wider than its own frame, the column never
        left 0, and `≥` was a column of dead code the first cut never
        reached.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            for number, rating in ((1, 5), (2, 3), (3, 3)):
                app.favorites.add("USER", number, name=f"n{number}", rating=rating)
            app._refresh_current_bank()
            await pilot.pause()
            assert await self._shown(app) == list(range(1, 129))
            await pilot.press("V")
            await pilot.pause()
            table = app.screen.query_one("#ratings", DataTable)
            assert table.cursor_coordinate == Coordinate(0, 0)
            # Driven with the keys, not with a coordinate assigned behind the
            # screen's back: the bug was in the key path.
            await pilot.press("right")
            await pilot.pause()
            assert table.cursor_coordinate == Coordinate(0, 1)
            await pilot.press("down", "down", "down")
            await pilot.pause()
            assert table.cursor_coordinate == Coordinate(3, 1)
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.rating_filter == RatingFilter(stars=3, at_least=True)
            assert await self._shown(app) == [1, 2, 3]

    async def test_a_toggle_keeps_the_column_it_was_made_in(self, app):
        """`table.clear()` resets the cursor to ``(0, 0)`` -- both halves of
        the coordinate, not just the row -- so `_fill` has to save and
        restore the column too. Otherwise every toggle threw the user back
        to `=` and each one cost another press of right."""
        async with app.run_test() as pilot:
            await pilot.press("V")
            await pilot.pause()
            table = app.screen.query_one("#ratings", DataTable)
            await pilot.press("right")
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            assert table.cursor_coordinate == Coordinate(1, 1)
            # A second toggle, one row further down: the second one is where
            # a row-only restore would have already lost the column.
            await pilot.press("down")
            await pilot.press("space")
            await pilot.pause()
            assert table.cursor_coordinate == Coordinate(2, 1)
            assert app.screen._current == RatingFilter(stars=2, at_least=True)
            # And the cell the cursor is on is the one that is marked.
            assert "x" in str(table.get_row("2")[1])

    async def test_space_on_the_stars_label_toggles_the_last_column(self, app):
        """The label is read-only, and it is reachable now the cursor moves.

        On a row cursor nothing to the right of `=` was reachable at all; on
        a cell cursor the user can walk onto the stars to read the row they
        are about to choose in, and `space` there used to do nothing at
        all -- which reads as a broken key. It goes to whichever radio column
        was last actually toggled in instead.
        """
        async with app.run_test() as pilot:
            await pilot.press("V")
            await pilot.pause()
            table = app.screen.query_one("#ratings", DataTable)
            # Choose in `≥` first: that is the column the screen remembers.
            await pilot.press("down")
            await pilot.press("right")
            await pilot.pause()
            assert table.cursor_coordinate == Coordinate(1, 1)
            await pilot.press("space")
            await pilot.pause()
            assert app.screen._current == RatingFilter(stars=1, at_least=True)
            # Now walk right onto the label of the row below.
            await pilot.press("down")
            await pilot.press("right", "right")
            await pilot.pause()
            assert table.cursor_coordinate == Coordinate(2, 2)
            await pilot.press("space")
            await pilot.pause()
            assert app.screen._current == RatingFilter(stars=2, at_least=True)
            assert "x" in str(table.get_row("2")[1])
            assert " " in str(table.get_row("1")[1])

    async def test_space_on_the_stars_label_defaults_to_exact(self, app):
        """Nothing has been toggled yet, so the fallback is the `=` column
        rather than nowhere."""
        async with app.run_test() as pilot:
            await pilot.press("V")
            await pilot.pause()
            table = app.screen.query_one("#ratings", DataTable)
            await pilot.press("down")
            await pilot.press("right", "right")
            await pilot.pause()
            assert table.cursor_coordinate == Coordinate(1, 2)
            await pilot.press("space")
            await pilot.pause()
            assert app.screen._current == RatingFilter(stars=1, at_least=False)
            assert "x" in str(table.get_row("1")[0])


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
        """ "--" is a rendering choice, not a name to persist."""
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


class TestUndo:
    """`z`/`Z`, the sibling editors' undo, over the favourites store.

    Built for the wrong `f` press -- the one keystroke that writes -- but the
    log is data rather than a closure, so preset editing can ride the same
    two keys later.
    """

    async def test_z_undoes_a_wrong_favourite(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("f")
            await pilot.pause()
            assert ("USER", 1) in app.favorites
            await pilot.press("z")
            await pilot.pause()
            assert ("USER", 1) not in app.favorites

    async def test_z_restores_an_unfavourited_slot(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("f")
            await pilot.press("f")
            await pilot.pause()
            assert ("USER", 1) not in app.favorites
            await pilot.press("z")
            await pilot.pause()
            assert ("USER", 1) in app.favorites

    async def test_undo_all_reverses_every_change(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("f")
            await pilot.press("down")
            await pilot.press("f")
            await pilot.pause()
            assert len(app.favorites) == 2
            await pilot.press("Z")
            await pilot.pause()
            assert len(app.favorites) == 0
            assert app._changes == []

    async def test_z_with_nothing_to_undo_says_so(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            assert app.last_status == "nothing to undo"

    async def test_the_subtitle_counts_pending_changes(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("f")
            await pilot.pause()
            assert "Δ1" in app.sub_title
            await pilot.press("z")
            await pilot.pause()
            assert "Δ" not in app.sub_title

    async def test_z_restores_the_previous_tags(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.press("f")
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("b", "a", "s", "s")
            await pilot.press("enter")
            await pilot.pause()
            assert app.favorites.get("USER", 1).tags == "bass"
            await pilot.press("z")
            await pilot.pause()
            assert app.favorites.get("USER", 1).tags == ""

    async def test_undo_restores_a_non_empty_old_value(self, app):
        """Not just "clear it": the value before the edit comes back."""
        from rxved.app import _Change

        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", tags="pad")
            app._record_change(_Change("tags", "USER", 1, old="pad", new="lead"))
            app.favorites.set_tags("USER", 1, "lead")
            app.action_undo()
            await pilot.pause()
            assert app.favorites.get("USER", 1).tags == "pad"

    async def _on_slot(self, app, pilot, number):
        """Put the cursor on a slot in the slot table, the bank's first."""
        await pilot.press("tab")
        for _ in range(number - 1):
            await pilot.press("down")
        await pilot.pause()

    async def test_asterisk_rates_the_favourite(self, app):
        """The one favourite field that had no key until now."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell", tags="lead")
            await self._on_slot(app, pilot, 1)
            await pilot.press("asterisk")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            choices = app.screen.query_one("#choices", DataTable)
            choices.cursor_coordinate = Coordinate(4, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            found = app.favorites.get("USER", 1)
            assert (found.rating, found.tags) == (4, "lead")

    async def test_the_rating_shows_as_stars_in_the_detail_line(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell")
            await self._on_slot(app, pilot, 1)
            await pilot.press("asterisk")
            await pilot.pause()
            app.screen.query_one("#choices").cursor_coordinate = Coordinate(3, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert "***" in str(app.query_one("#detail")._Static__content)

    async def test_the_rating_undoes_like_tags_and_notes(self, app):
        """The undo machinery has handled "rating" all along; nothing in the
        TUI could produce that kind of change until the key existed."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell")
            await self._on_slot(app, pilot, 1)
            await pilot.press("asterisk")
            await pilot.pause()
            app.screen.query_one("#choices").cursor_coordinate = Coordinate(5, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.favorites.get("USER", 1).rating == 5
            await pilot.press("z")
            await settle(pilot)
            assert app.favorites.get("USER", 1).rating == 0

    async def test_the_rating_refuses_a_slot_that_is_not_a_favourite(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await self._on_slot(app, pilot, 1)
            await pilot.press("asterisk")
            await pilot.pause()
            assert "not a favourite" in app.last_status
            assert app.last_status_refused


class TestSoundMode:
    """`M` -- the one write in rxved that is not to Temporary Performance.

    Only the *sound mode* axis: OM p. 39's ten modes are which-sound times
    panel state, and Edit, Part Play and DEMO have no address in the map at
    all (docs/RESOLUTION_NOTES.md §9b).
    """

    async def _mode_screen(self, app, pilot):
        await pilot.press("M")
        await pilot.pause()
        return app.screen

    async def test_M_lists_the_manual_five_by_name(self, app):
        async with app.run_test() as pilot:
            screen = await self._mode_screen(app, pilot)
            assert isinstance(screen, ChoiceScreen)
            # Rows are keyed by position, 0-4.
            choices = screen.query_one("#choices")
            rows = [str(choices.get_row(str(index))[1]) for index in range(5)]
            assert rows == ["PATCH", "PERFORM", "GM1", "GM2", "GS"]

    async def test_the_current_mode_is_marked_and_under_the_cursor(self, app):
        async with app.run_test() as pilot:
            screen = await self._mode_screen(app, pilot)
            choices = screen.query_one("#choices")
            # The demo synth is in PERFORM, which is mode 2 -- position 1.
            assert "x" in str(choices.get_row("1")[0])
            assert choices.cursor_row == 1

    async def test_patch_and_perform_are_written_without_a_confirm(self, app):
        async with app.run_test() as pilot:
            await self._mode_screen(app, pilot)
            choices = app.screen.query_one("#choices")
            choices.cursor_coordinate = Coordinate(0, 0)  # PATCH
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_setup().mode == 1
            assert "PATCH" in app.last_status

    async def test_gm_is_armed_before_it_is_fired(self, app):
        """GM initialises the sound generator and stores nothing (OM p. 96),
        so it is confirmed first like the store screen's write."""
        async with app.run_test() as pilot:
            await self._mode_screen(app, pilot)
            choices = app.screen.query_one("#choices")
            choices.cursor_coordinate = Coordinate(2, 0)  # GM1
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            assert app.bridge.read_setup().mode == 2  # nothing sent yet
            await pilot.press("n")  # cancel
            await settle(pilot)
            assert app.bridge.read_setup().mode == 2

    async def test_gm_is_written_once_confirmed(self, app):
        async with app.run_test() as pilot:
            await self._mode_screen(app, pilot)
            choices = app.screen.query_one("#choices")
            choices.cursor_coordinate = Coordinate(3, 0)  # GM2
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("y")
            await settle(pilot)
            assert app.bridge.read_setup().mode == 4

    async def test_escape_leaves_the_mode_alone(self, app):
        async with app.run_test() as pilot:
            await self._mode_screen(app, pilot)
            await pilot.press("escape")
            await settle(pilot)
            assert app.bridge.read_setup().mode == 2


class TestReadingNames:
    async def test_r_reads_a_user_bank(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_read_names()
            await settle(pilot)
            assert app.catalog.is_live("USER", 1)

    async def test_a_read_marks_names_that_disagree_with_print(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_read_names()
            await settle(pilot)
            # The demo's invented names are nothing like the fixture's.
            assert app.catalog.differs("USER", 1)


class TestTheDisagreementMarker:
    """`*` is worth a glance exactly when it is rare.

    Measured on real hardware: a USER bank somebody works in had all 128
    slots disagreeing with the printed list, and only slot 128 left holding
    the factory `INIT PATCH`. A marker on every row is arithmetically true
    and completely silent -- so when *nothing* agrees, the printed list has
    stopped describing the bank and the marker goes, with the factory name
    moved to the slot you are actually on.
    """

    async def test_one_saved_slot_still_gets_marked(self, app):
        """The case the marker exists for, and it must keep working."""
        app.catalog = cat.Catalog(
            [cat.Entry("USER", n, f"Factory {n:03d}") for n in range(1, 129)]
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            # Read the demo's names, then put slot 1 back to the factory one,
            # so exactly one slot agrees.
            app.action_read_names()
            await settle(pilot)
            app.catalog.set_live_name("USER", 1, "Factory 001")
            app._refresh_current_bank()
            await pilot.pause()
            assert not app.catalog.printed_list_void("USER", range(1, 129))

    async def test_a_fully_saved_bank_is_not_marked(self, app):
        app.catalog = cat.Catalog(
            [cat.Entry("USER", n, f"Factory {n:03d}") for n in range(1, 129)]
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_read_names()
            await settle(pilot)
            assert app.catalog.printed_list_void("USER", range(1, 129))
            assert "saved into" in app.last_status

    async def test_the_factory_name_moves_to_the_detail_line(self, app):
        """So the information is not lost -- it is just not on 128 rows."""
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_read_names()
            await settle(pilot)
            app._update_detail(0)
            await pilot.pause()
            detail = str(app.query_one("#detail", Static).render())
            assert "factory: Velvet Bell" in detail, detail
            assert app.catalog.name("USER", 1) == "Velvet Bell"

    async def test_an_unread_bank_is_not_void(self, app):
        """No evidence from the synth is not evidence of disagreement."""
        app.catalog = cat.Catalog([cat.Entry("USER", 1, "Grand XV")])
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not app.catalog.printed_list_void("USER", range(1, 129))

    async def test_r_refuses_a_rom_bank_and_says_why(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            index = banks.bank_ids().index("PST-A")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            app.action_read_names()
            await pilot.pause()
            assert "no address" in app.last_status
            assert app.last_status_refused
            assert app.bridge.selected_log == []

    async def test_reading_relabels_favourites(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 1, name="Velvet Bell")
            app.action_read_names()
            await settle(pilot)
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

    async def test_scan_runs_once_confirmed(self, patch_mode_app):
        async with patch_mode_app.run_test() as pilot:
            await pilot.pause()
            # A ROM/expansion bank: the only kind `s` is for. SRX-09-4 is the
            # smallest at 30 slots, which keeps the test quick without
            # weakening what it checks.
            index = banks.bank_ids().index("SRX-09-4")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            patch_mode_app.action_scan_bank()
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause(0.3)
            assert len(patch_mode_app.bridge.selected_log) == 30
            assert patch_mode_app.catalog.is_live("SRX-09-4", 1)

    async def test_scan_refuses_in_performance_mode(self, app):
        """Where a program change does not move the current patch.

        Verified on real hardware: an XV-2020 in PERFORM mode answered all
        128 reads with the patch it was already on, so the scan reported
        that one name 128 times. Refused up front, before the confirmation,
        rather than after playing the bank.
        """
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            index = banks.bank_ids().index("PST-A")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            app.action_scan_bank()
            await pilot.pause()
            assert app.bridge.selected_log == [], "sent program changes anyway"
            assert "PERFORM" in app.last_status
            assert app.last_status_refused
            # And no confirmation dialog was raised to be escaped from.
            assert not isinstance(app.screen, ConfirmScreen), (
                "asked for consent before refusing"
            )

    async def test_scan_refuses_a_bank_that_can_be_read_directly(self, app):
        """USER, R-USER and P-USER have addresses; `r` gets them silently.

        Scanning them instead is not merely slower -- on a synth that is not
        following the program change it reports the patch the synth is
        sitting on, once per slot, which is exactly what it did.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            index = banks.bank_ids().index("R-USER")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            app.action_scan_bank()
            await pilot.pause()
            assert app.bridge.selected_log == []
            assert "r" in app.last_status

    async def test_read_still_works_on_those_banks(self, app):
        """The refusal points somewhere real."""
        async with app.run_test() as pilot:
            await pilot.pause()
            index = banks.bank_ids().index("R-USER")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            app.action_read_names()
            await pilot.pause(0.3)
            assert app.catalog.is_live("R-USER", 1)
            assert app.bridge.selected_log == [], "reading selected something"

    async def test_srx_probe_asks_first_too(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_probe_srx()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert app.bridge.selected_log == []


class TestScanDevice:
    """`S`: the whole device, and `D`: discard and rebuild.

    The empty-catalog offer is the entry point; `S` is what it leads to.
    The user banks are silent; every SRX bank has no address in the
    parameter map, so its names can only be read by selecting each patch,
    which plays the instrument -- hence a confirmation, always.
    """

    def _app(self, tmp_path, *, catalog=None, offer_scan=True, config=None, demo=False):
        favorites = Favorites(str(tmp_path / "f.db"))
        application = RxvedApp(
            DemoBridge(),
            favorites=favorites,
            catalog=catalog if catalog is not None else cat.empty(),
            backup_dir=str(tmp_path / "backups"),
            live_names_path=str(tmp_path / "live-names.json"),
            read_banks_at_startup=False,
            config_path=config,
            demo=demo,
            offer_scan=offer_scan,
        )
        return application, favorites

    async def test_it_offers_when_there_is_no_catalog(self, tmp_path):
        application, favorites = self._app(tmp_path)
        try:
            async with application.run_test() as pilot:
                await pilot.pause(0.8)
                assert isinstance(application.screen, ScanOfferScreen)
        finally:
            favorites.close()

    async def test_it_does_not_offer_when_not_asked(self, tmp_path):
        application, favorites = self._app(tmp_path, offer_scan=False)
        try:
            async with application.run_test() as pilot:
                await pilot.pause(0.8)
                assert not isinstance(application.screen, ScanOfferScreen)
        finally:
            favorites.close()

    async def test_it_does_not_offer_when_the_catalog_knows_names(self, tmp_path):
        catalog = cat.Catalog([cat.Entry("USER", 1, "Velvet Bell")])
        application, favorites = self._app(tmp_path, catalog=catalog)
        try:
            async with application.run_test() as pilot:
                await pilot.pause(0.8)
                assert not isinstance(application.screen, ScanOfferScreen)
        finally:
            favorites.close()

    async def test_never_is_remembered(self, tmp_path):
        config = str(tmp_path / "config.toml")
        application, favorites = self._app(tmp_path, config=config)
        try:
            async with application.run_test() as pilot:
                await pilot.pause(0.8)
                assert isinstance(application.screen, ScanOfferScreen)
                await pilot.press("d")
                await pilot.pause(0.2)
        finally:
            favorites.close()
        assert load_ui_state(config)["scan_offer_dismissed"] is True

        second, second_favorites = self._app(tmp_path, config=config)
        try:
            async with second.run_test() as pilot:
                await pilot.pause(0.8)
                assert not isinstance(second.screen, ScanOfferScreen)
        finally:
            second_favorites.close()

    async def test_escape_defers_without_remembering(self, tmp_path):
        config = str(tmp_path / "config.toml")
        application, favorites = self._app(tmp_path, config=config)
        try:
            async with application.run_test() as pilot:
                await pilot.pause(0.8)
                assert isinstance(application.screen, ScanOfferScreen)
                await pilot.press("escape")
                await pilot.pause(0.2)
        finally:
            favorites.close()
        assert "scan_offer_dismissed" not in load_ui_state(config)

    async def test_answering_scan_starts_a_device_scan(self, tmp_path):
        application, favorites = self._app(tmp_path)
        try:
            async with application.run_test() as pilot:
                await pilot.pause(0.8)
                assert isinstance(application.screen, ScanOfferScreen)
                await pilot.press("y")
                await pilot.pause(0.2)
                # The user banks are silent but the SRX banks play, so the
                # offer hands off to the same confirmation `S` shows.
                assert isinstance(application.screen, ConfirmScreen)
                await pilot.press("y")
                await pilot.pause(0.2)
                assert isinstance(application.screen, ScanScreen)
                # A silent read of the writable banks is what the offer
                # promises first, and the demo bridge answers it.
                await settle(pilot, seconds=15)
                assert application.catalog.is_live("USER", 1)
                assert not isinstance(application.screen, ScanScreen)
        finally:
            favorites.close()

    async def test_S_on_the_demo_is_refused(self, tmp_path):
        """The demo invents its names; scanning it would write fiction."""
        application, favorites = self._app(tmp_path, offer_scan=False, demo=True)
        try:
            async with application.run_test() as pilot:
                await pilot.pause()
                await pilot.press("S")
                await pilot.pause(0.1)
                assert application.last_status_refused
                assert application.bridge.selected_log == []
        finally:
            favorites.close()

    async def test_D_asks_twice_and_cancelling_changes_nothing(self, tmp_path):
        catalog = cat.Catalog([cat.Entry("USER", 1, "Velvet Bell")])
        application, favorites = self._app(tmp_path, catalog=catalog, offer_scan=False)
        try:
            async with application.run_test() as pilot:
                await pilot.pause()
                await pilot.press("D")
                await pilot.pause(0.1)
                assert isinstance(application.screen, ConfirmScreen)
                await pilot.press("n")
                await pilot.pause(0.1)
                assert not isinstance(application.screen, ConfirmScreen)
                assert application.catalog.name("USER", 1) == "Velvet Bell"
        finally:
            favorites.close()

    async def test_D_empties_the_catalog_after_both_confirmations(self, tmp_path):
        catalog = cat.Catalog([cat.Entry("USER", 1, "Velvet Bell")])
        application, favorites = self._app(tmp_path, catalog=catalog, offer_scan=False)
        try:
            async with application.run_test() as pilot:
                await pilot.pause()
                await pilot.press("D")
                await pilot.pause(0.1)
                assert isinstance(application.screen, ConfirmScreen)
                await pilot.press("y")
                await pilot.pause(0.1)
                # The second screen is the one that means it.
                assert isinstance(application.screen, ConfirmScreen)
                assert application.catalog.name("USER", 1) == "Velvet Bell"
                await pilot.press("y")
                await pilot.pause(0.2)
                assert not application.catalog
                assert application.catalog.display_name("USER", 1) == cat.UNNAMED
        finally:
            favorites.close()


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

    async def test_a_scan_restores_the_previous_patch(self, patch_mode_app):
        async with patch_mode_app.run_test() as pilot:
            await pilot.pause(0.4)
            # PST-A, a preset bank -- the kind of bank a scan is for.
            index = banks.bank_ids().index("PST-A")
            for _ in range(index):
                await pilot.press("down")
            await pilot.pause()
            patch_mode_app.action_scan_bank()
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause(0.5)
            assert patch_mode_app.bridge.raw_log, "scan left the synth where it stopped"

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
            body = app.screen.body
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
            app.action_read_names()
            await settle(pilot)
            assert app.query_one("#slot-table").cursor_row == 20
            assert app._current_bank == "USER"


class TestFavouritesView:
    """`v` filters the real table, in two steps, and stays playable.

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
            await pilot.press("v")
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
            await pilot.press("v")
            await pilot.pause(0.2)
            await pilot.press("v")
            await pilot.pause(0.3)
            assert app.view_mode == "all-favourites"
            assert {s.bank_id for s in app._current_slots} == {"USER", "PST-B", "GM"}

    async def test_the_all_favourites_view_orders_banks_like_the_left_pane(self, app):
        """USER first, because the left pane puts it first.

        The view used `favorites.all(order="bank")`, which is
        `ORDER BY bank_id` -- and U sorts after P, so every preset bank came
        before the bank you actually work in. The two halves of one screen
        disagreed about which bank leads, and a user cannot tell which of them
        is the wrong one.
        """
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("PST-B", 29)
            app.favorites.add("USER", 1)
            app.favorites.add("PST-A", 1)
            app._fill_slots(app._current_bank)
            await pilot.press("v")
            await pilot.pause(0.2)
            await pilot.press("v")
            await pilot.pause(0.3)
            assert app.view_mode == "all-favourites"
            position = {bank_id: i for i, bank_id in enumerate(app._bank_ids)}
            seen = [(position[s.bank_id], s.number) for s in app._current_slots]
            assert seen == sorted(seen)
            assert app._current_slots[0].bank_id == "USER"
            assert {s.bank_id for s in app._current_slots} <= set(app._bank_ids)

    async def test_a_third_press_returns_to_the_full_bank(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("USER", 1)
            for _ in range(3):
                await pilot.press("v")
                await pilot.pause(0.2)
            assert app.view_mode == "all"
            assert app.query_one("#slot-table").row_count == 128

    async def test_a_slot_can_be_selected_from_the_filtered_list(self, app):
        """The whole reason this is a filter and not a report."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("PST-B", 29)
            await pilot.press("v")
            await pilot.pause(0.2)
            await pilot.press("v")
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
            await pilot.press("v")
            await pilot.pause(0.2)
            await pilot.press("v")
            await pilot.pause(0.3)
            assert str(table.columns["num"].label) == "bank / #"

    async def test_un_favouriting_removes_the_row_from_a_filtered_view(self, app):
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await self._mark(app, pilot, [1, 4])
            await pilot.press("v")
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
            await pilot.press("v")
            await pilot.pause(0.2)
            await pilot.press("v")
            await pilot.pause(0.3)
            assert app.query_one("#slot-table").row_count == 2

    async def test_an_empty_view_explains_itself(self, app):
        """F is always three steps; an empty one says so rather than jumping."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            await pilot.press("v")
            await pilot.pause(0.3)
            assert app.view_mode == "bank-favourites"
            assert app.query_one("#slot-table").row_count == 0
            assert "no favourites in this bank" in app.last_status

    async def test_the_step_is_the_same_whatever_is_favourited(self, app):
        """One keypress must land in the same view regardless of data."""
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.favorites.add("PST-B", 29)  # nothing in the current bank
            await pilot.press("v")
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
        return cat.Catalog(
            [
                cat.Entry("USER", 1, "Velvet Bell", category="BEL"),
                cat.Entry("USER", 2, "Rusty Pad", category="SPD"),
                cat.Entry("USER", 3, "Glass Pad", category="SPD"),
                cat.Entry("USER", 4, "Iron Bass", category="SBS"),
            ]
        )

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
            assert not isinstance(app.screen, CategoryScreen), (
                "enter did not close the picker"
            )
            assert app.categories == {"SPD"}
            assert [s.number for s in app._current_slots] == [2, 3]

    async def test_selecting_everything_is_stored_as_no_filter(self, app):
        app.catalog = self._catalog()
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            app.categories = {"SPD"}  # start from a real filter, so
            app._fill_slots("USER")  # clearing it is an observable change
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


class TestMultiSetup:
    """The multi-mode window, and the diagnosis under it.

    The diagnosis is the part worth testing: the obvious reading of a silent
    channel is "the part is muted", and two of the real causes are nowhere
    near the part. One cause is not readable at all, and saying so is a
    required part of the report rather than a caveat.
    """

    async def test_m_opens_the_window(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, MultiScreen)
            table = app.screen.query_one("#part-table", DataTable)
            assert table.row_count == 16

    async def test_it_reports_a_part_with_its_receive_switch_off(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            state = app.bridge.read_state(with_parts=True)
            report = " ".join(state.silence_report())
            assert "RX SWITCH is OFF" in report

    async def test_it_reports_a_part_at_zero_level(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            state = app.bridge.read_state(with_parts=True)
            assert "LEVEL is 0" in " ".join(state.silence_report())

    async def test_the_unreadable_mute_switch_is_always_named(self, app):
        """Never let the report read as exhaustive. It cannot be."""
        async with app.run_test() as pilot:
            await pilot.pause()
            state = app.bridge.read_state(with_parts=True)
            assert "Mute Switch" in " ".join(state.silence_report())


class TestPerformancePicker:
    """`p` in the multi screen: browse the 96 stored performances, load one.

    Loading replaces every byte of the edit buffer, which is why it is a
    screen of its own rather than a keystroke on the part table.
    """

    async def _multi(self, app, pilot):
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        return app.screen

    async def test_p_opens_the_picker_over_the_multi_screen(self, app):
        async with app.run_test() as pilot:
            await self._multi(app, pilot)
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, PerformanceScreen)
            table = app.screen.query_one("#perf-table", DataTable)
            # 64 user + 32 preset A + 32 preset B.
            assert table.row_count == 128

    async def test_the_picker_opens_on_the_current_performance(self, app):
        async with app.run_test() as pilot:
            await self._multi(app, pilot)
            await pilot.press("p")
            await pilot.pause()
            table = app.screen.query_one("#perf-table", DataTable)
            # The demo synth says it is on P-USER 005 (MSB 85, LSB 0, PC 4),
            # which is row 4 of the picker.
            assert table.cursor_row == 4

    async def test_choosing_a_performance_loads_it(self, app):
        async with app.run_test() as pilot:
            await self._multi(app, pilot)
            await pilot.press("p")
            await pilot.pause()
            table = app.screen.query_one("#perf-table", DataTable)
            # Row 64 (0-based) is P-PST-A 001.
            table.cursor_coordinate = Coordinate(64, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.selected.bank_id == "P-PST-A"
            assert app.bridge.selected.number == 1
            # Back on the part table, showing what was loaded.
            assert isinstance(app.screen, MultiScreen)

    async def test_escape_closes_the_picker_without_loading(self, app):
        async with app.run_test() as pilot:
            await self._multi(app, pilot)
            await pilot.press("p")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MultiScreen)
            assert app.bridge.selected is None


class TestPerformanceCommon:
    """`c` in the multi screen: the six settings that are not per-part."""

    async def _common(self, app, pilot):
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        return app.screen

    async def test_c_opens_the_common_editor(self, app):
        async with app.run_test() as pilot:
            screen = await self._common(app, pilot)
            assert isinstance(screen, CommonScreen)
            table = screen.query_one("#common-table", DataTable)
            assert table.row_count == 6

    async def test_editing_solo_writes_it(self, app):
        async with app.run_test() as pilot:
            screen = await self._common(app, pilot)
            table = screen.query_one("#common-table", DataTable)
            # Row 1 (0-based) is Solo Part Select.
            table.cursor_coordinate = Coordinate(1, 0)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # A solo is OFF or a part, so it is a list to pick from.
            assert isinstance(app.screen, ChoiceScreen)
            choices = app.screen.query_one("#choices", DataTable)
            assert str(choices.get_row("0")[1]) == "OFF"
            assert str(choices.get_row("3")[1]) == "PART 3"
            choices.cursor_coordinate = Coordinate(3, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_performance_common().solo == 3

    async def test_editing_the_name_writes_it(self, app):
        async with app.run_test() as pilot:
            screen = await self._common(app, pilot)
            table = screen.query_one("#common-table", DataTable)
            # Row 0 is the name; the prompt is seeded with "Demo Multi".
            table.cursor_coordinate = Coordinate(0, 0)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("!")
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_performance_common().name == "Demo Multi!"

    async def test_plus_steps_a_ranged_value_up(self, app):
        async with app.run_test() as pilot:
            screen = await self._common(app, pilot)
            table = screen.query_one("#common-table", DataTable)
            table.cursor_coordinate = Coordinate(1, 0)  # Solo Part Select
            await pilot.pause()
            await pilot.press("plus")
            await settle(pilot)
            assert app.bridge.read_performance_common().solo == 1

    async def test_minus_will_not_step_below_zero(self, app):
        async with app.run_test() as pilot:
            screen = await self._common(app, pilot)
            table = screen.query_one("#common-table", DataTable)
            table.cursor_coordinate = Coordinate(1, 0)  # Solo, already OFF
            await pilot.pause()
            await pilot.press("minus")
            await settle(pilot)
            # Refused before the write, so nothing to read back: still OFF.
            assert app.bridge.read_performance_common().solo is None

    async def test_a_digit_opens_the_prompt_holding_it(self, app):
        async with app.run_test() as pilot:
            screen = await self._common(app, pilot)
            table = screen.query_one("#common-table", DataTable)
            table.cursor_coordinate = Coordinate(1, 0)  # Solo Part Select
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            assert isinstance(app.screen, TextPromptScreen)
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_performance_common().solo == 4

    async def test_the_name_has_no_range_to_step(self, app):
        async with app.run_test() as pilot:
            screen = await self._common(app, pilot)
            table = screen.query_one("#common-table", DataTable)
            table.cursor_coordinate = Coordinate(0, 0)  # Name
            await pilot.pause()
            await pilot.press("plus")
            await pilot.pause()
            # No prompt and no write: the name is still what it was.
            assert isinstance(app.screen, CommonScreen)
            assert app.bridge.read_performance_common().name == "Demo Multi"


class TestEffects:
    """`e` in the multi screen: the MFX, chorus and reverb heads."""

    async def _effects(self, app, pilot):
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        return app.screen

    async def test_e_opens_the_effects_editor(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            assert isinstance(screen, EffectsScreen)
            table = screen.query_one("#fx-table", DataTable)
            keys = [c.key.value for c in table.columns.values()]
            assert "block" in keys and "value" in keys

    async def test_the_types_are_shown_by_name(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            # The demo's MFX type is 12, which the manual calls TREMOLO
            # CHORUS; both block types are the model's single algorithm.
            assert str(table.get_row("mfx:00")[2]) == "TREMOLO CHORUS"
            assert str(table.get_row("chorus:00")[2]) == "CHORUS"
            assert str(table.get_row("reverb:00")[2]) == "REVERB"

    async def test_editing_the_mfx_type_writes_it(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            table.cursor_coordinate = Coordinate(0, 0)  # MFX Type
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # A type is a list of algorithms, not a number to type.
            assert isinstance(app.screen, ChoiceScreen)
            choices = app.screen.query_one("#choices", DataTable)
            # The current type is marked, and under the cursor.
            assert "x" in str(choices.get_row("12")[0])
            assert choices.cursor_row == 12
            choices.cursor_coordinate = Coordinate(24, 0)  # REVERB
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_performance_fx().mfx_type == 24

    async def test_a_numeric_row_still_opens_the_number_prompt(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            table.cursor_coordinate = Coordinate(table.get_row_index("chorus:08"), 0)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # Depth is a plain 0-127 count: a number, not a list.
            assert isinstance(app.screen, TextPromptScreen)

    async def test_the_reverb_character_opens_the_chooser(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            table.cursor_coordinate = Coordinate(table.get_row_index("reverb:03"), 0)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            choices = app.screen.query_one("#choices", DataTable)
            assert str(choices.get_row("0")[1]) == "ROOM1"
            choices.cursor_coordinate = Coordinate(6, 0)  # DELAY
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            fx = app.bridge.read_performance_fx()
            assert params.decode_int4x4(fx.reverb_block[0x03:0x07]) == 32768 + 6

    async def test_a_control_sensitivity_is_written_biased(self, app):
        """The manual prints -63..+63; the wire carries 1-127."""
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            table.cursor_coordinate = Coordinate(6, 0)  # MFX Control 1 Sens
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("-", "1", "0")
            await pilot.press("enter")
            await settle(pilot)
            # 64 + (-10) on the wire.
            assert app.bridge.read_performance_fx().mfx_control_sens[0] == 54

    async def test_minus_steps_a_value(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            table.cursor_coordinate = Coordinate(1, 0)  # MFX Dry Send, 127
            await pilot.pause()
            await pilot.press("minus")
            await settle(pilot)
            assert app.bridge.read_performance_fx().mfx_dry == 126

    async def test_the_chorus_parameters_use_the_display_tables(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            # Every parameter is at display zero, which is the first entry
            # of its table -- not the number 0.
            assert str(table.get_row("chorus:04")[2]) == "0.05 Hz"
            assert str(table.get_row("chorus:0c")[2]) == "0.0 ms"
            assert str(table.get_row("chorus:18")[2]) == "200 Hz"
            assert str(table.get_row("reverb:03")[2]) == "ROOM1"

    async def test_a_chorus_parameter_writes_four_nibbles(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            table.cursor_coordinate = Coordinate(table.get_row_index("chorus:04"), 0)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # RATE is a list of the manual's frequencies, so pick the sixth.
            assert isinstance(app.screen, ChoiceScreen)
            choices = app.screen.query_one("#choices", DataTable)
            choices.cursor_coordinate = Coordinate(5, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            fx = app.bridge.read_performance_fx()
            # Display 5 is wire 32773, and the table's sixth rate.
            assert params.decode_int4x4(fx.chorus_block[0x04:0x08]) == 32773
            assert str(table.get_row("chorus:04")[2]) == "0.30 Hz"

    async def test_a_reverb_parameter_steps_with_plus(self, app):
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            table.cursor_coordinate = Coordinate(table.get_row_index("reverb:07"), 0)
            await pilot.pause()
            await pilot.press("plus")
            await settle(pilot)
            fx = app.bridge.read_performance_fx()
            assert params.decode_int4x4(fx.reverb_block[0x07:0x0B]) == 32769

    async def test_the_mfx_parameters_follow_the_algorithm(self, app):
        """The MFX's parameter list belongs to its type, so rows change."""
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            # Type 12 is TREMOLO CHORUS, whose 0x1d is its Tremolo Rate.
            assert str(table.get_row("mfx:1d")[1]) == "Tremolo Rate"
            table.cursor_coordinate = Coordinate(0, 0)  # MFX Type
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            choices = app.screen.query_one("#choices", DataTable)
            choices.cursor_coordinate = Coordinate(4, 0)  # PHASER
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_performance_fx().mfx_type == 4
            # PHASER's 0x1d is its Resonance.
            assert str(table.get_row("mfx:1d")[1]) == "Resonance"

    async def test_a_worded_mfx_parameter_opens_the_chooser(self, app):
        """STEREO CHORUS's Filter is OFF/LPF/HPF, not a number 0-2."""
        async with app.run_test() as pilot:
            screen = await self._effects(app, pilot)
            table = screen.query_one("#fx-table", DataTable)
            # Switch to STEREO CHORUS (type 14).
            table.cursor_coordinate = Coordinate(0, 0)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            choices = app.screen.query_one("#choices", DataTable)
            choices.cursor_coordinate = Coordinate(14, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_performance_fx().mfx_type == 14
            table.cursor_coordinate = Coordinate(table.get_row_index("mfx:11"), 0)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            options = app.screen.query_one("#choices", DataTable)
            assert str(options.get_row("0")[1]) == "OFF"
            assert str(options.get_row("1")[1]) == "LPF"


class TestPatchPicker:
    """`s` in the multi screen: pick a part's sound by bank and name."""

    async def _picker(self, app, pilot):
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        return app.screen

    async def test_s_opens_the_patch_picker(self, app):
        async with app.run_test() as pilot:
            screen = await self._picker(app, pilot)
            assert isinstance(screen, PatchPickerScreen)
            table = screen.query_one("#picker-banks", DataTable)
            assert table.row_count > 0

    async def test_the_first_bank_shows_its_slots(self, app):
        async with app.run_test() as pilot:
            screen = await self._picker(app, pilot)
            slots = screen.query_one("#picker-slots", DataTable)
            # The first bank is USER, which holds 128 patches.
            assert slots.row_count == 128

    async def test_picking_a_slot_sets_the_part(self, app):
        async with app.run_test() as pilot:
            screen = await self._picker(app, pilot)
            # The picker opens on the bank pane; tab moves to the slots.
            await pilot.press("tab")
            await pilot.pause()
            slots = screen.query_one("#picker-slots", DataTable)
            # Row 1 (0-based) is USER 002: MSB 87, LSB 0, PC 1.
            slots.cursor_coordinate = Coordinate(1, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            part = app.bridge.read_part(1)
            assert (part.msb, part.lsb, part.program_change) == (87, 0, 1)

    async def test_v_cycles_the_same_favourites_views_as_the_browser(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("USER", 2)
            app.favorites.add("USER", 5)
            app.favorites.add("PST-B", 29)
            screen = await self._picker(app, pilot)
            slots = screen.query_one("#picker-slots", DataTable)
            assert slots.row_count == 128  # all of USER
            await pilot.press("v")
            await pilot.pause()
            assert slots.row_count == 2  # this bank's favourites
            await pilot.press("v")
            await pilot.pause()
            assert slots.row_count == 3  # every favourite

    async def test_the_view_survives_closing_and_reopening_the_picker(self, app):
        """`v`, then `q`, then `s` comes back on the same view.

        The view belongs to the `m` session, so it outlives the picker that
        carried it -- the bug being that a fresh picker reset it to "all
        slots".
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("PST-B", 29)
            screen = await self._picker(app, pilot)
            await pilot.press("v")
            await pilot.press("v")
            await pilot.pause()
            assert screen.view == 2  # every favourite
            await pilot.press("q")  # leave the picker
            await pilot.pause()
            await pilot.press("s")  # open it again, same session
            await pilot.pause()
            reopened = app.screen
            assert isinstance(reopened, PatchPickerScreen)
            assert reopened.view == 2
            # The index is not all that survived: the view it names is shown.
            assert reopened.query_one("#picker-slots", DataTable).row_count == 1

    async def test_the_cursors_survive_closing_and_reopening_the_picker(self, app):
        """Both panes' cursors are kept for the session, not just the view.

        The bank and slot are remembered by key, not row number: the row at
        a given index is a different sound in another view, and a key names
        the sound wherever it sits.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await self._picker(app, pilot)
            banks_table = screen.query_one("#picker-banks", DataTable)
            # Row 3 is PST-C, a 128-slot bank -- deep enough to move within.
            banks_table.move_cursor(row=3)
            await pilot.pause()
            await pilot.press("tab")  # focus the slots
            await pilot.pause()
            screen.query_one("#picker-slots", DataTable).move_cursor(row=5)
            await pilot.pause()
            remembered = screen.position
            assert remembered.bank == "PST-C"
            assert remembered.focus == "picker-slots"
            await pilot.press("q")  # leave the picker
            await pilot.pause()
            await pilot.press("s")  # open it again, same session
            await pilot.pause()
            reopened = app.screen
            assert isinstance(reopened, PatchPickerScreen)
            assert reopened.position == remembered
            assert reopened.query_one("#picker-banks", DataTable).cursor_row == 3
            assert reopened.query_one("#picker-slots", DataTable).cursor_row == 5

    async def test_a_new_multi_session_starts_the_picker_fresh(self, app):
        """The only other half of the rule: nothing is persisted.

        A view and cursors kept across reopenings within one `m` session
        must not leak into the next, or "kept in the session" would really
        be "stored".
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await self._picker(app, pilot)
            await pilot.press("v")
            await pilot.pause()
            assert screen.view == 1
            screen.query_one("#picker-banks", DataTable).move_cursor(row=3)
            await pilot.pause()
            await pilot.press("q")  # leave the picker
            await pilot.pause()
            await pilot.press("q")  # leave the multi screen
            await pilot.pause()
            await pilot.press("m")  # a new session
            await pilot.pause()
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            reopened = app.screen
            assert isinstance(reopened, PatchPickerScreen)
            assert reopened.view == 0  # back to "all slots"
            assert reopened.query_one("#picker-banks", DataTable).cursor_row == 0

    async def test_a_favourite_can_be_picked_across_banks(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.favorites.add("PST-B", 29)
            screen = await self._picker(app, pilot)
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("v")
            await pilot.pause()
            await pilot.press("tab")  # focus the slots
            await pilot.pause()
            slots = screen.query_one("#picker-slots", DataTable)
            slots.cursor_coordinate = Coordinate(0, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            chosen = banks.slot("PST-B", 29)
            part = app.bridge.read_part(1)
            assert (part.msb, part.lsb, part.program_change) == (
                chosen.msb,
                chosen.lsb,
                chosen.program_change,
            )


class TestMultiEditing:
    """The one writable screen in the program.

    Two things are worth pinning down beyond "the value changed". The screen
    displays receive channel 1-16 and the wire carries 0-15, so an edit that
    skipped the conversion would be off by one in a way that still looks
    plausible. And a DT1 is unacknowledged, so the row must show what the
    device reports rather than what was sent.
    """

    async def _open(self, app, pilot):
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        return app.screen

    async def _cell(self, screen, pilot, part, column):
        table = screen.query_one("#part-table", DataTable)
        keys = [c.key.value for c in table.columns.values()]
        table.cursor_coordinate = Coordinate(part - 1, keys.index(column))
        await pilot.pause()
        return table

    async def test_space_toggles_receive_switch_off_and_on(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 1, "rx")
            assert app.bridge.read_part(1).receive_switch is True
            await pilot.press("space")
            await settle(pilot)
            assert app.bridge.read_part(1).receive_switch is False
            await pilot.press("space")
            await settle(pilot)
            assert app.bridge.read_part(1).receive_switch is True

    async def test_it_can_switch_a_silenced_part_back_on(self, app):
        """The case this was built for: part 5 starts with rx off."""
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            assert app.bridge.read_part(5).receive_switch is False
            await self._cell(screen, pilot, 5, "rx")
            await pilot.press("space")
            await settle(pilot)
            assert app.bridge.read_part(5).receive_switch is True

    async def test_the_channel_column_converts_to_the_wire(self, app):
        """1-16 on screen, 0-15 on the wire, and never confused."""
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 3, "ch")
            # Part 3 starts on wire channel 2, shown as 3.
            assert app.bridge.read_part(3).receive_channel == 2
            await pilot.press("minus")
            await settle(pilot)
            # Display 3 -> 2, wire 2 -> 1.
            assert app.bridge.read_part(3).receive_channel == 1

    async def test_bump_respects_the_range(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 6, "lvl")
            assert app.bridge.read_part(6).level == 0
            await pilot.press("minus")  # already at the floor
            await settle(pilot)
            assert app.bridge.read_part(6).level == 0

    async def test_the_row_shows_what_the_device_reports(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            table = await self._cell(screen, pilot, 5, "rx")
            await pilot.press("space")
            await settle(pilot)
            keys = [c.key.value for c in table.columns.values()]
            row = table.get_row("5")
            assert "on" in str(row[keys.index("rx")])
            # ...and the report no longer lists part 5 as switched off.
            assert "Part  5" not in screen._report_text()

    async def test_a_non_editable_column_is_refused_not_ignored(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 1, "part")
            await pilot.press("enter")
            await pilot.pause()
            # Still the multi screen: no prompt opened.
            assert isinstance(app.screen, MultiScreen)

    async def test_typing_a_digit_opens_the_prompt_holding_it(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 1, "lvl")
            await pilot.press("9")
            await pilot.pause()
            assert isinstance(app.screen, TextPromptScreen)
            field = app.screen.query_one("#value", Input)
            assert field.value == "9"
            # Cursor at the end, so the next digit continues the number.
            assert field.cursor_position == 1

    async def test_a_typed_number_is_written(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 1, "lvl")
            assert app.bridge.read_part(1).level == 100
            await pilot.press("1", "2")
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_part(1).level == 12

    async def test_enter_seeds_the_prompt_with_the_current_value(self, app):
        """Enter means "change this one", so start from what is there."""
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 1, "lvl")
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen.query_one("#value", Input).value == "100"

    async def test_digits_set_receive_switch_directly(self, app):
        """rx holds one bit: there is nothing to type into."""
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 5, "rx")
            assert app.bridge.read_part(5).receive_switch is False
            await pilot.press("1")
            await settle(pilot)
            assert isinstance(app.screen, MultiScreen)  # no prompt opened
            assert app.bridge.read_part(5).receive_switch is True

    async def test_an_out_of_range_number_is_refused_not_clamped(self, app):
        """Clamping would report success for a value never written."""
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 1, "ch")
            before = app.bridge.read_part(1).receive_channel
            await pilot.press("9", "9")
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_part(1).receive_channel == before

    async def test_digits_do_nothing_on_a_non_editable_cell(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await self._cell(screen, pilot, 1, "part")
            await pilot.press("7")
            await pilot.pause()
            assert isinstance(app.screen, MultiScreen)

    async def test_enter_on_a_named_column_opens_the_chooser(self, app):
        """Mono/Poly and the routing columns are words, not numbers."""
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            for _ in range(3):
                await pilot.press("tab")  # tone
            await pilot.pause()
            await self._cell(screen, pilot, 1, "mono")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            choices = app.screen.query_one("#choices", DataTable)
            assert str(choices.get_row("0")[1]) == "MONO"
            choices.cursor_coordinate = Coordinate(0, 0)
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_part(1).mono_poly == 0

    async def test_tab_switches_to_the_fx_columns(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            table = screen.query_one("#part-table", DataTable)
            labels = [str(c.label) for c in table.columns.values()]
            assert labels == [
                "part",
                "ch",
                "rx",
                "lvl",
                "PC",
                "LSB",
                "MSB",
                "patch",
                "",
            ]
            await pilot.press("tab")
            await pilot.pause()
            labels = [str(c.label) for c in table.columns.values()]
            assert labels == [
                "part",
                "mute",
                "dry",
                "cho",
                "rev",
                "out",
                "mfx",
                "patch",
                "",
            ]

    async def test_the_fx_view_keeps_the_patch_and_the_verdict(self, app):
        """Both views answer "what is this part" and "can it be heard"."""
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await pilot.press("tab")
            await pilot.pause()
            table = screen.query_one("#part-table", DataTable)
            keys = [c.key.value for c in table.columns.values()]
            assert keys[-2:] == ["patch", "flag"]
            # Part 7 is muted in the demo, and the flag says which cause.
            assert "MUTE" in str(table.get_row("7")[keys.index("flag")])

    async def test_mute_can_be_toggled_from_the_fx_view(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await pilot.press("tab")
            await pilot.pause()
            await self._cell(screen, pilot, 7, "mute")
            assert app.bridge.read_part(7).mute is True
            await pilot.press("space")
            await settle(pilot)
            assert app.bridge.read_part(7).mute is False

    async def test_a_send_level_can_be_typed(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            await pilot.press("tab")
            await pilot.pause()
            await self._cell(screen, pilot, 1, "rev")
            await pilot.press("6", "4")
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_part(1).reverb == 64

    async def test_the_effects_summary_is_shown(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            summary = screen._fx_summary()
            assert "MFX type 12" in summary
            assert "CHORUS ON" in summary
            assert "REVERB ON" in summary

    async def test_tab_cycles_every_view(self, app):
        async with app.run_test() as pilot:
            screen = await self._open(app, pilot)
            table = screen.query_one("#part-table", DataTable)

            def keys():
                return [c.key.value for c in table.columns.values()]

            assert "rx" in keys() and "lvl" in keys()
            await pilot.press("tab")
            await pilot.pause()
            assert "mute" in keys() and "cho" in keys()
            await pilot.press("tab")
            await pilot.pause()
            assert "rx_pc" in keys() and "rx_bs" in keys()
            await pilot.press("tab")
            await pilot.pause()
            assert "pan" in keys() and "lo" in keys()
            await pilot.press("tab")
            await pilot.pause()
            assert "cof" in keys() and "vrt" in keys()
            await pilot.press("tab")
            await pilot.pause()
            assert "leg" in keys() and "kfu" in keys()
            await pilot.press("tab")
            await pilot.pause()
            assert "vres" in keys()
            await pilot.press("tab")
            await pilot.pause()
            assert "rx" in keys() and "lvl" in keys()  # back to the start

    async def _offsets_view(self, app, pilot):
        screen = await self._open(app, pilot)
        for _ in range(4):
            await pilot.press("tab")
        await pilot.pause()
        return screen

    async def test_a_cutoff_offset_edit_is_written_biased(self, app):
        """The screen shows -64..+63; the wire byte is 0..127."""
        async with app.run_test() as pilot:
            screen = await self._offsets_view(app, pilot)
            await self._cell(screen, pilot, 1, "cof")
            await pilot.press("enter")
            await pilot.pause()
            # The prompt is seeded with the display value, "0" here.
            await pilot.press("-", "3", "2")
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_part(1).cutoff_offset == 32  # 64 - 32

    async def _reserve_view(self, app, pilot):
        screen = await self._open(app, pilot)
        for _ in range(6):
            await pilot.press("tab")
        await pilot.pause()
        return screen

    async def test_voice_reserve_writes_to_the_common_block(self, app):
        """Per-part in meaning, but stored at common offset 00 10 + part - 1."""
        async with app.run_test() as pilot:
            screen = await self._reserve_view(app, pilot)
            await self._cell(screen, pilot, 3, "vres")
            await pilot.press("enter")
            await pilot.pause()
            # The prompt is seeded with the current value, "0", selected.
            await pilot.press("5")
            await pilot.press("enter")
            await settle(pilot)
            reserves = app.bridge.read_performance_common().voice_reserves
            assert reserves[2] == 5  # part 3
            assert reserves[0] == 0  # part 1 untouched

    async def test_voice_reserve_steps_with_plus(self, app):
        async with app.run_test() as pilot:
            screen = await self._reserve_view(app, pilot)
            await self._cell(screen, pilot, 1, "vres")
            await pilot.press("plus")
            await settle(pilot)
            assert app.bridge.read_performance_common().voice_reserves[0] == 1

    async def _porta_view(self, app, pilot):
        screen = await self._open(app, pilot)
        for _ in range(5):
            await pilot.press("tab")
        await pilot.pause()
        return screen

    async def test_portamento_time_is_editable(self, app):
        """The one part parameter that is two nibbles, not one byte."""
        async with app.run_test() as pilot:
            screen = await self._porta_view(app, pilot)
            await self._cell(screen, pilot, 1, "ptt")
            await pilot.press("enter")
            await pilot.pause()
            # Seeded with the current value, 128 (PATCH), selected.
            await pilot.press("1", "0", "0")
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_part(1).portamento_time == 100

    async def test_portamento_time_shows_patch(self, app):
        """128 is not a time; the manual prints it PATCH (OM p. 149)."""
        async with app.run_test() as pilot:
            screen = await self._porta_view(app, pilot)
            table = screen.query_one("#part-table", DataTable)
            keys = [c.key.value for c in table.columns.values()]
            row = table.get_row("1")
            assert str(row[keys.index("ptt")]) == "PATCH"

    async def _rx_view(self, app, pilot):
        screen = await self._open(app, pilot)
        await pilot.press("tab", "tab")
        await pilot.pause()
        return screen

    async def test_a_channel_that_ignores_bank_select_is_shown(self, app):
        """The demo's channel 3 drops Bank Select. Part 3 listens there."""
        async with app.run_test() as pilot:
            screen = await self._rx_view(app, pilot)
            table = screen.query_one("#part-table", DataTable)
            keys = [c.key.value for c in table.columns.values()]
            row = table.get_row("3")
            assert "OFF" in str(row[keys.index("rx_bs")])

    async def test_toggling_receive_bank_select_writes_to_the_channel(self, app):
        async with app.run_test() as pilot:
            screen = await self._rx_view(app, pilot)
            await self._cell(screen, pilot, 3, "rx_bs")
            assert app.bridge.read_performance_midi(2).bank_select is False
            await pilot.press("space")
            await settle(pilot)
            assert app.bridge.read_performance_midi(2).bank_select is True

    async def test_a_channel_write_updates_every_part_sharing_it(self, app):
        """Parts 1 and 2 share channel 1, and share these settings."""
        async with app.run_test() as pilot:
            screen = await self._rx_view(app, pilot)
            await self._cell(screen, pilot, 1, "rx_pc")
            await pilot.press("space")
            await settle(pilot)
            table = screen.query_one("#part-table", DataTable)
            keys = [c.key.value for c in table.columns.values()]
            first = str(table.get_row("1")[keys.index("rx_pc")])
            second = str(table.get_row("2")[keys.index("rx_pc")])
            assert first == second == "[b]OFF[/b]"

    async def _tone_view(self, app, pilot):
        screen = await self._open(app, pilot)
        await pilot.press("tab", "tab", "tab")
        await pilot.pause()
        return screen

    async def test_pan_is_written_through_the_bias(self, app):
        """Display -64..63, wire 0..127. Off by 64 would still look sane."""
        async with app.run_test() as pilot:
            screen = await self._tone_view(app, pilot)
            await self._cell(screen, pilot, 1, "pan")
            # Enter, not a digit: `-` steps down, so a negative value is
            # typed over the pre-selected current one.
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("-", "2", "0")
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_part(1).pan == 44  # -20 + 64

    async def test_key_ranges_show_note_names(self, app):
        async with app.run_test() as pilot:
            screen = await self._tone_view(app, pilot)
            table = screen.query_one("#part-table", DataTable)
            keys = [c.key.value for c in table.columns.values()]
            # Demo part 2 starts at note 60.
            assert str(table.get_row("2")[keys.index("lo")]) == "C4"

    async def test_mono_poly_shows_its_name(self, app):
        async with app.run_test() as pilot:
            screen = await self._tone_view(app, pilot)
            table = screen.query_one("#part-table", DataTable)
            keys = [c.key.value for c in table.columns.values()]
            assert str(table.get_row("1")[keys.index("mono")]) == "PATCH"

    async def test_an_out_of_range_tone_value_is_refused(self, app):
        async with app.run_test() as pilot:
            screen = await self._tone_view(app, pilot)
            await self._cell(screen, pilot, 1, "oct")
            before = app.bridge.read_part(1).octave
            await pilot.press("9")
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            assert app.bridge.read_part(1).octave == before


class TestStoreIsHardToFireByAccident:
    """The one screen that can destroy something.

    What is being tested is not that it works but that it does not work too
    easily: opening it writes nothing, a single key writes nothing, and an
    arm aimed at one slot does not survive the cursor moving to another.
    """

    async def _open_store(self, app, pilot):
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("W")
        await pilot.pause()
        return app.screen

    async def test_opening_it_writes_nothing(self, app):
        async with app.run_test() as pilot:
            screen = await self._open_store(app, pilot)
            assert isinstance(screen, StoreScreen)
            assert app.bridge._performances == {}

    async def test_firing_without_arming_writes_nothing(self, app):
        async with app.run_test() as pilot:
            await self._open_store(app, pilot)
            await pilot.press("w")
            await settle(pilot)
            assert app.bridge._performances == {}

    async def test_arming_alone_writes_nothing(self, app):
        async with app.run_test() as pilot:
            screen = await self._open_store(app, pilot)
            await pilot.press("a")
            await pilot.pause()
            assert screen._armed == 1
            assert app.bridge._performances == {}

    async def test_moving_the_cursor_disarms(self, app):
        """An arm that survives a cursor move is aimed where nobody is
        looking."""
        async with app.run_test() as pilot:
            screen = await self._open_store(app, pilot)
            await pilot.press("a")
            await pilot.pause()
            assert screen._armed == 1
            await pilot.press("down")
            await pilot.pause()
            assert screen._armed is None
            await pilot.press("w")
            await settle(pilot)
            assert app.bridge._performances == {}

    async def test_arm_then_fire_writes_the_armed_slot(self, app):
        async with app.run_test() as pilot:
            await self._open_store(app, pilot)
            await pilot.press("down", "down")  # slot 3
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("w")
            await settle(pilot)
            from xv.bridge import TEMPORARY_PERFORMANCE, user_performance_base

            written = app.bridge._performances
            assert set(written) == {user_performance_base(3)}
            assert written[user_performance_base(3)] == (
                app.bridge.read_performance_blocks(TEMPORARY_PERFORMANCE)
            )

    async def test_the_previous_contents_are_backed_up(self, app):
        async with app.run_test() as pilot:
            await self._open_store(app, pilot)
            await pilot.press("a", "w")
            await settle(pilot)
            from xv import backup as bk

            saved = bk.list_backups(app.backup_dir())
            assert len(saved) == 1
            assert saved[0]["slot"] == 1

    async def test_the_suite_never_writes_to_the_real_data_dir(self, app, tmp_path):
        """This test exists because it happened.

        The store worker used to call data_dir() itself, so every test that
        fired it left a backup file in the user's own directory.
        """
        assert app.backup_dir().startswith(str(tmp_path))


class TestEnterReplacesAndDigitsAppend:
    """Two ways into the prompt, deliberately different.

    Enter means "change this value", so the current one is pre-selected and
    the first keystroke replaces it -- which is the only way to type a
    negative number on a screen where `-` steps down. A digit means "the
    value starts with this", so it appends.
    """

    async def _cell(self, app, pilot, column):
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.pause()
        table = app.screen.query_one("#part-table", DataTable)
        keys = [c.key.value for c in table.columns.values()]
        table.cursor_coordinate = Coordinate(0, keys.index(column))
        await pilot.pause()

    async def test_enter_preselects_so_typing_replaces(self, app):
        async with app.run_test() as pilot:
            await self._cell(app, pilot, "lvl")
            await pilot.press("enter")
            await pilot.pause()
            field = app.screen.query_one("#value", Input)
            assert field.value == "100"
            await pilot.press("7")
            await pilot.pause()
            assert field.value == "7"

    async def test_a_digit_appends_rather_than_replacing(self, app):
        async with app.run_test() as pilot:
            await self._cell(app, pilot, "lvl")
            await pilot.press("1")
            await pilot.pause()
            field = app.screen.query_one("#value", Input)
            assert field.value == "1"
            await pilot.press("2")
            await pilot.pause()
            assert field.value == "12"
