"""Failure types determine repair budgets independently of graph try placement."""

import unittest
from unittest.mock import Mock

from enterprise_agent.domain.errors import (
    PermissionDenied,
    RepairableError,
    UpstreamPending,
    VersionConflict,
    public_message,
)
from enterprise_agent.stages.intent.retry import recognize
from enterprise_agent.stages.intent.schema import Intent
from enterprise_agent.stages.verify.retry import verify


class RetryPolicyTests(unittest.TestCase):
    def proposal(self):
        return Intent(
            operations=[
                {
                    "request_id": "new_1",
                    "action": "none",
                    "category": "general_qa",
                    "instruction": "解释平均值",
                    "refs": [],
                }
            ]
        )

    def test_model_call_failure_has_one_repair_attempt(self):
        value = self.proposal()
        call = Mock(
            side_effect=[
                RepairableError("invalid JSON", code="model.invalid_output"),
                value,
            ]
        )
        proposal, ordered = recognize(call, lambda proposal: proposal.operations)
        self.assertIs(proposal, value)
        self.assertEqual(len(ordered), 1)
        self.assertEqual(call.call_count, 2)
        feedback = call.call_args.args[0]
        self.assertEqual(feedback["code"], "model.invalid_output")
        self.assertIsNone(feedback["rejected_intent"])

    def test_normalization_failure_keeps_rejected_intent(self):
        call = Mock(side_effect=[self.proposal(), self.proposal()])
        normalize = Mock(
            side_effect=[RepairableError("cycle", code="intent.cycle"), []]
        )
        recognize(call, normalize)
        self.assertEqual(
            call.call_args.args[0]["rejected_intent"]["operations"][0]["request_id"],
            "new_1",
        )

    def test_nonrepairable_errors_never_retry(self):
        for error in (
            PermissionDenied("private table"),
            VersionConflict("stale"),
            UpstreamPending("pending"),
            RuntimeError("network"),
            ValueError("unclassified"),
        ):
            with self.subTest(error=type(error).__name__):
                call = Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    recognize(call, Mock())
                self.assertEqual(call.call_count, 1)
        self.assertEqual(
            public_message(PermissionDenied("private table")), "无权访问所请求的资源"
        )

    def test_repair_budget_is_bounded(self):
        call = Mock(side_effect=RepairableError("bad", code="intent.invalid"))
        with self.assertRaises(RepairableError):
            recognize(call, Mock())
        self.assertEqual(call.call_count, 2)

    def test_verdict_repair_revalidates_the_new_evidence(self):
        call = Mock(side_effect=[self.proposal(), self.proposal()])
        validate = Mock(
            side_effect=[
                RepairableError("missing evidence", code="verify.evidence"),
                None,
            ]
        )
        verify(call, validate)
        self.assertEqual(validate.call_count, 2)
        self.assertIn("rejected_completion", call.call_args.args[0])
