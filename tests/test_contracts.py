"""The committed contracts are exactly what the generator builds; their rules hold."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE_ROOT / "tests" / "reference"))

import build_contracts  # noqa: E402

from runtime import catalog  # noqa: E402
from runtime.operations import _neutral_result  # noqa: E402


def _load(relative: str) -> Any:
    return yaml.safe_load((BUNDLE_ROOT / relative).read_text("utf-8"))


@pytest.fixture(scope="module")
def built() -> dict[str, Any]:
    return build_contracts.outputs()


def test_generated_files_match(built) -> None:
    assert _load("contracts/operation_manifest.yaml") == {"operations": built["operations"]}
    assert _load("contracts/step_ui_manifest.yaml") == {"forms": built["forms"]}
    assert _load("contracts/artifact_policies.yaml") == {
        "artifact_policies": built["artifact_policies"]
    }
    assert _load("ui/actions/actions.yaml") == {"actions": built["actions"]}
    assert _load("ui/pages/odoo-api.yaml") == built["api_page"]
    assert _load("ui/pages/changelog.yaml") == built["changelog_page"]
    assert _load("extension.yaml")["external_effects"] == built["effects"]


def test_operations_match_the_runtime_registry(built) -> None:
    ids = {op["operation_id"] for op in built["operations"]}
    assert ids == set(catalog.OPERATION_IDS)
    writes = {op["operation_id"] for op in built["operations"] if op["operation_kind"] == "action"}
    assert writes == set(catalog.WRITE_OPERATION_IDS)
    effects = {e["operation_id"]: e for e in _load("extension.yaml")["external_effects"]}
    assert set(effects) == writes
    for effect in effects.values():
        assert effect["idempotency"] == "required" and effect["test_mode_behavior"] == "suppress"


def test_neutral_test_mode_result_has_every_declared_output(built) -> None:
    for op in built["operations"]:
        if op["operation_kind"] == "action":
            result = _neutral_result(op["operation_id"])
            assert set(result) == {o["name"] for o in op["outputs"]}, op["operation_id"]
            assert result["external_effect_status"] == "failed"
            assert result["definitely_no_external_effect"] is True


_WIDGETS = {
    "artifact_handle": {"artifact_picker"},
    "object": {"json"},
    "array": {"json"},
    "json": {"json"},
    "boolean": {"boolean"},
    "integer": {"number"},
    "number": {"number"},
    "text": {"text", "textarea", "select"},
}


def test_every_input_has_a_matching_form_field(built) -> None:
    forms = {f["operation_id"]: f for f in built["forms"]}
    actions = {a["action_id"]: a for a in built["actions"]}
    for op in built["operations"]:
        oid = op["operation_id"]
        fields = {f["name"]: f for f in forms[oid]["fields"]}
        inputs = {i["name"]: i for i in op["inputs"] if i["name"] != "connection_ref"}
        assert set(fields) == set(inputs), oid
        for name, item in inputs.items():
            assert fields[name]["required"] == bool(item.get("required")), (oid, name)
            assert fields[name]["widget_type"] in _WIDGETS[item["value_type"]], (oid, name)
            if fields[name]["widget_type"] == "select":
                assert item["schema"].get("enum"), (oid, name)
                for option in fields[name]["options"]:
                    assert (
                        option["label"] != ""
                        and option["label"] != option["value"]
                        or name == "kind"
                    ), (oid, option)
        assert [p["name"] for p in actions[oid]["parameters"]] == [
            i["name"] for i in op["inputs"]
        ], oid
        assert [f"result.{n}" for n in actions[oid]["result_fields"]] == op["result_fields"], oid
        assert op["result_fields"] == [f"result.{o['name']}" for o in op["outputs"]], oid


def test_file_inputs_and_outputs_have_grants(built) -> None:
    from runtime import operations

    forms = {f["operation_id"]: f for f in built["forms"]}
    policies = {p["operation_id"]: p for p in built["artifact_policies"]}
    for op in built["operations"]:
        for item in op["inputs"]:
            if item["value_type"] == "artifact_handle":
                field = next(
                    f for f in forms[op["operation_id"]]["fields"] if f["name"] == item["name"]
                )
                assert field["widget_type"] == "artifact_picker"
                assert (
                    policies[op["operation_id"]]["inputs"][0]["binding_key"]
                    == catalog.UPLOAD_BINDING
                )
        if any(o["value_type"] == "artifact_handle" for o in op["outputs"]):
            assert policies[op["operation_id"]]["outputs"], op["operation_id"]
    assert (
        policies["download_attachment"]["outputs"][0]["content_type"]
        == operations.DOWNLOAD_CONTENT_TYPE
    )
    assert policies["download_attachment"]["outputs"][0]["binding_key"] == catalog.DOWNLOAD_BINDING
    assert (
        policies["export_records"]["outputs"][0]["content_type"] == operations.EXPORT_CONTENT_TYPE
    )
    assert policies["export_records"]["outputs"][0]["binding_key"] == catalog.EXPORT_BINDING


def test_help_texts_are_sentences(built) -> None:
    for form in built["forms"]:
        for field in form["fields"]:
            assert len(field.get("help_text", "").split()) >= 4, (
                form["operation_id"],
                field["name"],
            )


def test_every_shipped_page_is_in_the_ui_manifest() -> None:
    refs = {page["ref"] for page in _load("ui/ui_manifest.yaml")["pages"]}
    assert refs == {f"pages/{p.name}" for p in (BUNDLE_ROOT / "ui" / "pages").glob("*.yaml")}


def test_no_page_uses_a_table_a_bare_url_or_effect_plumbing() -> None:
    for path in (BUNDLE_ROOT / "ui" / "pages").glob("*.yaml"):
        text = path.read_text("utf-8")
        assert "external_effect_status" not in text and "definitely_no_external_effect" not in text
        for component in yaml.safe_load(text)["components"]:
            for line in str(component.get("body", "")).splitlines():
                assert not line.lstrip().startswith("|"), f"{path.name}: {line}"
                assert not any(w.startswith(("http://", "https://")) for w in line.split()), (
                    f"{path.name}: {line}"
                )


def test_api_page_documents_every_operation(built) -> None:
    bodies = "\n".join(c["body"] for c in built["api_page"]["components"])
    for op in built["operations"]:
        assert f"`{op['operation_id']}`" in bodies, op["operation_id"]
    assert built["api_page"]["components"][-1]["title"] == "Coverage"
    assert "pricelist price" in built["api_page"]["components"][-1]["body"]


def test_manifest_basics() -> None:
    manifest = _load("extension.yaml")
    assert manifest["extension_id"] == "flowsteward.odoo" and manifest["version"] == "0.1.0"
    assert manifest["egress"] == {"connection_fields": ["odoo.base_url"]}
    categories = json.loads((BUNDLE_ROOT / "tests/reference/categories.json").read_text("utf-8"))
    assert manifest["listing"]["category"] == "finance"
    assert set(manifest["listing"]["categories"]) <= set(categories)
    assert manifest["entrypoint"]["timeout_seconds"] == 600


def test_health_and_icon() -> None:
    import health

    assert health.check_health()["ok"] is True
    icon = (BUNDLE_ROOT / "icon.png").read_bytes()
    assert icon.startswith(b"\x89PNG\r\n\x1a\n") and len(icon) <= 100 * 1024
