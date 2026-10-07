# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# This file is part of rxved.

"""The highlighter-colour signals in ``read_marked_list.py``.

Only the blue signal has been tested on real paper. These tests pin the
synthetic yellow and orange signals to the ink colours they are meant to
catch, so a later change cannot quietly move them onto a different hue.
"""

from __future__ import annotations

import importlib.util
import os

import numpy as np
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL = os.path.join(os.path.dirname(_HERE), "tools", "read_marked_list.py")


def _tool():
    spec = importlib.util.spec_from_file_location("read_marked_list", _TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


marked = _tool()


def _pixel(rgb):
    return np.asarray([[[*rgb]]], dtype=int)


#: A pixel of each marker's ink on white paper, plus the neutral extremes.
BLUE = (120, 160, 230)
YELLOW = (250, 245, 110)
ORANGE = (250, 170, 80)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


@pytest.mark.parametrize(
    "marker,ink",
    [("blue", BLUE), ("yellow", YELLOW), ("orange", ORANGE)],
)
def test_each_marker_fires_on_its_own_ink(marker, ink):
    assert int(marked.marker_lift(_pixel(ink), marker)[0, 0]) >= marked.LIFT


@pytest.mark.parametrize("marker", ["blue", "yellow", "orange"])
def test_neutral_pixels_are_never_marker(marker):
    for ink in (WHITE, BLACK):
        assert int(marked.marker_lift(_pixel(ink), marker)[0, 0]) < marked.LIFT


def test_the_blue_signal_does_not_fire_on_warm_ink():
    for ink in (YELLOW, ORANGE):
        assert int(marked.marker_lift(_pixel(ink), "blue")[0, 0]) < marked.LIFT


def test_the_warm_signals_do_not_fire_on_blue():
    for marker in ("yellow", "orange"):
        assert int(marked.marker_lift(_pixel(BLUE), marker)[0, 0]) < marked.LIFT
