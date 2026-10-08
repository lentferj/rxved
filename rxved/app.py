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
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Header, Static, Checkbox
from vinsynlib.cli import add_common_arguments, make_parser, validate_common
from vinsynlib.spec import flag_help

from xv import banks
from xv import catalog as cat
from xv.config import load_ui_state, save_ui_state

from rxved import livenames

__all__ = [
    "RxvedApp",
    "main",
    # Re-exported for backward compatibility: these live in
    # :mod:`rxved.screens` now, but tests and callers import them from here.
    "ConfirmScreen",
    "TextPromptScreen",
    "ReportScreen",
    "StoreScreen",
    "PerformanceScreen",
    "MultiScreen",
    "CategoryScreen",
    "KeyHints",
    "wrap_blocks",
    "KEY_HINTS",
    "VIEW_ALL",
    "VIEW_BANK_FAVOURITES",
    "VIEW_ALL_FAVOURITES",
    "VIEW_CYCLE",
    "VIEW_LABEL",
    "EDITABLE_PART_COLUMNS",
    "EDITABLE_CHANNEL_COLUMNS",
    "_BIAS",
]

sys.setrecursionlimit(10000)

#: How long to let the synth load a performance before reading it back.
#: Reading too early returns the previous performance, silently -- the same
#: failure mode `scan_bank`'s retry loop exists for.
PERFORMANCE_LOAD_SETTLE = 0.2

#: What a bridge call can raise when the synth, the port or the arguments
#: are at fault. Workers catch exactly this -- not bare ``Exception`` -- so
#: a programming error (AttributeError, TypeError, ...) still fails loudly
#: instead of surfacing as a status-line "read failed".
_BRIDGE_ERRORS = (
    TimeoutError,
    ValueError,
    LookupError,
    RuntimeError,
    OSError,
    SystemError,
)


# Screens, the legend and the multi-mode table live in :mod:`rxved.screens`;
# this module keeps the browser itself. The names below are re-exported so
# ``from rxved.app import MultiScreen`` keeps working.
from rxved.screens import (  # noqa: E402
    VIEW_ALL,
    VIEW_ALL_FAVOURITES,
    VIEW_BANK_FAVOURITES,
    VIEW_CYCLE,
    VIEW_LABEL,
    CategoryScreen,
    ConfirmScreen,
    EDITABLE_CHANNEL_COLUMNS,
    EDITABLE_PART_COLUMNS,
    KEY_HINTS,
    KeyHints,
    MultiScreen,
    PerformanceScreen,
    ReportScreen,
    SearchScreen,
    StoreScreen,
    TextPromptScreen,
    _BIAS,
    _CHANNEL_FIELDS,
    _KIND_LABEL,
    wrap_blocks,
)


# --- the application --------------------------------------------------------


@dataclass(frozen=True)
class _Change:
    """One recorded change to the favourites store, enough to undo it.

    Favourites are the only thing rxved writes today, so a change is a slot
    plus the value before and after: ``old``/``new`` are the active flag for
    a toggle, the tag or note text for those. The shape is data rather than a
    closure so that the same log and the same ``z``/``Z`` keys can carry a
    preset write when editing arrives.
    """

    kind: str  # "favourite" | "tags" | "note" | "rating"
    bank_id: str
    number: int
    old: object
    new: object
    #: The displayed name, for re-favouriting an un-favourited slot: the
    #: store's ``toggle`` uses the stored name first, this as the fallback.
    name: str = ""

    @property
    def key(self) -> str:
        return f"{self.bank_id}:{self.number:03d}"

    @property
    def label(self) -> str:
        return {
            "favourite": "favourite",
            "tags": "tags",
            "note": "note",
            "rating": "rating",
        }.get(self.kind, self.kind)


