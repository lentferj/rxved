# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
# `wrap_blocks` and `KeyHints` are ported from the sibling s3ked project's
# s3ked/app.py, which ports them from eosed and k2kremote, all by the same
# author and all GPL-2.0-or-later:
#   Copyright (C) 2026  k2kremote contributors  - GPL-2.0-or-later
#   Copyright (C) 2026  eosed contributors      - GPL-2.0-or-later
#   Copyright (C) 2026  s3ked contributors      - GPL-2.0-or-later
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

"""``rxved`` -- a terminal browser for the XV-2020's sounds.

Two panes: banks on the left, that bank's slots on the right, with the three
numbers that actually select a sound -- program change, Bank Select LSB and
Bank Select MSB -- on every row, in that order, because that is the order
the hardware that receives them asks for it (an MPC One's MIDI track, for
one). That is the whole of the first version, and
the numbers are the point: everything else about a patch can be found by
listening to it, and those three cannot.

Three design rules, each of which is about not lying to the user:

**Moving the cursor never sends anything.** Selecting a patch on an XV-2020
changes what it is playing, audibly and immediately, and a browser that did
that on arrow-key movement would be unusable next to a running sequencer.
Selection is bound to Enter and nothing else, and the two operations that
sweep a whole bank (scanning preset names, probing for an SRX card) ask
first, because they play several hundred patches in a row.

**A name read from the device and a name read from a book are drawn
differently.** The USER bank's printed contents are what the machine shipped
with; after the user saves anything they are fiction. Live names are marked,
and a live name that disagrees with the printed one is marked more strongly
-- see :mod:`xv.catalog`.

**Nothing is cached across runs except favourites.** A front-panel edit is
invisible to us, so anything persisted would confidently go stale. The
sibling s3ked project states the same rule for the same reason.
"""

from __future__ import annotations

import argparse
import sys
import threading
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Header, Input, Label, Static

from xv import banks
from xv import params
from xv import catalog as cat

__all__ = ["RxvedApp", "main"]


# --- modal screens ----------------------------------------------------------


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no, for the operations that make the instrument play."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        width: 66; height: auto; border: thick $warning;
        background: $surface; padding: 1 2;
    }
    ConfirmScreen .prompt { margin-bottom: 1; }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("enter", "confirm", "Yes"),
        Binding("n", "dismiss_false", "No"),
        Binding("escape", "dismiss_false", "No"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._prompt, classes="prompt")
            yield Static("[b]y[/b] go ahead    [b]n[/b] / [b]esc[/b] cancel")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss_false(self) -> None:
        self.dismiss(False)


class TextPromptScreen(ModalScreen[Optional[str]]):
    """One line of text, for tags and notes."""

    DEFAULT_CSS = """
    TextPromptScreen { align: center middle; }
    TextPromptScreen > Vertical {
        width: 70; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, current: str = "",
                 select_all: bool = False) -> None:
        super().__init__()
        self._prompt = prompt
        self._current = current
        #: Pre-select the seeded text, so the first keystroke replaces it.
        #: Wanted when the seed is "the value you are changing" and not when
        #: it is "the digit you just typed" -- and it is the only way to
        #: enter a negative number on a screen where `-` steps down.
        self._select_all = select_all

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._current, id="value")

    def on_mount(self) -> None:
        field = self.query_one("#value", Input)
        field.focus()
        if self._select_all:
            field.select_all()
        else:
            # Cursor at the end, so a prompt opened by typing a digit
            # continues that number rather than inserting in front of it.
            field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ReportScreen(ModalScreen[None]):
    """A scrollable block of text: device details, help, scan results."""

    DEFAULT_CSS = """
    ReportScreen { align: center middle; }
    ReportScreen > Vertical {
        width: 84; height: 80%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    ReportScreen .body { height: 1fr; overflow-y: auto; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"[b]{self._title}[/b]")
            yield Static(self._body, classes="body")
            yield Static("[dim]esc / q to close[/dim]")

    def action_close(self) -> None:
        self.dismiss(None)


#: Separator between key hints in the legend, matching k2kremote, eosed and
#: s3ked.
_LEGEND_SEP = " · "


def wrap_blocks(blocks, width: int, sep: str = _LEGEND_SEP) -> str:
    """Pack ``blocks`` into lines no wider than ``width``, joined by ``sep``.

    Ported from the sibling s3ked, which ports it from eosed and k2kremote.
    Breaks happen only *between* blocks, so a hint like ``[ ] channel`` is
    never split mid-label; a block wider than ``width`` on its own simply
    takes its own line rather than being cut.
    """
    lines, current = [], ""
    for block in blocks:
        candidate = block if not current else current + sep + block
        if width and len(candidate) > width and current:
            lines.append(current)
            current = block
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


class KeyHints(Static):
    """The key legend, folded to the terminal's width over as many lines as it needs.

    **Replaces Textual's ``Footer``**, which is hardcoded to one line and
    truncates rather than wrapping. rxved hit exactly the failure the sibling
    projects document: in a 132-column window the legend wanted 251 columns,
    and the keys that fell off the end were the channel selector -- the
    newest and least guessable part of the interface, and the one this
    project had just spent an afternoon establishing was necessary.

    Working around it by shortening labels and hiding bindings, which is what
    rxved tried first, only moves which keys are invisible. Wrapping is the
    fix: one line on a wide terminal, more on a narrow one, and nothing ever
    hidden.
    """

    DEFAULT_CSS = "KeyHints { height: auto; background: $panel; padding: 0 1; }"

    def __init__(self, blocks, *, id=None):
        super().__init__(id=id)
        self._blocks = list(blocks)

    def on_mount(self) -> None:
        self._render_hints()

    def on_resize(self, event) -> None:
        self._render_hints()

    def set_blocks(self, blocks) -> None:
        self._blocks = list(blocks)
        self._render_hints()

    def _render_hints(self) -> None:
        self.update(wrap_blocks(self._blocks, self.size.width))


#: The legend. Every binding the app has, in the order somebody meets them.
#: Nothing is omitted, because KeyHints wraps rather than truncating.
KEY_HINTS = (
    "↑↓ move", "tab pane", "⏎ select on synth",
    "[ ] channel", "c set channel", "C categories", "R re-read",
    "f favourite", "F favourites view", "t tags", "n note", "/ search",
    "r read names", "s scan bank", "x probe SRX",
    "m multi setup", "i device", "? help", "q quit",
)


#: What the right-hand pane is showing. Cycled by `F`.
#:
#: ``BANK`` and ``FAVOURITES`` are filters over the same table rather than a
#: separate read-only screen, because the point of a favourites list is to
#: play the things on it -- everything that works in the full list (Enter to
#: select, `f` to un-favourite, tags, notes) has to keep working here.
VIEW_ALL = "all"
VIEW_BANK_FAVOURITES = "bank-favourites"
VIEW_ALL_FAVOURITES = "all-favourites"

#: The order `F` steps through.
VIEW_CYCLE = (VIEW_ALL, VIEW_BANK_FAVOURITES, VIEW_ALL_FAVOURITES)

VIEW_LABEL = {
    VIEW_ALL: "all slots",
    VIEW_BANK_FAVOURITES: "favourites in this bank",
    VIEW_ALL_FAVOURITES: "all favourites",
}


#: Short labels for the bank list's "kind" column. Spelled out rather than
#: truncated to the column width -- "patc" and "perf" are not words, and the
#: column is there to be read at a glance.
_KIND_LABEL = {
    banks.Kind.PATCH: "patch",
    banks.Kind.RHYTHM: "rhythm",
    banks.Kind.PERFORMANCE: "perf",
}


#: The Performance Part columns this screen can edit, by column key: the
#: parameter's offset in the part block, a label, and the range it accepts.
#:
#: Re-stated here rather than imported from XvBridge.WRITABLE_PART_OFFSETS so
#: the screen can be built without a bridge, with a test asserting the two
#: agree -- a screen that offers an offset the bridge refuses is a dialog
#: that fails only after the user has committed to it.
#:
#: "ch" is the exception that proves the project's rule about numbers: it is
#: 1-16 here because that is what the module shows, and 0-15 on the wire.
#: The conversion happens once, visibly, in _apply.
EDITABLE_PART_COLUMNS = {
    "ch": (0x00, "receive channel", 1, 16),
    "rx": (0x01, "receive switch", 0, 1),
    "pc": (0x06, "program change", 0, 127),
    "lsb": (0x05, "bank select LSB", 0, 127),
    "msb": (0x04, "bank select MSB", 0, 127),
    "lvl": (0x07, "part level", 0, 127),
    "mute": (0x1B, "mute switch", 0, 1),
    # Ranges here are in DISPLAY units, which for the biased ones are not
    # the wire ranges; XvBridge.WRITABLE_PART_OFFSETS validates the wire
    # side independently, so a bias mistake is caught rather than written.
    "pan": (0x08, "pan (L64-63R)", -64, 63),
    "oct": (0x15, "octave shift", -3, 3),
    "crs": (0x09, "coarse tune (semitones)", -48, 48),
    "fin": (0x0A, "fine tune (cents)", -50, 50),
    "bend": (0x0D, "pitch bend range (25 = PATCH)", 0, 25),
    "mono": (0x0B, "mono/poly (0 MONO, 1 POLY, 2 PATCH)", 0, 2),
    "lo": (0x17, "keyboard range lower (note number)", 0, 127),
    "hi": (0x18, "keyboard range upper (note number)", 0, 127),
    "dry": (0x1C, "dry send level", 0, 127),
    "cho": (0x1D, "chorus send level", 0, 127),
    "rev": (0x1E, "reverb send level", 0, 127),
    "out": (0x1F, "output assign", 0, 13),
    "mfx": (0x20, "output MFX select", 0, 2),
}

#: The two column sets the part table shows, toggled by `tab`. Sixteen parts
#: times thirteen parameters does not fit a terminal row, and cramming it
#: would cost the patch name -- which is the one column that says what a
#: part *is* rather than how it is set.
MIDI_COLUMNS = (("ch", "ch"), ("rx", "rx"), ("lvl", "lvl"), ("PC", "pc"),
                ("LSB", "lsb"), ("MSB", "msb"))
FX_COLUMNS = (("mute", "mute"), ("dry", "dry"), ("cho", "cho"),
              ("rev", "rev"), ("out", "out"), ("mfx", "mfx"))

#: Per-**channel** receive switches (Performance MIDI). Shown on the part
#: row for the channel that part listens on, which means two parts sharing a
#: channel show the same values -- because they genuinely share them.
RX_COLUMNS = (("ch", "ch"), ("rxPC", "rx_pc"), ("rxBS", "rx_bs"),
              ("bend", "rx_bend"), ("mod", "rx_mod"), ("vol", "rx_vol"),
              ("hold", "rx_hold"))

#: Per-part musical settings. Most of these are stored biased by 64 and are
#: shown here as the manual prints them -- see _BIAS.
TONE_COLUMNS = (("pan", "pan"), ("oct", "oct"), ("crs", "crs"),
                ("fin", "fin"), ("bend", "bend"), ("mono", "mono"),
                ("lo", "lo"), ("hi", "hi"))

#: The four column sets `tab` cycles through.
COLUMN_VIEWS = (("MIDI", MIDI_COLUMNS), ("FX / routing", FX_COLUMNS),
                ("receive switches", RX_COLUMNS), ("tone", TONE_COLUMNS))

#: display = wire - bias, wire = display + bias. Every entry is a place the
#: synth's byte and the manual's number differ, collected in one dict so
#: that the conversion is one line of code in one direction and one in the
#: other -- see xv/banks.py on why this project never lets those two drift.
_BIAS = {"ch": -1, "pan": 64, "crs": 64, "fin": 64, "oct": 64}

#: Column key -> the ChannelMidi attribute it shows.
_CHANNEL_FIELDS = {
    "rx_pc": "program_change", "rx_bs": "bank_select",
    "rx_bend": "bender", "rx_mod": "modulation",
    "rx_vol": "volume", "rx_hold": "hold_1",
}

#: Performance MIDI columns, by column key: offset, label, range. These are
#: written with XvBridge.write_channel_param and addressed by channel, not
#: by part -- see EDITABLE_PART_COLUMNS for the other allowlist.
EDITABLE_CHANNEL_COLUMNS = {
    "rx_pc": (0x00, "receive program change", 0, 1),
    "rx_bs": (0x01, "receive bank select", 0, 1),
    "rx_bend": (0x02, "receive bender", 0, 1),
    "rx_mod": (0x05, "receive modulation", 0, 1),
    "rx_vol": (0x06, "receive volume", 0, 1),
    "rx_hold": (0x09, "receive hold 1", 0, 1),
}


class StoreScreen(ModalScreen[None]):
    """Save the edit buffer into a user performance slot. **Destructive.**

    The only screen in rxved that can destroy something. Writing a
    performance into a user slot replaces what was stored there and no power
    cycle brings it back, so this follows the sibling projects' rule for
    destructive operations -- eosed's Master menu, k2kremote's DELBANK -- and
    then adds a backup:

    1. Pick a destination. Its **current** name is read from the synth and
       shown, because "slot 12" means nothing and the name it is about to
       replace means everything.
    2. **Arm.** Nothing has been sent yet.
    3. **Fire**, a different key, only while armed.

    Three deliberate steps, none of them a single keystroke from the browser.
    Moving the cursor over slots sends nothing and reads only names.

    Before a byte is written the destination is read in full and saved to a
    file. If that read fails, nothing is written at all: a store that cannot
    be undone is not one this program performs.
    """

    DEFAULT_CSS = """
    StoreScreen { align: center middle; }
    StoreScreen > Vertical {
        width: 76; height: 80%; border: thick $error;
        background: $surface; padding: 1 2;
    }
    StoreScreen DataTable { height: 1fr; }
    StoreScreen .armed { color: $error; text-style: bold; }
    """

    BINDINGS = [
        Binding("escape", "close", "Cancel"),
        Binding("q", "close", "Cancel"),
        Binding("a", "arm", "Arm", show=False),
        # Deliberately not Enter, and deliberately not next to "a" on the
        # keyboard: the fire key should not be the one a finger is already
        # resting on after arming.
        Binding("w", "fire", "Write", show=False),
    ]

    def __init__(self, source_name: str, slot_names, *, on_store=None) -> None:
        super().__init__()
        self._source = source_name
        #: ``{slot: name}``, as far as they have been read. Unread slots show
        #: as unknown rather than blank -- blank reads as "empty", and an
        #: occupied slot displayed as empty is how somebody overwrites work.
        self._names = dict(slot_names or {})
        self._on_store = on_store
        self._armed: Optional[int] = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f"[b]Write the edit buffer to a user performance[/b]\n"
                f"source: {self._source or 'the temporary performance'}")
            yield DataTable(id="slot-table", cursor_type="row",
                            zebra_stripes=True)
            yield Static(self._status(), id="store-status")

    def on_mount(self) -> None:
        table = self.query_one("#slot-table", DataTable)
        table.add_column("slot", key="slot")
        table.add_column("currently holds", key="name")
        for slot in range(1, 65):
            table.add_row(f"{slot:02d}", self._name_cell(slot),
                          key=str(slot))
        table.focus()

    def _name_cell(self, slot: int) -> str:
        name = self._names.get(slot)
        return name if name is not None else "[dim]not read[/dim]"

    def _slot(self) -> Optional[int]:
        table = self.query_one("#slot-table", DataTable)
        if not table.row_count:
            return None
        row = table.coordinate_to_cell_key(table.cursor_coordinate)[0]
        return int(row.value)

    def _status(self) -> str:
        if self._armed is None:
            return ("[dim]a to arm the slot under the cursor · "
                    "esc to cancel[/dim]")
        name = self._names.get(self._armed)
        holds = f"“{name}”" if name else "an unread performance"
        return (
            f"[b]ARMED[/b] — pressing [b]w[/b] overwrites slot "
            f"{self._armed:02d}, which holds {holds}.\n"
            f"Its current contents are saved to a backup file first. "
            f"Any other key disarms."
        )

    def on_data_table_row_highlighted(self, event) -> None:
        # Moving the cursor disarms. Arming is about one specific slot, and
        # an arm that survives a cursor move is an arm aimed somewhere the
        # user is no longer looking.
        if self._armed is not None:
            self._armed = None
            self.query_one("#store-status", Static).update(self._status())

    def action_arm(self) -> None:
        slot = self._slot()
        if slot is None:
            return
        self._armed = slot
        self.query_one("#store-status", Static).update(self._status())

    def action_fire(self) -> None:
        if self._armed is None:
            self.app.notify_status(
                "not armed — press a on the destination slot first",
                refused=True)
            return
        slot, self._armed = self._armed, None
        self.query_one("#store-status", Static).update(self._status())
        if self._on_store is None:
            self.app.notify_status("not connected to a synth", refused=True)
            return
        self.dismiss(None)
        self._on_store(slot)

    def action_close(self) -> None:
        self.dismiss(None)


