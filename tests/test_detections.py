from poolwatch.detections import Detection, filter_labels, parse_workflow_output
from poolwatch.geometry import Zone


def test_parse_nested_workflow_output():
    # Shape of a typical Workflow response with two model outputs.
    result = [{
        "people": {"image": {"width": 640, "height": 480},
                   "predictions": [{"x": 10, "y": 20, "width": 5, "height": 8,
                                    "confidence": 0.91, "class": "Person", "class_id": 0}]},
        "debris": {"predictions": [{"x": 1, "y": 2, "width": 3, "height": 4,
                                    "confidence": 0.5, "class": "leaf"}]},
        "count": 2,
    }]
    dets = parse_workflow_output(result)
    assert [d.label for d in dets] == ["person", "leaf"]
    assert dets[0].confidence == 0.91 and dets[0].width == 5


def test_parse_ignores_non_boxes():
    assert parse_workflow_output([{"image": {"width": 1, "height": 1}, "n": 3}]) == []
    assert parse_workflow_output([]) == []


def test_parse_accepts_class_name_key():
    dets = parse_workflow_output({"p": [{"x": 1, "y": 1, "width": 1, "height": 1,
                                         "class_name": "bug", "confidence": 0.7}]})
    assert dets[0].label == "bug"


def test_anchor_and_zone():
    zone = Zone.from_list("z", [[0, 0], [10, 0], [10, 10], [0, 10]])
    d = Detection("person", 0.9, 5, 8, 4, 6)  # center (5,8), bottom at y=11
    assert d.anchor("bottom_center") == (5, 11)
    assert d.in_zone(zone, "center")
    assert not d.in_zone(zone, "bottom_center")


def test_filter_labels_case_and_confidence():
    dets = [Detection("person", 0.9, 0, 0, 1, 1), Detection("person", 0.3, 0, 0, 1, 1),
            Detection("leaf", 0.9, 0, 0, 1, 1)]
    assert len(filter_labels(dets, ["Person"], 0.5)) == 1


def test_workflow_image_size_found():
    from poolwatch.detections import workflow_image_size
    result = [{"out": {"image": {"width": 640, "height": 360}, "predictions": []}}]
    assert workflow_image_size(result) == (640, 360)
    assert workflow_image_size([{"x": 1}]) is None


def test_rescale_snapshot_to_zone_resolution():
    from poolwatch.detections import rescale
    d = Detection("leaf", 0.9, 320, 180, 10, 20)
    (r,) = rescale([d], (640, 360), (1920, 1080))
    assert (r.x, r.y, r.width, r.height) == (960, 540, 30, 60)
    assert rescale([d], (1920, 1080), (1920, 1080)) == [d]
