<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
-->

# TODO

*What* is open. `docs/RESOLUTION_NOTES.md` tracks *how* to resolve each item.

## Status, 2026-09-11 (first session)

The browser exists and works, against the demo synth and against real
hardware. 166 tests, all passing, all synthetic.

What that means honestly: **the protocol layer is confirmed, the Bank Select
layer is not.** One hardware session established that the frame layout, the
model ID, the base-128 addressing, the checksum rule and the User Patch
address map are all right — they were confirmed together by a single
successful read, and then by 128 of them. It established nothing at all
about whether MSB 87 / LSB 65 / PC 28 actually selects Preset B patch 29,
because rxved has never been asked to select anything on the hardware and
have the result confirmed. That is item 1 below and it is the largest open
question in the project.

The other thing worth knowing before anything else: **the SRX Bank Select
allocation is settled** and does not need re-deriving. It took three
attempts (a guess, a wrong extrapolation from two card manuals, and finally
Roland's own series-wide table) and the answer is written down in
RESOLUTION_NOTES §4 with its source, precisely so nobody spends an evening
on it again.

---

## 1. No Bank Select triple has been confirmed on hardware

**Status:** open. Blocked on: nothing but a session at the machine.

Every MSB/LSB/PC in `xv/banks.py` is transcribed from Roland's tables and is
internally consistent, but no patch has been selected by rxved and confirmed
by the synth's own display.

The check is cheap and should be the first thing done at the machine: press
Enter on a slot in each bank and read the display. `PST-B 029` is the one to
start with — it is the manual's own worked example (OM p. 40) and it is the
case where the manual is loosest about 0-based versus 1-based.

Worth checking specifically:
- That the display shows the **number** rxved shows, not one either side.
- The three GM2 rhythm sets around the non-contiguous gap (program changes
  25 and 26, which are adjacent where the rest are eight apart).
- One performance, since those go out on the Performance Control channel
  (default 16), not the patch channel — rxved currently sends everything on
  one channel and this may well be wrong for `P-*` banks. See item 4.

## 2. `SELECT_GAP` and `SEND_GAP` are guesses

**Status:** open. Blocked on: hardware.

Both constants in `xv/bridge.py` are labelled as guesses at the point of
use, unlike the sibling s3ked's, which are measured. The manual's only
timing figure is 20 ms between Roland's own outgoing packets, which is not a
floor for ours.

`SELECT_GAP` (60 ms) is the one that bites: it gates how long `scan_bank()`
waits after a program change before reading the temporary area, and if it is
too short the failure is **silent** — every name attached to the wrong slot.
`scan_bank()` guards against that by re-reading when a name repeats, which
is a mitigation and not a measurement.

Procedure: walk the gap down, scan a preset bank at each value, and compare
against the printed list for that bank. The first value at which any name
lands on the wrong number is the floor.

## 3. Preset rhythm banks: is it 4 sets or 2?

**Status:** open. Blocked on: hardware, or a better source.

The same manual gives two answers. OM p. 40 says Preset Rhythm A and B hold
001–004. The p. 136 table says "001 - 002" in its number column and
"001 - 004" in the group column of the same row. rxved uses 4, matching
p. 40 and matching the Rhythm Set List (pp. 130–133), which prints four
named kits per preset group.

Resolvable in a minute at the machine: select preset rhythm A 003 and 004
and see whether anything happens.

## 4. Performances probably need their own MIDI channel

**Status:** open, and probably a real bug.

The XV-2020 has a **Performance Control Channel** (OM p. 94), which is 16
after a factory reset, and performances are selected on that channel — not
on the patch channel. rxved sends every Bank Select and Program Change on
one configured channel, so selecting a `P-*` slot very likely does nothing
unless the user has already set `--channel 16`.

Fix is probably: read the Performance Control Channel from the System area
(`02 00 00 00`, offset `00 09`, documented on OM p. 147) and use it for
performance banks. That read is free and non-audible, so it can happen at
startup.

## 5. SRX: nothing has been tested, and one board is fitted

**Status:** open. Blocked on: hardware.

The whole SRX table (RESOLUTION_NOTES §4) is transcribed and untested. The
author's machine has a board in it; `rxvcli probe-srx --yes` should identify
which LSBs answer, and that result should be checked against the table.

Note the probe's own limit, which is stated in its docstring and worth
repeating: the device answers an unsupported Bank Select by **staying where
it was**, so "no change" is the only signal available, and an LSB missing
from the result is not proof the board lacks it.

## 6. The catalog has no SRX names

**Status:** open. Not blocked — just work.

`tools/extract_catalog.py` covers the internal banks and GM. The SRX patch
listings the author has (SRX-02, -05, -06, -07, -08) are parseable Faxback
PDFs in the same format as the XV-2020 patch listing, so the existing entry
parser should mostly work; what they lack is Bank Select data, which is
already in `xv/banks.py` from SN 132, so the two only need joining on the
patch number.

Until then, SRX slots browse with correct numbers and no names — which is
exactly what the "scan the bank" and "read from device" paths are for.

## 7. The editor's parameter XML is untouched

**Status:** open. A future phase, not a defect.

`XVEditor/Script/XV-2020EditorScript.xml` is 1.7 MB of Roland's own panel
and parameter definitions for this synth. rxved does not read it and does
not need to as a browser. It is the obvious source if this ever grows an
editor — and it would spare transcribing the parameter address map by hand,
which is where the sibling projects spent most of their effort.

If that happens, the hardware rule in CLAUDE.md applies with force: rxved
currently cannot write to the synth's memory, and every destructive
operation added must go behind a modal arm-then-fire screen and must never
be key-bound.

## 8. Soundset files (`.syx`, `.xvl`) are not read

**Status:** open. A feature, not a defect.

The author has XV-2020 soundsets as both raw SysEx dumps and Roland
Librarian `.xvl` files. A librarian mode — read a bank out of a file,
compare it against what is on the machine, browse it without loading it —
is a natural second phase and needs no hardware to develop against.

`.syx` is presumably a stream of the same DT1 frames `xv/messages.py`
already decodes, which would make reading one nearly free. `.xvl` is a
Roland container format and unknown.

## 9. The per-platform data directory is untested off Linux

**Status:** open. Minor, and cheap to close.

`rxved/favorites.py` picks `%LOCALAPPDATA%` on Windows and
`~/Library/Application Support` on macOS, and the choice of Local over
Roaming on Windows is a deliberate one (a roaming profile copies files
wholesale at logon and logoff, which is a good way to corrupt a SQLite
database). All of it is covered by tests that patch `sys.platform`, so the
*logic* is pinned — but no rxved process has ever actually started on either
platform, and `python-rtmidi` and `textual` bring their own questions there.

## 10. Favourites cannot yet be exported

**Status:** open. Minor.

The database is the user's own work and currently only leaves the machine as
a SQLite file. `rxvcli fav` prints a table; a `--json` or `--csv` flag would
make it scriptable, and an importer would make the file portable between
machines.
