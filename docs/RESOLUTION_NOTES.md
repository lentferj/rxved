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
