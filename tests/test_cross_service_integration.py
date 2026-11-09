"""
Automated pytest wrapper for the cross-service integration check.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_integration_check import run_integration_check  # noqa: E402


def test_cross_service_integration_e2e():
    exit_code = run_integration_check()
    assert exit_code == 0
