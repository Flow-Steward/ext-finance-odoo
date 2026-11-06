"""Health command for the Odoo extension (no network)."""

from __future__ import annotations

import json


def check_health() -> dict[str, object]:
    # The XML-RPC transport needs defusedxml; a missing dependency is unhealthy.
    import defusedxml.xmlrpc  # noqa: F401

    from runtime.catalog import OPERATION_IDS, WRITE_OPERATION_IDS
    from runtime.versions import SUPPORTED

    return {
        "extension_id": "flowsteward.odoo",
        "ok": True,
        "status": "healthy",
        "odoo_versions": list(SUPPORTED),
        "apis": ["json2", "xmlrpc"],
        "declared_operations": len(OPERATION_IDS),
        "write_operations": len(WRITE_OPERATION_IDS),
    }


if __name__ == "__main__":
    print(json.dumps(check_health(), separators=(",", ":"), sort_keys=True))
