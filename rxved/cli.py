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
import os
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
        out.append(
            "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        )
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
            if len(pcs) > 1
            else str(pcs[0].program_change)
        )
        rows.append(
            [
                bank_id,
                entry.label,
                entry.kind,
                str(entry.msb),
                str(entry.lsb),
                pc_span,
                str(entry.count),
                "yes" if catalog.has(bank_id) else "",
                "user" if entry.writable else ("SRX" if entry.expansion else ""),
            ]
        )
    print(
        _fmt_table(
            rows, ["id", "label", "kind", "MSB", "LSB", "PC", "slots", "named", "note"]
        )
    )


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
        rows.append(
            [
                f"{slot.number:03d}",
                catalog.display_name(args.bank, slot.number),
                str(slot.msb),
                str(slot.lsb),
                str(slot.program_change),
                (entry.category or "") if entry else "",
                "*" if slot.number in marked else "",
            ]
        )
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
        rows.append(
            [
                bank_id,
                f"{number:03d}",
                name,
                str(slot.msb),
                str(slot.lsb),
                str(slot.program_change),
            ]
        )
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
    print(
        f"{found.bank.label} ({found.bank_id}) {found.number:03d}  "
        f"{catalog.display_name(found.bank_id, found.number)}"
    )
    print(
        f"kind: {found.kind}"
        + ("  (SRX expansion)" if found.bank.expansion else "")
        + ("  (writable)" if found.bank.writable else "")
    )


def _cmd_favorites(_bridge, args) -> None:
    favorites = _favorites(args)
    catalog = _catalog(args)
    try:
        if args.add:
            bank_id, number = _parse_slot(args.add)
            name = catalog.name(bank_id, number) or ""
            favorites.add(
                bank_id,
                number,
                name=name,
                rating=args.rating or 0,
                tags=args.tags or "",
                note=args.note or "",
            )
            print(f"added {bank_id} {number:03d}")
            return
        if args.remove:
            bank_id, number = _parse_slot(args.remove)
            if favorites.remove(bank_id, number):
                print(f"removed {bank_id} {number:03d}")
            else:
                print(f"{bank_id} {number:03d} was not a favourite", file=sys.stderr)
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
                wire = [str(slot.msb), str(slot.lsb), str(slot.program_change)]
            except LookupError:
                # A bank this build no longer defines. Shown, not dropped.
                wire = ["?", "?", "?"]
            table.append(
                [
                    fav.bank_id,
                    f"{fav.number:03d}",
                    fav.name or catalog.display_name(fav.bank_id, fav.number),
                    *wire,
                    "*" * fav.rating if fav.rating else "",
                    fav.tags,
                    fav.note,
                ]
            )
        print(
            _fmt_table(
                table,
                ["bank", "#", "name", "MSB", "LSB", "PC", "rating", "tags", "note"],
            )
        )
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
    print(_fmt_table([[tag, str(n)] for tag, n in counts.items()], ["tag", "count"]))


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
    print(
        f"device ID         {identity.device_display} "
        f"(wire byte {identity.device_id:#04x})"
    )
    print(f"family            {identity.family[0]:#04x} {identity.family[1]:#04x}")
    print(
        f"family number     {identity.family_number[0]:#04x} "
        f"{identity.family_number[1]:#04x}"
    )
    print(f"software revision {identity.revision_text}")


def _cmd_read(bridge, args) -> None:
    """Read a USER bank's names. Read-only: nothing is selected."""
    names = bridge.read_user_bank(args.bank)
    catalog = _catalog(args)
    rows = []
    for number in sorted(names):
        printed = catalog.name(args.bank, number)
        slot = banks.slot(args.bank, number)
        rows.append(
            [
                f"{number:03d}",
                names[number],
                str(slot.msb),
                str(slot.lsb),
                str(slot.program_change),
                "" if printed in (None, names[number]) else f"was {printed!r}",
            ]
        )
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
        print(
            f"note: could not read the synth's receive channels ({exc}); "
            f"using the configured channel, which may be wrong",
            file=sys.stderr,
        )
    channel = (args.channel - 1) if args.channel is not None else None
    if channel is None:
        channel = bridge.channel_for(slot.kind)
    if channel is None:
        raise SystemExit(
            "error: this synth has its Performance Control Channel set to "
            "OFF, so performances cannot be selected over MIDI."
        )
    bridge.select(slot, channel=channel)
    print(
        f"selected {slot}: MSB {slot.msb}, LSB {slot.lsb}, "
        f"PC {slot.program_change}, on MIDI channel {channel + 1}"
    )
    # Read back rather than assume: a select aimed at a channel nothing
    # listens on is ignored in silence.
    try:
        state = bridge.refresh_channel(channel)
        print("now: " + state.describes(channel), file=sys.stderr)
    except Exception as exc:
        print(f"note: could not read back ({exc})", file=sys.stderr)


