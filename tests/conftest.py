from __future__ import annotations

import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
# The bundle root (runtime, main) and the reference generators (build_contracts,
# op_specs) are imported by the tests; setting the path here keeps the test
# modules' imports at the top of the file.
for _path in (BUNDLE_ROOT, BUNDLE_ROOT / "tests" / "reference"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
