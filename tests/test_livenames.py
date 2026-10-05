# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.
#
# rxved is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# rxved is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""Names read off the instrument, and what survives a restart.

The bug these exist for: `Catalog._live` was a plain dict in a process that
exits. Every restart fell back to the printed catalog, so the browser showed
the factory USER bank for a machine full of the user's own patches -- and
pressing `r` fixed it for exactly as long as the session lasted.

And the trap next door: a USER bank read by scanning rather than by address
came back as the *current* patch's name 128 times over, which is a plausible
thing to believe. See `tests/test_scan_stuck.py` for that half.
"""

import json

from rxved import livenames
from xv import catalog as cat


class TestStore:
    def test_round_trips(self, tmp_path):
        path = str(tmp_path / "live-names.json")
        livenames.save({"USER:001": "Analogue!", "USER:002": "Alpha Juno"}, path)
        assert livenames.load(path) == {
            "USER:001": "Analogue!",
            "USER:002": "Alpha Juno",
        }

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        """Nothing read yet is the normal state of a fresh install."""
        assert livenames.load(str(tmp_path / "nope.json")) == {}

    def test_saving_merges_rather_than_replaces(self, tmp_path):
        """Losing R-USER because USER could not be read would be a poor trade."""
        path = str(tmp_path / "live-names.json")
        livenames.save({"R-USER:001": "Kit"}, path)
        livenames.save({"USER:001": "Analogue!"}, path)
        stored = livenames.load(path)
        assert stored == {"R-USER:001": "Kit", "USER:001": "Analogue!"}

    def test_a_bank_read_later_wins(self, tmp_path):
        """The newest read of a slot replaces the older one, not both."""
        path = str(tmp_path / "live-names.json")
        livenames.save({"USER:001": "Old Name"}, path)
        livenames.save({"USER:001": "New Name"}, path)
        assert livenames.load(path) == {"USER:001": "New Name"}

    def test_a_corrupt_file_is_survivable(self, tmp_path, capsys):
        """A cache that can be rebuilt in three seconds must not stop startup.

        The synth is right there; the names are a convenience. Refusing to
        open the browser over a malformed cache would trade a small problem
        for a large one.
        """
        path = tmp_path / "live-names.json"
        path.write_text("{not json at all", encoding="utf-8")
        assert livenames.load(str(path)) == {}
        assert "live-names.json" in capsys.readouterr().err

    def test_a_file_of_the_wrong_shape_is_ignored(self, tmp_path):
        """A hand-edit, or a version with a different layout."""
        path = tmp_path / "live-names.json"
        path.write_text(json.dumps(["USER:001"]), encoding="utf-8")
        assert livenames.load(str(path)) == {}

    def test_non_string_entries_are_dropped(self, tmp_path):
        path = tmp_path / "live-names.json"
        path.write_text(
            json.dumps({"USER:001": "Fine", "USER:002": 7, "bad": None}),
            encoding="utf-8",
        )
        assert livenames.load(str(path)) == {"USER:001": "Fine"}

    def test_it_writes_its_own_directory(self, tmp_path):
        """The data directory may not exist yet on a first run."""
        path = str(tmp_path / "deep" / "deeper" / "live-names.json")
        livenames.save({"USER:001": "Analogue!"}, path)
        assert livenames.load(path) == {"USER:001": "Analogue!"}

    def test_written_names_survive_a_reload(self, tmp_path):
        """The whole point: a restart must not lose them."""
        path = str(tmp_path / "live-names.json")
        livenames.save({livenames.key("USER", 1): "Analogue!"}, path)

        # What main() does at startup, on a fresh process.
        catalog = cat.empty()
        for key, name in livenames.load(path).items():
            bank_id, _, number = key.rpartition(":")
            catalog.set_live_name(bank_id, int(number), name)

        assert catalog.display_name("USER", 1) == "Analogue!"
        assert catalog.is_live("USER", 1)

    def test_apply_to_skips_keys_that_do_not_parse(self, tmp_path):
        """A name on the wrong slot is worse than a missing one.

        It is a plausible wrong answer, and there is no marker for it: the
        `*` only fires on a disagreement, so a key like "USER" or "USER:x"
        would land silently on slot 0 and look like a real reading.
        """
        path = tmp_path / "live-names.json"
        path.write_text(
            json.dumps(
                {"USER:001": "Fine", "USER": "No number", "USER:x": "Not digits"}
            ),
            encoding="utf-8",
        )
        catalog = cat.empty()
        applied = livenames.apply_to(catalog, str(path))

        assert applied == 1
        assert catalog.display_name("USER", 1) == "Fine"
        assert catalog.display_name("USER", 0) == cat.UNNAMED

    def test_apply_to_reports_how_many(self, tmp_path):
        path = str(tmp_path / "live-names.json")
        livenames.save({livenames.key("USER", n): f"P{n}" for n in (1, 2, 3)}, path)
        assert livenames.apply_to(cat.empty(), path) == 3

    def test_stored_names_beat_the_printed_list(self, tmp_path):
        """The disagreement is the point, so it must survive a restart too."""
        path = str(tmp_path / "live-names.json")
        livenames.save({livenames.key("USER", 1): "Analogue!"}, path)

        catalog = cat.Catalog([cat.Entry("USER", 1, "Grand XV")])
        for key, name in livenames.load(path).items():
            bank_id, _, number = key.rpartition(":")
            catalog.set_live_name(bank_id, int(number), name)

        assert catalog.display_name("USER", 1) == "Analogue!"
        assert catalog.differs("USER", 1), "the * marker depends on this"
        # And the printed name is still there to disagree with.
        assert catalog.name("USER", 1) == "Grand XV"


class TestTheCliUsesIt:
    """`rxvcli` was the odd one out: it read names and threw them away.

    `rxved` cached what it read at startup, so `rxvcli read USER` printing
    the same names and keeping none of them meant the shell could not prime
    the cache the browser then waited three seconds to fill.
    """

    def _args(self, tmp_path, *argv):
        from rxved import cli

        return cli.build_parser().parse_args(
            [
                "--live-names",
                str(tmp_path / "live-names.json"),
                "--favorites",
                str(tmp_path / "f.db"),
                *argv,
            ]
        )

    def test_read_caches_what_it_reads(self, tmp_path):
        from rxved import cli
        from rxved.demo import DemoBridge

        path = str(tmp_path / "live-names.json")
        bridge = DemoBridge()
        args = self._args(tmp_path, "read", "USER")

        cli._cmd_read(bridge, args)

        stored = livenames.load(path)
        expected = bridge.read_user_bank("USER")
        assert stored, "read saved nothing"
        for number, name in expected.items():
            assert stored[f"USER:{number:03d}"] == name

    def test_list_shows_the_cached_names(self, tmp_path):
        """Otherwise `read` would be pointless: nothing would use the result.

        Before this, `rxvcli list USER` reported the factory list even
        straight after a read -- the same wrong answer the browser used to
        give, reached by a different route.
        """
        from rxved import cli

        path = str(tmp_path / "live-names.json")
        args = self._args(tmp_path, "list", "USER")
        livenames.save({livenames.key("USER", 1): "Analogue!"}, path)

        catalog = cli._catalog(args)

        assert catalog.display_name("USER", 1) == "Analogue!"
        assert catalog.is_live("USER", 1)

    def test_no_cache_file_is_not_an_error(self, tmp_path):
        from rxved import cli

        args = self._args(tmp_path, "list", "USER")
        assert cli._catalog(args) is not None
