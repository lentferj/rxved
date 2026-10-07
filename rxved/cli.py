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

``select``, ``scan`` and ``scan-catalog`` change what the instrument is set
to play, and the two scans -- which send a program change per slot -- refuse
to run without ``--yes``. A shell is exactly the place where a recalled
history line fires something you did not mean to fire, and 475 program
changes into a live set is not a recoverable mistake.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from typing import Callable, Dict, List, Optional

from vinsynlib.cli import add_common_arguments, make_parser, validate_common
from vinsynlib.spec import flag_help

from xv import banks
from xv import catalog as cat

from rxved import livenames

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
    """The printed catalog, with any hardware-read names laid over it.

    The live layer matters here as much as in the browser: `rxvcli list USER`
    reporting what the factory shipped rather than what is in the machine is
    the same wrong answer the TUI used to give, reached by a different route.
    `read` writes that layer, and everything else here reads it.
    """
    catalog = cat.load(getattr(args, "catalog", None))
    livenames.apply_to(catalog, getattr(args, "live_names", None))
    return catalog


def _favorites(args):
    from rxved.favorites import Favorites

    return Favorites(getattr(args, "favorites", None))


# --- offline commands -------------------------------------------------------


def _cmd_ports(_bridge, _args) -> None:
    """Every MIDI port on this host, with a note on which look like an XV."""
    # Imported here, not at module scope: xv.bridge imports rtmidi eagerly,
    # and every other command in this file must work on a host with no MIDI
    # stack at all.
    from xv.bridge import MidiUnavailable, likely_xv_ports
    from vinsynlib import midi

    try:
        ins, outs = midi.list_ports()
        likely = set(likely_xv_ports())
    except MidiUnavailable as exc:
        raise SystemExit(
            f"error: {exc}\n"
            f"       rxved needs a MIDI backend: on Linux an ALSA sequencer "
            f"(try `modprobe snd-seq`); in a container it must be passed "
            f"through."
        ) from exc

    print(
        midi.render_ports(
            ins,
            outs,
            likely=likely,
            likely_label="looks like an XV-2020",
        )
    )
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
    """Read a USER bank's names. Read-only over MIDI: nothing is selected.

    Written to the live-names cache afterwards, so the next `rxved` opens
    already showing them rather than waiting on its own startup read. The
    read itself still touches nothing on the instrument; the write is to a
    local file, and it is the same thing `rxved` does at every launch.
    """
    names = bridge.read_user_bank(args.bank)
    catalog = _catalog(args)
    livenames.save(
        {livenames.key(args.bank, n): name for n, name in names.items()},
        getattr(args, "live_names", None),
    )
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
    if args.bank in ("USER", "P-USER", "R-USER"):
        raise SystemExit(
            f"error: {args.bank} has addresses in the parameter map, so "
            f"`rxvcli read {args.bank}` gets the same names directly -- "
            f"silently, exactly, in about 3 seconds. Scanning it instead "
            f"plays {entry.count} program changes, and on a synth that is "
            f"not following them it reports the patch the synth is sitting "
            f"on, once per slot. Scan is for ROM and expansion banks, which "
            f"have no address to read."
        )
    if args.bank not in ("USER", "P-USER", "R-USER"):
        setup = getattr(getattr(bridge, "state", None), "setup", None)
        if setup is not None and not setup.is_patch_mode:
            raise SystemExit(
                f"error: the synth is in {setup.mode_name} mode, where a "
                f"program change on the patch receive channel does not change "
                f"the patch that gets read back. Scanning {args.bank} would "
                f"send {entry.count} program changes and report the current "
                f"patch's name for every one of them. Switch to PATCH mode "
                f"(SYSTEM/MIDI) and try again. Nothing has been sent."
            )
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


#: The internal patch banks that can be scanned. GM is not among them: on the
#: XV-2020, selecting a GM patch (MSB 121) leaves the temporary patch area
#: unreadable -- the read times out, measured on hardware -- so GM names come
#: from the editor/PDF catalog instead. The GM variations (``GM-1``..``GM-9``)
#: are the same, and are sparse besides.
_INTERNAL_PATCH_BANKS = ("PST-A", "PST-B", "PST-C", "PST-D")


def _scan_targets(names: List[str]) -> List[str]:
    """Bank ids to scan, expanding an SRX card id to its patch pages.

    ``SRX-07`` is not a bank id -- the card is split across ``SRX-07-1`` to
    ``SRX-07-4`` -- so it is expanded here. Its rhythm bank is left out:
    there is no temporary rhythm area to read back, so it cannot be scanned.
    """
    out: List[str] = []
    for name in names:
        try:
            card = banks.srx_card(name)
        except LookupError:
            out.append(name)
        else:
            out.extend(b.id for b in card.patch_banks())
    seen = set()
    unique: List[str] = []
    for bank_id in out:
        if bank_id not in seen:
            seen.add(bank_id)
            unique.append(bank_id)
    return unique


