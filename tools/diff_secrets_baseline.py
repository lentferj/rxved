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

"""Compare two detect-secrets baselines, ignoring the timestamp.

`detect-secrets scan --baseline FILE` rewrites `generated_at` on every
run, including when it found nothing. Pointed at the tracked baseline --
which is the obvious thing to do, and what the tool's own docs suggest --
that means `make check` leaves the working tree dirty every time, and
every later commit carries a one-line diff that means nothing.

So `make audit-secrets` scans into a scratch copy and runs this. The
timestamp is dropped; everything else must match, and any difference is
reported and fails the check.

Exit codes: 0 identical (ignoring the timestamp), 1 different, 2 the
comparison itself could not be made.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List


def load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    return payload


def findings(payload: Dict[str, Any]) -> List[str]:
    """One line per recorded finding, sorted, so order never causes a diff."""
    results = payload.get("results") or {}
    if not isinstance(results, dict):
        return []
    out = []
    for filename, entries in results.items():
        if not isinstance(entries, list):
            out.append(f"{filename}: <malformed>")
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            out.append(
                f"{filename}:{entry.get('line_number', '?')} "
                f"{entry.get('type', '?')} "
                f"{entry.get('hashed_secret', '?')}"
            )
    return sorted(out)


def describe(path: str, payload: Dict[str, Any]) -> str:
    """One line per non-findings field that differs."""
    notes = []
    if payload.get("version") is None:
        notes.append(f"{path}: no 'version'")
    for key in ("plugins_used", "filters_used"):
        new = json.dumps(payload.get(key), sort_keys=True)
        if new == "null":
            notes.append(f"{path}: no '{key}'")
    return "; ".join(notes)


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} BASELINE SCAN", file=sys.stderr)
        return 2

    tracked_path, scan_path = argv[1], argv[2]
    try:
        tracked = load(tracked_path)
        scanned = load(scan_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"could not compare baselines: {exc}", file=sys.stderr)
        return 2

    problems = [
        note
        for note in (describe(tracked_path, tracked), describe(scan_path, scanned))
        if note
    ]

    before, after = findings(tracked), findings(scanned)
    if before != after:
        added = [line for line in after if line not in before]
        removed = [line for line in before if line not in after]
        for line in added:
            problems.append(f"NEW:     {line}")
        for line in removed:
            problems.append(f"REMOVED: {line}")

    if not problems:
        print("no secrets found beyond the baseline")
        return 0

    print("detect-secrets: the scan does not match .secrets.baseline", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(
        "\nIf these are real, review each one, then run `make baseline` and\n"
        "commit the regenerated baseline. If it is a false positive, silence\n"
        "that line with `# pragma: allowlist secret` rather than widening the\n"
        "baseline: a baseline entry is a record that a human looked at it.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
