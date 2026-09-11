<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
-->

# Resolution notes

*How* things were resolved, and where every transcribed number came from.
`TODO.md` tracks *what* is still open.

---

## §1 — Sources, and which one wins

Four kinds of source, in descending order of authority for the data rxved
carries.

**1. Roland Supplemental Note SN 132 v3.00 (Summer 2007), "Selecting
Internal and SRX-Series Sounds Via MIDI".**
The authority for the entire SRX allocation: every board's Bank Select MSB,
LSB range and program-change range in one table. Supersedes the individual
card manuals, and corrected two readings taken from them (§4).
Local copy: `~/Dokumente/SYNTHS/XV2020/roland_srx_selecting_sounds_via_midi.pdf`

**2. The Roland XV-2020 Editor for Windows (2006).**
`XV-2020Editor.exe`, 1 609 728 bytes, from the `XVEditor` install. Carries
the preset and GM name tables as fixed-width binary records — the same
12-byte width the device stores them in — so names come out byte-exact,
including apostrophes that a PDF text layer mangles. The GM records also
carry their own MSB/LSB/PC, so that mapping is *read*, not inferred. Layout
in §3.
Local copy: `~/.wine64_roland/drive_c/Program Files (x86)/Roland/XVEditor/`

Also ships `Script/XV-2020EditorScript.xml`, 1.7 MB of panel and parameter
definitions. **Not used yet**, and the obvious source for a future editor —
see TODO.

**3. The XV-2020 Owner's Manual (Roland Corporation, 2003), 169 pp.**
The authority for the SysEx protocol (MIDI Implementation, pp. 140–146) and
the internal Bank Select map (pp. 40–41, p. 136). Also the only source for
the performance and rhythm-set name lists.
Local copy: `~/Seafile/Bibliothek/Handbücher/Audio-Daws_and_Plugins/Synthesizer/XV-2020_OM.pdf`

**4. The XV-2020 Patch Listing** (Roland Corporation U.S. Faxback #10688,
2003). The only source for the USER bank's factory contents — the editor
reads those from the device and so embeds no copy.
Local copy: `~/Dokumente/SYNTHS/XV2020/xv2020patch.pdf`

**The hardware outranks all four** for anything it can be asked, which is
the user banks and the device's identity. See §6.

---

## §2 — The SysEx protocol

From the Owner's Manual, MIDI Implementation chapter:

```
F0 41 dev 00 10 11 aa bb cc dd ss ss ss ss sum F7    RQ1  (Data Request, p. 145)
F0 41 dev 00 10 12 aa bb cc dd <data …>       sum F7    DT1  (Data Set,     p. 145)
```

- `41` Roland; `00 10` the XV-2020 model ID; `dev` the device ID.
- Addresses are **four bytes, 7 bits each, base 128**. `00 7F 00 00` plus
  `00 01 00 00` is `01 00 00 00`, not `00 80 00 00`. Ordinary integer
  addition is wrong for any offset that crosses a byte.
- The **checksum covers the address as well as the data**: it is the value
  that makes address + data + checksum come to zero mod 128. A data-only
  checksum produces the machine's "Checksum error" (p. 118), which the
  manual lists as distinct from a malformed message — so the display tells
  you which mistake you made.
- **A request the device cannot serve is answered with silence**: "if the
  conditions are not met, nothing is transmitted." A timeout therefore
  carries no diagnostic information at all.

Top-level address map (p. 146), the part rxved uses:

| Address       | What                                        |
|---------------|---------------------------------------------|
| `01 00 00 00` | Setup                                       |
| `02 00 00 00` | System                                      |
| `10 00 00 00` | Temporary Performance                       |
| `1F 00 00 00` | Temporary Patch/Rhythm (Patch mode)         |
| `20 00 00 00` … `20 3F 00 00` | User Performance 1–64       |
| `30 00 00 00` … `30 7F 00 00` | User Patch 1–128            |
| `40 00 00 00` … `40 30 00 00` | User Rhythm 1–4 (step 16!)  |

Patch Name is 12 bytes at Patch Common offset `00 00`, ASCII 32–127,
space-padded (p. 149).

