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

"""Which ports `XvBridge.connect` opens, and when it is allowed to sweep.

Two bugs are here, and the second is why the first survived so long.

`XvBridge.autodetect` had no test at all, which is how a wire byte reached
`XvBridge.standard` -- a function whose `device_id` is the panel number. On
a synth set to 17 the reply carries 0x10, and 16 is not a panel number, so
autodetect raised "device ID 16 is out of range" *on the port that had just
answered*. The instrument was found; the failure read as the opposite.

And autodetect's own docstring claimed the remembered port "turns the common
case into one round trip rather than a sweep of thirty ports", while
`_sweep` probed every port on the machine every single launch. The
documented behaviour was what nobody had.

No hardware here: the probing is stubbed with what it would have found,
which is the whole of what these tests are about.
"""

import pytest

from xv import bridge as b
from xv import config as cfg
from xv import messages as m

XV_PORT = "Roland XV-2020:Roland XV-2020 MIDI 1 68:0"
OTHER_PORT = "Midi Through:Midi Through Port-0 14:0"


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


class _Probe:
    """Records what was probed and what answered.

    `answering` maps port name to the wire byte that answers there, or is
    left out for a port that stays silent -- which on this protocol is
    indistinguishable from a device that is off, and is the case these
    tests care about.
    """

    def __init__(self, answering):
        self.answering = answering
        self.tried = []
        self.swept = 0

    def install(self, monkeypatch):
        probe = self

        def fake_try_pair(send_name, recv_name, timeout):
            probe.tried.append(send_name)
            wire = probe.answering.get(send_name)
            return None if wire is None else _identity(wire, send_name)

        def fake_sweep(cls, config_path, probe_timeout, on_try):
            probe.swept += 1
            found = []
            for name, wire in probe.answering.items():
                if on_try is not None:
                    on_try(name)
                probe.tried.append(name)
                found.append(_identity(wire, name))
            return found

        monkeypatch.setattr(b.XvBridge, "_try_pair", staticmethod(fake_try_pair))
        monkeypatch.setattr(b.XvBridge, "_sweep", classmethod(fake_sweep))

        def fake_standard(cls, port_name, **kwargs):
            # `standard()` opens real ports, which is not what is under
            # test -- but its *conversion* is, because that is where the
            # device-ID bug lived: it runs the panel number through
            # device_id_byte() and raises on a wire byte. Kept faithful so
            # a reintroduced wire byte fails here as it did on hardware.
            bridge = object.__new__(cls)
            bridge.send_port = port_name
            bridge.device_id = m.device_id_byte(kwargs["device_id"])
            return bridge

        monkeypatch.setattr(b.XvBridge, "standard", classmethod(fake_standard))
        return probe


def _config(tmp_path, **settings):
    path = str(tmp_path / "config.toml")
    for key, value in settings.items():
        cfg._update_config(path, **{key: value})
    return path


# --- the normal path: trust the config ---------------------------------------


def test_a_remembered_port_is_used_without_sweeping(monkeypatch, tmp_path):
    """One Identity Request to the saved port, and nothing else.

    The whole point: a sweep pings every MIDI device on the machine, once
    per launch, and takes seconds on a box with thirty ports.
    """
    path = _config(tmp_path, send_port=XV_PORT, recv_port=XV_PORT, device_id=17)
    probe = _Probe({XV_PORT: m.device_id_byte(17)}).install(monkeypatch)

    bridge = b.XvBridge.connect(config_path=path)

    assert probe.swept == 0, "swept every port when the answer was on file"
    assert probe.tried == [XV_PORT], "probed something other than the saved port"
    assert bridge.device_id == 0x10


def test_the_remembered_path_takes_the_device_id_from_the_reply(monkeypatch, tmp_path):
    """Not from the config file, which may be stale.

    If somebody changes the device ID on the panel, the reply is right and
    the file is wrong. A wrongly addressed request is answered with
    silence, so the live answer is the only one that can be trusted.
    """
    path = _config(tmp_path, send_port=XV_PORT, recv_port=XV_PORT, device_id=17)
    _Probe({XV_PORT: m.device_id_byte(24)}).install(monkeypatch)

    bridge = b.XvBridge.connect(config_path=path)

    assert bridge.device_id == m.device_id_byte(24)


def test_the_remembered_path_does_not_write_the_config(monkeypatch, tmp_path):
    """It reads the file; it does not edit it.

    A plain launch takes the config as its input. Writing to it would mean
    the trusted thing silently changes under the next launch, and a stale
    port name would heal itself instead of being reported.
    """
    path = _config(tmp_path, send_port=XV_PORT, recv_port=XV_PORT, device_id=20)
    _Probe({XV_PORT: m.device_id_byte(21)}).install(monkeypatch)

    b.XvBridge.connect(config_path=path)

    assert cfg.load_last_ports(path) == (XV_PORT, XV_PORT)
    assert cfg.load_device_id(path) == 20


# --- nothing remembered ------------------------------------------------------


