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

"""``rxvcli`` -- the same information as the TUI, in a pipe.

Most of these commands need no synth and no MIDI stack at all: the bank
table, the program numbers and the name catalog are rxved's own data. Those
are listed in :data:`_OFFLINE` and never construct a bridge, so they work on
a headless box, in a container, and while somebody else is using the
hardware.

``select`` and ``scan`` are the two that make the instrument sound, and
``scan`` -- which sends a program change per slot -- refuses to run without
``--yes``. A shell is exactly the place where a recalled history line fires
something you did not mean to fire, and 475 program changes into a live set
is not a recoverable mistake.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional

from xv import banks
from xv import catalog as cat

__all__ = ["main", "build_parser"]


def _fmt_table(rows: List[List[str]], headers: List[str]) -> str:
    if not rows:
        return ""
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    out.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        out.append("  ".join(cell.ljust(widths[i])
                             for i, cell in enumerate(row)).rstrip())
    return "\n".join(out)


def _catalog(args) -> cat.Catalog:
    return cat.load(getattr(args, "catalog", None))


def _favorites(args):
    from rxved.favorites import Favorites

    return Favorites(getattr(args, "favorites", None))


# --- offline commands -------------------------------------------------------


def _cmd_ports(_bridge, _args) -> None:
    """Every MIDI port on this host, with a note on which look like an XV."""
    # Imported here, not at module scope: xv.bridge imports rtmidi eagerly,
    # and every other command in this file must work on a host with no MIDI
    # stack at all.
    from xv.bridge import MidiUnavailable, likely_xv_ports, list_ports

    try:
        ins, outs = list_ports()
        likely = set(likely_xv_ports())
    except MidiUnavailable as exc:
        raise SystemExit(
            f"error: {exc}\n"
            f"       rxved needs a MIDI backend: on Linux an ALSA sequencer "
            f"(try `modprobe snd-seq`); in a container it must be passed "
            f"through."
        ) from exc

    both = set(ins) & set(outs)
    for label, names in (("inputs", ins), ("outputs", outs)):
        print(f"{label}:")
        for name in names:
            marks = []
            if name in both:
                marks.append("bidirectional")
            if name in likely:
                marks.append("looks like an XV-2020")
            suffix = f"   [{', '.join(marks)}]" if marks else ""
            print(f"  {name}{suffix}")
        if not names:
            print("  (none)")
    if not likely:
        print(
            "\nNo port name mentions an XV-2020. That is not a problem: one "
            "reached\nover a DIN cable is behind an interface whose name says "
            "nothing about it.\n`rxvcli status` asks every bidirectional port "
            "who is there."
        )


def _cmd_banks(_bridge, args) -> None:
    catalog = _catalog(args)
    rows = []
    for bank_id in banks.bank_ids(args.kind):
        entry = banks.bank(bank_id)
        pcs = banks.slots(bank_id)
        pc_span = (
            f"{pcs[0].program_change}-{pcs[-1].program_change}"
            if len(pcs) > 1 else str(pcs[0].program_change)
        )
        rows.append([
            bank_id, entry.label, entry.kind, str(entry.msb), str(entry.lsb),
            pc_span, str(entry.count),
            "yes" if catalog.has(bank_id) else "",
            "user" if entry.writable else ("SRX" if entry.expansion else ""),
        ])
    print(_fmt_table(rows, ["id", "label", "kind", "MSB", "LSB", "PC",
                            "slots", "named", "note"]))


def _cmd_list(_bridge, args) -> None:
    catalog = _catalog(args)
    favorites = _favorites(args)
    try:
        marked = favorites.keys_for_bank(args.bank)
    finally:
        favorites.close()
    rows = []
    for slot in banks.slots(args.bank):
        entry = catalog.entry(args.bank, slot.number)
        rows.append([
            f"{slot.number:03d}",
            catalog.display_name(args.bank, slot.number),
            str(slot.msb), str(slot.lsb), str(slot.program_change),
            (entry.category or "") if entry else "",
            "*" if slot.number in marked else "",
        ])
    print(_fmt_table(rows, ["#", "name", "MSB", "LSB", "PC", "cat", "fav"]))


def _cmd_find(_bridge, args) -> None:
    catalog = _catalog(args)
    hits = catalog.search(args.name, kind=args.kind)
    if not hits:
        print(f"nothing matching {args.name!r}", file=sys.stderr)
        return
    rows = []
    for bank_id, number, name in hits:
        slot = banks.slot(bank_id, number)
        rows.append([bank_id, f"{number:03d}", name, str(slot.msb),
                     str(slot.lsb), str(slot.program_change)])
    print(_fmt_table(rows, ["bank", "#", "name", "MSB", "LSB", "PC"]))


def _cmd_resolve(_bridge, args) -> None:
    """What does this MSB/LSB/PC triple select? The inverse of everything else."""
    found = banks.lookup(args.msb, args.lsb, args.pc)
    if found is None:
        raise SystemExit(
            f"error: no bank claims MSB {args.msb} / LSB {args.lsb} / "
            f"PC {args.pc}. Note that PC here is the wire value, 0-based -- "
            f"the number the synth displays is one higher."
        )
    catalog = _catalog(args)
    print(f"{found.bank.label} ({found.bank_id}) {found.number:03d}  "
          f"{catalog.display_name(found.bank_id, found.number)}")
    print(f"kind: {found.kind}"
          + ("  (SRX expansion)" if found.bank.expansion else "")
          + ("  (writable)" if found.bank.writable else ""))


def _cmd_favorites(_bridge, args) -> None:
    favorites = _favorites(args)
    catalog = _catalog(args)
    try:
        if args.add:
            bank_id, number = _parse_slot(args.add)
            name = catalog.name(bank_id, number) or ""
            favorites.add(bank_id, number, name=name,
                          rating=args.rating or 0, tags=args.tags or "",
                          note=args.note or "")
            print(f"added {bank_id} {number:03d}")
            return
        if args.remove:
            bank_id, number = _parse_slot(args.remove)
            if favorites.remove(bank_id, number):
                print(f"removed {bank_id} {number:03d}")
            else:
                print(f"{bank_id} {number:03d} was not a favourite",
                      file=sys.stderr)
            return
        if args.tag:
            rows = favorites.with_tag(args.tag)
        elif args.search:
            rows = favorites.search(args.search)
        else:
            rows = favorites.all(order=args.order)
        if not rows:
            print("no favourites", file=sys.stderr)
            return
        table = []
        for fav in rows:
            try:
                slot = banks.slot(fav.bank_id, fav.number)
                wire = [str(slot.msb), str(slot.lsb),
                        str(slot.program_change)]
            except LookupError:
                # A bank this build no longer defines. Shown, not dropped.
                wire = ["?", "?", "?"]
            table.append([
                fav.bank_id, f"{fav.number:03d}",
                fav.name or catalog.display_name(fav.bank_id, fav.number),
                *wire,
                "*" * fav.rating if fav.rating else "",
                fav.tags, fav.note,
            ])
        print(_fmt_table(table, ["bank", "#", "name", "MSB", "LSB", "PC",
                                 "rating", "tags", "note"]))
    finally:
        favorites.close()


def _cmd_tags(_bridge, args) -> None:
    favorites = _favorites(args)
    try:
        counts = favorites.tags()
    finally:
        favorites.close()
    if not counts:
        print("no tags", file=sys.stderr)
        return
    print(_fmt_table([[tag, str(n)] for tag, n in counts.items()],
                     ["tag", "count"]))


# --- commands that need the device -----------------------------------------


def _cmd_status(bridge, _args) -> None:
    identity = bridge.identify()
    print(f"connection        {getattr(bridge, 'description', '?')}")
    if identity is None:
        raise SystemExit(
            "error: no Identity Reply. The XV-2020 answers a request it "
            "cannot serve with silence, so this means powered off, wrong "
            "port, Rx Exclusive off -- or simply busy."
        )
    print(f"device ID         {identity.device_display} "
          f"(wire byte {identity.device_id:#04x})")
    print(f"family            {identity.family[0]:#04x} "
          f"{identity.family[1]:#04x}")
    print(f"family number     {identity.family_number[0]:#04x} "
          f"{identity.family_number[1]:#04x}")
    print(f"software revision {identity.revision_text}")


def _cmd_read(bridge, args) -> None:
    """Read a USER bank's names. Read-only: nothing is selected."""
    names = bridge.read_user_bank(args.bank)
    catalog = _catalog(args)
    rows = []
    for number in sorted(names):
        printed = catalog.name(args.bank, number)
        slot = banks.slot(args.bank, number)
        rows.append([
            f"{number:03d}", names[number], str(slot.msb), str(slot.lsb),
            str(slot.program_change),
            "" if printed in (None, names[number]) else f"was {printed!r}",
        ])
    print(_fmt_table(rows, ["#", "name", "MSB", "LSB", "PC", "differs"]))


