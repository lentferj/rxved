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

"""Modal screens, the key legend and the multi-mode table.

Split out of :mod:`rxved.app` so the browser itself stays readable: this
module holds every widget the application opens, plus the column allowlists
and display-bias tables the multi-mode screen edits through. It imports
nothing from :mod:`rxved.app`, so the dependency runs one way only.

``wrap_blocks`` and ``KeyHints`` used to be defined here, ported from the
sibling s3ked project and from there from eosed and k2kremote -- four copies
of the same widget by the time this file's version was written. They are now
the family's: :mod:`vinsynlib.keys` and :mod:`vinsynlib.ui.hints`, imported
below and re-exported so every existing call site reads exactly as it did.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, Static, Checkbox
from vinsynlib import keys
from vinsynlib.keys import wrap_blocks
from vinsynlib.ui.hints import KeyHints

from xv import banks
from xv import params
from xv import catalog as cat

__all__ = [
    "ConfirmScreen",
    "TextPromptScreen",
    "ReportScreen",
    "wrap_blocks",
    "KeyHints",
    "KEY_HINTS",
    "VIEW_ALL",
    "VIEW_BANK_FAVOURITES",
    "VIEW_ALL_FAVOURITES",
    "VIEW_CYCLE",
    "VIEW_LABEL",
    "EDITABLE_PART_COLUMNS",
    "MIDI_COLUMNS",
    "FX_COLUMNS",
    "RX_COLUMNS",
    "TONE_COLUMNS",
    "COLUMN_VIEWS",
    "EDITABLE_CHANNEL_COLUMNS",
    "EDITABLE_COMMON_COLUMNS",
    "StoreScreen",
    "PerformanceScreen",
    "CommonScreen",
    "PatchPickerScreen",
    "MultiScreen",
    "CategoryScreen",
    "SearchScreen",
]


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

    def __init__(
        self, prompt: str, current: str = "", select_all: bool = False
    ) -> None:
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


class SearchScreen(ModalScreen[Optional[Tuple[str, Dict[str, bool]]]]):
    """Search prompt with checkboxes for what to search in.

    Returns (needle, options) or None if cancelled.
    """

    DEFAULT_CSS = """
    SearchScreen { align: center middle; }
    SearchScreen > Vertical {
        width: 70; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    SearchScreen .checkbox-row { height: auto; margin-top: 1; }
    SearchScreen Checkbox { margin-right: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        # Tab moves between input and checkboxes
    ]

    def __init__(
        self,
        *,
        search_names: bool = True,
        search_tags: bool = True,
        search_notes: bool = True,
    ) -> None:
        super().__init__()
        self._search_names = search_names
        self._search_tags = search_tags
        self._search_notes = search_notes

    def compose(self) -> ComposeResult:
        from textual.widgets import Checkbox

        with Vertical():
            yield Label("Search")
            yield Input(placeholder="search term…", id="value")
            with Horizontal(classes="checkbox-row"):
                yield Checkbox("names", value=self._search_names, id="opt-names")
                yield Checkbox("tags", value=self._search_tags, id="opt-tags")
                yield Checkbox("notes", value=self._search_notes, id="opt-notes")

    def on_mount(self) -> None:
        field = self.query_one("#value", Input)
        field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._finish(event.value)

    def _finish(self, needle: str) -> None:
        options = {
            "names": self.query_one("#opt-names", Checkbox).value,
            "tags": self.query_one("#opt-tags", Checkbox).value,
            "notes": self.query_one("#opt-notes", Checkbox).value,
        }
        # Require at least one option
        if not any(options.values()):
            self.app.notify_status("select at least one field", refused=True)
            return
        self.dismiss((needle, options))

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


#: The legend. Every binding the app has, in the order somebody meets them.
#: Nothing is omitted, because :class:`vinsynlib.ui.hints.KeyHints` wraps
#: rather than truncating.
#:
#: Built from the family's shared legend rather than hand-written. It used to
#: be a tuple of its own, in its own order, and it had already drifted: the
#: shared keys sat wherever they were added rather than in the shared order.
#: ``keys.legend`` puts this tool's own hints in the one place the family
#: leaves for them -- after `n note`, before `i device` -- so a reader's eye
#: finds them in the same spot in all nine programs.
def _view_key(blocks):
    """Swap the family's ``F favourites view`` for this tool's ``v``.

    ``f`` writes the favourites database and ``F`` does not, so the family's
    shift-pair left a write one slip away from a read. ``v`` has no such
    twin. The family asks a tool that renames a shared key to say why in its
    own test; see tests/test_conformance.py.
    """
    return tuple(
        "v favourites view" if block == "F favourites view" else block
        for block in blocks
    )


KEY_HINTS = _view_key(
    keys.legend(
        (
            # This synth's own: the two that read from the instrument rather
            # than from the catalog, the two that re-read its whole state, and
            # the one that changes what the synth itself is doing.
            "s scan bank",
            "x probe SRX",
            "C categories",
            "R re-read",
            "m multi setup",
            # The family keeps undo in its editor tier; this browser writes
            # the favourites store, so it carries the same two keys as its own.
            "z undo",
            "Z undo all",
        )
    )
)


#: What the right-hand pane is showing. Cycled by `v`.
#:
#: ``BANK`` and ``FAVOURITES`` are filters over the same table rather than a
#: separate read-only screen, because the point of a favourites list is to
#: play the things on it -- everything that works in the full list (Enter to
#: select, `f` to un-favourite, tags, notes) has to keep working here.
VIEW_ALL = "all"
VIEW_BANK_FAVOURITES = "bank-favourites"
VIEW_ALL_FAVOURITES = "all-favourites"

#: The order `v` steps through.
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
    "leg": (0x0C, "legato (0 OFF, 1 ON, 2 PATCH)", 0, 2),
    "pts": (0x0E, "portamento switch (0 OFF, 1 ON, 2 PATCH)", 0, 2),
    "ptt": (0x0F, "portamento time (128 = PATCH)", 0, 128),
    "cof": (0x11, "cutoff offset", -64, 63),
    "res": (0x12, "resonance offset", -64, 63),
    "atk": (0x13, "attack time offset", -64, 63),
    "dcy": (0x21, "decay time offset", -64, 63),
    "rel": (0x14, "release time offset", -64, 63),
    "vrt": (0x22, "vibrato rate", -64, 63),
    "vdp": (0x23, "vibrato depth", -64, 63),
    "vdl": (0x24, "vibrato delay", -64, 63),
    "kfl": (0x19, "keyboard fade width lower", 0, 127),
    "kfu": (0x1A, "keyboard fade width upper", 0, 127),
}