def _cmd_channels(bridge, args) -> None:
    """What each MIDI channel currently selects. Read-only, makes no sound.

    The question this answers is the one the synth will not answer on its
    own: Bank Select and Program Change are per channel, so a select only
    does anything if something is listening on the channel it went out on.
    """
    state = bridge.read_state(with_parts=args.parts or None)
    setup = state.setup
    print(f"sound mode                       {setup.mode_name}")
    print(f"patch / rhythm receive channel   {state.channels.patch_display}")
    if state.channels.performance_display is None:
        print(
            "performance control channel      OFF — performances cannot "
            "be selected over MIDI at all"
        )
    else:
        print(f"performance control channel      {state.channels.performance_display}")
    from xv.bridge import _describe_selection

    print(
        f"current patch                    "
        + _describe_selection(
            setup.patch_slot, setup.patch_msb, setup.patch_lsb, setup.patch_program
        )
    )
    print(
        f"current performance              "
        + _describe_selection(
            setup.performance_slot,
            setup.performance_msb,
            setup.performance_lsb,
            setup.performance_program,
        )
    )

    if state.parts:
        print()
        rows = [
            [
                str(p.part),
                str(p.channel_display),
                str(p.program_change),
                str(p.lsb),
                str(p.msb),
                str(p.slot) if p.slot else "?",
            ]
            for p in state.parts
        ]
        print(_fmt_table(rows, ["part", "ch", "PC", "LSB", "MSB", "slot"]))

    print()
    for channel in range(16):
        print("  " + state.describes(channel))


def _cmd_multi(bridge, args) -> None:
    """Multi-mode setup: the parts, and why a channel makes no sound.

    The CLI twin of the browser's `m` screen. Read-only, makes no sound,
    eighteen round trips.
    """
    state = bridge.read_state(with_parts=True)
    common = state.common
    header = f"sound mode  {state.setup.mode_name}"
    if common is not None and common.name:
        header += f"   performance  {common.name}"
    if common is not None and common.solo is not None:
        header += f"   SOLO on part {common.solo}"
    print(header)
    print()

    if state.parts:
        rows = []
        for part in state.parts:
            reason = part.silence_reason()
            if reason is None and state.soloed_out(part):
                reason = "not soloed"
            rows.append(
                [
                    str(part.part),
                    str(part.channel_display),
                    "on" if part.receive_switch else "OFF",
                    str(part.level),
                    str(part.program_change),
                    str(part.lsb),
                    str(part.msb),
                    str(part.slot) if part.slot else "?",
                    reason or "",
                ]
            )
        print(
            _fmt_table(
                rows, ["part", "ch", "rx", "lvl", "PC", "LSB", "MSB", "slot", ""]
            )
        )
        print()

        # The TUI puts these behind `tab`; a CLI has no cursor to move, so
        # it prints all three.
        rows = [
            [
                str(p.part),
                str(p.channel_display),
                "MUTE" if p.mute else "off",
                str(p.dry),
                str(p.chorus),
                str(p.reverb),
                p.output_name,
                p.output_mfx_name,
            ]
            for p in state.parts
        ]
        print(
            _fmt_table(rows, ["part", "ch", "mute", "dry", "cho", "rev", "out", "mfx"])
        )
        print()

    if state.midi:
        rows = [
            [
                str(e.channel_display),
                "on" if e.program_change else "OFF",
                "on" if e.bank_select else "OFF",
                "on" if e.bender else "off",
                "on" if e.modulation else "off",
                "on" if e.volume else "off",
                "on" if e.expression else "off",
                "on" if e.hold_1 else "off",
                "on" if e.phase_lock else "off",
                str(e.velocity_curve) if e.velocity_curve else "off",
            ]
            for e in state.midi
        ]
        print(
            _fmt_table(
                rows,
                [
                    "ch",
                    "rxPC",
                    "rxBS",
                    "bend",
                    "mod",
                    "vol",
                    "exp",
                    "hold",
                    "phase",
                    "vcurve",
                ],
            )
        )
        print()

    for line in state.silence_report():
        print(f"  {line}")


