<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
-->

# TODO

*What* is open. `docs/RESOLUTION_NOTES.md` tracks *how* to resolve each item.

## Status, 2026-09-11 (first session)

The browser exists and works, against the demo synth and against real
hardware. 187 tests, all passing, all synthetic.

What that means honestly: **the protocol layer and the patch Bank Select
map are confirmed; rhythm sets, performances and SRX are not.** One hardware session established that the frame layout, the
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

## 1. Bank Select triples — patches CONFIRMED, the rest still open

**Status:** partly resolved 2026-09-11. See RESOLUTION_NOTES §10.

Confirmed on hardware by sending each triple and reading the Setup block
back: USER, PST-A, PST-B, PST-D and GM all land exactly where `xv/banks.py`
says, **and the 0-based/1-based split is right** — PST-B 029 goes out as
program change 28 and the synth reports itself on 029.

The read-back makes the remaining checks cheap, since they no longer need
anybody watching the front panel. Still unconfirmed:

- **Rhythm sets** (MSB 86 / 120). Includes item 3's open question about
  whether the preset rhythm banks hold 4 or 2.
- **The GM2 rhythm program numbers** around the non-contiguous gap — 25 and
  26 are adjacent where the rest are eight apart.
- **Performances.** These need the synth put into PERFORM mode and a select
  on the Performance Control Channel; the channel is now read rather than
  guessed (item 4) but the triple itself is untested.
- **The whole SRX table** — item 5.

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

## 4. Performances need their own MIDI channel — FIXED 2026-09-11

**Status:** resolved. It was a real bug.

The XV-2020 receives patches and rhythm sets on the **Patch Receive
Channel** and performances on the **Performance Control Channel** (OM p. 94)
— two independent settings. rxved sent everything on one configured channel,
so a performance select went to the wrong channel and was **ignored without
complaint**, which looks exactly like rxved having sent nothing.

Confirmed by reading System Common off the machine (RESOLUTION_NOTES §8):
this XV-2020 receives patches on **channel 1** and performances on
**channel 15** — so the default would have been wrong, and so would the
"just use 16" guess this item originally suggested, since 16 is only the
factory default and this machine has been changed.

Fixed: `XvBridge.system_channels()` reads both at
`02 00 00 09`/`02 00 00 0B`, `use_system_channels()` caches them at startup
(one silent round trip), and `select()` picks the right one per slot kind.
With the control channel OFF it refuses rather than sending into the void.
`rxvcli channels` prints both.

**Still unverified:** that selecting a performance on channel 15 actually
changes this synth's performance. The channel is now right; whether the rest
of the triple is remains part of item 1.

## 5b. SRX-97's compatibility is inferred, not established

**Status:** open, minor.

SRX-98 is excluded from the bank table on the authority of its own manual
("No other products can be used", XV-2020 absent). SRX-97 is excluded on the
strength of being the same "Special SRX Board" series with no listing naming
the XV-2020 — its manual has not been read. If it turns up and does name the
XV-2020, flip `xv2020=True` on that row.

## 5. SRX: nothing has been tested, and one board is fitted

**Status:** open. Blocked on: hardware.

The whole SRX table (RESOLUTION_NOTES §4) is transcribed and untested. The
author's machine has a board in it; `rxvcli probe-srx --yes` should identify
which LSBs answer, and that result should be checked against the table.

Note the probe's own limit, which is stated in its docstring and worth
repeating: the device answers an unsupported Bank Select by **staying where
it was**, so "no change" is the only signal available, and an LSB missing
from the result is not proof the board lacks it.

## 6. The catalog has no SRX names — DONE for the boards with a manual

**Status:** resolved for SRX-07 and SRX-08; open for the rest.

`tools/extract_catalog.py --srx CARD=PDF` reads a board's patch list out of
its owner's manual, with categories, and files each patch under the LSB page
that selects it. Cross-checked against the separate Faxback listings; the
disagreements are all explained in RESOLUTION_NOTES §11.

Any other board needs only its owner's manual — the Faxback-style "patch
listing" PDFs carry no Voices or Category column and no Bank Select data, so
they do not work as input.

## 6b. The remaining boards (original item)

**Status:** open. Not blocked — just work.

`tools/extract_catalog.py` covers the internal banks and GM. The SRX patch
listings the author has (SRX-02, -05, -06, -07, -08) are parseable Faxback
PDFs in the same format as the XV-2020 patch listing, so the existing entry
parser should mostly work; what they lack is Bank Select data, which is
already in `xv/banks.py` from SN 132, so the two only need joining on the
patch number.

Done: all twelve usable boards are read from their own owner's manuals, with
categories, plus 174 rhythm-set names from the same files. 2637 patches.

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

## 11 — Why only channel 1 sounds

**Status:** answered 2026-09-11 against the hardware. Two causes at once:
the sound mode is PATCH (single-timbral), **and** parts 4-16 in the loaded
performance have Receive Switch off. See docs/RESOLUTION_NOTES.md §12.

What remains open is whether rxved should be able to *fix* it. Receive
Switch is not among the parameters the module itself can reach (OM p. 116),
so the only routes are the XV-2020 Editor or a SysEx write to Temporary
Performance. rxved is read-only by policy. Writing offset `00 01` of a part
touches the edit buffer rather than stored memory, but it is still a write,
and CLAUDE.md requires any such thing behind an arm-then-fire screen that is
never key-bound. Not built; the user's call.

**Superseded notes below.**

The `m` (multi-mode setup) window was written for this and has not yet been
run against the synth. Press `m` and read the report.

The likely answer is that nothing is muted: the synth was observed in
**PATCH** sound mode, which is single-timbral — only the Patch Receive
Channel sounds and the Performance Parts are not in use. PERFORM mode is
what makes it multitimbral.

**Correction:** an earlier version of this item said the Mute Switch was
absent from the parameter address map and could never be read. That was
wrong. It is Performance Part offset `00 1B` (OM p. 146), and rxved now
reads and writes it along with the send levels and output routing. Every
readable cause of a silent part is now covered.

