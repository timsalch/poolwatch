import pytest

from poolwatch.geometry import Zone

SQUARE = Zone.from_list("sq", [[0, 0], [10, 0], [10, 10], [0, 10]])


def test_contains_inside_and_outside():
    assert SQUARE.contains((5, 5))
    assert not SQUARE.contains((15, 5))
    assert not SQUARE.contains((-1, -1))


def test_concave_polygon():
    # L-shape: notch cut out of the top-right.
    ell = Zone.from_list("L", [[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10]])
    assert ell.contains((2, 8))
    assert not ell.contains((8, 8))


def test_area():
    assert SQUARE.area() == 100
    tri = Zone.from_list("t", [[0, 0], [4, 0], [0, 3]])
    assert tri.area() == 6


def test_needs_three_points():
    with pytest.raises(ValueError):
        Zone.from_list("bad", [[0, 0], [1, 1]])
