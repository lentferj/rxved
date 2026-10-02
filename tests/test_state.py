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

from xv.bridge import (
    ChannelMidi,
    DeviceState,
    PartState,
    PerformanceCommon,
    SetupState,
    SoundMode,
    SystemChannels,
)


class TestSilenceReport:
    """Built from states directly -- no bridge, no demo synth."""

    def _state(self, *, mode, parts=(), solo=None):
        setup = SetupState(
            mode=mode,
            patch_msb=87,
            patch_lsb=0,
            patch_program=0,
            performance_msb=85,
            performance_lsb=0,
            performance_program=0,
        )
        channels = SystemChannels(patch_receive=0, performance_control=14)
        common = PerformanceCommon(name="X", solo=solo)
        return DeviceState(
            setup=setup, channels=channels, parts=tuple(parts), common=common
        )

    def _part(self, number, channel, *, rx=True, level=100):
        return PartState(
            part=number,
            receive_channel=channel,
            receive_switch=rx,
            level=level,
            msb=87,
            lsb=64,
            program_change=0,
        )

    def test_patch_mode_is_reported_first(self):
        """The likeliest cause of "only channel 1 sounds", and not a mute."""
        report = self._state(mode=SoundMode.PATCH).silence_report()
        joined = " ".join(report)
        assert "single-timbral" in joined
        assert "only channel 1 sounds" in joined

    def test_patch_mode_with_unread_parts_says_nothing_about_them(self):
        """Unread is not the same as healthy, so claim nothing either way."""
        report = self._state(mode=SoundMode.PATCH).silence_report()
        assert not any("Part " in line for line in report)

    def test_patch_mode_still_reports_switched_off_parts(self):
        """The real machine's case: Patch mode AND most parts switched off.

        Reporting only the mode would send somebody to the front panel to
        switch to PERFORM, after which the symptom would not change. Both
        halves have to be said at once or the first one is misleading.
        """
        parts = [self._part(1, 0)] + [
            self._part(n, n - 1, rx=False) for n in range(2, 17)
        ]
        report = self._state(mode=SoundMode.PATCH, parts=parts).silence_report()
        joined = " ".join(report)
        assert "single-timbral" in joined
        assert "would not be enough on its own" in joined
        # Collapsed to a range rather than fifteen lines.
        assert "Parts 2-16: RX SWITCH is OFF" in joined
        # And the unreadable cause matters again, now that PERFORM is on the
        # table.
        assert "Mute Switch" in joined

    def test_solo_is_reported_as_the_cause_it_is(self):
        parts = [self._part(n, n - 1) for n in range(1, 17)]
        report = " ".join(
            self._state(mode=SoundMode.PERFORM, parts=parts, solo=3).silence_report()
        )
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
        report = self._state(mode=SoundMode.PERFORM, parts=parts).silence_report()
        assert "Nothing is silencing any part" in report[0]

    def test_unassigned_channels_are_distinguished_from_muted_ones(self):
        """A channel no part listens on is not a muted channel."""
        parts = [self._part(1, 0), self._part(2, 1)]
        report = " ".join(
            self._state(mode=SoundMode.PERFORM, parts=parts).silence_report()
        )
        assert "Nothing audible is assigned to channel" in report
        assert "not muted, just unused" in report

    def test_level_zero_and_rx_off_are_told_apart(self):
        parts = [self._part(1, 0, rx=False), self._part(2, 1, level=0)]
        report = " ".join(
            self._state(mode=SoundMode.PERFORM, parts=parts).silence_report()
        )
        assert "Part 1: RX SWITCH is OFF" in report
        assert "Part 2: LEVEL is 0" in report

    def test_the_unreadable_mute_switch_is_always_named(self):
        """The report must never read as exhaustive, in any state."""
        parts = [self._part(n, n - 1) for n in range(1, 17)]
        for state in (
            self._state(mode=SoundMode.PERFORM, parts=parts),
            self._state(mode=SoundMode.PERFORM, parts=parts, solo=3),
            self._state(mode=SoundMode.PERFORM, parts=[self._part(1, 0, rx=False)]),
        ):
            assert "Mute Switch" in " ".join(state.silence_report())

    def test_patch_mode_with_unread_parts_does_not_mention_the_mute_switch(self):
        """Nothing to check on the panel yet: the parts are not in use."""
        report = " ".join(self._state(mode=SoundMode.PATCH).silence_report())
        assert "Mute Switch" not in report

    def test_it_never_sends_you_to_a_panel_the_module_does_not_have(self):
        """The XV-2020 is a half-rack module with a three-digit LED.

        Its four controls reach a short list of parameters (OM p. 116), and
        Receive Switch, Mute Switch and Solo are not on it. Telling somebody
        to change those "on the front panel" is advice that cannot be
        followed on this instrument.
        """
        parts = [self._part(1, 0, rx=False)]
        for state in (
            self._state(mode=SoundMode.PERFORM, parts=parts),
            self._state(mode=SoundMode.PATCH, parts=parts),
        ):
            report = " ".join(state.silence_report())
            assert "front panel" not in report
            assert "XV-2020 Editor" in report

    def test_it_says_which_settings_the_module_itself_can_reach(self):
        parts = [self._part(1, 0, rx=False)]
        report = " ".join(
            self._state(mode=SoundMode.PERFORM, parts=parts).silence_report()
        )
        # Reachable on the module...
        assert "[VALUE]" in report and "[PATCH RX CH]" in report
        # ...and the ones that are not, said as such.
        assert "NOT in the module's own parameter list" in report


