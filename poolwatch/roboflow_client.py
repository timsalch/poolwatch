"""Runs a Roboflow Workflow on an image and returns parsed detections."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from .config import RoboflowConfig
from .detections import Detection, parse_workflow_output


class Detector(Protocol):
    def detect(self, image_path: str | Path) -> list[Detection]: ...


class RoboflowWorkflowDetector:
    """Calls a Workflow on Roboflow's hosted API or a local Inference server.

    Build one Workflow that outputs both person detections and your custom
    debris detections; the parser collects every box regardless of output name.
    Point api_url at http://localhost:9001 to run against `inference server start`.
    """

    def __init__(self, config: RoboflowConfig) -> None:
        from inference_sdk import InferenceHTTPClient  # optional dependency

        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise RuntimeError(f"Set the {config.api_key_env} environment variable")
        self._client = InferenceHTTPClient(api_url=config.api_url, api_key=api_key)
        self._config = config

    def detect(self, image_path: str | Path) -> list[Detection]:
        result = self._client.run_workflow(
            workspace_name=self._config.workspace,
            workflow_id=self._config.workflow_id,
            images={"image": str(image_path)},
        )
        return parse_workflow_output(result)
