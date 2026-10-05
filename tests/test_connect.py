# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
#
# rxved is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# rxved is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""What autodetect does with the device ID an Identity Reply carries.

`XvBridge.autodetect` had no test at all, which is how a wire byte reached
`XvBridge.standard` -- a function whose `device_id` is the panel number. On
a synth set to 17 the reply carries 0x10, and 16 is not a panel number, so
autodetect raised "device ID 16 is out of range" *on the port that had just
answered*. The instrument was found; the failure read as the opposite.

No hardware here: the probing is stubbed with what it would have found,
which is the whole of what this test is about.
"""

import pytest

from xv import bridge as b
from xv import messages as m

XV_PORT = "Roland XV-2020:Roland XV-2020 MIDI 1 68:0"


def _identity(wire: int, port: str = XV_PORT) -> b.DeviceIdentity:
    """What `_try_pair` returns when a device with `wire` answers on `port`."""
    return b.DeviceIdentity(
        send_port=port,
        recv_port=port,
        device_id=wire,
        family=m.IDENTITY_FAMILY,
        family_number=m.IDENTITY_FAMILY_NUMBER,
        revision=(1, 0),
    )


def _run_autodetect(monkeypatch, wire: int, tmp_path):
    """Autodetect against one stubbed answer; return (panel, wire_sent)."""
    seen = {}

    monkeypatch.setattr(
        b.XvBridge, "_sweep", classmethod(lambda cls, *a, **k: [_identity(wire)])
    )

    def fake_standard(cls, port_name, **kwargs):
        seen["panel"] = kwargs["device_id"]
        # The conversion `standard()` does, which is what used to raise.
        seen["wire"] = m.device_id_byte(kwargs["device_id"])
        return object.__new__(cls)

    monkeypatch.setattr(b.XvBridge, "standard", classmethod(fake_standard))

    b.XvBridge.autodetect(config_path=str(tmp_path / "config.toml"))
    return seen["panel"], seen["wire"]


@pytest.mark.parametrize("panel", [17, 24, 32])
def test_the_reply_is_converted_to_a_panel_number(monkeypatch, tmp_path, panel):
    """`standard()` takes the panel number; the reply carries the wire byte."""
    got_panel, got_wire = _run_autodetect(
        monkeypatch, m.device_id_byte(panel), tmp_path
    )
    assert got_panel == panel
    assert got_wire == m.device_id_byte(panel)


def test_the_default_device_id_is_the_one_that_broke(monkeypatch, tmp_path):
    """Panel 17 -- the XV-2020's default -- is the case that actually broke.

    `device_id_byte(0x10)` raises, because 16 is not in 17-32. This is the
    one-liner that says so, so a reintroduction fails here and not on
    somebody's synth.
    """
    with pytest.raises(ValueError):
        m.device_id_byte(m.device_id_byte(17))

    got_panel, got_wire = _run_autodetect(monkeypatch, m.device_id_byte(17), tmp_path)
    assert (got_panel, got_wire) == (17, 0x10)