class TestWritableOffsetsAgree:
    """Three places name the same six Performance Part offsets.

    They have to agree: a cell the screen offers but the bridge refuses is a
    dialog that fails only after the user has committed to it, and a demo
    that accepts a different set lets that drift ship untested.
    """

    def test_the_screen_offers_nothing_the_bridge_refuses(self):
        """One-directional on purpose.

        A cell the screen offers and the bridge refuses is a dialog that
        fails after the user has committed to it. The reverse is fine: the
        bridge allows velocity sensitivity, which has no column because the
        row was already wide enough.
        """
        from rxved.app import EDITABLE_PART_COLUMNS
        from xv.bridge import XvBridge

        offered = {offset for offset, _, _, _ in EDITABLE_PART_COLUMNS.values()}
        assert offered <= set(XvBridge.WRITABLE_PART_OFFSETS)

    def test_the_demo_accepts_exactly_what_the_bridge_does(self):
        from rxved.demo import DemoBridge
        from xv.bridge import XvBridge

        assert set(DemoBridge._PART_FIELDS) == set(XvBridge.WRITABLE_PART_OFFSETS)

    def test_nothing_outside_the_temporary_performance_is_writable(self):
        """The allowlist is the whole safety argument, so assert its shape.

        Every writable offset is a Performance **Part** parameter, and the
        address they are used with is 10 00 <20+n-1> <offset> -- Temporary
        Performance. Nothing here can reach a stored performance, a user
        patch, or the System area.
        """
        from xv.bridge import XvBridge

        assert set(XvBridge.WRITABLE_PART_OFFSETS) == {
            0x00,
            0x01,
            0x04,
            0x05,
            0x06,
            0x07,  # MIDI settings
            0x08,
            0x09,
            0x0A,
            0x0B,
            0x0D,  # pan, tune, poly
            0x15,
            0x16,
            0x17,
            0x18,  # octave, vel, range
            0x1B,
            0x1C,
            0x1D,
            0x1E,
            0x1F,
            0x20,  # mute, sends, routing
        }
        # Every one is inside the Performance Part block, which is 49 bytes.
        assert max(XvBridge.WRITABLE_PART_OFFSETS) < 49
        for offset, (label, low, high) in XvBridge.WRITABLE_PART_OFFSETS.items():
            assert 0 <= low <= high <= 127, label


class TestRangeCollapsing:
    """Thirteen parts sharing one reason is one line, not thirteen.

    The table above the report already shows each part's rx column, so a
    line per part restates it -- and buries the lines that say something
    the table cannot.
    """

    def test_contiguous_parts_collapse(self):
        from xv.bridge import _ranges

        assert _ranges([3, 4, 5, 6]) == "3-6"

    def test_gaps_are_kept(self):
        from xv.bridge import _ranges

        assert _ranges([1, 2, 5, 7, 8, 9]) == "1-2, 5, 7-9"

    def test_a_single_number_is_not_a_range(self):
        from xv.bridge import _ranges

        assert _ranges([7]) == "7"

    def test_it_is_order_and_duplicate_proof(self):
        from xv.bridge import _ranges

        assert _ranges([9, 3, 4, 3, 5]) == "3-5, 9"

    def test_the_singular_is_used_for_one_part(self):
        state = TestSilenceReport()._state(
            mode=SoundMode.PERFORM, parts=[TestSilenceReport()._part(1, 0, rx=False)]
        )
        assert any(line.startswith("Part 1:") for line in state.silence_report())


