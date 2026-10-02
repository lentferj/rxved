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

"""The wire codec, against the frames the manual prints."""

import pytest

from xv import messages as m


class TestDeviceId:
    """The bug that cost a hardware session: see :func:`m.device_id_byte`."""

    def test_panel_seventeen_is_wire_ten_hex(self):
        assert m.device_id_byte(17) == 0x10

    def test_panel_thirty_two_is_wire_one_f_hex(self):
        assert m.device_id_byte(32) == 0x1F

    def test_round_trips(self):
        for panel in range(17, 33):
            assert m.device_id_display(m.device_id_byte(panel)) == panel

    def test_broadcast_passes_through_both_ways(self):
        assert m.device_id_byte(127) == 0x7F
        assert m.device_id_display(0x7F) == 0x7F

    @pytest.mark.parametrize("panel", [0, 1, 16, 33, 64])
    def test_rejects_values_that_are_not_panel_numbers(self, panel):
        # 16 in particular: it is a valid *wire* byte and not a valid panel
        # number, and the old "accepts either" version took it silently.
        with pytest.raises(ValueError):
            m.device_id_byte(panel)

    def test_rq1_does_not_reconvert_a_wire_byte(self):
        """The regression this whole split exists to prevent.

        ``rq1`` takes the wire byte. Running it back through
        ``device_id_byte`` shifted 0x11 down to 0x10, so frames went to a
        device that was not there -- and the XV-2020 answers that with
        silence, which is indistinguishable from every other failure.
        """
        for wire in range(0x10, 0x20):
            frame = m.rq1((0x30, 0, 0, 0), m.size_bytes(12), device=wire)
            assert frame[2] == wire

    def test_dt1_does_not_reconvert_either(self):
        for wire in range(0x10, 0x20):
            frame = m.dt1((0x30, 0, 0, 0), b"\x01", device=wire)
            assert frame[2] == wire


class TestChecksum:
    def test_sum_of_body_and_checksum_is_zero_mod_128(self):
        body = [0x30, 0x00, 0x00, 0x00, 0x41, 0x42]
        assert (sum(body) + m.checksum(body)) % 128 == 0

    def test_covers_the_address_not_just_the_data(self):
        """Data-only checksums are the classic way to get "Checksum error"."""
        address = [0x30, 0x01, 0x00, 0x00]
        data = [0x41, 0x42]
        assert m.checksum(address + data) != m.checksum(data)

    def test_zero_when_the_body_already_sums_to_a_multiple(self):
        assert m.checksum([0x40, 0x40]) == 0

    def test_rejects_a_byte_with_the_high_bit_set(self):
        with pytest.raises(ValueError):
            m.checksum([0x80])


class TestAddresses:
    def test_carries_at_128_not_256(self):
        assert m.address_add((0, 0x7F, 0, 0), (0, 1, 0, 0)) == (1, 0, 0, 0)

    def test_plain_addition_would_be_wrong_here(self):
        # 0x7F + 1 is 0x80, which is not a legal address byte at all.
        result = m.address_add((0, 0, 0, 0x7F), (0, 0, 0, 1))
        assert result == (0, 0, 1, 0)
        assert all(0 <= b <= 0x7F for b in result)

    def test_rejects_a_byte_over_127_rather_than_masking_it(self):
        with pytest.raises(ValueError):
            m.pack_address((0, 0, 0, 128))

    def test_rejects_the_wrong_length(self):
        with pytest.raises(ValueError):
            m.pack_address((0, 0, 0))

    def test_user_patch_addresses_match_the_printed_map(self):
        # OM p. 146: 30 00 00 00 .. 30 7F 00 00
        assert m.user_patch_address(1) == (0x30, 0x00, 0x00, 0x00)
        assert m.user_patch_address(2) == (0x30, 0x01, 0x00, 0x00)
        assert m.user_patch_address(128) == (0x30, 0x7F, 0x00, 0x00)

    def test_user_performance_addresses_match(self):
        assert m.user_performance_address(1) == (0x20, 0x00, 0x00, 0x00)
        assert m.user_performance_address(64) == (0x20, 0x3F, 0x00, 0x00)

    def test_user_rhythms_step_by_sixteen(self):
        # 40 00, 40 10, 40 20, 40 30 -- not 40 00..40 03.
        assert m.user_rhythm_address(1) == (0x40, 0x00, 0x00, 0x00)
        assert m.user_rhythm_address(2) == (0x40, 0x10, 0x00, 0x00)
        assert m.user_rhythm_address(4) == (0x40, 0x30, 0x00, 0x00)

    @pytest.mark.parametrize("number", [0, 129, -1])
    def test_out_of_range_patch_numbers_raise(self, number):
        with pytest.raises(ValueError):
            m.user_patch_address(number)

    def test_size_is_base_128_too(self):
        # 128 bytes is 00 00 01 00, not 00 00 00 80.
        assert m.size_bytes(128) == (0, 0, 1, 0)
        assert m.size_bytes(12) == (0, 0, 0, 12)


