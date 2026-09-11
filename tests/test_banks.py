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

"""The bank table, against the numbers the manuals print."""

import pytest

from xv import banks


class TestPrintedTable:
    """Rows checked one by one against OM pp. 40-41 and p. 136."""

    @pytest.mark.parametrize("bank_id,msb,lsb,count", [
        ("USER", 87, 0, 128),
        ("PST-A", 87, 64, 128),
        ("PST-B", 87, 65, 128),
        ("PST-C", 87, 66, 128),
        ("PST-D", 87, 67, 128),
        ("R-USER", 86, 0, 4),
        ("R-PST-A", 86, 64, 4),
        ("R-PST-B", 86, 65, 4),
        ("P-USER", 85, 0, 64),
        ("P-PST-A", 85, 64, 32),
        ("P-PST-B", 85, 65, 32),
        ("GM", 121, 0, 128),
    ])
    def test_row(self, bank_id, msb, lsb, count):
        entry = banks.bank(bank_id)
        assert (entry.msb, entry.lsb, entry.count) == (msb, lsb, count)

    def test_the_manuals_own_worked_example(self):
        """OM p. 40: MSB 87, LSB 65, PC 18 selects Preset B patch 18."""
        found = banks.lookup(87, 65, 18)
        assert found is not None
        assert found.bank_id == "PST-B"
        # The manual's step 6 says "send a Program Change with a value of 18"
        # and "the Patch number appearing in the display changes to 18". Both
        # are quoted as 18, which is the one place the manual is loose --
        # rxved keeps the two apart, so this pins which is which.
        assert found.program_change == 18
        assert found.number == 19

    def test_unknown_triples_are_none_not_an_error(self):
        """A shared MIDI stream is mostly not addressed to us."""
        assert banks.lookup(0, 0, 0) is None


class TestNumbering:
    """The trap this module exists to keep out of the UI."""

    def test_display_number_is_one_more_than_the_wire_byte(self):
        for slot in banks.slots("PST-A"):
            assert slot.number == slot.program_change + 1

    def test_first_slot_is_display_one_and_wire_zero(self):
        first = banks.slots("USER")[0]
        assert (first.number, first.program_change) == (1, 0)

    def test_last_slot_is_display_128_and_wire_127(self):
        last = banks.slots("USER")[-1]
        assert (last.number, last.program_change) == (128, 127)

    def test_no_bank_ever_emits_a_program_change_of_128(self):
        """MIDI has no program 128; a bank that produced one is off by one."""
        for bank_id in banks.bank_ids():
            for slot in banks.slots(bank_id):
                assert 0 <= slot.program_change <= 127


class TestGmRhythm:
    """The one bank whose program changes are not contiguous."""

    def test_uses_the_gm2_drum_kit_program_numbers(self):
        pcs = [s.program_change for s in banks.slots("R-GM")]
        assert pcs == [p - 1 for p in banks.GM_RHYTHM_PROGRAMS]

    def test_electronic_and_tr808_are_adjacent(self):
        """25 and 26, which an "every 8th program" model gets wrong."""
        assert 25 in banks.GM_RHYTHM_PROGRAMS
        assert 26 in banks.GM_RHYTHM_PROGRAMS

    def test_display_numbers_stay_contiguous(self):
        assert [s.number for s in banks.slots("R-GM")] == list(range(1, 10))


class TestSelectMessages:
    def test_order_is_msb_then_lsb_then_program_change(self):
        """The device latches the bank on the PC, so order is not cosmetic."""
        slot = banks.slot("PST-B", 29)
        messages = slot.select_messages(channel=0)
        assert messages == [
            [0xB0, 0, 87],
            [0xB0, 32, 65],
            [0xC0, 28],
        ]

    def test_channel_is_encoded_in_the_status_byte(self):
        messages = banks.slot("USER", 1).select_messages(channel=15)
        assert messages[0][0] == 0xBF
        assert messages[2][0] == 0xCF

    @pytest.mark.parametrize("channel", [-1, 16, 99])
    def test_rejects_a_channel_outside_zero_to_fifteen(self, channel):
        with pytest.raises(ValueError):
            banks.slot("USER", 1).select_messages(channel=channel)