class TestMuteIsReadable:
    """The Mute Switch IS in the parameter address map, at offset 00 1B.

    An earlier version of this report said it was not, and told people to
    check a front panel the XV-2020 does not have. It is at Performance Part
    offset 00 1B (OM p. 146), between Keyboard Fade Width Upper and Part Dry
    Send Level, and rxved both reads and writes it.
    """

    def _part(self, **kw):
        base = dict(part=1, receive_channel=0, msb=87, lsb=64, program_change=0)
        base.update(kw)
        return PartState(**base)

    def test_a_muted_part_is_reported(self):
        assert self._part(mute=True).silence_reason() == "MUTE is on"

    def test_mute_is_distinct_from_rx_off(self):
        assert self._part(receive_switch=False).silence_reason() == ("RX SWITCH is OFF")

    def test_rx_off_is_reported_before_mute(self):
        """Both true: name the one nearest the sound's path in."""
        both = self._part(receive_switch=False, mute=True)
        assert both.silence_reason() == "RX SWITCH is OFF"

    def test_all_sends_at_zero_is_silence_too(self):
        part = self._part(dry=0, chorus=0, reverb=0)
        assert part.silence_reason() == "DRY, CHO and REV sends are all 0"

    def test_a_wet_only_part_is_not_silent(self):
        """Running fully wet is a legitimate setting, not a fault."""
        assert self._part(dry=0, chorus=0, reverb=90).silence_reason() is None

    def test_the_report_no_longer_claims_mute_is_unreadable(self):
        setup = SetupState(
            mode=SoundMode.PERFORM,
            patch_msb=87,
            patch_lsb=0,
            patch_program=0,
            performance_msb=85,
            performance_lsb=0,
            performance_program=0,
        )
        state = DeviceState(
            setup=setup,
            channels=SystemChannels(patch_receive=0, performance_control=14),
            parts=(self._part(mute=True),),
            common=PerformanceCommon(name="X", solo=None),
        )
        report = " ".join(state.silence_report())
        assert "MUTE is on" in report
        assert "absent from the parameter address map" not in report


class TestOutputAssign:
    def test_values_this_model_ignores_are_named_not_hidden(self):
        """A performance from an XV-5080 can carry output 6.

        Showing it as "6*" explains a part that makes no sound here;
        showing a blank, or silently normalising it to A, does not.
        """
        from xv.bridge import OUTPUT_ASSIGN, OUTPUT_ASSIGN_ON_XV2020

        assert OUTPUT_ASSIGN[10] == "6*"
        assert 10 not in OUTPUT_ASSIGN_ON_XV2020
        # Every starred entry is one this model ignores, and vice versa.
        starred = {value for value, name in OUTPUT_ASSIGN.items() if name.endswith("*")}
        assert starred == set(OUTPUT_ASSIGN) - OUTPUT_ASSIGN_ON_XV2020

    def test_the_map_covers_the_whole_documented_range(self):
        from xv.bridge import OUTPUT_ASSIGN

        assert set(OUTPUT_ASSIGN) == set(range(14))


