from __future__ import annotations

from typing import Any

import httpx

from geoskillbench.data_service.models import ArchiveResult, DatasetDescriptor, ReleaseResult, RunRegistration
from geoskillbench.security.redaction import redact


class DataServiceError(RuntimeError):
    """数据服务调用失败，message 不包含认证信息。"""

    def __init__(self, message: str, *, status_code: int | None = None, code: str = "data_service_error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class DataServiceClient:
    """同步数据服务客户端；控制面不与 MCP tools/list/tools/call 混用。"""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("data service base_url must not be empty")
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout
        self._client = client

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        own_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout, headers=self._headers)
        try:
            response = client.request(method, url, json=payload, headers=self._headers or None)
            if response.status_code >= 400:
                detail = ""
                try:
                    body = response.json()
                    detail = str(body.get("code") or body.get("detail") or "") if isinstance(body, dict) else ""
                except ValueError:
                    pass
                code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict"}.get(
                    response.status_code, "data_service_error"
                )
                suffix = f": {detail}" if detail else ""
                raise DataServiceError(
                    f"data service request failed ({code}){suffix}",
                    status_code=response.status_code,
                    code=code,
                )
            try:
                value = response.json()
            except ValueError as exc:
                raise DataServiceError("data service returned invalid JSON", code="invalid_response") from exc
            if not isinstance(value, dict):
                raise DataServiceError("data service returned a non-object response", code="invalid_response")
            return value
        except httpx.HTTPError as exc:
            raise DataServiceError(f"data service network error: {type(exc).__name__}", code="network_error") from exc
        except DataServiceError:
            raise
        except Exception as exc:
            raise DataServiceError(f"data service request failed: {type(exc).__name__}", code="request_error") from exc
        finally:
            if own_client:
                client.close()

    def register_run(
        self,
        run_id: str,
        *,
        scenario_id: str,
        inputs: list[str] | None = None,
        references: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> RunRegistration:
        payload: dict[str, Any] = {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "inputs": inputs or [],
            "references": references or [],
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return RunRegistration.model_validate(self._request("POST", "/admin/runs", payload))

    def archive_run(self, run_id: str, *, evidence: dict[str, Any] | None = None) -> ArchiveResult:
        return ArchiveResult.model_validate(self._request("POST", f"/admin/runs/{run_id}/archive", evidence or {}))

    def release_run(self, run_id: str) -> ReleaseResult:
        return ReleaseResult.model_validate(self._request("DELETE", f"/admin/runs/{run_id}"))

    def inspect_dataset(self, handle: str) -> DatasetDescriptor:
        return DatasetDescriptor.model_validate(self._request("GET", f"/datasets/{handle}"))

    def read_for_evaluation(self, handle: str) -> bytes:
        url = f"{self.base_url}/datasets/{handle}/evaluation"
        own_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout, headers=self._headers)
        try:
            response = client.get(url, headers=self._headers or None)
            if response.status_code >= 400:
                raise DataServiceError(
                    f"evaluation read failed ({response.status_code})",
                    status_code=response.status_code,
                    code="evaluation_read_error",
                )
            return response.content
        except httpx.HTTPError as exc:
            raise DataServiceError(f"evaluation read network error: {type(exc).__name__}", code="network_error") from exc
        finally:
            if own_client:
                client.close()
