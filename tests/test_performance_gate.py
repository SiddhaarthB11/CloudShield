import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudshield.candidate import write_json
from cloudshield.fixer import failed_check_context
from cloudshield.performance_gate import performance_judge
from cloudshield.verifier import verify
from test_verifier import AUTH, report


CHECK = "performance/normal"


class PerformanceGateTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        previous = Path.cwd()
        os.chdir(scratch.name)
        self.addCleanup(os.chdir, previous)
        self.root = Path.cwd()
        self.manifest = self.root / "target.json"
        (self.root / "compose.yml").write_text("services: {}", encoding="utf-8")
        self.jar = self.root / "ApacheJMeter.jar"
        self.jar.touch()
        write_json(self.manifest, {"base_url": "http://127.0.0.1:18010", "compose_file": "compose.yml",
                                   "credentials_file": "lab.env", "compose_project": "lab", "source_dir": "app"})

    def judge(self, mark, p95s, failures=None, runtime="fastapi worker settings"):
        judged = report(mark, failures or {})
        metrics = [{"p95_ms": p95, "errors": 0, "samples": 100, "error_pct": 0,
                    "successful_requests_per_second": 7} for p95 in p95s]
        with patch("cloudshield.performance_gate.judge_report", return_value=judged), \
             patch("cloudshield.performance_gate.docker_output", return_value=(runtime, "")), \
             patch("cloudshield.performance_gate.source_revision", return_value=mark * 40), \
             patch("cloudshield.performance_gate.verified_backend", return_value=judged["deployment"]), \
             patch("cloudshield.performance_gate.TargetChecks"), \
             patch("cloudshield.performance_gate.run_jmeter", side_effect=metrics) as jmeter:
            result = performance_judge(self.manifest, name="normal", java="java", jar=self.jar)
        self.assertEqual(jmeter.call_count, 2)
        return result

    def test_confirmed_latency_breach_reaches_fixer_but_mixed_trials_do_not(self):
        confirmed = self.judge("c", [705, 715])
        self.assertEqual(confirmed["overall"], "fail")
        failure, = failed_check_context(confirmed)
        self.assertEqual(failure["check"], CHECK)
        self.assertEqual([row["p95_ms"] for row in failure["observations"]], [705, 715])
        self.assertIsNotNone(failure["expected"])
        mixed = self.judge("c", [705, 125])
        self.assertEqual(mixed["overall"], "inconclusive")
        self.assertFalse(mixed["performance"]["repair_eligible"])

    def test_verifier_requires_jmeter_and_robot_on_comparable_candidate(self):
        baseline = self.judge("c", [705, 715])
        for result, expected in (
            (self.judge("d", [125, 135]), "verified"),
            (self.judge("d", [125, 135], {AUTH: "fail"}), "rejected"),
            (self.judge("d", [125, 135], runtime="changed worker settings"), "inconclusive"),
            (report("d", {}), "inconclusive"),
        ):
            with self.subTest(expected=expected):
                deployment = {"status": "started", "baseline_revision": "c" * 40,
                              "candidate_revision": "d" * 40, "deployment": result["deployment"]}
                self.assertEqual(verify(baseline, result, deployment)["verdict"], expected)