class TestChannelReceiveSwitches:
    """Performance MIDI: whether a channel acts on what rxved sends.

    Per MIDI channel, not per part -- the manual marks these "+" where the
    per-part parameters are marked "#" (OM p. 74), and the address map gives
    them their own sixteen blocks at 10 00 <10+ch> 00.

    This is the failure this program is most able to cause: rxved sends Bank
    Select and Program Change, and a channel with either switch off acts on
    neither, silently.
    """

    def _state(self, midi):
        setup = SetupState(
            mode=SoundMode.PERFORM,
            patch_msb=87,
            patch_lsb=0,
            patch_program=0,
            performance_msb=85,
            performance_lsb=0,
            performance_program=0,
        )
        return DeviceState(
            setup=setup,
            channels=SystemChannels(patch_receive=0, performance_control=14),
            parts=(
                PartState(part=1, receive_channel=0, msb=87, lsb=64, program_change=0),
            ),
            common=PerformanceCommon(name="X", solo=None),
            midi=tuple(midi),
        )

    def test_bank_select_off_is_called_out_as_the_wrong_patch(self):
        """Not "it does nothing" -- the PC still lands, in the old bank."""
        entry = ChannelMidi(channel=0, bank_select=False)
        assert "whatever bank the part is already on" in (entry.selection_problem())

    def test_program_change_off_is_a_different_message(self):
        entry = ChannelMidi(channel=0, program_change=False)
        assert entry.selection_problem() == "ignores Program Change"

    def test_both_off_is_said_once(self):
        entry = ChannelMidi(channel=0, program_change=False, bank_select=False)
        assert entry.selection_problem() == ("ignores Program Change and Bank Select")

    def test_a_healthy_channel_has_no_problem(self):
        assert ChannelMidi(channel=0).selection_problem() is None
        assert ChannelMidi(channel=0).accepts_selection

    def test_the_report_names_the_channels(self):
        midi = [
            ChannelMidi(channel=c, bank_select=c not in (2, 3, 4)) for c in range(16)
        ]
        report = " ".join(self._state(midi).silence_report())
        assert "Channels 3-5: ignores Bank Select" in report

    def test_selection_problem_is_none_when_the_blocks_are_unread(self):
        """Unread is not "fine": say nothing rather than something false."""
        assert self._state([]).selection_problem(0) is None

    def test_unread_blocks_produce_no_findings(self):
        report = " ".join(self._state([]).silence_report())
        assert "ignores" not in report

    def test_the_screen_and_the_bridge_agree_on_channel_offsets(self):
        """Same trap as the part columns: offered here, refused there."""
        from rxved.app import EDITABLE_CHANNEL_COLUMNS
        from xv.bridge import XvBridge

        offered = {offset for offset, _, _, _ in EDITABLE_CHANNEL_COLUMNS.values()}
        assert offered <= set(XvBridge.WRITABLE_CHANNEL_OFFSETS)

    def test_the_demo_accepts_every_channel_offset_the_bridge_does(self):
        from rxved.demo import DemoBridge
        from xv.bridge import XvBridge

        assert set(DemoBridge._CHANNEL_FIELDS) == set(XvBridge.WRITABLE_CHANNEL_OFFSETS)

    def test_channel_writes_stay_in_the_temporary_performance(self):
        """The safety argument again: 10 00 <10+ch> <offset>, nothing else."""
        from xv.bridge import XvBridge

        assert set(XvBridge.WRITABLE_CHANNEL_OFFSETS) == set(range(0x0C))


class TestDisplayVersusWire:
    """Every column where the number shown is not the byte sent.

    This is the project's central hazard applied to the tone columns: pan,
    octave and the tunes are stored biased by 64, so a screen that forgot
    the bias would show plausible numbers and write wrong ones.
    """

    def test_every_biased_column_round_trips(self):
        from rxved.app import _BIAS

        for column, bias in _BIAS.items():
            for display in (-64, -1, 0, 1, 63):
                wire = display + bias
                assert wire - bias == display, column

    def test_the_channel_column_is_the_one_that_goes_the_other_way(self):
        """ch is 1-16 shown and 0-15 sent; the rest are signed round 64."""
        from rxved.app import _BIAS

        assert _BIAS["ch"] == -1
        assert 1 + _BIAS["ch"] == 0  # display 1 -> wire 0
        assert 16 + _BIAS["ch"] == 15

    def test_pan_spans_the_whole_byte(self):
        from rxved.app import _BIAS, EDITABLE_PART_COLUMNS

        _o, _label, low, high = EDITABLE_PART_COLUMNS["pan"]
        assert low + _BIAS["pan"] == 0
        assert high + _BIAS["pan"] == 127

    def test_each_display_range_lands_inside_the_wire_range(self):
        """The two allowlists are independent, and must not disagree."""
        from rxved.app import _BIAS, EDITABLE_PART_COLUMNS
        from xv.bridge import XvBridge

        for column, (offset, _label, low, high) in EDITABLE_PART_COLUMNS.items():
            bias = _BIAS.get(column, 0)
            _wire_label, wire_low, wire_high = XvBridge.WRITABLE_PART_OFFSETS[offset]
            assert low + bias >= wire_low, column
            assert high + bias <= wire_high, column

    def test_note_names_match_the_manuals_numbering(self):
        """The XV-2020 counts C-1 as note 0, so 60 is C4 (OM p. 73)."""
        from xv.params import note_name

        assert note_name(0) == "C-1"
        assert note_name(60) == "C4"
        assert note_name(127) == "G9"