class MultiScreen(ModalScreen[None]):
    """Multi-mode setup: all 16 Performance Parts, editable.

    The report underneath the table is why this screen exists. A part that
    makes no sound is the obvious reading of a silent channel and is
    frequently not the reason: in PATCH mode the parts are not in use at
    all, and Solo Part Select silences fifteen of them from a byte nowhere
    near any of them.

    **Edits go to Temporary Performance -- the edit buffer, not a stored
    performance.** A power cycle, or loading any performance, discards every
    byte written here, and rxved has no code that performs the Write (store)
    operation. That is what makes this the one writable screen in an
    otherwise read-only program: nothing done here can cost a saved sound.

    Every edit is read back, and the row shows what the synth reports rather
    than what was sent. A DT1 is unacknowledged, so a write the XV-2020
    declines -- or one that is meaningless in the current mode -- looks
    exactly like one it accepted.
    """

    DEFAULT_CSS = """
    MultiScreen { align: center middle; }
    MultiScreen > Vertical {
        width: 100; height: 90%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    MultiScreen DataTable { height: auto; max-height: 18; }
    MultiScreen .report { height: 1fr; overflow-y: auto; padding-top: 1; }
    MultiScreen .hint { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        # Enter is NOT bound here. A focused DataTable consumes it for its
        # own cell selection, so a plain binding never fires -- the trap the
        # category picker fell into. It arrives as CellSelected instead; see
        # on_data_table_cell_selected.
        Binding("space", "toggle_cell", "Toggle", show=False),
        Binding("tab", "toggle_view", "MIDI / FX columns", show=False),
        # Opens the arm-then-fire screen. Opening it writes nothing; the
        # destructive step is two further keys inside it, on purpose.
        Binding("W", "store", "Write to a slot", show=False),
        Binding("plus", "bump(1)", "+1", show=False),
        Binding("equals_sign", "bump(1)", "+1", show=False),
        Binding("minus", "bump(-1)", "-1", show=False),
    ] + [
        # Typing a digit on a numeric cell starts entering a number, the way
        # a spreadsheet does -- pressing Enter first to open an empty-ish
        # prompt is a step nobody wants for a three-keystroke value.
        Binding(str(digit), f"type_digit('{digit}')", show=False)
        for digit in range(10)
    ]

    def __init__(self, state, catalog, *, on_write=None,
                 on_write_channel=None) -> None:
        super().__init__()
        self._state = state
        self._catalog = catalog
        #: Index into COLUMN_VIEWS. Cycled by `tab`.
        self._view = 0
        #: ``on_write(part, offset, value, adopt)``. The screen never touches
        #: MIDI: it runs on the main thread, and workers do MIDI.
        self._on_write = on_write
        #: ``on_write_channel(channel, offset, value, adopt)``.
        self._on_write_channel_cb = on_write_channel

    # --- building ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        state = self._state
        common = state.common
        title = f"Multi-mode setup — {state.setup.mode_name} mode"
        if common is not None and common.name:
            title += f" — performance “{common.name}”"
        with Vertical():
            yield Label(f"[b]{title}[/b]")
            yield Static(self._fx_summary(), classes="hint", id="fx")
            yield DataTable(id="part-table", cursor_type="cell",
                            zebra_stripes=True)
            yield Static(self._hint_text(), classes="hint", id="hint")
            yield Static(self._report_text(), classes="report", id="report")
            yield Static("[dim]esc / q to close[/dim]")

    def _fx_summary(self) -> str:
        if self._state.fx is None:
            return "[dim]effects not read[/dim]"
        return self._state.fx.summary()

    def _hint_text(self) -> str:
        which = COLUMN_VIEWS[self._view][0]
        nxt = COLUMN_VIEWS[(self._view + 1) % len(COLUMN_VIEWS)][0]
        return (
            f"[b]{which}[/b] columns · tab for {nxt} · type a number, or "
            f"⏎ to edit · space toggles · +/- adjust · W writes to a slot · "
            f"edits go to the temporary performance, so a power cycle undoes "
            f"them"
        )

    def _columns(self):
        """part, the current set, then the two that are always worth seeing.

        The patch name and the silence flag stay in every view: the name is
        the only column that says what a part *is*, and the flag is the
        answer to the question the screen was opened to ask.
        """
        middle = COLUMN_VIEWS[self._view][1]
        return ((("part", "part"),) + middle
                + (("patch", "patch"), ("", "flag")))

    def on_mount(self) -> None:
        self._build_table()

    def _build_table(self) -> None:
        table = self.query_one("#part-table", DataTable)
        table.clear(columns=True)
        for label, key in self._columns():
            table.add_column(label, key=key)
        for part in self._state.parts:
            table.add_row(*self._row_cells(part), key=str(part.part))

    def action_toggle_view(self) -> None:
        self._view = (self._view + 1) % len(COLUMN_VIEWS)
        self._build_table()
        self.query_one("#hint", Static).update(self._hint_text())

    def _cell_text(self, part, column: str) -> str:
        """One cell, bolded where the value is why a part cannot be heard."""
        if column == "ch":
            return str(part.channel_display)
        if column == "rx":
            return "on" if part.receive_switch else "[b]OFF[/b]"
        if column == "lvl":
            return str(part.level) if part.level else "[b]0[/b]"
        if column == "pc":
            return str(part.program_change)
        if column == "lsb":
            return str(part.lsb)
        if column == "msb":
            return str(part.msb)
        if column == "mute":
            return "[b]MUTE[/b]" if part.mute else "off"
        if column in ("dry", "cho", "rev"):
            value = {"dry": part.dry, "cho": part.chorus,
                     "rev": part.reverb}[column]
            # Only worth flagging when *every* send is down; a part can
            # legitimately run entirely wet or entirely dry.
            dead = part.dry == part.chorus == part.reverb == 0
            return f"[b]{value}[/b]" if dead else str(value)
        if column == "out":
            # A star means the XV-2020 ignores this value -- the performance
            # came from a bigger sibling. Worth seeing, not worth silently
            # normalising away.
            name = part.output_name
            return f"[b]{name}[/b]" if name.endswith("*") else name
        if column == "mfx":
            name = part.output_mfx_name
            return f"[b]{name}[/b]" if name.endswith("*") else name
        if column in ("pan", "oct", "crs", "fin"):
            value = self._current_value(part, column)
            return f"+{value}" if value > 0 else str(value)
        if column == "bend":
            return "PAT" if part.bend_range == 25 else str(part.bend_range)
        if column == "mono":
            return params.MONO_POLY.get(part.mono_poly,
                                            str(part.mono_poly))
        if column in ("lo", "hi"):
            low, high = part.key_lower, part.key_upper
            value = low if column == "lo" else high
            text = params.note_name(value)
            # A range that excludes the whole keyboard is a silent part for
            # a reason no switch explains, so make it visible.
            return f"[b]{text}[/b]" if low > high else text
        if column in EDITABLE_CHANNEL_COLUMNS:
            entry = self._state.channel_midi(part.receive_channel)
            if entry is None:
                return "[dim]?[/dim]"
            on = getattr(entry, _CHANNEL_FIELDS[column])
            # rxPC and rxBS off are why a select does nothing, so they are
            # bolded; the rest are ordinary settings.
            loud = column in ("rx_pc", "rx_bs")
            if on:
                return "on"
            return "[b]OFF[/b]" if loud else "off"
        return ""

    def _row_cells(self, part):
        slot = part.slot
        if slot is not None:
            name = self._catalog.display_name(slot.bank_id, slot.number)
            name = f"{slot.bank.label} {slot.number:03d}  {name}".rstrip()
        else:
            name = "[dim]no bank claims this[/dim]"
        reason = part.silence_reason()
        if reason is None and self._state.soloed_out(part):
            reason = "not soloed"
        middle = tuple(self._cell_text(part, key)
                       for _label, key in self._columns()[1:-2])
        return ((str(part.part),) + middle
                + (name, f"[b]{reason}[/b]" if reason else ""))

    def _report_text(self) -> str:
        lines = list(self._state.silence_report())
        return "\n".join(f"· {line}" if line else "" for line in lines)

    # --- editing -------------------------------------------------------------

    def _cursor(self):
        """``(part, column_key)`` under the cursor; part is ``None`` if the
        column is not one of the editable ones."""
        table = self.query_one("#part-table", DataTable)
        if not table.row_count:
            return None, None
        row_key, column_key = table.coordinate_to_cell_key(
            table.cursor_coordinate)
        column = column_key.value
        if (column not in EDITABLE_PART_COLUMNS
                and column not in EDITABLE_CHANNEL_COLUMNS):
            return None, column
        part = next((p for p in self._state.parts
                     if str(p.part) == row_key.value), None)
        return part, column

    def _current_value(self, part, column: str) -> int:
        """The value as this column displays it -- 1-based for ch."""
        if column in _BIAS and column != "ch":
            wire = {"pan": part.pan, "crs": part.coarse, "fin": part.fine,
                    "oct": part.octave}[column]
            return wire - _BIAS[column]
        return {
            "ch": part.channel_display,
            "rx": 1 if part.receive_switch else 0,
            "lvl": part.level,
            "pc": part.program_change,
            "lsb": part.lsb,
            "msb": part.msb,
            "mute": 1 if part.mute else 0,
            "dry": part.dry,
            "cho": part.chorus,
            "rev": part.reverb,
            "out": part.output_assign,
            "mfx": part.output_mfx,
            "bend": part.bend_range,
            "mono": part.mono_poly,
            "lo": part.key_lower,
            "hi": part.key_upper,
        }.get(column) if column not in EDITABLE_CHANNEL_COLUMNS else (
            self._channel_value(part, column))

    def _channel_value(self, part, column: str) -> int:
        entry = self._state.channel_midi(part.receive_channel)
        if entry is None:
            return 0
        return 1 if getattr(entry, _CHANNEL_FIELDS[column]) else 0

    def on_data_table_cell_selected(self, event) -> None:
        """Enter on a cell. This is where Enter arrives, not a binding."""
        if event.data_table.id == "part-table":
            self.action_edit_cell()

    def action_type_digit(self, digit: str) -> None:
        """A digit on a numeric cell opens the prompt already holding it.

        On the rx column there is nothing to type: it holds one bit, so 0
        and 1 set it directly and any other digit is refused rather than
        silently rounded to something.
        """
        part, column = self._cursor()
        if part is None:
            return
        if self._is_switch(column):
            if digit in ("0", "1"):
                self._apply(part, column, int(digit))
            else:
                self.app.notify_status(
                    f"{self._spec(column)[1]} is 0 or 1", refused=True)
            return
        self._prompt_for_value(part, column, seed=digit)

    def action_edit_cell(self) -> None:
        part, column = self._cursor()
        if part is None:
            self.app.notify_status(
                f"{column} is not editable" if column else "nothing to edit",
                refused=True)
            return
        if self._is_switch(column):
            self.action_toggle_cell()
            return
        # Seeded with the current value, because Enter means "change this
        # one" and the old value is usually the starting point. Typing a
        # digit instead replaces outright -- see action_type_digit.
        # Selected, not just seeded: `-` steps down on this screen, so the
        # only way to type a negative pan or tune is to replace the whole
        # value. Enter is the path that has to allow it.
        self._prompt_for_value(
            part, column, seed=str(self._current_value(part, column)),
            select_all=True)

    def _prompt_for_value(self, part, column: str, *, seed: str,
                          select_all: bool = False) -> None:
        _offset, label, low, high = self._spec(column)

        def done(text) -> None:
            if text is None or not text.strip():
                return
            try:
                value = int(text.strip())
            except ValueError:
                self.app.notify_status(f"{text!r} is not a number",
                                       refused=True)
                return
            self._apply(part, column, value)

        self.app.push_screen(
            TextPromptScreen(
                f"Part {part.part} — {label} ({low}-{high})", seed,
                select_all=select_all),
            done,
        )

    #: The one-bit columns: space toggles them and a digit sets them,
    #: because there is nothing to type into a switch.
    _SWITCHES = {"rx": "receive_switch", "mute": "mute"}

    def _is_switch(self, column: str) -> bool:
        """One-bit columns: space toggles, a digit sets, no prompt."""
        return column in self._SWITCHES or column in _CHANNEL_FIELDS

    @staticmethod
    def _spec(column: str):
        """``(offset, label, low, high)`` for either allowlist."""
        if column in EDITABLE_CHANNEL_COLUMNS:
            return EDITABLE_CHANNEL_COLUMNS[column]
        return EDITABLE_PART_COLUMNS[column]

    def action_toggle_cell(self) -> None:
        part, column = self._cursor()
        if part is None or not self._is_switch(column):
            return
        if column in _CHANNEL_FIELDS:
            now = self._channel_value(part, column)
        else:
            now = getattr(part, self._SWITCHES[column])
        self._apply(part, column, 0 if now else 1)

    def action_bump(self, delta: int) -> None:
        part, column = self._cursor()
        if part is None:
            return
        _offset, _label, low, high = self._spec(column)
        value = self._current_value(part, column) + delta
        if low <= value <= high:
            self._apply(part, column, value)

    def _apply(self, part, column: str, value: int) -> None:
        offset, label, low, high = self._spec(column)
        if not low <= value <= high:
            self.app.notify_status(
                f"{label} takes {low}-{high}, not {value}", refused=True)
            return
        if self._on_write is None:
            self.app.notify_status("not connected to a synth", refused=True)
            return
        if column in EDITABLE_CHANNEL_COLUMNS:
            # Addressed by channel, not by part. Several parts can share the
            # channel, so the write shows up on every row that does -- which
            # is the truth, and why the whole table is rebuilt rather than
            # one row patched.
            self._on_write_channel(part.receive_channel, offset, value)
            return
        # The one place a displayed number becomes a wire byte. Every
        # column that differs is in _BIAS; everything else passes through.
        wire = value + _BIAS.get(column, 0)
        self._on_write(part.part, offset, wire, self._adopt_part)

    def _on_write_channel(self, channel: int, offset: int,
                          value: int) -> None:
        if self._on_write_channel_cb is None:
            self.app.notify_status("not connected to a synth", refused=True)
            return
        self._on_write_channel_cb(channel, offset, value,
                                  self._adopt_channel)

    def _adopt_channel(self, fresh) -> None:
        """Replace one channel's MIDI block with what the device reported."""
        self._state = replace(
            self._state,
            midi=tuple(fresh if e.channel == fresh.channel else e
                       for e in self._state.midi),
        )
        # Every row on that channel changes at once, so rebuild rather than
        # patch -- and keep the cursor where the user left it.
        table = self.query_one("#part-table", DataTable)
        where = table.cursor_coordinate
        self._build_table()
        table.cursor_coordinate = where
        self.query_one("#report", Static).update(self._report_text())

    def _adopt_part(self, fresh) -> None:
        """Replace one part with what the device reported after a write."""
        self._state = replace(
            self._state,
            parts=tuple(fresh if p.part == fresh.part else p
                        for p in self._state.parts),
        )
        table = self.query_one("#part-table", DataTable)
        for column, cell in zip(table.columns.values(),
                                self._row_cells(fresh)):
            table.update_cell(str(fresh.part), column.key, cell)
        self.query_one("#report", Static).update(self._report_text())

    def action_store(self) -> None:
        name = ""
        if self._state.common is not None:
            name = self._state.common.name
        self.app.open_store_screen(name)

    def action_close(self) -> None:
        self.dismiss(None)


