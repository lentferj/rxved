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

"""The `--hardware` option, and the synth fixture for the file that uses it.

**Nothing here runs unless you ask for it.** CLAUDE.md's hardware rule is
that only one session drives the synth at a time, so the suite that talks to
a real XV-2020 is opt-in twice over: the `--hardware` flag, and the machine
actually answering. CI never passes the flag, so `tests/test_hardware.py`
skips there by design rather than by accident -- an environment variable
covers the case of a synth plugged into a CI runner that nobody meant to
address.

A skip is a skip, not a pass. `pytest -rs` names the reason, so "the synth
was off" and "the offsets are wrong" never look like the same green.
"""

import pytest

#: Why the option is spelled out rather than folded into `-m hardware`.
HELP = (
    "run the tests that need a real XV-2020 on a MIDI port. They open the "
    "ports, so only one session may use them at a time. Without this flag "
    "they skip."
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("hardware", "tests against a real XV-2020")
    group.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help=HELP,
    )


def _asked_for_hardware(config: pytest.Config) -> bool:
    """The flag, or the environment variable that says the same thing."""
    import os

    return bool(config.getoption("--hardware")) or bool(
        os.environ.get("RXVED_HARDWARE")
    )


@pytest.fixture(scope="session")
def synth(request: pytest.FixtureRequest):
    """A live :class:`~xv.bridge.XvBridge`, or a skip saying why not.

    Session-scoped on purpose: one connection, one set of ports, held for
    the whole run rather than opened and closed per test. The tests that
    write put back what they changed, so the next one starts clean.

    A failure to connect is a skip, never a failure, and the reason lists
    every cause the manual gives rather than picking one -- a request the
    device cannot serve is answered with silence, so a timeout does not
    distinguish them (CLAUDE.md, "Two protocol facts").
    """
    if not _asked_for_hardware(request.config):
        pytest.skip(
            "hardware tests need --hardware (or RXVED_HARDWARE=1); the "
            "suite that talks to a real XV-2020 is opt-in because only one "
            "session may drive the synth"
        )

    from xv import bridge as b

    try:
        connection = b.XvBridge.connect(scan=False)
    except (b.DeviceNotFound, b.MidiUnavailable) as exc:
        pytest.skip(
            f"no XV-2020 answered ({exc}). The device may be powered off, "
            "on a different port (USB renumbers them), busy, or have Rx "
            "Exclusive off -- the manual says a request it cannot serve is "
            "answered with silence, so a timeout does not say which."
        )
    try:
        yield connection
    finally:
        connection.close()
