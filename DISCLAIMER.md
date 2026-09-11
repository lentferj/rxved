<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
-->

# Disclaimer

## AI Assistance & Human Authorship

In the interest of transparency: rxved was created by its **human author,
Jan Lentfer (<jan.lentfer@web.de>)**, working together with Anthropic's
**Claude**, an AI coding assistant, and follows the pattern established by
the author's sibling **eosed**, **s3ked**, **k2kremote** and **mpc2emu**
projects.

**The ideas and the direction are human.** Targeting the XV-2020, starting
with a browser rather than an editor, the specific first deliverable — "let
me browse the synth's contents and read the MIDI program number, Bank MSB
and Bank LSB" — the favourites database, and every source that made the
data trustworthy (the Roland XV Editor binary, the SRX manuals, and above
all Roland's Supplemental Note SN 132) came from the human author.

**Claude assisted with the execution:** locating the name and Bank Select
tables inside Roland's editor binary and writing the extractor for them;
transcribing the SysEx frame layout, address map and Bank Select tables into
`xv/messages.py` and `xv/banks.py`; writing the codec, transport, CLI,
Textual TUI and favourites store; and drafting the documentation and tests.

## Some of this is verified against hardware. Much of it is not.

**Read which is which before trusting a byte offset.**

Verified on **2026-09-11** against the author's own XV-2020 (USB, ALSA
client `Roland XV-2020`, device ID 17):

- **Port discovery and Identity Request/Reply.** The synth answered a
  broadcast Universal Identity Request with exactly the bytes the manual
  prints: manufacturer `41`, family `10 01`, family number `00 03`.
- **The device-ID numbering**, the hard way — see below.
- **RQ1/DT1 round trip.** A Data Request at `30 00 00 00` for 12 bytes
  returned a Data Set carrying that address and a legible patch name, so the
  frame layout, the model ID, the base-128 address arithmetic and the
  checksum-over-address-and-data rule are all confirmed together.
- **The User Patch address map.** All 128 User Patch names read back from
  `30 00 00 00` … `30 7F 00 00`, one per slot, in order.
- **The Setup block** (`01 00 00 00`): sound mode, and the Bank Select and
  Program Number of both the current patch and the current performance.
- **All 16 Performance Parts** (`10 00 <20+n-1> 00`): each one's receive
  channel and patch selection. Three parts were found sharing channel 1.
- **The Bank Select triples for USER, PST-A, PST-B, PST-D and GM**, sent and
  read back, confirming the MSB/LSB map and the 0-based/1-based split.
- **System Common's two receive-channel bytes** (`02 00 00 09` and
  `02 00 00 0B`), confirmed both by single-byte reads and by a block read of
  the surrounding area. This machine receives patches on channel 1 and
  performances on channel 15 — see RESOLUTION_NOTES §8.

**Not verified.** Everything else, including:

- The User Performance (`20 nn 00 00`) and User Rhythm (`40 n0 00 00`)
  addresses. Transcribed from the same table that got User Patch right,
  which is encouraging and is not evidence.
- Rhythm-set and performance Bank Select triples. The patch ones are now
  confirmed (above); these are not, and performances additionally need the
  synth put into PERFORM mode to test.
- The whole SRX allocation. **No SRX board was fitted to test against**, and
  the one in the author's machine has not been probed.
- Both timing constants in `xv/bridge.py` (`SEND_GAP`, `SELECT_GAP`). These
  are **guesses**, labelled as such at the point of use. The sibling s3ked's
  are measured; these are not, and must not be copied as if they were.
  `SELECT_GAP` in particular gates `scan_bank`, and if it is too short the
  failure is silent — every name attached to the wrong slot number.
- Anything in `tools/extract_catalog.py` beyond the fact that it produces
  1044 plausible names that agree with the printed lists.

### One bug worth recording, because of how it hid

`xv.messages.device_id_byte` originally accepted "either form" of a device
ID — a panel number (17–32) or a wire byte (`0x10`–`0x1F`) — and passed the
latter through unchanged. Those two ranges **overlap for every value from 17
to 31**, so the function could not tell which it had been handed, and chose
wrong. The panel's own "17" became `0x11` instead of `0x10`.

The XV-2020 answers a request addressed to the wrong device ID with
**silence**, which is byte-for-byte the same observation as a synth that is
powered off, on another port, or merely busy. Every synthetic test passed:
they all agreed with the same wrong convention. It took one read against
real hardware to find, and it is the clearest argument in this project for
why the hardware verification above matters and why the list of unverified
things above should be read as a warning rather than a formality.

## Hardware safety

rxved does not write to the synth's memory. There is no method in `xv/` that
stores a patch, renames a slot, or erases anything.

It does **play the instrument**, in three places, all of which say so:

- `Enter` in the TUI, `rxvcli select` — sends Bank Select and Program
  Change, changing the sound immediately and audibly.
- `s` in the TUI, `rxvcli scan --yes` — the same, once per slot, up to 128
  times in a row, leaving the synth on the last one.
- `x` in the TUI, `rxvcli probe-srx --yes` — the same across candidate
  Bank Select LSBs.

The two sweeping operations ask for confirmation in the TUI and refuse to
run without `--yes` on the command line. None of them fire from cursor
movement. **Do not run a scan into a live take.**

## No warranty

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License in
`COPYING` for details.

## Not affiliated with Roland

Roland, XV-2020, SRX, JV and Fantom are trademarks of Roland Corporation.
This is an independent project, not endorsed by or affiliated with Roland,
and uses those names only to say what hardware it talks to.

The patch, rhythm-set and performance **names** are Roland's, and are not
distributed with rxved. `tools/extract_catalog.py` generates them locally
from your own copies of Roland's files, and its output is gitignored. See
README.md.
