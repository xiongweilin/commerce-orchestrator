"""Importing the service layer must never pull in the DBOS runtime."""

from __future__ import annotations

import sys


def test_services_do_not_import_dbos() -> None:
    assert "dbos" not in sys.modules

    import app.services
    import app.services.approvals
    import app.services.commands
    import app.services.effect_ledger
    import app.services.outbox_inbox
    import app.services.reconciliation
    import app.services.state_machines
    import app.services.webhooks
    import app.services.workflows  # noqa: F401 - explicit import side effect

    assert "dbos" not in sys.modules
