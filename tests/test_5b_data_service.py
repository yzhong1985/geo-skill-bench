from __future__ import annotations

import json
import threading
from typing import Any

import httpx
import pytest

from geoskillbench.data_service.client import DataServiceClient, DataServiceError


def test_data_service_client_redacts_auth_and_maps_http_errors() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(403, json={"code": "forbidden"}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = DataServiceClient("http://data.local", token="secret", client=client)
    with pytest.raises(DataServiceError, match="forbidden") as error:
        service.release_run("run_1")
    assert error.value.code == "forbidden"
    assert seen["authorization"] == "Bearer secret"
    assert "secret" not in str(error.value)
    client.close()


def test_data_service_client_validates_registration_response() -> None:
    descriptor = {
        "handle": "dh_input",
        "alias": "schools",
        "role": "input",
        "run_id": "run_1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"run_id": "run_1", "inputs": [descriptor], "references": []},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    registration = DataServiceClient("http://data.local", client=client).register_run(
        "run_1", scenario_id="scenario", inputs=["schools-v1"]
    )
    assert registration.inputs[0].handle == "dh_input"
    client.close()
