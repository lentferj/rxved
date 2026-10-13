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

"""The offer and the progress screen, with no synth in sight.

Both screens are handed a prompt or a flag and hand back an answer; neither
opens a port, so they can be driven headlessly. A throwaway ``App`` pushes
the screen under test and records what it dismisses with, which is all the
app wiring the screens actually depend on.

The wording is worth a test of its own: rxved's offer has to say the scan
**plays the instrument**, unlike the sibling p2ked project's silent one,
because on this hardware a preset or SRX name can only be read by selecting
it.
"""

import threading

import pytest
from textual.app import App
from textual.widgets import Button, Static

from rxved.scan import SCAN_OFFER, ScanOfferScreen, ScanScreen

pytestmark = pytest.mark.asyncio


class _Harness(App[None]):
    """The least an app can be and still push a screen and keep its answer."""

    def __init__(self, screen, sink) -> None:
        super().__init__()
        self._screen = screen
        self._sink = sink

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._sink)


async def _answer(key: str) -> list[str]:
    """Push the offer, send one key, and collect what it dismissed with."""
    results: list[str] = []
    app = _Harness(ScanOfferScreen(), results.append)
    async with app.run_test() as pilot:
        await pilot.press(key)
    return results


class TestTheOffer:
    async def test_y_means_scan(self) -> None:
        assert await _answer("y") == ["scan"]

    async def test_n_means_later(self) -> None:
        assert await _answer("n") == ["later"]

    async def test_d_means_never(self) -> None:
        assert await _answer("d") == ["never"]

    async def test_escape_defers_rather_than_refusing(self) -> None:
        # The key pressed to make the dialog go away must be the reversible
        # one; only `d` is allowed to stop the offer coming back.
        assert await _answer("escape") == ["later"]

    async def test_the_buttons_give_the_same_three_answers(self) -> None:
        for button_id, expected in (
            ("scan", "scan"),
            ("later", "later"),
            ("never", "never"),
        ):
            results: list[str] = []
            app = _Harness(ScanOfferScreen(), results.append)
            async with app.run_test() as pilot:
                assert app.screen.query_one(f"#{button_id}", Button)
                await pilot.click(f"#{button_id}")
            assert results == [expected], button_id

    async def test_the_prompt_says_the_scan_plays_the_synth(self) -> None:
        # p2ked's wording would be a lie on this hardware, so the default
        # prompt has to name the playing rather than promise silence.
        assert "plays the synth" in SCAN_OFFER
        assert "silently" in SCAN_OFFER


class TestTheProgress:
    async def test_set_progress_before_mount_does_not_raise(self) -> None:
        screen = ScanScreen(threading.Event())
        # Before the app pushes it there is no #scan-line to update, which
        # is exactly the gap the worker can report into. It must not raise.
        screen.set_progress("reading PST-A: 1/128")
        assert screen._line == "reading PST-A: 1/128"

    async def test_set_progress_after_mount_updates_the_widget(self) -> None:
        screen = ScanScreen(threading.Event())
        app = _Harness(screen, lambda _result: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen.set_progress("reading PST-A: 1/128")
            await pilot.pause()
            assert (
                app.screen.query_one("#scan-line", Static).content
                == "reading PST-A: 1/128"
            )

    async def test_cancel_sets_the_flag_and_is_idempotent(self) -> None:
        event = threading.Event()
        screen = ScanScreen(event)
        assert not screen.cancelled
        screen.action_cancel()
        assert event.is_set()
        assert screen.cancelled
        # A second press is a no-op, not a second event or a second line.
        screen.action_cancel()
        assert event.is_set()
        assert screen.cancelled

    async def test_the_cancel_button_stops_the_scan(self) -> None:
        event = threading.Event()
        screen = ScanScreen(event)
        app = _Harness(screen, lambda _result: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#cancel")
            assert event.is_set()
            assert app.screen.query_one("#cancel", Button).disabled
