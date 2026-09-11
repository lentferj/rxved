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

"""A stand-in XV-2020, so rxved runs with no hardware and no MIDI ports.

Follows the sibling projects' rule: **develop and test synthetically.** Only
one session may drive the synth at a time, and a browser that has to be
pointed at real hardware to be worked on is a browser nobody can work on
while somebody is playing.

The names it invents are invented -- see CLAUDE.md on why fixtures never
carry a manufacturer's patch names. They are deliberately unlike the real
ones so that a screenshot taken in demo mode cannot be mistaken for a
screenshot of a real machine's contents.

:class:`DemoBridge` opens no ports and constructs no rtmidi object, so it
imports cleanly on a host with no MIDI stack at all.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from xv import banks
from xv import messages as m

__all__ = ["DemoBridge", "DEMO_DEVICE_ID"]

DEMO_DEVICE_ID = m.DEFAULT_DEVICE_ID

#: Word pools for invented names. Chosen to read as plausible synth patches
#: while being obviously not Roland's -- no real instrument trademarks, per
#: the sibling projects' rule.
_ADJECTIVES = (
    "Velvet", "Rusty", "Glass", "Hollow", "Bright", "Distant", "Warm",
    "Folded", "Iron", "Paper", "Amber", "Quiet", "Sharp", "Drifting",
    "Static", "Woven",
)
_NOUNS = (
    "Bell", "Pad", "Bass", "Lead", "Choir", "Pluck", "Sweep", "Stack",
    "Drone", "Keys", "Brass", "Wash", "Spike", "Hum", "Comb", "Grain",
)


def _demo_name(bank_id: str, number: int) -> str:
    """A stable invented name for a slot. Deterministic, so tests can assert."""
    seed = sum(ord(c) for c in bank_id) * 31 + number * 17
    adjective = _ADJECTIVES[seed % len(_ADJECTIVES)]
    noun = _NOUNS[(seed // len(_ADJECTIVES)) % len(_NOUNS)]
    return f"{adjective} {noun}"[:m.PATCH_NAME_LEN]


class DemoBridge:
    """Answers the subset of :class:`xv.bridge.XvBridge` that rxved uses.

    Not a subclass: :mod:`xv.bridge` imports rtmidi at module scope, and the
    point of this class is to work where that import fails.
    """

    def __init__(self, *, latency: float = 0.0, channel: int = 0) -> None:
        self.description = "demo (no MIDI ports opened)"
        self.device_id = DEMO_DEVICE_ID
        self.channel = channel
        self.timeout = 1.0
        #: Slowing the fake down is the only way to see the browser's
        #: progress reporting and its "reading..." states without hardware.
        self.latency = latency
        self.identity = None
        #: What the fake is "playing", so select/scan behave like the real
        #: thing rather than being no-ops.
        self.selected: Optional[banks.Slot] = None
        self.selected_log: List[banks.Slot] = []
        self.channel_log: List[Optional[int]] = []
        #: Raw (msb, lsb, pc) triples, which is how a restore goes out.
        self.raw_log: List[tuple] = []
        #: The demo synth answers with the factory defaults -- patch
        #: channel 1, performance control channel 16 -- so the two-channel
        #: behaviour is exercised without hardware. Deliberately *not* the
        #: same channel: a fake where both are 1 would pass a test that a
        #: real machine fails.
        self.channels: Optional[object] = None
        self.state: Optional[object] = None
        #: Edits made through the multi-mode screen, as the temporary
        #: performance would hold them: in memory, gone when this object is.
        self._part_edits: Dict[int, dict] = {}
        self._closed = False

    # --- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self._closed = True

    def _tick(self) -> None:
        if self.latency:
            time.sleep(self.latency)

    # --- identity -----------------------------------------------------------

    def identify(self, *, timeout: Optional[float] = None):
        from xv.bridge import DeviceIdentity

        self._tick()
        return DeviceIdentity(
            send_port="demo", recv_port="demo", device_id=DEMO_DEVICE_ID,
            family=m.IDENTITY_FAMILY, family_number=m.IDENTITY_FAMILY_NUMBER,
            revision=(0, 0, 0, 0),
        )

    def is_connected(self, *, timeout: float = 1.0) -> bool:
        return True

    # --- reads --------------------------------------------------------------

    def user_patch_name(self, number: int, *, timeout=None) -> str:
        if not 1 <= number <= 128:
            raise ValueError(f"user patch number {number} is outside 1-128")
        self._tick()
        return _demo_name("USER", number)

    def user_performance_name(self, number: int, *, timeout=None) -> str:
        if not 1 <= number <= 64:
            raise ValueError(f"user performance {number} is outside 1-64")
        self._tick()
        return _demo_name("P-USER", number)

    def user_rhythm_name(self, number: int, *, timeout=None) -> str:
        if not 1 <= number <= 4:
            raise ValueError(f"user rhythm {number} is outside 1-4")
        self._tick()
        return _demo_name("R-USER", number)

    def temporary_patch_name(self, *, timeout=None) -> str:
        self._tick()
        if self.selected is None:
            return "Demo Init"
        return _demo_name(self.selected.bank_id, self.selected.number)

    def read_user_bank(self, bank_id: str, *, on_progress=None,
                       timeout=None) -> Dict[int, str]:
        readers = {
            "USER": self.user_patch_name,
            "P-USER": self.user_performance_name,
            "R-USER": self.user_rhythm_name,
        }
        if bank_id not in readers:
            raise LookupError(
                f"{bank_id} has no address in the XV-2020's parameter map, "
                f"so it cannot be read directly; only "
                f"{', '.join(sorted(readers))} can."
            )
        read = readers[bank_id]
        entries = banks.slots(bank_id)
        out: Dict[int, str] = {}
        for index, entry in enumerate(entries):
            out[entry.number] = read(entry.number)
            if on_progress is not None:
                on_progress(index + 1, len(entries))
        return out

    # --- the operations that would make a sound -----------------------------

    def system_channels(self, *, timeout=None):
        from xv.bridge import SystemChannels

        self._tick()
        # Factory defaults: patch on 1 (wire 0), performances on 16 (wire 15).
        return SystemChannels(patch_receive=0, performance_control=15)

    def use_system_channels(self, *, timeout=None):
        self.channels = self.system_channels()
        return self.channels

    def channel_for(self, kind: str) -> Optional[int]:
        if self.channels is not None:
            return self.channels.for_kind(kind)
        return self.channel

    def read_setup(self, *, timeout=None):
        from xv.bridge import SetupState, SoundMode

        self._tick()
        # A demo synth in PERFORM mode, so the multitimbral paths -- which
        # are the ones with anything to get wrong -- are the ones exercised.
        return SetupState(
            mode=SoundMode.PERFORM,
            performance_msb=85, performance_lsb=0, performance_program=4,
            patch_msb=87, patch_lsb=0, patch_program=0,
        )

    def read_part(self, part: int, *, timeout=None):
        from xv.bridge import PartState

        if not 1 <= part <= 16:
            raise ValueError(f"part {part} is outside 1-16")
        self._tick()
        # Parts 1 and 2 deliberately share channel 1, as a layer -- the real
        # machine does this and a fake where every part had its own channel
        # would let the layered case go untested.
        channel = 0 if part in (1, 2) else part - 1
        # Two deliberately silenced parts, one by each readable mechanism,
        # so the silence report has something real to find in demo mode.
        # Both of these are what a channel that has "gone quiet" usually is.
        state = PartState(part=part, receive_channel=channel,
                          receive_switch=part != 5,
                          level=0 if part == 6 else 100,
                          msb=87, lsb=64, program_change=part - 1)
        return replace(state, **self._part_edits.get(part, {}))

    #: Offsets this fake accepts, mapped to the PartState field they set.
    #: Same allowlist as the real bridge, which a test checks.
    _PART_FIELDS = {
        0x00: "receive_channel",
        0x01: "receive_switch",
        0x04: "msb",
        0x05: "lsb",
        0x06: "program_change",
        0x07: "level",
    }

    def write_part_param(self, part: int, offset: int, value: int, *,
                         verify=True, timeout=None):
        """Remember an edit, the way the temporary area would hold it.

        Kept in memory and never written anywhere, so a demo session is
        still a session that touches nothing.
        """
        if not 1 <= part <= 16:
            raise ValueError(f"part {part} is outside 1-16")
        if offset not in self._PART_FIELDS:
            raise ValueError(f"offset {offset:#04x} is not writable")
        self._tick()
        field = self._PART_FIELDS[offset]
        stored = bool(value) if field == "receive_switch" else value
        self._part_edits.setdefault(part, {})[field] = stored
        return value

    def read_performance_common(self, *, timeout=None):
        from xv.bridge import PerformanceCommon

        self._tick()
        # Solo off: the demo's silent parts are silent for per-part reasons,
        # which keeps the two mechanisms distinguishable in a screenshot.
        return PerformanceCommon(name="Demo Multi", solo=None)

    def read_parts(self, *, on_progress=None, timeout=None):
        out = []
        for part in range(1, 17):
            out.append(self.read_part(part))
            if on_progress is not None:
                on_progress(part, 16)
        return tuple(out)

    def read_state(self, *, with_parts=None, on_progress=None, timeout=None):
        from xv.bridge import DeviceState

        setup = self.read_setup()
        channels = self.system_channels()
        self.channels = channels
        want = setup.multitimbral if with_parts is None else with_parts
        parts = self.read_parts(on_progress=on_progress) if want else ()
        common = self.read_performance_common() if want else None
        self.state = DeviceState(setup=setup, channels=channels, parts=parts,
                                 common=common)
        return self.state

    def refresh_channel(self, channel: int, *, timeout=None):
        return self.read_state()

    def select(self, entry: banks.Slot, *, channel: Optional[int] = None
               ) -> None:
        if channel is None:
            channel = self.channel_for(entry.kind)
        #: Recorded alongside the slot so a test can assert which channel a
        #: select actually went out on -- the thing the real bug was about.
        self.selected = entry
        self.selected_log.append(entry)
        self.channel_log.append(channel)
        self._tick()

    def select_raw(self, msb: int, lsb: int, program_change: int, *,
                   channel: Optional[int] = None) -> None:
        self.raw_log.append((msb, lsb, program_change))
        self._tick()

    def scan_bank(self, bank_id: str, *, on_progress=None,
                  settle: float = 0.0, restore: bool = True,
                  timeout=None) -> Dict[int, str]:
        entries = banks.slots(bank_id)
        out: Dict[int, str] = {}
        before = (87, 0, 0) if restore else None
        for index, entry in enumerate(entries):
            self.select(entry)
            out[entry.number] = _demo_name(bank_id, entry.number)
            if on_progress is not None:
                on_progress(index + 1, len(entries), out[entry.number])
        if before is not None:
            self.select_raw(*before)
        return out

    def probe_srx(self, *, lsb_range: Iterable[int] = range(0, 64),
                  settle: float = 0.0, restore: bool = True, on_progress=None,
                  timeout=None) -> Dict[int, str]:
        """Pretend an SRX-07 is fitted, so the probe path has something to find."""
        card = banks.srx_card("SRX-07")
        expected = {
            card.patch_lsb_base + page: _demo_name(f"{card.id}-{page + 1}", 1)
            for page in range(4)
        }
        found: Dict[int, str] = {}
        for lsb in lsb_range:
            self._tick()
            if lsb in expected:
                found[lsb] = expected[lsb]
            if on_progress is not None:
                on_progress(lsb, found.get(lsb))
        if restore:
            self.select_raw(87, 0, 0)
        return found
