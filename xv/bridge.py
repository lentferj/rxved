# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
# The throttled-output queue, the MultiIn merged-input facade, the
# rtmidi-backend-client leak fix, the port-enumeration helpers, the
# read-modify-write config store and the clean-exit signal handling are
# ported from the sibling s3ked project's s3k/bridge.py, which ports them
# from eosed (eos/bridge.py), which ports them from k2kremote
# (k2kremote/midi_bridge.py), which ports them from mpc2emu
# (tests/re_banks/krz_sysex_live.py):
#   Copyright (C) 2025-2026  mpc2emu contributors    - GPL-2.0-or-later
#   Copyright (C) 2026  k2kremote contributors       - GPL-2.0-or-later
#   Copyright (C) 2026  eosed contributors           - GPL-2.0-or-later
#   Copyright (C) 2026  s3ked contributors           - GPL-2.0-or-later
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

"""MIDI transport and device operations for the XV-2020.

**Partly verified against hardware, on 2026-09-11.** What was exercised
against a real XV-2020 and what was not is set out in DISCLAIMER.md and
docs/RESOLUTION_NOTES.md -- read which is which before trusting a byte
offset.

Three things here differ from the sibling projects and are worth reading
before trusting them.

**Autodetect can use a real broadcast, and that is a luxury.** s3ked has to
sweep, because its protocol has no broadcast address and the only message
reporting a device's exclusive channel can itself only be sent to the right
channel. The XV-2020 answers a Universal Identity Request sent to device
``7F``, and its reply carries both the family number that identifies the
model and the device ID it is set to. So discovery here is one message per
port, and it comes back knowing what to talk to -- no channel sweep, no
guessing. :meth:`XvBridge.connect` uses it, both to check the port it
remembers and to sweep when asked to.

The discriminator against a MIDI-Thru loop echoing our own bytes is that we
send an Identity *Request* (sub-ID 01) and match only an Identity *Reply*
(sub-ID 02) -- the same different-opcode trick k2kremote uses.

**Selecting a patch is a side effect on an instrument somebody may be
playing.** Bank Select plus Program Change changes what the XV-2020 sounds,
immediately and audibly. rxved's browser is otherwise read-only, so every
path that sends one is explicit about it and none of them fire from cursor
movement. :meth:`scan_bank` is the sharp one: reading preset names means
selecting each patch in turn, because the preset banks have no addresses in
the map. It is never called implicitly.

**Read gaps are guesses and are labelled as such.** Nothing in this project
has been timed against hardware. The manual gives exactly one number --
packets over 256 bytes go out "at an interval of about 20 ms" (OM p. 145) --
which is Roland describing its own sending, not a floor for ours. The
defaults below are conservative multiples of that, and finding the real
floor is a hardware task recorded in TODO.md. They are not measured, unlike
s3ked's, and must not be copied as if they were.
"""

from __future__ import annotations

import signal
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import rtmidi  # noqa: E402

from xv import banks
from xv import messages as m

#: Local settings, gitignored. Re-exported here so ``xv.bridge`` keeps its
#: old API: ``from xv import bridge as b; b.load_channel(...)`` still works.
from xv.config import (
    DEFAULT_CONFIG_PATH,
    load_channel,
    load_device_id,
    load_last_ports,
    save_channel,
    save_device_id,
    save_last_ports,
)
from xv.params import (
    MONO_POLY,
    OUTPUT_ASSIGN,
    OUTPUT_ASSIGN_ON_XV2020,
    OUTPUT_MFX,
    PERFORMANCE_BLOCKS,
    SIGNED_PART_FIELDS,
    TEMPORARY_PERFORMANCE,
    USER_PERFORMANCE_SLOTS,
    note_name,
    user_performance_base,
)

__all__ = [
    "SEND_GAP",
    "SELECT_GAP",
    "DEFAULT_TIMEOUT",
    "AUTODETECT_TIMEOUT",
    "DEFAULT_CONFIG_PATH",
    "MidiUnavailable",
    "DeviceNotFound",
    "AmbiguousDevice",
    "list_ports",
    "bidirectional_ports",
    "likely_xv_ports",
    "load_last_ports",
    "save_last_ports",
    "load_device_id",
    "save_device_id",
    "load_channel",
    "save_channel",
    "ThrottledOut",
    "MultiIn",
    "install_clean_exit",
    "DeviceIdentity",
    "SystemChannels",
    "SoundMode",
    "SetupState",
    "PartState",
    "PerformanceCommon",
    "PerformanceFx",
    "ChannelMidi",
    # Re-exported from xv.params so callers have one import site for the
    # tables the state objects are read against.
    "MONO_POLY",
    "OUTPUT_ASSIGN",
    "OUTPUT_ASSIGN_ON_XV2020",
    "OUTPUT_MFX",
    "SIGNED_PART_FIELDS",
    "USER_PERFORMANCE_SLOTS",
    "note_name",
    "DeviceState",
    "PERFORMANCE_CHANNEL_OFF",
    "DeviceError",
    "XvBridge",
]

# --- defaults ---------------------------------------------------------------

#: Gap enforced after an outgoing SysEx request.
#:
#: **A GUESS, not a measurement.** A request is followed by a blocking wait
#: for its reply, so it is largely self-pacing; this is only the floor for
#: back-to-back sends. The one figure the manual gives is 20 ms between
#: Roland's own outgoing packets (OM p. 145), and this matches it.
SEND_GAP = 0.020

#: Gap enforced after a Bank Select / Program Change triple, before the
#: temporary area is read back.
#:
#: **A GUESS, and the one most likely to be wrong.** A program change on an
#: XV-2020 loads a patch and its four tones from ROM; reading the temporary
#: area before that finishes would return the *previous* patch, and the
#: failure is silent and plausible -- a bank scan one slot out of step, every
#: name attached to the wrong number. :meth:`scan_bank` therefore does not
#: rely on this gap alone; it checks that the name it read is not the one it
#: read last. See TODO.md for measuring the real settle time.
SELECT_GAP = 0.060

#: How long to wait for a reply before giving up. Generous, because a
#: request the device cannot serve right now is answered with silence rather
#: than an error (OM p. 145) -- so a short timeout turns "busy" into "absent".
DEFAULT_TIMEOUT = 2.0

#: Per-port budget during autodetect. One Identity Request each, so the
#: total cost is this times the number of ports.
AUTODETECT_TIMEOUT = 0.6

#: How many consecutive slots may read back a name *identical to the one
#: before* during a scan before the scan gives up and says the synth is not
#: moving. The first slot cannot be a repeat -- there is nothing to repeat
#: -- so this is reached on the (limit + 1)th select.
#:
#: Two adjacent slots genuinely sharing a name is normal and must not trip
#: this; the retry loop already re-reads those. Four repeats is past
#: coincidence in any bank Roland or a user would build, and small enough
#: that a broken scan stops after a handful of program changes rather than
#: 128 of them.
STUCK_SLOT_LIMIT = 4

#: Raw value of Performance Control Channel meaning "OFF" (OM p. 147: the
#: parameter runs 0-16 and its values read "1 - 16, OFF", so 0-15 are the
#: channels and 16 is off). With it off, performances cannot be selected over
#: MIDI at all -- which is a thing rxved should say rather than discover by
#: sending messages that vanish.
PERFORMANCE_CHANNEL_OFF = 16


#: Substrings that mark a port as probably belonging to an XV-2020. Used to
#: order the autodetect sweep, never to decide the answer -- the Identity
#: Reply decides that. A USB-connected XV-2020 enumerates as "Roland
#: XV-2020"; one on a 5-pin DIN cable is behind an interface whose name says
#: nothing at all, so name matching can only ever be a hint.
_XV_PORT_HINTS = ("xv-2020", "xv2020", "xv 2020")


class MidiUnavailable(RuntimeError):
    """No MIDI backend on this host at all."""


class DeviceError(RuntimeError):
    """The device answered, but not with what was asked for."""


class DeviceNotFound(RuntimeError):
    """Nothing answered an Identity Request on any port tried."""


class AmbiguousDevice(RuntimeError):
    """More than one XV-2020 answered, and none was pinned in the config."""

    def __init__(self, found):
        self.found = list(found)
        detail = "; ".join(
            f"{ident.send_port} -> {ident.recv_port} (device ID {ident.device_display})"
            for ident in self.found
        )
        super().__init__(
            f"{len(self.found)} XV-2020s answered: {detail}. "
            f"Pass --port to choose one, or pin send_port/recv_port in "
            f"{DEFAULT_CONFIG_PATH}."
        )


# --- MIDI port enumeration --------------------------------------------------
# python-rtmidi's close_port() does not tear down the backend ALSA sequencer
# client; only delete() does. Every transient MidiIn/MidiOut -- even one built
# solely to call get_ports() -- would otherwise orphan a client until process
# exit, and on a host with many MIDI ports a single autodetect sweep could
# exhaust the sequencer's client slots. This host has over thirty.


def _delete_quiet(port) -> None:
    try:
        port.delete()
    except (AttributeError, RuntimeError, OSError, SystemError, ValueError):
        pass


def _probe(factory, what: str):
    """Construct a transient rtmidi probe, or say why we couldn't.

    rtmidi raises out of the *constructor* when the backend is missing, so
    this cannot be handled at the get_ports() call.
    """
    try:
        return factory()
    except (RuntimeError, SystemError, OSError, ValueError) as exc:
        raise MidiUnavailable(
            f"no MIDI backend available on this host ({what}: {exc})"
        ) from exc


def _enum_in() -> List[str]:
    probe = _probe(rtmidi.MidiIn, "input")
    try:
        return probe.get_ports()
    finally:
        _delete_quiet(probe)


def _enum_out() -> List[str]:
    probe = _probe(rtmidi.MidiOut, "output")
    try:
        return probe.get_ports()
    finally:
        _delete_quiet(probe)


def list_ports() -> Tuple[List[str], List[str]]:
    """``(input_port_names, output_port_names)`` available on this host."""
    return _enum_in(), _enum_out()


def bidirectional_ports() -> List[str]:
    """Names present as both an input and an output."""
    ins, outs = list_ports()
    in_set = set(ins)
    return [name for name in outs if name in in_set]