class CategoryScreen(ModalScreen[Optional[set]]):
    """Pick any number of categories to narrow the list to.

    Offers only the categories actually present in what the user is looking
    at, with a count beside each. A filter that lists every category the
    XV-2020 defines would offer 38 choices where the current view has six,
    and most of them would select nothing.
    """

    DEFAULT_CSS = """
    CategoryScreen { align: center middle; }
    CategoryScreen > Vertical {
        width: 62; height: 80%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    CategoryScreen DataTable { height: 1fr; }
    """

    BINDINGS = [
        Binding("space", "toggle", "Toggle"),
        # `priority`, for the same reason the browser's Enter needs it: the
        # focused DataTable consumes Enter for its own row selection, so
        # without this the key does nothing at all and the only way out of
        # the picker is escape -- which discards the choice.
        Binding("enter", "accept", "Apply", priority=True),
        Binding("a", "all", "All"),
        Binding("x", "none", "Clear"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, counts: Dict[str, int], selected) -> None:
        super().__init__()
        self._counts = dict(counts)
        self._selected = set(selected)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[b]Categories[/b] — narrows whatever is already "
                        "shown")
            yield DataTable(id="cats", cursor_type="row")
            yield Static("[b]space[/b] toggle   [b]a[/b] all   [b]x[/b] clear"
                         "   [b]enter[/b] apply   [b]esc[/b] cancel")

    def on_mount(self) -> None:
        table = self.query_one("#cats", DataTable)
        for label, key in (("", "on"), ("cat", "code"), ("", "name"),
                           ("n", "count")):
            table.add_column(label, key=key)
        self._fill()
        table.focus()

    def _fill(self) -> None:
        table = self.query_one("#cats", DataTable)
        row = table.cursor_row
        table.clear()
        for code, count in self._counts.items():
            table.add_row(
                "[b]x[/b]" if code in self._selected else " ",
                code,
                cat.CATEGORY_NAMES.get(code, ""),
                str(count),
                key=code,
            )
        if 0 < row < len(self._counts):
            table.move_cursor(row=row)

    def _code_at_cursor(self) -> Optional[str]:
        row = self.query_one("#cats", DataTable).cursor_row
        codes = list(self._counts)
        return codes[row] if 0 <= row < len(codes) else None

    def action_toggle(self) -> None:
        code = self._code_at_cursor()
        if code is None:
            return
        self._selected.symmetric_difference_update({code})
        self._fill()

    def action_all(self) -> None:
        self._selected = set(self._counts)
        self._fill()

    def action_none(self) -> None:
        self._selected = set()
        self._fill()

    def action_accept(self) -> None:
        # Everything selected is the same as no filter, and saying so beats
        # leaving a filter displayed that excludes nothing.
        if self._selected == set(self._counts):
            self.dismiss(set())
        else:
            self.dismiss(self._selected)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --- the application --------------------------------------------------------


