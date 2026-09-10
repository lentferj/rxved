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
guessing. :meth:`XvBridge.autodetect` uses it.

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

#: Local settings, gitignored. Alongside the sibling projects' convention.
DEFAULT_CONFIG_PATH = "config.toml"

#: Substrings that mark a port as probably belonging to an XV-2020. Used to
#: order the autodetect sweep, never to decide the answer -- the Identity
#: Reply decides that. A USB-connected XV-2020 enumerates as "Roland
#: XV-2020"; one on a 5-pin DIN cable is behind an interface whose name says
#: nothing at all, so name matching can only ever be a hint.
_XV_PORT_HINTS = ("xv-2020", "xv2020", "xv 2020")


class MidiUnavailable(RuntimeError):
    """No MIDI backend on this host at all."""


class DeviceNotFound(RuntimeError):
    """Nothing answered an Identity Request on any port tried."""


class AmbiguousDevice(RuntimeError):
    """More than one XV-2020 answered, and none was pinned in the config."""

    def __init__(self, found):
        self.found = list(found)
        detail = "; ".join(
            f"{ident.send_port} -> {ident.recv_port} "
            f"(device ID {ident.device_display})"
            for ident in self.found
        )
        super().__init__(
            f"{len(self.found)} XV-2020s answered: {detail}. "
            f"Pass --port to choose one, or pin send_port/recv_port in "
            f"{DEFAULT_CONFIG_PATH}."
        )


#: Set by the first save that had to leave an unreadable config alone.
_warned_unreadable = False


# --- config.toml ------------------------------------------------------------
# Read-modify-write, never a blind overwrite, so unrelated keys survive each
# other's saves. Ported from s3ked, including the reason it is careful:
# collapsing "missing" and "unparseable" turns the next save into a blind
# overwrite of a file this code never understood, so one stray bracket costs
# the user every other setting in it, silently.


def _read_config(path: str) -> Tuple[dict, str]:
    """``(settings, status)`` where status is ok / missing / unreadable."""
    import os
    import tomllib

    if not os.path.exists(path):
        return {}, "missing"
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return {}, "unreadable"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # written by a build running under a non-UTF-8 locale; decode
        # leniently so hand-edited keys survive, and let the next save repair
        text = raw.decode("cp1252", errors="replace")
    try:
        return tomllib.loads(text), "ok"
    except Exception:
        return {}, "unreadable"


def _read_config_dict(path: str) -> dict:
    return _read_config(path)[0]


def _update_config(path: str, **changes) -> None:
    global _warned_unreadable

    data, status = _read_config(path)
    if status == "unreadable":
        if not _warned_unreadable:
            _warned_unreadable = True
            print(f"rxved: {path} could not be parsed, so settings are not "
                  f"being saved. Fix or delete it; nothing has been "
                  f"overwritten.", file=sys.stderr)
        return
    data.update(changes)
    _write_config_dict(data, path)


def _write_config_dict(data: dict, path: str) -> None:
    lines = ["# rxved local config - gitignored, safe to delete."]
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    try:
        # encoding= is not optional: without it Python uses the locale codec,
        # and TOML is UTF-8 by spec. Both ends must say so.
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass  # the cache is a convenience, not required for correctness


def load_last_ports(path: str = DEFAULT_CONFIG_PATH
                    ) -> Optional[Tuple[str, str]]:
    """The send/receive pair that answered last time, if any."""
    data = _read_config_dict(path)
    send_port = data.get("send_port")
    recv_port = data.get("recv_port")
    if isinstance(send_port, str) and isinstance(recv_port, str):
        return send_port, recv_port
    return None


def save_last_ports(send_port: str, recv_port: str,
                    path: str = DEFAULT_CONFIG_PATH) -> None:
    _update_config(path, send_port=send_port, recv_port=recv_port)


