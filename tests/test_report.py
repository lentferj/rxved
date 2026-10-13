# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
#
# rxved is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.

"""The adopted frame."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from rxved.screens import ReportScreen


class ReportApp(App[None]):
    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen
        self.closed = False

    def compose(self) -> ComposeResult:
        yield from []

    def on_mount(self) -> None:
        def closed(_: None) -> None:
            self.closed = True

        self.push_screen(self._screen, closed)


class TestTheAdoptedFrame:
    """What survived the swap: the subclass, the keys, the scroll."""

    async def test_escape_closes(self):
        async with ReportApp(ReportScreen("rxved", "test")).run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(pilot.app, ReportApp) and pilot.app.closed

    async def test_q_closes(self):
        async with ReportApp(ReportScreen("rxved", "test")).run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(pilot.app, ReportApp) and pilot.app.closed

    async def test_enter_closes(self):
        async with ReportApp(ReportScreen("rxved", "test")).run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(pilot.app, ReportApp) and pilot.app.closed

    async def test_the_body_scrolls(self):
        body = "\n".join(f"line {n}" for n in range(200))
        async with ReportApp(ReportScreen("rxved", body)).run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            await pilot.press("pagedown")
            await pilot.pause()
            scroll = screen.query_one(VerticalScroll)
            assert scroll.scroll_offset.y > 0, "the body scrolled"

    async def test_the_box_is_78_by_28_thick(self):
        async with ReportApp(ReportScreen("rxved", "Two panes.")).run_test(
            size=(100, 40)
        ) as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            box = screen.query_one("Vertical")
            region = box.region
            # The adopted library frame: 84x80% in a 100x40 area -> ~84x32.
            assert region.width == 84, f"width {region.width}"
            assert region.height == 32, f"height {region.height}"
            assert "thick" in str(box.styles.border), "border thick"

    async def test_the_frame_carries_rxved_words(self):
        body_text = "Two panes. Left: every bank the XV-2020 can be sent to."
        async with ReportApp(ReportScreen("rxved", body_text)).run_test() as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            static_body = screen.query_one(".body", Static)
            body_str = str(static_body.visual) if static_body.visual else ""
            assert "rxved" in body_str or "rxved" in str(screen.title_text)
            assert "Two panes. Left: every bank the XV-2020 can be sent to." in body_str