class RxvedApp(App):
    """The browser."""

    CSS = """
    Screen { layers: base; }
    #panes { height: 1fr; }
    #banks { width: 34; border-right: solid $panel; }
    #slots { width: 1fr; }
    DataTable { height: 1fr; }
    #status { height: 1; background: $boost; color: $text; padding: 0 1; }
    #detail { height: 4; padding: 0 1; border-top: solid $panel; }
    .refused { background: $error; color: $text; }
    """

    # The legend is drawn by KeyHints, not by Textual's Footer, so nothing
    # here needs shortening to fit and nothing is hidden -- see KeyHints for
    # why that matters. `priority` on enter is still load-bearing: a focused
    # DataTable consumes Enter for its own row selection.
    BINDINGS = [
        # Deliberately NOT a priority binding, and not a binding at all for
        # the key itself -- see on_data_table_row_selected. A priority Enter
        # here fires over modal screens too, which silently broke the
        # category picker: its own Enter binding never ran, so the only way
        # out was escape, which discards the choice.
        Binding("enter", "select_slot", "Select on synth", show=False),
        Binding("left_square_bracket", "channel_down", "Channel -"),
        Binding("right_square_bracket", "channel_up", "Channel +"),
        Binding("c", "pick_channel", "Set channel"),
        Binding("C", "pick_categories", "Categories"),
        Binding("R", "refresh_state", "Re-read the synth"),
        Binding("f", "toggle_favorite", "Favourite"),
        Binding("F", "cycle_favorites", "Favourites view"),
        Binding("t", "edit_tags", "Tags"),
        Binding("n", "edit_note", "Note"),
        Binding("slash", "search", "Search"),
        Binding("r", "read_bank", "Read names"),
        Binding("s", "scan_bank", "Scan bank"),
        Binding("x", "probe_srx", "Probe SRX"),
        Binding("m", "multi_setup", "Multi-mode setup"),
        Binding("i", "device_info", "Device"),
        Binding("question_mark", "help", "Help"),
        Binding("tab", "switch_pane", "Switch pane"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, bridge, *, favorites, catalog=None,
                 channel: int = 0) -> None:
        super().__init__()
        self.bridge = bridge
        self.favorites = favorites
        self.catalog = catalog if catalog is not None else cat.empty()
        self.channel = channel
        #: Every bridge call is taken under this, and shutdown waits on it --
        #: see :func:`_close_when_idle`. A worker mid-request that has its
        #: port closed underneath it leaves the synth composing a reply for
        #: nobody, which is how the sibling projects wedged their hardware.
        self._bridge_lock = threading.RLock()
        self._bank_ids: List[str] = banks.bank_ids()
        self._current_bank: str = self._bank_ids[0]
        self._current_slots: List[banks.Slot] = []
        #: Set while we are repopulating a table, so the row-highlight
        #: handler does not react to our own writes.
        self._filling = False
        self._busy = False
        self.last_status = ""
        self.last_status_refused = False
        #: Which of VIEW_CYCLE the slot pane is showing.
        self.view_mode = VIEW_ALL
        #: Category codes to narrow to; empty means no narrowing. Applied on
        #: top of the favourites view rather than instead of it, so "soft
        #: pads I have favourited" is one filter over another.
        self.categories: set = set()
        #: The MIDI channel the browser sends on, 0-based. Set from the
        #: synth's own Patch Receive Channel once that has been read --
        #: until then it is whatever was configured, and the UI says so.
        self.target_channel = channel
        self.channel_is_from_device = False

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="panes"):
            with Vertical(id="banks"):
                yield DataTable(id="bank-table", cursor_type="row")
            with Vertical(id="slots"):
                yield DataTable(id="slot-table", cursor_type="row")
        yield Static("", id="detail")
        yield Static("", id="status")
        yield KeyHints(KEY_HINTS, id="hints")

    def on_mount(self) -> None:
        self.title = "rxved"
        self.sub_title = getattr(self.bridge, "description", "")

        # Keyed columns, because a favourite toggle changes exactly two
        # cells and rebuilding a table to change a cell is what caused the
        # cursor to jump -- see _mark_favorite and on_data_table_row_highlighted.
        bank_table = self.query_one("#bank-table", DataTable)
        for label, key in (("bank", "bank"), ("kind", "kind"),
                           ("n", "n"), ("fav", "fav")):
            bank_table.add_column(label, key=key)
        self._fill_banks()

        slot_table = self.query_one("#slot-table", DataTable)
        # PC, LSB, MSB -- least significant first. Not the order the
        # manual's tables print, but the order a sequencer's MIDI track
        # asks for the three, which is where these numbers get typed.
        for label, key in (("#", "num"), ("name", "name"), ("PC", "pc"),
                           ("LSB", "lsb"), ("MSB", "msb"), ("cat", "cat"),
                           ("fav", "fav")):
            slot_table.add_column(label, key=key)
        self._fill_slots(self._current_bank)

        bank_table.focus()
        self._read_channels_worker()
        if not self.catalog:
            self.notify_status(
                "no name catalog -- numbers only. Build one with "
                "tools/extract_catalog.py, or press r/s to read names off "
                "the synth."
            )
        else:
            self.notify_status(
                f"{len(self.catalog)} names from {self.catalog.source or 'catalog'}"
            )

    @work(thread=True)
    def _read_channels_worker(self) -> None:
        """Learn the synth's mode, channels and per-part state, at startup.

        Silent -- nothing is selected. Worth doing eagerly because until it
        is known, rxved cannot say what a Bank Select on any given channel
        would even hit: in Patch mode only one channel selects anything, and
        in Performance mode it depends which part listens where.
        """
        try:
            with self._bridge_lock:
                state = self.bridge.read_state()
        except Exception as exc:
            self.call_from_thread(
                self.notify_status,
                f"could not read the synth's state ({exc}); sending on "
                f"channel {self.target_channel + 1}, which may hit nothing",
                refused=True,
            )
            return
        self.call_from_thread(self._adopt_state, state, True)

    def _adopt_state(self, state, move_cursor: bool = False) -> None:
        """Take a freshly read DeviceState and redraw what depends on it."""
        if move_cursor and not self.channel_is_from_device:
            # Start where the synth is actually listening rather than on
            # channel 1 by assumption.
            self.target_channel = state.channels.patch_receive
            self.channel_is_from_device = True
        self._update_detail(
            self.query_one("#slot-table", DataTable).cursor_row)
        self.notify_status(f"read back from the synth — "
                           f"{state.setup.mode_name} mode")

    # --- filling the tables -------------------------------------------------

    def _fill_banks(self) -> None:
        table = self.query_one("#bank-table", DataTable)
        self._filling = True
        try:
            table.clear()
            for bank_id in self._bank_ids:
                entry = banks.bank(bank_id)
                marked = len(self.favorites.keys_for_bank(bank_id))
                table.add_row(
                    bank_id,
                    _KIND_LABEL[entry.kind],
                    str(entry.count),
                    str(marked) if marked else "",
                    key=bank_id,
                )
        finally:
            self._filling = False

    def _visible_slots(self, bank_id: str) -> List[banks.Slot]:
        """The rows the right-hand pane should show, for the current view.

        In the all-favourites view this spans banks, so the caller cannot
        assume every slot belongs to ``bank_id`` -- which is why row keys are
        ``slot.key`` (``PST-B:029``) rather than the slot number. Two banks
        can both hold a slot 29, and a duplicate row key is a silent
        corruption in a DataTable.
        """
        if self.view_mode == VIEW_ALL:
            rows = banks.slots(bank_id)
        elif self.view_mode == VIEW_BANK_FAVOURITES:
            marked = self.favorites.keys_for_bank(bank_id)
            rows = [s for s in banks.slots(bank_id) if s.number in marked]
        else:
            rows = []
            for favourite in self.favorites.all(order="bank"):
                try:
                    rows.append(banks.slot(favourite.bank_id,
                                           favourite.number))
                except LookupError:
                    # A favourite for a bank this build no longer defines.
                    # Skipped rather than crashing the view.
                    continue
        return self._by_category(rows)

    def _by_category(self, rows: List[banks.Slot]) -> List[banks.Slot]:
        """Narrow to the selected categories. A no-op when none are chosen."""
        if not self.categories:
            return rows
        return [
            s for s in rows
            if self.catalog.category(s.bank_id, s.number) in self.categories
        ]

    def _fill_slots(self, bank_id: str, *, cursor: int = 0) -> None:
        """Rebuild the slot table. Only for when every row changes.

        A change to one cell goes through :meth:`DataTable.update_cell`
        instead: ``clear()`` resets the cursor and *posts* a RowHighlighted,
        which is delivered after this method has returned.
        """
        table = self.query_one("#slot-table", DataTable)
        entry = banks.bank(bank_id)
        self._current_bank = bank_id
        self._current_slots = self._visible_slots(bank_id)
        across_banks = self.view_mode == VIEW_ALL_FAVOURITES
        favorited = self.favorites.keys()
        self._filling = True
        try:
            table.clear()
            for slot in self._current_slots:
                name = self.catalog.display_name(slot.bank_id, slot.number)
                if self.catalog.differs(slot.bank_id, slot.number):
                    # The machine and the book disagree. For a USER slot this
                    # is the normal state of a synth somebody uses, and it is
                    # the single most useful thing this screen can point out.
                    shown = f"[b]{name}[/b] [dim]*[/dim]"
                elif self.catalog.is_live(slot.bank_id, slot.number):
                    shown = f"[b]{name}[/b]"
                else:
                    shown = name
                catalog_entry = self.catalog.entry(slot.bank_id, slot.number)
                table.add_row(
                    (f"{slot.bank_id} {slot.number:03d}" if across_banks
                     else f"{slot.number:03d}"),
                    shown,
                    str(slot.program_change),
                    str(slot.lsb),
                    str(slot.msb),
                    (catalog_entry.category or "") if catalog_entry else "",
                    "*" if slot.key in favorited else "",
                    key=slot.key,
                )
        finally:
            self._filling = False
        column = table.columns.get("num")
        if column is not None:
            column.label = Text("bank / #" if across_banks else "#")
        table.refresh()
        if 0 < cursor < len(self._current_slots):
            table.move_cursor(row=cursor)
        self._update_subtitle()
        self._update_detail(table.cursor_row)

    def _update_subtitle(self) -> None:
        entry = banks.bank(self._current_bank)
        shown = len(self._current_slots)
        if self.view_mode == VIEW_ALL:
            plural = {"patch": "patches", "rhythm": "rhythm sets",
                      "performance": "performances"}[entry.kind]
            what = f"{entry.label} ({self._current_bank}) — {shown} {plural}"
        elif self.view_mode == VIEW_BANK_FAVOURITES:
            what = (f"{entry.label} ({self._current_bank}) — {shown} "
                    f"favourite{'' if shown == 1 else 's'}")
        else:
            what = (f"all favourites — {shown} "
                    f"across {len({s.bank_id for s in self._current_slots})} "
                    f"bank(s)")
        if self.categories:
            what += "   ·   " + "/".join(sorted(self.categories))
        self.sub_title = f"{what}   ·   ch {self.target_channel + 1}"

    def _update_detail(self, row: int) -> None:
        if not 0 <= row < len(self._current_slots):
            if self.view_mode != VIEW_ALL:
                where = ("this bank" if self.view_mode == VIEW_BANK_FAVOURITES
                         else "any bank")
                self.query_one("#detail", Static).update(
                    f"[b]No favourites in {where}.[/b]\n"
                    f"Press [b]F[/b] for the next view, or go back to the "
                    f"full list and press [b]f[/b] on a slot."
                )
            else:
                self.query_one("#detail", Static).update("")
            return
        slot = self._current_slots[row]
        name = self.catalog.display_name(slot.bank_id, slot.number)
        fav = self.favorites.get(slot.bank_id, slot.number)
        lines = [
            f"[b]{slot.bank.label} {slot.number:03d}[/b]  {name}",
            f"Program Change [b]{slot.program_change}[/b] "
            f"[dim](wire, 0-based; the display shows "
            f"{slot.number})[/dim]   "
            f"Bank Select LSB [b]{slot.lsb}[/b] (CC#32)   "
            f"MSB [b]{slot.msb}[/b] (CC#0)",
        ]
        state = getattr(self.bridge, "state", None)
        if state is not None:
            lines.append(
                f"[b]{state.setup.mode_name}[/b] mode  ·  sending on ch "
                f"[b]{self.target_channel + 1}[/b]  ·  "
                f"{state.describes_short(self.target_channel)}"
            )
        else:
            lines.append(
                f"send on ch [b]{self.target_channel + 1}[/b] "
                f"[dim](synth state not read)[/dim]")
        if fav is not None:
            bits = ["favourite"]
            if fav.rating:
                bits.append("*" * fav.rating)
            if fav.tags:
                bits.append(f"tags: {fav.tags}")
            if fav.note:
                bits.append(fav.note)
            lines.append("[b]" + "  |  ".join(bits) + "[/b]")
        self.query_one("#detail", Static).update("\n".join(lines))

    # --- events -------------------------------------------------------------

    def on_data_table_row_selected(self, event) -> None:
        """Enter on a slot row selects it on the synth.

        Handled as the table's own event rather than as an App-level Enter
        binding. A focused DataTable consumes Enter for row selection, so a
        plain binding never fires; a `priority` binding does fire, but also
        fires over any modal screen that is open, which is worse.
        """
        if event.data_table.id == "slot-table":
            self.action_select_slot()

    def on_data_table_row_highlighted(self, event) -> None:
        if self._filling:
            return
        if event.data_table.id == "bank-table":
            row = event.cursor_row
            if 0 <= row < len(self._bank_ids):
                bank_id = self._bank_ids[row]
                # Idempotent on purpose. `_filling` cannot guard this -- the
                # message is delivered after the flag is cleared -- so the
                # handler has to be safe to receive for the bank it is
                # already showing.
                if self.view_mode == VIEW_ALL_FAVOURITES:
                    # The right pane is the union across banks; letting the
                    # bank cursor silently replace it would make the view
                    # impossible to hold still while scrolling the left pane.
                    self._current_bank = bank_id
                    return
                if bank_id != self._current_bank:
                    self._fill_slots(bank_id)
        elif event.data_table.id == "slot-table":
            self._update_detail(event.cursor_row)

    # --- status -------------------------------------------------------------

    def notify_status(self, message: str, *, refused: bool = False) -> None:
        #: The last thing said to the user. Kept as an attribute as well as
        #: rendered, because a Static's text is not reliably readable back
        #: out of the widget across Textual versions, and the status line is
        #: where every refusal explains itself -- it needs to be assertable.
        self.last_status = message
        self.last_status_refused = refused
        widget = self.query_one("#status", Static)
        widget.update(message)
        widget.set_class(refused, "refused")

    # --- helpers ------------------------------------------------------------

    def _focused_table(self) -> DataTable:
        focused = self.focused
        if isinstance(focused, DataTable):
            return focused
        return self.query_one("#bank-table", DataTable)

    def _current_slot(self) -> Optional[banks.Slot]:
        table = self.query_one("#slot-table", DataTable)
        row = table.cursor_row
        if not 0 <= row < len(self._current_slots):
            return None
        return self._current_slots[row]

    def _refresh_current_bank(self) -> None:
        """Rebuild the slot table in place, keeping the cursor.

        Deliberately does **not** touch the bank table. Rebuilding that was
        the bug behind the cursor jumping to slot 001 on every favourite
        toggle: ``clear()`` snaps the bank cursor to row 0 and posts a
        RowHighlighted that arrives after ``_filling`` is back to False, so
        the handler faithfully loaded bank 0's slots. Nothing here changes
        the bank list's contents anyway.
        """
        table = self.query_one("#slot-table", DataTable)
        self._fill_slots(self._current_bank, cursor=table.cursor_row)

    def _mark_favorite(self, slot: banks.Slot, now: bool) -> None:
        """Reflect a favourite toggle, without moving the cursor."""
        slot_table = self.query_one("#slot-table", DataTable)
        marked = len(self.favorites.keys_for_bank(slot.bank_id))
        try:
            self.query_one("#bank-table", DataTable).update_cell(
                slot.bank_id, "fav", str(marked) if marked else "")
        except Exception:
            pass

        if not now and self.view_mode != VIEW_ALL:
            # The row no longer matches the filter, so it has to go. Rebuild
            # and hold the cursor at the same position, which is now the row
            # that took its place -- the same thing a mail client does when
            # you delete out of a filtered list.
            row = slot_table.cursor_row
            self._fill_slots(self._current_bank)
            remaining = len(self._current_slots)
            if remaining:
                slot_table.move_cursor(row=min(row, remaining - 1))
            self._update_detail(slot_table.cursor_row)
            return

        try:
            slot_table.update_cell(slot.key, "fav", "*" if now else "")
        except Exception:
            # The row is gone (the bank changed under us); a full rebuild is
            # the honest fallback and costs one frame.
            self._refresh_current_bank()
        self._update_detail(slot_table.cursor_row)

    # --- actions ------------------------------------------------------------

    def action_switch_pane(self) -> None:
        if self._focused_table().id == "bank-table":
            self.query_one("#slot-table", DataTable).focus()
        else:
            self.query_one("#bank-table", DataTable).focus()

    def action_select_slot(self) -> None:
        """Select the highlighted slot on the synth. Bound to Enter only."""
        slot = self._current_slot()
        if slot is None:
            return
        self._select_worker(slot)

    @work(thread=True)
    def _select_worker(self, slot: banks.Slot) -> None:
        try:
            with self._bridge_lock:
                # A performance is selected on the Performance Control
                # Channel and nowhere else, so that one is not the user's to
                # choose. Everything else goes out on the channel they
                # picked, because in a multitimbral mode that is precisely
                # which part they are aiming at.
                used = self._channel_for(slot)
                if used is None:
                    raise RuntimeError(
                        "this synth has its Performance Control Channel set "
                        "to OFF, so performances cannot be selected over MIDI")
                self.bridge.select(slot, channel=used)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"select: {exc}", refused=True)
            return
        message = (
            f"selected {slot} on MIDI channel {used + 1} "
            f"(PC {slot.program_change}, LSB {slot.lsb}, MSB {slot.msb})"
        )
        state = getattr(self.bridge, "state", None)
        if state is None:
            message += "  —  synth state not read; this may have hit nothing"
        self.call_from_thread(self.notify_status, message)
        # Read back what the synth now says it is on, rather than assuming
        # the send landed: a Bank/PC aimed at a channel nothing listens on is
        # ignored in silence.
        self._refresh_channel_worker(used)

    def _channel_for(self, slot: banks.Slot):
        """Which channel this slot must go out on."""
        if slot.kind == banks.Kind.PERFORMANCE:
            channels = getattr(self.bridge, "channels", None)
            if channels is not None:
                return channels.performance_control
        return self.target_channel

    def action_toggle_favorite(self) -> None:
        slot = self._current_slot()
        if slot is None:
            return
        name = self.catalog.display_name(slot.bank_id, slot.number)
        now = self.favorites.toggle(
            slot.bank_id, slot.number,
            name="" if name == cat.UNNAMED else name,
        )
        self._mark_favorite(slot, now)
        self.notify_status(
            f"{slot} {'added to' if now else 'removed from'} favourites "
            f"({len(self.favorites)} total)"
        )

    def action_edit_tags(self) -> None:
        slot = self._current_slot()
        if slot is None:
            return
        existing = self.favorites.get(slot.bank_id, slot.number)
        if existing is None:
            self.notify_status(
                f"{slot} is not a favourite yet -- press f first",
                refused=True)
            return

        def apply(value: Optional[str]) -> None:
            if value is None:
                return
            self.favorites.set_tags(slot.bank_id, slot.number, value)
            self._update_detail(
                self.query_one("#slot-table", DataTable).cursor_row)
            self.notify_status(f"{slot} tags set")

        self.push_screen(
            TextPromptScreen(f"Tags for {slot} (comma separated)",
                             existing.tags),
            apply,
        )

    def action_edit_note(self) -> None:
        slot = self._current_slot()
        if slot is None:
            return
        existing = self.favorites.get(slot.bank_id, slot.number)
        if existing is None:
            self.notify_status(
                f"{slot} is not a favourite yet -- press f first",
                refused=True)
            return

        def apply(value: Optional[str]) -> None:
            if value is None:
                return
            self.favorites.set_note(slot.bank_id, slot.number, value)
            self._update_detail(
                self.query_one("#slot-table", DataTable).cursor_row)
            self.notify_status(f"{slot} note set")

        self.push_screen(
            TextPromptScreen(f"Note for {slot}", existing.note), apply)

    def action_cycle_favorites(self) -> None:
        """Step: all slots -> this bank's favourites -> every favourite.

        A filter over the real table rather than a read-only list, so a
        favourite can be played, re-tagged or un-favourited straight from it.

        **Always exactly three steps**, even when a view would come up empty.
        An earlier version skipped empty views to be helpful, which made one
        keypress land somewhere different depending on what happened to be
        favourited -- a key whose behaviour you cannot predict is worse than
        an empty list that explains itself.
        """
        index = VIEW_CYCLE.index(self.view_mode)
        self.view_mode = VIEW_CYCLE[(index + 1) % len(VIEW_CYCLE)]
        self._fill_slots(self._current_bank)
        self.query_one("#slot-table", DataTable).focus()
        shown = len(self._current_slots)
        if self.view_mode == VIEW_ALL:
            self.notify_status("showing all slots")
        elif not shown:
            where = ("this bank" if self.view_mode == VIEW_BANK_FAVOURITES
                     else "any bank")
            self.notify_status(
                f"no favourites in {where} yet — press f on a slot to add "
                f"one, or F again for the next view")
        else:
            self.notify_status(
                f"showing {VIEW_LABEL[self.view_mode]} — {shown} row(s); "
                f"enter still selects, f un-favourites")

    def action_pick_categories(self) -> None:
        """Narrow the current list by category. Stacks on the current view."""
        # Offered over the *unfiltered* rows of the current view, so the
        # picker still lists a category after you have filtered it away --
        # otherwise the only way back would be to clear the filter blind.
        saved, self.categories = self.categories, set()
        try:
            rows = self._visible_slots(self._current_bank)
        finally:
            self.categories = saved
        counts = self.catalog.categories_in(
            [(s.bank_id, s.number) for s in rows])
        if not counts:
            self.notify_status("nothing to categorise here", refused=True)
            return

        def apply(chosen) -> None:
            if chosen is None:
                return
            self.categories = set(chosen)
            self._fill_slots(self._current_bank)
            self.query_one("#slot-table", DataTable).focus()
            shown = len(self._current_slots)
            if not self.categories:
                self.notify_status(f"category filter cleared — {shown} rows")
            else:
                self.notify_status(
                    f"{'/'.join(sorted(self.categories))} — {shown} of "
                    f"{len(rows)} rows")

        self.push_screen(CategoryScreen(counts, self.categories), apply)

    def action_search(self) -> None:
        def run(needle: Optional[str]) -> None:
            if not needle:
                return
            hits = self.catalog.search(needle)
            if not hits:
                self.notify_status(f"nothing matching {needle!r}")
                return
            lines = [f"{len(hits)} match(es) for {needle!r}", ""]
            for bank_id, number, name in hits[:400]:
                try:
                    slot = banks.slot(bank_id, number)
                    wire = (f"PC {slot.program_change:>3}  "
                            f"LSB {slot.lsb:>3}  MSB {slot.msb:>3}")
                except LookupError:
                    wire = ""
                lines.append(f"{bank_id:<10} {number:03d}  {name:<14} {wire}")
            if len(hits) > 400:
                lines.append(f"... and {len(hits) - 400} more")
            self.push_screen(ReportScreen(f"Search: {needle}",
                                          "\n".join(lines)))

        self.push_screen(TextPromptScreen("Search names"), run)

    def action_read_bank(self) -> None:
        """Read the highlighted bank's names off the synth, if it has an address.

        Genuinely read-only -- nothing is selected and the instrument keeps
        playing whatever it was playing -- so unlike scan it needs no
        confirmation.
        """
        bank_id = self._current_bank
        if bank_id not in ("USER", "P-USER", "R-USER"):
            self.notify_status(
                f"{bank_id} is ROM or expansion and has no address in the "
                f"parameter map -- press s to scan it by selection instead "
                f"(that plays the synth)",
                refused=True,
            )
            return
        if self._busy:
            self.notify_status("already talking to the synth", refused=True)
            return
        self._read_bank_worker(bank_id)

    @work(thread=True)
    def _read_bank_worker(self, bank_id: str) -> None:
        self._busy = True
        try:
            def progress(done: int, total: int) -> None:
                self.call_from_thread(
                    self.notify_status, f"reading {bank_id}: {done}/{total}")

            with self._bridge_lock:
                names = self.bridge.read_user_bank(
                    bank_id, on_progress=progress)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"read {bank_id}: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(self._apply_names, bank_id, names)

    def action_scan_bank(self) -> None:
        """Learn a bank's names by selecting every slot in it. Asks first."""
        bank_id = self._current_bank
        entry = banks.bank(bank_id)
        if self._busy:
            self.notify_status("already talking to the synth", refused=True)
            return

        def go(confirmed: bool) -> None:
            if confirmed:
                self._scan_bank_worker(bank_id)

        self.push_screen(
            ConfirmScreen(
                f"Scan {entry.label} ({bank_id}) by selecting each of its "
                f"{entry.count} slots?\n\n"
                f"This is the only way to read preset and expansion names -- "
                f"they have no address in the parameter map -- but it "
                f"[b]plays the synth[/b]: it sends {entry.count} program "
                f"changes on MIDI channel {self.target_channel + 1}. rxved puts "
                f"the synth back on the patch it was on when the scan "
                f"finishes, but everything in between is audible — not what "
                f"you want mid-take."
            ),
            go,
        )

    @work(thread=True)
    def _scan_bank_worker(self, bank_id: str) -> None:
        self._busy = True
        try:
            def progress(done: int, total: int, name: str) -> None:
                self.call_from_thread(
                    self.notify_status,
                    f"scanning {bank_id}: {done}/{total}  {name}")

            with self._bridge_lock:
                names = self.bridge.scan_bank(bank_id, on_progress=progress)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"scan {bank_id}: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(self._apply_names, bank_id, names)

    def _apply_names(self, bank_id: str, names: Dict[int, str]) -> None:
        for number, name in names.items():
            self.catalog.set_live_name(bank_id, number, name)
        changed = self.favorites.refresh_names(
            lambda b, n: self.catalog.live_name(b, n))
        self._refresh_current_bank()
        differing = sum(
            1 for number in names if self.catalog.differs(bank_id, number))
        message = f"read {len(names)} name(s) from {bank_id}"
        if differing:
            message += (f"; {differing} differ from the printed list "
                        f"(marked *)")
        if changed:
            message += f"; relabelled {changed} favourite(s)"
        self.notify_status(message)

    def action_probe_srx(self) -> None:
        """Find which SRX Bank Select LSBs the fitted card answers on."""
        if self._busy:
            self.notify_status("already talking to the synth", refused=True)
            return

        def go(confirmed: bool) -> None:
            if confirmed:
                self._probe_srx_worker()

        self.push_screen(
            ConfirmScreen(
                "Probe for a fitted SRX card?\n\n"
                "rxved only has the Bank Select allocation for cards whose "
                "manual it has read (SRX-07, SRX-08). This finds any card by "
                "selecting patch 1 at each candidate LSB and reading back "
                "what the synth says it is now playing.\n\n"
                "It [b]plays the synth[/b] — 64 program changes — though "
                "rxved puts it back on the patch it was on when the probe "
                "finishes."
            ),
            go,
        )

    @work(thread=True)
    def _probe_srx_worker(self) -> None:
        self._busy = True
        try:
            def progress(lsb: int, name: Optional[str]) -> None:
                self.call_from_thread(
                    self.notify_status,
                    f"probing SRX LSB {lsb}" + (f": {name}" if name else ""))

            with self._bridge_lock:
                found = self.bridge.probe_srx(on_progress=progress)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"probe: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(self._report_srx, found)

    def _report_srx(self, found: Dict[int, str]) -> None:
        if not found:
            self.notify_status(
                "no SRX card answered. That is not proof there is none -- "
                "the synth answers an unsupported bank by staying put, so "
                "'no change' is the only signal available."
            )
            return
        lines = [
            "Bank Select LSBs under MSB 93 that produced a distinct patch 1:",
            "",
        ]
        for lsb, name in sorted(found.items()):
            known = [card.id for card in banks.SRX_CARDS
                     if lsb in card.patch_lsbs]
            tag = f"  ({known[0]})" if known else "  (not in rxved's table)"
            lines.append(f"  LSB {lsb:>3}   {name:<14}{tag}")
        lines += [
            "",
            "An LSB missing here is not proof the card lacks it: the device",
            "answers an unsupported Bank Select by staying where it was, so a",
            "page whose patch 1 shares a name with the previous page's reads",
            "as absent. Confirm against the card's own manual.",
        ]
        self.push_screen(ReportScreen("SRX probe", "\n".join(lines)))

    def action_channel_down(self) -> None:
        self._set_channel((self.target_channel - 1) % 16)

    def action_channel_up(self) -> None:
        self._set_channel((self.target_channel + 1) % 16)

    def action_pick_channel(self) -> None:
        def apply(value):
            if value is None:
                return
            try:
                number = int(value.strip())
            except ValueError:
                self.notify_status(f"{value!r} is not a channel number",
                                   refused=True)
                return
            if not 1 <= number <= 16:
                self.notify_status("MIDI channels are 1-16", refused=True)
                return
            self._set_channel(number - 1)

        self.push_screen(
            TextPromptScreen("Send on MIDI channel (1-16)",
                             str(self.target_channel + 1)),
            apply,
        )

    def _set_channel(self, channel: int) -> None:
        """Change the send channel and read back what is on the new one.

        The read-back is the point. Bank Select and Program Change are
        per-channel, so "which channel" decides whether a select lands on a
        patch, on one part of a performance, on several layered parts at
        once, or on nothing at all -- and the synth reports none of that
        back on its own.
        """
        self.target_channel = channel
        self.channel_is_from_device = True
        self._update_subtitle()
        self._update_detail(
            self.query_one("#slot-table", DataTable).cursor_row)
        if self._busy:
            self.notify_status(
                f"send channel {channel + 1} (synth busy; not re-read)")
            return
        self._refresh_channel_worker(channel)

    @work(thread=True)
    def _refresh_channel_worker(self, channel: int) -> None:
        try:
            with self._bridge_lock:
                state = self.bridge.refresh_channel(channel)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status,
                f"channel {channel + 1}: could not read back ({exc})",
                refused=True)
            return
        self.call_from_thread(self._adopt_state, state)

    def action_refresh_state(self) -> None:
        """Full re-read, including every part's receive channel."""
        if self._busy:
            self.notify_status("already talking to the synth", refused=True)
            return
        self._refresh_state_worker()

    @work(thread=True)
    def _refresh_state_worker(self) -> None:
        self._busy = True
        try:
            def progress(done, total):
                self.call_from_thread(
                    self.notify_status, f"reading part {done}/{total}")

            with self._bridge_lock:
                state = self.bridge.read_state(with_parts=True,
                                               on_progress=progress)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"refresh: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(self._adopt_state, state)

    def action_multi_setup(self) -> None:
        """Show all 16 parts, reading them fresh first.

        Fresh rather than cached: the cached state may have been assembled
        by refresh_channel, which re-reads only the parts on one channel, and
        a stale Receive Switch on this screen is exactly the wrong thing to
        be wrong about.
        """
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        self._multi_setup_worker()

    @work(thread=True)
    def _multi_setup_worker(self) -> None:
        """**MIDI only** -- the screen is built on the main thread."""
        self._busy = True
        try:
            def progress(done, total):
                self.call_from_thread(
                    self.notify_status, f"reading part {done}/{total}")

            with self._bridge_lock:
                state = self.bridge.read_state(with_parts=True,
                                               on_progress=progress)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"multi setup: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(self._show_multi_setup, state)

    def _show_multi_setup(self, state) -> None:
        self._adopt_state(state)
        if not state.parts:
            # Single-timbral: there is nothing to tabulate, and a table of
            # sixteen unused parts would imply there is.
            self.push_screen(ReportScreen(
                "Multi-mode setup",
                "\n".join(f"· {line}" for line in state.silence_report()),
            ))
            return
        self.push_screen(MultiScreen(
            state, self.catalog,
            on_write=self._write_part_param,
            on_write_channel=self._write_channel_param))

    def _write_part_param(self, part: int, offset: int, value: int,
                          adopt) -> None:
        """Hand one part-parameter write to a worker. Main thread."""
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        self._write_part_worker(part, offset, value, adopt)

    def open_store_screen(self, source_name: str) -> None:
        """Show the arm-then-fire write screen. Main thread; sends nothing.

        Slot names are whatever has already been read -- from a `r` on
        P-USER, or from a previous visit. Reading all 64 here would cost 64
        round trips before showing anything, and an unread slot is labelled
        as unread rather than blank, so nothing is misrepresented as empty.
        """
        names = {}
        for number in range(1, 65):
            name = self.catalog.live_name("P-USER", number)
            if name:
                names[number] = name
        self.push_screen(
            StoreScreen(source_name, names, on_store=self._store_to_slot))

    def _store_to_slot(self, slot: int) -> None:
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        self._store_worker(slot)

    @work(thread=True)
    def _store_worker(self, slot: int) -> None:
        """**MIDI only**, plus one file write, which is the point of it.

        The backup is written from this thread deliberately: it is a plain
        file, not the favourites SQLite connection, and holding 1.3 KB of
        somebody's performance in a variable while hopping threads is a
        chance to lose it that buys nothing.
        """
        from xv import backup as bk
        from rxved.favorites import data_dir

        self._busy = True
        try:
            def progress(done, total):
                self.call_from_thread(
                    self.notify_status, f"writing block {done}/{total}")

            with self._bridge_lock:
                previous, mismatched = self.bridge.store_temporary_to_slot(
                    slot, on_progress=progress)
            path = bk.save(
                previous, bk.default_dir(data_dir()), slot=slot,
                device_id=self.bridge.device_id,
                source=f"{self.bridge.description} (before store)")
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"store: {exc}", refused=True)
            return
        finally:
            self._busy = False

        if mismatched:
            self.call_from_thread(
                self.notify_status,
                f"slot {slot}: {len(mismatched)} block(s) did NOT read back "
                f"as written ({', '.join(mismatched[:3])}...). Previous "
                f"contents are in {path}",
                refused=True)
        else:
            self.call_from_thread(
                self.notify_status,
                f"stored into user performance {slot}; what was there "
                f"is in {path}")

    def _write_channel_param(self, channel: int, offset: int, value: int,
                             adopt) -> None:
        """Hand one Performance MIDI write to a worker. Main thread."""
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        self._write_channel_worker(channel, offset, value, adopt)

    @work(thread=True)
    def _write_channel_worker(self, channel: int, offset: int, value: int,
                              adopt) -> None:
        """**MIDI only.** Write one byte, then re-read the channel's block."""
        self._busy = True
        try:
            with self._bridge_lock:
                landed = self.bridge.write_channel_param(
                    channel, offset, value)
                fresh = self.bridge.read_performance_midi(channel)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"write: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(adopt, fresh)
        if landed != value:
            self.call_from_thread(
                self.notify_status,
                f"ch {channel + 1}: wrote {value}, synth reports {landed}",
                refused=True)
        else:
            self.call_from_thread(
                self.notify_status,
                f"ch {channel + 1}: set to {landed} in the temporary "
                f"performance (not stored)")

    @work(thread=True)
    def _write_part_worker(self, part: int, offset: int, value: int,
                           adopt) -> None:
        """**MIDI only.** Write one byte, then re-read the whole part.

        The whole part, not the byte written: the XV-2020 is free to adjust
        neighbouring parameters in response -- and whether it does is not
        something rxved knows -- so re-reading only what was sent could leave
        the rest of the row saying something that stopped being true.
        """
        self._busy = True
        try:
            with self._bridge_lock:
                landed = self.bridge.write_part_param(part, offset, value)
                fresh = self.bridge.read_part(part)
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"write: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(adopt, fresh)
        if landed != value:
            # Not an error: the synth is entitled to refuse, and saying
            # nothing would leave the user believing a write that did not
            # happen. The row already shows the truth; this says why.
            self.call_from_thread(
                self.notify_status,
                f"part {part}: wrote {value}, synth reports {landed}",
                refused=True)
        else:
            self.call_from_thread(
                self.notify_status,
                f"part {part}: set to {landed} in the temporary performance "
                f"(not stored)")

    def action_device_info(self) -> None:
        self._device_info_worker()

    @work(thread=True)
    def _device_info_worker(self) -> None:
        """Ask the synth who it is. **MIDI only** -- see _show_device_info.

        This worker deliberately touches nothing but the bridge. Building the
        report needs the favourites database, and that is a SQLite connection
        opened on the main thread, which refuses cross-thread use; doing it
        here crashed the whole application the first time anybody pressed
        `i`. The rule the rest of the app already followed and this one broke:
        a worker does the MIDI, the main thread does everything else.
        """
        try:
            with self._bridge_lock:
                identity = self.bridge.identify()
        except Exception as exc:
            self.call_from_thread(
                self.notify_status, f"identify: {exc}", refused=True)
            return
        self.call_from_thread(self._show_device_info, identity)

    def _show_device_info(self, identity) -> None:
        """Build and show the device report. Main thread only."""
        if identity is None:
            self.notify_status(
                "no Identity Reply. The XV-2020 answers a request it cannot "
                "serve with silence, so this means powered off, wrong port, "
                "Rx Exclusive off -- or simply busy.",
                refused=True,
            )
            return
        rows = [
            f"connection        {getattr(self.bridge, 'description', '?')}",
            f"device ID         {identity.device_display} "
            f"(wire byte {identity.device_id:#04x})",
            f"family            "
            f"{identity.family[0]:#04x} {identity.family[1]:#04x}",
            f"family number     {identity.family_number[0]:#04x} "
            f"{identity.family_number[1]:#04x}",
            f"software revision {identity.revision_text}",
        ]
        state = getattr(self.bridge, "state", None)
        if state is not None:
            rows += [
                "",
                f"sound mode        {state.setup.mode_name}",
                f"patch channel     {state.channels.patch_display}",
                f"performance chan  "
                + (str(state.channels.performance_display)
                   if state.channels.performance_display is not None
                   else "OFF"),
                f"rxved sends on    ch {self.target_channel + 1}"
                f"  ({state.describes_short(self.target_channel)})",
            ]
            if state.parts:
                rows.append("")
                rows.append("part  ch   slot")
                for part in state.parts:
                    rows.append(
                        f"{part.part:>4}  {part.channel_display:>2}   "
                        f"{part.slot if part.slot else '?'}")
        else:
            rows.append(f"rxved sends on    ch {self.target_channel + 1} "
                        f"(synth state not read)")
        rows += [
            "",
            f"catalog           {len(self.catalog)} names"
            + (f" from {self.catalog.source}" if self.catalog.source else ""),
            f"favourites        {len(self.favorites)} "
            f"in {self.favorites.path}",
        ]
        self.push_screen(ReportScreen("Device", "\n".join(rows)))

    def action_help(self) -> None:
        self.push_screen(ReportScreen("rxved", _HELP))