def _cmd_select(bridge, args) -> None:
    bank_id, number = _parse_slot(args.slot)
    slot = banks.slot(bank_id, number)
    # Read the synth's own channels first: patches and performances arrive on
    # two different ones, and a performance sent to the patch channel is
    # ignored silently.
    try:
        bridge.use_system_channels()
    except Exception as exc:
        print(f"note: could not read the synth's receive channels ({exc}); "
              f"using the configured channel, which may be wrong",
              file=sys.stderr)
    bridge.select(slot)
    channel = bridge.channel_for(slot.kind)
    print(f"selected {slot}: MSB {slot.msb}, LSB {slot.lsb}, "
          f"PC {slot.program_change}, on MIDI channel {channel + 1}")


def _cmd_channels(bridge, _args) -> None:
    """Which channels the synth listens on. Read-only, makes no sound."""
    channels = bridge.system_channels()
    print(f"patch / rhythm receive channel   {channels.patch_display}")
    if channels.performance_display is None:
        print("performance control channel      OFF — performances cannot "
              "be selected over MIDI at all")
    else:
        print(f"performance control channel      "
              f"{channels.performance_display}")


def _cmd_scan(bridge, args) -> None:
    entry = banks.bank(args.bank)
    if not args.yes:
        raise SystemExit(
            f"error: scanning {args.bank} sends {entry.count} program "
            f"changes and leaves the synth on the last one -- it plays the "
            f"instrument. Re-run with --yes if that is what you want."
        )

    def progress(done: int, total: int, name: str) -> None:
        print(f"\r  {done}/{total}  {name:<14}", end="", file=sys.stderr)

    names = bridge.scan_bank(args.bank, on_progress=progress)
    print("", file=sys.stderr)
    rows = []
    for number in sorted(names):
        slot = banks.slot(args.bank, number)
        rows.append([f"{number:03d}", names[number], str(slot.msb),
                     str(slot.lsb), str(slot.program_change)])
    print(_fmt_table(rows, ["#", "name", "MSB", "LSB", "PC"]))