**Note what is not there: the preset banks.** They are ROM and have no
addresses. This is the single fact that shapes rxved's design — see §5.

---

## §3 — Mining the editor binary

Located by structure rather than by hardcoded offsets, so the extractor
survives a different build and fails loudly instead of reading stale bytes.

**Preset patch table** — the longest run of `[12 printable ASCII][NUL][byte]`
records in the file. Exactly 512 of them, which split cleanly into PST-A,
PST-B, PST-C and PST-D at 128-record boundaries; every boundary was checked
against the manual's printed lists and matched. The trailing byte is a
category index, 1-based, into the "Choosing Patches by Category" table on
OM p. 37 (PNO, EP, KEY, … CMB — 38 categories).

**GM tables** — records of `[MSB][LSB][PC][12 name][NUL]`, 16 bytes,
identified by their own MSB byte. 256 records at MSB 121 (GM2 patches) and
9 at MSB 120 (GM2 rhythm sets). Because each record states its LSB and
program change, the split of the 256 GM patches into a capital-sound bank
and nine variation banks is read off the data rather than assumed, and the
nine drum kits' non-contiguous program changes (1, 9, 17, 25, 26, 33, 41,
49, 57 — note 25 and 26 adjacent) come straight out.

**What could not be mined: the rhythm-set and performance name tables.**
Both are in the binary, but the linker **pools identical string constants**,
and three of the eight internal kit names are also performance names. The
rhythm table's entries are therefore shared with the performance table's and
the run is not contiguous, so reading order off the pool would be guesswork.
Taken from the manual instead. Recorded here so nobody tries again.

---

## §4 — The SRX allocation, and two wrong turns

**Wrong turn 1.** The XV-2020's own manual prints the LSB column for "EXP-A"
and "EXP-B" as `0-` — declining to say — and defers to the card. rxved first
modelled that as EXP-A = LSB 0, EXP-B = LSB 1. A guess, and wrong.

**Wrong turn 2.** The SRX-07 and SRX-08 manuals gave real numbers (LSB
11–14 and 15–18), four apart, suggesting four LSBs per card. Extrapolating
that backwards puts the early cards at negative LSBs, so the model was
rebuilt from five individual card manuals — right about those five, wrong
about the shape.

**Resolution: SN 132 has the whole series in one table.** The scheme is a
running allocation: each board gets as many consecutive LSBs as its patch
count needs at 128 per LSB, and its rhythm LSB is its *first* patch LSB
under MSB 92 instead of 93.

| Board | Patch LSBs | Patches | Rhythm LSB | Sets |
|-------|------------|---------|------------|------|
| SRX-01 | 0            | 41  | 0  | 79 |
| SRX-02 | 1            | 50  | —  | —  |
| SRX-03 | 2            | 128 | 2  | 12 |
| SRX-04 | 3            | 128 | —  | —  |
| SRX-05 | 4–6          | 312 | 4  | 34 |
| SRX-06 | 7–10         | 449 | 7  | 5  |
| SRX-07 | 11–14        | 475 | 11 | 11 |
| SRX-08 | 15–18        | 448 | 15 | 21 |
| SRX-09 | 19–22        | 414 | 19 | 12 |
| SRX-10 | 23           | 100 | —  | —  |
| SRX-11 | 24           | 42  | —  | —  |
| SRX-12 | **26**       | 105 | —  | —  |
| SRX-97 | 97           | 12  | —  | —  |
| SRX-98 | 98           | 78  | —  | —  |

Two things to keep:

- **LSB 25 is assigned to nothing.** SRX-11 ends at 24 and SRX-12 starts at
  26. That hole is why this is a transcribed table and not a function of the
  board number: any formula fitted to the low boards puts SRX-12 at 25, and
  a wrong LSB does not fail loudly — it selects a different board's patch,
  or nothing.
- SN 132 **corrected two readings** taken from the individual manuals:
  SRX-02 has 50 patches (not a full 128), and SRX-08 has 21 rhythm sets (not
  the 12 a truncated PDF column suggested).

