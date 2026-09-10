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

"""Terminal browser for the Roland XV-2020's sounds.

The user-facing domain: :mod:`rxved.app` (Textual TUI, ``rxved``),
:mod:`rxved.cli` (argparse, ``rxvcli``), :mod:`rxved.favorites` (the local
favourites database) and :mod:`rxved.demo` (a bridge stand-in so both front
ends run with no hardware and no MIDI ports open).

The protocol and transport live in the sibling :mod:`xv` package.
"""

__version__ = "0.1.0"