_HELP = """\
Two panes. Left: every bank the XV-2020 can be sent to. Right: that bank's
slots, with the three numbers that select each one.

  PC    Program Change            -- the byte on the wire, 0-based
  LSB   Bank Select LSB, CC#32    -- which bank within the kind
  MSB   Bank Select MSB, CC#0     -- what kind of thing (85 performance,
                                     86 rhythm, 87 patch, 92/93 SRX,
                                     120/121 GM)

They are shown in that order -- PC first, MSB last -- because that is the
order a sequencer asks for them on a MIDI track, and this screen exists to
be copied from. The synth receives them the other way round: both Bank
Select bytes must arrive BEFORE the program change, or the program change
lands in whatever bank was already selected.

The "#" column is what the synth's own display shows, and it is one more
than PC. Both are given because both are right, in different places: the
manual's tables number patches 001-128 and the MIDI byte runs 0-127.

Bank Select and Program Change are PER MIDI CHANNEL, so which channel you
send on decides what a select actually hits:

  PATCH mode     only the Patch Receive Channel selects anything. Every
                 other channel is ignored in silence.
  PERFORM mode   each of the 16 parts has its own receive channel and its
                 own patch. Several parts may share one channel, and then a
                 Program Change there changes all of them. The whole
                 performance is selected on the Performance Control Channel,
                 which is a separate setting again.

The line under the table says which mode the synth is in, which channel
rxved will send on, and what is currently on that channel -- read back from
the synth, not assumed. It re-reads whenever you change channel or select
something.

Keys
  arrows / tab   move; tab switches pane
  [ / ]          previous / next send channel      c  type a channel
  R              re-read everything, including each part's receive channel
  enter          select this slot ON THE SYNTH -- it will sound
  f              favourite / un-favourite
  F              cycle the right pane: all slots → this bank's favourites →
                 every favourite. The filtered views are the real table, so
                 enter still selects and f still un-favourites.
  t / n          tags / note (favourites only)
  C              filter by category — multi-select, and it stacks on top of
                 whichever favourites view is showing
  /              search names
  r              read this bank's names from the synth (USER banks only,
                 read-only, nothing is selected)
  s              scan this bank by selecting every slot -- the only way to
                 read preset and SRX names, and it plays the synth
  x              probe for a fitted SRX card
  i              device identity     ?  this screen     q  quit

Names in bold were read from the synth. A trailing * means the synth
disagrees with the printed list -- normal for a USER bank somebody has
saved into, and the most useful thing on the screen.
"""


