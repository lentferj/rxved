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

"""The favourites database."""

import os

import pytest

from rxved.favorites import Favorites, SCHEMA_VERSION


@pytest.fixture
def favorites(tmp_path):
    with Favorites(str(tmp_path / "favorites.db")) as store:
        yield store


class TestBasics:
    def test_starts_empty(self, favorites):
        assert len(favorites) == 0
        assert favorites.all() == []

    def test_add_then_get(self, favorites):
        favorites.add("PST-B", 29, name="Iron Drone")
        found = favorites.get("PST-B", 29)
        assert found is not None
        assert found.name == "Iron Drone"
        assert found.key == "PST-B:029"

    def test_contains_takes_either_form(self, favorites):
        favorites.add("PST-B", 29)
        assert ("PST-B", 29) in favorites
        assert "PST-B:029" in favorites
        assert ("PST-B", 30) not in favorites

    def test_toggle_flips_and_reports_the_new_state(self, favorites):
        assert favorites.toggle("USER", 1) is True
        assert len(favorites) == 1
        assert favorites.toggle("USER", 1) is False
        assert len(favorites) == 0

    def test_remove_reports_whether_there_was_one(self, favorites):
        favorites.add("USER", 1)
        assert favorites.remove("USER", 1) is True
        assert favorites.remove("USER", 1) is False

    def test_keys_for_bank_is_scoped(self, favorites):
        favorites.add("USER", 1)
        favorites.add("USER", 5)
        favorites.add("PST-A", 9)
        assert favorites.keys_for_bank("USER") == {1, 5}
        assert favorites.keys_for_bank("PST-A") == {9}
        assert favorites.keys_for_bank("PST-D") == set()


class TestIdentity:
    """Keyed on the slot, never on the name. See the module docstring."""

    def test_the_same_slot_is_one_row_however_it_is_named(self, favorites):
        favorites.add("USER", 1, name="Velvet Bell")
        favorites.add("USER", 1, name="Quiet Grain")
        assert len(favorites) == 1
        assert favorites.get("USER", 1).name == "Quiet Grain"

    def test_re_adding_preserves_the_original_date(self, favorites):
        """Re-adding is an edit, not a re-acquisition.

        Resetting ``added`` would corrupt the one ordering the user cannot
        reconstruct from anything else.
        """
        first = favorites.add("USER", 1, name="a")
        second = favorites.add("USER", 1, name="b")
        assert second.added == first.added

    def test_two_banks_may_hold_the_same_number(self, favorites):
        favorites.add("USER", 1)
        favorites.add("PST-A", 1)
        assert len(favorites) == 2


class TestFieldsSurviveEachOther:
    """The bug _merge() exists to prevent."""

    def test_setting_a_rating_keeps_tags_and_note(self, favorites):
        favorites.add("USER", 1, name="x", tags="pad, warm", note="verse")
        favorites.set_rating("USER", 1, 4)
        found = favorites.get("USER", 1)
        assert found.rating == 4
        assert found.tags == "pad, warm"
        assert found.note == "verse"
        assert found.name == "x"

    def test_setting_tags_keeps_rating_and_note(self, favorites):
        favorites.add("USER", 1, rating=5, note="chorus")
        favorites.set_tags("USER", 1, "bass")
        found = favorites.get("USER", 1)
        assert found.rating == 5
        assert found.note == "chorus"
        assert found.tags == "bass"

    def test_setting_a_note_keeps_the_rest(self, favorites):
        favorites.add("USER", 1, rating=3, tags="lead")
        favorites.set_note("USER", 1, "bridge")
        found = favorites.get("USER", 1)
        assert (found.rating, found.tags, found.note) == (3, "lead", "bridge")

    @pytest.mark.parametrize("rating", [-1, 6, 100])
    def test_rejects_an_out_of_range_rating(self, favorites, rating):
        favorites.add("USER", 1)
        with pytest.raises(ValueError):
            favorites.set_rating("USER", 1, rating)


class TestTags:
    def test_parsed_into_a_list(self, favorites):
        favorites.add("USER", 1, tags="pad, warm,  analog ")
        assert favorites.get("USER", 1).tag_list == ["pad", "warm", "analog"]

    def test_normalised_on_write(self, favorites):
        favorites.add("USER", 1)
        favorites.set_tags("USER", 1, " pad ,, PAD, warm ")
        assert favorites.get("USER", 1).tags == "pad, warm"

    def test_with_tag_does_not_match_a_substring(self, favorites):
        """"pad" must not match "padded", nor "lead" "misleading"."""
        favorites.add("USER", 1, tags="padded")
        favorites.add("USER", 2, tags="pad")
        hits = favorites.with_tag("pad")
        assert [f.number for f in hits] == [2]

    def test_with_tag_is_case_insensitive(self, favorites):
        favorites.add("USER", 1, tags="Pad")
        assert len(favorites.with_tag("pAD")) == 1

    def test_counts_tags_in_use(self, favorites):
        favorites.add("USER", 1, tags="pad, warm")
        favorites.add("USER", 2, tags="pad")
        assert favorites.tags() == {"pad": 2, "warm": 1}


