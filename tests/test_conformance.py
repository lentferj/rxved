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

"""rxved keeps the contract the other eight projects in the family keep.

Every check below is the same one, run against this program rather than
written out again here. The reasons are in docs/UX-SPEC.md section 5 and in
the library: what drifted was never a bug anyone chose, it was a copy that
nobody had a second copy to compare against.
"""

from rxved.app import KEY_HINTS, RxvedApp, build_parser
from rxved.cli import build_parser as build_cli_parser
from vinsynlib import conformance, spec
from xv.terms import TERMS

#: This tool's own hints, named once. `rxved.screens.KEY_HINTS` is built
#: from `keys.legend(*EXTRA_HINTS)`, so the two cannot drift; naming them
#: again here is what the library's legend check needs to know which blocks
#: are meant to be ours and which are the family's.
EXTRA_HINTS = (
    "s scan bank",
    "x probe SRX",
    "C categories",
    "R re-read",
    "m multi setup",
    "z undo",
    "Z undo all",
)


def test_the_browser_flags_match_the_family() -> None:
    # A tuple rather than the set in the template: check_flags takes a
    # Sequence, and mypy is right that a set is not one.
    assert not conformance.check_flags(
        build_parser(),
        required=(
            "port",
            "scan",
            "recv-port",
            "channel",
            "device-id",
            "demo",
            "timeout",
            "catalog",
            "config",
            "favorites",
        ),
    )


def test_the_browser_binds_the_shared_keys() -> None:
    # select=True, and unlike the two editors this tool does bind Enter to
    # an action. It is not a *priority* binding on purpose: a priority Enter
    # would fire over modal screens too, which silently broke the category
    # picker, so it arrives as the table's own RowSelected and calls
    # action_select_slot from there. The key works and the legend promises it.
    assert not conformance.check_bindings(
        RxvedApp, favourites=True, channel=True, select=True
    )


def test_the_legend_is_the_family_legend() -> None:
    assert not conformance.check_legend(KEY_HINTS, extras=EXTRA_HINTS)


def test_the_two_front_ends_agree_on_the_shared_flags() -> None:
    """`rxved` and `rxvcli` are two front doors to one program.

    The spec says so: "every tool has two front ends with the same parser
    options", same help text, same exit codes. It is scoped to the family's
    canonical flags rather than to everything either parser has, because
    rxved's own flags are deliberately its own and need not be on both --
    `--backup-dir` is the TUI's, and `rxvcli` spells it `--dir` on the three
    `perf-*` commands that use it.

    Both are built by :func:`vinsynlib.cli.add_common_arguments`, so the
    remaining risk is the words being passed differently at the two call
    sites, which is exactly what this catches.
    """
    browser = {a.dest for a in build_parser()._actions}
    pipe = {a.dest for a in build_cli_parser()._actions}
    shared = {f.name.replace("-", "_") for f in spec.CANONICAL_FLAGS}
    missing = sorted(shared & browser - pipe)
    assert not missing, (
        f"rxvcli no longer offers {missing}; two front doors to one program "
        f"take the same options"
    )


def test_the_vocabulary_is_declared() -> None:
    assert not conformance.check_terms(TERMS)
