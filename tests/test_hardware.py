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

"""Tests against a real XV-2020. **Opt-in: see :mod:`tests.conftest`.**

Everything else in this suite runs against :mod:`rxved.demo`, which is the
point of it -- the suite is fast, needs no hardware, and passes on a machine
with no MIDI stack at all. That is also its limit: a demo synth agrees with
whatever rxved believes, so every offset below the API surface was only ever
checked against rxved's own transcription of Roland's documents.

This file is the other half. It asks the machine, and the machine's answer
is the one that cannot be transcribed wrong:

* **Read-only, structural.** Every offset and size in
  :data:`~xv.params.PERFORMANCE_BLOCKS`, round-tripped against the block the
  synth reports. This is the check the editor cross-check cannot do --
  ``tools/check_editor_map.py`` compares against Roland's *editor script*,
  which is a third transcription, not the instrument.
* **Writes, read back and restored.** Temporary Performance first, because
  that is the edit buffer a power cycle discards, and the Sound Mode byte
  last, because that one is global state and is the reason this file exists
  rather than being in the ordinary suite.

**What these tests deliberately do not do.** They never assert on a patch,
rhythm or performance *name*: Roland's ROM contents are not this project's
to redistribute, and a fixture naming them would be a copy of them
(CLAUDE.md). Slot *numbers* are fine -- a number identifies a slot, not a
work. They never touch a user slot, never rename anything, and never send a
scan: the operations that make the instrument play stay behind their own
confirmations, which is not something a test should bypass.
"""

import pytest
from xv import banks
from xv import params
from xv.bridge import PerformanceCommon
from xv.bridge import PerformanceFx
from xv.bridge import SetupState
from xv.bridge import TEMPORARY_PERFORMANCE

#: A part level every part can be parked at while a write is verified.
#: Not 0, which silences the part and looks exactly like a failed write.
PARKED_PART_LEVEL = 100


class TestHardwareBlockSizes:
    """Every block in the temporary performance, at its declared size.

    ``read_performance_blocks`` raises rather than padding a short block, so
    a declaration that is wrong anywhere -- a size, an address, a
    mis-paired heading -- fails loudly here instead of quietly giving back
    one byte too few for a later write to put in the wrong place.

    The sizes came from the "Total Size" each section of the address map
    prints, and were cross-checked against the editor script. The script is
    a second human reading; this is the machine.
    """

    def test_every_block_comes_back_at_the_size_the_map_declares(self, synth):
        blocks = synth.read_performance_blocks(TEMPORARY_PERFORMANCE)
        declared = {name: size for name, _address, size in params.PERFORMANCE_BLOCKS}
        assert set(blocks) == set(declared), (
            "the synth offered a different set of blocks than rxved asks for"
        )
        for name, want in sorted(declared.items()):
            assert len(blocks[name]) == want, (
                f"block {name!r} came back {len(blocks[name])} bytes, "
                f"rxved declares {want}"
            )

    def test_the_blocks_are_the_ones_a_real_performance_needs(self, synth):
        """36 blocks: common, the three effects, 16 MIDI, 16 parts."""
        blocks = synth.read_performance_blocks(TEMPORARY_PERFORMANCE)
        assert len(blocks) == 36
        for index in range(16):
            assert f"midi{index + 1}" in blocks
            assert f"part{index + 1}" in blocks


class TestHardwareReadOffsets:
    """The reads the browser and the CLI depend on.

    Structural assertions only: a field is present, a count is right, a
    value is inside the range the manual prints. A name would be Roland's
    ROM and is not asserted on (CLAUDE.md).
    """

    def test_setup_reports_a_mode_the_manual_lists(self, synth):
        setup = synth.read_setup()
        assert isinstance(setup, SetupState)
        assert setup.mode in params.SOUND_MODES, (
            f"the synth reported sound mode {setup.mode}, which is not one "
            f"of {params.SOUND_MODES}"
        )
        assert setup.mode_name, "a mode with no name cannot be shown"

    def test_the_patch_and_performance_selectors_are_inside_their_ranges(self, synth):
        setup = synth.read_setup()
        for label, value in (
            ("performance MSB", setup.performance_msb),
            ("performance LSB", setup.performance_lsb),
            ("performance PC", setup.performance_program),
            ("patch MSB", setup.patch_msb),
            ("patch LSB", setup.patch_lsb),
            ("patch PC", setup.patch_program),
        ):
            assert 0 <= value <= 127, f"setup {label} is {value}"

    def test_every_part_reads_back_sixteen_rows(self, synth):
        parts = synth.read_parts()
        assert len(parts) == 16
        assert [part.part for part in parts] == list(range(1, 17))

    def test_one_part_block_is_fortynine_bytes(self, synth):
        """``00 21`` through the vibrato delay; the size the map declares."""
        part = synth.read_part(1)
        assert part.part == 1
        # Absence of an exception above is half the assertion; the other half
        # is that the fields rxved pulls out are in range rather than noise.
        assert 0 <= part.receive_channel <= 15
        assert 0 <= part.level <= 127

    def test_the_performance_common_reads_back_with_all_its_fields(self, synth):
        common = synth.read_performance_common()
        assert isinstance(common, PerformanceCommon)
        assert 0 <= len(common.name) <= 12, f"name is {len(common.name)} characters"
        assert len(common.voice_reserves) == 16, (
            "Voice Reserve is one byte per part, parts 1-16"
        )
        for index, reserve in enumerate(common.voice_reserves):
            assert 0 <= reserve <= 64, f"part {index + 1} reserve is {reserve}"

    def test_the_effects_blocks_read_back_whole(self, synth):
        fx = synth.read_performance_fx()
        assert isinstance(fx, PerformanceFx)
        assert fx.mfx_type in params.MFX_TYPES, (
            f"the synth reported MFX type {fx.mfx_type}, which rxved has no "
            "name for -- a new algorithm in a firmware update, or a "
            "mis-transcribed list"
        )
        assert len(fx.mfx_block) == 145
        assert len(fx.chorus_block) == 52
        assert len(fx.reverb_block) == 83

    def test_each_channels_midi_block_reads_back_twelve_bytes(self, synth):
        for channel in range(16):
            entry = synth.read_performance_midi(channel)
            assert entry.channel == channel


