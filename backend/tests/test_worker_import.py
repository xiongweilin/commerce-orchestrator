"""Worker entrypoints import without side effects (no DBOS launch)."""

from __future__ import annotations

import os
import sys

TEST_DB_URL = os.environ["COMMERCE_DATABASE_URL"]


def test_import_app_worker_and_configure_dbos_without_launch() -> None:
    assert "dbos" not in sys.modules

    import app.worker  # noqa: F401
    from app.workflows.bootstrap import configure_dbos  # noqa: F401

    cfg = configure_dbos()
    assert cfg["config"]["name"] == "commerce-orchestrator"
    assert cfg["config"]["application_database_url"] == TEST_DB_URL
    # Importing the worker must not start the DBOS runtime.
    assert "dbos" not in sys.modules


def test_importing_services_again_keeps_dbos_out() -> None:
    import app.services  # noqa: F401
    from app.services import commands  # noqa: F401

    assert "dbos" not in sys.modules