class TestOrdering:
    def test_rejects_an_order_it_does_not_know(self, favorites):
        """ORDER BY cannot be a bound parameter, so the name is validated."""
        with pytest.raises(ValueError):
            favorites.all(order="name; DROP TABLE favorites")

    def test_by_bank_is_stable(self, favorites):
        favorites.add("PST-A", 9)
        favorites.add("USER", 1)
        favorites.add("PST-A", 2)
        rows = favorites.all(order="bank")
        assert [(f.bank_id, f.number) for f in rows] == [
            ("PST-A", 2), ("PST-A", 9), ("USER", 1)]


class TestSearchAndRefresh:
    def test_searches_name_tags_and_note(self, favorites):
        favorites.add("USER", 1, name="Quiet Grain")
        favorites.add("USER", 2, tags="grainy")
        favorites.add("USER", 3, note="a grain of it")
        assert len(favorites.search("grain")) == 3

    def test_refresh_names_reports_how_many_changed(self, favorites):
        favorites.add("USER", 1, name="Velvet Bell")
        favorites.add("USER", 2, name="Amber Wash")
        names = {1: "Quiet Grain", 2: "Amber Wash"}
        changed = favorites.refresh_names(
            lambda bank, number: names.get(number))
        assert changed == 1
        assert favorites.get("USER", 1).name == "Quiet Grain"

    def test_refresh_names_ignores_a_source_with_nothing_to_say(self, favorites):
        favorites.add("USER", 1, name="Velvet Bell")
        assert favorites.refresh_names(lambda bank, number: None) == 0
        assert favorites.get("USER", 1).name == "Velvet Bell"


class TestPersistence:
    def test_survives_a_reopen(self, tmp_path):
        path = str(tmp_path / "f.db")
        with Favorites(path) as store:
            store.add("PST-B", 29, name="Iron Drone", tags="bass")
        with Favorites(path) as store:
            found = store.get("PST-B", 29)
            assert found is not None
            assert found.tags == "bass"

    def test_creates_its_directory(self, tmp_path):
        path = str(tmp_path / "deep" / "deeper" / "f.db")
        with Favorites(path) as store:
            store.add("USER", 1)
        assert os.path.exists(path)

    def test_records_its_schema_version(self, tmp_path):
        path = str(tmp_path / "f.db")
        with Favorites(path) as store:
            version = store._db.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION

    def test_refuses_a_database_from_a_newer_build(self, tmp_path):
        """Dropping columns we do not understand is worse than not opening."""
        path = str(tmp_path / "f.db")
        with Favorites(path) as store:
            store._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            store._db.commit()
        with pytest.raises(RuntimeError, match="newer rxved"):
            Favorites(path)


