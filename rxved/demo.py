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
from typing import Dict, Iterable, List, Optional

from xv import banks
from xv import messages as m

__all__ = ["DemoBridge", "DEMO_DEVICE_ID"]

DEMO_DEVICE_ID = m.DEFAULT_DEVICE_ID

#: Word pools for invented names. Chosen to read as plausible synth patches
#: while being obviously not Roland's -- no real instrument trademarks, per
#: the sibling projects' rule.
_ADJECTIVES = (
    "Velvet",
    "Rusty",
    "Glass",
    "Hollow",
    "Bright",
    "Distant",
    "Warm",
    "Folded",
    "Iron",
    "Paper",
    "Amber",
    "Quiet",
    "Sharp",
    "Drifting",
    "Static",
    "Woven",
)
_NOUNS = (
    "Bell",
    "Pad",
    "Bass",
    "Lead",
    "Choir",
    "Pluck",
    "Sweep",
    "Stack",
    "Drone",
    "Keys",
    "Brass",
    "Wash",
    "Spike",
    "Hum",
    "Comb",
    "Grain",
)


def _demo_name(bank_id: str, number: int) -> str:
    """A stable invented name for a slot. Deterministic, so tests can assert."""
    seed = sum(ord(c) for c in bank_id) * 31 + number * 17
    adjective = _ADJECTIVES[seed % len(_ADJECTIVES)]
    noun = _NOUNS[(seed // len(_ADJECTIVES)) % len(_NOUNS)]
    return f"{adjective} {noun}"[: m.PATCH_NAME_LEN]


class DemoBridge:
    """Answers the subset of :class:`xv.bridge.XvBridge` that rxved uses.

    Not a subclass: :mod:`xv.bridge` imports rtmidi at module scope, and the
    point of this class is to work where that import fails.
    """

    def __init__(
        self, *, latency: float = 0.0, channel: int = 0, patch_mode: bool = False
    ) -> None:
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
        #: PERFORM by default, because the multitimbral paths are the ones
        #: with anything to get wrong. `patch_mode=True` gives a fake in
        #: PATCH mode instead, which is what `scan_bank` needs -- a program
        #: change only moves the current patch in PATCH mode, so a scan
        #: against a PERFORM-mode fake correctly refuses.
        self.patch_mode = patch_mode
        #: Edits made through the multi-mode screen, as the temporary
        #: performance would hold them: in memory, gone when this object is.
        self._part_edits: Dict[int, dict] = {}
        self._channel_edits: Dict[int, dict] = {}
        #: Whole performances the fake has been asked to store, keyed by
        #: address prefix. In memory only -- a demo session writes nothing.
        self._performances: Dict[tuple, dict] = {}
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
            send_port="demo",
            recv_port="demo",
            device_id=DEMO_DEVICE_ID,
            family=m.IDENTITY_FAMILY,
            family_number=m.IDENTITY_FAMILY_NUMBER,
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

    def read_user_bank(
        self, bank_id: str, *, on_progress=None, timeout=None
    ) -> Dict[int, str]:
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
            mode=SoundMode.PATCH if self.patch_mode else SoundMode.PERFORM,
            performance_msb=85,
            performance_lsb=0,
            performance_program=4,
            patch_msb=87,
            patch_lsb=0,
            patch_program=0,
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
        state = PartState(
            part=part,
            receive_channel=channel,
            receive_switch=part != 5,
            level=0 if part == 6 else 100,
            msb=87,
            lsb=64,
            program_change=part - 1,
            # Part 7 is muted and part 8 has every send at
            # zero, so each readable cause of silence appears
            # once in demo mode.
            mute=part == 7,
            dry=0 if part == 8 else 127,
            chorus=0 if part in (1, 8) else 20,
            reverb=0 if part == 8 else 40,
            # Part 9 carries an output assign this model
            # ignores, as a performance from a bigger sibling
            # would.
            output_assign=10 if part == 9 else 0,
            output_mfx=0,
            # A split across two parts on channel 1, so the
            # keyboard-range columns have something real in
            # them and the note-name rendering is exercised.
            key_lower=0 if part != 2 else 60,
            key_upper=59 if part == 1 else 127,
            pan=64 + (part - 8) * 4,
        )
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
        0x08: "pan",
        0x09: "coarse",
        0x0A: "fine",
        0x0B: "mono_poly",
        0x0D: "bend_range",
        0x15: "octave",
        0x16: "velocity_sens",
        0x17: "key_lower",
        0x18: "key_upper",
        0x1B: "mute",
        0x1C: "dry",
        0x1D: "chorus",
        0x1E: "reverb",
        0x1F: "output_assign",
        0x20: "output_mfx",
    }

    #: Fields the fake stores as booleans, matching PartState.
    _PART_FLAGS = ("receive_switch", "mute")

    def write_part_param(
        self, part: int, offset: int, value: int, *, verify=True, timeout=None
    ):
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
        stored = bool(value) if field in self._PART_FLAGS else value
        self._part_edits.setdefault(part, {})[field] = stored
        return value

    def read_performance_blocks(self, base, *, on_progress=None, timeout=None):
        from xv.bridge import PERFORMANCE_BLOCKS

        self._tick()
        stored = self._performances.get(tuple(base))
        if stored is not None:
            return dict(stored)
        # A demo performance whose bytes are deterministic but not uniform,
        # so a round-trip test cannot pass by comparing zeros to zeros.
        out = {}
        for index, (name, _sub, size) in enumerate(PERFORMANCE_BLOCKS):
            seed = (base[0] * 7 + base[1] * 13 + index) & 0x7F
            out[name] = bytes((seed + i) & 0x7F for i in range(size))
        out["common"] = b"Demo Perf   " + out["common"][12:]
        if on_progress is not None:
            for i in range(1, len(PERFORMANCE_BLOCKS) + 1):
                on_progress(i, len(PERFORMANCE_BLOCKS))
        return out

    def write_performance_blocks(
        self, base, blocks, *, on_progress=None, verify=True, timeout=None
    ):
        from xv.bridge import PERFORMANCE_BLOCKS

        missing = [n for n, _a, _s in PERFORMANCE_BLOCKS if n not in blocks]
        if missing:
            raise ValueError(
                f"refusing to write a partial performance; missing {', '.join(missing)}"
            )
        self._tick()
        self._performances[tuple(base)] = dict(blocks)
        if on_progress is not None:
            for i in range(1, len(PERFORMANCE_BLOCKS) + 1):
                on_progress(i, len(PERFORMANCE_BLOCKS))
        return []

    def store_temporary_to_slot(self, slot: int, *, on_progress=None, timeout=None):
        from xv.bridge import TEMPORARY_PERFORMANCE, user_performance_base

        base = user_performance_base(slot)
        previous = self.read_performance_blocks(base)
        blocks = self.read_performance_blocks(TEMPORARY_PERFORMANCE)
        mismatched = self.write_performance_blocks(
            base, blocks, on_progress=on_progress
        )
        return previous, mismatched

    def read_performance_midi(self, channel: int, *, timeout=None):
        from xv.bridge import ChannelMidi

        if not 0 <= channel <= 15:
            raise ValueError(f"channel {channel} is outside 0-15")
        self._tick()
        # Channel 3 ignores Bank Select: a select there changes the patch to
        # the wrong one rather than failing, which is the nastiest of the
        # failures this screen exists to surface, so the demo has one.
        base = ChannelMidi(
            channel=channel, bank_select=channel != 2, program_change=channel != 11
        )
        return replace(base, **self._channel_edits.get(channel, {}))

    _CHANNEL_FIELDS = {
        0x00: "program_change",
        0x01: "bank_select",
        0x02: "bender",
        0x03: "poly_pressure",
        0x04: "channel_pressure",
        0x05: "modulation",
        0x06: "volume",
        0x07: "pan",
        0x08: "expression",
        0x09: "hold_1",
        0x0A: "phase_lock",
        0x0B: "velocity_curve",
    }

    def write_channel_param(
        self, channel: int, offset: int, value: int, *, verify=True, timeout=None
    ):
        if not 0 <= channel <= 15:
            raise ValueError(f"channel {channel} is outside 0-15")
        if offset not in self._CHANNEL_FIELDS:
            raise ValueError(f"offset {offset:#04x} is not writable")
        self._tick()
        field = self._CHANNEL_FIELDS[offset]
        stored = value if field == "velocity_curve" else bool(value)
        self._channel_edits.setdefault(channel, {})[field] = stored
        return value

    def read_channel_midi(self, *, on_progress=None, timeout=None):
        out = []
        for channel in range(16):
            out.append(self.read_performance_midi(channel))
            if on_progress is not None:
                on_progress(channel + 1, 16)
        return tuple(out)

    def read_performance_fx(self, *, timeout=None):
        from xv.bridge import PerformanceFx

        self._tick()
        return PerformanceFx(
            mfx_type=12,
            mfx_dry=127,
            mfx_chorus=0,
            mfx_reverb=40,
            mfx_output=1,
            chorus_type=1,
            chorus_level=64,
            chorus_output=1,
            chorus_output_select=0,
            reverb_type=1,
            reverb_level=80,
            reverb_output=1,
        )

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
        fx = self.read_performance_fx() if want else None
        midi = self.read_channel_midi() if want else ()
        self.state = DeviceState(
            setup=setup, channels=channels, parts=parts, common=common, fx=fx, midi=midi
        )
        return self.state

    def refresh_channel(self, channel: int, *, timeout=None):
        return self.read_state()

    def select(self, entry: banks.Slot, *, channel: Optional[int] = None) -> None:
        if channel is None:
            channel = self.channel_for(entry.kind)
        #: Recorded alongside the slot so a test can assert which channel a
        #: select actually went out on -- the thing the real bug was about.
        self.selected = entry
        self.selected_log.append(entry)
        self.channel_log.append(channel)
        self._tick()

    def select_raw(
        self, msb: int, lsb: int, program_change: int, *, channel: Optional[int] = None
    ) -> None:
        self.raw_log.append((msb, lsb, program_change))
        self._tick()

    def scan_bank(
        self,
        bank_id: str,
        *,
        on_progress=None,
        settle: float = 0.0,
        restore: bool = True,
        timeout=None,
    ) -> Dict[int, str]:
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

    def probe_srx(
        self,
        *,
        lsb_range: Iterable[int] = range(0, 64),
        settle: float = 0.0,
        restore: bool = True,
        on_progress=None,
        timeout=None,
    ) -> Dict[int, str]:
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