class TestSrx:
    """Roland Supplemental Note SN 132, row by row. See xv/banks.py."""

    @pytest.mark.parametrize("card_id,base,pages,patches", [
        ("SRX-01", 0, 1, 41),
        ("SRX-02", 1, 1, 50),
        ("SRX-03", 2, 1, 128),
        ("SRX-04", 3, 1, 128),
        ("SRX-05", 4, 3, 312),
        ("SRX-06", 7, 4, 449),
        ("SRX-07", 11, 4, 475),
        ("SRX-08", 15, 4, 448),
        ("SRX-09", 19, 4, 414),
        ("SRX-10", 23, 1, 100),
        ("SRX-11", 24, 1, 42),
        ("SRX-12", 26, 1, 105),
        ("SRX-97", 97, 1, 12),
        ("SRX-98", 98, 1, 78),
    ])
    def test_card_allocation(self, card_id, base, pages, patches):
        card = banks.srx_card(card_id)
        assert card.patch_lsb_base == base
        assert len(card.patch_lsbs) == pages
        assert card.patch_count == patches

    def test_lsb_spans_are_derived_not_fixed_at_four(self):
        """SRX-02 uses one LSB and SRX-05 three.

        A fixed four-per-card span would have each of these claiming LSBs
        that belong to the next card in the series.
        """
        assert list(banks.srx_card("SRX-02").patch_lsbs) == [1]
        assert list(banks.srx_card("SRX-05").patch_lsbs) == [4, 5, 6]

    def test_lsb_25_belongs_to_nothing(self):
        """SRX-11 ends at 24 and SRX-12 starts at 26.

        The hole is in Roland's own table. It is the reason the allocation
        is transcribed rather than computed: any formula fitted to the low
        boards puts SRX-12 at 25 and selects the wrong board's patches.
        """
        assert all(25 not in card.patch_lsbs for card in banks.SRX_CARDS)
        assert list(banks.srx_card("SRX-11").patch_lsbs) == [24]
        assert list(banks.srx_card("SRX-12").patch_lsbs) == [26]

    def test_boards_without_rhythm_sets_have_none(self):
        """02, 04, 10, 11, 12, 97 and 98 have no rhythm row in SN 132."""
        for card_id in ("SRX-02", "SRX-04", "SRX-10", "SRX-11", "SRX-12",
                        "SRX-97", "SRX-98"):
            assert banks.srx_card(card_id).rhythm_lsb is None

    def test_boards_the_xv2020_cannot_use_are_not_banks(self):
        """SN 132 lists every SRX board; this machine cannot play them all.

        SRX-98's manual says in as many words that no products other than
        the ones it names may use it, and the XV-2020 is not among them.
        Offering a browser slot that can never be addressed is worse than
        leaving it out -- but the row stays, because it is correct for the
        hosts that can.
        """
        excluded = [c.id for c in banks.SRX_CARDS if not c.xv2020]
        assert excluded == ["SRX-97", "SRX-98"]
        for card_id in excluded:
            assert banks.srx_card(card_id).patch_count > 0
            with pytest.raises(LookupError):
                banks.bank(f"{card_id}-1")
        for bank in banks.SRX_BANKS:
            assert not bank.id.startswith(("SRX-97", "SRX-98"))

    def test_the_series_allocation_does_not_overlap(self):
        """Each card starts where the previous one stopped."""
        used = {}
        for card in banks.SRX_CARDS:
            for lsb in card.patch_lsbs:
                assert lsb not in used, (
                    f"LSB {lsb} claimed by both {used.get(lsb)} and {card.id}")
                used[lsb] = card.id

    def test_rhythm_lsb_is_the_cards_first_patch_lsb(self):
        for card in banks.SRX_CARDS:
            if card.rhythm_lsb is not None:
                assert card.rhythm_lsb == card.patch_lsb_base

    def test_a_card_with_no_rhythm_sets_gets_no_rhythm_bank(self):
        """SRX-02 is a piano card; an empty bank would be a dead row."""
        assert banks.srx_card("SRX-02").rhythm_bank() is None
        assert "SRX-02-R" not in banks.bank_ids()

    def test_the_last_page_is_short_not_padded(self):
        # SRX-07 has 475 patches: 128 + 128 + 128 + 91.
        assert banks.bank("SRX-07-4").count == 91
        assert sum(banks.bank(f"SRX-07-{n}").count for n in range(1, 5)) == 475

    def test_unknown_card_names_the_ones_it_has(self):
        with pytest.raises(LookupError, match="SRX-07"):
            banks.srx_card("SRX-99")


