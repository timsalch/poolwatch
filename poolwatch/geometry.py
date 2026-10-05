"""Polygon zones in image pixel coordinates."""

from __future__ import annotations

from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True)
class Zone:
    """A named polygon drawn over the camera image (pixel coordinates)."""

    name: str
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError(f"Zone '{self.name}' needs at least 3 points")

    @classmethod
    def from_list(cls, name: str, points: list[list[float]]) -> "Zone":
        return cls(name, tuple((float(x), float(y)) for x, y in points))

    def contains(self, point: Point) -> bool:
        """Ray-casting point-in-polygon test. Points on an edge may go either way."""
        x, y = point
        inside = False
        pts = self.points
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

    def area(self) -> float:
        """Shoelace formula."""
        pts = self.points
        total = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            total += x1 * y2 - x2 * y1
        return abs(total) / 2.0
