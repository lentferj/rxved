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

"""``rxvcli fav``, end to end.

This command had no command-level test of any kind. The flags it had earned
-- ``--rate``, and the ``--add`` that carries an existing rating through
rather than wiping it -- were the subtlest logic in the CLI and had never
been run, which is how ``rxvcli mode`` came to raise ``KeyError: 'mode'``
with a ``--help`` that promised it.

So these drive the parser and the handler the way a shell does, and assert
on the database and on stdout rather than on internals. The pattern is
``tests/test_scan_catalog.py``'s: build the args, call the command.
"""

import pytest

from rxved import cli

#: Slot names are the instrument's, not ours, and these assertions never name
#: one: the bank id and the displayed number identify a slot, which is all a
#: command-line test needs to say.
USER_1 = "USER:001"
USER_2 = "USER:002"
USER_3 = "USER:003"


def _argv(tmp_path, *argv):
    """``rxvcli``'s arguments with its favourites pointed at a scratch file."""
    return ["--favorites", str(tmp_path / "favorites.db"), *argv]


def _args(tmp_path, *argv):
    return cli.build_parser().parse_args(_argv(tmp_path, *argv))


def _fav(args):
    """The store the args point at, for reading back what a command did."""
    from rxved.favorites import Favorites

    return Favorites(args.favorites)


def _api(argv):
    """``BANK:N`` into what :meth:`rxved.favorites.Favorites.get` takes."""
    bank_id, number = argv.split(":")
    return bank_id, int(number)


class TestRate:
    """``--rate``: the one verb that writes a rating and nothing else."""

    def test_it_sets_the_rating_and_leaves_the_rest_alone(self, tmp_path):
        args = _args(
            tmp_path, "fav", "--add", USER_1, "--rating", "4", "--tags", "lead"
        )
        cli._cmd_favorites(None, args)
        args = _args(tmp_path, "fav", "--rate", USER_1, "2")
        cli._cmd_favorites(None, args)
        with _fav(args) as store:
            found = store.get(*_api(USER_1))
            assert (found.rating, found.tags) == (2, "lead")

    def test_it_refuses_a_slot_that_is_not_a_favourite(self, tmp_path):
        """``set_rating`` routes through ``add()``, so a slot that is not a
        favourite would be *created* by the attempt to rate it -- a row the
        user never asked for, wearing a rating they did mean to give."""
        args = _args(tmp_path, "fav", "--rate", USER_3, "3")
        with pytest.raises(SystemExit) as caught:
            cli._cmd_favorites(None, args)
        assert "not a favourite" in str(caught.value)
        with _fav(args) as store:
            assert store.get(*_api(USER_3)) is None

    def test_a_rating_outside_zero_to_five_is_one_line_not_a_traceback(self, tmp_path):
        """``set_rating`` raises a bare ``ValueError``, which used to reach
        the shell as a stack trace for what is a typing slip."""
        args = _args(tmp_path, "fav", "--add", USER_1)
        cli._cmd_favorites(None, args)
        args = _args(tmp_path, "fav", "--rate", USER_1, "9")
        with pytest.raises(SystemExit) as caught:
            cli._cmd_favorites(None, args)
        assert "--rate wants 0-5" in str(caught.value)
        with _fav(args) as store:
            assert store.get(*_api(USER_1)).rating == 0


class TestAddIsNotAWipe:
    """``--add`` on a slot you already have.

    The command is an upsert that *replaces* rating, tags and note, so
    anything not passed would silently wipe it. It reads what is there and
    carries it through, which is what "favourite this and rate it 4" means
    to a person.
    """

    def test_re_adding_without_a_rating_keeps_it(self, tmp_path):
        args = _args(tmp_path, "fav", "--add", USER_1, "--rating", "5")
        cli._cmd_favorites(None, args)
        args = _args(tmp_path, "fav", "--add", USER_1)
        cli._cmd_favorites(None, args)
        with _fav(args) as store:
            assert store.get(*_api(USER_1)).rating == 5

    def test_re_adding_with_a_rating_replaces_only_that(self, tmp_path):
        args = _args(tmp_path, "fav", "--add", USER_1, "--rating", "5", "--note", "top")
        cli._cmd_favorites(None, args)
        args = _args(tmp_path, "fav", "--add", USER_1, "--rating", "1")
        cli._cmd_favorites(None, args)
        with _fav(args) as store:
            found = store.get(*_api(USER_1))
            assert (found.rating, found.note) == (1, "top")

    def test_add_rejects_a_rating_outside_zero_to_five(self, tmp_path):
        """The store has no CHECK constraint on the column, so this is the
        only thing standing between a typing slip and a rating that breaks the
        column's value-and-scale invariant -- seven stars with no scale, or a
        negative one that renders as dots and that ``--rating-exactly 0``
        cannot find."""
        for bad in ("7", "-1"):
            args = _args(tmp_path, "fav", "--add", USER_1, "--rating", bad)
            with pytest.raises(SystemExit) as caught:
                cli._cmd_favorites(None, args)
            assert "--rating wants 0-5" in str(caught.value)
        args = _args(tmp_path, "fav")
        with _fav(args) as store:
            assert store.get(*_api(USER_1)) is None


