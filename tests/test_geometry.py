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


# Pool 0..100 square with a built-in table at 70..90 x 10..30 (a corner).
POOL = Zone.from_list("water", [[0, 0], [100, 0], [100, 100], [0, 100]],
                      [[[70, 10], [90, 10], [90, 30], [70, 30]]])


def test_excluded_area_not_in_zone():
    assert not POOL.contains((80, 20))   # on the table
    assert POOL.contains((95, 20))       # water between table and wall
    assert POOL.contains((80, 35))       # water just past the table
    assert POOL.contains((50, 50))


def test_area_subtracts_exclusions():
    assert POOL.area() == 10_000 - 400


def test_multiple_exclusions():
    z = Zone.from_list("w", [[0, 0], [10, 0], [10, 10], [0, 10]],
                       [[[1, 1], [2, 1], [2, 2], [1, 2]], [[7, 7], [8, 7], [8, 8], [7, 8]]])
    assert not z.contains((1.5, 1.5)) and not z.contains((7.5, 7.5)) and z.contains((5, 5))
    assert z.area() == 98


def test_exclusion_needs_three_points():
    with pytest.raises(ValueError):
        Zone.from_list("w", [[0, 0], [10, 0], [10, 10]], [[[1, 1], [2, 2]]])