class TestIntegrity:
    def test_bank_ids_are_unique(self):
        ids = banks.bank_ids()
        assert len(ids) == len(set(ids))

    def test_no_two_banks_share_an_msb_lsb_pair(self):
        """They would be the same bank, and lookup() would pick arbitrarily."""
        seen = {}
        for entry in banks.BANKS:
            key = (entry.msb, entry.lsb)
            assert key not in seen, f"{entry.id} collides with {seen.get(key)}"
            seen[key] = entry.id

    def test_every_slot_round_trips_through_lookup(self):
        for bank_id in banks.bank_ids():
            for slot in banks.slots(bank_id):
                found = banks.lookup(slot.msb, slot.lsb, slot.program_change)
                assert found is not None
                assert found.bank_id == bank_id
                assert found.number == slot.number

    def test_slot_keys_are_unique_and_stable(self):
        keys = [s.key for b in banks.bank_ids() for s in banks.slots(b)]
        assert len(keys) == len(set(keys))
        assert banks.slot("PST-B", 29).key == "PST-B:029"

    def test_out_of_range_slot_says_what_the_bank_holds(self):
        with pytest.raises(LookupError, match="128"):
            banks.slot("USER", 200)


class TestSystemChannels:
    """The two receive channels, and the OFF case."""

    def test_performance_and_patch_are_separate(self):
        from xv.bridge import SystemChannels

        channels = SystemChannels(patch_receive=0, performance_control=14)
        assert channels.for_kind(banks.Kind.PATCH) == 0
        assert channels.for_kind(banks.Kind.RHYTHM) == 0
        assert channels.for_kind(banks.Kind.PERFORMANCE) == 14

    def test_display_numbers_are_one_based(self):
        from xv.bridge import SystemChannels

        channels = SystemChannels(patch_receive=0, performance_control=14)
        assert (channels.patch_display, channels.performance_display) == (1, 15)

    def test_performance_off_has_no_channel(self):
        """With the control channel OFF, performances are unreachable.

        Reported as None so the caller refuses rather than picking a channel
        and sending messages the synth throws away.
        """
        from xv.bridge import SystemChannels

        channels = SystemChannels(patch_receive=0, performance_control=None)
        assert channels.performance_display is None
        assert channels.for_kind(banks.Kind.PERFORMANCE) is None
        assert channels.for_kind(banks.Kind.PATCH) == 0


