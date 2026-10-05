import pytest

from poolwatch.config import RoboflowConfig
from poolwatch.roboflow_client import RoboflowWorkflowDetector


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run_workflow(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


RESULT = [{"predictions": {"predictions": [
    {"x": 10, "y": 10, "width": 4, "height": 4, "confidence": 0.8, "class": "leaf"}]}}]


def test_detect_sends_workflow_ids_image_and_parameters():
    cfg = RoboflowConfig("tim-ws", "pool-wf", parameters={"confidence": 0.4, "max_detections": 1000})
    client = FakeClient(RESULT)
    dets = RoboflowWorkflowDetector(cfg, client=client).detect("frame.jpg")
    call = client.calls[0]
    assert call["workspace_name"] == "tim-ws" and call["workflow_id"] == "pool-wf"
    assert call["images"] == {"image": "frame.jpg"}
    assert call["parameters"] == {"confidence": 0.4, "max_detections": 1000}
    assert [d.label for d in dets] == ["leaf"]


def test_no_parameters_sends_none():
    client = FakeClient([])
    RoboflowWorkflowDetector(RoboflowConfig("w", "f"), client=client).detect("x.jpg")
    assert client.calls[0]["parameters"] is None


def test_missing_api_key_explains_where_it_goes(monkeypatch):
    monkeypatch.delenv("ROBOFLOW_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ROBOFLOW_API_KEY environment variable"):
        RoboflowWorkflowDetector(RoboflowConfig("w", "f"))


def test_real_sdk_client_builds_with_header_auth(monkeypatch):
    pytest.importorskip("inference_sdk")
    monkeypatch.setenv("ROBOFLOW_API_KEY", "test-key-not-real")
    det = RoboflowWorkflowDetector(RoboflowConfig("w", "f"))
    assert det._client is not None  # no network call happens at construction


def test_detections_rescaled_to_zone_size():
    result = [{"p": {"image": {"width": 640, "height": 360},
                     "predictions": [{"x": 320, "y": 180, "width": 10, "height": 10,
                                      "confidence": 0.9, "class": "leaf"}]}}]
    det = RoboflowWorkflowDetector(RoboflowConfig("w", "f"), client=FakeClient(result),
                                   reference_size=(1920, 1080))
    (d,) = det.detect("snap.jpg")
    assert (d.x, d.y) == (960, 540)


def test_no_reference_size_leaves_coordinates():
    result = [{"p": {"image": {"width": 640, "height": 360},
                     "predictions": [{"x": 320, "y": 180, "width": 10, "height": 10,
                                      "confidence": 0.9, "class": "leaf"}]}}]
    (d,) = RoboflowWorkflowDetector(RoboflowConfig("w", "f"), client=FakeClient(result)).detect("s.jpg")
    assert (d.x, d.y) == (320, 180)
