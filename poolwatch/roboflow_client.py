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

    def __init__(self, config: RoboflowConfig, client=None) -> None:
        self._config = config
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
        return parse_workflow_output(result)