class TestWhereItLives:
    """One assertion per platform, since only one of them can be exercised.

    ``sys.platform`` is patched rather than skipped-around: the point is that
    a change to :func:`data_dir` cannot quietly break the two platforms the
    author does not develop on.
    """

    def test_linux_follows_xdg(self, monkeypatch):
        import rxved.favorites as mod

        monkeypatch.setattr(mod.sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", "/data/xdg")
        assert mod.data_dir() == os.path.join("/data/xdg", "rxved")

    def test_linux_falls_back_to_local_share(self, monkeypatch):
        import rxved.favorites as mod

        monkeypatch.setattr(mod.sys, "platform", "linux")
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setenv("HOME", "/home/someone")
        assert mod.data_dir() == "/home/someone/.local/share/rxved"

    def test_a_relative_xdg_data_home_is_ignored(self, monkeypatch):
        """XDG requires absolute paths and says relative ones must be ignored.

        Honouring one would put the database wherever the process happened to
        be started, which for a TUI is wherever the user last ran `cd`.
        """
        import rxved.favorites as mod

        monkeypatch.setattr(mod.sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
        monkeypatch.setenv("HOME", "/home/someone")
        assert mod.data_dir() == "/home/someone/.local/share/rxved"

    def test_macos_uses_application_support(self, monkeypatch):
        import rxved.favorites as mod

        monkeypatch.setattr(mod.sys, "platform", "darwin")
        monkeypatch.setenv("HOME", "/Users/someone")
        assert mod.data_dir() == (
            "/Users/someone/Library/Application Support/rxved")

    def test_windows_uses_local_appdata_not_roaming(self, monkeypatch):
        """Roaming syncs the file; a synced SQLite database is a corrupt one."""
        import rxved.favorites as mod

        monkeypatch.setattr(mod.sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
        monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
        result = mod.data_dir()
        assert result.endswith("rxved")
        assert "Local" in result
        assert "Roaming" not in result

    def test_windows_falls_back_to_roaming_if_local_is_missing(self, monkeypatch):
        """A wrong-looking but writable path beats refusing to start."""
        import rxved.favorites as mod

        monkeypatch.setattr(mod.sys, "platform", "win32")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
        assert mod.data_dir().endswith("rxved")
        assert "Roaming" in mod.data_dir()

    def test_windows_with_no_environment_at_all_still_answers(self, monkeypatch):
        import rxved.favorites as mod

        monkeypatch.setattr(mod.sys, "platform", "win32")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert mod.data_dir().endswith("rxved")

    def test_the_database_sits_in_that_directory(self, monkeypatch):
        import rxved.favorites as mod

        monkeypatch.setattr(mod.sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", "/data/xdg")
        assert mod.default_path() == os.path.join(
            "/data/xdg", "rxved", "favorites.db")

    def test_an_explicit_path_overrides_the_platform_default(self, tmp_path):
        path = str(tmp_path / "elsewhere.db")
        with Favorites(path) as store:
            assert store.path == path


class TestThreadSafety:
    """The store is opened on one thread and read from others.

    A Textual worker asking `len()` of a default SQLite connection raises
    ProgrammingError *out of the worker*, which ends the application. That is
    what pressing `i` did the first time anybody pressed it.
    """

    def test_usable_from_another_thread(self, favorites):
        import concurrent.futures as cf

        favorites.add("USER", 1, name="Velvet Bell")
        with cf.ThreadPoolExecutor(1) as pool:
            assert pool.submit(lambda: len(favorites)).result() == 1
            assert pool.submit(
                lambda: favorites.get("USER", 1).name).result() == "Velvet Bell"

    def test_concurrent_writes_all_land(self, favorites):
        import concurrent.futures as cf

        def add(number):
            favorites.add("PST-A", number, name=f"slot {number}")

        with cf.ThreadPoolExecutor(8) as pool:
            list(pool.map(add, range(1, 65)))
        assert len(favorites) == 64

    def test_reads_are_not_torn_by_a_concurrent_write(self, favorites):
        """A cursor iterated after the lock is released would interleave."""
        import concurrent.futures as cf

        for number in range(1, 33):
            favorites.add("PST-A", number)

        def read():
            return len(favorites.all(order="bank"))

        def write(number):
            favorites.add("PST-B", number)

        with cf.ThreadPoolExecutor(8) as pool:
            futures = [pool.submit(read) for _ in range(20)]
            futures += [pool.submit(write, n) for n in range(1, 21)]
            counts = [f.result() for f in futures if f.result() is not None]
        # Every read returned a whole, self-consistent row set.
        assert all(isinstance(c, int) and c >= 32 for c in counts)


class TestUnfavouritingKeepsWhatTheUserTyped:
    """Un-favouriting used to delete the row, annotations and all.

    `f` in the browser unfavourites without asking, so a rating, tags, a
    note and the date it was first favourited were one keystroke from
    destruction -- on the one store this project calls the user's own. The
    row is now kept with its `active` flag cleared, and the same keystroke
    brings it back.
    """

    def test_a_toggle_cycle_keeps_rating_tags_and_note(self, tmp_path):
        with Favorites(str(tmp_path / "f.db")) as store:
            store.add("BANK", 5, name="Invented",
                      rating=4, tags="a, b", note="hello")
            before = store.get("BANK", 5)

            assert store.toggle("BANK", 5) is False
            assert store.get("BANK", 5) is None
            assert store.toggle("BANK", 5) is True

            after = store.get("BANK", 5)
            assert (after.rating, after.tags, after.note) == (4, "a, b",
                                                              "hello")
            assert after.added == before.added

    def test_an_unfavourited_slot_is_not_a_favourite(self, tmp_path):
        with Favorites(str(tmp_path / "f.db")) as store:
            store.add("BANK", 5, name="Invented", rating=3)
            store.remove("BANK", 5)
            assert len(store) == 0
            assert store.keys() == set()
            assert store.all() == []
            assert store.search("Invented") == []

    def test_forget_really_deletes(self, tmp_path):
        with Favorites(str(tmp_path / "f.db")) as store:
            store.add("BANK", 5, name="Invented", rating=3)
            store.remove("BANK", 5)
            assert [f.rating for f in store.dormant()] == [3]
            assert store.forget("BANK", 5) is True
            assert store.dormant() == []

    def test_a_v1_database_upgrades_without_losing_rows(self, tmp_path):
        import sqlite3

        path = str(tmp_path / "old.db")
        db = sqlite3.connect(path)
        db.executescript(
            """
            CREATE TABLE favorites (
                bank_id TEXT NOT NULL, number INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                rating INTEGER NOT NULL DEFAULT 0,
                tags TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                added REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (bank_id, number));
            """)
        db.execute("INSERT INTO favorites (bank_id, number, name, rating) "
                   "VALUES ('BANK', 7, 'Invented', 5)")
        db.execute("PRAGMA user_version = 1")
        db.commit()
        db.close()

        with Favorites(path) as store:
            kept = store.get("BANK", 7)
            assert kept is not None, "an existing row must stay a favourite"
            assert kept.rating == 5
