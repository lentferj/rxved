#!/usr/bin/env python3
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

"""Read highlighter marks off a scanned patch list into the favourites store.

For the perfectly reasonable workflow of printing a patch list, going through
it at the keyboard with a highlighter, and wanting the result in software
afterwards.

Input is a scan of Roland's printed Patch List pages -- two half-tables per
page, ``No. | Name | Category | Category No. | Voice | Key Assign`` -- with
rows marked in pale highlighter. Output is a text list beside the PDF and,
with ``--apply``, rows in the favourites database.

Three things about the detection are worth knowing, because the first two
approaches to each were wrong:

**Rows are anchored from the bottom.** The page header sits at a different
height on each page, so clipping a fixed number of pixels off the top
truncates the first data row on one page and not the other -- which shifts
every row by one and mis-assigns every favourite, silently and plausibly.
The last data row is found as the band before the large gap above the
footnote, and the other 63 are stepped back from it at the measured pitch.

**Rows are segmented on the whole row, not the number column.** Where the
marker is dense it washes out the digits underneath, so a row can be marked
*and* invisible to a detector looking for dark pixels in the number cell.

**The signal is blue-minus-red, not a colour match.** The marker fades, the
photocopier lifts it further, and by the time it reaches the scan some marks
are barely tinted. But black text is neutral and white paper is neutral, so
any positive blue lift at all is the highlighter and nothing else.

Rows scoring just under the threshold are reported rather than dropped
quietly. When this was first run, all nine such rows turned out to sit
directly above a row that *is* marked -- what the detector saw was the top
edge of the mark below bleeding into the row's window. That is the expected
shape of a false positive here, and a borderline row that is *not* above a
marked row deserves a look.

Usage::

    python3 tools/read_marked_list.py scan.pdf --bank 1=PST-C --bank 2=PST-D
    python3 tools/read_marked_list.py scan.pdf --bank 1=PST-C --apply
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple

try:
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover - a clear message beats a traceback
    raise SystemExit(
        "error: this tool needs numpy and Pillow.\n"
        "       .venv/bin/pip install numpy pillow"
    )

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xv import banks  # noqa: E402
from xv import catalog as cat  # noqa: E402

#: Render resolution. The row pitch is 36 px here, which is enough for the
#: bands to separate cleanly.
DPI = 300

#: Blue lift (blue channel minus red) that counts as marker rather than
#: paper, ink or scanner noise.
BLUE_LIFT = 8

#: Marked pixels in a number cell above which the row counts as highlighted.
#: Set at the natural gap in the sorted scores, which is wide on real scans:
#: on the first sheet this was used against, page 1 jumped 239 -> 371 and
#: page 2 jumped 268 -> 553, with every confirmed row scoring 615 or more.
THRESHOLD = 300

#: Scores in this band are reported for a human to adjudicate.
BORDERLINE = (150, THRESHOLD)

#: Bands further apart than this multiple of the row pitch are the gap above
#: the page footnote.
GAP_FACTOR = 1.6

#: x ranges for the two half-tables at :data:`DPI`, as (number cell, whole
#: row). The whole-row range is what rows are segmented on.
COLUMNS = (
    (0, {"num": (185, 315), "row": (180, 1150)}),
    (64, {"num": (1262, 1392), "row": (1255, 2235)}),
)

ROWS_PER_HALF = 64


def render(pdf: str, outdir: str) -> List[str]:
    """Rasterise every page of the PDF, returning the image paths in order."""
    prefix = os.path.join(outdir, "page")
    try:
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-png", pdf, prefix],
            check=True, capture_output=True,
        )
    except FileNotFoundError:
        raise SystemExit(
            "error: pdftoppm is not installed; it is in poppler-utils."
        ) from None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"error: pdftoppm failed: {exc}") from None
    pages = sorted(
        os.path.join(outdir, name) for name in os.listdir(outdir)
        if name.startswith("page") and name.endswith(".png")
    )
    if not pages:
        raise SystemExit(f"error: {pdf} produced no pages")
    return pages


def _bands(a, x0: int, x1: int, ytop: int = 600) -> List[Tuple[int, int]]:
    dark = (a[:, :, 0] < 140) & (a[:, :, 1] < 140) & (a[:, :, 2] < 140)
    proj = dark[:, x0:x1].sum(axis=1)
    proj[:ytop] = 0
    out, start = [], None
    for y, value in enumerate(proj):
        if value > 0 and start is None:
            start = y
        elif value == 0 and start is not None:
            if y - start >= 8:
                out.append((start, y))
            start = None
    return out


def row_centres(a, x0: int, x1: int, count: int = ROWS_PER_HALF):
    """``(centres, pitch)`` for the data rows, anchored on the last one."""
    bands = _bands(a, x0, x1)
    if len(bands) < count // 2:
        raise SystemExit(
            f"error: only {len(bands)} text bands in x {x0}-{x1}; is this a "
            f"Roland patch list page at {DPI} dpi?"
        )
    centres = [(s + e) // 2 for s, e in bands]
    pitch = float(np.median(np.diff(centres)))
    last = centres[-1]
    for i in range(len(centres) - 1):
        if centres[i + 1] - centres[i] > pitch * GAP_FACTOR:
            last = centres[i]
            break
    return [last - (count - n) * pitch for n in range(1, count + 1)], pitch


def score_page(path: str) -> Dict[int, int]:
    """``{slot number: marked pixel count}`` for all 128 rows on a page."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    mask = np.clip(a[:, :, 2] - a[:, :, 0], 0, None) >= BLUE_LIFT
    scores: Dict[int, int] = {}
    for base, cols in COLUMNS:
        centres, pitch = row_centres(a, *cols["row"])
        x0, x1 = cols["num"]
        for n, cy in enumerate(centres, start=1):
            lo, hi = int(cy - pitch * 0.5), int(cy + pitch * 0.5)
            scores[base + n] = int(mask[lo:hi, x0:x1].sum())
    return scores