#: The two column sets the part table shows, toggled by `tab`. Sixteen parts
#: times thirteen parameters does not fit a terminal row, and cramming it
#: would cost the patch name -- which is the one column that says what a
#: part *is* rather than how it is set.
MIDI_COLUMNS = (
    ("ch", "ch"),
    ("rx", "rx"),
    ("lvl", "lvl"),
    ("PC", "pc"),
    ("LSB", "lsb"),
    ("MSB", "msb"),
)
FX_COLUMNS = (
    ("mute", "mute"),
    ("dry", "dry"),
    ("cho", "cho"),
    ("rev", "rev"),
    ("out", "out"),
    ("mfx", "mfx"),
)

#: Per-**channel** receive switches (Performance MIDI). Shown on the part
#: row for the channel that part listens on, which means two parts sharing a
#: channel show the same values -- because they genuinely share them.
RX_COLUMNS = (
    ("ch", "ch"),
    ("rxPC", "rx_pc"),
    ("rxBS", "rx_bs"),
    ("bend", "rx_bend"),
    ("mod", "rx_mod"),
    ("vol", "rx_vol"),
    ("hold", "rx_hold"),
)

#: Per-part musical settings. Most of these are stored biased by 64 and are
#: shown here as the manual prints them -- see _BIAS.
TONE_COLUMNS = (
    ("pan", "pan"),
    ("oct", "oct"),
    ("crs", "crs"),
    ("fin", "fin"),
    ("bend", "bend"),
    ("mono", "mono"),
    ("lo", "lo"),
    ("hi", "hi"),
)

#: The TVF/TVA offsets and the vibrato. Kept together because they are the
#: sound-shaping block a person tweaks after picking the patch; most are
#: stored biased by 64, like the tone columns.
OFFSET_COLUMNS = (
    ("cof", "cof"),
    ("res", "res"),
    ("atk", "atk"),
    ("dcy", "dcy"),
    ("rel", "rel"),
    ("vrt", "vrt"),
    ("vdp", "vdp"),
    ("vdl", "vdl"),
)

#: Portamento and the keyboard-range fade widths.
PORTA_COLUMNS = (
    ("leg", "leg"),
    ("pts", "pts"),
    ("ptt", "ptt"),
    ("kfl", "kfl"),
    ("kfu", "kfu"),
)

#: Voice Reserve, one column: per-part in meaning, but stored in the
#: Performance Common block, so its own view keeps that visible.
RESERVE_COLUMNS = (("vres", "vres"),)

#: The column sets `tab` cycles through.
COLUMN_VIEWS = (
    ("MIDI", MIDI_COLUMNS),
    ("FX / routing", FX_COLUMNS),
    ("receive switches", RX_COLUMNS),
    ("tone", TONE_COLUMNS),
    ("offsets / vibrato", OFFSET_COLUMNS),
    ("porta / key", PORTA_COLUMNS),
    ("voice reserve", RESERVE_COLUMNS),
)

