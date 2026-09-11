<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
-->

# rxved

A terminal browser for the **Roland XV-2020**'s sounds.

Two panes: every bank the synth can be sent to on the left, that bank's
slots on the right, and on every row the three numbers that actually select
a sound — Program Change, Bank Select LSB and Bank Select MSB, in the order
a sequencer's MIDI track asks for them.

```
 bank       kind    n    fav      #    name          PC   LSB  MSB  cat  fav
 USER       patch   128           003  Folded Keys      2   65   87  DGT
 PST-A      patch   128  1        004  Static Pluck     3   65   87  DGT
▸PST-B      patch   128  1        005  Woven Bass       4   65   87  BS
 PST-C      patch   128           …
 GM         patch   128          ▸029  Iron Drone      28   65   87  SBS  *

 PRESET B 029  Iron Drone
 Program Change 28 (wire, 0-based; the display shows 29)
 Bank Select LSB 65 (CC#32)   MSB 87 (CC#0)
```

That last line is the point of the project. Everything else about a patch
can be found by listening to it; those three numbers cannot, and the manual
that lists them is 169 pages long and prints program numbers in a different
base from the one that goes on the wire.

Plus a local **favourites** database, so the patches you actually use stop
being something you rediscover.

rxved is a **browser, not an editor**. There is no code in it that writes to
the synth's memory.

---

## What it does

- **A send channel, because Bank Select and Program Change are per
  channel.** Which channel you send on decides what a select actually hits,
  and the synth never volunteers that: in Patch mode only the Patch Receive
  Channel selects anything, and in Performance mode each of the 16 parts has
  its own channel and its own patch — several parts may share one, in which
  case a Program Change there moves all of them. rxved reads the synth's
  mode, its two receive channels and every part's state, shows what is
  currently on the channel you are aiming at, and re-reads it whenever you
  change channel or select something.
- **Every bank the XV-2020 addresses**: USER, Preset A–D, GM2 and its nine
  variation banks, the internal and GM2 rhythm sets, all three performance
  banks, and every SRX expansion board from SRX-01 to SRX-98 — 57 banks and
  a little over 4000 addressable slots.
- **Names**, from two sources kept deliberately distinct: the printed lists,
  and what the synth itself says. A name read from the hardware is shown in
  bold; one that *disagrees* with the printed list gets a `*`. On a USER
  bank somebody has saved into, that mark is the most useful thing on the
  screen.
- **Read names off the synth.** `r` reads a USER bank directly — genuinely
  read-only, nothing is selected, the instrument keeps playing whatever it
  was playing.
- **Scan a bank.** `s` learns preset and SRX names the only way they can be
  learned: by selecting each slot and reading back. This **plays the
  instrument**, and asks first.
- **Find a fitted SRX board.** `x` probes for it, for boards Roland
  documented after rxved's table was written.
- **Favourites**, with ratings, tags and notes, in a SQLite database in the
  platform's own application-data directory — so a `git clean` in a checkout
  cannot take it:

  | | |
  |---|---|
  | Linux / BSD | `$XDG_DATA_HOME/rxved/favorites.db`, else `~/.local/share/rxved/favorites.db` |
  | macOS | `~/Library/Application Support/rxved/favorites.db` |
  | Windows | `%LOCALAPPDATA%\rxved\favorites.db` |

  Windows uses Local rather than Roaming on purpose: a roaming profile
  copies files wholesale at logon and logoff, and a SQLite database caught
  mid-copy — or opened from two machines against one synced file — is a
  known way to corrupt one. Override any of it with `--favorites`.
- **A CLI** (`rxvcli`) for everything, most of which needs no synth at all.

