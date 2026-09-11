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
numbers that actually select a sound -- Bank Select MSB, Bank Select LSB and
program change -- on every row. That is the whole of the first version, and
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
from typing import Dict, List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Header, Input, Label, Static

from xv import banks
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

    def __init__(self, prompt: str, current: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._current, id="value")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

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
    "[ ] channel", "c set channel", "R re-read",
    "f favourite", "F list", "t tags", "n note", "/ search",
    "r read names", "s scan bank", "x probe SRX",
    "i device", "? help", "q quit",
)


#: Short labels for the bank list's "kind" column. Spelled out rather than
#: truncated to the column width -- "patc" and "perf" are not words, and the
#: column is there to be read at a glance.
_KIND_LABEL = {
    banks.Kind.PATCH: "patch",
    banks.Kind.RHYTHM: "rhythm",
    banks.Kind.PERFORMANCE: "perf",
}


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
        Binding("enter", "select_slot", "Select on synth", priority=True),
        Binding("left_square_bracket", "channel_down", "Channel -"),
        Binding("right_square_bracket", "channel_up", "Channel +"),
        Binding("c", "pick_channel", "Set channel"),
        Binding("R", "refresh_state", "Re-read the synth"),
        Binding("f", "toggle_favorite", "Favourite"),
        Binding("F", "show_favorites", "List favourites"),
        Binding("t", "edit_tags", "Tags"),
        Binding("n", "edit_note", "Note"),
        Binding("slash", "search", "Search"),
        Binding("r", "read_bank", "Read names"),
        Binding("s", "scan_bank", "Scan bank"),
        Binding("x", "probe_srx", "Probe SRX"),
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
        for label, key in (("#", "num"), ("name", "name"), ("MSB", "msb"),
                           ("LSB", "lsb"), ("PC", "pc"), ("cat", "cat"),
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

    def _fill_slots(self, bank_id: str, *, cursor: int = 0) -> None:
        """Rebuild the slot table. Only for when every row changes.

        A change to one cell goes through :meth:`DataTable.update_cell`
        instead: ``clear()`` resets the cursor and *posts* a RowHighlighted,
        which is delivered after this method has returned.
        """
        table = self.query_one("#slot-table", DataTable)
        entry = banks.bank(bank_id)
        self._current_bank = bank_id
        self._current_slots = banks.slots(bank_id)
        favorited = self.favorites.keys_for_bank(bank_id)
        self._filling = True
        try:
            table.clear()
            for slot in self._current_slots:
                name = self.catalog.display_name(bank_id, slot.number)
                if self.catalog.differs(bank_id, slot.number):
                    # The machine and the book disagree. For a USER slot this
                    # is the normal state of a synth somebody uses, and it is
                    # the single most useful thing this screen can point out.
                    shown = f"[b]{name}[/b] [dim]*[/dim]"
                elif self.catalog.is_live(bank_id, slot.number):
                    shown = f"[b]{name}[/b]"
                else:
                    shown = name
                catalog_entry = self.catalog.entry(bank_id, slot.number)
                table.add_row(
                    f"{slot.number:03d}",
                    shown,
                    str(slot.msb),
                    str(slot.lsb),
                    str(slot.program_change),
                    (catalog_entry.category or "") if catalog_entry else "",
                    "*" if slot.number in favorited else "",
                    key=str(slot.number),
                )
        finally:
            self._filling = False
        if 0 < cursor < len(self._current_slots):
            table.move_cursor(row=cursor)
        self._update_subtitle()
        self._update_detail(table.cursor_row)

    def _update_subtitle(self) -> None:
        entry = banks.bank(self._current_bank)
        plural = {"patch": "patches", "rhythm": "rhythm sets",
                  "performance": "performances"}[entry.kind]
        self.sub_title = (
            f"{entry.label} ({self._current_bank}) — {entry.count} {plural}"
            f"   ·   ch {self.target_channel + 1}"
        )

    def _update_detail(self, row: int) -> None:
        if not 0 <= row < len(self._current_slots):
            self.query_one("#detail", Static).update("")
            return
        slot = self._current_slots[row]
        name = self.catalog.display_name(slot.bank_id, slot.number)
        fav = self.favorites.get(slot.bank_id, slot.number)
        lines = [
            f"[b]{slot.bank.label} {slot.number:03d}[/b]  {name}",
            f"Bank Select MSB [b]{slot.msb}[/b] (CC#0)   "
            f"LSB [b]{slot.lsb}[/b] (CC#32)   "
            f"Program Change [b]{slot.program_change}[/b] "
            f"[dim](wire, 0-based; the display shows "
            f"{slot.number})[/dim]",
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
        """Update the two cells a favourite toggle actually changes."""
        slot_table = self.query_one("#slot-table", DataTable)
        try:
            slot_table.update_cell(str(slot.number), "fav",
                                   "*" if now else "")
        except Exception:
            # The row is gone (the bank was switched under us); a full
            # rebuild is the honest fallback and costs one frame.
            self._refresh_current_bank()
        marked = len(self.favorites.keys_for_bank(slot.bank_id))
        try:
            self.query_one("#bank-table", DataTable).update_cell(
                slot.bank_id, "fav", str(marked) if marked else "")
        except Exception:
            pass
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
            f"(MSB {slot.msb}, LSB {slot.lsb}, PC {slot.program_change})"
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

    def action_show_favorites(self) -> None:
        rows = self.favorites.all(order="bank")
        if not rows:
            self.notify_status("no favourites yet -- press f on a slot")
            return
        lines = [f"{len(rows)} favourite(s)", ""]
        for fav in rows:
            label = fav.name or self.catalog.display_name(fav.bank_id,
                                                          fav.number)
            try:
                slot = banks.slot(fav.bank_id, fav.number)
                wire = f"MSB {slot.msb:>3}  LSB {slot.lsb:>3}  PC {slot.program_change:>3}"
            except LookupError:
                # A favourite whose bank rxved no longer defines -- an SRX
                # card removed from the table, say. Shown rather than hidden:
                # the row is the user's, and silently dropping it from the
                # list is how a favourites file quietly rots.
                wire = "[dim]bank not in this build[/dim]"
            extra = []
            if fav.rating:
                extra.append("*" * fav.rating)
            if fav.tags:
                extra.append(fav.tags)
            if fav.note:
                extra.append(fav.note)
            suffix = ("   " + " | ".join(extra)) if extra else ""
            lines.append(
                f"{fav.bank_id:<10} {fav.number:03d}  {label:<14} {wire}{suffix}"
            )
        self.push_screen(ReportScreen("Favourites", "\n".join(lines)))

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
                    wire = (f"MSB {slot.msb:>3}  LSB {slot.lsb:>3}  "
                            f"PC {slot.program_change:>3}")
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

  MSB   Bank Select MSB, CC#0     -- what kind of thing (85 performance,
                                     86 rhythm, 87 patch, 92/93 SRX,
                                     120/121 GM)
  LSB   Bank Select LSB, CC#32    -- which bank within that
  PC    Program Change            -- the byte on the wire, 0-based

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
  f              favourite / un-favourite      F  list favourites
  t / n          tags / note (favourites only)
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