# --- entry point ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rxved",
        description="Terminal browser for the Roland XV-2020's sounds.",
    )
    parser.add_argument("--port", help="MIDI port name (default: autodetect)")
    parser.add_argument("--recv-port", default=None,
                        help="input port, if it differs from --port")
    parser.add_argument(
        "--demo", action="store_true",
        help="run against the built-in demo synth; opens no MIDI ports")
    parser.add_argument("--device-id", type=int, default=None,
                        help="XV-2020 device ID as its display shows it "
                             "(17-32); default: autodetected, else 17")
    parser.add_argument("--channel", type=int, default=None,
                        help="MIDI channel to send Bank Select / Program "
                             "Change on, 1-16 (default 1)")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--catalog", default=None,
                        help="path to a generated name catalog")
    parser.add_argument("--favorites", default=None,
                        help="path to the favourites database")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.channel is not None and not 1 <= args.channel <= 16:
        sys.exit("error: --channel is 1-16")
    channel = (args.channel - 1) if args.channel is not None else None

    if args.demo:
        from rxved.demo import DemoBridge

        bridge = DemoBridge(channel=channel or 0)
        config_path = None
    else:
        # Before any port is opened: SIGTERM otherwise ends the process where
        # it stands, leaving the port open and the synth composing an answer
        # nobody will read. See xv.bridge.install_clean_exit.
        from xv import bridge as b

        b.install_clean_exit()
        config_path = args.config or b.DEFAULT_CONFIG_PATH
        if channel is None:
            channel = b.load_channel(config_path) or 0
        kwargs = {}
        if args.timeout is not None:
            kwargs["timeout"] = args.timeout
        try:
            if args.port:
                device_id = (
                    args.device_id
                    if args.device_id is not None
                    else (b.load_device_id(config_path) or 17)
                )
                bridge = b.XvBridge.standard(
                    args.port, recv_port_name=args.recv_port,
                    device_id=device_id, channel=channel, **kwargs)
            else:
                bridge = b.XvBridge.autodetect(
                    config_path=config_path, channel=channel,
                    on_try=lambda name: print(f"  probing {name}...",
                                              file=sys.stderr),
                    **kwargs)
        except Exception as exc:
            sys.exit(f"error: {exc}")

    from rxved.favorites import Favorites

    catalog = cat.load(args.catalog)
    favorites = Favorites(args.favorites)

    app = RxvedApp(bridge, favorites=favorites, catalog=catalog,
                   channel=channel or 0)
    try:
        app.run()
    finally:
        _close_when_idle(app, bridge)
        favorites.close()
    return 0


#: How long shutdown waits for an in-flight exchange to finish. Generous: a
#: bank scan is one select-and-read per slot and a worker mid-sweep needs
#: seconds, not milliseconds.
SHUTDOWN_GRACE = 8.0


def _close_when_idle(app, bridge, grace: float = SHUTDOWN_GRACE) -> None:
    """Close the bridge, but not while a worker is mid-exchange.

    Ported from s3ked, along with the finding behind it: closing the port
    tidily is not enough. The application's bridge calls run in worker
    threads, and unwinding the main thread closes the port underneath a
    worker that has sent a request and is waiting for the answer -- leaving
    the instrument composing a reply for a listener that has gone. In the
    sibling project that state wedged an S3000XL until it was power cycled.

    So this takes the same lock every bridge call takes, and waits -- but
    boundedly, because a hung worker must not hold the process open forever.
    """
    lock = getattr(app, "_bridge_lock", None)
    acquired = False
    if lock is not None:
        acquired = lock.acquire(timeout=grace)
    try:
        bridge.close()
    finally:
        if acquired:
            lock.release()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