def test_nothing_remembered_sweeps_and_saves(monkeypatch, tmp_path):
    """First run: nothing is known, so everything is asked."""
    path = str(tmp_path / "config.toml")
    probe = _Probe({XV_PORT: m.device_id_byte(17)}).install(monkeypatch)

    b.XvBridge.connect(config_path=path)

    assert probe.swept == 1
    assert cfg.load_last_ports(path) == (XV_PORT, XV_PORT)
    assert cfg.load_device_id(path) == 17


# --- --scan ------------------------------------------------------------------


def test_scan_sweeps_even_with_a_remembered_port(monkeypatch, tmp_path):
    """The way to recover a synth that has moved to another port."""
    path = _config(tmp_path, send_port=OTHER_PORT, recv_port=OTHER_PORT)
    probe = _Probe({XV_PORT: m.device_id_byte(17)}).install(monkeypatch)

    b.XvBridge.connect(config_path=path, scan=True)

    assert probe.swept == 1
    assert cfg.load_last_ports(path) == (XV_PORT, XV_PORT)
    assert cfg.load_device_id(path) == 17


def test_scan_and_no_scan_agree_on_a_healthy_setup(monkeypatch, tmp_path):
    """A remembered port that still works gives the same answer either way."""
    path = _config(tmp_path, send_port=XV_PORT, recv_port=XV_PORT, device_id=17)
    _Probe({XV_PORT: m.device_id_byte(17)}).install(monkeypatch)

    quiet = b.XvBridge.connect(config_path=path)
    scanned = b.XvBridge.connect(config_path=path, scan=True)

    assert (quiet.send_port, quiet.device_id) == (scanned.send_port, scanned.device_id)


# --- the remembered port is dead ---------------------------------------------


def test_a_dead_remembered_port_is_an_error_naming_scan(monkeypatch, tmp_path):
    """An error, not a silent sweep.

    The guess is gone, but sweeping anyway would make an ordinary launch
    quietly take the slow path for a reason nobody asked about. Naming
    --scan says what happened and what to do about it.
    """
    path = _config(tmp_path, send_port=XV_PORT, recv_port=XV_PORT, device_id=17)
    probe = _Probe({}).install(monkeypatch)

    with pytest.raises(b.DeviceNotFound) as exc:
        b.XvBridge.connect(config_path=path)

    assert "--scan" in str(exc.value)
    assert probe.swept == 0, "swept despite being told not to"
    assert cfg.load_last_ports(path) == (XV_PORT, XV_PORT), "erased the guess"


def test_a_dead_remembered_port_can_be_recovered_with_scan(monkeypatch, tmp_path):
    """The documented way out actually works."""
    path = _config(tmp_path, send_port=OTHER_PORT, recv_port=OTHER_PORT)
    _Probe({XV_PORT: m.device_id_byte(17)}).install(monkeypatch)

    with pytest.raises(b.DeviceNotFound):
        b.XvBridge.connect(config_path=path)

    b.XvBridge.connect(config_path=path, scan=True)

    assert cfg.load_last_ports(path) == (XV_PORT, XV_PORT)


# --- the device ID conversion, which autodetect used to get wrong -----------


@pytest.mark.parametrize("panel", [17, 24, 32])
def test_the_reply_is_converted_to_a_panel_number(monkeypatch, tmp_path, panel):
    """`standard()` takes the panel number; the reply carries the wire byte."""
    path = _config(tmp_path, send_port=XV_PORT, recv_port=XV_PORT)
    _Probe({XV_PORT: m.device_id_byte(panel)}).install(monkeypatch)

    bridge = b.XvBridge.connect(config_path=path, scan=True)

    assert bridge.device_id == m.device_id_byte(panel)


def test_the_default_device_id_is_the_one_that_broke(monkeypatch, tmp_path):
    """Panel 17, answered with 0x10 -- which is 16, and 16 is not a panel number."""
    with pytest.raises(ValueError):
        m.device_id_byte(m.device_id_byte(17))

    path = str(tmp_path / "config.toml")
    _Probe({XV_PORT: m.device_id_byte(17)}).install(monkeypatch)

    assert b.XvBridge.connect(config_path=path).device_id == 0x10


def test_two_answers_are_still_refused(monkeypatch, tmp_path):
    """A sweep keeps probing after a hit so it can refuse to pick."""
    path = str(tmp_path / "config.toml")
    other = "ESI M4U eX:ESI M4U eX MIDI 1 68:0"
    _Probe({XV_PORT: m.device_id_byte(17), other: m.device_id_byte(17)}).install(
        monkeypatch
    )

    with pytest.raises(b.AmbiguousDevice):
        b.XvBridge.connect(config_path=path, scan=True)


def test_nothing_answers_at_all(monkeypatch, tmp_path):
    path = str(tmp_path / "config.toml")
    _Probe({}).install(monkeypatch)

    with pytest.raises(b.DeviceNotFound):
        b.XvBridge.connect(config_path=path)