def likely_xv_ports() -> List[str]:
    """Bidirectional ports whose name suggests an XV-2020.

    A hint for ordering the sweep and for what to offer the user first --
    **not** an answer. An XV-2020 reached over a DIN cable through a generic
    interface has a port name that says nothing about it, and a port called
    "Roland XV-2020" is only evidence that the USB device is plugged in, not
    that it is powered up and listening.
    """
    return [
        name
        for name in bidirectional_ports()
        if any(hint in name.lower() for hint in _XV_PORT_HINTS)
    ]


def _open_out(port_name: str) -> "rtmidi.MidiOut":
    out = _probe(rtmidi.MidiOut, "output")
    names = out.get_ports()
    if port_name not in names:
        _delete_quiet(out)
        raise RuntimeError(f"no output port named {port_name!r}; have {names}")
    out.open_port(names.index(port_name))
    return out


def _open_in(port_name: str) -> "rtmidi.MidiIn":
    in_port = _probe(lambda: rtmidi.MidiIn(queue_size_limit=8192), "input")
    names = in_port.get_ports()
    if port_name not in names:
        _delete_quiet(in_port)
        raise RuntimeError(f"no input port named {port_name!r}; have {names}")
    in_port.open_port(names.index(port_name))
    # Without this rtmidi drops SysEx on the floor and the reply never
    # arrives -- presenting exactly as a device that does not answer.
    in_port.ignore_types(sysex=False)
    return in_port


class ThrottledOut:
    """Wrap an ``rtmidi.MidiOut`` so SysEx never floods the device.

    Only ``0xF0``-leading messages are gapped; ordinary MIDI -- which for
    rxved means Bank Select and Program Change -- passes straight through,
    since those are three-byte messages the device handles at wire speed.

    The gap is applied as time owed *after* a send, so the wait before any
    send is determined by what preceded it rather than by what is about to
    go out.
    """

    def __init__(self, port, gap: float = SEND_GAP):
        self._port = port
        self._gap = gap
        self._last = 0.0
        self._owed = 0.0

    def send_message(self, message) -> None:
        if not message or message[0] != m.SYSEX_START:
            self._port.send_message(message)
            return
        wait = self._owed - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._port.send_message(message)
        self._last = time.time()
        self._owed = self._gap

    def __getattr__(self, name):
        return getattr(self._port, name)


class MultiIn:
    """An ``rtmidi.MidiIn``-compatible facade polling one or more ports.

    With ``exact=False`` every input whose name *contains* ``name`` is opened
    and polled in turn; with ``exact=True`` (used after autodetect, where the
    cabling is known) only the input whose name *equals* ``name`` is opened.
    """

    def __init__(self, name: str, *, exact: bool = False):
        self.ports: List = []
        for index, port_name in enumerate(_enum_in()):
            matches = (
                (port_name == name) if exact else (name.lower() in port_name.lower())
            )
            if matches:
                port = rtmidi.MidiIn(queue_size_limit=8192)
                port.open_port(index)
                port.ignore_types(sysex=False)
                self.ports.append(port)
        if not self.ports:
            raise RuntimeError(f"no input port matching {name!r}")

    def get_message(self):
        for port in self.ports:
            message = port.get_message()
            if message is not None:
                return message
        return None

    def get_ports(self) -> List[str]:
        return _enum_in()

    def close_port(self) -> None:
        for port in self.ports:
            port.close_port()
            _delete_quiet(port)
        self.ports = []


#: Signals worth turning into a clean exit, filtered to those this platform
#: HAS. Windows has no SIGHUP, and naming it in a default argument puts the
#: lookup at import time where a try/except inside the function cannot catch
#: it -- which in the sibling s3ked broke every test at collection.
CLEAN_EXIT_SIGNALS = tuple(
    number
    for number in (getattr(signal, name, None) for name in ("SIGTERM", "SIGHUP"))
    if number is not None
)


def install_clean_exit(signals=None) -> None:
    """Turn termination signals into :class:`SystemExit`, so ports close.

    Ctrl-C already unwinds: it raises ``KeyboardInterrupt`` and any
    ``finally`` that closes the bridge runs. **SIGTERM does not** -- the
    default action ends the process where it stands, leaving the MIDI port
    open and a request outstanding that the synth is still composing an
    answer to.

    Idempotent, and it leaves alone any handler the caller has already
    installed -- a host application with its own shutdown is better at this
    than we are.
    """
    for number in CLEAN_EXIT_SIGNALS if signals is None else signals:
        try:
            existing = signal.getsignal(number)
        except (ValueError, OSError):  # not available on this platform
            continue
        if existing not in (signal.SIG_DFL, None):
            continue  # somebody else owns it
        try:
            signal.signal(
                number,
                lambda signum, _frame: (_ for _ in ()).throw(SystemExit(128 + signum)),
            )
        except (ValueError, OSError):
            # signal() only works on the main thread of the main interpreter
            continue


# --- the bridge -------------------------------------------------------------


class SoundMode:
    """What the synth is currently doing, from Setup ``01 00 00 00`` offset 0.

    This is the setting that decides what a Bank Select and Program Change
    on a given channel *mean*, so nothing that sends one should proceed
    without knowing it:

    * **PATCH** -- one patch, on the Patch Receive Channel. Single-timbral.
      Bank/PC on any other channel does nothing at all.
    * **PERFORM** -- 16 parts, each with its own Receive Channel and its own
      patch. Bank/PC on a part's channel selects that part's patch; Bank/PC
      on the Performance Control Channel selects the whole performance.
    * **GM1 / GM2 / GS** -- multitimbral under the respective standard.
    """

    PATCH = 1
    PERFORM = 2
    GM1 = 3
    GM2 = 4
    GS = 5

    NAMES = {PATCH: "PATCH", PERFORM: "PERFORM", GM1: "GM1", GM2: "GM2", GS: "GS"}

    #: Modes in which more than one MIDI channel selects anything.
    MULTITIMBRAL = frozenset({PERFORM, GM1, GM2, GS})

    @classmethod
    def name(cls, value: int) -> str:
        return cls.NAMES.get(value, f"unknown ({value})")


@dataclass(frozen=True)
class PartState:
    """One Performance Part: which channel it listens on, and what it holds.

    Read from Temporary Performance, ``10 00 <20+n-1> 00`` (OM p. 146, 149):
    offset ``00 00`` is Receive Channel and ``00 04``-``00 06`` are the
    part's own Patch Bank Select MSB / LSB / Program Number.
    """

    part: int  #: 1-16, as the front panel numbers them.
    receive_channel: int  #: 0-based wire channel.
    msb: int
    lsb: int
    program_change: int
    #: Offset ``00 01``, Receive Switch. OFF means the part ignores its
    #: channel entirely -- the first thing to check when a channel is silent.
    receive_switch: bool = True
    #: Offset ``00 07``, Part Level (CC#7). 0 is silence that looks like a
    #: dead channel but is really a fader down.
    level: int = 127
    #: Offset ``00 1B``, Mute Switch. Silences the part while it goes on
    #: receiving and tracking MIDI (OM p. 74).
    mute: bool = False
    #: Offsets ``00 1C``-``00 1E``: Dry, Chorus (CC#93) and Reverb (CC#91)
    #: send levels. Dry at 0 with no send routed is another way to be
    #: inaudible while every switch reads on.
    dry: int = 127
    chorus: int = 0
    reverb: int = 0
    #: Offset ``00 1F``, Part Output Assign -- where the part's dry signal
    #: goes. See :data:`OUTPUT_ASSIGN`.
    output_assign: int = 0
    #: Offset ``00 20``, Part Output MFX Select. See :data:`OUTPUT_MFX`.
    output_mfx: int = 0
    #: Musical settings, all stored **biased by 64** where they are signed --
    #: the wire byte is not the number the manual prints. Kept as the wire
    #: value here and converted once, visibly, at the edge; see
    #: :data:`SIGNED_PART_FIELDS`.
    pan: int = 64  #: ``00 08``, 0-127, displayed L64-63R.
    coarse: int = 64  #: ``00 09``, 16-112, displayed -48-+48.
    fine: int = 64  #: ``00 0A``, 14-114, displayed -50-+50.
    mono_poly: int = 2  #: ``00 0B``: MONO, POLY, PATCH.
    bend_range: int = 25  #: ``00 0D``, 0-24 semitones or 25 = PATCH.
    octave: int = 64  #: ``00 15``, 61-67, displayed -3-+3.
    velocity_sens: int = 64  #: ``00 16``, 1-127, displayed -63-+63.
    key_lower: int = 0  #: ``00 17``, note number.
    key_upper: int = 127  #: ``00 18``, note number.

    @property
    def channel_display(self) -> int:
        return self.receive_channel + 1

    @property
    def slot(self) -> Optional[banks.Slot]:
        """The bank/slot this part is set to, if rxved's table claims it."""
        return banks.lookup(self.msb, self.lsb, self.program_change)

    @property
    def output_name(self) -> str:
        return OUTPUT_ASSIGN.get(self.output_assign, f"?{self.output_assign}")

    @property
    def output_mfx_name(self) -> str:
        return OUTPUT_MFX.get(self.output_mfx, f"?{self.output_mfx}")

    @property
    def silent(self) -> bool:
        """Whether this part cannot be heard, for a readable reason."""
        return self.silence_reason() is not None

    def silence_reason(self) -> Optional[str]:
        """Why this part makes no sound, in the words the synth's page uses.

        Ordered by how far from the sound the cause sits, so the first
        answer is the nearest one to fix.
        """
        if not self.receive_switch:
            return "RX SWITCH is OFF"
        if self.mute:
            return "MUTE is on"
        if self.level == 0:
            return "LEVEL is 0"
        if self.dry == 0 and self.chorus == 0 and self.reverb == 0:
            return "DRY, CHO and REV sends are all 0"
        return None


@dataclass(frozen=True)
class SetupState:
    """The Setup block: what is selected right now.

    ``01 00 00 00``, 15 bytes (the map gives Total Size ``00 00 00 0F``).
    Carries the sound mode plus the Bank Select and Program Change of both
    the current patch and the current performance -- which is to say, the
    read-back of exactly what rxved's browser sends.
    """

    mode: int
    patch_msb: int
    patch_lsb: int
    patch_program: int
    performance_msb: int
    performance_lsb: int
    performance_program: int

    @property
    def mode_name(self) -> str:
        return SoundMode.name(self.mode)

    @property
    def multitimbral(self) -> bool:
        return self.mode in SoundMode.MULTITIMBRAL

    @property
    def is_patch_mode(self) -> bool:
        """Whether a program change moves *the* patch this synth has.

        True only in PATCH mode, and that is what makes it worth naming:
        `scan_bank` works by selecting a slot and reading back the patch the
        machine says it is now playing. In every other mode the read-back
        does not follow the program change -- verified on a real XV-2020 in
        PERFORM mode, which answered all 128 reads with the patch it was
        already sitting on -- so a scan there cannot produce names, only a
        plausible-looking row of the same wrong one.
        """
        return self.mode == SoundMode.PATCH

    @property
    def patch_slot(self) -> Optional[banks.Slot]:
        return banks.lookup(self.patch_msb, self.patch_lsb, self.patch_program)

    @property
    def performance_slot(self) -> Optional[banks.Slot]:
        return banks.lookup(
            self.performance_msb, self.performance_lsb, self.performance_program
        )