The individual SRX patch listings (Faxback-style PDFs, as opposed to the
owner's manuals) carry **names but no Bank Select data at all** — worth
knowing before opening one hoping for a table.

---

## §5 — Why reading a preset name plays the instrument

The preset banks have no addresses (§2), so there is no read that reaches
them. The only route is:

1. Select the patch (Bank Select MSB, LSB, then Program Change — in that
   order; the device latches the bank on the program change).
2. Read the Temporary Patch area at `1F 00 00 00`.

That changes what the synth is sounding. Hence:

- `scan_bank()` is never called implicitly, confirms in the TUI, and needs
  `--yes` on the command line.
- Cursor movement in the browser sends nothing. This is enforced by a test
  (`tests/test_app.py::TestCursorIsSilent`), because it is the property that
  decides whether the tool can be left open next to a running sequencer.

**The settle problem.** A program change loads a patch and its four tones
from ROM. Reading the temporary area too early returns the *previous*
patch — not an error, just every name attached to the wrong slot number,
silently and plausibly. `SELECT_GAP` (60 ms) is a **guess**; the manual's
only timing figure is 20 ms between Roland's own outgoing packets, which is
not a floor for us. So `scan_bank()` does not rely on the gap alone: a name
equal to the previous slot's is re-read up to three times. Imperfect — two
adjacent slots that genuinely share a name will retry and then correctly
keep the duplicate — but it fails towards slowness rather than towards quiet
corruption. Measuring the real settle time is a TODO.

---

## §6 — The 2026-09-11 hardware session

Against the author's XV-2020 over USB (ALSA client 68, `Roland XV-2020 MIDI
1`), device ID 17.

**Identity.** A broadcast Universal Identity Request returned exactly the
documented bytes: `F0 7E 10 06 02 41 10 01 00 03 00 00 00 00 F7`. Family
`10 01`, family number `00 03`. Family alone is not enough to identify the
model — `10 01` is shared across the XV/JV line — so rxved matches the
family *number* too, or it would adopt an XV-3080 on the same chain.

**The device-ID bug.** `device_id_byte()` accepted "either" a panel number
(17–32) or a wire byte (`0x10`–`0x1F`) and passed the latter through. Those
ranges **overlap for 17–31**, so panel "17" became `0x11`. Every RQ1 went to
a device that was not there, and the XV-2020's answer to that is silence —
indistinguishable from powered-off, wrong-port, or busy. Every synthetic
test passed, because they all shared the same wrong convention.

Fixed by making the two directions strictly typed: `device_id_byte()` takes
panel numbers only and rejects 16; `rq1`/`dt1` validate a wire byte and
never convert. Regression tests in
`tests/test_messages.py::TestDeviceId`, including one asserting that `rq1`
preserves every wire byte from `0x10` to `0x1F`.

**What the fix confirmed, all at once.** With the right device ID, a Data
Request at `30 00 00 00` for 12 bytes returned a Data Set carrying that
address and a legible name — so the frame layout, model ID, base-128
addressing, checksum-over-address-and-data, and the User Patch address map
are all confirmed together. All 128 User Patch names then read back in
order.

The machine's USER bank holds a third-party analogue soundset, not the
factory contents — which is exactly the case the printed-versus-live name
distinction in `xv/catalog.py` exists for. Every one of the 128 slots was
reported as differing from the printed list, correctly.

---

## §7 — Catalog extraction results

`tools/extract_catalog.py` currently produces **1044 names across 22 banks**:
PST-A/B/C/D (128 each, with category tags), GM and its nine variation banks
(256 total), the nine GM2 rhythm sets, the three internal rhythm-set banks,
the three performance banks, and USER's factory contents.

Two parsing bugs found and fixed while building it, both of the
silently-drops-data kind:

- `$` in the entry regex without `re.MULTILINE` is end-of-*string*, not
  end-of-line, so every entry that ended its line — the whole rightmost
  column, 31 of 128 — vanished.
- The performance table was anchored on the phrase "Performance List", whose
  first occurrence in the document is the table-of-contents entry, 9000
  lines before the table. Anchored on the table's own column header instead.

The extractor leaves a bank **out entirely** rather than shipping a partial
one, on the grounds that a browser showing 60% of a bank's names with no
indication which 40% are missing is worse than one showing none.

---

## §8 — Two receive channels, not one (2026-09-11)

rxved originally sent every Bank Select and Program Change on one configured
channel. That is wrong, and wrong in the quietest possible way.

The XV-2020 has **two** independent receive-channel settings in System
Common (OM p. 147, panel description p. 94):

| Offset | Parameter | Range | Encoding |
|--------|-----------|-------|----------|
| `02 00 00 09` | Performance Control Channel | 0–16 | 0–15 = channels 1–16, **16 = OFF** |
| `02 00 00 0B` | Patch Receive Channel | 0–15 | channels 1–16 |

Patches and rhythm sets arrive on the second; performances on the first. A
performance select sent to the patch channel is not rejected — it is
ignored, and the synth carries on playing what it was playing, which is
indistinguishable from rxved having sent nothing at all.

**Read off the author's machine**, single-byte read at `02 00 00 09` and a
32-byte block read of System Common, which agree:

```
System Common 00 00..00 1F:
00 04 00 00 40 7F 01 00 01 0E 10 00 40 40 40 40 ...
                           ^^    ^^
                     09 = 0E     0B = 00
```

- Performance Control Channel `0E` = 14 → **channel 15**
- Patch Receive Channel `00` = 0 → **channel 1**

Note that 15 is *not* the factory default of 16, so "just default to 16"
would also have been wrong on this machine. Reading beats guessing, and the
read is one round trip and makes no sound.

The block read is worth keeping as a habit: it is what confirms the
single-byte read landed where the map says rather than somewhere merely
plausible.

Implemented as `XvBridge.system_channels()` / `use_system_channels()`, with
`select()` choosing per slot kind and refusing outright when the control
channel is OFF. The demo bridge answers with the factory defaults (1 and 16)
rather than one channel for both, so the two-channel behaviour cannot pass a
test by accident.

**Not resolved by this**: whether a performance select on the right channel
actually works. The channel is now right; the rest of the triple is still
part of TODO item 1.

---

## §9 — Bank Select is per channel, and the mode decides what that means

rxved's first version had one send channel and one idea of "the current
patch". Both are wrong, and the correction came from a user observation
("MIDI PC/LSB is per MIDI channel") that the manual, the web and the
hardware all confirm.

### What the mode changes

Setup `01 00 00 00` offset `00 00` is **Sound Mode**: PATCH, PERFORM, GM1,
GM2, GS. It decides what a Bank Select and Program Change on a given
channel do:

* **PATCH** — single-timbral. Only the Patch Receive Channel selects
  anything; a Bank/PC on any other channel is ignored in silence.
* **PERFORM** — 16 parts, each with its own Receive Channel *and* its own
  patch. A Bank/PC on a part's channel selects that part's patch. The whole
  performance is selected on the Performance Control Channel, a third
  setting again (§8).
* **GM1 / GM2 / GS** — multitimbral under the respective standard.

Sound On Sound and Roland's own documentation agree: Performance mode is
what makes it a 16-part module, and Patch mode is one sound on one channel.

### The read-back

The Setup block is 15 bytes (`00 00 00 0F`) and is exactly the read-back
this project needed, in rxved's own terms:

| Offset | Parameter |
|--------|-----------|
| `00 00` | Sound Mode (1–5) |
| `00 04`–`00 06` | Performance Bank Select MSB / LSB / Program Number |
| `00 07`–`00 09` | Patch Bank Select MSB / LSB / Program Number |

Per-part state is in Temporary Performance, `10 00 <20+n-1> 00` — offset
`00 00` Receive Channel, `00 04`–`00 06` that part's Bank MSB / LSB / PC.

### Parts share channels

Read off the machine, in the performance it had loaded:

```
part  rxCh  MSB  LSB   PC
   1     1   87   65   83
   2     1   87   67  124
   3     1   87   65   60
   4     4   87   64    0     ... parts 4-16 each on their own channel
```

**Parts 1, 2 and 3 all listen on channel 1.** That is a layer, and it is
normal. So "what is on channel N" is a *list*, and a Program Change sent
there moves every part on it at once. `DeviceState.parts_on()` returns a
list for this reason, and the UI says "layered, so a Program Change here
moves all 3" rather than naming one part and being quietly wrong about the
other two.

### Also read: the machine was in PATCH mode

Sound Mode came back as 1 = PATCH, with the Patch Receive Channel on 1 and
the Performance Control Channel on 15. So the original assumption — one
channel, one patch — happened to be *harmless* on this machine in this mode,
and would have broken the moment it was switched to PERFORM. Worth recording
as the kind of bug that hides behind a lucky configuration.

---

## §10 — Every Bank Select triple confirmed on hardware (2026-09-11)

TODO item 1 is closed for patches. Each triple was sent on the Patch Receive
Channel and the Setup block read back immediately:

| Sent | MSB | LSB | PC | Synth reported |
|------|-----|-----|----|----------------|
| PST-B 029 | 87 | 65 | 28 | PST-B 029 |
| PST-A 001 | 87 | 64 | 0 | PST-A 001 |
| GM 001 | 121 | 0 | 0 | GM 001 |
| PST-D 128 | 87 | 67 | 127 | PST-D 128 |
| USER 064 | 87 | 0 | 63 | USER 064 |

This settles three things at once:

1. **The MSB/LSB map is right** for USER, all four preset banks and GM.
2. **The 0-based/1-based split is right.** PST-B 029 goes out as program
   change 28 and the synth reports itself on 029. That is the single
   numbering trap this project was built around, and it now has evidence
   rather than an argument.
3. **The read-back works**, which is what makes the rest of the verification
   cheap — any triple can now be checked without a human reading the front
   panel.

Still not confirmed: performances (they need a mode switch and the control
channel), rhythm sets, and the whole SRX table.

---

## §11 — Expansion-board names, and four ways a column parse goes wrong

`tools/extract_catalog.py --srx CARD=PDF` reads a board's patch list out of
its owner's manual: 475 names for SRX-07 and 448 for SRX-08, with
categories, split across the LSB pages that actually select them.

The parse is one regex per row and it took four corrections, each of which
had produced complete, plausible, wrong output:

**The manuals contain more than one patch list.** One for the Fantom/XV/
JUNO-G family and another for the RD series, MC-909 and G-70 — and they are
*different patches*. SRX-05 number 298 is "OldSkool FX" in the first and
"Noise Cycle" in the second. Parsing is now scoped to the section headed for
the XV series.

**Column separators are not consistent.** SRX-05 leaves one space after the
number, SRX-07 row 34 leaves one before the category ("3 (1) EL.PIANO"),
others leave one after the name. Every separator had to become `\s+`.

**Which needs a constraint to stay safe**, and the one that works is that a
name may contain a single space but never two, while a column gap is always
at least two. Without it, row 34 read as the name "Heavens Tine 3 (1)
EL.PIANO 124 FuzzheadSRX" carrying the *next* row's voice count and
category.

**Validating after matching does not work.** `finditer` consumes what a
match covers, so rejecting a bad match does not make the engine try a better
one — it moves past, and the row is lost. SRX-07 row 40 reads "Clav 1 SRX
2 KEYBOARDS", whose shortest parse is name "Clav", voices 1, category "SRX".
The fix is to build the real category names into the pattern as an
alternation: "SRX" is then not a category, so the engine backtracks to name
"Clav 1 SRX", voices 2, category "KEYBOARDS" by itself.

### The two boards spell tempo differently, and say so

SRX-07's phrase-loop patches print a recommended tempo in its own column —
`453  Pursuit 90   (90)   2  BEAT&GROOVE` — and its footnote reads "the
numbers **in parenthesis following** the Patch name". SRX-08 prints
`37  BAD 88   3  BEAT&GROOVE` and its footnote reads "the numbers
**included in** the Patch name".

So "BAD 88" is the whole name and "(90)" is not. The two readings look
inconsistent and are correct, which is only knowable from the footnotes.

### Cross-check

The separate Faxback patch listings agree on 421 of 475 SRX-07 names and 396
of 448 for SRX-08. The disagreements are all explained:

* the listings truncate to a narrower column ("Tenamos L" for "Tenamos
  L100");
* SRX-08's tempo suffix is absent from them ("BAD" for "BAD 88");
* and a handful are genuine Roland renames between revisions — "TouchRhdsSRX"
  became "Touch EP SRX", "Cool Rhodes" became "Cool EP 2". The owner's manual
  is the later document and the one whose names the device displays, so it
  wins.

### Names-only listings are a fallback, and a lossy one

Roland also published a one-page "Patch Listing" per board: names, no voice
count, no category, no Bank Select. `--srx-list` reads them, and they are
the only source for boards whose owner's manual is not to hand.

They are less reliable than they look, and **the failure is not detectable
from the sheet alone**. SRX-06's listing numbers a page-break artefact as
patch 271 ("271. No."), so the 179 names after it each land on the patch
before them -- and the sheet still parses as a complete, gapless 1..449,
ending at 450 for a 449-patch board. That was caught only because SRX-06
also has a manual to disagree with.

An automatic check on "numbers past the documented count" was tried and
removed: SRX-01's sheet prints its 79 rhythm sets in the right-hand columns,
so it legitimately numbers far past its 41 patches, and no threshold
separates that from a genuine off-by-one. The tool now simply reports which
boards came from a listing, and says they are unverified.

Read from owner's manuals, with categories: SRX-02 (50), SRX-05 (312),
SRX-06 (449), SRX-07 (475), SRX-08 (448). Read from listings, names only:
SRX-01 (41), SRX-03 (128), SRX-09 (414). No source yet: SRX-04, 10, 11, 12,
97, 98.

### The XV-2020 has two expansion slots, not one

Recorded because an earlier note in `xv/banks.py` said one, and reasoned
from it. The manual is explicit: "You can install **up to two** SRX Series
Wave Expansion Boards" (OM p. 7), and it calls them Wave Expansion Board A
and B throughout — which is what the "EXP-A"/"EXP-B" in its Bank Select
table means. Roland's own compatibility guide lists the XV-2020 as 2 SRX
slots alongside the XV-3080 and XV-88.

This changes nothing in the bank table, and the reason is worth keeping:
**the LSB identifies the board, not the slot**. SN 132 says so outright —
"Expansion-board sounds are properly found and selected no matter where your
expansion boards are installed" — so an SRX-07 answers on LSB 11-14 in
either slot, and rxved never needs to know which slot holds what. EXP-A and
EXP-B are front-panel labels only.

### SRX-10, and a third class of source

SRX-10 had no document at all — only a screen capture of its Patch List
page. `--srx-names` reads a hand-written `number, name, category` table for
exactly this case, and `SRX-10-names.txt` holds the transcription.

Its header reads "(BANK SELECT MSB:93; LSB:23)" and it runs to 100 patches,
which is a third independent confirmation of SN 132's row for that board and
of rxved's table.

**This is the least trustworthy route in the pipeline and is marked as
such.** Everything else is read out of a file Roland shipped and most of it
is checked against a second one; this has been through a human eye. The
count is still checked against the board's documented total, so a missing or
duplicated row is caught — but a mistyped *name* on the right number passes
silently, and nothing in the project can find it.

### The second-list trap is in the listings too

`read_srx_list` did not scope to the XV section -- only the manual parser
did -- and it happened to work because every sheet to hand prints the XV
list first. SRX-04 showed that is luck, not a rule: its sheet carries an
"RD-700 Patch List:" straight after the XV one, and the two differ from
patch 98 ("Harp StrPad" against "Spec/Pizz"). Both readers now share
`_xv_section`, which also learned the sheets' own heading style
("XV-Series Patch List:" as well as "For Fantom series/XV series/...").

Coverage: SRX-01, 02, 03, 04, 05, 06, 07, 08, 09 and 10 complete
(2545 patches); SRX-11, 12, 97 and 98 have no source yet.