def _cmd_scan(bridge, args) -> None:
    entry = banks.bank(args.bank)
    if not args.yes:
        raise SystemExit(
            f"error: scanning {args.bank} sends {entry.count} program "
            f"changes -- it plays the instrument, audibly, for as long as it "
            f"takes. (The patch it was on is restored at the end.) Re-run "
            f"with --yes if that is what you want."
        )

    def progress(done: int, total: int, name: str) -> None:
        print(f"\r  {done}/{total}  {name:<14}", end="", file=sys.stderr)

    names = bridge.scan_bank(args.bank, on_progress=progress)
    print("", file=sys.stderr)
    rows = []
    for number in sorted(names):
        slot = banks.slot(args.bank, number)
        rows.append(
            [
                f"{number:03d}",
                names[number],
                str(slot.msb),
                str(slot.lsb),
                str(slot.program_change),
            ]
        )
    print(_fmt_table(rows, ["#", "name", "MSB", "LSB", "PC"]))


def _cmd_probe_srx(bridge, args) -> None:
    if not args.yes:
        raise SystemExit(
            "error: probing sends a program change per candidate LSB -- it "
            "plays the instrument. (The patch it was on is restored at the "
            "end.) Re-run with --yes."
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
        f"error: {text!r} is not a slot; write it as BANK:NUMBER, e.g. PST-B:29"
    )


def _backup_dir(args) -> str:
    from xv import backup as bk

    from rxved.favorites import data_dir

    return args.dir or bk.default_dir(data_dir())


def _progress(label: str):
    def report(done: int, total: int) -> None:
        print(f"\r  {label} {done}/{total}", end="", file=sys.stderr)

    return report


def _cmd_perf_list(bridge, args) -> None:
    """Backups on disk. Needs no synth."""
    from xv import backup as bk

    directory = _backup_dir(args)
    rows = []
    for entry in bk.list_backups(directory):
        if "error" in entry:
            rows.append([os.path.basename(entry["path"]), "", "", entry["error"]])
            continue
        slot = f"{entry['slot']:02d}" if entry["slot"] else "temp"
        rows.append(
            [os.path.basename(entry["path"]), slot, entry["saved"], entry["name"]]
        )
    if not rows:
        print(f"no backups in {directory}")
        return
    print(f"{directory}\n")
    print(_fmt_table(rows, ["file", "slot", "saved", "name"]))


def _cmd_perf_backup(bridge, args) -> None:
    """Read a performance and write it to a file. Read-only, makes no sound."""
    from xv import backup as bk
    from xv import bridge as b

    if args.slot is None:
        base, slot = b.TEMPORARY_PERFORMANCE, None
        what = "the temporary performance"
    else:
        base, slot = b.user_performance_base(args.slot), args.slot
        what = f"user performance {args.slot}"
    print(f"reading {what}...", file=sys.stderr)
    blocks = bridge.read_performance_blocks(base, on_progress=_progress("block"))
    print("", file=sys.stderr)
    path = bk.save(
        blocks,
        _backup_dir(args),
        slot=slot,
        device_id=bridge.device_id,
        source=bridge.description,
    )
    print(f"saved {bk.performance_name(blocks)!r} to {path}")


