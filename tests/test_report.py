# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
#
# rxved is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.

"""The adopted frame and the build line it carries."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static, Label

from rxved.screens import ReportScreen
from rxved.app import _build_line
from vinsynlib.build import Build


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


COMMIT = "0bad4f2" * 5
CHECKOUT = Build(version=None, commit=COMMIT)
RELEASE = Build(version="0.4.1")
NEITHER = Build(version=None)


class TestTheBuildLine:
    """The tiers, stubbed and real."""

    @staticmethod
    def _line(monkeypatch, tool=CHECKOUT, library=CHECKOUT) -> str:
        def fake(name, origin=None):
            return tool if name == "rxved" else library

        monkeypatch.setattr("rxved.app.build_of", fake)
        return _build_line()

    def test_checkout_names_its_commit_and_no_version(self, monkeypatch):
        line = self._line(monkeypatch, tool=CHECKOUT, library=CHECKOUT)
        assert COMMIT[:7] in line
        assert "rxved" in line
        assert "vinsynlib" in line
        assert "0.4." not in line, "checkout invented a version"

    def test_release_names_version_and_no_commit(self, monkeypatch):
        line = self._line(monkeypatch, tool=RELEASE, library=RELEASE)
        assert "0.4.1" in line
        assert "(" not in line, "release printed a commit it cannot know"

    def test_neither_says_so(self, monkeypatch):
        line = self._line(monkeypatch, tool=NEITHER, library=NEITHER)
        assert "rxved" in line and "vinsynlib" in line
        assert "(" not in line
        assert "unknown" in line.lower() or line.endswith("unknown version")

    def test_the_real_environment_has_both_names(self):
        line = _build_line()
        assert "rxved" in line
        assert "vinsynlib" in line

    def test_library_half_has_no_commit_in_this_venv(self):
        line = _build_line()
        # The library is a release wheel (0.4.2), so no sha.
        # The rxved half, being editable, carries a sha.
        parts = line.split(" and ")
        library_part = next(p for p in parts if "vinsynlib" in p)
        rxved_part = next(p for p in parts if "rxved" in p)
        assert "(" not in library_part, "library half should have no commit"
        assert "(" in rxved_part, "rxved half should have a sha"
