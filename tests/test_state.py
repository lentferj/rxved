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

"""What the synth's state says about a channel that makes no sound.

Pure state objects -- no bridge, no demo synth, no event loop. The logic
under test is a diagnosis, and the thing worth pinning down is that it never
reads as exhaustive: one of the ways to silence a part on an XV-2020 is not
in the parameter address map at all.
"""

from xv.bridge import (DeviceState, PartState, PerformanceCommon, SetupState,
                       SoundMode, SystemChannels)


class TestSilenceReport:
    """Built from states directly -- no bridge, no demo synth."""

    def _state(self, *, mode, parts=(), solo=None):
        setup = SetupState(
            mode=mode, patch_msb=87, patch_lsb=0, patch_program=0,
            performance_msb=85, performance_lsb=0, performance_program=0,
        )
        channels = SystemChannels(patch_receive=0, performance_control=14)
        common = PerformanceCommon(name="X", solo=solo)
        return DeviceState(setup=setup, channels=channels,
                           parts=tuple(parts), common=common)

    def _part(self, number, channel, *, rx=True, level=100):
        return PartState(part=number, receive_channel=channel,
                         receive_switch=rx, level=level,
                         msb=87, lsb=64, program_change=0)

    def test_patch_mode_is_the_whole_answer(self):
        """The likeliest cause of "only channel 1 sounds", and not a mute."""
        report = self._state(mode=SoundMode.PATCH).silence_report()
        joined = " ".join(report)
        assert "single-timbral" in joined
        assert "only channel 1 sounds" in joined
        # Nothing about parts: in this mode they are not in use, and listing
        # them as healthy would suggest they were being consulted.
        assert not any("Part " in line for line in report)

    def test_solo_is_reported_as_the_cause_it_is(self):
        parts = [self._part(n, n - 1) for n in range(1, 17)]
        report = " ".join(
            self._state(mode=SoundMode.PERFORM, parts=parts,
                        solo=3).silence_report())
        assert "Solo Part Select is on, set to part 3" in report

    def test_soloed_out_parts_are_not_called_muted(self):
        parts = [self._part(n, n - 1) for n in range(1, 17)]
        state = self._state(mode=SoundMode.PERFORM, parts=parts, solo=3)
        # Part 5 is inaudible, but nothing on part 5 is wrong.
        assert state.soloed_out(parts[4])
        assert parts[4].silence_reason() is None
        assert state.audible_parts(4) == []

    def test_a_healthy_synth_says_so_plainly(self):
        parts = [self._part(n, n - 1) for n in range(1, 17)]
        report = self._state(mode=SoundMode.PERFORM,
                             parts=parts).silence_report()
        assert "Nothing readable is silencing any part" in report[0]

    def test_unassigned_channels_are_distinguished_from_muted_ones(self):
        """A channel no part listens on is not a muted channel."""
        parts = [self._part(1, 0), self._part(2, 1)]
        report = " ".join(
            self._state(mode=SoundMode.PERFORM, parts=parts).silence_report())
        assert "No part listens on channel" in report
        assert "nothing is muted there" in report

    def test_level_zero_and_rx_off_are_told_apart(self):
        parts = [self._part(1, 0, rx=False), self._part(2, 1, level=0)]
        report = " ".join(
            self._state(mode=SoundMode.PERFORM, parts=parts).silence_report())
        assert "Part  1 (ch  1): RX SWITCH is OFF" in report
        assert "Part  2 (ch  2): LEVEL is 0" in report

    def test_the_unreadable_mute_switch_is_always_named(self):
        """The report must never read as exhaustive, in any state."""
        parts = [self._part(n, n - 1) for n in range(1, 17)]
        for state in (
            self._state(mode=SoundMode.PERFORM, parts=parts),
            self._state(mode=SoundMode.PERFORM, parts=parts, solo=3),
            self._state(mode=SoundMode.PERFORM,
                        parts=[self._part(1, 0, rx=False)]),
        ):
            assert "Mute Switch" in " ".join(state.silence_report())

    def test_patch_mode_report_does_not_mention_the_mute_switch(self):
        """There is nothing to check on the panel: the parts are not in use."""
        report = " ".join(self._state(mode=SoundMode.PATCH).silence_report())
        assert "Mute Switch" not in report