def _cmd_perf_verify(bridge, args) -> None:
    """Prove a user slot can be written, without changing it.

    Reads the slot, writes back the **identical** bytes, reads again and
    compares. If that round trip is faithful, DT1 to a user address really
    does store -- which the Owner's Manual never states; it documents only
    the front-panel WRITE procedure. Nothing is at risk, because the bytes
    written are the ones already there.
    """
    from xv import bridge as b

    base = b.user_performance_base(args.slot)
    if not args.yes:
        raise SystemExit(
            f"error: this writes user performance {args.slot} -- with its "
            f"own current contents, so it changes nothing, but it is still "
            f"a write to stored memory. Re-run with --yes."
        )
    before = bridge.read_performance_blocks(base, on_progress=_progress("read"))
    print("", file=sys.stderr)
    mismatched = bridge.write_performance_blocks(
        base, before, on_progress=_progress("write")
    )
    print("", file=sys.stderr)
    after = bridge.read_performance_blocks(base)
    changed = [name for name in before if before[name] != after.get(name)]
    if mismatched or changed:
        print(
            f"MISMATCH: {len(mismatched)} block(s) did not verify on "
            f"write, {len(changed)} differ after: "
            f"{', '.join(sorted(set(mismatched) | set(changed)))}"
        )
        print(
            "Writing to a user slot does NOT work this way on this "
            "machine. Do not use `perf store`."
        )
        return
    print(
        f"all {len(before)} blocks round-tripped identically -- "
        f"user slot {args.slot} is writable over SysEx, and is unchanged"
    )


def _cmd_perf_store(bridge, args) -> None:
    """Save the edit buffer into a user slot. **Destructive.**"""
    from xv import backup as bk

    if not args.yes:
        raise SystemExit(
            f"error: this overwrites user performance {args.slot}, "
            f"permanently -- a power cycle does not bring it back. The slot "
            f"is backed up to {_backup_dir(args)} first. Re-run with --yes."
        )
    backup_blocks, mismatched = bridge.store_temporary_to_slot(
        args.slot, on_progress=_progress("block")
    )
    print("", file=sys.stderr)
    path = bk.save(
        backup_blocks,
        _backup_dir(args),
        slot=args.slot,
        device_id=bridge.device_id,
        source=f"{bridge.description} (before store)",
    )
    print(
        f"previous contents of slot {args.slot} "
        f"({bk.performance_name(backup_blocks)!r}) saved to {path}"
    )
    if mismatched:
        print(
            f"WARNING: {len(mismatched)} block(s) did not read back as "
            f"written: {', '.join(mismatched)}"
        )
        print(f"Restore with: rxvcli perf-restore {path} --slot {args.slot} --yes")
        return
    print(f"stored into user performance {args.slot}")


def _cmd_perf_restore(bridge, args) -> None:
    """Write a backup file back into a user slot. **Destructive.**"""
    from xv import backup as bk

    blocks = bk.load(args.file)
    if not args.yes:
        raise SystemExit(
            f"error: this overwrites user performance {args.slot} with "
            f"{bk.performance_name(blocks)!r} from {args.file}. Re-run with "
            f"--yes."
        )
    from xv import bridge as b

    mismatched = bridge.write_performance_blocks(
        b.user_performance_base(args.slot), blocks, on_progress=_progress("block")
    )
    print("", file=sys.stderr)
    if mismatched:
        print(
            f"WARNING: {len(mismatched)} block(s) did not read back as "
            f"written: {', '.join(mismatched)}"
        )
        return
    print(f"restored {bk.performance_name(blocks)!r} into user performance {args.slot}")


_COMMANDS: Dict[str, Callable] = {
    "perf-list": _cmd_perf_list,
    "perf-backup": _cmd_perf_backup,
    "perf-verify": _cmd_perf_verify,
    "perf-store": _cmd_perf_store,
    "perf-restore": _cmd_perf_restore,
    "ports": _cmd_ports,
    "banks": _cmd_banks,
    "list": _cmd_list,
    "find": _cmd_find,
    "resolve": _cmd_resolve,
    "fav": _cmd_favorites,
    "tags": _cmd_tags,
    "status": _cmd_status,
    "channels": _cmd_channels,
    "multi": _cmd_multi,
    "read": _cmd_read,
    "select": _cmd_select,
    "scan": _cmd_scan,
    "probe-srx": _cmd_probe_srx,
}

