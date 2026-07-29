"""请求与 Chat turn 可观测性安全边界测试。"""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.error_handler import generic_error_handler
from app.observability import RequestObservabilityMiddleware, log_event


def _events(caplog) -> list[dict]:
    events = []
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except (json.JSONDecodeError, TypeError):
            continue
        if "event" in payload:
            events.append(payload)
    return events


def test_request_log_uses_correlation_id_without_query_or_auth(caplog):
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)

    @app.get("/items/{item_id}")
    def read_item(item_id: str):
        return {"item_id": item_id}

    secret_query = "private-question-must-not-be-logged"
    secret_token = "private-token-must-not-be-logged"
    caplog.set_level(logging.INFO)

    response = TestClient(app).get(
        f"/items/42?question={secret_query}",
        headers={
            "Authorization": f"Bearer {secret_token}",
            "X-Request-ID": "resume-demo-42",
        },
    )

    request_event = next(e for e in _events(caplog) if e["event"] == "request_completed")
    application_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "app.observability"
    )
    assert response.headers["x-request-id"] == "resume-demo-42"
    assert request_event["request_id"] == "resume-demo-42"
    assert request_event["route"] == "/items/{item_id}"
    assert request_event["status_code"] == 200
    assert secret_query not in application_logs
    assert secret_token not in application_logs


def test_invalid_request_id_is_replaced(caplog):
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)

    @app.get("/")
    def root():
        return {"status": "ok"}

    response = TestClient(app).get("/", headers={"X-Request-ID": "invalid id with spaces"})

    assert response.headers["x-request-id"] != "invalid id with spaces"
    assert len(response.headers["x-request-id"]) == 32


def test_structured_event_rejects_sensitive_field_names():
    with pytest.raises(ValueError, match="敏感日志字段"):
        log_event(logging.getLogger("test"), "unsafe", content="文档正文")


def test_generic_500_log_omits_exception_detail(caplog):
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware)
    app.add_exception_handler(Exception, generic_error_handler)
    secret_detail = "database-parameter-must-not-be-logged"

    @app.get("/boom")
    def boom():
        raise RuntimeError(secret_detail)

    caplog.set_level(logging.INFO)
    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    application_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("app.")
    )

    assert response.status_code == 500
    assert secret_detail not in application_logs
    failure_event = next(e for e in _events(caplog) if e["event"] == "request_failed")
    completed_event = next(e for e in _events(caplog) if e["event"] == "request_completed")
    assert failure_event["error_type"] == "RuntimeError"
    assert failure_event["route"] == "/boom"
    assert failure_event["request_id"] == completed_event["request_id"]
    assert response.headers["x-request-id"] == completed_event["request_id"]
