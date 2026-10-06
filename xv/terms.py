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

"""What the Roland calls things, declared once.

**The instrument's word is the instrument's.** The XV-2020's own panel and
manual say PATCH and BANK, so those are the words every string a user reads
uses here -- the list view, the pane headings, the CLI help, the README.

The wire's word is different and stays in the code that builds wire
messages: on the wire this is a **Program Change**, and the three bytes that
select one -- Bank Select MSB, Bank Select LSB, Program Change -- are what
the first pane of rxved exists to print in that order. That translation is
not a naming exercise, so it stays in :mod:`xv.banks` where it is visible.

**A bank is not one thing.** This synth has patches, rhythm sets and
performances in separate banks, all selectable by the same three bytes with
a different MSB, and the difference changes what a program change *does*
(loading a rhythm set rewrites the kit the parts play from). ``own`` names
those three, so a tool can say "rhythm set" rather than "sound" without the
family having an opinion about it -- which is exactly what ``own`` is for.
"""

from __future__ import annotations

from vinsynlib.terms import Terminology, register

__all__ = ["TERMS"]

TERMS = Terminology(
    app_name="rxved",
    sound="patch",
    container="bank",
    device="Roland XV-2020",
    own={
        # The other two kinds of thing a Bank Select can reach on this
        # synth. A rhythm set is not a sound -- selecting one rewrites the
        # kit -- and a performance is up to sixteen parts rather than one
        # sound. Neither is a synonym for patch, so neither is renamed to it.
        "rhythm": "rhythm set",
        "performance": "performance",
        # What the catalog holds for a ROM or expansion bank, as opposed to
        # one this tool can write to.
        "read only": "ROM",
    },
)

#: Registered at import so the family's registry knows what this tool calls
#: its concepts. Registration rather than a monkeypatched module global: a
#: library cannot know the name of the program using it, and passing it in
#: is honest where patching is global.
register(TERMS)
