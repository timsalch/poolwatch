"""Runs a Roboflow Workflow on an image and returns parsed detections."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from .config import RoboflowConfig
from .detections import Detection, parse_workflow_output, rescale, workflow_image_size


class Detector(Protocol):
    def detect(self, image_path: str | Path) -> list[Detection]: ...


class RoboflowWorkflowDetector:
    """Calls a Workflow on Roboflow's hosted API or a local Inference server.

    Build one Workflow that outputs both person detections and your custom
    debris detections; the parser collects every box regardless of output name.
    Point api_url at http://localhost:9001 to run against `inference server start`.
    """

    def __init__(
        self,
        config: RoboflowConfig,
        client=None,
        reference_size: tuple[int, int] | None = None,
    ) -> None:
        """reference_size: the (width, height) the zones were drawn on. Detections from
        frames of another resolution are rescaled to it."""
        self._config = config
        self._reference_size = reference_size
        self._client = client if client is not None else self._make_client(config)

    @staticmethod
    def _make_client(config: RoboflowConfig):
        from inference_sdk import InferenceConfiguration, InferenceHTTPClient  # optional dependency

        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Set the {config.api_key_env} environment variable to your Roboflow API key"
            )
        client = InferenceHTTPClient(api_url=config.api_url, api_key=api_key)
        try:
            # Send the key in a header rather than the URL (inference-sdk >= 1.5).
            client = client.configure(InferenceConfiguration(api_key_transport="header"))
        except TypeError:
            pass  # older SDK without that option
        return client

    def detect(self, image_path: str | Path) -> list[Detection]:
        result = self._client.run_workflow(
            workspace_name=self._config.workspace,
            workflow_id=self._config.workflow_id,
            images={"image": str(image_path)},
            parameters=dict(self._config.parameters) or None,
            use_cache=True,
        )
        detections = parse_workflow_output(result)
        if self._reference_size:
            frame_size = workflow_image_size(result)
            if frame_size:
                detections = rescale(detections, frame_size, self._reference_size)
        return detections


class CachingDetector:
    """Remembers detections per image so replaying a demo doesn't re-bill Roboflow.

    The cache key is the image's path, size and modification time plus the
    Workflow id, so a retrained Workflow (new id) or an edited image is re-run.
    """

    def __init__(self, inner: Detector, cache_dir: str | Path, key: str = "") -> None:
        import hashlib
        import json

        self._inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._key = key
        self._hashlib, self._json = hashlib, json

    def _path_for(self, image_path: str | Path) -> Path:
        p = Path(image_path)
        st = p.stat()
        raw = f"{self._key}|{p.resolve()}|{st.st_size}|{st.st_mtime_ns}"
        return self._dir / (self._hashlib.sha1(raw.encode()).hexdigest() + ".json")

    def detect(self, image_path: str | Path) -> list[Detection]:
        cached = self._path_for(image_path)
        if cached.exists():
            return [Detection(**d) for d in self._json.loads(cached.read_text())]
        dets = self._inner.detect(image_path)
        cached.write_text(self._json.dumps([d.__dict__ for d in dets]))
        return dets