class TestDeviceState:
    """What a Bank Select on a given channel would actually hit."""

    @staticmethod
    def _state(mode, *, patch_rx=0, perf_ctrl=15, parts=()):
        from xv.bridge import DeviceState, PartState, SetupState, SystemChannels

        return DeviceState(
            setup=SetupState(mode=mode, patch_msb=87, patch_lsb=0,
                             patch_program=0, performance_msb=85,
                             performance_lsb=0, performance_program=4),
            channels=SystemChannels(patch_receive=patch_rx,
                                    performance_control=perf_ctrl),
            parts=parts,
        )

    @staticmethod
    def _part(part, channel, pc):
        from xv.bridge import PartState

        return PartState(part=part, receive_channel=channel, msb=87, lsb=64,
                         program_change=pc)

    def test_patch_mode_is_single_channel(self):
        from xv.bridge import SoundMode

        state = self._state(SoundMode.PATCH, patch_rx=0)
        assert "the patch" in state.describes(0)
        # Every other channel selects nothing at all, and saying so is more
        # use than showing a blank.
        assert "nothing" in state.describes(5)

    def test_perform_mode_maps_channels_to_parts(self):
        from xv.bridge import SoundMode

        state = self._state(SoundMode.PERFORM,
                            parts=(self._part(1, 0, 0), self._part(2, 3, 1)))
        assert "part 1" in state.describes(0)
        assert "part 2" in state.describes(3)
        assert "no part listens" in state.describes(7)

    def test_layered_parts_on_one_channel_are_all_reported(self):
        """Parts freely share a channel; the real machine had three on one."""
        from xv.bridge import SoundMode

        state = self._state(
            SoundMode.PERFORM,
            parts=(self._part(1, 0, 0), self._part(2, 0, 1),
                   self._part(3, 0, 2)),
        )
        assert len(state.parts_on(0)) == 3
        described = state.describes(0)
        assert described.startswith("ch 1: ")
        # Every layered part named, and the consequence spelled out -- a
        # Program Change on a shared channel moves all of them at once.
        for part in (1, 2, 3):
            assert f"{part}=" in described
        assert "moves all 3" in described

    def test_describes_short_omits_the_channel_prefix(self):
        """For callers that have already said which channel they mean."""
        from xv.bridge import SoundMode

        state = self._state(SoundMode.PERFORM, parts=(self._part(1, 0, 0),))
        assert state.describes(0) == "ch 1: " + state.describes_short(0)

    def test_the_performance_control_channel_selects_the_performance(self):
        from xv.bridge import SoundMode

        state = self._state(SoundMode.PERFORM, perf_ctrl=14,
                            parts=(self._part(1, 0, 0),))
        assert "whole performance" in state.describes(14)

    def test_gm_modes_count_as_multitimbral(self):
        from xv.bridge import SoundMode

        for mode in (SoundMode.PERFORM, SoundMode.GM1, SoundMode.GM2,
                     SoundMode.GS):
            assert self._state(mode).setup.multitimbral
        assert not self._state(SoundMode.PATCH).setup.multitimbral

    def test_setup_decodes_the_current_selection_as_a_slot(self):
        from xv.bridge import SoundMode

        setup = self._state(SoundMode.PATCH).setup
        assert setup.patch_slot is not None
        assert setup.patch_slot.bank_id == "USER"
        assert setup.performance_slot.bank_id == "P-USER"

    def test_an_unclaimed_triple_shows_its_numbers(self):
        """"Unrecognised" alone is useless exactly when the bytes matter."""
        from xv.bridge import SetupState, SoundMode, SystemChannels, DeviceState

        state = DeviceState(
            setup=SetupState(mode=SoundMode.PATCH, patch_msb=87, patch_lsb=3,
                             patch_program=5, performance_msb=85,
                             performance_lsb=0, performance_program=0),
            channels=SystemChannels(patch_receive=0, performance_control=15),
        )
        described = state.describes_short(0)
        assert "87" in described and "3" in described and "5" in described
        assert "no bank claims" in described

    def test_an_unrecognised_bank_is_none_not_a_crash(self):
        """An SRX board rxved has no row for, or a mode it did not expect."""
        from xv.bridge import SetupState

        setup = SetupState(mode=1, patch_msb=99, patch_lsb=99,
                           patch_program=0, performance_msb=99,
                           performance_lsb=99, performance_program=0)
        assert setup.patch_slot is None
        assert setup.performance_slot is None