def _cmd_probe_srx(bridge, args) -> None:
    if not args.yes:
        raise SystemExit(
            "error: probing sends a program change per candidate LSB and "
            "leaves the synth on the last one that answered -- it plays the "
            "instrument. Re-run with --yes."
        )
    found = bridge.probe_srx(lsb_range=range(args.first_lsb, args.last_lsb + 1))
    if not found:
        print(
            "no SRX card answered. That is not proof there is none: the synth "
            "answers an unsupported Bank Select by staying put, so 'no "
            "change' is the only signal available.",
            file=sys.stderr,
        )
        return
    rows = []
    for lsb, name in sorted(found.items()):
        known = [c.id for c in banks.SRX_CARDS if lsb in c.patch_lsbs]
        rows.append([str(lsb), name, known[0] if known else "(unknown card)"])
    print(_fmt_table(rows, ["LSB", "patch 1", "card"]))


# --- plumbing ---------------------------------------------------------------


def _parse_slot(text: str) -> tuple:
    """``PST-B:29`` or ``PST-B/29`` or ``PST-B 29`` -> ``("PST-B", 29)``."""
    for separator in (":", "/", " "):
        if separator in text:
            bank_id, _, number = text.partition(separator)
            try:
                return bank_id.strip(), int(number)
            except ValueError:
                break
    raise SystemExit(
        f"error: {text!r} is not a slot; write it as BANK:NUMBER, e.g. "
        f"PST-B:29"
    )


_COMMANDS: Dict[str, Callable] = {
    "ports": _cmd_ports,
    "banks": _cmd_banks,
    "list": _cmd_list,
    "find": _cmd_find,
    "resolve": _cmd_resolve,
    "fav": _cmd_favorites,
    "tags": _cmd_tags,
    "status": _cmd_status,
    "channels": _cmd_channels,
    "read": _cmd_read,
    "select": _cmd_select,
    "scan": _cmd_scan,
    "probe-srx": _cmd_probe_srx,
}

