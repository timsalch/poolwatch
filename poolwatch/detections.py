"""Detection model and parsing of Roboflow Workflow output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from .geometry import Point, Zone

Anchor = Literal["center", "bottom_center"]


@dataclass(frozen=True)
class Detection:
    """One bounding box. x/y are the box CENTER, matching Roboflow's convention."""

    label: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height

    def anchor(self, kind: Anchor = "center") -> Point:
        if kind == "bottom_center":
            return (self.x, self.y + self.height / 2)
        return (self.x, self.y)

    def in_zone(self, zone: Zone, anchor: Anchor = "center") -> bool:
        return zone.contains(self.anchor(anchor))


_BOX_KEYS = {"x", "y", "width", "height"}


def _looks_like_box(obj: Any) -> bool:
    return isinstance(obj, dict) and _BOX_KEYS <= obj.keys() and (
        "class" in obj or "class_name" in obj
    )


def _walk(obj: Any) -> Iterable[dict]:
    if _looks_like_box(obj):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def parse_workflow_output(result: Any) -> list[Detection]:
    """Pull every bounding box out of a Workflow result, whatever the output names are.

    Workflows return a list (one entry per input image) of dicts keyed by the
    output names you configured, so rather than hard-coding a name we walk the
    structure and collect anything shaped like a Roboflow prediction.
    """
    detections = []
    for box in _walk(result):
        detections.append(
            Detection(
                label=str(box.get("class", box.get("class_name"))).lower(),
                confidence=float(box.get("confidence", 0.0)),
                x=float(box["x"]),
                y=float(box["y"]),
                width=float(box["width"]),
                height=float(box["height"]),
            )
        )
    return detections


def filter_labels(
    detections: Iterable[Detection], labels: Iterable[str], min_confidence: float
) -> list[Detection]:
    wanted = {label.lower() for label in labels}
    return [d for d in detections if d.label in wanted and d.confidence >= min_confidence]
