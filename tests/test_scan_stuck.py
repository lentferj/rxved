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

"""A scan that cannot work must say so, not report a name 128 times.

Found on real hardware, on a synth in PERFORMANCE mode. Bank Select plus
Program Change was sent on the patch receive channel for each of USER's 128
slots, and `temporary_patch_name()` came back "Cutter Clav" every time --
which was the patch the synth was actually sitting on (PST-A 042). The old
retry loop noticed the name was not changing, retried three times, then
accepted it, and the browser showed one wrong name repeated down the bank
with a `*` beside each. Indistinguishable from a bank of identically-named
patches, which is the problem: it was believed.

In PERFORMANCE mode the patch receive channel does not move the current
patch, and a Bank Select receive switch that is off drops the bank bytes
while the program change still lands. Two different causes, one observable:
the synth does not follow.
"""

import pytest

from types import SimpleNamespace

from xv import bridge as b
from xv import banks
from xv import messages as m


class _FakeBridge(b.XvBridge):
    """A real `XvBridge` with only the port-touching methods replaced.

    Everything `scan_bank` is actually about -- the retry loop, the stuck
    counter, the restore -- is the shipping code rather than a
    re-implementation of it, which is the point: a test that re-implements
    the loop proves nothing about the loop.

    Records the selects, so a test can assert the scan gave up early instead
    of playing all 128 slots to reach the same wrong answer.
    """

    def __init__(self):
        # Deliberately not super().__init__(): that validates a wire device
        # ID and holds ports, and there are none here.
        self.selected = []
        self.restored = []

    def select(self, entry, *, channel=None):
        self.selected.append(entry.number)

    def select_raw(self, msb, lsb, program_change, *, channel=None):
        self.restored.append((msb, lsb, program_change))

    def read_setup(self, *, timeout=None):
        return SimpleNamespace(patch_msb=87, patch_lsb=64, patch_program=41)

    def channel_for(self, kind):
        return 0


class _StuckBridge(_FakeBridge):
    """The hardware case: every read comes back with the current patch.

    What a synth in PERFORMANCE mode did -- Bank Select and Program Change
    on the patch receive channel, and the name read back never changed.
    """

    def __init__(self, name="Cutter Clav"):
        super().__init__()
        self.name = name

    def temporary_patch(self, *, timeout=None):
        return self.name, 0


class _ScriptedBridge(_FakeBridge):
    """A synth that follows, with the first few reads scripted.

    `script` is consumed one entry per select; past its end the name is
    derived from the slot number, so a test can arrange a specific opening
    (two slots sharing a name, say) and let the rest of the bank behave.
    """

    def __init__(self, script=()):
        super().__init__()
        self.script = list(script)

    def temporary_patch(self, *, timeout=None):
        index = len(self.selected) - 1
        if index < len(self.script):
            return self.script[index], 0
        return f"Patch {index + 1:03d}", 0


@pytest.fixture
def stuck():
    return _StuckBridge()


def _scan(bridge, bank_id="PST-A", **kwargs):
    """Run the real XvBridge.scan_bank against a fake."""
    return bridge.scan_bank(bank_id, settle=0.0, **kwargs)


class TestStuckScanIsRefused:
    def test_it_raises_rather_than_reporting_the_same_name(self, stuck):
        with pytest.raises(b.DeviceError) as exc:
            _scan(stuck, "PST-A")
        message = str(exc.value)
        assert "did not change patch" in message
        assert "Cutter Clav" in message, "should say what it kept reading"

    def test_it_names_the_likely_cause(self, stuck):
        """Both real causes, so the user can act without reading the source."""
        with pytest.raises(b.DeviceError) as exc:
            _scan(stuck, "PST-A")
        message = str(exc.value).lower()
        assert "performance mode" in message
        assert "rxbs" in message or "bank select receive" in message

    def test_it_gives_up_early(self, stuck):
        """A handful of program changes, not 128.

        The answer cannot change once the synth has declined to move four
        times running, and every extra one is a note the user did not ask
        to hear. The first slot cannot be a repeat, so the limit is reached
        on the (limit + 1)th select.
        """
        with pytest.raises(b.DeviceError):
            _scan(stuck, "PST-A")
        assert len(stuck.selected) == b.STUCK_SLOT_LIMIT + 1
        assert len(stuck.selected) < 10, "still grinding through the bank"

    def test_it_puts_the_patch_back(self, stuck):
        """A scan that fails still must not leave the synth somewhere else."""
        with pytest.raises(b.DeviceError):
            _scan(stuck, "PST-A")
        assert stuck.restored == [(87, 64, 41)]

    def test_the_limit_is_not_so_small_it_trips_on_real_duplicates(self):
        """Two adjacent slots sharing a name is normal and must survive.

        The retry loop already re-reads those; this limit is only about a
        run long enough that the program change is being ignored.
        """
        assert b.STUCK_SLOT_LIMIT >= 4


class TestRealDuplicatesStillWork:
    """The retry logic it grew alongside must not have been broken."""

    def test_two_adjacent_slots_may_share_a_name(self):
        """Real banks do this. It must not be mistaken for a stuck synth."""
        bridge = _ScriptedBridge(["Alpha Juno", "Alpha Juno", "Mini Bass"])
        names = _scan(bridge, "SRX-01-1")
        assert names[1] == "Alpha Juno"
        assert names[2] == "Alpha Juno"
        assert names[3] == "Mini Bass"


class TestScanStillWorksWhenTheSynthFollows:
    def test_distinct_names_are_kept(self):
        """The ordinary case, unchanged: every slot gets its own name."""
        names = _scan(_ScriptedBridge(), "PST-A")
        assert len(names) == banks.bank("PST-A").count
        assert names[1] == "Patch 001"
        assert names[128] == "Patch 128"
        assert len(set(names.values())) == 128, "names were collapsed"


class TestWireByteIsNotConfusedWithPanelNumber:
    """Kept here because the two halves of this bug are the same mistake.

    `scan_bank` reaches `standard()`'s conversion through `_from_identity`,
    and both were wrong in the same way at different times.
    """

    def test_the_reply_byte_is_not_a_panel_number(self):
        assert m.device_id_byte(17) == 0x10
        with pytest.raises(ValueError):
            m.device_id_byte(0x10)