def _discovery_targets(bridge) -> List[str]:
    """Every patch bank the synth actually answers for.

    The internal presets are always there. The SRX pages are not assumed from
    the table -- a card is only scanned if probing finds it -- so a machine
    with one board fitted does not spend a minute scanning four empty ones.
    """
    targets = list(_INTERNAL_PATCH_BANKS)
    found = bridge.probe_srx(
        on_progress=lambda lsb, name: print(
            f"\r  probing SRX LSB {lsb:2d}  {name or '':<16}",
            end="",
            file=sys.stderr,
        )
    )
    print("", file=sys.stderr)
    seen = set()
    for lsb in sorted(found):
        for card in banks.SRX_CARDS:
            if lsb in card.patch_lsbs and card.id not in seen:
                seen.add(card.id)
                targets.extend(b.id for b in card.patch_banks())
                break
    return targets


def _require_patch_mode(bridge) -> None:
    """Refuse before touching the synth if it is not in PATCH mode.

    In PERFORM mode a program change on the patch receive channel does not
    move the patch that gets read back, so a scan would report the current
    patch's name for every slot. Checked up front rather than left to the
    first stuck bank, so discovery does not probe 64 LSBs to learn nothing.
    """
    try:
        setup = bridge.read_setup()
    except (
        LookupError,
        TimeoutError,
        ValueError,
        RuntimeError,
        OSError,
        SystemError,
    ):
        return
    if not setup.is_patch_mode:
        raise SystemExit(
            f"error: the synth is in {setup.mode_name} mode, where a program "
            f"change on the patch receive channel does not move the patch "
            f"that gets read back. Switch it to PATCH mode (SYSTEM/MIDI) and "
            f"try again. Nothing has been sent."
        )


def _patch_targets(targets: List[str]) -> List[str]:
    """Drop the banks a scan cannot serve, saying why for each."""
    out: List[str] = []
    for bank_id in targets:
        entry = banks.bank(bank_id)
        if bank_id in ("USER", "P-USER", "R-USER"):
            print(
                f"note: skipping {bank_id}: it has addresses, so "
                f"`rxvcli read {bank_id}` gets it silently",
                file=sys.stderr,
            )
        elif entry.kind != "patch":
            print(
                f"note: skipping {bank_id}: only patch banks have a "
                f"temporary area to read back",
                file=sys.stderr,
            )
        else:
            out.append(bank_id)
    return out


def _cmd_scan_catalog(bridge, args) -> None:
    """Build or update the name catalog from a hardware scan.

    The scan selects every slot in a bank and reads the temporary patch area
    back, so it changes what the synth is set to play (and puts it back when
    the bank is done). It is the only way to get ROM and expansion names off
    the hardware; the writable banks are not scanned, because `rxvcli read`
    gets those silently and exactly.
    """
    discovery = args.discover or not args.banks

    if not args.yes:
        raise SystemExit(
            "error: scanning selects every slot in a bank and changes what "
            "the synth is set to play. That does not make a note sound by "
            "itself, but if anything is already playing, every slot change is "
            "heard: turn the volume down first. The patch the synth was on is "
            "restored at the end. Re-run with --yes."
        )

    if discovery:
        _require_patch_mode(bridge)
        targets = _discovery_targets(bridge)
    else:
        targets = _scan_targets(args.banks)

    # Decided before the scan: confirming a sweep and then watching it skip
    # everything is a poor way to find out the bank cannot be scanned.
    patch_targets = _patch_targets(targets)
    if not patch_targets:
        raise SystemExit("error: nothing to scan")
    if not discovery:
        _require_patch_mode(bridge)

    path = getattr(args, "catalog", None) or cat.DEFAULT_CATALOG_PATH
    catalog = cat.empty() if args.no_merge else cat.load(path)
    existing_source = catalog.source
    total = 0
    counts: Dict[str, int] = {}
    failures: Dict[str, str] = {}
    for bank_id in patch_targets:

        def progress(done: int, total_: int, name: str, bank_id=bank_id) -> None:
            print(
                f"\r  {bank_id}: {done}/{total_}  {name:<16}",
                end="",
                file=sys.stderr,
            )

        try:
            names = bridge.scan_bank_with_categories(bank_id, on_progress=progress)
        except (LookupError, TimeoutError, ValueError, RuntimeError) as exc:
            # One bank that will not answer must not cost the others: a
            # discovery run is the one place a single bad page is most
            # likely, and the catalog is still worth writing for the rest.
            print("", file=sys.stderr)
            failures[bank_id] = str(exc)
            print(
                f"  {bank_id}: FAILED -- {str(exc).splitlines()[0]}",
                file=sys.stderr,
            )
            continue
        print("", file=sys.stderr)
        for number, (name, category_byte) in names.items():
            catalog.set_name(
                bank_id, number, name, category=cat.category_code(category_byte)
            )
        counts[bank_id] = len(names)
        total += len(names)

    if not counts:
        raise SystemExit("error: no bank could be scanned; the catalog was not written")

    scan_source = "hardware scan (rxvcli scan-catalog)"
    source = (
        f"{existing_source}; {scan_source}"
        if existing_source and not args.no_merge
        else scan_source
    )
    cat.dump(
        catalog,
        path,
        source=source,
        generated=datetime.date.today().isoformat(),
        note=(
            "Built by scanning the connected XV-2020, one program change per "
            "slot. Names come from the instrument, not from Roland's printed "
            "documents. Not distributed with rxved."
        ),
    )
    print(f"wrote {path}: {total} name(s) across {len(counts)} bank(s)")
    for bank_id in sorted(counts):
        print(f"  {bank_id:<10} {counts[bank_id]:>4}")
    if failures:
        print(
            f"note: {len(failures)} bank(s) failed and were left as they were:",
            file=sys.stderr,
        )
        for bank_id, message in failures.items():
            print(f"  {bank_id}: {message.splitlines()[0]}", file=sys.stderr)
        raise SystemExit(1)


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
    "scan-catalog": _cmd_scan_catalog,
    "probe-srx": _cmd_probe_srx,
}

