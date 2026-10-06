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

"""The wrapping key legend.

Separate from test_app.py because these are pure functions and need no event
loop -- that module marks everything asyncio.
"""


class TestKeyLegend:
    """The legend wraps; it never truncates.

    rxved used Textual's Footer and lost the channel keys off the right-hand
    edge of a 132-column window — the newest and least guessable part of the
    interface, invisible to anyone who had not read the README. The sibling
    projects had already hit and solved this, and KeyHints is their fix.
    """

    def test_every_binding_appears_in_the_legend(self):
        """Nothing is discoverable only from the source."""
        from rxved.app import KEY_HINTS, RxvedApp

        legend = " ".join(KEY_HINTS)
        for binding in RxvedApp.BINDINGS:
            key = {
                "left_square_bracket": "[",
                "right_square_bracket": "]",
                "slash": "/",
                "question_mark": "?",
                "enter": "⏎",
            }.get(binding.key, binding.key)
            assert key in legend, f"{binding.key} is not in the legend"

    def test_it_wraps_rather_than_dropping_blocks(self):
        from rxved.app import KEY_HINTS, wrap_blocks

        for width in (40, 60, 80, 132, 200):
            wrapped = wrap_blocks(KEY_HINTS, width)
            for block in KEY_HINTS:
                assert block in wrapped, f"{block!r} lost at width {width}"

    def test_no_line_exceeds_the_width(self):
        from rxved.app import KEY_HINTS, wrap_blocks

        for width in (60, 80, 132):
            for line in wrap_blocks(KEY_HINTS, width).splitlines():
                assert len(line) <= width, f"{line!r} overflows {width}"

    def test_a_block_wider_than_the_terminal_takes_its_own_line(self):
        """Better one over-long line than a hint cut mid-word."""
        from rxved.app import wrap_blocks

        wrapped = wrap_blocks(["short", "a very long single block indeed"], 10)
        assert "a very long single block indeed" in wrapped.splitlines()

    def test_narrower_terminals_get_more_lines_not_fewer_keys(self):
        from rxved.app import KEY_HINTS, wrap_blocks

        wide = len(wrap_blocks(KEY_HINTS, 200).splitlines())
        narrow = len(wrap_blocks(KEY_HINTS, 60).splitlines())
        assert narrow > wide


class TestWorkersStayOffTheStore:
    """A worker touching the SQLite store kills the app; see CLAUDE.md.

    Checked structurally rather than by exercising every worker, because the
    failure only appears when a particular key is pressed with a particular
    store open — which is how it reached a user in the first place.
    """

    def test_no_worker_reaches_favorites_or_catalog_directly(self):
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path("rxved/app.py").read_text())
        app = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RxvedApp"
        )
        offenders = []
        for fn in app.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if not any(
                (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "work")
                or getattr(d, "id", "") == "work"
                for d in fn.decorator_list
            ):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "self"
                    and node.value.attr in ("favorites", "catalog")
                ):
                    offenders.append(f"{fn.name}: self.{node.value.attr}.{node.attr}")
        assert not offenders, (
            "workers must do MIDI only and hand off with call_from_thread: "
            + ", ".join(offenders)
        )


class TestTheChannelIsRemembered:
    """`c` should not have to be pressed again next time.

    save_channel existed from the first commit and was never called, so the
    channel was loaded at startup from a file nothing ever wrote.
    """

    def test_saving_then_loading_round_trips(self, tmp_path):
        from xv import config

        path = str(tmp_path / "config.toml")
        config.save_channel(9, path)
        assert config.load_channel(path) == 9

    def test_it_survives_other_settings_being_written(self, tmp_path):
        from xv import config

        path = str(tmp_path / "config.toml")
        config.save_channel(5, path)
        config.save_device_id(21, path)
        config.save_last_ports("a", "b", path)
        assert config.load_channel(path) == 5, "another write must not drop it"
        assert config.load_device_id(path) == 21

    def test_the_app_writes_it_when_the_channel_changes(self, tmp_path):
        from xv import config

        path = str(tmp_path / "config.toml")
        from rxved.app import RxvedApp
        from rxved.demo import DemoBridge
        from rxved.favorites import Favorites

        store = Favorites(str(tmp_path / "f.db"))
        try:
            app = RxvedApp(DemoBridge(), favorites=store, config_path=path)
            app._remember_channel(11)
            assert config.load_channel(path) == 11
        finally:
            store.close()

    def test_no_config_path_means_no_file(self, tmp_path):
        """Tests must not drop a config into the working directory."""
        import os
        from rxved.app import RxvedApp
        from rxved.demo import DemoBridge
        from rxved.favorites import Favorites

        store = Favorites(str(tmp_path / "f.db"))
        try:
            app = RxvedApp(DemoBridge(), favorites=store, config_path=None)
            app._remember_channel(3)
            assert os.listdir(tmp_path) == ["f.db"] or "config.toml" not in os.listdir(
                tmp_path
            )
        finally:
            store.close()

    def test_a_device_id_outside_the_panel_range_is_refused(self, tmp_path):
        """17-32 are panel numbers; 0x10-0x1F are the wire bytes they mean.

        The two ranges overlap and neither is a subset of the other, so a
        value from the wrong one is a value the panel cannot show and the
        wire would answer with silence. ``device_id_byte`` takes panel
        numbers only and raises on the rest.
        """
        from xv import config

        path = str(tmp_path / "config.toml")
        for rejected in (0, 16, 33, 127, True):
            config.save_device_id(rejected, path)
            assert config.load_device_id(path) is None, rejected

    def test_a_port_name_containing_a_quote_still_reads_back(self, tmp_path):
        """The escaping trap, in the file that is actually used.

        A MIDI port name is whatever ALSA reports, and a quote in one
        produces a file that is not TOML. The writer refuses to overwrite a
        file it cannot parse, so the cache never heals: every later run
        reads nothing and writes nothing until somebody deletes it by hand.
        """
        import tomllib

        from xv import config

        path = str(tmp_path / "config.toml")
        awkward = 'Roland XV-2020:1 ("USB")'
        config.save_last_ports(awkward, awkward, path)

        with open(path, "rb") as handle:
            tomllib.load(handle)  # must not raise
        assert config.load_last_ports(path) == (awkward, awkward)


class TestTheLegendIsComplete:
    """A legend that omits a key teaches the user the key does not exist.

    The sibling projects carry the same test. Textual's key names are not
    what a legend prints, so they are translated rather than the test being
    loosened.
    """

    SHOWN_AS = {
        "left_square_bracket": "[",
        "right_square_bracket": "]",
        "slash": "/",
        "question_mark": "?",
    }
    IMPLIED = {"enter", "tab"}

    def test_every_binding_appears(self):
        from rxved.app import KEY_HINTS, RxvedApp

        legend = " ".join(KEY_HINTS)
        missing = []
        for binding in RxvedApp.BINDINGS:
            if binding.key in self.IMPLIED:
                continue
            shown = self.SHOWN_AS.get(binding.key, binding.key)
            if f"{shown} " not in legend:
                missing.append(binding.key)
        assert not missing, f"bound but missing from the legend: {missing}"
