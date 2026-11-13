"""Acceptance tests for request identity, revisions, task execution and provenance."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enterprise_agent.flow.application import Application
from tests.fixtures import FixtureModel
from tests.postgres_fixture import (
    LegacyTestStores,
    cleanup,
)
from tests.postgres_fixture import (
    TestStores as Stores,
)


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self._legacy_mode = self._testMethodName in {
            "test_conflicting_legacy_deliveries_roll_back_migration",
            "test_failed_migration_rolls_back_and_keeps_backup",
            "test_request_specs_and_legacy_deliveries_preserve_existing_ledger",
            "test_migration_preserves_history_artifacts_and_update",
            "test_unified_migration_recovers_historical_text_and_bindings",
            "test_unified_migration_failure_removes_new_content_and_can_retry",
        }
        if self._legacy_mode:
            replacement = patch(self.__class__.__module__ + ".Stores", LegacyTestStores)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.model = FixtureModel()
        factory = LegacyTestStores if self._legacy_mode else Stores
        self.engine = Application(factory(Path(self.directory.name)), self.model)

    def tearDown(self):
        self.engine.close()
        cleanup()
        self.directory.cleanup()

    def completed(self, question="列出前五个产品", session_id=None):
        result = self.engine.ask(1, question, session_id)
        self.assertEqual(result["status"], "completed", result["blocks"])
        return result
