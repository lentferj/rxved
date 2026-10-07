# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.

"""``rxvcli scan-catalog`` builds the name catalog from a hardware scan."""

from __future__ import annotations

import json

import pytest

from rxved import cli
from rxved.demo import DemoBridge


def _args(tmp_path, *argv):
    return cli.build_parser().parse_args(
        ["--catalog", str(tmp_path / "catalog.json"), *argv]
    )


def test_it_writes_the_scanned_names(tmp_path, capsys):
    args = _args(tmp_path, "scan-catalog", "SRX-07-1", "--yes")
    cli._cmd_scan_catalog(DemoBridge(patch_mode=True), args)
    capsys.readouterr()
    raw = json.loads((tmp_path / "catalog.json").read_text())
    rows = raw["banks"]["SRX-07-1"]
    assert len(rows) == 128
    assert all(row["name"] for row in rows)
    assert "hardware scan" in raw["source"]


def test_discovery_finds_the_fitted_card(tmp_path, capsys):
    args = _args(tmp_path, "scan-catalog", "--yes")
    cli._cmd_scan_catalog(DemoBridge(patch_mode=True), args)
    capsys.readouterr()
    raw = json.loads((tmp_path / "catalog.json").read_text())
    # The internal presets are always scanned, plus the SRX-07 the demo probe
    # reports as fitted.
    assert {"PST-A", "PST-B", "PST-C", "PST-D"} <= set(raw["banks"])
    assert {"SRX-07-1", "SRX-07-2", "SRX-07-3", "SRX-07-4"} <= set(raw["banks"])


def test_it_refuses_without_yes(tmp_path):
    args = _args(tmp_path, "scan-catalog", "SRX-07-1")
    with pytest.raises(SystemExit) as exc:
        cli._cmd_scan_catalog(DemoBridge(patch_mode=True), args)
    assert "turn the volume down" in str(exc.value)
    assert not (tmp_path / "catalog.json").exists()


def test_it_refuses_in_perform_mode(tmp_path):
    args = _args(tmp_path, "scan-catalog", "SRX-07-1", "--yes")
    with pytest.raises(SystemExit) as exc:
        cli._cmd_scan_catalog(DemoBridge(patch_mode=False), args)
    assert "PATCH mode" in str(exc.value)
    assert not (tmp_path / "catalog.json").exists()


def test_merge_keeps_other_banks_and_categories(tmp_path, capsys):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "source": "printed",
                "generated": "2026-01-01",
                "banks": {
                    "SRX-07-1": [{"n": 1, "name": "Old", "category": "PNO"}],
                    "PST-A": [{"n": 1, "name": "Keep"}],
                },
            }
        )
    )
    args = _args(tmp_path, "scan-catalog", "SRX-07-1", "--yes")
    cli._cmd_scan_catalog(DemoBridge(patch_mode=True), args)
    capsys.readouterr()
    raw = json.loads(path.read_text())
    assert raw["banks"]["PST-A"] == [{"n": 1, "name": "Keep"}]
    assert raw["banks"]["SRX-07-1"][0]["category"] == "PNO"
    assert raw["banks"]["SRX-07-1"][0]["name"] != "Old"
    assert raw["source"].startswith("printed; hardware scan")


def test_no_merge_writes_only_the_scanned_bank(tmp_path, capsys):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {"source": "printed", "banks": {"PST-A": [{"n": 1, "name": "Keep"}]}}
        )
    )
    args = _args(tmp_path, "scan-catalog", "SRX-07-1", "--yes", "--no-merge")
    cli._cmd_scan_catalog(DemoBridge(patch_mode=True), args)
    capsys.readouterr()
    raw = json.loads(path.read_text())
    assert list(raw["banks"]) == ["SRX-07-1"]
    assert raw["source"] == "hardware scan (rxvcli scan-catalog)"


def test_a_rhythm_bank_is_refused(tmp_path, capsys):
    args = _args(tmp_path, "scan-catalog", "SRX-07-R", "--yes")
    with pytest.raises(SystemExit):
        cli._cmd_scan_catalog(DemoBridge(patch_mode=True), args)
    assert "only patch banks" in capsys.readouterr().err


def test_a_writable_bank_is_refused(tmp_path, capsys):
    args = _args(tmp_path, "scan-catalog", "USER", "--yes")
    with pytest.raises(SystemExit):
        cli._cmd_scan_catalog(DemoBridge(patch_mode=True), args)
    assert "rxvcli read USER" in capsys.readouterr().err