def load_device_id(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    value = _read_config_dict(path).get("device_id")
    return value if isinstance(value, int) else None


def save_device_id(device_id: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    _update_config(path, device_id=int(device_id))


def load_channel(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    value = _read_config_dict(path).get("channel")
    return value if isinstance(value, int) else None


def save_channel(channel: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    _update_config(path, channel=int(channel))


# --- MIDI port enumeration --------------------------------------------------
# python-rtmidi's close_port() does not tear down the backend ALSA sequencer
# client; only delete() does. Every transient MidiIn/MidiOut -- even one built
# solely to call get_ports() -- would otherwise orphan a client until process
# exit, and on a host with many MIDI ports a single autodetect sweep could
# exhaust the sequencer's client slots. This host has over thirty.


def _delete_quiet(port) -> None:
    try:
        port.delete()
    except Exception:
        pass


def _probe(factory, what: str):
    """Construct a transient rtmidi probe, or say why we couldn't.

    rtmidi raises out of the *constructor* when the backend is missing, so
    this cannot be handled at the get_ports() call.
    """
    try:
        return factory()
    except Exception as exc:  # rtmidi raises SystemError/RuntimeError here
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
        name for name in bidirectional_ports()
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
                (port_name == name) if exact
                else (name.lower() in port_name.lower())
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
    for number in (getattr(signal, name, None)
                   for name in ("SIGTERM", "SIGHUP"))
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
    for number in (CLEAN_EXIT_SIGNALS if signals is None else signals):
        try:
            existing = signal.getsignal(number)
        except (ValueError, OSError):        # not available on this platform
            continue
        if existing not in (signal.SIG_DFL, None):
            continue                          # somebody else owns it
        try:
            signal.signal(
                number,
                lambda signum, _frame: (_ for _ in ()).throw(
                    SystemExit(128 + signum)),
            )
        except (ValueError, OSError):
            # signal() only works on the main thread of the main interpreter
            continue


# --- the bridge -------------------------------------------------------------


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

    def __init__(self, send_port, recv_port, *,
                 device_id: int = m.DEFAULT_DEVICE_ID,
                 channel: int = 0, timeout: float = DEFAULT_TIMEOUT,
                 description: str = "",
                 identity: Optional[DeviceIdentity] = None):
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
        if not (0x00 <= int(device_id) <= 0x1F
                or int(device_id) == m.BROADCAST_DEVICE):
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
        self._closed = False

    # --- construction -------------------------------------------------------

    @classmethod
    def standard(cls, port_name: str, *, recv_port_name: Optional[str] = None,
                 device_id: int = 17, channel: int = 0,
                 timeout: float = DEFAULT_TIMEOUT) -> "XvBridge":
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
        except Exception:
            _delete_quiet(out._port)
            raise
        return cls(
            out, in_port, device_id=m.device_id_byte(device_id),
            channel=channel, timeout=timeout,
            description=(
                port_name if recv_name == port_name
                else f"{port_name} -> {recv_name}"
            ),
        )

    @classmethod
    def autodetect(cls, *, config_path: str = DEFAULT_CONFIG_PATH,
                   channel: Optional[int] = None,
                   timeout: float = DEFAULT_TIMEOUT,
                   probe_timeout: float = AUTODETECT_TIMEOUT,
                   on_try: Optional[Callable[[str], None]] = None
                   ) -> "XvBridge":
        """Find an XV-2020 by broadcasting an Identity Request.

        Ports are tried in the order most likely to pay off: the pair
        remembered from last time first, then ports whose name mentions an
        XV-2020, then everything else. The remembered pair turns the common
        case into one round trip rather than a sweep of thirty ports.
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
        bridge = cls.standard(
            identity.send_port, recv_port_name=identity.recv_port,
            device_id=identity.device_id,
            channel=channel if channel is not None else 0,
            timeout=timeout,
        )
        bridge.identity = identity
        save_last_ports(identity.send_port, identity.recv_port, config_path)
        # Stored as the panel number, since that is what the user reads off
        # the machine and types back as --device-id.
        save_device_id(m.device_id_display(identity.device_id), config_path)
        return bridge

    @classmethod
    def _sweep(cls, config_path: str, probe_timeout: float,
               on_try: Optional[Callable[[str], None]]) -> List[DeviceIdentity]:
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
    def _try_pair(send_name: str, recv_name: str,
                  probe_timeout: float) -> Optional[DeviceIdentity]:
        """One Identity Request on one port pair.

        Every failure is swallowed and reported as "nothing here". During a
        sweep that is right: ports get taken by other applications, vanish
        between enumeration and open, or belong to hardware that dislikes
        being written to, and none of that should stop the search.
        """
        try:
            out = _open_out(send_name)
        except Exception:
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
                        send_port=send_name, recv_port=recv_name,
                        device_id=reply.device, family=reply.family,
                        family_number=reply.family_number,
                        revision=reply.revision,
                    )
            return None
        except Exception:
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
            except Exception:
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

    def request(self, address: Sequence[int], size: int, *,
                timeout: Optional[float] = None) -> bytes:
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
                continue        # somebody else's message on a shared port
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

    # --- operations ---------------------------------------------------------

    def identify(self, *, timeout: Optional[float] = None
                 ) -> Optional[DeviceIdentity]:
        """Ask again who is on the other end, on the already-open ports."""
        self._drain()
        self._send(m.IDENTITY_REQUEST)
        deadline = time.time() + (
            AUTODETECT_TIMEOUT if timeout is None else timeout)
        while time.time() < deadline:
            message = self._in.get_message()
            if message is None:
                time.sleep(0.002)
                continue
            reply = m.parse_identity_reply(message[0])
            if reply is not None and reply.is_xv2020:
                return DeviceIdentity(
                    send_port=self.description, recv_port=self.description,
                    device_id=reply.device, family=reply.family,
                    family_number=reply.family_number,
                    revision=reply.revision,
                )
        return None

    def is_connected(self, *, timeout: float = AUTODETECT_TIMEOUT) -> bool:
        return self.identify(timeout=timeout) is not None

    def user_patch_name(self, number: int, *,
                        timeout: Optional[float] = None) -> str:
        """The name stored in User Patch ``number`` (1-128), read live."""
        data = self.request(
            m.user_patch_address(number), m.PATCH_NAME_LEN, timeout=timeout
        )
        return m.decode_name(data[:m.PATCH_NAME_LEN])

    def user_performance_name(self, number: int, *,
                              timeout: Optional[float] = None) -> str:
        """The name stored in User Performance ``number`` (1-64)."""
        data = self.request(
            m.user_performance_address(number), m.PERFORMANCE_NAME_LEN,
            timeout=timeout,
        )
        return m.decode_name(data[:m.PERFORMANCE_NAME_LEN])

    def user_rhythm_name(self, number: int, *,
                         timeout: Optional[float] = None) -> str:
        """The name stored in User Rhythm ``number`` (1-4)."""
        data = self.request(
            m.address_add(m.user_rhythm_address(number), m.OFF_RHYTHM_COMMON),
            m.RHYTHM_NAME_LEN, timeout=timeout,
        )
        return m.decode_name(data[:m.RHYTHM_NAME_LEN])

    def temporary_patch_name(self, *, timeout: Optional[float] = None) -> str:
        """The name of the patch the machine is playing right now."""
        data = self.request(
            m.temporary_patch_address(), m.PATCH_NAME_LEN, timeout=timeout
        )
        return m.decode_name(data[:m.PATCH_NAME_LEN])

    def read_user_bank(self, bank_id: str, *,
                       on_progress: Optional[Callable[[int, int], None]] = None,
                       timeout: Optional[float] = None) -> Dict[int, str]:
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

    def select(self, entry: banks.Slot, *, channel: Optional[int] = None
               ) -> None:
        """Select a slot on the instrument. **This changes what it plays.**

        Bank Select MSB, Bank Select LSB, then Program Change, in that order
        -- the device latches the bank on the program change, so a PC that
        arrives before the LSB selects from the previously latched bank.
        """
        target = self.channel if channel is None else channel
        for message in entry.select_messages(target):
            self._out.send_message(message)

    def probe_srx(self, *, lsb_range: Iterable[int] = range(0, 64),
                  settle: float = SELECT_GAP,
                  on_progress: Optional[Callable[[int, Optional[str]], None]] = None,
                  timeout: Optional[float] = None) -> Dict[int, str]:
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
        return found

    def scan_bank(self, bank_id: str, *,
                  on_progress: Optional[Callable[[int, int, str], None]] = None,
                  settle: float = SELECT_GAP,
                  timeout: Optional[float] = None) -> Dict[int, str]:
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
        """
        entries = banks.slots(bank_id)
        out: Dict[int, str] = {}
        previous: Optional[str] = None
        for index, entry in enumerate(entries):
            self.select(entry)
            time.sleep(settle)
            name = self.temporary_patch_name(timeout=timeout)
            attempts = 0
            while name == previous and attempts < 3:
                time.sleep(settle)
                name = self.temporary_patch_name(timeout=timeout)
                attempts += 1
            out[entry.number] = name
            previous = name
            if on_progress is not None:
                on_progress(index + 1, len(entries), name)
        return out