def _describe_selection(slot, msb: int, lsb: int, program: int) -> str:
    """A slot if rxved's table claims the triple, else the raw numbers.

    Saying only "an unrecognised bank/PC" is the least useful thing possible
    at exactly the moment the numbers matter most: an unclaimed triple means
    either the synth is somewhere rxved's bank table does not cover (an SRX
    board it has no row for, a GM variation past LSB 9) or the table is
    wrong. Both are diagnosable from the bytes and neither is diagnosable
    from the word "unrecognised".
    """
    if slot is not None:
        return str(slot)
    return f"MSB {msb} / LSB {lsb} / PC {program} — no bank claims this"


#: Where a setting can actually be changed on an XV-2020. The module has a
#: three-digit LED and four controls, and OM p. 116 lists exactly which
#: parameters those can reach. Most of what silences a part is not on that
#: list, so "change it on the front panel" is advice that cannot be
#: followed -- it takes the XV-2020 Editor, or SysEx, which is what rxved's
#: multi-mode screen now sends.
_WHERE_TO_CHANGE = (
    "On the module itself you can reach the sound mode (press [VALUE] until "
    "the PATCH or PERFORM indicator lights, OM p. 38), Part Level (hold "
    "[VOLUME], press [VALUE], then [CATEGORY/BANK], OM p. 72) and a part's "
    "Receive Channel ([PATCH RX CH]/[PART], OM p. 94). Receive Switch, Mute "
    "Switch and Solo Part Select are NOT in the module's own parameter list "
    "(OM p. 116) -- those take the XV-2020 Editor, or this screen."
)


def _ranges(numbers: Iterable[int]) -> str:
    """``[3,4,5,9]`` -> ``"3-5, 9"``. For reports that would otherwise be
    a column of near-identical lines."""
    ordered = sorted(set(numbers))
    if not ordered:
        return ""
    spans: List[List[int]] = [[ordered[0], ordered[0]]]
    for number in ordered[1:]:
        if number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ", ".join(
        str(low) if low == high else f"{low}-{high}" for low, high in spans
    )


#: Chorus and Reverb Type on an XV-2020 are one bit: the effect is in or
#: out. Its siblings offer several algorithms here.
_ON_OFF = {0: "OFF", 1: "ON"}


@dataclass(frozen=True)
class ChannelMidi:
    """Performance MIDI for one channel: what that channel will accept.

    ``10 00 <10+channel> 00``, 12 bytes (OM p. 146, 74). **Per MIDI channel,
    not per part** -- the manual marks these ``+`` where the per-part
    parameters are marked ``#``, and the address map gives them their own
    sixteen blocks. Two parts sharing a channel share these settings.

    The first two bytes are the ones that matter to rxved, because they gate
    exactly what it sends:

    * **Receive Program Change** off -- a Program Change does nothing.
    * **Receive Bank Select** off -- the Bank Select bytes are dropped and
      the Program Change lands in whatever bank the part was already on.

    The second is the nastier of the two: a select appears to work, the part
    changes patch, and it is the wrong patch. Neither failure produces any
    response at all from the synth, so nothing downstream can detect it.
    """

    channel: int  #: 0-based wire channel.
    program_change: bool = True
    bank_select: bool = True
    bender: bool = True
    poly_pressure: bool = True
    channel_pressure: bool = True
    modulation: bool = True
    volume: bool = True
    pan: bool = True
    expression: bool = True
    hold_1: bool = True
    phase_lock: bool = False
    velocity_curve: int = 0

    @property
    def channel_display(self) -> int:
        return self.channel + 1

    @property
    def accepts_selection(self) -> bool:
        """Whether a Bank Select + Program Change here would do what it says."""
        return self.program_change and self.bank_select

    def selection_problem(self) -> Optional[str]:
        """Why a select on this channel would not land, if it would not."""
        if not self.program_change and not self.bank_select:
            return "ignores Program Change and Bank Select"
        if not self.program_change:
            return "ignores Program Change"
        if not self.bank_select:
            return (
                "ignores Bank Select -- a Program Change would land in "
                "whatever bank the part is already on"
            )
        return None


@dataclass(frozen=True)
class PerformanceFx:
    """The performance's MFX, chorus and reverb, as far as routing goes.

    Enough to answer "where does a part's sound actually go", which is the
    question the multi-mode screen is for. Not enough to edit an effect,
    which rxved does not do.
    """

    mfx_type: int
    mfx_dry: int
    mfx_chorus: int
    mfx_reverb: int
    mfx_output: int
    chorus_type: int
    chorus_level: int
    chorus_output: int
    chorus_output_select: int
    reverb_type: int
    reverb_level: int
    reverb_output: int

    #: Chorus Output Select (``10 00 04 00`` offset ``00 03``).
    CHORUS_OUTPUT_SELECT = {0: "MAIN", 1: "REV", 2: "MAIN+REV"}

    @property
    def chorus_on(self) -> bool:
        return bool(self.chorus_type)

    @property
    def reverb_on(self) -> bool:
        return bool(self.reverb_type)

    def summary(self) -> str:
        """One line for the top of the multi-mode screen."""
        chorus = _ON_OFF.get(self.chorus_type, "?")
        reverb = _ON_OFF.get(self.reverb_type, "?")
        route = self.CHORUS_OUTPUT_SELECT.get(self.chorus_output_select, "?")
        return (
            f"MFX type {self.mfx_type} → "
            f"{OUTPUT_ASSIGN.get(self.mfx_output, '?')}  "
            f"(dry {self.mfx_dry} cho {self.mfx_chorus} "
            f"rev {self.mfx_reverb})   ·   "
            f"CHORUS {chorus} level {self.chorus_level} → {route}   ·   "
            f"REVERB {reverb} level {self.reverb_level}"
        )


@dataclass(frozen=True)
class PerformanceCommon:
    """Temporary Performance Common, ``10 00 00 00`` (OM p. 149).

    Only the fields that can explain a silent channel are kept. The one that
    matters is Solo Part Select at offset ``00 0C``: with it set, exactly one
    part sounds and the other fifteen are silent while every parameter on
    them still reads back perfectly normal.
    """

    name: str
    #: Solo Part Select. ``None`` is OFF; otherwise the 1-16 part number that
    #: is soloed. The map gives 0-32 as "OFF, 1 - 16, 17 - 32", and the
    #: 17-32 half belongs to machines with 32 parts; an XV-2020 has 16.
    solo: Optional[int] = None