class TestRatingFilters:
    """``--rating-at-least`` and ``--rating-exactly``."""

    def _seeded(self, tmp_path):
        """Three favourites: five stars, three stars, none."""
        args = _args(tmp_path, "fav", "--add", USER_1, "--rating", "5")
        cli._cmd_favorites(None, args)
        args = _args(tmp_path, "fav", "--add", USER_2, "--rating", "3")
        cli._cmd_favorites(None, args)
        args = _args(tmp_path, "fav", "--add", USER_3)
        cli._cmd_favorites(None, args)
        return _args(tmp_path, "fav")

    @staticmethod
    def _listed(capsys):
        """The slot numbers in the printed table, in the order printed."""
        lines = capsys.readouterr().out.splitlines()
        rows = [line.split()[1] for line in lines if line.startswith("USER")]
        return rows

    def test_at_least_keeps_the_rated_and_up(self, tmp_path, capsys):
        """Which rows survive, and in the order they print: the default
        ``--order added`` is newest first, so the three-star slot seeded
        second prints ahead of the five-star one."""
        self._seeded(tmp_path)
        cli._cmd_favorites(None, _args(tmp_path, "fav", "--rating-at-least", "3"))
        assert self._listed(capsys) == ["002", "001"]

    def test_exactly_zero_is_the_unrated_one(self, tmp_path, capsys):
        self._seeded(tmp_path)
        cli._cmd_favorites(None, _args(tmp_path, "fav", "--rating-exactly", "0"))
        assert self._listed(capsys) == ["003"]

    def test_nothing_matching_says_so_and_exits_zero(self, tmp_path, capsys):
        self._seeded(tmp_path)
        cli._cmd_favorites(None, _args(tmp_path, "fav", "--rating-exactly", "2"))
        assert "no favourites" in capsys.readouterr().err

    def test_it_stacks_on_a_tag_filter(self, tmp_path, capsys):
        """A post-filter over the tag selection, not a branch beside it:
        ``--tag lead --rating-at-least 3`` has to mean what it says."""
        args = _args(
            tmp_path, "fav", "--add", USER_1, "--rating", "5", "--tags", "lead"
        )
        cli._cmd_favorites(None, args)
        args = _args(tmp_path, "fav", "--add", USER_2, "--rating", "5", "--tags", "pad")
        cli._cmd_favorites(None, args)
        args = _args(
            tmp_path, "fav", "--add", USER_3, "--rating", "1", "--tags", "lead"
        )
        cli._cmd_favorites(None, args)
        cli._cmd_favorites(
            None, _args(tmp_path, "fav", "--tag", "lead", "--rating-at-least", "3")
        )
        assert self._listed(capsys) == ["001"]

    def test_both_flags_together_is_a_usage_error(self, tmp_path, capsys):
        # The parser's own message, not ours, so the pair has to reach the
        # parser as a list of arguments rather than as parsed args.
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(
                _argv(
                    tmp_path,
                    "fav",
                    "--rating-at-least",
                    "3",
                    "--rating-exactly",
                    "0",
                )
            )
        assert "not allowed with" in capsys.readouterr().err

    def test_the_column_shows_the_stars(self, tmp_path, capsys):
        self._seeded(tmp_path)
        cli._cmd_favorites(None, _args(tmp_path, "fav", "--rating-exactly", "5"))
        out = capsys.readouterr().out
        # Value and scale, the same string the browser's column shows.
        assert "★★★★★" in out
        assert "★★★··" not in out