## Install

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
```

Needs Python 3.11+, `python-rtmidi` and `textual`. On Linux you also need an
ALSA sequencer — `rxvcli ports` will tell you plainly if there isn't one.

## Run

```sh
rxved --demo          # built-in demo synth; opens no MIDI ports
rxved                 # autodetect a real XV-2020
rxved --port "Roland XV-2020:Roland XV-2020 MIDI 1 68:0"
```

Autodetect broadcasts a Universal Identity Request on every bidirectional
port and matches the XV-2020's family *number* — not just its family code,
which it shares with the rest of the XV/JV line — so it will not adopt an
XV-3080 on the same chain. The port that answered is remembered in
`config.toml`, so the next run is one round trip rather than a sweep.

### Keys

| | |
|---|---|
| arrows, `tab` | move; `tab` switches pane |
| `enter` | select this slot **on the synth** — it will sound |
| `f` / `F` | favourite / list favourites |
| `t` / `n` | tags / note (on a favourite) |
| `/` | search names |
| `r` | read this bank's names from the synth (USER banks; read-only) |
| `s` | scan this bank by selecting every slot (**plays the synth**) |
| `x` | probe for a fitted SRX board (**plays the synth**) |
| `F` | cycle: all slots → this bank's favourites → every favourite |
| `C` | filter by category (multi-select), on top of whichever view is showing |
| `[` / `]` / `c` | previous / next send channel, or type one |
| `R` | re-read the synth's mode, channels and all 16 parts |
| `m` | multi-mode setup: all 16 parts, **editable**, and why a channel is silent |
| `i` / `?` / `q` | device identity / help / quit |

### Multi-mode setup (`m`)

The one screen that writes. It shows every Performance Part — its MIDI
settings and its effects routing — and underneath, a report on why any
channel makes no sound.

That report exists because the obvious answer is usually wrong. In PATCH
mode the synth is single-timbral and the parts are not in use at all, so
fifteen channels are silent with every part parameter reading perfectly
normal. Solo Part Select silences fifteen parts from one byte nowhere near
any of them. And a channel no audible part listens on is not a muted
channel. The report tells these apart.

Type a number straight into a numeric cell, `⏎` edits the cell under the
cursor, `space` toggles a switch, `+`/`-` adjust. **Edits go to Temporary Performance — the edit buffer, not a stored
performance — and a power cycle undoes them.** rxved never performs the
Write (store) operation. Each edit is read back and the row shows what the
synth reports, not what was sent.

This matters on an XV-2020 specifically: it is a half-rack module with a
three-digit LED, and OM p. 116 lists what its four controls can reach.
Receive Switch, Mute Switch and Solo Part Select are **not** on that list —
without the editor or SysEx there is no way to see or change them at all.

`tab` switches the middle columns between **MIDI** (ch, rx, lvl, PC, LSB,
MSB) and **FX / routing** (mute, dry, cho, rev, out, mfx); the patch name and
the silence verdict stay in both. Above the table, the performance's MFX type
and routing, chorus and reverb.

Output-assign values this model ignores are shown with a star (`6*`) rather
than hidden — a performance written on an XV-5080 can carry one, and that
explains a silent part where a blank would not.

**Moving the cursor never sends anything.** That is enforced by a test, not
just by intent: it is the property that decides whether you can leave this
open next to a running sequencer.

### Command line

```sh
rxvcli ports                      # MIDI ports, and which look like an XV
rxvcli banks                      # the whole bank table
rxvcli list PST-B                 # one bank's slots
rxvcli find "bass" --kind patch   # search names
rxvcli resolve 87 65 28           # what does this MSB/LSB/PC select?
rxvcli status                     # ask the synth who it is
rxvcli channels                   # what each MIDI channel currently selects
rxvcli read USER                  # read USER names (read-only)
rxvcli select PST-B:29            # select it on the synth
rxvcli scan PST-A --yes           # learn a preset bank's names (plays it)
rxvcli probe-srx --yes            # find the fitted board (plays it)

rxvcli fav --add PST-B:29 --tags "bass, trance" --note "for the intro"
rxvcli fav --tag bass
rxvcli tags
```

`ports`, `banks`, `list`, `find`, `resolve`, `fav` and `tags` construct no
bridge and open no port — they work on a headless box and while somebody
else is using the hardware. `scan` and `probe-srx` refuse to run without
`--yes`, because a shell is exactly where a recalled history line fires
something you did not mean to fire.

## Patch names are not included

rxved ships **no** Roland content. The bank, number, MSB, LSB and program
change are all its own arithmetic; only the names come from Roland, and
those you generate locally:

```sh
python3 tools/extract_catalog.py \
    --srx SRX-07=SRX-07_OM.pdf --srx SRX-08=SRX-08_OM.pdf