#: Commands that construct no bridge and open no port. `ports` is here too:
#: it enumerates, which needs the MIDI backend but not the synth, and it is
#: the first thing a new user runs.
_OFFLINE = {"ports", "banks", "list", "find", "resolve", "fav", "tags"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rxvcli",
        description="Command-line browser for the Roland XV-2020's sounds.",
    )
    parser.add_argument("--port", help="MIDI port name (default: autodetect)")
    parser.add_argument("--recv-port", default=None)
    parser.add_argument("--demo", action="store_true",
                        help="use the built-in demo synth; opens no ports")
    parser.add_argument("--device-id", type=int, default=None,
                        help="device ID as the synth displays it (17-32)")
    parser.add_argument("--channel", type=int, default=None, help="1-16")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--favorites", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ports", help="list MIDI ports on this host")

    sp = sub.add_parser("banks", help="the bank table (no device needed)")
    sp.add_argument("--kind", choices=["patch", "rhythm", "performance"],
                    default=None)

    sp = sub.add_parser("list", help="one bank's slots (no device needed)")
    sp.add_argument("bank", help="bank id, e.g. PST-B")

    sp = sub.add_parser("find", help="search names (no device needed)")
    sp.add_argument("name")
    sp.add_argument("--kind", choices=["patch", "rhythm", "performance"],
                    default=None)

    sp = sub.add_parser("resolve",
                        help="what does this MSB/LSB/PC select?")
    sp.add_argument("msb", type=int)
    sp.add_argument("lsb", type=int)
    sp.add_argument("pc", type=int, help="program change, 0-based (wire)")

    sp = sub.add_parser("fav", help="the favourites database")
    sp.add_argument("--add", metavar="BANK:N")
    sp.add_argument("--remove", metavar="BANK:N")
    sp.add_argument("--rating", type=int, default=None)
    sp.add_argument("--tags", default=None)
    sp.add_argument("--note", default=None)
    sp.add_argument("--tag", default=None, help="list favourites with this tag")
    sp.add_argument("--search", default=None)
    sp.add_argument("--order", default="added",
                    choices=["added", "rating", "bank", "name"])

    sub.add_parser("tags", help="tags in use, with counts")
    sub.add_parser("status", help="ask the synth who it is")
    sub.add_parser("channels",
                   help="which MIDI channels the synth listens on")

    sp = sub.add_parser("read", help="read a USER bank's names (read-only)")
    sp.add_argument("bank", choices=["USER", "P-USER", "R-USER"])

    sp = sub.add_parser("select", help="select a slot ON THE SYNTH")
    sp.add_argument("slot", metavar="BANK:N")

    sp = sub.add_parser(
        "scan", help="read a bank's names by selecting every slot (plays it)")
    sp.add_argument("bank")
    sp.add_argument("--yes", action="store_true",
                    help="required: this plays the instrument")

    sp = sub.add_parser("probe-srx", help="find a fitted SRX card (plays it)")
    sp.add_argument("--first-lsb", type=int, default=0)
    sp.add_argument("--last-lsb", type=int, default=63)
    sp.add_argument("--yes", action="store_true",
                    help="required: this plays the instrument")

    return parser


def _build_bridge(args):
    if args.demo:
        from rxved.demo import DemoBridge

        return DemoBridge(channel=(args.channel - 1) if args.channel else 0)

    from xv import bridge as b

    b.install_clean_exit()
    config_path = args.config or b.DEFAULT_CONFIG_PATH
    channel = (args.channel - 1) if args.channel is not None else (
        b.load_channel(config_path) or 0)
    kwargs = {}
    if args.timeout is not None:
        kwargs["timeout"] = args.timeout
    if args.port:
        device_id = (args.device_id if args.device_id is not None
                     else (b.load_device_id(config_path) or 17))
        return b.XvBridge.standard(
            args.port, recv_port_name=args.recv_port, device_id=device_id,
            channel=channel, **kwargs)
    return b.XvBridge.autodetect(
        config_path=config_path, channel=channel,
        on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
        **kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = _COMMANDS[args.command]

    if args.channel is not None and not 1 <= args.channel <= 16:
        sys.exit("error: --channel is 1-16")

    if args.command in _OFFLINE:
        try:
            handler(None, args)
        except (LookupError, ValueError) as exc:
            sys.exit(f"error: {exc}")
        return 0

    try:
        bridge = _build_bridge(args)
    except Exception as exc:
        sys.exit(f"error: {exc}")

    try:
        handler(bridge, args)
    except (LookupError, TimeoutError, ValueError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
