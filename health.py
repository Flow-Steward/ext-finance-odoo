"""Health command for the Odoo extension (no network)."""

from __future__ import annotations

import json
from importlib.util import find_spec


def check_health() -> dict[str, object]:
    from runtime.catalog import OPERATION_IDS, WRITE_OPERATION_IDS
    from runtime.versions import SUPPORTED

    # The XML-RPC parser (defusedxml) is a locked bundled dependency. The host
    # puts it on the path of the installed runtime only; `extensions validate`
    # runs this file from the source tree without it. So it is reported, not
    # required: requiring it here would fail every source validation.
    parser = "defusedxml" if find_spec("defusedxml") is not None else "not on path"
    return {
        "extension_id": "flowsteward.odoo",
        "ok": True,
        "status": "healthy",
        "odoo_versions": list(SUPPORTED),
        "apis": ["json2", "xmlrpc"],
        "xmlrpc_parser": parser,
        "declared_operations": len(OPERATION_IDS),
        "write_operations": len(WRITE_OPERATION_IDS),
    }


if __name__ == "__main__":
    print(json.dumps(check_health(), separators=(",", ":"), sort_keys=True))
