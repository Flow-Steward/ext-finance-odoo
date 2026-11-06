#!/usr/bin/env python3
"""Offline smoke check: health, a suppressed write and a refused operation, no network."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = {"group_id": None, "for_each_id": None, "step_id": None, "step_execution_id": None}


def _run(args: list[str], payload: dict | None = None) -> dict:
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = proc.stdout.strip()
    if not output:
        raise RuntimeError(f"{args} printed nothing: {proc.stderr}")
    return json.loads(output)


def _action(operation_id: str, operation_input: dict, *, test_mode: bool) -> dict:
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {**SCOPE, "test_mode": test_mode},
        "action": {
            "action_id": operation_id,
            "input": {"connection_ref": "smoke", **operation_input},
            "target": {
                "connection": {
                    "connection_id": "smoke",
                    "connection_type_id": "odoo",
                    "config": {"base_url": "https://smoke.example.com", "api": "json2"},
                    "secrets": {"api_key": "smoke-not-a-real-key"},
                }
            },
        },
    }


class _FakeResponse:
    status = 200
    headers: dict = {}

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, size: int = -1) -> bytes:
        chunk, self._body = (
            self._body[: max(size, 0) or None],
            self._body[max(size, 0) or len(self._body) :],
        )
        return chunk

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _representative_read() -> None:
    """A successful res.partner search through the real runtime, with the network mocked."""
    sys.path.insert(0, str(ROOT))
    from runtime.operations import handle_runtime
    from runtime.transport import OdooTransport

    answers = {
        "/json/2/res.partner/fields_get": {"name": {"type": "char"}, "ref": {"type": "char"}},
        "/json/2/res.partner/search_read": [{"id": 7, "name": "UAB Smoke"}],
    }

    def opener(request, *, timeout_seconds, purpose):
        path = request.full_url.removeprefix("https://smoke.example.com")
        if path not in answers:
            raise RuntimeError(request.full_url)
        return _FakeResponse(json.dumps(answers[path]).encode())

    response = handle_runtime(
        _action(
            "search_records",
            {"model": "res.partner", "domain": [["ref", "=", "C1"]], "fields": ["name"]},
            test_mode=False,
        ),
        transport_factory=lambda connection: OdooTransport(
            connection, opener=opener, sleeper=lambda _s: None
        ),
    )
    record = (response.get("result") or {}).get("record") or {}
    if not response.get("ok") or record.get("name") != "UAB Smoke":
        raise RuntimeError(f"representative read failed: {response}")


def main() -> int:
    health = _run(["health.py"])
    if health.get("status") != "healthy" or health.get("odoo_versions") != [16, 17, 18, 19, 20]:
        raise RuntimeError(f"health failed: {health}")
    suppressed = _run(
        ["main.py"],
        _action(
            "create_record", {"model": "res.partner", "values": {"name": "Smoke"}}, test_mode=True
        ),
    )
    if not suppressed.get("ok") or suppressed.get("definitely_no_external_effect") is not True:
        raise RuntimeError(f"test-mode write was not suppressed: {suppressed}")
    refused = _run(["main.py"], _action("not_an_operation", {}, test_mode=False))
    if refused.get("error_code") != "unsupported_operation":
        raise RuntimeError(f"unknown operation was not refused: {refused}")
    _representative_read()
    print(json.dumps({"ok": True, "extension_id": "flowsteward.odoo"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
