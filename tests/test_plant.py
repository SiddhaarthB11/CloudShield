import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloudshield.checks import read_object
from cloudshield.faults import CATALOG, CODE_FAULTS
from cloudshield.plant import draw, plant


class PlantTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        previous = Path.cwd()
        os.chdir(scratch.name)
        self.addCleanup(os.chdir, previous)

    def test_seed_replays_five_distinct_cases(self):
        selected = draw(3)
        self.assertEqual(draw(3), selected)
        self.assertNotEqual(draw(5), selected)
        self.assertEqual(len(set(selected)), 5)
        self.assertNotIn("app-logic", selected)
        self.assertTrue(set(selected) <= CATALOG.keys())

    def test_batch_records_expected_outcomes_and_routes_ai_only_to_code(self):
        proposer, reviewer = Mock(), Mock()

        def run(name, repo, port, repair_port, **options):
            expected = CATALOG[name]
            outcome = {"dependency": "recovered", "config-drift": "recovered", "syntax": "verified"}.get(
                name, expected["outcome"])
            return {"status": "matched", "outcome": outcome, "observed": [expected["check"]],
                    "report": f"{name}/fault.json"}

        with patch("cloudshield.plant.run_fault", side_effect=run) as fault:
            result = plant("target", Mock(), seed=3, proposer=proposer, reviewer=reviewer)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["counts"], {"handoff": 1, "verified": 2, "recovered": 2})
        self.assertEqual([call.args[0] for call in fault.call_args_list], draw(3))
        for call in fault.call_args_list:
            expected = {"heal": True}
            if call.args[0] in {*CODE_FAULTS, "syntax"}:
                expected.update(proposer=proposer, reviewer=reviewer)
            self.assertEqual(call.kwargs, expected)
        self.assertEqual([row["scenario"] for row in result["scenarios"] if row.get("initially_blocked_checks")],
                         ["startup", "syntax"])
        self.assertEqual(read_object(result["report"]), result)

    def test_cleanup_failure_blocks_later_cases(self):
        with patch("cloudshield.plant.run_fault", return_value={"status": "inconclusive",
                                                                "repair_cleanup": "failed"}) as fault:
            result = plant("target", Mock(), seed=3)
        fault.assert_called_once()
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["counts"], {"inconclusive": 1, "not_run": 4})
