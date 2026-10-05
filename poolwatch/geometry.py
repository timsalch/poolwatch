"""Polygon zones in image pixel coordinates."""

from __future__ import annotations

from dataclasses import dataclass

Point = tuple[float, float]


def _as_points(raw: list[list[float]]) -> tuple[Point, ...]:
    return tuple((float(x), float(y)) for x, y in raw)


def _polygon_contains(pts: tuple[Point, ...], point: Point) -> bool:
    """Ray-casting point-in-polygon test. Points on an edge may go either way."""
    x, y = point
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _polygon_area(pts: tuple[Point, ...]) -> float:
    """Shoelace formula."""
    total = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


@dataclass(frozen=True)
class Zone:
    """A named polygon drawn over the camera image (pixel coordinates).

    `holes` are areas cut out of the zone, e.g. a built-in table in the pool:
    the water around it counts, the table itself doesn't.
    """

    name: str
    points: tuple[Point, ...]
    holes: tuple[tuple[Point, ...], ...] = ()

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError(f"Zone '{self.name}' needs at least 3 points")
        for hole in self.holes:
            if len(hole) < 3:
                raise ValueError(f"Each area excluded from zone '{self.name}' needs at least 3 points")

    @classmethod
    def from_list(
        cls, name: str, points: list[list[float]], holes: list[list[list[float]]] | None = None
    ) -> "Zone":
        return cls(name, _as_points(points), tuple(_as_points(h) for h in (holes or [])))

    def without_holes(self) -> "Zone":
        """The full outline, ignoring excluded areas."""
        return Zone(self.name, self.points) if self.holes else self

    def contains(self, point: Point) -> bool:
        if not _polygon_contains(self.points, point):
            return False
        return not any(_polygon_contains(h, point) for h in self.holes)

    def area(self) -> float:
        """Outer area minus excluded areas (holes are assumed to sit inside the zone)."""
        return max(0.0, _polygon_area(self.points) - sum(_polygon_area(h) for h in self.holes))