class TestFrames:
    def test_rq1_layout_matches_the_manual(self):
        frame = m.rq1((0x30, 0, 0, 0), m.size_bytes(12), device=0x10)
        assert frame[0] == 0xF0
        assert frame[1] == m.ROLAND_ID
        assert frame[2] == 0x10
        assert (frame[3], frame[4]) == m.MODEL_ID
        assert frame[5] == m.CMD_RQ1
        assert frame[-1] == 0xF7
        assert (sum(frame[6:-1])) % 128 == 0

    def test_dt1_round_trips_through_the_parser(self):
        frame = m.dt1((0x30, 0x02, 0, 0), m.encode_name("Velvet Bell"), device=0x10)
        parsed = m.parse_dt1(frame)
        assert parsed is not None
        assert parsed.address == (0x30, 0x02, 0, 0)
        assert m.decode_name(parsed.data) == "Velvet Bell"

    def test_a_foreign_manufacturer_is_skipped_not_an_error(self):
        """A shared port carries other devices' traffic."""
        assert (
            m.parse_dt1(
                bytes([0xF0, 0x43, 0x10, 0x00, 0x10, 0x12, 0, 0, 0, 0, 0, 0xF7])
            )
            is None
        )

    def test_a_wrong_model_id_is_skipped(self):
        assert (
            m.parse_dt1(
                bytes([0xF0, 0x41, 0x10, 0x00, 0x0B, 0x12, 0, 0, 0, 0, 0, 0xF7])
            )
            is None
        )

    def test_a_bad_checksum_on_our_own_frame_raises(self):
        """Corruption on our conversation must not present as a timeout."""
        frame = bytearray(m.dt1((0x30, 0, 0, 0), b"\x01", device=0x10))
        frame[-2] = (frame[-2] + 1) % 128
        with pytest.raises(ValueError, match="checksum"):
            m.parse_dt1(bytes(frame))


class TestIdentity:
    #: The exact reply the manual prints (OM p. 145), and the exact reply a
    #: real XV-2020 sent on 2026-09-11.
    REPLY = bytes.fromhex("f07e1006024110010003 00000000 f7".replace(" ", ""))

    def test_parses_the_documented_reply(self):
        reply = m.parse_identity_reply(self.REPLY)
        assert reply is not None
        assert reply.is_xv2020
        assert reply.family == (0x10, 0x01)
        assert reply.family_number == (0x00, 0x03)
        assert reply.device_display == 17

    def test_rejects_a_sibling_with_the_same_family_code(self):
        """Family 10 01 is shared across the XV/JV line.

        Matching on the family alone would adopt an XV-3080 sharing the MIDI
        chain; the family *number* is what separates them.
        """
        other = bytearray(self.REPLY)
        other[9] = 0x04  # family number 00 04
        reply = m.parse_identity_reply(bytes(other))
        assert reply is not None
        assert not reply.is_xv2020

    def test_an_identity_request_is_not_a_reply(self):
        """The discriminator against a MIDI-Thru loop echoing our own bytes."""
        assert m.parse_identity_reply(m.IDENTITY_REQUEST) is None


class TestNames:
    def test_decodes_and_strips_the_padding(self):
        assert m.decode_name(b"Velvet Bell ") == "Velvet Bell"

    def test_encodes_to_a_fixed_width_field(self):
        assert m.encode_name("Velvet Bell") == b"Velvet Bell "
        assert len(m.encode_name("x")) == m.PATCH_NAME_LEN

    def test_truncates_rather_than_overflowing_the_field(self):
        assert len(m.encode_name("a" * 40)) == m.PATCH_NAME_LEN

    def test_shows_an_out_of_range_byte_rather_than_dropping_it(self):
        """A hole is debuggable; a silently shortened name is not."""
        assert m.decode_name(b"AB\x00CD") == "AB.CD"