@dataclass(frozen=True)
class DeviceState:
    """Everything needed to answer "what does channel N do right now?"."""

    setup: SetupState
    channels: "SystemChannels"
    #: Empty unless the parts were read; they are only meaningful in a
    #: multitimbral mode.
    parts: Tuple[PartState, ...] = ()
    #: ``None`` unless Performance Common was read, for the same reason.
    common: Optional["PerformanceCommon"] = None
    #: ``None`` unless the effects blocks were read.
    fx: Optional["PerformanceFx"] = None
    #: Empty unless the 16 Performance MIDI blocks were read.
    midi: Tuple["ChannelMidi", ...] = ()

    def parts_on(self, channel: int) -> List[PartState]:
        """Every part listening on this 0-based channel.

        A **list**, not one part, and that is not defensive programming:
        parts freely share a receive channel to make a layer. The machine
        this was written against has parts 1, 2 and 3 all on channel 1.
        """
        return [p for p in self.parts if p.receive_channel == channel]

    def audible_parts(self, channel: int) -> List[PartState]:
        """Parts on this channel that would actually be heard."""
        return [
            p for p in self.parts_on(channel) if not p.silent and not self.soloed_out(p)
        ]

    def soloed_out(self, part: PartState) -> bool:
        """Whether Solo Part Select silences this part."""
        solo = self.common.solo if self.common is not None else None
        return solo is not None and part.part != solo

    def silence_report(self) -> List[str]:
        """Why channels are silent. Every readable cause, and there are six.

        This exists because the obvious answer -- "the part is muted" -- is
        often not the answer, and the real ones are spread across three
        different blocks:

        * In **PATCH mode** the synth is single-timbral. Fifteen channels are
          silent and no part parameter is out of place, because the parts are
          not in use at all.
        * **Solo Part Select** silences fifteen parts from one byte in
          Performance Common, nowhere near the parts themselves.
        * Per part: **Receive Switch** off, **Mute Switch** on, **Level** 0,
          or every **send** at 0.

        An earlier version of this claimed the Mute Switch was not in the
        parameter address map and could never be read. That was wrong: it is
        Performance Part offset ``00 1B`` (OM p. 146), and rxved both reads
        and writes it.
        """
        lines: List[str] = []
        if not self.setup.multitimbral:
            channel = self.channels.patch_display
            lines.append(
                f"Sound mode is {self.setup.mode_name}, which is "
                f"single-timbral: only channel {channel} sounds, and the "
                f"Performance Parts are not in use. This alone explains a "
                f"synth that answers on one channel and ignores the other "
                f"15. For multitimbral, press [VALUE] on the module until "
                f"the PERFORM indicator lights (OM p. 38)."
            )
            # ...but if the parts were read anyway, say what switching to
            # PERFORM would actually get. Stopping here is what turns one
            # cause into the cause: a machine can be in Patch mode *and*
            # have most of its parts switched off, and someone who fixes
            # only the mode then finds the symptom unchanged.
            forward = self._part_findings(
                lead="Switching to PERFORM would not be enough on its own. "
                "In the performance currently loaded:"
            )
            forward.extend(self._selection_findings())
            lines.extend(forward)
            if forward:
                lines.append(_WHERE_TO_CHANGE)
            return lines

        lines.extend(self._selection_findings())

        if self.common is not None and self.common.solo is not None:
            lines.append(
                f"Solo Part Select is on, set to part {self.common.solo}: "
                f"every other part is silenced from Performance Common, not "
                f"from the parts themselves."
            )

        lines.extend(self._part_findings())

        if not lines:
            lines.append(
                "Nothing is silencing any part and every channel accepts "
                "Bank Select and Program Change: Receive Switches on, MUTE "
                "off, non-zero levels, sends that go somewhere, Solo off."
            )
        lines.append(_WHERE_TO_CHANGE)
        return lines

    def channel_midi(self, channel: int) -> Optional["ChannelMidi"]:
        """The Performance MIDI block for a 0-based channel, if it was read."""
        for entry in self.midi:
            if entry.channel == channel:
                return entry
        return None

    def selection_problem(self, channel: int) -> Optional[str]:
        """Why a Bank Select + Program Change on this channel would not land.

        ``None`` means "nothing readable is in the way", which in Patch mode
        includes every channel but one -- that case is the caller's to know,
        and :meth:`describes_short` already says it.
        """
        entry = self.channel_midi(channel)
        return None if entry is None else entry.selection_problem()

    def _selection_findings(self) -> List[str]:
        """Channels that will not act on what rxved sends.

        First in the report, ahead of anything about audibility: a part can
        be perfectly audible and still ignore every select aimed at it, and
        that failure is the one this program is most able to cause.
        """
        if not self.midi:
            return []
        found: List[str] = []
        by_problem: Dict[str, List[int]] = {}
        for entry in self.midi:
            problem = entry.selection_problem()
            if problem is not None:
                by_problem.setdefault(problem, []).append(entry.channel_display)
        for problem, channels in by_problem.items():
            word = "Channel" if len(channels) == 1 else "Channels"
            found.append(f"{word} {_ranges(channels)}: {problem}.")
        return found

    def _part_findings(self, *, lead: str = "") -> List[str]:
        """Per-reason summary of what is silent. Empty when the parts are
        unread, which is not the same as "nothing wrong with them".

        Grouped by reason and collapsed to ranges rather than listed one
        part per line. On the machine this was written against thirteen
        parts share one reason, and thirteen near-identical lines restate
        what the table directly above them already shows in its own column
        -- which buries the lines that say something the table does not.
        """
        if not self.parts:
            return []
        by_reason: Dict[str, List[int]] = {}
        for part in self.parts:
            reason = part.silence_reason()
            if reason is not None:
                by_reason.setdefault(reason, []).append(part.part)

        found: List[str] = []
        for reason, numbers in by_reason.items():
            label = "Part" if len(numbers) == 1 else "Parts"
            found.append(f"{label} {_ranges(numbers)}: {reason}")

        listening = {p.receive_channel for p in self.parts if not p.silent}
        unused = [c + 1 for c in range(16) if c not in listening]
        if unused:
            word = "channel" if len(unused) == 1 else "channels"
            found.append(
                f"Nothing audible is assigned to {word} {_ranges(unused)}"
                f" -- not muted, just unused by any part that can sound."
            )
        if found and lead:
            found.insert(0, lead)
        return found

    def describes(self, channel: int) -> str:
        """One line saying what a Bank Select / PC on this channel would hit.

        Prefixed with the channel, for places that show it on its own.
        :meth:`describes_short` omits the prefix for callers that have
        already said which channel they mean.
        """
        return f"ch {channel + 1}: {self.describes_short(channel)}"

    def describes_short(self, channel: int) -> str:
        """As :meth:`describes`, without the leading channel number."""
        if not self.setup.multitimbral:
            if channel == self.channels.patch_receive:
                return "the patch — currently " + _describe_selection(
                    self.setup.patch_slot,
                    self.setup.patch_msb,
                    self.setup.patch_lsb,
                    self.setup.patch_program,
                )
            return (
                f"nothing — in {self.setup.mode_name} mode only the Patch "
                f"Receive Channel ({self.channels.patch_display}) "
                f"selects anything"
            )
        if channel == self.channels.performance_control:
            return "the whole performance — currently " + _describe_selection(
                self.setup.performance_slot,
                self.setup.performance_msb,
                self.setup.performance_lsb,
                self.setup.performance_program,
            )
        here = self.parts_on(channel)
        if not here:
            return "no part listens on this channel"
        if len(here) == 1:
            part = here[0]
            return f"part {part.part} — currently " + _describe_selection(
                part.slot, part.msb, part.lsb, part.program_change
            )
        names = ", ".join(f"{p.part}={p.slot if p.slot else '?'}" for p in here)
        return (
            f"parts {names} — layered, so a Program Change here moves all {len(here)}"
        )


@dataclass(frozen=True)
class SystemChannels:
    """The two receive channels, as **0-based wire channels**.

    0-based to match every other channel in this codebase and the byte on the
    wire; :attr:`patch_display` and :attr:`performance_display` give the
    1-based numbers the synth's own screen shows, and nothing converts
    silently between them -- the same rule as program numbers in
    :mod:`xv.banks`.
    """

    patch_receive: int
    #: ``None`` when Performance Control Channel is OFF.
    performance_control: Optional[int]

    @property
    def patch_display(self) -> int:
        return self.patch_receive + 1

    @property
    def performance_display(self) -> Optional[int]:
        if self.performance_control is None:
            return None
        return self.performance_control + 1

    def for_kind(self, kind: str) -> Optional[int]:
        """The channel a slot of this kind must be selected on.

        ``None`` means "there is no such channel", which today happens only
        for performances with the control channel off.
        """
        if kind == banks.Kind.PERFORMANCE:
            return self.performance_control
        return self.patch_receive


@dataclass(frozen=True)
class DeviceIdentity:
    """What an Identity Reply told us, and where it came from."""

    send_port: str
    recv_port: str
    device_id: int
    family: Tuple[int, int]
    family_number: Tuple[int, int]
    revision: Tuple[int, ...]

    @property
    def device_display(self) -> int:
        """The device ID as the XV-2020's own display numbers it (17-32)."""
        return m.device_id_display(self.device_id)

    @property
    def revision_text(self) -> str:
        return ".".join(str(byte) for byte in self.revision) or "unknown"


