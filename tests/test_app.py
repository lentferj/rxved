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
from textual.widgets import DataTable, Input, Static

from textual.coordinate import Coordinate

from rxved.app import (
    CategoryScreen,
    CommonScreen,
    MultiScreen,
    PerformanceScreen,
    ReportScreen,
    RxvedApp,
    StoreScreen,
    TextPromptScreen,
)
from rxved.screens import ConfirmScreen
from rxved.demo import DemoBridge
from rxved.favorites import Favorites
from xv import banks
from xv import catalog as cat

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

    def _app(self, tmp_path, **kwargs):
        bridge = DemoBridge()
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
            # The favourite marker leads the row; PC, LSB and MSB stay in
            # the order an MPC's MIDI track asks for them, which is the
            # whole point of this test.
            assert labels == ["♥", "#", "name", "PC", "LSB", "MSB", "cat"]
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
            # The prompt is seeded with the current value (0, for OFF) and
            # selected, so a digit replaces it.
            await pilot.press("3")
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