#: Commands that construct no bridge and open no port. `ports` is here too:
#: it enumerates, which needs the MIDI backend but not the synth, and it is
#: the first thing a new user runs.
_OFFLINE = {"ports", "banks", "list", "find", "resolve", "fav", "tags", "perf-list"}


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser(
        "rxvcli",
        "Command-line browser for the Roland XV-2020's sounds.",
        distribution="rxved",
    )
    # The mutually exclusive --port/--scan pair is this tool's own rule; see
    # rxved.app.build_parser, which says why. Everything else is the
    # family's, with the family's help text.
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--port", default=None, help=flag_help("port"))
    group.add_argument(
        "--scan", action="store_true", default=False, help=flag_help("scan")
    )
    add_common_arguments(
        parser,
        port=False,
        scan=False,
        recv_port=True,
        channel=True,
        device_id=True,
        demo=True,
        timeout=True,
        catalog=True,
        config=True,
        favorites=True,
    )
    # rxved's own, and stays: the family has no opinion about where names
    # read off a synth are cached between runs.
    parser.add_argument(
        "--live-names",
        default=None,
        help="where names read from the synth are kept between runs "
        "(default: beside the favourites database)",
    )
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

    sp = sub.add_parser(
        "read",
        help="read a USER bank's names (read-only over MIDI) and cache them",
    )
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

    sp = sub.add_parser(
        "scan-catalog",
        help="build/update the name catalog by scanning the synth "
        "(changes the selected patch)",
    )
    sp.add_argument(
        "banks",
        nargs="*",
        help="bank ids or SRX card ids to scan, e.g. SRX-07 or PST-A; "
        "omit to discover the whole synth",
    )
    sp.add_argument(
        "--discover",
        action="store_true",
        help="scan the internal presets and every fitted SRX card "
        "(the default when no banks are named)",
    )
    sp.add_argument(
        "--no-merge",
        action="store_true",
        help="write only the scanned banks, discarding the rest of the catalog",
    )
    sp.add_argument(
        "--yes",
        action="store_true",
        help="required: this changes the selected patch on the synth",
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
    return b.XvBridge.connect(
        config_path=config_path,
        channel=channel,
        scan=args.scan,
        on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
        **kwargs,
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = _COMMANDS[args.command]

    # Before any bridge is built: a mistyped channel costs a message rather
    # than a program change sent to a live instrument, and a wrong channel
    # is not an error anywhere -- the synth simply plays nothing, or
    # something else does.
    validate_common(args)
    # ...and then this unit's own narrower device-ID range, which is panel
    # numbers here and a byte everywhere else in the family.
    if args.device_id is not None and not 17 <= args.device_id <= 32:
        sys.exit("error: --device-id is 17-32, as the panel shows it")

    if args.command in _OFFLINE:
        try:
            handler(None, args)
        except (LookupError, ValueError) as exc:
            sys.exit(f"error: {exc}")
        return 0

    try:
        bridge = _build_bridge(args)
    except (
        LookupError,
        TimeoutError,
        ValueError,
        RuntimeError,
        OSError,
        SystemError,
    ) as exc:
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
