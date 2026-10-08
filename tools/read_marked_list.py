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

**The signal is a channel lift, not a colour match.** The marker fades, the
photocopier lifts it further, and by the time it reaches the scan some marks
are barely tinted. But black text is neutral and white paper is neutral, so
any positive lift in the marker's own direction is the highlighter and
nothing else. Three markers are offered: ``blue`` (blue ahead of red),
``yellow`` (red and green ahead of blue) and ``orange`` (red ahead of blue).
**Only the blue marker has been tested on real paper** -- a pale blue text
marker on Roland's printed Patch List pages. The yellow and orange signals
are synthetic approximations from the ink colours; their thresholds are
starting points, not measurements, so check the borderline report and use
``--threshold`` if the cut looks wrong.

Rows scoring just under the threshold are reported rather than dropped
quietly. When this was first run, all nine such rows turned out to sit
directly above a row that *is* marked -- what the detector saw was the top
edge of the mark below bleeding into the row's window. That is the expected
shape of a false positive here, and a borderline row that is *not* above a
marked row deserves a look.

Each page is described by its bank and the first patch number in each of
its columns. That is the one thing a person must read off the scan, and it
is what makes everything else checkable: the row counts follow from it, and
they have to add up to the bank's documented size or the tool stops.

Usage::

    python3 tools/read_marked_list.py scan.pdf --page 1=PST-C:1,65
    python3 tools/read_marked_list.py scan.pdf --page 1=SRX-07:1,91,181 --apply
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

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

#: Markers this tool knows. Only ``blue`` has been measured on real paper (a
#: pale blue text marker on Roland's printed Patch List pages); ``yellow``
#: and ``orange`` are synthetic approximations and are untested.
MARKERS = ("blue", "yellow", "orange")

#: Per-pixel lift in the marker's own direction above which a pixel counts as
#: marker rather than paper, ink or scanner noise. Measured for blue; applied
#: to the approximations too, which is why --threshold exists.
LIFT = 8

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

#: Column geometry at :data:`DPI`, per Roland page template, as (number
#: cell, whole row) x ranges. The whole-row range is what rows are located
#: on; the number cell is where the marker is measured.
#:
#: Two templates, picked by how many column starts are given for the page:
#:
#: ``wide``  the internal patch lists -- two half-tables of 64, large type.
#: ``srx``   the expansion-board lists -- three columns, ~90 rows, small
#:           type, and rows whose digits touch vertically.
LAYOUTS = {
    "wide": [
        {"num": (185, 315), "row": (180, 1150)},
        {"num": (1262, 1392), "row": (1255, 2235)},
    ],
    "srx": [
        {"num": (150, 232), "row": (150, 814)},
        {"num": (890, 972), "row": (890, 1561)},
        {"num": (1637, 1719), "row": (1637, 2304)},
    ],
}


def render(pdf: str, outdir: str) -> List[str]:
    """Rasterise every page of the PDF, returning the image paths in order."""
    prefix = os.path.join(outdir, "page")
    try:
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-png", pdf, prefix],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise SystemExit(
            "error: pdftoppm is not installed; it is in poppler-utils."
        ) from None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"error: pdftoppm failed: {exc}") from None
    pages = sorted(
        os.path.join(outdir, name)
        for name in os.listdir(outdir)
        if name.startswith("page") and name.endswith(".png")
    )
    if not pages:
        raise SystemExit(f"error: {pdf} produced no pages")
    return pages


