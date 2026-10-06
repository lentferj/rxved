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

"""Local ``config.toml``: which ports answered last, and on which channel.

Now a binding of :class:`vinsynlib.config.Settings` rather than a seventh
copy of the read-modify-write store. The behaviour, including the traps it
is written around, is the family's and lives in one place:

* Read-modify-write, never a blind overwrite, so unrelated keys survive each
  other's saves -- this file holds a port pair, a device ID and a channel.
* Refuses to write over a file it cannot parse, and says so once. Collapsing
  "missing" and "unparseable" turns the next save into a blind overwrite of
  a file the code never understood, so one stray bracket would cost the user
  every other setting in it, silently.
* Everything written is escaped. A port name is whatever ALSA reports, and a
  quote in one produces a file that is not TOML -- which the refusal above
  then declines to overwrite, so the cache never heals.
* Only :class:`OSError` is swallowed: a read-only directory or a full disk,
  where forgetting a preference beats refusing to run.
* A TOML boolean is not a channel and not a device ID.

What is added here is the name, and two things only this instrument knows.

**The device ID is a panel number, not a wire byte.** The XV-2020 *displays*
its device ID as 17-32, which is the wire byte 0x10-0x1F plus one. The two
ranges overlap, so a value from the wrong one is answered with silence, and a
value the panel cannot show is not something the user can confirm on the
hardware. So the library's byte-wide default is narrowed here, once.

**The remembered port pair is remembered as a pair.** The library's
``load_ports`` returns a value only when both ends are known, which is what
this project always did and what :meth:`xv.bridge.XvBridge.connect` needs: an
output-only or input-only memory is not half of a connection, it is no
connection.

BEHAVIOUR CHANGE, once: the library writes the output port under the key
``port``, where this file used to write ``send_port``. An existing rxved
``config.toml`` therefore forgets its remembered port on the first run after
this change, re-probes, and is rewritten in the new shape. That is the whole
cost, and ``config.toml`` is explicitly disposable -- deleting it costs one
re-entry of each setting -- so the trade is a family-wide key name against one
forgotten port.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from vinsynlib.config import Settings

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "load_channel",
    "load_device_id",
    "load_last_ports",
    "save_channel",
    "save_device_id",
    "save_last_ports",
    "settings",
]

#: Local settings, gitignored. Named the way this project has always named
#: it; the library calls the same thing ``DEFAULT_PATH``.
DEFAULT_CONFIG_PATH = "config.toml"

#: One store for this application. Exposed so a caller that needs a key the
#: convenience wrappers below do not cover -- a test writing a fixture, a
#: bridge recording what it found -- can use ``settings.update(path, ...)``
#: rather than growing another private TOML reader beside it.
settings = Settings("rxved", DEFAULT_CONFIG_PATH)

#: The panel numbers this synth displays, which is what it is remembered as.
#: See :func:`xv.messages.device_id_byte` for the conversion to the wire.
DEVICE_ID_MINIMUM = 17
DEVICE_ID_MAXIMUM = 32


def load_last_ports(
    path: str = DEFAULT_CONFIG_PATH,
) -> Optional[Tuple[str, str]]:
    """The send/receive pair that answered last time, if both are known."""
    return settings.load_ports(path)


def save_last_ports(
    send_port: str, recv_port: str, path: str = DEFAULT_CONFIG_PATH
) -> None:
    """Remember both ports in one write; see the module docstring."""
    settings.save_ports(send_port, recv_port, path)


def load_device_id(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    """The remembered device ID, as the **panel** numbers it: 17-32.

    Anything outside that range is refused here rather than handed to
    :func:`xv.messages.device_id_byte`, which takes panel numbers only.
    """
    return settings.load_device_id(
        path, minimum=DEVICE_ID_MINIMUM, maximum=DEVICE_ID_MAXIMUM
    )


def save_device_id(device_id: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    settings.save_device_id(device_id, path)


def load_channel(path: str = DEFAULT_CONFIG_PATH) -> Optional[int]:
    """The channel last sent on, zero-based, or None."""
    return settings.load_channel(path)


def save_channel(channel: int, path: str = DEFAULT_CONFIG_PATH) -> None:
    settings.save_channel(channel, path)


def _read_dict(path: str) -> Dict[str, Any]:
    """Every key in the file, for a caller that needs more than one.

    The status is deliberately not surfaced: a reader that cannot act on a
    parse failure gets no settings, which is the same answer as an absent
    file, and the writer -- which *can* act -- is the one that refuses.
    """
    return settings.read(path)[0]
