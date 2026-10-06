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

"""The local favourites database.

Now a binding of :mod:`vinsynlib.favorites` rather than a sixth copy of it.
The schema, the migration, the cross-thread lock and the per-platform path
rules are the family's and live in one place -- see that module's docstring
for why a favourites store is keyed on the slot rather than the name, why it
is SQLite, and why it lives in the *data* directory rather than beside the
checkout.

What is added here is the name, and it is a parameter rather than a constant.
``APP_NAME`` used to be a module global that :func:`data_dir` read, which
meant a shared implementation could only ever know one application's name.
:class:`Favorites` is a subclass rather than a rebinding for the same
reason: the library's default ``app_name`` is its own, and a bare
``Favorites()`` must land in *rxved*'s data directory -- that file is the
user's own work, and ``--favorites`` should not be the only way to find it.

rxved has nothing to declare beyond that. It keys a favourite on the
bank id and the displayed number (``PST-B:029``), which is the family's
scheme, and the ``PST-``/``RHY-``/``PRF-`` bank ids come from
:mod:`xv.banks` -- this instrument's business, not the family's.
"""

from __future__ import annotations

from typing import Any

from vinsynlib import favorites as _favorites
from vinsynlib.favorites import SCHEMA_VERSION, Favorite

__all__ = [
    "APP_NAME",
    "DB_NAME",
    "SCHEMA_VERSION",
    "Favorite",
    "Favorites",
    "data_dir",
    "default_path",
]

#: The directory name used under whichever per-platform data root applies.
APP_NAME = "rxved"

#: The database's filename, in that directory.
DB_NAME = "favorites.db"


def data_dir() -> str:
    """This application's per-platform data directory.

    Data, not configuration: favourites are the user's own work -- the one
    thing in this project worth backing up -- while ``config.toml`` is a
    disposable cache of which port answered last.
    """
    return _favorites.data_dir(APP_NAME)


def default_path() -> str:
    """Where the favourites database lives, per platform.

    Overridable everywhere it is used -- ``--favorites`` on both front ends --
    so a user who wants it beside a project, on a stick, or in a synced
    folder can say so.
    """
    return _favorites.default_path(APP_NAME, DB_NAME)


class Favorites(_favorites.Favorites):
    """A favourites store that defaults to *this* application's directory.

    A subclass rather than a rebinding because the base class defaults
    ``app_name`` to the library's own name -- correct for a library that
    cannot know its caller, wrong here.
    """

    def __init__(
        self, path: str | None = None, *, db_name: str = DB_NAME, **kwargs: Any
    ) -> None:
        kwargs.setdefault("app_name", APP_NAME)
        super().__init__(path, db_name=db_name, **kwargs)

    def __enter__(self) -> Favorites:
        # Narrowed from the base class's own return type, so that
        # ``with Favorites(...) as db`` type-checks as this subclass. The
        # base returns itself; this says so in a type the fixtures can use.
        return self
