<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
-->

# rxved

A terminal browser for the **Roland XV-2020**'s sounds.

Two panes: every bank the synth can be sent to on the left, that bank's
slots on the right, and on every row the three numbers that actually select
a sound — Bank Select MSB, Bank Select LSB and Program Change.

```
 bank       kind    n    fav      #    name          MSB  LSB  PC   cat  fav
 USER       patch   128           003  Folded Keys     87   65   2    DGT
 PST-A      patch   128  1        004  Static Pluck    87   65   3    DGT
▸PST-B      patch   128  1        005  Woven Bass     87   65   4    BS
 PST-C      patch   128           …
 GM         patch   128          ▸029  Iron Drone      87   65   28   SBS  *

 PRESET B 029  Iron Drone
 Bank Select MSB 87 (CC#0)   LSB 65 (CC#32)   Program Change 28 (wire,
 0-based; the display shows 29)
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
| `i` / `?` / `q` | device identity / help / quit |

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
rxvcli channels                   # which MIDI channels it listens on
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
python3 tools/extract_catalog.py
```

That reads your own copies of Roland's XV-2020 Editor for Windows, the
Owner's Manual and the Patch Listing, and writes `xv/data/catalog.json`,
which is gitignored. It currently produces **1044 names across 22 banks**.
Pass `--editor`, `--manual` and `--patch-list` if yours are somewhere other
than the defaults.

Without it, rxved still works — you get numbers instead of names, and `r`
and `s` fill names in from the synth itself.

The editor binary is preferred over the PDFs wherever it reaches: it stores
names as fixed-width 12-byte records, the same width the device uses, so
they come out byte-exact rather than with the apostrophes a PDF text layer
mangles. Its GM records carry their own MSB/LSB/PC, so that mapping is read
rather than inferred. See `docs/RESOLUTION_NOTES.md` §3.

## How much of this is verified?

**Read `DISCLAIMER.md` before trusting a byte offset.** Short version:

- **Verified** against a real XV-2020 on 2026-09-11: port discovery, the
  Identity Request/Reply, the device-ID numbering, the RQ1/DT1 round trip
  (which confirms the frame layout, model ID, base-128 addressing and the
  checksum rule together), all 128 User Patch addresses, and the two System
  Common receive-channel bytes.
- **Not verified**: every Bank Select triple, the whole SRX table, the
  performance and rhythm addresses, and both timing constants — which are
  *guesses*, labelled as such in the code.

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
.venv/bin/python -m pytest      # 172 tests, all synthetic, no hardware
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