#: Commands that construct no bridge and open no port. `ports` is here too:
#: it enumerates, which needs the MIDI backend but not the synth, and it is
#: the first thing a new user runs.
_OFFLINE = {"ports", "banks", "list", "find", "resolve", "fav", "tags", "perf-list"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rxvcli",
        description="Command-line browser for the Roland XV-2020's sounds.",
    )
    parser.add_argument("--port", help="MIDI port name (default: autodetect)")
    parser.add_argument("--recv-port", default=None)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="use the built-in demo synth; opens no ports",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=None,
        help="device ID as the synth displays it (17-32)",
    )
    parser.add_argument("--channel", type=int, default=None, help="1-16")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--catalog", default=None)
    parser.add_argument("--favorites", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ports", help="list MIDI ports on this host")

    sp = sub.add_parser("banks", help="the bank table (no device needed)")
    sp.add_argument("--kind", choices=["patch", "rhythm", "performance"], default=None)

    sp = sub.add_parser("list", help="one bank's slots (no device needed)")
    sp.add_argument("bank", help="bank id, e.g. PST-B")

    sp = sub.add_parser("find", help="search names (no device needed)")
    sp.add_argument("name")
    sp.add_argument("--kind", choices=["patch", "rhythm", "performance"], default=None)

    sp = sub.add_parser("resolve", help="what does this MSB/LSB/PC select?")
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
    sp.add_argument(
        "--order", default="added", choices=["added", "rating", "bank", "name"]
    )

    sub.add_parser("tags", help="tags in use, with counts")
    sub.add_parser("status", help="ask the synth who it is")
    sp = sub.add_parser("channels", help="what each MIDI channel currently selects")
    sp.add_argument(
        "--parts",
        action="store_true",
        help="read all 16 Performance Parts even in Patch mode "
        "(16 extra round trips, still silent)",
    )

    sub.add_parser("multi", help="multi-mode setup, and why a channel is silent")

    # --- performances -------------------------------------------------------
    sp = sub.add_parser("perf-list", help="performance backups on disk")
    sp.add_argument("--dir", help="backup directory (default: the data dir)")

    sp = sub.add_parser("perf-backup", help="read a performance to a backup file")
    sp.add_argument(
        "--slot", type=int, help="user performance 1-64 (default: the edit buffer)"
    )
    sp.add_argument("--dir")

    sp = sub.add_parser(
        "perf-verify", help="prove a user slot is writable WITHOUT changing it"
    )
    sp.add_argument("slot", type=int)
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser(
        "perf-store",
        help="save the edit buffer to a user slot (DESTROYS "
        "what is there; backs it up first)",
    )
    sp.add_argument("slot", type=int)
    sp.add_argument("--dir")
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser(
        "perf-restore", help="write a backup file back to a user slot (DESTRUCTIVE)"
    )
    sp.add_argument("file")
    sp.add_argument("--slot", type=int, required=True)
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser("read", help="read a USER bank's names (read-only)")
    sp.add_argument("bank", choices=["USER", "P-USER", "R-USER"])

    sp = sub.add_parser("select", help="select a slot ON THE SYNTH")
    sp.add_argument("slot", metavar="BANK:N")

    sp = sub.add_parser(
        "scan", help="read a bank's names by selecting every slot (plays it)"
    )
    sp.add_argument("bank")
    sp.add_argument(
        "--yes", action="store_true", help="required: this plays the instrument"
    )

    sp = sub.add_parser("probe-srx", help="find a fitted SRX card (plays it)")
    sp.add_argument("--first-lsb", type=int, default=0)
    sp.add_argument("--last-lsb", type=int, default=63)
    sp.add_argument(
        "--yes", action="store_true", help="required: this plays the instrument"
    )

    return parser


def _build_bridge(args):
    if args.demo:
        from rxved.demo import DemoBridge

        return DemoBridge(channel=(args.channel - 1) if args.channel else 0)

    from xv import bridge as b

    b.install_clean_exit()
    config_path = args.config or b.DEFAULT_CONFIG_PATH
    channel = (
        (args.channel - 1)
        if args.channel is not None
        else (b.load_channel(config_path) or 0)
    )
    kwargs = {}
    if args.timeout is not None:
        kwargs["timeout"] = args.timeout
    if args.port:
        device_id = (
            args.device_id
            if args.device_id is not None
            else (b.load_device_id(config_path) or 17)
        )
        return b.XvBridge.standard(
            args.port,
            recv_port_name=args.recv_port,
            device_id=device_id,
            channel=channel,
            **kwargs,
        )
    return b.XvBridge.autodetect(
        config_path=config_path,
        channel=channel,
        on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
        **kwargs,
    )


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