def _centres(
    a: Any, x0: int, x1: int, ytop: int = 440, ybot: int = 3350, minh: int = 10
) -> List[int]:
    """y centre of every run of ink in a number cell."""
    dark = (a[:, :, 0] < 150) & (a[:, :, 1] < 150) & (a[:, :, 2] < 150)
    proj = dark[:, x0:x1].sum(axis=1)
    proj[:ytop] = 0
    proj[ybot:] = 0
    out, start = [], None
    for y, value in enumerate(proj > 0):
        if value and start is None:
            start = y
        elif not value and start is not None:
            if y - start >= minh:
                out.append((start + y) // 2)
            start = None
    if start is not None:
        out.append((start + ybot) // 2)
    return out


#: Fractions of a column's width a horizontal rule must darken to count.
#: Tried in turn: rules print solid on some pages of a scan and broken on
#: others, and a single cutoff that works for one page finds six rules on the
#: next. Only a fraction that yields exactly ``rows + 1`` rules is accepted,
#: so a loose cutoff cannot quietly invent boundaries.
RULE_FRACTIONS = (0.75, 0.6, 0.5, 0.4, 0.3, 0.22)


def _rule_groups(a: Any, x0: int, x1: int, fraction: float) -> List[int]:
    dark = (a[:, :, 0] < 170) & (a[:, :, 1] < 170) & (a[:, :, 2] < 170)
    width = x1 - x0
    hits = np.nonzero(dark[:, x0:x1].sum(axis=1) > width * fraction)[0]
    # -1 rather than None: these are row indices, so a non-negative sentinel
    # keeps the arithmetic plain ints, which the numpy stubs can check. numpy
    # 2 types `hits` as signed integers, and `None` in the sum is a hard
    # error under `mypy --strict`.
    groups: List[int] = []
    start = prev = -1
    for hit in hits:
        y = int(hit)
        if start < 0:
            start = prev = y
        elif y - prev > 2:
            groups.append((start + prev) // 2)
            start = y
        prev = y
    if start >= 0:
        groups.append((start + prev) // 2)
    return groups


def _rule_rows(a: Any, x0: int, x1: int, rows: int) -> Optional[List[float]]:
    """Row centres from the table's own horizontal rules, or ``None``.

    The expansion-board pages rule every row, and a rule is the only feature
    that says exactly where a row begins and ends -- ``rows + 1`` of them
    bound ``rows`` rows, and that count is its own proof. Nothing is returned
    unless the count comes out exactly, so this never guesses.
    """
    for fraction in RULE_FRACTIONS:
        groups = _rule_groups(a, x0, x1, fraction)
        if len(groups) < 3:
            continue
        spacing = float(np.median(np.diff(groups)))
        if spacing <= 0:
            continue
        tol = max(3.0, spacing * 0.2)
        runs, current = [], [groups[0]]
        for previous, y in zip(groups, groups[1:]):
            if abs(y - previous - spacing) <= tol:
                current.append(y)
            else:
                runs.append(current)
                current = [y]
        runs.append(current)
        kept = max(runs, key=len)
        # The rule under the column header sits further above row 1 than rows
        # sit apart -- row 1 gets extra padding beneath the heading -- so the
        # run stops one short of the table's top. Take one more if it is
        # within two row heights.
        if len(kept) == rows:
            index = groups.index(kept[0])
            if index > 0 and spacing < kept[0] - groups[index - 1] <= spacing * 2.2:
                kept.insert(0, groups[index - 1])
        if len(kept) == rows + 1:
            return [(kept[i] + kept[i + 1]) / 2 for i in range(rows)]
    return None


def fit_grid(a: Any, x0: int, x1: int, rows: int) -> Tuple[List[float], int]:
    """Row centres for a column of ``rows`` rows, and how many were accounted.

    Finding the table's real extent is the whole problem, and the obvious
    approaches fail in opposite directions:

    *Stripping the header by its gap* does not work on the internal lists.
    The header sits 41 px above row 1 where rows are 36 apart -- too close to
    the row pitch to tell apart -- so it survives as a "row", the footnote
    lines survive at the other end, and the pitch computed first-to-last
    comes out 43.6 instead of 36. Every row then lands on the wrong one. This
    was caught only because the scan plainly marks USER 2 and the tool said
    USER 3.

    *Requiring a constant gap between rows of text* does not work on the
    expansion-board lists. At that type size the digits of adjacent rows
    touch and merge into a single run of ink, so genuine rows vanish and the
    run of "even" spacing shatters -- 43 rows found where there are 90.

    So: use the table's own rules when it has them, and otherwise take the
    longest stretch of text whose gaps are all near a whole number of row
    pitches, with a tolerance tight enough to exclude a header sitting 5 px
    further out than a row.
    """
    ruled = _rule_rows(a, x0, x1, rows)
    if ruled is not None:
        return ruled, rows

    centres = _centres(a, x0, x1)
    if len(centres) < 2:
        raise SystemExit(f"error: no rows found in x {x0}-{x1}")
    gaps = np.diff(centres)
    typical = float(np.median([g for g in gaps if 20 <= g <= 60] or gaps))

    # Take the longest stretch of rows whose spacing is the row pitch, and
    # anchor on its LAST row.
    #
    # Both ends of an unruled table are surrounded by things that look like
    # rows. Above sit the bank title, the handwritten bank-select note and
    # the column header -- the header only a few pixels further out than a
    # row, which is why stripping by gap size cannot find the top. Below sits
    # the footnote. Anchoring at the top put every row of PST-B 65-128 one
    # place low; scanning for the first big gap found the gap above the
    # header instead of the one below the table and collapsed to a single
    # row. The longest regular stretch is neither of those things.
    low, high = typical * 0.82, typical * 1.25
    best_start = best_len = 0
    start = 0
    for i, gap in enumerate(gaps):
        if low <= gap <= high:
            if i - start + 1 > best_len:
                best_start, best_len = start, i - start + 1
        else:
            start = i + 1
    if best_len == 0:
        raise SystemExit(f"error: no regular row spacing in x {x0}-{x1}")
    first_index, last_index = best_start, best_start + best_len
    first, last = centres[first_index], centres[last_index]
    accounted = best_len + 1
    pitch = (last - first) / (accounted - 1)
    return [last - (rows - 1 - n) * pitch for n in range(rows)], accounted


def marker_lift(rgb, marker: str):
    """The per-pixel marker signal for one marker, as an integer array.

    Each is the difference between the channels the marker reflects and the
    channels it absorbs: blue reflects blue and absorbs red; yellow reflects
    red and green and absorbs blue; orange reflects red and absorbs blue and
    some green. Black text and white paper are neutral in all three.
    """
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    if marker == "blue":
        return blue - red
    if marker == "yellow":
        return np.minimum(red, green) - blue
    if marker == "orange":
        return red - blue
    raise SystemExit(
        f"error: unknown marker {marker!r}; choose from {', '.join(MARKERS)}"
    )


def score_page(
    path: str, layout: str, starts: List[int], counts: List[int], marker: str = "blue"
) -> Dict[int, int]:
    """``{patch number: marked pixel count}`` for one page.

    ``starts`` is the first patch number in each column and ``counts`` how
    many rows each holds; together they say what every row on the page is.
    ``marker`` selects the channel lift that counts as a mark.
    """
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    mask = np.clip(marker_lift(a, marker), 0, None) >= LIFT
    columns = LAYOUTS[layout]
    if len(starts) != len(columns):
        raise SystemExit(
            f"error: {path} was given {len(starts)} column starts but the "
            f"{layout!r} template has {len(columns)}"
        )
    scores: Dict[int, int] = {}
    for cols, start, rows in zip(columns, starts, counts):
        if rows <= 0:
            continue
        centres, found = fit_grid(a, *cols["row"], rows)
        pitch = ((centres[-1] - centres[0]) / (rows - 1)) if rows > 1 else 30
        # One over is the column header joining the regular stretch, which
        # is harmless: the grid is anchored on the LAST row, not the first.
        # Fewer than expected means rows were lost, and then every row on the
        # column is in doubt.
        if not rows <= found <= rows + 1:
            print(
                f"warning: {os.path.basename(path)} column starting at "
                f"{start}: expected {rows} rows, the evenly spaced stretch "
                f"of ink accounts for {found}. Every row on this column is "
                f"suspect; check it with --verify before trusting it.",
                file=sys.stderr,
            )
        x0, x1 = cols["num"]
        for n in range(rows):
            cy = centres[n]
            lo, hi = int(cy - pitch * 0.45), int(cy + pitch * 0.45)
            scores[start + n] = int(mask[lo:hi, x0:x1].sum())
    return scores


def auto_threshold(scores: Dict[int, int]) -> int:
    """The cut between marked and unmarked, from the data itself.

    The marker's strength varies with the pen, the photocopier and the cell
    size, so a constant does not travel between documents. What does travel
    is the shape: a large cluster at nearly zero, a large cluster well above
    it, and a wide empty band between. This returns the middle of the widest
    such band.
    """
    values = sorted(scores.values())
    if not values:
        return THRESHOLD
    top = values[-1]
    best_gap, cut = 0, THRESHOLD
    for i in range(len(values) - 1):
        low, high = values[i], values[i + 1]
        # Only consider the empty band in the middle of the range: the tail
        # above is spacing between genuinely marked rows.
        if not (top * 0.05 <= low <= top * 0.55):
            continue
        if high - low > best_gap:
            best_gap, cut = high - low, (low + high) // 2
    return cut


def parse_pages(values: List[str] | None) -> Dict[int, Tuple[str, List[int]]]:
    """``--page 1=SRX-07:1,91,181`` -> ``{1: ("SRX-07", [1, 91, 181])}``."""
    out: Dict[int, Tuple[str, List[int]]] = {}
    for item in values or []:
        page, _, rest = item.partition("=")
        card, _, starts = rest.partition(":")
        try:
            number = int(page)
            firsts = [int(v) for v in starts.split(",") if v.strip()]
        except ValueError:
            raise SystemExit(
                f"error: --page wants N=BANK:first,first,... , got {item!r}"
            )
        if not card or not firsts:
            raise SystemExit(
                f"error: --page {item!r} is missing a bank or its column starts"
            )
        out[number] = (card, firsts)
    return out


def resolve(
    pages: Dict[int, Tuple[str, List[int]]], totals: Dict[str, int]
) -> Dict[int, List[int]]:
    """Work out how many rows each column holds, and check the arithmetic.

    Each column runs from its own first number to just before the next
    column's, and the last column of a bank runs to that bank's total. If the
    resulting counts do not add up to the total, the column starts were
    misread and nothing further is worth doing.
    """
    order: Dict[str, List[Tuple[int, int]]] = {}
    for page in sorted(pages):
        card, firsts = pages[page]
        for index, first in enumerate(firsts):
            order.setdefault(card, []).append((first, page, index))
    plan: Dict[int, List[int]] = {}
    for card, entries in order.items():
        entries.sort()
        total = totals[card]
        for i, (first, page, index) in enumerate(entries):
            nxt = entries[i + 1][0] if i + 1 < len(entries) else total + 1
            plan.setdefault(page, [])
            while len(plan[page]) <= index:
                plan[page].append(0)
            plan[page][index] = nxt - first
        counted = sum(
            nxt - first
            for i, (first, _p, _c) in enumerate(entries)
            for nxt in [entries[i + 1][0] if i + 1 < len(entries) else total + 1]
        )
        if counted != total:
            raise SystemExit(
                f"error: the column starts given for {card} describe "
                f"{counted} patches, but {card} has {total}. One of the "
                f"first numbers is wrong; nothing has been written."
            )
        print(
            f"{card}: {counted} patches across "
            f"{len(entries)} columns — matches the documented total"
        )
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="read_marked_list.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", help="scanned patch list")
    parser.add_argument(
        "--page",
        action="append",
        default=None,
        metavar="N=BANK:first,first",
        help="a page: its bank and the first patch number in each of its "
        "columns, e.g. --page 1=SRX-07:1,91,181 or --page 5=PST-B:1,65",
    )
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        help="marked-pixel cut (default: from the data)",
    )
    parser.add_argument(
        "--marker",
        choices=MARKERS,
        default="blue",
        help="highlighter colour: blue (tested on real paper), or the "
        "synthetic yellow/orange approximations",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="also add the rows to the favourites database",
    )
    parser.add_argument("--favorites", default=None)
    parser.add_argument("--note", default=None)
    return parser


def _bank_for(card: str, number: int) -> Tuple[str, int]:
    """Map a card-wide patch number to the bank and slot that select it.

    An expansion board's patches are numbered 1..N straight through in its
    manual, but selecting one needs the LSB page it falls on -- SRX-07's
    patch 300 is LSB 13, slot 44. :mod:`xv.banks` already holds that split.
    """
    try:
        card_def = banks.srx_card(card)
    except LookupError:
        return card, number  # an internal bank: numbers are slots
    page, slot = divmod(number - 1, 128)
    return f"{card}-{page + 1}", slot + 1


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    pages = parse_pages(args.page)
    if not pages:
        raise SystemExit("error: describe each page, e.g. --page 1=SRX-07:1,91,181")

    totals: Dict[str, int] = {}
    for card, _firsts in pages.values():
        if card in totals:
            continue
        try:
            totals[card] = banks.srx_card(card).patch_count
        except LookupError:
            totals[card] = banks.bank(card).count
    plan = resolve(pages, totals)

    catalog = cat.load()
    source = os.path.basename(args.pdf)
    note = args.note if args.note is not None else f"marked in {source}"

    body: List[str] = []
    notes: List[str] = []
    chosen: List[Tuple[str, int, str]] = []

    with tempfile.TemporaryDirectory() as tmp:
        images = render(args.pdf, tmp)
        for page in sorted(pages):
            if page > len(images):
                raise SystemExit(f"error: {source} has no page {page}")
            card, firsts = pages[page]
            counts = plan[page]
            layout = "srx" if len(firsts) == 3 else "wide"
            scores = score_page(
                images[page - 1], layout, firsts, counts, marker=args.marker
            )
            cut = args.threshold or auto_threshold(scores)
            hits = sorted(n for n, v in scores.items() if v >= cut)
            near = sorted(n for n, v in scores.items() if cut * 0.5 <= v < cut)
            body.append(f"{card}   page {page}   [{layout} template, cut at {cut}]")
            body.append(f"{len(hits)} marked")
            body.append("")
            for number in hits:
                bank_id, slot_number = _bank_for(card, number)
                slot = banks.slot(bank_id, slot_number)
                name = catalog.display_name(bank_id, slot_number)
                body.append(
                    f"  {number:3d}  {name:<14} "
                    f"{bank_id:<9} slot {slot_number:3d}  "
                    f"MSB {slot.msb:3d}  LSB {slot.lsb:3d}  "
                    f"PC {slot.program_change:3d}"
                )
                chosen.append((bank_id, slot_number, name))
            body.append("")
            if near:
                notes.append(
                    f"page {page} ({card}): "
                    + ", ".join(f"{n} ({scores[n]})" for n in near)
                )

    if notes:
        body.append(
            "Borderline rows, NOT included. Check whether each sits "
            "directly above a row"
        )
        body.append(
            "that IS marked -- if so it is the mark below bleeding "
            "into this row's window,"
        )
        body.append("which is the usual false positive here and correctly excluded.")
        body.append("")
        body += [f"  {n}" for n in notes]
        body.append("")
    body.append(f"Total: {len(chosen)} favourites")

    header = [
        f"XV-2020 favourites, read from {source}",
        "",
        f"Rows marked with a {args.marker} highlighter on a printed patch",
        "list, detected by measuring the marker's channel lift over each",
        "number cell. Names come from the local catalog where it has them,",
        "not from OCR of the scan.",
        "",
    ]
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
            for bank_id, number, name in chosen:
                if (bank_id, number) in store:
                    present += 1
                    continue
                store.add(
                    bank_id, number, name=catalog.name(bank_id, number) or "", note=note
                )
                added += 1
            print(
                f"\n{store.path}: added {added}, already present "
                f"{present}; {len(store)} favourites in total"
            )
        finally:
            store.close()
    else:
        print("\n(nothing written to the favourites database; pass --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