```

That reads your own copies of Roland's XV-2020 Editor for Windows, the
Owner's Manual and the Patch Listing, and writes `xv/data/catalog.json`,
which is gitignored. It currently produces **3856 names across 55 banks**: the XV-2020's own
1045, plus every expansion board whose owner's manual you point `--srx` at —
2637 patches and 174 rhythm sets across SRX-01 to SRX-12, with categories,
each split across the LSB pages that select it.

Two weaker routes exist for boards with no manual: `--srx-list` reads a
names-only Faxback sheet, `--srx-names` a hand-written table. Prefer the
manual: it brings categories, and replacing the weaker sources with manuals
found three parsing defects that neither weaker source could reveal on its
own (RESOLUTION_NOTES §11).
Pass `--editor`, `--manual` and `--patch-list` if yours are somewhere other
than the defaults.

Without it, rxved still works — you get numbers instead of names, and `r`
and `s` fill names in from the synth itself.

The editor binary is preferred over the PDFs wherever it reaches: it stores
names as fixed-width 12-byte records, the same width the device uses, so
they come out byte-exact rather than with the apostrophes a PDF text layer
mangles. Its GM records carry their own MSB/LSB/PC, so that mapping is read
rather than inferred. See `docs/RESOLUTION_NOTES.md` §3.

## Reading a marked-up printout

If you print a patch list, go through it at the keyboard with a highlighter
and want the result in software afterwards:

```sh
python3 tools/read_marked_list.py scan.pdf --bank 1=PST-C --bank 2=PST-D --apply
```

It writes a text list beside the PDF and, with `--apply`, adds the rows to
the favourites database. Names come from the local catalog rather than from
OCR of the scan, so they are exact.

The marker fades, and a photocopier and a scanner each fade it further, so
detection measures how far blue runs ahead of red rather than matching a
colour — black text and white paper are both neutral, and any blue lift at
all is the highlighter. Rows that score just under the line are listed rather
than dropped quietly; in practice they are rows sitting directly beneath a
marked one, catching the top edge of its mark.

## How much of this is verified?

**Read `DISCLAIMER.md` before trusting a byte offset.** Short version:

- **Verified** against a real XV-2020 on 2026-09-11: port discovery, the
  Identity Request/Reply, the device-ID numbering, the RQ1/DT1 round trip
  (which confirms the frame layout, model ID, base-128 addressing and the
  checksum rule together), all 128 User Patch addresses, the two System
  Common receive-channel bytes, the Setup block, all 16 Performance Parts,
  and — sent and read back — the Bank Select triples for USER, PST-A, PST-B,
  PST-D and GM, **including the 0-based/1-based split**.
- **Not verified**: rhythm-set and performance triples, the whole SRX
  table, and both timing constants — which are *guesses*, labelled as such
  in the code.

One bug is worth knowing about because of how it hid: `device_id_byte()`
originally accepted "either" a panel number (17–32) or a wire byte
(`0x10`–`0x1F`), and those ranges overlap for every value from 17 to 31.
Panel "17" became `0x11`, every request went to a device that was not there,
and the XV-2020 answers a wrongly addressed request with **silence** —
indistinguishable from powered-off, wrong-port or busy. Every synthetic test
passed, because they all shared the same wrong convention. It took one read
against real hardware to find. `DISCLAIMER.md` has the full account.

## Development

```sh
.venv/bin/python -m pytest      # 187 tests, all synthetic, no hardware
```

Project layout follows the author's sibling **eosed** and **s3ked**
projects — a device-domain package and a UI package that knows nothing about
MIDI:

- `xv/messages.py` — the wire codec. RQ1/DT1, base-128 address arithmetic,
  the checksum that covers the address as well as the data.
- `xv/banks.py` — what MSB, LSB and Program Change select. The table the
  whole project exists to display.
- `xv/catalog.py` — names, and the printed-versus-live distinction.
- `xv/bridge.py` — MIDI transport, throttling, discovery, the reads.
- `rxved/app.py` — the Textual TUI. `rxved/cli.py` — the CLI.
- `rxved/favorites.py` — the favourites database.
- `rxved/demo.py` — a synth stand-in that opens no ports.

`TODO.md` is what's open; `docs/RESOLUTION_NOTES.md` is how each thing was
resolved and where every transcribed number came from.

## License and third-party sources

GPL-2.0-or-later. Full text in `COPYING`; attributions in `LICENSE`.

| What | Source |
|---|---|
| SysEx frame layout, model ID, checksum, address map | XV-2020 Owner's Manual (Roland Corporation, 2003), pp. 140–146 |
| Internal Bank Select map | Same manual, pp. 40–41 and p. 136 |
| SRX Bank Select allocation, all boards | Roland Supplemental Note SN 132 v3.00 (2007), "Selecting Internal and SRX-Series Sounds Via MIDI" |
| Patch / rhythm / performance names | Roland's XV-2020 Editor, Owner's Manual and Patch Listing — read locally, **not distributed** |
| Transport layer | Ported from the author's s3ked, eosed, k2kremote and mpc2emu, all GPL-2.0-or-later |

Roland, XV-2020, SRX, JV and Fantom are trademarks of Roland Corporation.
This is an independent project, not endorsed by or affiliated with Roland,
and uses those names only to say what hardware it talks to.

rxved was written by Jan Lentfer with the assistance of Anthropic's Claude;
see `DISCLAIMER.md` for who did what.