#: Part parameters that live in the Performance Common block rather than the
#: part block. Voice Reserve is the only one: offset ``00 10`` is part 1, so
#: the real address is ``00 10 + part - 1`` and the write goes through the
#: common path. The offset here is the base, used for the label and range
#: only -- :meth:`MultiScreen._apply` adds the part.
EDITABLE_COMMON_COLUMNS = {
    "vres": (0x10, "voice reserve", 0, 64),
}

#: display = wire - bias, wire = display + bias. Every entry is a place the
#: synth's byte and the manual's number differ, collected in one dict so
#: that the conversion is one line of code in one direction and one in the
#: other -- see xv/banks.py on why this project never lets those two drift.
_BIAS = {
    "ch": -1,
    "pan": 64,
    "crs": 64,
    "fin": 64,
    "oct": 64,
    "cof": 64,
    "res": 64,
    "atk": 64,
    "dcy": 64,
    "rel": 64,
    "vrt": 64,
    "vdp": 64,
    "vdl": 64,
}

#: Column key -> the ChannelMidi attribute it shows.
_CHANNEL_FIELDS = {
    "rx_pc": "program_change",
    "rx_bs": "bank_select",
    "rx_bend": "bender",
    "rx_mod": "modulation",
    "rx_vol": "volume",
    "rx_hold": "hold_1",
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
                f"source: {self._source or 'the temporary performance'}"
            )
            yield DataTable(id="slot-table", cursor_type="row", zebra_stripes=True)
            yield Static(self._status(), id="store-status")

    def on_mount(self) -> None:
        table = self.query_one("#slot-table", DataTable)
        table.add_column("slot", key="slot")
        table.add_column("currently holds", key="name")
        for slot in range(1, 65):
            table.add_row(f"{slot:02d}", self._name_cell(slot), key=str(slot))
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
            return "[dim]a to arm the slot under the cursor · esc to cancel[/dim]"
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
                "not armed — press a on the destination slot first", refused=True
            )
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


