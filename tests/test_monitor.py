import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloudshield.candidate import write_json
from cloudshield.monitor import observe
from test_verifier import DATABASE, HEALTH, report


class MonitorTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.target = ("compose.yml", "lab.env", "cloudshield-test")
        self.options = {"proposer": Mock(), "reviewer": Mock(), "allowed": ["backend/app/main.py"], "port": 18006}

    def evidence(self, db_state="running"):
        return {"containers": [
            {"service": "db", "state": db_state, "health": "healthy" if db_state == "running" else None},
            {"service": "backend", "state": "running", "health": "healthy", **report("c", {})["deployment"]},
        ]}

    def test_database_outage_is_deduplicated_until_recovery(self):
        outage = report("c", {DATABASE: "fail"})
        with patch("cloudshield.monitor.collect_evidence",
                   side_effect=[self.evidence("exited"), self.evidence("exited"), self.evidence("exited"),
                                self.evidence(), self.evidence("exited"), self.evidence("exited")]) as collect, \
             patch("cloudshield.monitor.judge_report", side_effect=[outage, outage, report("c", {}), outage]), \
             patch("cloudshield.monitor.repair") as repair:
            first, repeat, recovery, new = [
                observe("target.json", self.target, self.root, **self.options) for _ in range(4)
            ]
        self.assertEqual([first["status"], repeat["status"], recovery["status"], new["status"]],
                         ["handoff", "duplicate", "healthy", "handoff"])
        self.assertEqual(repeat["duplicate_of"], first["report"])
        self.assertEqual(first["deployed_revision"], recovery["deployed_revision"])
        self.assertEqual(sum(call.kwargs.get("log_lines") == 10 for call in collect.call_args_list), 2)
        repair.assert_not_called()

    def test_supported_failure_starts_one_reviewed_attempt_on_observed_deployment(self):
        failure = report("c", {HEALTH: "fail"})
        with patch("cloudshield.monitor.collect_evidence", return_value=self.evidence()), \
             patch("cloudshield.monitor.judge_report", return_value=failure), \
             patch("cloudshield.monitor.repair", return_value={"status": "verified", "report": "repair.json",
                                                               "attempts": []}) as repair:
            first = observe("target.json", self.target, self.root, **self.options)
            second = observe("target.json", self.target, self.root, **self.options)
        self.assertEqual((first["status"], second["status"]), ("repair_available", "duplicate"))
        self.assertEqual(second["duplicate_of"], first["report"])
        repair.assert_called_once_with("target.json", None, self.options["allowed"], 18006, max_attempts=3,
                                       proposer=self.options["proposer"], reviewer=self.options["reviewer"],
                                       expected_deployment=failure["deployment"], judge=None)

    def test_recovers_each_new_outage_after_post_start_checks_pass(self):
        manifest = self.root / "target.json"
        write_json(manifest, {"source_dir": "source"})
        outage = report("c", {DATABASE: "fail"})
        stopped = self.evidence("exited")
        stopped["containers"][0]["container_id"] = "a" * 64
        with patch("cloudshield.monitor.collect_evidence", side_effect=[stopped, stopped, self.evidence()] * 2), \
             patch("cloudshield.monitor.judge_report", side_effect=[outage, report("c", {})] * 2) as judge, \
             patch("cloudshield.monitor.source_revision", return_value="c" * 40), \
             patch("cloudshield.monitor.start_existing_services") as start, \
             patch("cloudshield.monitor.repair") as repair:
            results = [observe(manifest, self.target, self.root, recover_services=["db"], **self.options)
                       for _ in range(2)]
        self.assertEqual([result["status"] for result in results], ["recovered", "recovered"])
        self.assertEqual(judge.call_count, 4)
        self.assertEqual(start.call_count, 2)
        self.assertTrue(all(call.args[1] == [{"service": "db", "container_id": "a" * 64}]
                            for call in start.call_args_list))
        repair.assert_not_called()
