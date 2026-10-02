# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
# The frame layout, command IDs, model ID, checksum rule, address map and the
# Identity Reply payload are transcribed as data from Roland's own
# "MIDI Implementation" chapter of the XV-2020 Owner's Manual (Roland
# Corporation, 2003), pp. 140-146. See docs/RESOLUTION_NOTES.md §1.
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

"""The XV-2020 System Exclusive wire codec.

**Partly verified against hardware, on 2026-09-11.** What was exercised
against a real XV-2020 and what was not is set out in DISCLAIMER.md and
docs/RESOLUTION_NOTES.md -- read which is which before trusting a byte
offset.

Roland's address-mapped SysEx, in the form the XV-2020 uses it::

    F0 41 dev 00 10 11 aa bb cc dd ss ss ss ss sum F7   RQ1 (request)
    F0 41 dev 00 10 12 aa bb cc dd <data...>      sum F7   DT1 (data set)

``41`` is Roland; ``00 10`` is the XV-2020's model ID; ``dev`` is the device
ID, which is **``10H``-based, not zero-based** -- the manual gives the range
as ``10H - 1FH`` plus the ``7FH`` broadcast, so the front panel's "Device ID
17" is the byte ``10H``. :func:`device_id_byte` does that conversion in one
place, because getting it wrong produces a device that simply never answers
and no other symptom.

Three properties of this protocol shape everything built on it:

**Addresses are 7-bit, four bytes, and do not carry.** Each of ``aa bb cc
dd`` is 0-127, so address arithmetic is base-128 and ordinary integer
addition is wrong. :func:`address_add` exists so that no caller open-codes
it; :func:`pack_address` refuses a byte over 127 rather than truncating it,
because a silently masked address reads or writes *somewhere else on the
device* and the reply looks perfectly well-formed.

**The checksum covers the address too**, not just the data -- sum address and
data bytes, take it modulo 128, and the checksum is what makes that total
come out to zero. A checksum computed over the data alone is the classic way
to get "Checksum error" on the machine's display, which the manual lists as a
distinct error from a malformed message (OM p. 118).

**A request that cannot be served is answered with silence.** The manual is
explicit: "if the device is in a state in which it is able to transmit, it
will transmit; if the conditions are not met, nothing is transmitted."  So a
timeout here does not mean the device is absent or the address is wrong --
those are indistinguishable from a device that was merely busy. Callers must
not treat one timed-out read as proof of anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

__all__ = [
    "SYSEX_START",
    "SYSEX_END",
    "ROLAND_ID",
    "MODEL_ID",
    "BROADCAST_DEVICE",
    "DEFAULT_DEVICE_ID",
    "CMD_RQ1",
    "CMD_DT1",
    "IDENTITY_REQUEST",
    "IDENTITY_FAMILY",
    "IDENTITY_FAMILY_NUMBER",
    "Address",
    "ADDR_SETUP",
    "ADDR_SYSTEM",
    "ADDR_TEMP_PERFORMANCE",
    "ADDR_TEMP_PATCH_PATCH_MODE",
    "ADDR_USER_PERFORMANCE",
    "ADDR_USER_PATCH",
    "ADDR_USER_RHYTHM",
    "OFF_PATCH_COMMON",
    "OFF_RHYTHM_COMMON",
    "PATCH_NAME_LEN",
    "PERFORMANCE_NAME_LEN",
    "RHYTHM_NAME_LEN",
    "device_id_byte",
    "device_id_display",
    "pack_address",
    "size_bytes",
    "address_scale",
    "address_add",
    "checksum",
    "rq1",
    "dt1",
    "identity_request",
    "DataSet",
    "parse_dt1",
    "IdentityReply",
    "parse_identity_reply",
    "decode_name",
    "encode_name",
    "user_patch_address",
    "user_performance_address",
    "user_rhythm_address",
    "temporary_patch_address",
]

# --- frame constants --------------------------------------------------------

SYSEX_START = 0xF0
SYSEX_END = 0xF7

#: Roland's manufacturer ID (OM p. 140).
ROLAND_ID = 0x41

#: "The model ID of the exclusive messages used by this instrument is
#: 00H 10H" (OM p. 145). Two bytes, in this order.
MODEL_ID = (0x00, 0x10)

#: Device ID that every unit answers to regardless of its own setting.
BROADCAST_DEVICE = 0x7F

#: Factory default. The front panel calls this "17"; see
#: :func:`device_id_byte` for why the two numbers differ.
DEFAULT_DEVICE_ID = 0x10

CMD_RQ1 = 0x11  #: Data Request 1  (OM p. 145)
CMD_DT1 = 0x12  #: Data Set 1      (OM p. 145)

#: Universal Non-realtime Identity Request, broadcast. Not Roland-specific,
#: and the only way to ask "is anything there?" without knowing the device ID
#: -- which is exactly the discovery problem, since a Roland RQ1 addressed to
#: the wrong device ID is answered with silence.
IDENTITY_REQUEST = bytes((SYSEX_START, 0x7E, BROADCAST_DEVICE, 0x06, 0x01, SYSEX_END))

#: Device family code / family number in the XV-2020's Identity Reply
#: (OM p. 145): ``F0 7E dev 06 02 41 10 01 00 03 00 00 00 00 F7``.
IDENTITY_FAMILY = (0x10, 0x01)
IDENTITY_FAMILY_NUMBER = (0x00, 0x03)


# --- addresses --------------------------------------------------------------

#: A four-byte, 7-bit-per-byte address.
Address = Tuple[int, int, int, int]

# Top-level map, OM p. 146. Written as literal 4-tuples rather than derived,
# so each one can be read straight off the printed table when checking.
ADDR_SETUP: Address = (0x01, 0x00, 0x00, 0x00)
ADDR_SYSTEM: Address = (0x02, 0x00, 0x00, 0x00)
ADDR_TEMP_PERFORMANCE: Address = (0x10, 0x00, 0x00, 0x00)
#: Temporary Patch/Rhythm as seen in Patch mode -- the sound the machine is
#: playing right now. Reading a name from here is how rxved learns what a
#: *preset* patch is called: the preset banks have no addresses of their own.
ADDR_TEMP_PATCH_PATCH_MODE: Address = (0x1F, 0x00, 0x00, 0x00)
ADDR_USER_PERFORMANCE: Address = (0x20, 0x00, 0x00, 0x00)
ADDR_USER_PATCH: Address = (0x30, 0x00, 0x00, 0x00)
ADDR_USER_RHYTHM: Address = (0x40, 0x00, 0x00, 0x00)

#: Offset of Patch Common within a patch, and of Rhythm Common within a
#: rhythm set (OM p. 146, tables 1-4-1 and 1-4-2). Both are zero, and both
#: are named rather than open-coded so the next person does not have to
#: verify that they still are.
OFF_PATCH_COMMON: Address = (0x00, 0x00, 0x00, 0x00)
OFF_RHYTHM_COMMON: Address = (0x00, 0x10, 0x00, 0x00)

#: Name field lengths. Patch Name 1..12 at Patch Common offset 00 00
#: (OM p. 149); Performance Name 1..12; Rhythm Name 1..12. All ASCII in the
#: range 32-127, space-padded.
PATCH_NAME_LEN = 12
PERFORMANCE_NAME_LEN = 12
RHYTHM_NAME_LEN = 12

#: Stride between consecutive User Patches: ``30 00``, ``30 01`` ... ``30 7F``
#: (OM p. 146). One step in the second address byte.
_USER_PATCH_STRIDE: Address = (0x00, 0x01, 0x00, 0x00)
#: Same for User Performances: ``20 00`` .. ``20 3F``.
_USER_PERFORMANCE_STRIDE: Address = (0x00, 0x01, 0x00, 0x00)
#: User Rhythms step by 16, not 1: ``40 00``, ``40 10``, ``40 20``, ``40 30``.
_USER_RHYTHM_STRIDE: Address = (0x00, 0x10, 0x00, 0x00)


def device_id_byte(panel: int) -> int:
    """The wire byte for the device ID printed on the machine's own display.

    The XV-2020's device ID is documented as ``10H - 1FH`` -- **not** the
    zero-based ``00H - 1FH`` other Roland gear uses -- and the front panel
    numbers it 17-32 to match, so panel "17" is the byte ``0x10``.

    **This takes the panel number only.** It used to accept "either form",
    passing a value already in ``0x10``-``0x1F`` straight through as a
    convenience -- which is unsound, because those two ranges *overlap*:
    16-31 as wire bytes and 17-32 as panel numbers share every value from 17
    to 31, so the function could not tell which the caller meant, and
    silently chose wrong. Passing the panel's own "17" got ``0x11``, one
    device ID too high, and the XV-2020 -- which answers a request addressed
    to the wrong ID with **silence** -- simply never replied. Caught against
    real hardware, which is the only way it could have been caught: every
    synthetic test agreed with itself.

    So the two directions are now strictly typed by convention. Anything
    holding a wire byte (an Identity Reply's device field, a bridge's
    ``device_id``) passes it around as a wire byte and never comes back
    through here.
    """
    value = int(panel)
    if value == BROADCAST_DEVICE:
        return BROADCAST_DEVICE
    if not 17 <= value <= 32:
        raise ValueError(
            f"device ID {panel} is out of range; the XV-2020 displays its "
            f"device ID as 17-32 (wire bytes 0x10-0x1F). Use 127 to "
            f"broadcast. If you have a raw wire byte, do not pass it here -- "
            f"it is already what this function returns."
        )
    return value - 17 + 0x10


def device_id_display(wire: int) -> int:
    """Inverse of :func:`device_id_byte`, for messages shown to the user.

    Reports the number printed on the machine's display, since that is the
    one the user can check against the panel.
    """
    value = int(wire)
    if value == BROADCAST_DEVICE:
        return BROADCAST_DEVICE
    if not 0x00 <= value <= 0x1F:
        raise ValueError(
            f"device ID byte {wire:#04x} is not a device ID; the XV-2020 "
            f"uses 0x10-0x1F, or 0x7F for broadcast"
        )
    return value - 0x10 + 17


def pack_address(address: Sequence[int]) -> bytes:
    """Validate a four-byte 7-bit address and return it as bytes.

    Refuses out-of-range bytes rather than masking them. Masking would turn
    an arithmetic slip into a well-formed message aimed at a *different*
    place on the device -- and since the reply carries the address it was
    served from, the caller would have to be checking that to notice.
    """
    values = list(address)
    if len(values) != 4:
        raise ValueError(f"address must be 4 bytes, got {len(values)}")
    for index, byte in enumerate(values):
        if not 0 <= byte <= 0x7F:
            raise ValueError(
                f"address byte {index} is {byte}, outside the 7-bit range "
                f"0-127; Roland addresses do not use the high bit"
            )
    return bytes(values)


def address_add(base: Sequence[int], offset: Sequence[int]) -> Address:
    """Add two four-byte addresses in base 128.

    Roland addresses carry at 128, not 256: ``00 7F 00 00`` plus ``00 01 00
    00`` is ``01 00 00 00``. Treating the four bytes as a plain 32-bit
    integer gets this wrong for every offset that crosses a byte boundary,
    which is most of the interesting ones.
    """
    left = pack_address(base)
    right = pack_address(offset)
    total = _flatten(left) + _flatten(right)
    if total >= 128**4:
        raise ValueError(
            f"address {tuple(left)} + {tuple(right)} overflows the 4-byte address space"
        )
    return _unflatten(total)


def address_scale(offset: Sequence[int], count: int) -> Address:
    """``offset`` taken ``count`` times, in base 128."""
    value = _flatten(pack_address(offset)) * int(count)
    if value >= 128**4:
        raise ValueError(f"{tuple(offset)} x {count} overflows the address space")
    return _unflatten(value)


def _flatten(address: Sequence[int]) -> int:
    """A four-byte base-128 address as a plain integer."""
    value = 0
    for byte in address:
        value = value * 128 + byte
    return value


def _unflatten(value: int) -> Address:
    """The inverse of :func:`_flatten`."""
    return (
        (value // (128**3)) % 128,
        (value // (128**2)) % 128,
        (value // 128) % 128,
        value % 128,
    )


def checksum(payload: Iterable[int]) -> int:
    """Roland's checksum over address **and** data bytes.

    The value that makes ``sum(address + data + [checksum]) % 128 == 0``.
    Computing it over the data alone is the standard way to get the
    machine's "Checksum error" (OM p. 118) -- a distinct error from a
    malformed message, so the display does tell you which mistake you made.
    """
    total = 0
    for byte in payload:
        if not 0 <= byte <= 0x7F:
            raise ValueError(f"checksum byte {byte} is outside the 7-bit range 0-127")
        total += byte
    return (128 - (total % 128)) % 128


def _device_wire(device: int) -> int:
    """Validate a device ID that is already a wire byte.

    ``rq1`` and ``dt1`` take the **wire byte**, not the panel number, and
    used to run it back through :func:`device_id_byte` "to be safe". That was
    the opposite of safe: the two ranges overlap, so a correct wire byte of
    ``0x11`` was read as panel 17 and shifted again to ``0x10``. Frames went
    out addressed to a device that was not there, and the XV-2020's answer to
    that is silence. Validated here instead, and never converted.
    """
    value = int(device)
    if value == BROADCAST_DEVICE or 0x00 <= value <= 0x1F:
        return value
    raise ValueError(
        f"device {device} is not a device-ID wire byte; expected 0x10-0x1F "
        f"or 0x7F. Convert a panel number (17-32) with device_id_byte()."
    )


def rq1(
    address: Sequence[int], size: Sequence[int], *, device: int = DEFAULT_DEVICE_ID
) -> bytes:
    """A Data Request (RQ1) frame. ``device`` is the wire byte.

    ``size`` is itself a four-byte base-128 address-shaped quantity, which is
    why it is not an ``int``: the manual specifies request sizes in the same
    notation as addresses, and a 128-byte request is ``00 00 01 00``, not
    ``00 00 00 80``. Pass :func:`size_bytes` output if you have a plain
    count.
    """
    body = pack_address(address) + pack_address(size)
    return (
        bytes((SYSEX_START, ROLAND_ID, _device_wire(device)) + MODEL_ID + (CMD_RQ1,))
        + body
        + bytes((checksum(body), SYSEX_END))
    )


def size_bytes(count: int) -> Address:
    """A plain byte count as the four-byte base-128 size RQ1 wants."""
    if count < 0 or count >= 128**4:
        raise ValueError(f"size {count} is out of range")
    return _unflatten(count)


def dt1(
    address: Sequence[int], data: Sequence[int], *, device: int = DEFAULT_DEVICE_ID
) -> bytes:
    """A Data Set (DT1) frame. ``device`` is the wire byte.

    rxved sends these in exactly one place: XvBridge.write_part_param, which
    writes six Performance Part parameters in the *temporary* area. It does
    not write patches -- every write to an XV-2020 patch is a write to a user
    slot that had something else in it.

    The encoder predates that use, because DT1 is also what the device
    *replies* with, and a round-trip test that can only decode is worth much
    less than one that can build the frame it expects to see.
    """
    payload = list(pack_address(address)) + [int(b) for b in data]
    for byte in payload:
        if not 0 <= byte <= 0x7F:
            raise ValueError(f"data byte {byte} is outside the 7-bit range")
    return (
        bytes((SYSEX_START, ROLAND_ID, _device_wire(device)) + MODEL_ID + (CMD_DT1,))
        + bytes(payload)
        + bytes((checksum(payload), SYSEX_END))
    )


def identity_request(device: int = BROADCAST_DEVICE) -> bytes:
    """Universal Identity Request, broadcast by default."""
    if device == BROADCAST_DEVICE:
        return IDENTITY_REQUEST
    return bytes((SYSEX_START, 0x7E, _device_wire(device), 0x06, 0x01, SYSEX_END))


# --- decoding ---------------------------------------------------------------


@dataclass(frozen=True)
class DataSet:
    """A decoded DT1 reply."""

    device: int
    address: Address
    data: bytes

    @property
    def device_display(self) -> int:
        return device_id_display(self.device)


def parse_dt1(frame: Sequence[int]) -> Optional[DataSet]:
    """Decode a DT1 frame, or ``None`` if this is not one of ours.

    Returns ``None`` -- rather than raising -- for anything that is not an
    XV-2020 DT1, because a shared MIDI port carries other devices' traffic
    and the caller's job is to skip it, not to handle an exception per
    foreign message. A **bad checksum on a frame that is otherwise ours**
    does raise: that is corruption on our own conversation, and silently
    dropping it would present as an unexplained timeout.
    """
    data = bytes(frame)
    if len(data) < 12:
        return None
    if data[0] != SYSEX_START or data[-1] != SYSEX_END:
        return None
    if data[1] != ROLAND_ID:
        return None
    if (data[3], data[4]) != MODEL_ID:
        return None
    if data[5] != CMD_DT1:
        return None
    body = data[6:-2]
    if len(body) < 4:
        return None
    expected = checksum(body)
    if data[-2] != expected:
        raise ValueError(
            f"DT1 checksum mismatch: frame carries {data[-2]:#04x}, "
            f"the address and data sum to {expected:#04x}"
        )
    return DataSet(
        device=data[2],
        address=(body[0], body[1], body[2], body[3]),
        data=bytes(body[4:]),
    )


@dataclass(frozen=True)
class IdentityReply:
    """A decoded Universal Identity Reply."""

    device: int
    manufacturer: int
    family: Tuple[int, int]
    family_number: Tuple[int, int]
    revision: Tuple[int, ...]

    @property
    def is_xv2020(self) -> bool:
        """Whether this reply is the XV-2020's, byte for byte.

        Family alone is not enough: ``10 01`` is shared across Roland's
        XV/JV line, and family *number* ``00 03`` is what separates the
        XV-2020 from its siblings. Matching on the family only would happily
        adopt an XV-3080 sitting on the same MIDI chain.
        """
        return (
            self.manufacturer == ROLAND_ID
            and self.family == IDENTITY_FAMILY
            and self.family_number == IDENTITY_FAMILY_NUMBER
        )

    @property
    def device_display(self) -> int:
        return device_id_display(self.device)


def parse_identity_reply(frame: Sequence[int]) -> Optional[IdentityReply]:
    """Decode a Universal Identity Reply, or ``None`` if it is not one."""
    data = bytes(frame)
    if len(data) < 8:
        return None
    if data[0] != SYSEX_START or data[-1] != SYSEX_END:
        return None
    if data[1] != 0x7E:
        return None
    if data[3] != 0x06 or data[4] != 0x02:
        return None
    body = data[5:-1]
    if len(body) < 5:
        return None
    return IdentityReply(
        device=data[2],
        manufacturer=body[0],
        family=(body[1], body[2]),
        family_number=(body[3], body[4]),
        revision=tuple(body[5:]),
    )


def decode_name(data: Sequence[int]) -> str:
    """Decode a patch/performance/rhythm name field.

    The map gives these as ASCII 32-127 and the machine space-pads them, so
    the trailing spaces are padding and not part of the name. Bytes outside
    the documented range are shown as ``.`` rather than dropped: a name that
    silently loses characters is worse to debug than one that visibly has a
    hole in it, and a stray byte here means the read landed at the wrong
    address.
    """
    out = []
    for byte in data:
        out.append(chr(byte) if 32 <= byte <= 126 else ".")
    return "".join(out).rstrip()


def encode_name(name: str, length: int = PATCH_NAME_LEN) -> bytes:
    """Encode a name to a fixed-width, space-padded, 7-bit ASCII field."""
    trimmed = name[:length].ljust(length)
    out = bytearray()
    for char in trimmed:
        code = ord(char)
        out.append(code if 32 <= code <= 126 else 0x20)
    return bytes(out)


# --- convenience address builders -------------------------------------------


def user_patch_address(number: int) -> Address:
    """Address of User Patch ``number`` (1-128), at its Patch Common."""
    if not 1 <= number <= 128:
        raise ValueError(f"user patch number {number} is outside 1-128")
    return address_add(ADDR_USER_PATCH, address_scale(_USER_PATCH_STRIDE, number - 1))


def user_performance_address(number: int) -> Address:
    """Address of User Performance ``number`` (1-64)."""
    if not 1 <= number <= 64:
        raise ValueError(f"user performance number {number} is outside 1-64")
    return address_add(
        ADDR_USER_PERFORMANCE,
        address_scale(_USER_PERFORMANCE_STRIDE, number - 1),
    )


def user_rhythm_address(number: int) -> Address:
    """Address of User Rhythm ``number`` (1-4), at its Rhythm Common."""
    if not 1 <= number <= 4:
        raise ValueError(f"user rhythm number {number} is outside 1-4")
    return address_add(ADDR_USER_RHYTHM, address_scale(_USER_RHYTHM_STRIDE, number - 1))


def temporary_patch_address() -> Address:
    """Address of the patch the machine is playing right now, in Patch mode.

    This is the only way to learn a **preset** patch's name from the device:
    the preset banks are ROM and have no addresses in the map, so the
    procedure is to select the patch over MIDI and then read the temporary
    area. That means reading a preset name is not a read-only act -- it
    changes what the machine is playing. See
    :meth:`xv.bridge.XvBridge.scan_bank`.
    """
    return ADDR_TEMP_PATCH_PATCH_MODE
