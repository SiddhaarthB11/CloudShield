import unittest
from unittest.mock import patch

from cloudshield.reviewer import DEFAULT_REVIEW_MODEL, review_gemini
from test_verifier import HEALTH, report


class ReviewerTests(unittest.TestCase):
    def test_sends_failed_checks_and_exact_diff(self):
        baseline = report("c", {HEALTH: "fail"})
        diff = b"-    return False\n+    return True\n"
        reply = {"decision": "approve", "reason": "Restores health response"}
        with patch("cloudshield.reviewer.generate_json", return_value=reply) as generate:
            review = review_gemini(baseline, diff, model=DEFAULT_REVIEW_MODEL)
        model, context, _, _ = generate.call_args.args
        self.assertEqual(model, DEFAULT_REVIEW_MODEL)
        self.assertEqual(set(context), {"failed_checks", "candidate_diff"})
        self.assertEqual([check["check"] for check in context["failed_checks"]], [HEALTH])
        self.assertEqual(context["candidate_diff"], diff.decode("utf-8"))
        self.assertEqual(review, {"model": DEFAULT_REVIEW_MODEL, **reply})

    def test_missing_or_ambiguous_decision_fails(self):
        baseline = report("c", {HEALTH: "fail"})
        for reply in ({"decision": "maybe", "reason": "Unsure"}, {"decision": "approve", "reason": " "}):
            with self.subTest(reply=reply), patch("cloudshield.reviewer.generate_json", return_value=reply):
                with self.assertRaises(ValueError):
                    review_gemini(baseline, b"diff", model=DEFAULT_REVIEW_MODEL)