def _parse_banks(values: List[str]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for item in values:
        page, _, bank_id = item.partition("=")
        try:
            number = int(page)
        except ValueError:
            raise SystemExit(f"error: --bank wants PAGE=BANK, got {item!r}")
        banks.bank(bank_id)          # fail now, not after a minute of work
        out[number] = bank_id
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="read_marked_list.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", help="scanned patch list")
    parser.add_argument(
        "--bank", action="append", default=[], metavar="PAGE=BANK",
        help="which bank each 1-based page is, e.g. --bank 1=PST-C")
    parser.add_argument("-o", "--output", default=None,
                        help="text list (default: the PDF's name, .txt)")
    parser.add_argument("--threshold", type=int, default=THRESHOLD)
    parser.add_argument("--apply", action="store_true",
                        help="also add the rows to the favourites database")
    parser.add_argument("--favorites", default=None)
    parser.add_argument("--note", default=None,
                        help="note stored on each favourite (default: the "
                             "PDF's filename)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    page_banks = _parse_banks(args.bank)
    if not page_banks:
        raise SystemExit(
            "error: say which bank each page is, e.g. --bank 1=PST-C")

    catalog = cat.load()
    source = os.path.basename(args.pdf)
    note = args.note if args.note is not None else f"marked in {source}"

    header = [
        f"XV-2020 favourites, read from {source}",
        "",
        "Rows marked with a highlighter on a printed patch list, detected by",
        "measuring the blue-minus-red lift over each number cell. A row "
        "counts as",
        f"marked above {args.threshold} marked pixels. Names come from the "
        f"local catalog,",
        "not from OCR of the scan.",
        "",
    ]
    body: List[str] = []
    notes: List[str] = []
    chosen: List[Tuple[str, int]] = []

    with tempfile.TemporaryDirectory() as tmp:
        pages = render(args.pdf, tmp)
        for index, path in enumerate(pages, start=1):
            bank_id = page_banks.get(index)
            if bank_id is None:
                notes.append(f"page {index}: no --bank given, skipped")
                continue
            entry = banks.bank(bank_id)
            scores = score_page(path)
            hits = sorted(n for n, v in scores.items()
                          if v >= args.threshold and n <= entry.count)
            near = sorted(n for n, v in scores.items()
                          if BORDERLINE[0] <= v < args.threshold
                          and n <= entry.count)
            body.append(f"{entry.label} ({bank_id})   MSB {entry.msb} | "
                        f"LSB {entry.lsb}   [page {index}]")
            body.append(f"{len(hits)} marked")
            body.append("")
            for number in hits:
                slot = banks.slot(bank_id, number)
                name = catalog.display_name(bank_id, number)
                row = catalog.entry(bank_id, number)
                body.append(
                    f"  {number:3d}  {name:<14} "
                    f"{(row.category or '') if row else '':<4}"
                    f"  MSB {slot.msb:3d}  LSB {slot.lsb:3d}  "
                    f"PC {slot.program_change:3d}")
                chosen.append((bank_id, number))
            body.append("")
            if near:
                notes.append(
                    f"{bank_id}: " + ", ".join(f"{n} ({scores[n]})"
                                               for n in near))

    if notes:
        body.append("Borderline rows, NOT included. Check whether each sits "
                    "directly above a row")
        body.append("that IS marked -- if so it is the mark below bleeding "
                    "into this row's window,")
        body.append("which is the usual false positive here and correctly "
                    "excluded.")
        body.append("")
        body += [f"  {n}" for n in notes]
        body.append("")
    body.append(f"Total: {len(chosen)} favourites")

    out = args.output or os.path.splitext(args.pdf)[0] + ".txt"
    with open(out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(header + body) + "\n")
    print("\n".join(body))
    print(f"\nwritten to {out}")

    if args.apply:
        from rxved.favorites import Favorites

        store = Favorites(args.favorites)
        try:
            added = present = 0
            for bank_id, number in chosen:
                if (bank_id, number) in store:
                    present += 1
                    continue
                store.add(bank_id, number,
                          name=catalog.name(bank_id, number) or "", note=note)
                added += 1
            print(f"\n{store.path}: added {added}, already present {present}; "
                  f"{len(store)} favourites in total")
        finally:
            store.close()
    else:
        print("\n(nothing written to the favourites database; pass --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