class TestHardwareTemporaryWrite:
    """A write to Temporary Performance, read back and put back.

    The edit buffer, not a stored performance: a power cycle discards all of
    it, which is what makes it safe to exercise here. The value is restored
    regardless of the assertion's outcome, so a failure leaves the synth as
    it was rather than at whatever level the test happened to stop at.
    """

    def test_a_part_level_write_is_read_back(self, synth):
        part_number = 1
        before = synth.read_part(part_number).level
        try:
            landed = synth.write_part_param(part_number, 0x07, PARKED_PART_LEVEL)
            assert landed == PARKED_PART_LEVEL, (
                f"wrote part level {PARKED_PART_LEVEL}, the synth reports "
                f"{landed}. A DT1 is unacknowledged, so this is the synth "
                "declining it -- or a wrong offset, which in a Roland map "
                "does not fail but writes something else."
            )
        finally:
            if before != PARKED_PART_LEVEL:
                synth.write_part_param(part_number, 0x07, before)
        assert synth.read_part(part_number).level == before

    def test_a_part_offset_write_lands_in_the_offset_it_names(self, synth):
        """Writing one byte must not disturb the byte next to it.

        The whole reason ``WRITABLE_PART_OFFSETS`` is an allowlist: a Roland
        map does not fail on a wrong address, it writes something else. So a
        write is checked by reading the block back and comparing, per block.
        """
        part_number = 4
        before = synth.read_performance_blocks(TEMPORARY_PERFORMANCE)[
            f"part{part_number}"
        ]
        try:
            synth.write_part_param(part_number, 0x07, PARKED_PART_LEVEL)
            after = synth.read_performance_blocks(TEMPORARY_PERFORMANCE)[
                f"part{part_number}"
            ]
            assert after[0x07] == PARKED_PART_LEVEL
            differing = [
                offset
                for offset in range(len(before))
                if offset != 0x07 and after[offset] != before[offset]
            ]
            assert not differing, (
                f"writing offset 00 07 also changed {differing}: "
                + ", ".join(f"{o:#04x}" for o in differing)
            )
        finally:
            if before[0x07] != PARKED_PART_LEVEL:
                synth.write_part_param(part_number, 0x07, before[0x07])


class TestHardwareSoundMode:
    """The Sound Mode write, and the only one of the suite that is global.

    ``M`` and ``rxvcli mode`` write a byte in the Setup block, which is not
    the temporary area and survives a power cycle -- so this is the one
    place the hardware suite changes what the machine *is* rather than what
    its edit buffer holds. Two things keep it that way:

    * it moves between PATCH and PERFORM only, which is what the front
      panel's [VALUE] knob does and costs nothing to undo; GM1, GM2 and GS
      initialise the sound generator and store nothing (OM p. 96), so they
      are refused here and left to the manual protocol in
      docs/RESOLUTION_NOTES.md §9b.
    * whichever of the two it started in is put back, in a ``finally``.
    """

    def test_the_mode_writes_and_reads_back(self, synth):
        before = synth.read_setup().mode
        try:
            target = 1 if before != 1 else 2
            landed = synth.write_sound_mode(target)
            assert landed == target, (
                f"asked for {params.SOUND_MODES[target]}, the synth reports {landed}"
            )
            assert synth.read_setup().mode == target
        finally:
            if synth.read_setup().mode != before:
                synth.write_sound_mode(before)
        assert synth.read_setup().mode == before

    def test_the_mode_is_one_of_the_five_the_manual_lists(self, synth):
        """A read-only framing of the same fact, so the suite says something
        even on a machine where writing the mode is not wanted."""
        assert synth.read_setup().mode in params.SOUND_MODES

    def test_gm_is_refused_by_the_bridge_itself(self, synth):
        """Not skipped: the bridge refuses an out-of-range mode, and this
        asserts it rather than trusting that nothing sent one."""
        with pytest.raises(ValueError):
            synth.write_sound_mode(0)
        with pytest.raises(ValueError):
            synth.write_sound_mode(6)