class RxvedApp(App):
    """The browser."""

    CSS = """
    Screen { layers: base; }
    #panes { height: 1fr; }
    /* Sized to its content like the sibling projects', not to a number.
       These bank labels are short enough for a fixed width today, but a
       fixed one does not truncate the end of a label -- the table scrolls
       sideways and clips the *start*, which reads as corruption rather
       than as narrow. p2ked hit exactly that. */
    #banks {
        width: auto; min-width: 24; max-width: 52;
        border-right: solid $panel;
    }
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
        Binding("v", "cycle_favorites", "Favourites view"),
        Binding("t", "edit_tags", "Tags"),
        Binding("n", "edit_note", "Note"),
        Binding("z", "undo", "Undo"),
        Binding("Z", "undo_all", "Undo all"),
        Binding("slash", "search", "Search"),
        Binding("r", "read_names", "Read names"),
        Binding("s", "scan_bank", "Scan bank"),
        Binding("x", "probe_srx", "Probe SRX"),
        Binding("m", "multi_setup", "Multi-mode setup"),
        Binding("i", "device_info", "Device"),
        Binding("question_mark", "help", "Help"),
        Binding("tab", "switch_pane", "Switch pane"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        bridge,
        *,
        favorites,
        catalog=None,
        channel: int = 0,
        backup_dir: Optional[str] = None,
        config_path: Optional[str] = None,
        live_names_path: Optional[str] = None,
        read_banks_at_startup: bool = True,
    ) -> None:
        super().__init__()
        self.bridge = bridge
        #: Where the send channel is remembered between runs. Injected, and
        #: None in tests, so a test cannot write a config into the working
        #: directory -- the same reason backup_dir is injected.
        self._config_path = config_path
        #: Where names read off the synth are kept between runs. Injected for
        #: the same reason: a test that exercises a bank read must not write
        #: into the real data directory.
        self._live_names_path = live_names_path
        #: Whether to re-read the writable banks when the window opens.
        #: Injectable for the same reason `backup_dir` is: a test that mounts
        #: the app would otherwise start a background read that overwrites
        #: the catalog it was handed and writes the result into the real
        #: data directory.
        self._read_banks_at_startup = read_banks_at_startup
        self.favorites = favorites
        #: Where performance backups are written. Injected rather than
        #: looked up inside the worker so that tests -- which exercise the
        #: destructive store -- cannot write into the real data directory.
        #: They did, once, and left files in it.
        self._backup_dir = backup_dir
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
        #: Set by :func:`_close_when_idle` before it starts waiting, so a
        #: long background read can give up instead of holding shutdown for
        #: its whole length. The bridge lock already makes closing safe; this
        #: only makes it quick.
        self._closing = False
        self.last_status = ""
        self.last_status_refused = False
        #: Undo log, oldest first, in-memory only. Favourites are the only
        #: thing written today, so an entry is a slot and the value before
        #: and after; `z`/`Z` replay it backwards.
        self._changes: List[_Change] = []
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
        #: Whether the channel came from config (not default). If True, we
        #: don't overwrite it with the device's Patch Receive Channel.
        self._channel_from_config = channel != 0
        self.channel_is_from_device = False
        #: The currently open MultiScreen, if any. Used by the refresh callback.
        self._multi_screen: Optional[MultiScreen] = None
        #: Saved search options (names, tags, notes). Defaults to all on.
        self._search_options: Dict[str, bool] = {
            "names": True,
            "tags": True,
            "notes": True,
        }

        # Load persisted UI state (bank position, slot cursor, view mode, categories)
        if self._config_path is not None:
            ui_state = load_ui_state(self._config_path)
            if "bank_index" in ui_state:
                idx = ui_state["bank_index"]
                if 0 <= idx < len(self._bank_ids):
                    self._current_bank = self._bank_ids[idx]
                    self._saved_bank_index = idx
            if "slot_cursor" in ui_state:
                self._saved_slot_cursor = ui_state["slot_cursor"]
            else:
                self._saved_slot_cursor = None
            if "view_mode" in ui_state:
                vm = ui_state["view_mode"]
                if vm in VIEW_CYCLE:
                    self.view_mode = vm
            if "categories" in ui_state:
                self.categories = set(ui_state["categories"])
        else:
            self._saved_slot_cursor = None

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
        for label, key in (
            ("bank", "bank"),
            ("kind", "kind"),
            ("n", "n"),
            ("fav", "fav"),
        ):
            bank_table.add_column(label, key=key)
        self._fill_banks()

        # Restore bank table cursor position from saved state
        saved_bank_index = getattr(self, "_saved_bank_index", 0)
        if 0 <= saved_bank_index < len(self._bank_ids):
            bank_table.move_cursor(row=saved_bank_index)

        slot_table = self.query_one("#slot-table", DataTable)
        # PC, LSB, MSB -- least significant first. Not the order the
        # manual's tables print, but the order a sequencer's MIDI track
        # asks for the three, which is where these numbers get typed.
        # The favourite marker leads the row rather than trailing it, and
        # is a heart rather than an asterisk. Leading, because it is a
        # gutter mark the eye runs down; a heart, because an asterisk
        # already means "this name disagrees with the catalog" two columns
        # over, and one glyph should not mean two things in one table.
        for label, key in (
            ("♥", "fav"),
            ("#", "num"),
            ("name", "name"),
            ("PC", "pc"),
            ("LSB", "lsb"),
            ("MSB", "msb"),
            ("cat", "cat"),
        ):
            slot_table.add_column(label, key=key)
        # Use saved slot cursor on first fill
        saved_cursor = getattr(self, "_saved_slot_cursor", 0)
        self._fill_slots(self._current_bank, cursor=saved_cursor or 0)
        self._saved_slot_cursor = None  # Only use once

        bank_table.focus()

        # Re-apply slot cursor after bank table gets focus -- focus changes
        # can reset the visual cursor, so we re-apply it once the dust settles.
        if saved_cursor:
            # Use a timer with a small delay to ensure focus changes have settled
            self.set_timer(0.1, lambda: self._restore_slot_cursor(saved_cursor))
        self._read_channels_worker()
        self._read_user_bank_at_startup()
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
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(
                self.notify_status,
                f"could not read the synth's state ({exc}); sending on "
                f"channel {self.target_channel + 1}, which may hit nothing",
                refused=True,
            )
            return
        self.call_from_thread(self._adopt_state, state, True)

    #: The writable banks, and the only ones worth re-reading at startup.
    #:
    #: USER is here because it is the bank that is *about* to be wrong. The
    #: printed list says what the factory shipped; anything the user has
    #: saved into says something else, and nothing else in the program can
    #: tell the difference -- only the instrument can. The other two are
    #: cheap (about a second each) and just as authoritatively the user's
    #: own, so there is no reason to leave them stale.
    STARTUP_BANKS = ("USER", "R-USER", "P-USER")

    def _read_user_bank_at_startup(self) -> None:
        """Re-read the writable banks in the background, once, quietly.

        Runs off the main thread and never asks, because it selects nothing:
        `read_user_bank` is RQ1/DT1 against addresses in the parameter map,
        so the instrument is not touched and keeps playing whatever it was
        playing. About three seconds for all three banks, against a bridge
        the user may be about to press a key on -- hence the background, and
        hence sharing `_busy` with everything else so the two cannot overlap.

        Whatever was read last time is already on screen by the time this
        runs, so this only ever *corrects* the display. If it fails, the
        stored names stand and there is nothing to undo.
        """
        if not self._read_banks_at_startup or not hasattr(
            self.bridge, "read_user_bank"
        ):
            return
        self._busy = True
        self._startup_read_worker()

    @work(thread=True)
    def _startup_read_worker(self) -> None:
        for bank_id in self.STARTUP_BANKS:
            if self._closing:
                self.call_from_thread(self._end_busy)
                return

            def progress(done: int, total: int, _bank=bank_id) -> None:
                self.call_from_thread(
                    self.notify_status, f"reading {_bank} names: {done}/{total}"
                )

            try:
                with self._bridge_lock:
                    names = self.bridge.read_user_bank(bank_id, on_progress=progress)
            except _BRIDGE_ERRORS as exc:
                self.call_from_thread(
                    self.notify_status,
                    f"could not read {bank_id} names ({exc}); keeping the "
                    f"ones read last time",
                    refused=True,
                )
                continue
            self.call_from_thread(self._apply_names_quiet, bank_id, names)
        self.call_from_thread(self._end_busy)

    def _save_live_names(self, bank_id: str, names: Dict[int, str]) -> None:
        """Persist one bank's hardware-read names, if we have somewhere to.

        `live_names_path` is resolved by `main()` and injected, rather than
        defaulted here, for the reason `backup_dir` is: a path this class
        looks up for itself is a path a test can write to by accident. Two
        tests in tests/test_legend.py build an app without one, and did --
        writing DemoBridge's invented patch names into the real data
        directory. None meaning "do not persist" makes that impossible.
        """
        if not self._live_names_path:
            return
        livenames.save(
            {livenames.key(bank_id, n): name for n, name in names.items()},
            self._live_names_path,
        )

    def _end_busy(self) -> None:
        self._busy = False
        self._refresh_current_bank()

    def _apply_names_quiet(self, bank_id: str, names: Dict[int, str]) -> None:
        """`_apply_names` without the status line.

        The startup read is not something the user asked for and three
        banks' worth of "read 128 names from USER" would bury the message
        they are actually waiting for. It reports nothing on success; a
        failure still speaks, because silently showing yesterday's names
        for a bank that has since changed is the bug this whole thing
        exists to fix.
        """
        for number, name in names.items():
            self.catalog.set_live_name(bank_id, number, name)
        self._save_live_names(bank_id, names)
        self.favorites.refresh_names(lambda b, n: self.catalog.live_name(b, n))

    def _adopt_state(self, state, move_cursor: bool = False) -> None:
        """Take a freshly read DeviceState and redraw what depends on it."""
        if (
            move_cursor
            and not self.channel_is_from_device
            and not self._channel_from_config
        ):
            # Start where the synth is actually listening rather than on
            # channel 1 by assumption. But don't override a channel loaded
            # from config (non-default).
            self.target_channel = state.channels.patch_receive
            self.channel_is_from_device = True
        self._update_detail(self.query_one("#slot-table", DataTable).cursor_row)
        self.notify_status(f"read back from the synth — {state.setup.mode_name} mode")

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
                    rows.append(banks.slot(favourite.bank_id, favourite.number))
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
            s
            for s in rows
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
        # When nothing in this bank agrees with the printed list any more --
        # the normal state of a USER bank somebody works in -- marking every
        # row tells the eye nothing. The printed name moves to the detail
        # line for the slot you are actually on.
        self._printed_void = self.catalog.printed_list_void(
            bank_id, (s.number for s in self._current_slots)
        )
        self._filling = True
        try:
            table.clear()
            for slot in self._current_slots:
                name = self.catalog.display_name(slot.bank_id, slot.number)
                if self.catalog.differs(slot.bank_id, slot.number) and not (
                    self._printed_void and not across_banks
                ):
                    # The machine and the book disagree, and *rarely*, which
                    # is what makes it worth a glance: somebody saved over
                    # this slot.
                    shown = f"[b]{name}[/b] [dim]*[/dim]"
                elif self.catalog.is_live(slot.bank_id, slot.number):
                    shown = f"[b]{name}[/b]"
                else:
                    shown = name
                catalog_entry = self.catalog.entry(slot.bank_id, slot.number)
                table.add_row(
                    "[b]♥[/b]" if slot.key in favorited else "",
                    (
                        f"{slot.bank_id} {slot.number:03d}"
                        if across_banks
                        else f"{slot.number:03d}"
                    ),
                    shown,
                    str(slot.program_change),
                    str(slot.lsb),
                    str(slot.msb),
                    (catalog_entry.category or "") if catalog_entry else "",
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

    def _restore_slot_cursor(self, cursor: int) -> None:
        """Re-apply slot cursor after focus changes have settled."""
        try:
            table = self.query_one("#slot-table", DataTable)
            if 0 <= cursor < len(self._current_slots):
                table.move_cursor(row=cursor)
        except (LookupError, ValueError, AttributeError):
            pass

    def _update_subtitle(self) -> None:
        entry = banks.bank(self._current_bank)
        shown = len(self._current_slots)
        if self.view_mode == VIEW_ALL:
            plural = {
                "patch": "patches",
                "rhythm": "rhythm sets",
                "performance": "performances",
            }[entry.kind]
            what = f"{entry.label} ({self._current_bank}) — {shown} {plural}"
        elif self.view_mode == VIEW_BANK_FAVOURITES:
            what = (
                f"{entry.label} ({self._current_bank}) — {shown} "
                f"favourite{'' if shown == 1 else 's'}"
            )
        else:
            what = (
                f"all favourites — {shown} "
                f"across {len({s.bank_id for s in self._current_slots})} "
                f"bank(s)"
            )
        if self.categories:
            what += "   ·   " + "/".join(sorted(self.categories))
        line = f"{what}   ·   ch {self.target_channel + 1}"
        if self._changes:
            line += f"   ·   Δ{len(self._changes)}"
        self.sub_title = line

    def _update_detail(self, row: int) -> None:
        if not 0 <= row < len(self._current_slots):
            if self.view_mode != VIEW_ALL:
                where = (
                    "this bank"
                    if self.view_mode == VIEW_BANK_FAVOURITES
                    else "any bank"
                )
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
        header = f"[b]{slot.bank.label} {slot.number:03d}[/b]  {name}"
        printed = self.catalog.name(slot.bank_id, slot.number)
        if self.catalog.differs(slot.bank_id, slot.number) and printed:
            # The factory name, for the slot you are on rather than on all
            # 128 rows at once. On a bank nobody has saved into, the
            # printed list still describes the machine and this is the only
            # place it is shown.
            header += f"   [dim]factory: {printed}[/dim]"
        lines = [
            header,
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
                f"[dim](synth state not read)[/dim]"
            )
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
                    self._save_ui_state(bank_index=row)
        elif event.data_table.id == "slot-table":
            self._update_detail(event.cursor_row)
            # Only save slot cursor when the slot table is focused -- i.e. the
            # user is actively navigating. This prevents programmatic cursor
            # moves during startup (when bank table gets focus) from
            # overwriting the saved position.
            if self._focused_table().id == "slot-table":
                self._save_ui_state(slot_cursor=event.cursor_row)

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
                slot.bank_id, "fav", str(marked) if marked else ""
            )
        except (KeyError, ValueError, LookupError, AttributeError):
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
            slot_table.update_cell(slot.key, "fav", "[b]♥[/b]" if now else "")
        except (KeyError, ValueError, LookupError, AttributeError):
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
                        "to OFF, so performances cannot be selected over MIDI"
                    )
                self.bridge.select(slot, channel=used)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(self.notify_status, f"select: {exc}", refused=True)
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
        label = "" if name == cat.UNNAMED else name
        was = self.favorites.get(slot.bank_id, slot.number) is not None
        now = self.favorites.toggle(slot.bank_id, slot.number, name=label)
        self._record_change(
            _Change(
                "favourite", slot.bank_id, slot.number, old=was, new=now, name=label
            )
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
                f"{slot} is not a favourite yet -- press f first", refused=True
            )
            return

        def apply(value: Optional[str]) -> None:
            if value is None:
                return
            old = existing.tags
            self.favorites.set_tags(slot.bank_id, slot.number, value)
            self._record_change(
                _Change("tags", slot.bank_id, slot.number, old=old, new=value)
            )
            self._update_detail(self.query_one("#slot-table", DataTable).cursor_row)
            self.notify_status(f"{slot} tags set")

        self.push_screen(
            TextPromptScreen(f"Tags for {slot} (comma separated)", existing.tags),
            apply,
        )

    def action_edit_note(self) -> None:
        slot = self._current_slot()
        if slot is None:
            return
        existing = self.favorites.get(slot.bank_id, slot.number)
        if existing is None:
            self.notify_status(
                f"{slot} is not a favourite yet -- press f first", refused=True
            )
            return

        def apply(value: Optional[str]) -> None:
            if value is None:
                return
            old = existing.note
            self.favorites.set_note(slot.bank_id, slot.number, value)
            self._record_change(
                _Change("note", slot.bank_id, slot.number, old=old, new=value)
            )
            self._update_detail(self.query_one("#slot-table", DataTable).cursor_row)
            self.notify_status(f"{slot} note set")

        self.push_screen(TextPromptScreen(f"Note for {slot}", existing.note), apply)

    # --- undo ---------------------------------------------------------------

    def _record_change(self, change: _Change) -> None:
        """Append one change to the undo log and show the pending count."""
        self._changes.append(change)
        self._update_subtitle()

    def action_undo(self) -> None:
        self._undo(1)

    def action_undo_all(self) -> None:
        self._undo(len(self._changes))

    def _undo(self, count: int) -> None:
        """Reverse the last ``count`` changes, newest first.

        In-memory and unbounded, like the sibling editors: there is nothing
        here worth surviving a restart, and a cap would only ever be hit by
        the person who wants it least.
        """
        if not self._changes:
            self.notify_status("nothing to undo")
            return
        reverted: List[_Change] = []
        for _ in range(min(count, len(self._changes))):
            change = self._changes[-1]
            try:
                self._apply_undo(change)
            except Exception as exc:
                # Popped only after the write succeeded, so a failure part-way
                # leaves the log describing what is still applied -- the same
                # rule the sibling editors' undo follows.
                self.notify_status(f"undo failed: {exc}", refused=True)
                break
            self._changes.pop()
            reverted.append(change)
        if not reverted:
            return
        self._refresh_after_undo(reverted)
        self._update_subtitle()
        if len(reverted) == 1:
            change = reverted[0]
            left = f" — Δ{len(self._changes)} left" if self._changes else ""
            self.notify_status(f"undid {change.label} on {change.key}{left}")
        else:
            self.notify_status(f"undid {len(reverted)} change(s)")

    def _apply_undo(self, change: _Change) -> None:
        """Reverse one recorded change in the favourites store."""
        if change.kind == "favourite":
            # toggle is its own inverse: it flips the active flag and restores
            # whatever annotations the row was carrying.
            self.favorites.toggle(change.bank_id, change.number, name=change.name)
        elif change.kind == "tags":
            self.favorites.set_tags(change.bank_id, change.number, str(change.old))
        elif change.kind == "note":
            self.favorites.set_note(change.bank_id, change.number, str(change.old))
        elif change.kind == "rating":
            self.favorites.set_rating(change.bank_id, change.number, int(change.old))
        else:
            raise ValueError(f"unknown change kind {change.kind!r}")

    def _refresh_after_undo(self, reverted: List[_Change]) -> None:
        """Repaint what an undo touched: the slot table, bank counts, detail."""
        bank_table = self.query_one("#bank-table", DataTable)
        for bank_id in {c.bank_id for c in reverted if c.kind == "favourite"}:
            marked = len(self.favorites.keys_for_bank(bank_id))
            try:
                bank_table.update_cell(bank_id, "fav", str(marked) if marked else "")
            except (KeyError, ValueError, LookupError, AttributeError):
                pass
        self._refresh_current_bank()
        self._update_detail(self.query_one("#slot-table", DataTable).cursor_row)

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
        self._save_ui_state(view_mode=self.view_mode)
        self.query_one("#slot-table", DataTable).focus()
        shown = len(self._current_slots)
        if self.view_mode == VIEW_ALL:
            self.notify_status("showing all slots")
        elif not shown:
            where = (
                "this bank" if self.view_mode == VIEW_BANK_FAVOURITES else "any bank"
            )
            self.notify_status(
                f"no favourites in {where} yet — press f on a slot to add "
                f"one, or F again for the next view"
            )
        else:
            self.notify_status(
                f"showing {VIEW_LABEL[self.view_mode]} — {shown} row(s); "
                f"enter still selects, f un-favourites"
            )

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
        counts = self.catalog.categories_in([(s.bank_id, s.number) for s in rows])
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
                    f"{'/'.join(sorted(self.categories))} — {shown} of {len(rows)} rows"
                )
            self._save_ui_state(categories=list(self.categories))

        self.push_screen(CategoryScreen(counts, self.categories), apply)

    def action_search(self) -> None:
        """Prompt for search term with checkboxes for what to search in."""
        opts = self._search_options

        def run(result: Optional[Tuple[str, Dict[str, bool]]]) -> None:
            if result is None:
                return
            needle, options = result
            # Remember the options for next time
            self._search_options = options
            self._perform_search(needle, options)

        self.push_screen(
            SearchScreen(
                search_names=opts.get("names", True),
                search_tags=opts.get("tags", True),
                search_notes=opts.get("notes", True),
            ),
            run,
        )

    def _perform_search(self, needle: str, options: Dict[str, bool]) -> None:
        """Run the search with the given options and show results."""
        hits: List[Tuple[str, int, str]] = []
        search_names = options.get("names", False)
        search_tags = options.get("tags", False)
        search_notes = options.get("notes", False)

        # Search catalog names if requested
        if search_names:
            hits.extend(self.catalog.search(needle))

        # Search favorites if requested
        if search_tags or search_notes:
            fav_hits = self.favorites.search(needle)
            # Filter by what the user selected
            for fav in fav_hits:
                match = False
                if search_tags and fav.tags and needle.lower() in fav.tags.lower():
                    match = True
                if search_notes and fav.note and needle.lower() in fav.note.lower():
                    match = True
                if match:
                    hits.append((fav.bank_id, fav.number, fav.name))

        # Deduplicate by (bank_id, number)
        seen = set()
        unique_hits = []
        for bank_id, number, name in hits:
            key = (bank_id, number)
            if key not in seen:
                seen.add(key)
                unique_hits.append((bank_id, number, name))

        if not unique_hits:
            self.notify_status(f"nothing matching {needle!r}")
            return

        unique_hits.sort(key=lambda row: (row[0], row[1]))
        lines = [f"{len(unique_hits)} match(es) for {needle!r}", ""]
        for bank_id, number, name in unique_hits[:400]:
            try:
                slot = banks.slot(bank_id, number)
                wire = (
                    f"PC {slot.program_change:>3}  LSB {slot.lsb:>3}  MSB {slot.msb:>3}"
                )
            except LookupError:
                wire = ""
            lines.append(f"{bank_id:<10} {number:03d}  {name:<14} {wire}")
        if len(unique_hits) > 400:
            lines.append(f"... and {len(unique_hits) - 400} more")
        self.push_screen(ReportScreen(f"Search: {needle}", "\n".join(lines)))

    def action_read_names(self) -> None:
        """Read the highlighted bank's names off the synth, if it has an address.

        Genuinely read-only -- nothing is selected and the instrument keeps
        playing whatever it was playing -- so unlike scan it needs no
        confirmation.

        Named ``read_names`` because that is what the family calls this key:
        ``r`` means *re-read from the device*, and it was bound to
        ``read_bank`` here, which said the same thing in a way no other tool
        in the family used.
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
        # Set on the main thread, not in the worker. A
        # @work(thread=True) method returns at once and would set the
        # flag on its own thread, so two quick presses both passed the
        # check above before either worker ran -- two of whatever the
        # worker does, from one intent.
        self._busy = True
        self._read_bank_worker(bank_id)

    @work(thread=True)
    def _read_bank_worker(self, bank_id: str) -> None:
        try:

            def progress(done: int, total: int) -> None:
                self.call_from_thread(
                    self.notify_status, f"reading {bank_id}: {done}/{total}"
                )

            with self._bridge_lock:
                names = self.bridge.read_user_bank(bank_id, on_progress=progress)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(
                self.notify_status, f"read {bank_id}: {exc}", refused=True
            )
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
        # Checked before the confirmation, not after: in Performance mode the
        # scan cannot work at all, and asking "play 128 notes?" first and
        # then refusing is worse than saying so up front. Observed on real
        # hardware -- the synth answered every read with the patch it was
        # already on, and every slot came back with that one name.
        state = getattr(self.bridge, "state", None)
        if state is not None and not state.setup.is_patch_mode:
            self.notify_status(
                f"the synth is in [b]{state.setup.mode_name}[/b] mode, where a "
                f"program change on the patch receive channel does not change "
                f"the patch that gets read back -- so scanning would report "
                f"the current patch's name for every one of {entry.count} "
                f"slots. Switch to PATCH mode (SYSTEM/MIDI) and press s "
                f"again. Nothing has been sent.",
                refused=True,
            )
            return
        if bank_id in ("USER", "P-USER", "R-USER"):
            # Scanning these is not just slower than reading them, it is
            # wrong: it plays 128 program changes to learn what `r` reads
            # silently and exactly, and on a synth that is not following the
            # program change it reports the patch the synth is sitting on,
            # once per slot. Verified on hardware -- 128 slots all reading
            # the current patch's name.
            self.notify_status(
                f"{bank_id} has addresses in the parameter map, so r reads it "
                f"directly: silent, exact, and about 3 seconds. s is only "
                f"for ROM and expansion banks, which have no address.",
                refused=True,
            )
            return

        def go(confirmed: bool) -> None:
            if confirmed:
                if self._busy:
                    self.notify_status("already talking to the synth", refused=True)
                    return
                # Set on the main thread, not in the worker. A
                # @work(thread=True) method returns at once and would set the
                # flag on its own thread, so two quick presses both passed the
                # check above before either worker ran -- two of whatever the
                # worker does, from one intent.
                self._busy = True
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
        try:

            def progress(done: int, total: int, name: str) -> None:
                self.call_from_thread(
                    self.notify_status, f"scanning {bank_id}: {done}/{total}  {name}"
                )

            with self._bridge_lock:
                names = self.bridge.scan_bank(bank_id, on_progress=progress)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(
                self.notify_status, f"scan {bank_id}: {exc}", refused=True
            )
            return
        finally:
            self._busy = False
        self.call_from_thread(self._apply_names, bank_id, names)

    def _apply_names(self, bank_id: str, names: Dict[int, str]) -> None:
        for number, name in names.items():
            self.catalog.set_live_name(bank_id, number, name)
        # Persisted here rather than at exit: a crash, a power cut or a
        # `q` during the read should not cost the read, and these names are
        # the only authority there is for a bank somebody has saved into.
        self._save_live_names(bank_id, names)
        changed = self.favorites.refresh_names(
            lambda b, n: self.catalog.live_name(b, n)
        )
        self._refresh_current_bank()
        differing = sum(1 for number in names if self.catalog.differs(bank_id, number))
        message = f"read {len(names)} name(s) from {bank_id}"
        void = self.catalog.printed_list_void(bank_id, names)
        if void:
            message += (
                "; this bank has been saved into, so the printed list no "
                "longer describes it (factory name of the highlighted slot "
                "is shown below)"
            )
        elif differing:
            message += f"; {differing} differ from the printed list (marked *)"
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
                if self._busy:
                    self.notify_status("already talking to the synth", refused=True)
                    return
                # Set on the main thread, not in the worker. A
                # @work(thread=True) method returns at once and would set the
                # flag on its own thread, so two quick presses both passed the
                # check above before either worker ran -- two of whatever the
                # worker does, from one intent.
                self._busy = True
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
        try:

            def progress(lsb: int, name: Optional[str]) -> None:
                self.call_from_thread(
                    self.notify_status,
                    f"probing SRX LSB {lsb}" + (f": {name}" if name else ""),
                )

            with self._bridge_lock:
                found = self.bridge.probe_srx(on_progress=progress)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(self.notify_status, f"probe: {exc}", refused=True)
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
            known = [card.id for card in banks.SRX_CARDS if lsb in card.patch_lsbs]
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

    def _remember_channel(self, channel: int) -> None:
        """Save the send channel for next time.

        Only **OSError** is swallowed -- a read-only directory or a full
        disk, where forgetting the channel is better than refusing to run.
        A broader ``except`` is what hid the first version of this method
        failing outright: the bridge module was imported inside ``main``
        only, so every call raised NameError into a bare handler and the
        file was silently never written.
        """
        if self._config_path is None:
            return
        from xv import bridge as bridge_module

        try:
            bridge_module.save_channel(channel, self._config_path)
        except OSError:
            pass

    def _save_ui_state(
        self,
        *,
        bank_index: Optional[int] = None,
        slot_cursor: Optional[int] = None,
        view_mode: Optional[str] = None,
        categories: Optional[List[str]] = None,
    ) -> None:
        """Persist UI state (bank, slot cursor, view mode, categories)."""
        if self._config_path is None:
            return
        try:
            save_ui_state(
                self._config_path,
                bank_index=bank_index,
                slot_cursor=slot_cursor,
                view_mode=view_mode,
                categories=categories,
            )
        except OSError:
            pass

    def action_pick_channel(self) -> None:
        def apply(value):
            if value is None:
                return
            try:
                number = int(value.strip())
            except ValueError:
                self.notify_status(f"{value!r} is not a channel number", refused=True)
                return
            if not 1 <= number <= 16:
                self.notify_status("MIDI channels are 1-16", refused=True)
                return
            self._set_channel(number - 1)

        self.push_screen(
            TextPromptScreen(
                "Send on MIDI channel (1-16)", str(self.target_channel + 1)
            ),
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
        self._channel_from_config = True
        self._remember_channel(channel)
        self._update_subtitle()
        self._update_detail(self.query_one("#slot-table", DataTable).cursor_row)
        if self._busy:
            self.notify_status(f"send channel {channel + 1} (synth busy; not re-read)")
            return
        self._refresh_channel_worker(channel)

    @work(thread=True)
    def _refresh_channel_worker(self, channel: int) -> None:
        try:
            with self._bridge_lock:
                state = self.bridge.refresh_channel(channel)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(
                self.notify_status,
                f"channel {channel + 1}: could not read back ({exc})",
                refused=True,
            )
            return
        self.call_from_thread(self._adopt_state, state)

    def action_refresh_state(self) -> None:
        """Full re-read, including every part's receive channel."""
        if self._busy:
            self.notify_status("already talking to the synth", refused=True)
            return
        # Set on the main thread, not in the worker. A
        # @work(thread=True) method returns at once and would set the
        # flag on its own thread, so two quick presses both passed the
        # check above before either worker ran -- two of whatever the
        # worker does, from one intent.
        self._busy = True
        self._refresh_state_worker()

    @work(thread=True)
    def _refresh_state_worker(self) -> None:
        try:

            def progress(done, total):
                self.call_from_thread(
                    self.notify_status, f"reading part {done}/{total}"
                )

            with self._bridge_lock:
                state = self.bridge.read_state(with_parts=True, on_progress=progress)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(self.notify_status, f"refresh: {exc}", refused=True)
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
        # Set on the main thread, not in the worker. A
        # @work(thread=True) method returns at once and would set the
        # flag on its own thread, so two quick presses both passed the
        # check above before either worker ran -- two of whatever the
        # worker does, from one intent.
        self._busy = True
        self._multi_setup_worker()

    @work(thread=True)
    def _multi_setup_worker(self) -> None:
        """**MIDI only** -- the screen is built on the main thread."""
        try:

            def progress(done, total):
                self.call_from_thread(
                    self.notify_status, f"reading part {done}/{total}"
                )

            with self._bridge_lock:
                state = self.bridge.read_state(with_parts=True, on_progress=progress)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(
                self.notify_status, f"multi setup: {exc}", refused=True
            )
            return
        finally:
            self._busy = False
        self.call_from_thread(self._show_multi_setup, state)

    def _show_multi_setup(self, state) -> None:
        self._adopt_state(state)
        if not state.parts:
            # Single-timbral: there is nothing to tabulate, and a table of
            # sixteen unused parts would imply there is.
            self.push_screen(
                ReportScreen(
                    "Multi-mode setup",
                    "\n".join(f"· {line}" for line in state.silence_report()),
                )
            )
            return
        # push_screen returns an AwaitMount; the screen is mounted synchronously,
        # so we can get the actual screen instance via the screen stack.
        awaitable = self.push_screen(
            MultiScreen(
                state,
                self.catalog,
                on_write=self._write_part_param,
                on_write_channel=self._write_channel_param,
                on_refresh=self._refresh_multi_setup,
                on_load=self._load_performance,
                on_close=lambda: setattr(self, "_multi_screen", None),
            )
        )
        # The screen is now the active screen; grab it from the screen stack.
        self._multi_screen = self.screen

    def _refresh_multi_setup(self) -> None:
        """Re-read the full multi-mode state from the synth."""
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        if self._multi_screen is None:
            self.notify_status("multi-mode screen not open", refused=True)
            return
        self._busy = True
        self._refresh_multi_worker()

    @work(thread=True)
    def _refresh_multi_worker(self) -> None:
        """**MIDI only** -- re-read the full state for the multi-mode screen."""
        try:

            def progress(done, total):
                self.call_from_thread(
                    self.notify_status, f"reading part {done}/{total}"
                )

            with self._bridge_lock:
                state = self.bridge.read_state(with_parts=True, on_progress=progress)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(
                self.notify_status, f"multi refresh: {exc}", refused=True
            )
            return
        finally:
            self._busy = False
        self.call_from_thread(self._on_multi_refresh_complete, state)

    def _on_multi_refresh_complete(self, state) -> None:
        """Update the multi-mode screen with fresh state."""
        self._adopt_state(state)
        # Prefer the tracked screen reference, but fall back to the current
        # screen if it's a MultiScreen (e.g., if the reference wasn't captured).
        screen = self._multi_screen
        if screen is None and isinstance(self.screen, MultiScreen):
            screen = self.screen
        if screen is not None:
            screen.update_state(state)

    def _load_performance(self, slot: banks.Slot) -> None:
        """Load a stored performance into the edit buffer. Main thread."""
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        # Set on the main thread, not in the worker (see _multi_setup_worker).
        self._busy = True
        self._load_performance_worker(slot)

    @work(thread=True)
    def _load_performance_worker(self, slot: banks.Slot) -> None:
        """**MIDI only** -- select the performance, then re-read the state.

        A performance is selected on the Performance Control Channel and
        nowhere else, so that one is not the user's to choose. The settle
        between the select and the read is the load time: reading too early
        returns the performance that was there before, silently.
        """
        try:
            with self._bridge_lock:
                used = self._channel_for(slot)
                if used is None:
                    raise RuntimeError(
                        "this synth has its Performance Control Channel set "
                        "to OFF, so performances cannot be selected over MIDI"
                    )
                self.bridge.select(slot, channel=used)
                time.sleep(PERFORMANCE_LOAD_SETTLE)
                state = self.bridge.read_state(with_parts=True)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(self.notify_status, f"load: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(self.notify_status, f"loaded {slot} into the edit buffer")
        self.call_from_thread(self._on_multi_refresh_complete, state)

    def _write_part_param(self, part: int, offset: int, value: int, adopt) -> None:
        """Hand one part-parameter write to a worker. Main thread."""
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        # Set on the main thread, not in the worker. A
        # @work(thread=True) method returns at once and would set the
        # flag on its own thread, so two quick presses both passed the
        # check above before either worker ran -- two of whatever the
        # worker does, from one intent.
        self._busy = True
        self._write_part_worker(part, offset, value, adopt)

    def backup_dir(self) -> str:
        """Where backups go. The per-platform data directory unless a
        caller said otherwise."""
        if self._backup_dir is not None:
            return self._backup_dir
        from xv import backup as bk
        from rxved.favorites import data_dir

        return bk.default_dir(data_dir())

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
        self.push_screen(StoreScreen(source_name, names, on_store=self._store_to_slot))

    def _store_to_slot(self, slot: int) -> None:
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        # Set on the main thread, not in the worker. A
        # @work(thread=True) method returns at once and would set the
        # flag on its own thread, so two quick presses both passed the
        # check above before either worker ran -- two of whatever the
        # worker does, from one intent.
        self._busy = True
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

        try:

            def progress(done, total):
                self.call_from_thread(
                    self.notify_status, f"writing block {done}/{total}"
                )

            with self._bridge_lock:
                previous, mismatched = self.bridge.store_temporary_to_slot(
                    slot, on_progress=progress
                )
            path = bk.save(
                previous,
                self.backup_dir(),
                slot=slot,
                device_id=self.bridge.device_id,
                source=f"{self.bridge.description} (before store)",
            )
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(self.notify_status, f"store: {exc}", refused=True)
            return
        finally:
            self._busy = False

        if mismatched:
            self.call_from_thread(
                self.notify_status,
                f"slot {slot}: {len(mismatched)} block(s) did NOT read back "
                f"as written ({', '.join(mismatched[:3])}...). Previous "
                f"contents are in {path}",
                refused=True,
            )
        else:
            self.call_from_thread(
                self.notify_status,
                f"stored into user performance {slot}; what was there is in {path}",
            )

    def _write_channel_param(
        self, channel: int, offset: int, value: int, adopt
    ) -> None:
        """Hand one Performance MIDI write to a worker. Main thread."""
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        # Set on the main thread, not in the worker. A
        # @work(thread=True) method returns at once and would set the
        # flag on its own thread, so two quick presses both passed the
        # check above before either worker ran -- two of whatever the
        # worker does, from one intent.
        self._busy = True
        self._write_channel_worker(channel, offset, value, adopt)

    @work(thread=True)
    def _write_channel_worker(
        self, channel: int, offset: int, value: int, adopt
    ) -> None:
        """**MIDI only.** Write one byte, then re-read the channel's block."""
        try:
            with self._bridge_lock:
                landed = self.bridge.write_channel_param(channel, offset, value)
                fresh = self.bridge.read_performance_midi(channel)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(self.notify_status, f"write: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(adopt, fresh)
        if landed != value:
            self.call_from_thread(
                self.notify_status,
                f"ch {channel + 1}: wrote {value}, synth reports {landed}",
                refused=True,
            )
        else:
            self.call_from_thread(
                self.notify_status,
                f"ch {channel + 1}: set to {landed} in the temporary "
                f"performance (not stored)",
            )

    @work(thread=True)
    def _write_part_worker(self, part: int, offset: int, value: int, adopt) -> None:
        """**MIDI only.** Write one byte, then re-read the whole part.

        The whole part, not the byte written: the XV-2020 is free to adjust
        neighbouring parameters in response -- and whether it does is not
        something rxved knows -- so re-reading only what was sent could leave
        the rest of the row saying something that stopped being true.
        """
        try:
            with self._bridge_lock:
                landed = self.bridge.write_part_param(part, offset, value)
                fresh = self.bridge.read_part(part)
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(self.notify_status, f"write: {exc}", refused=True)
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
                refused=True,
            )
        else:
            self.call_from_thread(
                self.notify_status,
                f"part {part}: set to {landed} in the temporary performance "
                f"(not stored)",
            )

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
        except _BRIDGE_ERRORS as exc:
            self.call_from_thread(self.notify_status, f"identify: {exc}", refused=True)
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
            f"family            {identity.family[0]:#04x} {identity.family[1]:#04x}",
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
                + (
                    str(state.channels.performance_display)
                    if state.channels.performance_display is not None
                    else "OFF"
                ),
                f"rxved sends on    ch {self.target_channel + 1}"
                f"  ({state.describes_short(self.target_channel)})",
            ]
            if state.parts:
                rows.append("")
                rows.append("part  ch   slot")
                for part in state.parts:
                    rows.append(
                        f"{part.part:>4}  {part.channel_display:>2}   "
                        f"{part.slot if part.slot else '?'}"
                    )
        else:
            rows.append(
                f"rxved sends on    ch {self.target_channel + 1} (synth state not read)"
            )
        rows += [
            "",
            f"catalog           {len(self.catalog)} names"
            + (f" from {self.catalog.source}" if self.catalog.source else ""),
            f"favourites        {len(self.favorites)} in {self.favorites.path}",
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
  v              cycle the right pane: all slots → this bank's favourites →
                 every favourite. The filtered views are the real table, so
                 enter still selects and f still un-favourites.
  t / n          tags / note (favourites only)
  z / Z          undo / undo all (favourite toggles, tags, notes)
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
    parser = make_parser("rxved", "Terminal browser for the Roland XV-2020's sounds.")
    # --port and --scan are mutually exclusive here, and are added by hand
    # rather than by add_common_arguments for that reason: --scan says "do
    # not trust the remembered port", which is not a question --port can
    # also be answering, and a flag accepted and then ignored is worse than
    # no flag. The help text is still the family's, from vinsynlib.spec.
    #
    # This is the one place this tool's command line is not entirely
    # assembled by the shared helper. It is a real divergence and it is
    # deliberate: the group is this project's rule, not the family's.
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--port", default=None, help=flag_help("port"))
    group.add_argument(
        "--scan", action="store_true", default=False, help=flag_help("scan")
    )
    add_common_arguments(
        parser,
        port=False,
        scan=False,
        recv_port=True,
        channel=True,
        device_id=True,
        demo=True,
        timeout=True,
        catalog=True,
        config=True,
        favorites=True,
    )
    # --live-names and --backup-dir are rxved's own, and stay: they name
    # concepts this family has no opinion about -- where names read off a
    # synth are cached, and where performance backups go. A flag the family
    # has no contract for is not a flag the family should be given.
    parser.add_argument(
        "--live-names",
        default=None,
        help="where names read from the synth are kept between runs "
        "(default: beside the favourites database)",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="where performance backups are written "
        "(default: alongside the favourites database)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # The family's range checks, before anything is opened: a mistyped
    # channel costs a message rather than a wrong program change sent to a
    # live instrument, and a wrong channel is not an error anywhere -- the
    # synth simply plays nothing, or something else does.
    validate_common(args)
    channel = (args.channel - 1) if args.channel is not None else None

    # --device-id is a byte family-wide, because that is what a SysEx device
    # ID is in most of these protocols. This one is not: the XV-2020
    # *displays* it as 17-32, and :func:`xv.messages.device_id_byte` takes a
    # panel number and refuses a wire byte. So the shared check is followed
    # by this unit's own, which is the one that can say so.
    if args.device_id is not None and not 17 <= args.device_id <= 32:
        sys.exit("error: --device-id is 17-32, as the panel shows it")

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
                    args.port,
                    recv_port_name=args.recv_port,
                    device_id=device_id,
                    channel=channel,
                    **kwargs,
                )
            else:
                bridge = b.XvBridge.connect(
                    config_path=config_path,
                    channel=channel,
                    scan=args.scan,
                    on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
                    **kwargs,
                )
        except _BRIDGE_ERRORS as exc:
            sys.exit(f"error: {exc}")

    from rxved.favorites import Favorites

    catalog = cat.load(args.catalog)
    # Before the app is built, so the first frame already shows the names the
    # synth gave last time. Waiting for the hardware read to come back would
    # mean opening on the printed list and correcting it a moment later,
    # which is the flicker this exists to remove -- and for a USER bank the
    # printed list is not a rough version of the truth, it is a different
    # bank's worth of names.
    livenames.apply_to(catalog, args.live_names)
    try:
        favorites = Favorites(args.favorites)
    except (_BRIDGE_ERRORS, sqlite3.Error) as exc:
        # _migrate refuses a database written by a newer build, which is the
        # right refusal, and sqlite3 raises on a file that is not a database
        # at all. Every other startup failure here leaves by sys.exit; these
        # two used to print a traceback.
        raise SystemExit(f"error: {exc}")

    app = RxvedApp(
        bridge,
        favorites=favorites,
        catalog=catalog,
        channel=channel or 0,
        backup_dir=args.backup_dir,
        config_path=config_path,
        live_names_path=args.live_names or livenames.default_path(),
    )
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
    # Tell a background worker to stop starting new work, before waiting on
    # it -- otherwise quitting during the startup bank read waits for all
    # three banks to finish.
    app._closing = True
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
