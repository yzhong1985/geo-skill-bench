from __future__ import annotations

import pytest
from pydantic import ValidationError

from geoskillbench.data_service.models import DatasetDescriptor, RunRegistration
from geoskillbench.models.scenario import MCPServerConfig


def _descriptor(**overrides):
    value = {
        "handle": "dh_input_1",
        "alias": "schools",
        "role": "input",
        "run_id": "run_1",
        "geometry_type": "Point",
        "crs": "EPSG:4326",
    }
    value.update(overrides)
    return value


def test_formal_transport_rejects_legacy_modes() -> None:
    with pytest.raises(ValidationError):
        MCPServerConfig(id="gis", name="GIS", transport="mock", url="mock://gis")
    with pytest.raises(ValidationError):
        MCPServerConfig(id="gis", name="GIS", transport="stdio", url="http://localhost")


def test_transport_requires_http_endpoint() -> None:
    with pytest.raises(ValidationError):
        MCPServerConfig(id="gis", name="GIS", transport="http", url="mock://gis")


def test_descriptor_rejects_physical_location_metadata() -> None:
    with pytest.raises(ValidationError):
        DatasetDescriptor.model_validate(_descriptor(metadata={"table": "schools"}))


def test_registration_keeps_reference_outside_inputs() -> None:
    registration = RunRegistration.model_validate(
        {
            "run_id": "run_1",
            "inputs": [_descriptor()],
            "references": [_descriptor(handle="dh_ref", alias="expected", role="reference")],
        }
    )
    assert registration.inputs[0].role == "input"
    assert registration.references[0].role == "reference"


def test_supermap_result_is_rewritten_to_opaque_handle() -> None:
    from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter

    adapter = MCPToolAdapter()
    adapter.register_datasets({}, run_id="run_abc")
    public = adapter._register_generated_dataset(
        {
            "success": True,
            "bufferResult": "agentx_gpa_result_sdx_tmp_createbuffer_x",
            "tableName": "tmp_createBuffer_x",
            "bufferResultSvcURL": "http://192.168.168.20:8090/iserver/services/data-gpa-result/rest/data/datasources/gpa_result/datasets/tmp_createBuffer_x",
        }
    )
    assert public == {
        "success": True,
        "handle": "dh_run_abc_buffer_result",
        "dataset": "buffer_result",
        "alias": "buffer_result",
        "role": "result",
        "run_id": "run_abc",
    }
    assert "tableName" not in public
    assert "bufferResultSvcURL" not in public
    stored = adapter.get_dataset_store()["buffer_result"]
    dumped = stored.model_dump()
    assert dumped["handle"] == "dh_run_abc_buffer_result"
    assert "tmp_createBuffer_x" not in str(dumped)
    location = adapter.get_result_location("buffer_result")
    assert location is not None
    assert location["tableName"] == "tmp_createBuffer_x"


def test_http_tool_event_registers_supermap_result_for_assertions() -> None:
    from geoskillbench.executors.http_agent_executor import HttpAgentExecutor
    from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
    from geoskillbench.models.result import ToolCallRecord

    adapter = MCPToolAdapter()
    adapter.register_datasets({}, run_id="run_ext")
    executor = HttpAgentExecutor(adapter)
    calls: list[ToolCallRecord] = []
    pending: dict = {}
    executor._consume_tool_event(
        {"name": "createBuffer", "event_type": "tool_start", "run_id": "t1", "input": {"distance": 500}},
        pending,
        calls,
    )
    executor._consume_tool_event(
        {
            "name": "createBuffer",
            "event_type": "tool_end",
            "run_id": "t1",
            "output": {
                "success": True,
                "tableName": "tmp_createBuffer_x",
                "bufferResult": "agentx_gpa_result_sdx_tmp_createbuffer_x",
                "bufferResultSvcURL": "http://example.invalid/iserver/tmp_createBuffer_x",
            },
        },
        pending,
        calls,
    )
    assert calls[0].result == {
        "success": True,
        "handle": "dh_run_ext_buffer_result",
        "dataset": "buffer_result",
        "alias": "buffer_result",
        "role": "result",
        "run_id": "run_ext",
    }
    assert adapter.get_result_location("buffer_result")["tableName"] == "tmp_createBuffer_x"


def test_http_tool_event_keeps_non_gis_payload() -> None:
    from geoskillbench.executors.http_agent_executor import HttpAgentExecutor
    from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
    from geoskillbench.models.result import ToolCallRecord

    adapter = MCPToolAdapter()
    adapter.register_datasets({}, run_id="run_ext")
    executor = HttpAgentExecutor(adapter)
    calls: list[ToolCallRecord] = []
    executor._consume_tool_event(
        {"name": "chat", "event_type": "tool_end", "run_id": "t2", "output": {"text": "完成"}},
        {},
        calls,
    )
    assert calls[0].result == {"text": "完成"}
    assert adapter.get_result_location("buffer_result") is None


def test_registration_rejects_cross_run_descriptor() -> None:
    with pytest.raises(ValidationError):
        RunRegistration.model_validate({"run_id": "run_1", "inputs": [_descriptor(run_id="run_2")]})
