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

"""The empty-catalog offer and a scan's progress: two screens and no MIDI.

Split out of :mod:`rxved.app` for the same reason :mod:`rxved.screens` is:
the browser itself stays readable, and the widgets can be tested without a
bridge. The app owns the worker and the wire; these two screens only ask a
question and report what they are told, so the rule that the main thread
does not touch a port holds by construction.

The offer's wording is deliberately **not** the sibling p2ked project's.
p2ked's scan is a Generic Name Request that selects nothing and makes no
sound; rxved's hardware is different. The preset and SRX banks have no
address in the parameter map (CLAUDE.md, "The preset banks have no
addresses"), so a preset or SRX name can only be read by selecting the
patch, which **plays the instrument**. Only USER, R-USER and P-USER can be
read silently. The prompt below says so, because the same sentence copied
from p2ked would be a lie about this synth.

The offer has three answers rather than a yes/no because "not now" and
"never ask again" are different questions, and only one of them should stop
the offer coming back. Escape is the first, not the second: a key pressed to
make a dialog go away must never quietly change a preference.
"""

from __future__ import annotations

import contextlib
import threading
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Static

__all__ = ["ScanOffer", "SCAN_OFFER", "ScanOfferScreen", "ScanScreen"]

#: The offer's three answers. Plain strings rather than an enum: they cross
#: a screen boundary as a value, and there is nothing to serialise.
ScanOffer = str  # "scan" | "later" | "never"

#: What the offer says. It has to be honest about the hardware, so it names
#: the user banks it reads silently and the preset and SRX banks it can only
#: read by playing. Kept here rather than in the app so the sentence a person
#: actually reads lives beside the screen that shows it -- and so a test can
#: assert the claim rather than trust a call site to repeat it.
SCAN_OFFER = (
    "There is no name catalog yet, so the sounds show as numbers.\n\n"
    "Scanning reads the names back from the synth. The user banks are read "
    "silently: nothing there is selected, and nothing sounds. The preset and "
    "SRX banks have no address in the parameter map, so their names can only "
    "be read by selecting each patch in turn -- which plays the synth, one "
    "program change per slot, for as long as that takes. rxved puts the "
    "synth back on the patch it was on when the scan finishes."
)


class ScanOfferScreen(ModalScreen[ScanOffer]):
    """Offer to build the name catalog when there is none at all.

    Three answers rather than a yes/no, because "not now" and "never ask
    again" are different, and only one of them should stop the question
    coming back. ``y`` scans, ``n`` and escape defer, ``d`` dismisses it for
    good; the three buttons say the same. Escape and ``n`` both mean
    "later" and never "never" -- a reflex dismissal must not change a
    preference.

    It displays the prompt it is handed and nothing else. Whether the answer
    starts a scan, is remembered, or is ignored is the app's to decide, which
    is what keeps this file free of MIDI.
    """

    DEFAULT_CSS = """
    ScanOfferScreen { align: center middle; }
    ScanOfferScreen > Vertical {
        width: 72; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    ScanOfferScreen .prompt { margin-bottom: 1; }
    ScanOfferScreen > Vertical > Horizontal {
        height: auto; align: center middle;
    }
    ScanOfferScreen Button { margin: 0 1; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "scan", "Scan"),
        Binding("n", "later", "Not now"),
        Binding("d", "never", "Don't ask again"),
        # Escape leans the same way as `n`. It is the nearest key to hand and
        # the one most likely pressed without reading, so it must be the
        # reversible answer; `d` is the one that has to be meant.
        Binding("escape", "later", "Not now"),
    ]

    def __init__(self, prompt: str = SCAN_OFFER) -> None:
        super().__init__()
        self.prompt_text = prompt

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.prompt_text, classes="prompt")
            with Horizontal():
                yield Button("Scan", variant="primary", id="scan")
                yield Button("Not now", id="later")
                yield Button("Don't ask again", id="never")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # The ids are the answers, so a press needs no lookup table. An id
        # of None should not happen, and "later" is the safe reading of it.
        self.dismiss(event.button.id or "later")

    def action_scan(self) -> None:
        self.dismiss("scan")

    def action_later(self) -> None:
        self.dismiss("later")

    def action_never(self) -> None:
        self.dismiss("never")


class ScanScreen(ModalScreen[None]):
    """Progress for a scan, with a Cancel button.

    The worker lives in the app; this only shows what it reports and raises
    a flag when the user cancels. The flag is read between banks -- a read in
    flight is not interrupted -- so cancelling is honest about stopping at
    the next boundary rather than mid-message, and a half-read bank is never
    left that way.

    Progress can arrive before this screen has composed: the worker is
    started and then the screen pushed, and the gap is real. ``set_progress``
    therefore keeps the latest line itself and treats a missing widget as
    "not mounted yet" rather than an error, so the first report is shown once
    the screen does compose instead of crashing the scan.
    """

    DEFAULT_CSS = """
    ScanScreen { align: center middle; }
    ScanScreen > Vertical {
        width: 72; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    ScanScreen .title { margin-bottom: 1; }
    ScanScreen > Vertical > Horizontal {
        height: auto; align: center middle;
    }
    ScanScreen Button { margin: 0 1; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
    ]

    def __init__(self, cancel: threading.Event) -> None:
        super().__init__()
        self._cancel = cancel
        self._cancelled = False
        #: The latest line, kept here as well as on screen: the worker can
        #: report progress before this screen has composed, and a query for a
        #: widget that is not there yet must not raise.
        self._line = "starting…"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[b]Scanning the synth[/b]", classes="title")
            yield Static(self._line, id="scan-line")
            with Horizontal():
                yield Button("Cancel", id="cancel")

    def set_progress(self, line: str) -> None:
        """Main thread. What the worker wants shown. Safe before mount."""
        self._line = line
        with contextlib.suppress(NoMatches):
            self.query_one("#scan-line", Static).update(line)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Raise the flag; the scan stops at the next bank boundary."""
        if self._cancelled:
            return
        self._cancelled = True
        self._cancel.set()
        self.set_progress("cancelling — stopping after this bank…")
        with contextlib.suppress(NoMatches):
            self.query_one("#cancel", Button).disabled = True

    @property
    def cancelled(self) -> bool:
        """Whether Cancel has been pressed, for the worker to report."""
        return self._cancelled