class XvBridge:
    """MIDI transport plus the handful of operations rxved needs.

    Read-only by construction: there is no method here that writes to the
    device's memory. The only outbound messages are Identity Requests, Data
    Requests, and -- from :meth:`select` and :meth:`scan_bank` alone -- Bank
    Select and Program Change, which change what the instrument is playing
    but not what it stores.
    """

    def __init__(
        self,
        send_port,
        recv_port,
        *,
        device_id: int = m.DEFAULT_DEVICE_ID,
        channel: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
        description: str = "",
        identity: Optional[DeviceIdentity] = None,
    ):
        """``device_id`` is the **wire byte** (0x10-0x1F), not the panel number.

        Deliberately not converted here. The two numberings overlap -- panel
        17-32 against wire 0x10-0x1F -- so a function that guesses which one
        it was handed guesses wrong for every value from 17 to 31, and the
        XV-2020 answers a wrongly addressed request with silence. Callers
        that have a panel number convert it once, at the edge, with
        :func:`xv.messages.device_id_byte`.
        """
        self._out = send_port
        self._in = recv_port
        if not (0x00 <= int(device_id) <= 0x1F or int(device_id) == m.BROADCAST_DEVICE):
            raise ValueError(
                f"device_id {device_id} is not a wire byte; expected "
                f"0x10-0x1F. A panel number (17-32) goes through "
                f"xv.messages.device_id_byte() first."
            )
        self.device_id = int(device_id)
        self.channel = channel
        self.timeout = timeout
        self.description = description
        self.identity = identity
        #: The synth's own receive channels, once read. ``None`` until then,
        #: which is what makes :meth:`channel_for` fall back rather than
        #: silently claim knowledge it does not have.
        self.channels: Optional["SystemChannels"] = None
        #: The last :meth:`read_state` result, if any. What the UI shows for
        #: "what is on this channel"; ``None`` until read, so nothing claims
        #: knowledge it has not fetched.
        self.state: Optional["DeviceState"] = None
        self._closed = False

    # --- construction -------------------------------------------------------

    @classmethod
    def standard(
        cls,
        port_name: str,
        *,
        recv_port_name: Optional[str] = None,
        device_id: int = 17,
        channel: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "XvBridge":
        """Open a named port pair without probing.

        ``device_id`` here is the **panel number** (17-32), because this is
        the entry point a user's ``--device-id`` reaches; it is converted to
        the wire byte once, right below.

        For when the user has said which port to use. Nothing is verified --
        if the XV-2020 is not on that port, the first read times out rather
        than failing here, which is the honest outcome given that a busy
        device and an absent one are indistinguishable on this protocol.
        """
        recv_name = recv_port_name or port_name
        out = ThrottledOut(_open_out(port_name))
        try:
            in_port = _open_in(recv_name)
        except (RuntimeError, OSError, SystemError, ValueError):
            _delete_quiet(out._port)
            raise
        return cls(
            out,
            in_port,
            device_id=m.device_id_byte(device_id),
            channel=channel,
            timeout=timeout,
            description=(
                port_name if recv_name == port_name else f"{port_name} -> {recv_name}"
            ),
        )

    @classmethod
    def autodetect(
        cls,
        *,
        config_path: str = DEFAULT_CONFIG_PATH,
        channel: Optional[int] = None,
        timeout: float = DEFAULT_TIMEOUT,
        probe_timeout: float = AUTODETECT_TIMEOUT,
        on_try: Optional[Callable[[str], None]] = None,
    ) -> "XvBridge":
        """Find an XV-2020 by broadcasting an Identity Request on every port.

        Ports are tried in the order most likely to pay off: the pair
        remembered from last time first, then ports whose name mentions an
        XV-2020, then everything else. Every port is tried even after one
        answers, because a second XV-2020 on the chain is worth refusing
        over rather than picking one.

        This is what :meth:`connect` falls back to, and what ``--scan``
        forces. The answer is saved, so the next launch need not repeat it.
        """
        found = cls._sweep(config_path, probe_timeout, on_try)
        if not found:
            raise DeviceNotFound(
                "no XV-2020 answered an Identity Request on any MIDI port. "
                "Check that it is powered on, that Rx Exclusive is ON "
                "(SYSTEM/MIDI), and that both directions are cabled -- "
                "rxved needs to hear the reply, not just send."
            )
        if len(found) > 1:
            raise AmbiguousDevice(found)
        identity = found[0]
        bridge = cls._from_identity(identity, channel, timeout)
        save_last_ports(identity.send_port, identity.recv_port, config_path)
        # Stored as the panel number, since that is what the user reads off
        # the machine and types back as --device-id.
        save_device_id(identity.device_display, config_path)
        return bridge

    @classmethod
    def _from_identity(
        cls, identity: DeviceIdentity, channel: Optional[int], timeout: float
    ) -> "XvBridge":
        """A bridge aimed at the device that just identified itself.

        `standard()` takes the **panel number**, and `identity.device_id` is
        the wire byte straight out of the Identity Reply. Handing it over
        unconverted is the same class of mistake `device_id_byte()`'s own
        docstring is about, and it is fatal rather than silent: an XV-2020
        set to 17 answers with 0x10, which is 16, and 16 is not a panel
        number -- so `standard()` raised "device ID 16 is out of range" on
        the port that had just answered. The sweep found the synth and the
        failure looked like the opposite.
        """
        bridge = cls.standard(
            identity.send_port,
            recv_port_name=identity.recv_port,
            device_id=identity.device_display,
            channel=channel if channel is not None else 0,
            timeout=timeout,
        )
        bridge.identity = identity
        return bridge

    @classmethod
    def connect(
        cls,
        *,
        config_path: str = DEFAULT_CONFIG_PATH,
        channel: Optional[int] = None,
        timeout: float = DEFAULT_TIMEOUT,
        probe_timeout: float = AUTODETECT_TIMEOUT,
        scan: bool = False,
        on_try: Optional[Callable[[str], None]] = None,
    ) -> "XvBridge":
        """Open the synth, sweeping every port only when that is necessary.

        The normal path is one Identity Request to the port remembered in
        ``config_path``. That is not just an optimisation: a sweep sends an
        Identity Request to *every* bidirectional port on the machine, which
        on a studio box with thirty of them takes seconds, prints thirty
        lines, and pings every piece of hardware on the chain once per
        launch. Once the answer is known there is nothing to learn from
        asking again.

        So: use the remembered port if there is one, sweep if there is not,
        and sweep on request. ``scan=True`` is what ``--scan`` reaches --
        needed after the synth moves to another port, which USB
        re-enumeration does by renumbering the ALSA client out from under
        the saved name.

        A remembered port that does not answer is an **error**, not a
        licence to sweep. The guess is gone, but sweeping anyway would make
        a launch silently take the slow path for a reason the user did not
        ask about; naming ``--scan`` says what happened and what to do.
        """
        if not scan:
            remembered = load_last_ports(config_path)
            if remembered is not None:
                return cls._open_remembered(
                    remembered, config_path, channel, timeout, probe_timeout, on_try
                )
        return cls.autodetect(
            config_path=config_path,
            channel=channel,
            timeout=timeout,
            probe_timeout=probe_timeout,
            on_try=on_try,
        )

    @classmethod
    def _open_remembered(
        cls,
        remembered: Tuple[str, str],
        config_path: str,
        channel: Optional[int],
        timeout: float,
        probe_timeout: float,
        on_try: Optional[Callable[[str], None]],
    ) -> "XvBridge":
        """One round trip to the saved port. Never writes to the config.

        It does not re-save what is already there, and deliberately does
        not re-save a device ID read back from the instrument: the file is
        this function's input, so writing to it would make a plain launch
        mutate the thing it is being asked to trust.
        """
        send_name, recv_name = remembered
        if on_try is not None:
            on_try(send_name)
        identity = cls._try_pair(send_name, recv_name, probe_timeout)
        if identity is None:
            raise DeviceNotFound(
                f"nothing answered an Identity Request on the remembered "
                f"port\n  {send_name}\n"
                f"which {config_path} says is the XV-2020. It may be powered "
                f"off, on a different port now (USB renumbers MIDI ports), or "
                f"busy. Re-run with --scan to probe every port and update "
                f"{config_path}."
            )
        return cls._from_identity(identity, channel, timeout)

    @classmethod
    def _sweep(
        cls,
        config_path: str,
        probe_timeout: float,
        on_try: Optional[Callable[[str], None]],
    ) -> List[DeviceIdentity]:
        candidates = cls._probe_order(config_path)
        found: List[DeviceIdentity] = []
        for send_name, recv_name in candidates:
            if on_try is not None:
                on_try(send_name)
            identity = cls._try_pair(send_name, recv_name, probe_timeout)
            if identity is not None:
                found.append(identity)
        return found

    @staticmethod
    def _probe_order(config_path: str) -> List[Tuple[str, str]]:
        """Port pairs to try, best bet first."""
        ins, outs = list_ports()
        in_set = set(ins)
        pairs: List[Tuple[str, str]] = []
        seen = set()

        def add(send_name: str, recv_name: str) -> None:
            key = (send_name, recv_name)
            if key in seen:
                return
            seen.add(key)
            pairs.append(key)

        remembered = load_last_ports(config_path)
        if remembered and remembered[0] in outs and remembered[1] in in_set:
            add(*remembered)
        for name in outs:
            if name in in_set and any(h in name.lower() for h in _XV_PORT_HINTS):
                add(name, name)
        for name in outs:
            if name in in_set:
                add(name, name)
        return pairs

    @staticmethod
    def _try_pair(
        send_name: str, recv_name: str, probe_timeout: float
    ) -> Optional[DeviceIdentity]:
        """One Identity Request on one port pair.

        Every failure is swallowed and reported as "nothing here". During a
        sweep that is right: ports get taken by other applications, vanish
        between enumeration and open, or belong to hardware that dislikes
        being written to, and none of that should stop the search.
        """
        try:
            out = _open_out(send_name)
        except (RuntimeError, OSError, SystemError, ValueError):
            return None
        in_port = None
        try:
            in_port = _open_in(recv_name)
            out.send_message(list(m.IDENTITY_REQUEST))
            deadline = time.time() + probe_timeout
            while time.time() < deadline:
                message = in_port.get_message()
                if message is None:
                    time.sleep(0.002)
                    continue
                reply = m.parse_identity_reply(message[0])
                # Matching a *Reply* to a *Request* is what distinguishes a
                # real device from a MIDI-Thru loop handing our own bytes
                # back; is_xv2020 then rejects the rest of the XV/JV family,
                # which shares the family code and differs in family number.
                if reply is not None and reply.is_xv2020:
                    return DeviceIdentity(
                        send_port=send_name,
                        recv_port=recv_name,
                        device_id=reply.device,
                        family=reply.family,
                        family_number=reply.family_number,
                        revision=reply.revision,
                    )
            return None
        except (RuntimeError, OSError, SystemError, ValueError):
            return None
        finally:
            if in_port is not None:
                in_port.close_port()
                _delete_quiet(in_port)
            out.close_port()
            _delete_quiet(out)

    # --- transport ----------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for port in (self._in, self._out):
            try:
                port.close_port()
            except (AttributeError, RuntimeError, OSError, ValueError, SystemError):
                pass
            _delete_quiet(getattr(port, "_port", port))

    def _drain(self) -> None:
        """Discard anything queued before a request goes out.

        Without this a reply left over from a previous, timed-out exchange
        is picked up as the answer to the *next* request -- which on a bank
        scan means every name one slot out of step.
        """
        while self._in.get_message() is not None:
            pass

    def _send(self, frame: bytes) -> None:
        self._out.send_message(list(frame))

    def request(
        self, address: Sequence[int], size: int, *, timeout: Optional[float] = None
    ) -> bytes:
        """RQ1 at ``address`` for ``size`` bytes; return the DT1 payload.

        Raises ``TimeoutError`` on silence, which -- per the manual -- means
        either "not there", "wrong device ID", "Rx Exclusive is off" or
        merely "busy". The message says so rather than picking one.
        """
        self._drain()
        self._send(m.rq1(address, m.size_bytes(size), device=self.device_id))
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        wanted = tuple(address)
        while time.time() < deadline:
            message = self._in.get_message()
            if message is None:
                time.sleep(0.002)
                continue
            try:
                packet = m.parse_dt1(message[0])
            except ValueError:
                # A checksum failure on a frame that is otherwise ours is
                # corruption on our own conversation, not foreign traffic.
                raise
            if packet is None:
                continue  # somebody else's message on a shared port
            if packet.address != wanted:
                # A late answer to an earlier request, or a dump the device
                # sent on its own. Skipping on address rather than taking the
                # first DT1 is what keeps a scan aligned.
                continue
            return packet.data
        raise TimeoutError(
            f"no reply to a request at "
            f"{'.'.join(f'{b:02X}' for b in wanted)}. The XV-2020 answers a "
            f"request it cannot serve with silence, so this means one of: "
            f"powered off, wrong port, device ID mismatch (rxved is using "
            f"{m.device_id_display(self.device_id)}), Rx Exclusive off, or "
            f"simply busy."
        )

    # --- the one write ------------------------------------------------------

    #: Performance Part offsets rxved will write, and the range each accepts.
    #:
    #: An allowlist rather than an arbitrary-address poke, and deliberately
    #: so: a DT1 to a mistyped address in a Roland map does not fail, it
    #: writes something else. Every entry here is a Performance **Part**
    #: parameter in the *temporary* area, which is the edit buffer -- power
    #: cycling or loading another performance discards all of it.
    WRITABLE_PART_OFFSETS = {
        0x00: ("receive channel", 0, 15),
        0x01: ("receive switch", 0, 1),
        0x04: ("bank select MSB", 0, 127),
        0x05: ("bank select LSB", 0, 127),
        0x06: ("program change", 0, 127),
        0x07: ("part level", 0, 127),
        0x08: ("part pan", 0, 127),
        0x09: ("part coarse tune", 16, 112),
        0x0A: ("part fine tune", 14, 114),
        0x0B: ("part mono/poly", 0, 2),
        0x0D: ("part pitch bend range", 0, 25),
        0x15: ("part octave shift", 61, 67),
        0x16: ("part velocity sensitivity", 1, 127),
        0x17: ("keyboard range lower", 0, 127),
        0x18: ("keyboard range upper", 0, 127),
        0x1B: ("mute switch", 0, 1),
        0x1C: ("dry send level", 0, 127),
        0x1D: ("chorus send level", 0, 127),
        0x1E: ("reverb send level", 0, 127),
        # 0-13 as the map gives it. Values this model ignores are still
        # accepted: refusing them would make it impossible to *clear* one
        # that arrived in a performance written on a bigger XV.
        0x1F: ("output assign", 0, 13),
        0x20: ("output MFX select", 0, 2),
    }

    #: Performance MIDI offsets rxved will write, per channel, with ranges.
    #: Same temporary area as the parts (``10 00 <10+channel> <offset>``) and
    #: the same argument for why writing them is safe.
    WRITABLE_CHANNEL_OFFSETS = {
        0x00: ("receive program change", 0, 1),
        0x01: ("receive bank select", 0, 1),
        0x02: ("receive bender", 0, 1),
        0x03: ("receive poly pressure", 0, 1),
        0x04: ("receive channel pressure", 0, 1),
        0x05: ("receive modulation", 0, 1),
        0x06: ("receive volume", 0, 1),
        0x07: ("receive pan", 0, 1),
        0x08: ("receive expression", 0, 1),
        0x09: ("receive hold 1", 0, 1),
        0x0A: ("phase lock", 0, 1),
        0x0B: ("velocity curve type", 0, 4),
    }

    # --- whole performances -------------------------------------------------

    def read_performance_blocks(
        self,
        base: Tuple[int, int],
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, bytes]:
        """Every block of one performance. 36 round trips, no sound.

        ``base`` is :data:`TEMPORARY_PERFORMANCE` or the result of
        :func:`user_performance_base`. A short block is an error rather than
        something to pad: a partial read that later gets written back would
        write the wrong thing to the wrong offsets.
        """
        out: Dict[str, bytes] = {}
        total = len(PERFORMANCE_BLOCKS)
        for index, (name, (sub_hi, sub_lo), size) in enumerate(
            PERFORMANCE_BLOCKS, start=1
        ):
            address = (base[0], base[1], sub_hi, sub_lo)
            data = self.request(address, size, timeout=timeout)
            if len(data) != size:
                raise DeviceError(
                    f"block {name} at "
                    f"{'.'.join(f'{b:02X}' for b in address)} returned "
                    f"{len(data)} bytes, expected {size}"
                )
            out[name] = bytes(data)
            if on_progress is not None:
                on_progress(index, total)
        return out

    def write_performance_blocks(
        self,
        base: Tuple[int, int],
        blocks: Dict[str, bytes],
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        verify: bool = True,
        timeout: Optional[float] = None,
    ) -> List[str]:
        """Write a whole performance. Returns the names of blocks that
        did **not** read back identical.

        **This is the one operation in rxved that can destroy something.**
        Written to a user slot it replaces whatever was stored there, and no
        power cycle brings it back. Callers are expected to have taken a
        backup; :meth:`store_temporary_to_slot` does.

        Verification is per block and by comparison, not by trusting the
        send: DT1 is unacknowledged, so the only evidence that a write
        landed is reading the bytes back and finding them equal.
        """
        missing = [
            name for name, _addr, _size in PERFORMANCE_BLOCKS if name not in blocks
        ]
        if missing:
            raise ValueError(
                f"refusing to write a partial performance; missing {', '.join(missing)}"
            )

        mismatched: List[str] = []
        total = len(PERFORMANCE_BLOCKS)
        for index, (name, (sub_hi, sub_lo), size) in enumerate(
            PERFORMANCE_BLOCKS, start=1
        ):
            payload = blocks[name]
            if len(payload) != size:
                raise ValueError(
                    f"block {name} is {len(payload)} bytes, expected {size}"
                )
            address = (base[0], base[1], sub_hi, sub_lo)
            self._send(m.dt1(address, payload, device=self.device_id))
            time.sleep(SEND_GAP)
            if verify:
                back = self.request(address, size, timeout=timeout)
                if bytes(back) != payload:
                    mismatched.append(name)
            if on_progress is not None:
                on_progress(index, total)
        return mismatched

    def store_temporary_to_slot(
        self,
        slot: int,
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[Dict[str, bytes], List[str]]:
        """Save the edit buffer into User Performance ``slot``.

        Returns ``(backup, mismatched)`` -- the destination's **previous**
        contents, read before anything is written so the slot can be put
        back, and the names of any blocks that did not verify.

        The backup is taken first and unconditionally. If reading the
        destination fails, nothing is written: a store that cannot be undone
        is not one this program performs.
        """
        base = user_performance_base(slot)
        backup = self.read_performance_blocks(base, timeout=timeout)
        blocks = self.read_performance_blocks(TEMPORARY_PERFORMANCE, timeout=timeout)
        mismatched = self.write_performance_blocks(
            base, blocks, on_progress=on_progress, timeout=timeout
        )
        return backup, mismatched

    def write_channel_param(
        self,
        channel: int,
        offset: int,
        value: int,
        *,
        verify: bool = True,
        timeout: Optional[float] = None,
    ) -> int:
        """Set one Performance MIDI parameter for a channel.

        ``channel`` is the 0-based wire channel. As with
        :meth:`write_part_param` this goes to the temporary performance and
        returns the value read back, not the value sent.
        """
        if not 0 <= channel <= 15:
            raise ValueError(f"channel {channel} is outside 0-15")
        if offset not in self.WRITABLE_CHANNEL_OFFSETS:
            raise ValueError(
                f"offset {offset:#04x} is not one of the Performance MIDI "
                f"parameters rxved writes"
            )
        label, low, high = self.WRITABLE_CHANNEL_OFFSETS[offset]
        if not low <= value <= high:
            raise ValueError(f"{label} takes {low}-{high}, got {value}")

        address = (0x10, 0x00, 0x10 + channel, offset)
        self._send(m.dt1(address, [value], device=self.device_id))
        time.sleep(SEND_GAP)
        if not verify:
            return value
        data = self.request(address, 1, timeout=timeout)
        if not data:
            raise DeviceError(
                f"wrote {label} on channel {channel + 1} but read back nothing"
            )
        return data[0]

    def write_part_param(
        self,
        part: int,
        offset: int,
        value: int,
        *,
        verify: bool = True,
        timeout: Optional[float] = None,
    ) -> int:
        """Set one Performance Part parameter in the **temporary** area.

        This is the only write in rxved, and what makes it acceptable is
        where it goes: ``10 00 <20+part-1> <offset>`` is Temporary
        Performance -- the edit buffer the module is playing from right now,
        not a stored performance. Power-cycling the XV-2020, or loading any
        performance, discards every byte written here. rxved does not perform
        the Write (store) operation and has no code that could.

        Returns the value **read back from the device**, not the value sent.
        A DT1 is fire-and-forget: the XV-2020 does not acknowledge it, and a
        parameter it declines to change -- or one written while the mode
        makes it meaningless -- produces exactly the same silence as success.
        Reporting the sent value as fact would be a guess dressed as a
        confirmation, so the caller gets what the synth says it now holds and
        can tell the user when the two differ.
        """
        if not 1 <= part <= 16:
            raise ValueError(f"part {part} is outside 1-16")
        if offset not in self.WRITABLE_PART_OFFSETS:
            raise ValueError(
                f"offset {offset:#04x} is not one of the Performance Part "
                f"parameters rxved writes "
                f"({', '.join(f'{o:#04x}' for o in self.WRITABLE_PART_OFFSETS)})"
            )
        label, low, high = self.WRITABLE_PART_OFFSETS[offset]
        if not low <= value <= high:
            raise ValueError(f"{label} takes {low}-{high}, got {value}")

        address = (0x10, 0x00, 0x20 + part - 1, offset)
        self._send(m.dt1(address, [value], device=self.device_id))
        time.sleep(SEND_GAP)
        if not verify:
            return value
        # One byte back from the same address. Cheap, and the only way to
        # know anything happened at all.
        data = self.request(address, 1, timeout=timeout)
        if not data:
            raise DeviceError(f"wrote {label} on part {part} but read back nothing")
        return data[0]

    # --- operations ---------------------------------------------------------

    def identify(self, *, timeout: Optional[float] = None) -> Optional[DeviceIdentity]:
        """Ask again who is on the other end, on the already-open ports."""
        self._drain()
        self._send(m.IDENTITY_REQUEST)
        deadline = time.time() + (AUTODETECT_TIMEOUT if timeout is None else timeout)
        while time.time() < deadline:
            message = self._in.get_message()
            if message is None:
                time.sleep(0.002)
                continue
            reply = m.parse_identity_reply(message[0])
            if reply is not None and reply.is_xv2020:
                return DeviceIdentity(
                    send_port=self.description,
                    recv_port=self.description,
                    device_id=reply.device,
                    family=reply.family,
                    family_number=reply.family_number,
                    revision=reply.revision,
                )
        return None

    def is_connected(self, *, timeout: float = AUTODETECT_TIMEOUT) -> bool:
        return self.identify(timeout=timeout) is not None

    def system_channels(self, *, timeout: Optional[float] = None) -> "SystemChannels":
        """Which MIDI channels this synth actually listens on.

        Read from System Common (OM p. 147), and worth reading rather than
        assuming, because **patches and performances arrive on two different
        channels** and only one of them is the obvious one:

        * ``00 0B`` Patch Receive Channel, raw 0-15 for channels 1-16.
        * ``00 09`` Performance Control Channel, raw 0-15 for channels 1-16
          with **16 meaning OFF** -- the map gives the range as 0-16 and
          spells the values "1 - 16, OFF" (p. 94). A factory reset sets it to
          16, i.e. *channel* 16, which is raw 15.

        A Bank Select and Program Change aimed at a performance on the patch
        channel does not fail. It is simply ignored, and the synth carries on
        playing what it was playing -- which looks exactly like rxved having
        sent nothing at all. Reading these two bytes costs one round trip,
        makes no sound, and removes the guess.
        """
        # One request for offsets 09, 0A, 0B: three bytes, one round trip.
        data = self.request((0x02, 0x00, 0x00, 0x09), 3, timeout=timeout)
        if len(data) < 3:
            raise DeviceError(
                f"System Common read returned {len(data)} bytes, expected 3"
            )
        raw_performance = data[0]
        raw_patch = data[2]  # 0x0A between them is (reserved)
        return SystemChannels(
            patch_receive=raw_patch,
            performance_control=(
                None if raw_performance >= PERFORMANCE_CHANNEL_OFF else raw_performance
            ),
        )

    def read_setup(self, *, timeout: Optional[float] = None) -> SetupState:
        """Read the Setup block: sound mode, and what is selected now.

        One round trip, 15 bytes, no sound. This is the read-back for
        everything the browser sends -- the synth reports the Bank Select
        MSB/LSB and Program Number it is currently on, in exactly the terms
        rxved displays them.
        """
        data = self.request((0x01, 0x00, 0x00, 0x00), 0x0F, timeout=timeout)
        if len(data) < 10:
            raise DeviceError(f"Setup read returned {len(data)} bytes, expected 15")
        return SetupState(
            mode=data[0],
            performance_msb=data[4],
            performance_lsb=data[5],
            performance_program=data[6],
            patch_msb=data[7],
            patch_lsb=data[8],
            patch_program=data[9],
        )

    def read_part(self, part: int, *, timeout: Optional[float] = None) -> PartState:
        """Read one Performance Part's channel and patch selection.

        ``10 00 <20+part-1> 00``: the Performance Part blocks sit at
        Performance offsets ``00 20 00`` (Part 1) through ``00 2F 00``
        (Part 16), one step of the third address byte each.
        """
        if not 1 <= part <= 16:
            raise ValueError(f"part {part} is outside 1-16")
        # 0x21 bytes: through Part Output MFX Select at offset 00 20. One
        # round trip for the MIDI settings and the effects routing together,
        # rather than two reads of the same block.
        data = self.request((0x10, 0x00, 0x20 + part - 1, 0x00), 0x21, timeout=timeout)
        if len(data) < 0x21:
            raise DeviceError(
                f"part {part} read returned {len(data)} bytes, expected 33"
            )
        return PartState(
            part=part,
            receive_channel=data[0],
            receive_switch=bool(data[1]),
            msb=data[4],
            lsb=data[5],
            program_change=data[6],
            level=data[7],
            pan=data[0x08],
            coarse=data[0x09],
            fine=data[0x0A],
            mono_poly=data[0x0B],
            bend_range=data[0x0D],
            octave=data[0x15],
            velocity_sens=data[0x16],
            key_lower=data[0x17],
            key_upper=data[0x18],
            mute=bool(data[0x1B]),
            dry=data[0x1C],
            chorus=data[0x1D],
            reverb=data[0x1E],
            output_assign=data[0x1F],
            output_mfx=data[0x20],
        )

    def read_performance_common(
        self, *, timeout: Optional[float] = None
    ) -> PerformanceCommon:
        """Temporary Performance Common: its name, and Solo Part Select.

        ``10 00 00 00``, 13 bytes -- the 12-byte name plus offset ``00 0C``.
        One round trip, no sound.
        """
        data = self.request((0x10, 0x00, 0x00, 0x00), 0x0D, timeout=timeout)
        if len(data) < 13:
            raise DeviceError(
                f"Performance Common read returned {len(data)} bytes, expected 13"
            )
        name = "".join(
            chr(byte) if 32 <= byte <= 126 else " " for byte in data[:12]
        ).rstrip()
        solo = data[12]
        return PerformanceCommon(name=name, solo=None if solo == 0 else solo)

    def read_performance_midi(
        self, channel: int, *, timeout: Optional[float] = None
    ) -> "ChannelMidi":
        """One channel's Performance MIDI block. ``10 00 <10+channel> 00``."""
        if not 0 <= channel <= 15:
            raise ValueError(f"channel {channel} is outside 0-15")
        data = self.request((0x10, 0x00, 0x10 + channel, 0x00), 0x0C, timeout=timeout)
        if len(data) < 0x0C:
            raise DeviceError(
                f"channel {channel + 1} MIDI read returned {len(data)} "
                f"bytes, expected 12"
            )
        return ChannelMidi(
            channel=channel,
            program_change=bool(data[0]),
            bank_select=bool(data[1]),
            bender=bool(data[2]),
            poly_pressure=bool(data[3]),
            channel_pressure=bool(data[4]),
            modulation=bool(data[5]),
            volume=bool(data[6]),
            pan=bool(data[7]),
            expression=bool(data[8]),
            hold_1=bool(data[9]),
            phase_lock=bool(data[0x0A]),
            velocity_curve=data[0x0B],
        )

    def read_channel_midi(
        self,
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        timeout: Optional[float] = None,
    ) -> Tuple["ChannelMidi", ...]:
        """All 16 Performance MIDI blocks. Sixteen round trips, no sound."""
        out = []
        for channel in range(16):
            out.append(self.read_performance_midi(channel, timeout=timeout))
            if on_progress is not None:
                on_progress(channel + 1, 16)
        return tuple(out)

    def read_performance_fx(
        self, *, timeout: Optional[float] = None
    ) -> "PerformanceFx":
        """The performance's three effects blocks. Three round trips, silent.

        ``10 00 02 00`` MFX, ``10 00 04 00`` Chorus, ``10 00 06 00`` Reverb
        (OM p. 146). Only the head of each -- type, level and routing. The
        dozens of per-algorithm parameters that follow mean nothing without
        knowing the algorithm, and rxved does not edit effects.
        """
        mfx = self.request((0x10, 0x00, 0x02, 0x00), 5, timeout=timeout)
        chorus = self.request((0x10, 0x00, 0x04, 0x00), 4, timeout=timeout)
        reverb = self.request((0x10, 0x00, 0x06, 0x00), 3, timeout=timeout)
        for name, data, want in (
            ("MFX", mfx, 5),
            ("chorus", chorus, 4),
            ("reverb", reverb, 3),
        ):
            if len(data) < want:
                raise DeviceError(
                    f"{name} read returned {len(data)} bytes, expected {want}"
                )
        return PerformanceFx(
            mfx_type=mfx[0],
            mfx_dry=mfx[1],
            mfx_chorus=mfx[2],
            mfx_reverb=mfx[3],
            mfx_output=mfx[4],
            chorus_type=chorus[0],
            chorus_level=chorus[1],
            chorus_output=chorus[2],
            chorus_output_select=chorus[3],
            reverb_type=reverb[0],
            reverb_level=reverb[1],
            reverb_output=reverb[2],
        )

    def read_parts(
        self,
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[PartState, ...]:
        """All 16 Performance Parts. Sixteen round trips, no sound."""
        out = []
        for part in range(1, 17):
            out.append(self.read_part(part, timeout=timeout))
            if on_progress is not None:
                on_progress(part, 16)
        return tuple(out)

    def read_state(
        self,
        *,
        with_parts: Optional[bool] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        timeout: Optional[float] = None,
    ) -> DeviceState:
        """Everything needed to say what each MIDI channel currently does.

        ``with_parts`` defaults to "only if the mode makes them matter" --
        sixteen extra round trips are worth skipping when the synth is in
        Patch mode and only one channel selects anything.
        """
        setup = self.read_setup(timeout=timeout)
        channels = self.system_channels(timeout=timeout)
        self.channels = channels
        want_parts = setup.multitimbral if with_parts is None else with_parts
        parts = (
            self.read_parts(on_progress=on_progress, timeout=timeout)
            if want_parts
            else ()
        )
        # Solo Part Select lives here, and it silences fifteen parts without
        # touching any of them -- so reading the parts without it can produce
        # a screen on which everything looks fine and the synth is quiet.
        common = self.read_performance_common(timeout=timeout) if want_parts else None
        fx = self.read_performance_fx(timeout=timeout) if want_parts else None
        # The two bytes that decide whether anything rxved sends is even
        # looked at. Worth sixteen round trips on a screen whose job is to
        # explain why something did not happen.
        midi = self.read_channel_midi(timeout=timeout) if want_parts else ()
        state = DeviceState(
            setup=setup, channels=channels, parts=parts, common=common, fx=fx, midi=midi
        )
        self.state = state
        return state

    def refresh_channel(
        self, channel: int, *, timeout: Optional[float] = None
    ) -> DeviceState:
        """Re-read just enough to say what is on one channel. Cheap, silent.

        The full :meth:`read_state` costs eighteen round trips in a
        multitimbral mode, which is too much to spend every time the user
        nudges the channel selector. This re-reads the Setup block always
        (one trip, and it is what changes when a patch is selected) and, in
        a multitimbral mode, only the parts already known to listen on this
        channel.

        The cached part list is used to decide *which* parts to re-read, not
        for their contents. That is a deliberate trade: a part's receive
        channel changes only when a different performance is loaded, and
        :meth:`read_state` or the browser's refresh key covers that, whereas
        a part's *patch* changes every time anybody sends a program change.
        """
        setup = self.read_setup(timeout=timeout)
        previous = self.state
        channels = (
            previous.channels
            if previous is not None
            else self.system_channels(timeout=timeout)
        )
        parts: Tuple[PartState, ...] = previous.parts if previous is not None else ()
        if setup.multitimbral:
            if not parts:
                parts = self.read_parts(timeout=timeout)
            else:
                fresh = {
                    p.part: self.read_part(p.part, timeout=timeout)
                    for p in parts
                    if p.receive_channel == channel
                }
                parts = tuple(fresh.get(p.part, p) for p in parts)
        # Carried over, not re-read: Solo Part Select changes when a
        # performance is loaded, which is what the refresh key is for. Losing
        # it here would quietly turn "part 3 is soloed" into "nothing is
        # soloed" on the next cursor move, which is worse than stale.
        common = previous.common if previous is not None else None
        fx = previous.fx if previous is not None else None
        midi = previous.midi if previous is not None else ()
        state = DeviceState(
            setup=setup, channels=channels, parts=parts, common=common, fx=fx, midi=midi
        )
        self.state = state
        self.channels = channels
        return state

    def user_patch_name(self, number: int, *, timeout: Optional[float] = None) -> str:
        """The name stored in User Patch ``number`` (1-128), read live."""
        data = self.request(
            m.user_patch_address(number), m.PATCH_NAME_LEN, timeout=timeout
        )
        return m.decode_name(data[: m.PATCH_NAME_LEN])

    def user_performance_name(
        self, number: int, *, timeout: Optional[float] = None
    ) -> str:
        """The name stored in User Performance ``number`` (1-64)."""
        data = self.request(
            m.user_performance_address(number),
            m.PERFORMANCE_NAME_LEN,
            timeout=timeout,
        )
        return m.decode_name(data[: m.PERFORMANCE_NAME_LEN])

    def user_rhythm_name(self, number: int, *, timeout: Optional[float] = None) -> str:
        """The name stored in User Rhythm ``number`` (1-4)."""
        data = self.request(
            m.address_add(m.user_rhythm_address(number), m.OFF_RHYTHM_COMMON),
            m.RHYTHM_NAME_LEN,
            timeout=timeout,
        )
        return m.decode_name(data[: m.RHYTHM_NAME_LEN])

    def temporary_patch_name(self, *, timeout: Optional[float] = None) -> str:
        """The name of the patch the machine is playing right now."""
        data = self.request(
            m.temporary_patch_address(), m.PATCH_NAME_LEN, timeout=timeout
        )
        return m.decode_name(data[: m.PATCH_NAME_LEN])

    def read_user_bank(
        self,
        bank_id: str,
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[int, str]:
        """Read every name in one of the three writable banks.

        Only USER, R-USER and P-USER can be read this way -- they are the
        only banks with addresses in the map. This is a genuinely read-only
        operation: nothing is selected and the instrument keeps playing
        whatever it was playing.
        """
        readers = {
            "USER": self.user_patch_name,
            "P-USER": self.user_performance_name,
            "R-USER": self.user_rhythm_name,
        }
        if bank_id not in readers:
            raise LookupError(
                f"{bank_id} has no address in the XV-2020's parameter map, "
                f"so it cannot be read directly; only "
                f"{', '.join(sorted(readers))} can. Preset banks are ROM and "
                f"must be scanned by selection instead -- see scan_bank()."
            )
        read = readers[bank_id]
        entries = banks.slots(bank_id)
        out: Dict[int, str] = {}
        for index, entry in enumerate(entries):
            out[entry.number] = read(entry.number, timeout=timeout)
            if on_progress is not None:
                on_progress(index + 1, len(entries))
        return out

    # --- the two operations that make a sound -------------------------------

    def select(self, entry: banks.Slot, *, channel: Optional[int] = None) -> None:
        """Select a slot on the instrument. **This changes what it plays.**

        Bank Select MSB, Bank Select LSB, then Program Change, in that order
        -- the device latches the bank on the program change, so a PC that
        arrives before the LSB selects from the previously latched bank.

        The channel is chosen for you unless you pass one, because **it is
        not one channel**: patches and rhythm sets arrive on the Patch
        Receive Channel and performances on the Performance Control Channel,
        and both are settings on the synth. :meth:`system_channels` reads
        them; :meth:`use_system_channels` caches the answer. Until then this
        falls back to ``self.channel``, which is a guess and will be wrong
        for performances on most machines -- a factory reset puts the
        performance channel on 16 and the patch channel on 1.

        Sending a performance select to the patch channel does not fail. It
        is ignored, and the synth keeps playing what it was playing, which
        is indistinguishable from rxved having sent nothing.
        """
        if channel is None:
            channel = self.channel_for(entry.kind)
        if channel is None:
            raise DeviceError(
                f"{entry} cannot be selected over MIDI: this synth has its "
                f"Performance Control Channel set to OFF (SYSTEM/MIDI on the "
                f"front panel), so it ignores performance Bank Select and "
                f"Program Change entirely."
            )
        for message in entry.select_messages(channel):
            self._out.send_message(message)

    def select_raw(
        self, msb: int, lsb: int, program_change: int, *, channel: Optional[int] = None
    ) -> None:
        """Send a Bank Select / Program Change triple by its raw numbers.

        Needed because a *restore* must be able to put the synth back on a
        triple rxved's bank table does not claim -- an SRX board it has no
        row for, say. Restoring only the triples we happen to recognise
        would be worse than not restoring at all, since it would silently
        move the synth somewhere else.
        """
        target = self.channel if channel is None else channel
        for value, name in (
            (msb, "MSB"),
            (lsb, "LSB"),
            (program_change, "program change"),
        ):
            if not 0 <= value <= 127:
                raise ValueError(f"{name} {value} is outside 0-127")
        self._out.send_message([0xB0 | target, 0, msb])
        self._out.send_message([0xB0 | target, 32, lsb])
        self._out.send_message([0xC0 | target, program_change])

    def channel_for(self, kind: str) -> Optional[int]:
        """The channel a slot of this kind should be selected on.

        Uses the synth's own setting once :meth:`use_system_channels` has
        read it, and otherwise falls back to the configured channel.
        """
        if self.channels is not None:
            return self.channels.for_kind(kind)
        return self.channel

    def use_system_channels(
        self, *, timeout: Optional[float] = None
    ) -> "SystemChannels":
        """Read the synth's receive channels and use them from now on.

        Free and silent -- one SysEx round trip, nothing selected -- so it is
        worth doing at startup rather than guessing. Cached in
        :attr:`channels`.
        """
        self.channels = self.system_channels(timeout=timeout)
        return self.channels

    def probe_srx(
        self,
        *,
        lsb_range: Iterable[int] = range(0, 64),
        settle: float = SELECT_GAP,
        restore: bool = True,
        on_progress: Optional[Callable[[int, Optional[str]], None]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[int, str]:
        """Find which SRX Bank Select LSBs the fitted card actually answers on.

        **This plays the instrument**, for the same reason :meth:`scan_bank`
        does. It selects patch 1 at each candidate LSB under the SRX patch
        MSB and reads the temporary area back.

        Why this exists rather than a lookup table: the LSB base is assigned
        per card, is not derivable from the card number (SRX-07 is 11,
        SRX-08 is 15, and extrapolating backwards puts the early cards at
        negative LSBs), and rxved only has the allocation for cards whose
        manual has been read. Probing finds any card, including ones this
        project has never heard of.

        Returns ``{lsb: name of patch 1}`` for each LSB that produced a
        distinct name.

        **An LSB missing from the result is not proof the card lacks it.**
        The device answers an unsupported Bank Select by staying where it
        was, so "no change" is the only available signal, and a card whose
        patch 1 on two pages happens to share a name would read as one page.
        Treat the output as a strong hint to confirm against the card's
        documentation, not as a survey.
        """
        found: Dict[int, str] = {}
        previous: Optional[str] = None
        before = self._remember_selection() if restore else None
        for lsb in lsb_range:
            self._out.send_message([0xB0 | self.channel, 0, banks.MSB_SRX_PATCH])
            self._out.send_message([0xB0 | self.channel, 32, lsb])
            self._out.send_message([0xC0 | self.channel, 0])
            time.sleep(settle)
            try:
                name = self.temporary_patch_name(timeout=timeout)
            except TimeoutError:
                if on_progress is not None:
                    on_progress(lsb, None)
                continue
            if name and name != previous:
                found[lsb] = name
                previous = name
            if on_progress is not None:
                on_progress(lsb, found.get(lsb))
        self._restore_selection(before)
        return found

    def scan_bank(
        self,
        bank_id: str,
        *,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        settle: float = SELECT_GAP,
        restore: bool = True,
        timeout: Optional[float] = None,
    ) -> Dict[int, str]:
        """Learn a whole bank's names by selecting each slot and reading back.

        **This plays the instrument.** It is the only way to get preset
        names off the hardware -- the preset banks are ROM and have no
        addresses in the parameter map -- and it leaves the XV-2020 sitting
        on the last slot scanned. Never called implicitly; the browser asks
        first.

        The re-read loop is the part that matters. A program change takes
        time to load, and reading the temporary area too early returns the
        *previous* patch: not an error, just every name attached to the
        wrong number, which is both silent and plausible. So a name equal to
        the previous slot's is retried rather than accepted. That is
        imperfect -- two adjacent slots genuinely sharing a name will retry
        and then, correctly, keep the duplicate -- but it fails towards
        slowness rather than towards quiet corruption.

        **Unless the instrument never moved at all**, which is a different
        failure with the same disguise: the retry loop gives up, accepts the
        unchanged name, and reports it for all 128 slots. That is not a slow
        load, it is a synth that is not following the program change -- in
        Performance mode the patch receive channel does not move the current
        patch, and a Bank Select receive switch switched off drops the bank
        bytes while the program change still lands. Both were observed on
        real hardware, and both produce a screen of one wrong name repeated
        128 times, which looks exactly like a bank of identically-named
        patches. :meth:`scan_bank` therefore refuses rather than reports it;
        see :func:`_stuck_after`.
        """
        entries = banks.slots(bank_id)
        out: Dict[int, str] = {}
        previous: Optional[str] = None
        stuck = 0
        before = self._remember_selection() if restore else None
        try:
            for index, entry in enumerate(entries):
                self.select(entry)
                time.sleep(settle)
                name = self.temporary_patch_name(timeout=timeout)
                attempts = 0
                while name == previous and attempts < 3:
                    time.sleep(settle)
                    name = self.temporary_patch_name(timeout=timeout)
                    attempts += 1
                if name == previous:
                    stuck += 1
                else:
                    stuck = 0
                out[entry.number] = name
                previous = name
                if on_progress is not None:
                    on_progress(index + 1, len(entries), name)
                # Bail out early rather than play the same wrong note 128
                # times: the answer cannot change once the synth has
                # declined to move three times running.
                if stuck >= STUCK_SLOT_LIMIT:
                    raise DeviceError(
                        f"the synth did not change patch: slots 1-{stuck + 1} of "
                        f"{bank_id} all read {name!r}. A program change is "
                        f"either being ignored or is not moving the patch "
                        f"that is read back.\n"
                        f"Most likely the synth is in PERFORMANCE mode, "
                        f"where the patch receive channel does not select the "
                        f"current patch -- switch it to PATCH mode (SYSTEM/"
                        f"MIDI) and try again. Otherwise check the Bank "
                        f"Select receive switch for the send channel in `m` "
                        f"(multi-mode setup): with rxBS off the bank bytes "
                        f"are dropped and the program change still lands."
                    )
        finally:
            self._restore_selection(before)
        return out

    def _remember_selection(self) -> Optional[Tuple[int, int, int]]:
        """The patch triple to put back after a sweep, if it can be read."""
        try:
            setup = self.read_setup()
        except (
            TimeoutError,
            ValueError,
            LookupError,
            RuntimeError,
            OSError,
            SystemError,
        ):
            # Not worth failing a scan over; the caller is told the synth is
            # left on the last slot either way.
            return None
        return (setup.patch_msb, setup.patch_lsb, setup.patch_program)

    def _restore_selection(self, before: Optional[Tuple[int, int, int]]) -> None:
        """Put the synth back where the sweep found it."""
        if before is None:
            return
        try:
            self.select_raw(*before, channel=self.channel_for(banks.Kind.PATCH))
        except (ValueError, LookupError, RuntimeError, OSError, SystemError):
            pass
