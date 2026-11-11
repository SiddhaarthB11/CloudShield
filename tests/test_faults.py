import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudshield.candidate import write_json
from cloudshield.faults import ANONYMOUS, HEALTH, STARTUP_MARKER, run_runtime


class FaultTests(unittest.TestCase):
    def test_startup_handoff_requires_evidence_of_the_planted_exit(self):
        target = {"project": "isolated", "manifest": "target.json"}
        backend = {"service": "backend", "state": "exited", "exit_code": 23, "logs": [STARTUP_MARKER]}
        with patch("cloudshield.faults.run_candidate", side_effect=ValueError("Compose could not start backend")), \
             patch("cloudshield.faults.capture_lab_evidence") as evidence:
            evidence.return_value = {"containers": [{**backend, "logs": []}]}
            with self.assertRaisesRegex(ValueError, "could not start"):
                run_runtime("startup", target, Path("."), 18008, None, None)
            evidence.return_value = {"containers": [backend]}
            result = run_runtime("startup", target, Path("."), 18008, None, None)
        self.assertEqual(result["outcome"], "handoff")
        self.assertEqual(result["observed"], ["compose/backend-exit"])

    def test_regression_case_records_the_fixed_and_broken_checks_separately(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            write_json(root / "attempt-1-verdict.json", {"resolved_targets": [HEALTH]})
            result = {"status": "rejected", "report": str(root / "repair.json"),
                      "attempts": [{"number": 1, "stage": "verify", "outcome": "rejected",
                                    "regressions": [ANONYMOUS]}]}
            with patch("cloudshield.faults.run_candidate"), patch("cloudshield.faults.repair", return_value=result):
                runtime = run_runtime("regression", {"project": "isolated", "manifest": "target.json"},
                                      root, 18008, None, None)
            self.assertEqual(runtime["outcome"], "rejected")
            self.assertEqual(runtime["resolved"], [HEALTH])
            self.assertEqual(runtime["regressions"], [ANONYMOUS])