class PerformanceScreen(ModalScreen[Optional[banks.Slot]]):
    """Pick a stored performance to load into the edit buffer.

    The counterpart to :class:`StoreScreen`: that one writes the edit buffer
    into a user slot, this one reads a slot into the buffer. Loading replaces
    every byte of the buffer, which is why it is its own screen and not a
    keystroke on the part table.

    The list is the whole performance map -- User 1-64, Preset A and B 1-32 --
    with the names from the catalog, and the performance the synth is on now
    marked, so "where am I" is answerable without leaving the picker.
    """

    DEFAULT_CSS = """
    PerformanceScreen { align: center middle; }
    PerformanceScreen > Vertical {
        width: 76; height: 80%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    PerformanceScreen DataTable { height: 1fr; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
    ]

    #: The banks that hold performances, in the order the picker shows them.
    BANKS = ("P-USER", "P-PST-A", "P-PST-B")

    def __init__(self, catalog, current: Optional[Tuple[int, int, int]] = None) -> None:
        super().__init__()
        self._catalog = catalog
        #: ``(msb, lsb, program_change)`` the synth is on, to mark its row.
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[b]Load a performance into the edit buffer[/b]")
            yield DataTable(id="perf-table", cursor_type="row", zebra_stripes=True)
            yield Static(
                "[dim]⏎ load · esc cancel · loading replaces the edit buffer "
                "(save it with W first)[/dim]",
                id="perf-status",
            )

    def on_mount(self) -> None:
        table = self.query_one("#perf-table", DataTable)
        table.add_column("bank", key="bank")
        table.add_column("#", key="number")
        table.add_column("name", key="name")
        current_row = 0
        for bank_id in self.BANKS:
            for number in banks.bank(bank_id).numbers():
                slot = banks.slot(bank_id, number)
                name = self._catalog.display_name(bank_id, number)
                mark = ""
                if self._current == (slot.msb, slot.lsb, slot.program_change):
                    mark = "  [b]← current[/b]"
                    # row_count is the index this row is about to take.
                    current_row = table.row_count
                table.add_row(bank_id, f"{number:03d}", name + mark, key=slot.key)
        table.focus()
        if table.row_count:
            table.move_cursor(row=current_row)

    def _slot(self) -> Optional[banks.Slot]:
        table = self.query_one("#perf-table", DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate)[0].value
        bank_id, _, number = key.rpartition(":")
        try:
            return banks.slot(bank_id, int(number))
        except (LookupError, ValueError):
            return None

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id == "perf-table":
            self.dismiss(self._slot())

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommonScreen(ModalScreen[None]):
    """Temporary Performance Common, editable.

    The six settings that are not per-part, so they do not belong on the
    16-row part table: the name, Solo Part Select, the MFX control channel,
    and the three effect sources. Everything here writes to the temporary
    performance and is read back, exactly like a part edit.

    The five ranged rows take the same three gestures as the part table --
    Enter, a digit, or `+`/`-` -- because a value with a clear range should
    not be changed one way here and another way one screen over. The name is
    the exception: it has no range, so Enter is the way in.
    """

    DEFAULT_CSS = """
    CommonScreen { align: center middle; }
    CommonScreen > Vertical {
        width: 72; height: auto; max-height: 80%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    CommonScreen DataTable { height: auto; max-height: 8; }
    CommonScreen .hint { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        # The same three gestures the part table has, so a value with a clear
        # range is changed the same way in both places: Enter opens the
        # prompt, a digit opens it already holding that digit, and +/- step
        # the value in place. The name has no range, so all three but Enter
        # leave it alone.
        Binding("plus", "bump(1)", "+1", show=False),
        Binding("equals_sign", "bump(1)", "+1", show=False),
        Binding("minus", "bump(-1)", "-1", show=False),
    ] + [
        Binding(str(digit), f"type_digit('{digit}')", show=False) for digit in range(10)
    ]

    #: ``(key, label, offset, zero_means)``. ``offset`` is None for the name;
    #: ``zero_means`` is what value 0 displays as, and None marks the name.
    FIELDS = (
        ("name", "Name", None, None),
        ("solo", "Solo Part Select", 0x0C, "OFF"),
        ("mfx_ctrl", "MFX Control Channel", 0x0D, "OFF"),
        ("mfx_src", "MFX Source", 0x30, "PERFORM"),
        ("chorus_src", "Chorus Source", 0x33, "PERFORM"),
        ("reverb_src", "Reverb Source", 0x34, "PERFORM"),
    )

    def __init__(self, common, *, on_write=None, on_write_name=None) -> None:
        super().__init__()
        self._common = common
        #: ``on_write(offset, value, adopt)``.
        self._on_write = on_write
        #: ``on_write_name(name, adopt)``.
        self._on_write_name = on_write_name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[b]Performance Common — the edit buffer[/b]")
            yield DataTable(id="common-table", cursor_type="row", zebra_stripes=True)
            yield Static(
                "[dim]⏎ edits · type a number, or +/- to adjust · esc closes · "
                "writes go to the temporary performance[/dim]",
                classes="hint",
            )

    def on_mount(self) -> None:
        table = self.query_one("#common-table", DataTable)
        table.add_column("setting", key="setting")
        table.add_column("value", key="value")
        for key, label, _offset, _zero in self.FIELDS:
            table.add_row(label, self._display(key), key=key)
        table.focus()

    def _value(self, key: str):
        return {
            "name": self._common.name,
            "solo": self._common.solo,
            "mfx_ctrl": self._common.mfx_control_channel,
            "mfx_src": self._common.mfx_source,
            "chorus_src": self._common.chorus_source,
            "reverb_src": self._common.reverb_source,
        }[key]

    def _display(self, key: str) -> str:
        field = next(f for f in self.FIELDS if f[0] == key)
        _key, _label, _offset, zero = field
        value = self._value(key)
        if zero is None:
            return value or "[dim](unnamed)[/dim]"
        if value is None:
            return zero
        return f"PART {value}" if zero == "PERFORM" else str(value)

    def _field(self):
        table = self.query_one("#common-table", DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate)[0].value
        return next((f for f in self.FIELDS if f[0] == key), None)

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id == "common-table":
            self.action_edit()

    def action_edit(self) -> None:
        field = self._field()
        if field is None:
            return
        key, label, offset, zero = field
        current = self._value(key)
        if zero is None:
            self.app.push_screen(
                TextPromptScreen(f"{label} (up to 12 characters)", current or ""),
                self._set_name,
            )
            return
        seed = "0" if current is None else str(current)
        self.app.push_screen(
            TextPromptScreen(f"{label} (0 = {zero}, 1-16)", seed, select_all=True),
            lambda text: self._set_value(offset, label, text),
        )

    def action_bump(self, delta: int) -> None:
        """`+`/`-` step a ranged value in place, as on the part table."""
        field = self._field()
        if field is None:
            return
        _key, _label, _offset, zero = field
        if zero is None:
            # The name has no range to step; Enter is the way in.
            return
        self._bump(field, delta)

    def action_type_digit(self, digit: str) -> None:
        """A digit opens the value prompt already holding it."""
        field = self._field()
        if field is None:
            return
        _key, label, offset, zero = field
        if zero is None:
            return  # the name is text; Enter is the way in
        self.app.push_screen(
            TextPromptScreen(f"{label} (0 = {zero}, 1-16)", digit),
            lambda text: self._set_value(offset, label, text),
        )

    def _bump(self, field, delta: int) -> None:
        key, label, offset, _zero = field
        current = self._value(key)
        self._write_value(offset, label, (0 if current is None else current) + delta)

    def _set_name(self, text) -> None:
        if text is None or self._on_write_name is None:
            return
        self._on_write_name(text, self.update_common)

    def _set_value(self, offset, label, text) -> None:
        if text is None or not text.strip():
            return
        try:
            value = int(text.strip())
        except ValueError:
            self.app.notify_status(f"{text!r} is not a number", refused=True)
            return
        self._write_value(offset, label, value)

    def _write_value(self, offset, label, value: int) -> None:
        if not 0 <= value <= 16:
            self.app.notify_status(
                f"{label} takes 0 (OFF/PERFORM) to 16, not {value}", refused=True
            )
            return
        if self._on_write is not None:
            self._on_write(offset, value, self.update_common)

    def update_common(self, common) -> None:
        """Replace the screen's common with what the device reported."""
        self._common = common
        table = self.query_one("#common-table", DataTable)
        for key, _label, _offset, _zero in self.FIELDS:
            table.update_cell(key, "value", self._display(key))

    def action_close(self) -> None:
        self.dismiss(None)


class PatchPickerScreen(ModalScreen[Optional[banks.Slot]]):
    """Pick a sound for a part by name, not by Program Change number.

    The part table's LSB/MSB/PC columns are the wire truth, and nobody
    knows which program change "Stereo Piano" is. This lists every bank the
    table defines -- the internal patches and rhythm sets, the GM
    variations, and the SRX pages -- with the names from the catalog, and
    returns the slot the person chose.

    Two panes: the banks, and the selected bank's slots. `tab` moves between
    them; the arrow keys move within; `⏎` picks. `v` steps the slot pane
    through the same three views the browser has -- all slots, this bank's
    favourites, every favourite -- so a person who marked favourites there
    can pick from them here.
    """

    DEFAULT_CSS = """
    PatchPickerScreen { align: center middle; }
    PatchPickerScreen > Vertical {
        width: 90; height: 90%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    PatchPickerScreen DataTable { height: 1fr; width: 1fr; }
    PatchPickerScreen .hint { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("tab", "switch_pane", "Switch pane", show=False),
        Binding("v", "cycle_view", "Favourites view", show=False),
    ]

    def __init__(self, catalog, current=None, favorites=None) -> None:
        super().__init__()
        self._catalog = catalog
        #: The slot the part is on now, marked in the list.
        self._current = current
        #: The favourites store, for the `v` view cycle. None in a test that
        #: does not care about favourites; the cycle then only has "all".
        self._favorites = favorites
        #: Index into VIEW_CYCLE, cycled by `v` -- the same three views the
        #: browser's own table has.
        self._view = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title(), id="picker-title")
            with Horizontal():
                yield DataTable(
                    id="picker-banks", cursor_type="row", zebra_stripes=True
                )
                yield DataTable(
                    id="picker-slots", cursor_type="row", zebra_stripes=True
                )
            yield Static(
                "[dim]tab pane · v favourites view · ⏎ pick · esc cancel[/dim]",
                classes="hint",
            )

    def on_mount(self) -> None:
        table = self.query_one("#picker-banks", DataTable)
        table.add_column("bank", key="bank")
        for bank in banks.BANKS:
            if bank.kind in (banks.Kind.PATCH, banks.Kind.RHYTHM):
                table.add_row(f"{bank.label} ({bank.id})", key=bank.id)
        table.focus()
        self._fill_slots()

    def _bank_id(self) -> Optional[str]:
        table = self.query_one("#picker-banks", DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate)[0].value

    def _fill_slots(self) -> None:
        table = self.query_one("#picker-slots", DataTable)
        table.clear(columns=True)
        table.add_column("bank / #", key="number")
        table.add_column("name", key="name")
        table.add_column("MSB", key="msb")
        table.add_column("LSB", key="lsb")
        table.add_column("PC", key="pc")
        for slot in self._visible_slots():
            name = self._catalog.display_name(slot.bank_id, slot.number)
            mark = "  [b]← current[/b]" if self._matches(slot) else ""
            table.add_row(
                f"{slot.bank_id} {slot.number:03d}",
                name + mark,
                str(slot.msb),
                str(slot.lsb),
                str(slot.program_change),
                key=slot.key,
            )

    def _visible_slots(self) -> list[banks.Slot]:
        """The rows the slot pane should show, for the current view.

        The same three views the browser's own table cycles through, so the
        favourites marked there can be picked from here as well. Row keys are
        ``slot.key`` (``PST-B:029``) rather than the number: the all-favourites
        view spans banks, and two banks can both hold a slot 29.
        """
        view = VIEW_CYCLE[self._view]
        if view == VIEW_ALL:
            bank_id = self._bank_id()
            return banks.slots(bank_id) if bank_id is not None else []
        if self._favorites is None:
            return []
        if view == VIEW_BANK_FAVOURITES:
            bank_id = self._bank_id()
            if bank_id is None:
                return []
            marked = self._favorites.keys_for_bank(bank_id)
            return [s for s in banks.slots(bank_id) if s.number in marked]
        rows = []
        for favourite in self._favorites.all(order="bank"):
            try:
                rows.append(banks.slot(favourite.bank_id, favourite.number))
            except LookupError:
                # A favourite for a bank this build no longer defines.
                # Skipped rather than crashing the view.
                continue
        return rows

    def _title(self) -> str:
        view = VIEW_CYCLE[self._view]
        return f"[b]Pick a sound for this part[/b]  [dim]· {VIEW_LABEL[view]}[/dim]"

    def _matches(self, slot: banks.Slot) -> bool:
        if self._current is None:
            return False
        return (self._current.msb, self._current.lsb, self._current.program_change) == (
            slot.msb,
            slot.lsb,
            slot.program_change,
        )

    def on_data_table_row_highlighted(self, event) -> None:
        if event.data_table.id != "picker-banks":
            return
        # The bank pane only chooses rows in the two bank-scoped views; in
        # the all-favourites view every bank is already listed.
        if VIEW_CYCLE[self._view] == VIEW_ALL_FAVOURITES:
            return
        self._fill_slots()

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id == "picker-slots":
            self.dismiss(self._slot())

    def _slot(self) -> Optional[banks.Slot]:
        table = self.query_one("#picker-slots", DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate)[0].value
        bank_id, _, number = key.rpartition(":")
        try:
            return banks.slot(bank_id, int(number))
        except (LookupError, ValueError):
            return None

    def action_switch_pane(self) -> None:
        focused = self.focused
        if isinstance(focused, DataTable) and focused.id == "picker-banks":
            self.query_one("#picker-slots", DataTable).focus()
        else:
            self.query_one("#picker-banks", DataTable).focus()

    def action_cycle_view(self) -> None:
        """`v`: all slots, this bank's favourites, every favourite."""
        self._view = (self._view + 1) % len(VIEW_CYCLE)
        self.query_one("#picker-title", Label).update(self._title())
        self._fill_slots()

    def action_cancel(self) -> None:
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
        Binding("p", "pick_performance", "Load a performance", show=False),
        Binding("c", "common", "Performance common", show=False),
        Binding("s", "pick_patch", "Pick a sound", show=False),
        Binding("r", "refresh", "Refresh", show=False),
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

    def __init__(
        self,
        state,
        catalog,
        *,
        on_write=None,
        on_write_channel=None,
        on_refresh=None,
        on_load=None,
        on_pick_patch=None,
        on_write_common=None,
        on_write_common_name=None,
        on_close=None,
    ) -> None:
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
        #: ``on_refresh()`` -- called when the user presses 'r' to re-read
        #: the full state from the synth.
        self._on_refresh = on_refresh
        #: ``on_load(slot)`` -- called when the user picks a stored
        #: performance to load into the edit buffer.
        self._on_load = on_load
        #: ``on_pick_patch(part, slot, adopt)`` -- set a part's sound by name.
        self._on_pick_patch = on_pick_patch
        #: ``on_write_common(offset, value, adopt)`` -- Performance Common.
        self._on_write_common = on_write_common
        #: ``on_write_common_name(name, adopt)``.
        self._on_write_common_name = on_write_common_name
        #: ``on_close()`` -- called when the screen is dismissed.
        self._on_close = on_close

    # --- building ------------------------------------------------------------

    def _title(self) -> str:
        state = self._state
        title = f"Multi-mode setup — {state.setup.mode_name} mode"
        common = state.common
        if common is not None and common.name:
            title += f" — performance “{common.name}”"
        return title

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"[b]{self._title()}[/b]", id="multi-title")
            yield Static(self._fx_summary(), classes="hint", id="fx")
            yield DataTable(id="part-table", cursor_type="cell", zebra_stripes=True)
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
            f"⏎ to edit · space toggles · +/- adjust · r refresh · "
            f"p loads a performance · c common · s pick sound · W writes to a slot · "
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
        return (("part", "part"),) + middle + (("patch", "patch"), ("", "flag"))

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
            value = {"dry": part.dry, "cho": part.chorus, "rev": part.reverb}[column]
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
        if column == "vres":
            return str(self._current_value(part, "vres"))
        if column in (
            "pan",
            "oct",
            "crs",
            "fin",
            "cof",
            "res",
            "atk",
            "dcy",
            "rel",
            "vrt",
            "vdp",
            "vdl",
        ):
            value = self._current_value(part, column)
            return f"+{value}" if value > 0 else str(value)
        if column in ("leg", "pts"):
            return params.ON_OFF_PATCH.get(self._current_value(part, column), "?")
        if column == "ptt":
            value = self._current_value(part, "ptt")
            return "PATCH" if value >= 128 else str(value)
        if column == "bend":
            return "PAT" if part.bend_range == 25 else str(part.bend_range)
        if column == "mono":
            return params.MONO_POLY.get(part.mono_poly, str(part.mono_poly))
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
        middle = tuple(
            self._cell_text(part, key) for _label, key in self._columns()[1:-2]
        )
        return (str(part.part),) + middle + (name, f"[b]{reason}[/b]" if reason else "")

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
        row_key, column_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        column = column_key.value
        # Allow "patch" column to be editable (maps to "pc" for bump/type)
        if (
            column not in EDITABLE_PART_COLUMNS
            and column not in EDITABLE_CHANNEL_COLUMNS
            and column not in EDITABLE_COMMON_COLUMNS
            and column != "patch"
        ):
            return None, column
        part = next(
            (p for p in self._state.parts if str(p.part) == row_key.value), None
        )
        return part, column

    def _current_value(self, part, column: str) -> int:
        """The value as this column displays it -- 1-based for ch."""
        if column == "vres":
            common = self._state.common
            if common is None or part.part - 1 >= len(common.voice_reserves):
                return 0
            return common.voice_reserves[part.part - 1]
        if column in _BIAS and column != "ch":
            wire = {
                "pan": part.pan,
                "crs": part.coarse,
                "fin": part.fine,
                "oct": part.octave,
                "cof": part.cutoff_offset,
                "res": part.resonance_offset,
                "atk": part.attack_offset,
                "dcy": part.decay_offset,
                "rel": part.release_offset,
                "vrt": part.vibrato_rate,
                "vdp": part.vibrato_depth,
                "vdl": part.vibrato_delay,
            }[column]
            return wire - _BIAS[column]
        return (
            {
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
                "leg": part.legato,
                "pts": part.portamento_switch,
                "ptt": part.portamento_time,
                "kfl": part.key_fade_lower,
                "kfu": part.key_fade_upper,
            }.get(column)
            if column not in EDITABLE_CHANNEL_COLUMNS
            else (self._channel_value(part, column))
        )

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
        # "patch" column maps to "pc" for editing
        if column == "patch":
            column = "pc"
        if self._is_switch(column):
            if digit in ("0", "1"):
                self._apply(part, column, int(digit))
            else:
                self.app.notify_status(
                    f"{self._spec(column)[1]} is 0 or 1", refused=True
                )
            return
        self._prompt_for_value(part, column, seed=digit)

    def action_edit_cell(self) -> None:
        part, column = self._cursor()
        if part is None:
            self.app.notify_status(
                f"{column} is not editable" if column else "nothing to edit",
                refused=True,
            )
            return
        # "patch" column maps to "pc" for editing
        if column == "patch":
            column = "pc"
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
            part, column, seed=str(self._current_value(part, column)), select_all=True
        )

    def _prompt_for_value(
        self, part, column: str, *, seed: str, select_all: bool = False
    ) -> None:
        _offset, label, low, high = self._spec(column)

        def done(text) -> None:
            if text is None or not text.strip():
                return
            try:
                value = int(text.strip())
            except ValueError:
                self.app.notify_status(f"{text!r} is not a number", refused=True)
                return
            self._apply(part, column, value)

        self.app.push_screen(
            TextPromptScreen(
                f"Part {part.part} — {label} ({low}-{high})",
                seed,
                select_all=select_all,
            ),
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
        """``(offset, label, low, high)`` for any of the allowlists."""
        if column in EDITABLE_CHANNEL_COLUMNS:
            return EDITABLE_CHANNEL_COLUMNS[column]
        if column in EDITABLE_COMMON_COLUMNS:
            return EDITABLE_COMMON_COLUMNS[column]
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
        # "patch" column maps to "pc" for bump operations
        if column == "patch":
            column = "pc"
        _offset, _label, low, high = self._spec(column)
        value = self._current_value(part, column) + delta
        if low <= value <= high:
            self._apply(part, column, value)

    def _apply(self, part, column: str, value: int) -> None:
        offset, label, low, high = self._spec(column)
        if not low <= value <= high:
            self.app.notify_status(
                f"{label} takes {low}-{high}, not {value}", refused=True
            )
            return
        if column in EDITABLE_COMMON_COLUMNS:
            self._apply_common(part, value, offset)
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

    def _apply_common(self, part, value: int, base: int) -> None:
        """Write a per-part value that lives in the Performance Common block.

        Voice Reserve is the one: the allowlist gives the base offset ``00
        10``, and the part number picks the byte from there, so part 1 is
        ``00 10`` and part 16 is ``00 1F``.
        """
        if self._on_write_common is None:
            self.app.notify_status("not connected to a synth", refused=True)
            return
        self._on_write_common(base + part.part - 1, value, self._adopt_common)

    def _on_write_channel(self, channel: int, offset: int, value: int) -> None:
        if self._on_write_channel_cb is None:
            self.app.notify_status("not connected to a synth", refused=True)
            return
        self._on_write_channel_cb(channel, offset, value, self._adopt_channel)

    def _adopt_channel(self, fresh) -> None:
        """Replace one channel's MIDI block with what the device reported."""
        self._state = replace(
            self._state,
            midi=tuple(
                fresh if e.channel == fresh.channel else e for e in self._state.midi
            ),
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
            parts=tuple(
                fresh if p.part == fresh.part else p for p in self._state.parts
            ),
        )
        table = self.query_one("#part-table", DataTable)
        for column, cell in zip(table.columns.values(), self._row_cells(fresh)):
            table.update_cell(str(fresh.part), column.key, cell)
        self.query_one("#report", Static).update(self._report_text())

    def _adopt_common(self, common) -> None:
        """Replace the Performance Common after a Voice Reserve write.

        Only Voice Reserve is editable from this table, so only that column
        is repainted -- a full rebuild would move the cursor. The column is
        absent in every other view, and the person may have switched views
        while the write was in flight, so the test is against the current
        one rather than the one that was on screen when they pressed.
        """
        self._state = replace(self._state, common=common)
        if any(key == "vres" for _label, key in self._columns()):
            table = self.query_one("#part-table", DataTable)
            for part in self._state.parts:
                table.update_cell(str(part.part), "vres", self._cell_text(part, "vres"))
        self.query_one("#report", Static).update(self._report_text())

    def action_pick_performance(self) -> None:
        """Pick a stored performance and load it into the edit buffer."""
        setup = self._state.setup
        current = (
            setup.performance_msb,
            setup.performance_lsb,
            setup.performance_program,
        )

        def chosen(slot) -> None:
            if slot is not None and self._on_load is not None:
                self._on_load(slot)

        self.app.push_screen(PerformanceScreen(self._catalog, current), chosen)

    def _cursor_part(self):
        """The part under the cursor, whatever the column is."""
        table = self.query_one("#part-table", DataTable)
        if not table.row_count:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate)[0]
        return next(
            (p for p in self._state.parts if str(p.part) == row_key.value), None
        )

    def action_pick_patch(self) -> None:
        """Pick this part's sound by bank and name."""
        part = self._cursor_part()
        if part is None:
            return

        def chosen(slot) -> None:
            if slot is not None and self._on_pick_patch is not None:
                self._on_pick_patch(part.part, slot, self._adopt_part)

        self.app.push_screen(
            PatchPickerScreen(self._catalog, part.slot, self.app.favorites), chosen
        )

    def action_common(self) -> None:
        """Edit the Performance Common block (name, solo, effect sources)."""
        if self._state.common is None:
            self.app.notify_status("performance common not read", refused=True)
            return
        self.app.push_screen(
            CommonScreen(
                self._state.common,
                on_write=self._on_write_common,
                on_write_name=self._on_write_common_name,
            )
        )

    def action_store(self) -> None:
        name = ""
        if self._state.common is not None:
            name = self._state.common.name
        self.app.open_store_screen(name)

    def action_refresh(self) -> None:
        """Re-read the full multi-mode state from the synth."""
        if self._on_refresh is None:
            self.app.notify_status("not connected to a synth", refused=True)
            return
        self._on_refresh()

    def update_common(self, common) -> None:
        """Replace the performance common, and the title that names it."""
        self._state = replace(self._state, common=common)
        self.query_one("#multi-title", Label).update(f"[b]{self._title()}[/b]")

    def update_state(self, state) -> None:
        """Replace the screen's state with fresh data from the synth."""
        self._state = state
        self._build_table()
        self.query_one("#fx", Static).update(self._fx_summary())
        self.query_one("#report", Static).update(self._report_text())
        self.query_one("#hint", Static).update(self._hint_text())

    def action_close(self) -> None:
        if self._on_close is not None:
            self._on_close()
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
            yield Label("[b]Categories[/b] — narrows whatever is already shown")
            yield DataTable(id="cats", cursor_type="row")
            yield Static(
                "[b]space[/b] toggle   [b]a[/b] all   [b]x[/b] clear"
                "   [b]enter[/b] apply   [b]esc[/b] cancel"
            )

    def on_mount(self) -> None:
        table = self.query_one("#cats", DataTable)
        for label, key in (("", "on"), ("cat", "code"), ("", "name"), ("n", "count")):
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
