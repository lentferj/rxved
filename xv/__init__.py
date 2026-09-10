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

"""Roland XV-2020: protocol, addressing and transport.

The device domain. Knows nothing about any user interface -- the split
mirrors the sibling eosed (``eos``/``eosed``) and s3ked (``s3k``/``s3ked``)
projects:

* :mod:`xv.messages` -- the wire codec. Roland's address-mapped SysEx: RQ1,
  DT1, the base-128 address arithmetic and the checksum that covers the
  address as well as the data.
* :mod:`xv.banks` -- what Bank Select MSB, LSB and Program Change select.
  The table this whole project is built to show.
* :mod:`xv.catalog` -- names for those slots, and the distinction between a
  name read from a book and one read from the hardware.
* :mod:`xv.bridge` -- how the bytes get there. MIDI transport, throttling,
  port discovery by Identity Request, and the read operations.

Two facts shape everything here. The XV-2020's **preset banks have no
addresses** in the parameter map -- they are ROM, and the only way to read a
preset name off the device is to select it and read the temporary area, which
makes it play. And a **request the device cannot serve is answered with
silence**, so a timeout never distinguishes "absent" from "busy".
"""

__all__ = ["messages", "banks", "catalog", "bridge"]
