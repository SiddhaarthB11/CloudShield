import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloudshield.controller import repair
from test_verifier import AUTH, HEALTH, report


class ControllerTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.manifest = self.root / "target.json"
        self.manifest.write_text(json.dumps({
            "base_url": "http://127.0.0.1:18000", "compose_file": "compose.yml",
            "credentials_file": "lab.env", "compose_project": "lab", "source_dir": "backend/app",
        }), encoding="utf-8")
        self.first = self.root / "first.patch"
        self.first.write_bytes(b"first candidate")
        self.second = self.root / "second.patch"
        self.second.write_bytes(b"second candidate")

    def test_retries_distinct_patch_and_keeps_verdicts_and_cleanup(self):
        baseline = report("c", {HEALTH: "fail"})
        bad, good = report("d", {AUTH: "fail"}), report("e", {})
        first = {"project": "first", "candidate_revision": "d" * 40, "manifest": "first.json"}
        second = {"project": "second", "candidate_revision": "e" * 40, "manifest": "second.json"}
        records = [{"status": "started", "baseline_revision": "c" * 40,
                    "candidate_revision": judge["deployment"]["source_revision"], "deployment": judge["deployment"]}
                   for judge in (bad, good)]
        with patch("cloudshield.controller.judge_report", side_effect=[baseline, bad, good]), \
             patch("cloudshield.controller.baseline_evidence", return_value={"containers": []}), \
             patch("cloudshield.controller.prepare_candidate", side_effect=[first, second]) as prepare, \
             patch("cloudshield.controller.run_candidate", side_effect=[records[0], {}, records[1], {}]) as run:
            result = repair(self.manifest, [self.first, self.first, self.second], ["backend/app/main.py"])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["duplicate_patches_skipped"], 1)
        self.assertEqual([attempt["outcome"] for attempt in result["attempts"]], ["rejected", "verified"])
        self.assertEqual([call.args for call in run.call_args_list], [
            ("first", "start"), ("first", "discard"), ("second", "start"), ("second", "discard"),
        ])
        self.assertTrue(all(call.kwargs["expected_revision"] == "c" * 40 for call in prepare.call_args_list))
        persisted = Path(result["report"])
        self.assertEqual(json.loads(persisted.read_text()), result)
        self.assertEqual(json.loads((persisted.parent / "attempt-1-verdict.json").read_text())["regressions"], [AUTH])

    def test_clean_baseline_creates_no_candidate(self):
        with patch("cloudshield.controller.judge_report", return_value=report("c", {})), \
             patch("cloudshield.controller.prepare_candidate") as prepare:
            result = repair(self.manifest, [self.first], ["backend/app/main.py"])
        self.assertEqual(result["status"], "no_repair_needed")
        prepare.assert_not_called()

    def test_monitor_repair_stops_if_the_deployment_changed(self):
        with patch("cloudshield.controller.judge_report", return_value=report("d", {HEALTH: "fail"})), \
             patch("cloudshield.controller.prepare_candidate") as prepare:
            result = repair(self.manifest, [self.first], ["backend/app/main.py"],
                            expected_deployment=report("c", {HEALTH: "fail"})["deployment"])
        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("deployment changed", result["detail"])
        prepare.assert_not_called()

    def test_build_failure_attempts_cleanup_and_keeps_failure_stage(self):
        candidate = {"project": "first", "candidate_revision": "d" * 40, "manifest": "first.json"}
        with patch("cloudshield.controller.judge_report", return_value=report("c", {HEALTH: "fail"})), \
             patch("cloudshield.controller.baseline_evidence", return_value={}), \
             patch("cloudshield.controller.prepare_candidate", return_value=candidate) as prepare, \
             patch("cloudshield.controller.run_candidate", side_effect=[ValueError("build failed"), {}]) as run:
            result = repair(self.manifest, [self.first, self.second], ["backend/app/main.py"], max_attempts=1)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["attempts"][0]["stage"], "start")
        self.assertEqual(result["attempts"][0]["detail"], "build failed")
        self.assertEqual(result["attempts"][0]["cleanup"], "discarded")
        run.assert_called_with("first", "discard")
        self.assertEqual(prepare.call_count, 1)

    def test_failed_cleanup_stops_before_trying_another_patch(self):
        candidate = {"project": "first", "candidate_revision": "d" * 40, "manifest": "first.json"}
        with patch("cloudshield.controller.judge_report", return_value=report("c", {HEALTH: "fail"})), \
             patch("cloudshield.controller.baseline_evidence", return_value={}), \
             patch("cloudshield.controller.prepare_candidate", return_value=candidate) as prepare, \
             patch("cloudshield.controller.run_candidate", side_effect=[ValueError("build failed"), ValueError("Docker offline")]):
            result = repair(self.manifest, [self.first, self.second], ["backend/app/main.py"])
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["attempts"][0]["cleanup"], "failed")
        self.assertIn("candidate discard", result["detail"])
        self.assertEqual(prepare.call_count, 1)

    def test_model_generated_patch_uses_the_existing_candidate_and_verifier(self):
        baseline, good = report("c", {HEALTH: "fail"}), report("d", {})
        candidate = {"project": "first", "candidate_revision": "d" * 40, "manifest": "first.json"}
        deployment = {"status": "started", "baseline_revision": "c" * 40, "candidate_revision": "d" * 40,
                      "deployment": good["deployment"]}
        def proposer(source_dir, judged, evidence, paths, attempts):
            self.assertEqual(source_dir, self.root / "backend" / "app")
            self.assertEqual(judged, baseline)
            return b"generated patch", "Restore health"

        with patch("cloudshield.controller.judge_report", side_effect=[baseline, good]), \
             patch("cloudshield.controller.baseline_evidence", return_value={}), \
             patch("cloudshield.controller.prepare_candidate", return_value=candidate) as prepare, \
             patch("cloudshield.controller.run_candidate", side_effect=[deployment, {}]):
            result = repair(self.manifest, None, ["backend/app/main.py"], proposer=proposer)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["patch_source"], "gemini")
        self.assertEqual(prepare.call_args.args[1].read_bytes(), b"generated patch")

    def test_model_handoff_does_not_create_a_candidate(self):
        with patch("cloudshield.controller.judge_report", return_value=report("c", {HEALTH: "fail"})), \
             patch("cloudshield.controller.baseline_evidence", return_value={}), \
             patch("cloudshield.controller.prepare_candidate") as prepare:
            result = repair(self.manifest, None, ["backend/app/main.py"],
                            proposer=lambda *args: (None, "Needs an operator"))
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["fixer_proposals"], [{"summary": "Needs an operator", "status": "no_patch"}])
        prepare.assert_not_called()

    def test_model_retains_verified_progress_and_tests_cumulative_patch(self):
        baseline = report("c", {HEALTH: "fail", AUTH: "fail"})
        partial, final = report("d", {AUTH: "fail"}), report("e", {})
        partial_manifest = self.root / "partial.json"
        partial_manifest.write_text('{"source_dir":"partial-source"}', encoding="utf-8")
        first = {"project": "first", "candidate_revision": "d" * 40, "manifest": str(partial_manifest)}
        staged = {"worktree": "staged", "candidate_revision": "f" * 40}
        second = {"project": "second", "candidate_revision": "e" * 40, "manifest": "final.json"}
        deployments = [{"status": "started", "baseline_revision": "c" * 40,
                        "candidate_revision": candidate["deployment"]["source_revision"],
                        "deployment": candidate["deployment"]} for candidate in (partial, final)]
        proposer = Mock(side_effect=[(b"first fix", "Fix health"), (b"second fix", "Fix authorization")])
        with patch("cloudshield.controller.judge_report", side_effect=[baseline, partial, final]), \
             patch("cloudshield.controller.baseline_evidence", return_value={}), \
             patch("cloudshield.controller.prepare_candidate", side_effect=[first, staged, second]) as prepare, \
             patch("cloudshield.controller.candidate_diff", return_value=b"combined fix"), \
             patch("cloudshield.controller.run_candidate", side_effect=[deployments[0], {}, deployments[1], {}]):
            result = repair(self.manifest, None, ["backend/app/main.py"], proposer=proposer)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["attempts"][0]["retained_progress"])
        self.assertEqual(proposer.call_args.args[1], partial)
        self.assertEqual(prepare.call_args.args[1].read_bytes(), b"combined fix")

    def test_review_blocks_docker_without_approval(self):
        candidate = {"project": "first", "worktree": "candidate", "baseline_revision": "c" * 40,
                     "candidate_revision": "d" * 40}
        for reviewer, outcome in (
            (Mock(return_value={"decision": "reject", "reason": "Weakens authorization"}), "rejected"),
            (Mock(side_effect=ValueError("Reviewer unavailable")), "inconclusive"),
        ):
            with self.subTest(outcome=outcome), \
                 patch("cloudshield.controller.judge_report", return_value=report("c", {HEALTH: "fail"})) as judge, \
                 patch("cloudshield.controller.baseline_evidence", return_value={}), \
                 patch("cloudshield.controller.prepare_candidate", return_value=candidate), \
                 patch("cloudshield.controller.candidate_diff", return_value=b"candidate diff"), \
                 patch("cloudshield.controller.run_candidate") as run:
                result = repair(self.manifest, [self.first], ["backend/app/main.py"], reviewer=reviewer)
                self.assertEqual(result["status"], outcome)
                self.assertEqual(result["attempts"][0]["stage"], "review")
                self.assertEqual(result["attempts"][0]["cleanup"], "not_started")
                self.assertEqual(judge.call_count, 1)
                run.assert_not_called()

    def test_review_approval_is_audited_before_build_and_verifier_can_reject(self):
        baseline, bad = report("c", {HEALTH: "fail"}), report("d", {AUTH: "fail"})
        candidate = {"project": "first", "worktree": "candidate", "baseline_revision": "c" * 40,
                     "candidate_revision": "d" * 40, "manifest": "first.json"}
        deployment = {"status": "started", "baseline_revision": "c" * 40, "candidate_revision": "d" * 40,
                      "deployment": bad["deployment"]}
        reviewer = Mock(return_value={"decision": "approve", "reason": "Plausible repair"})

        def run_candidate(project, action):
            if action == "start":
                run_dir, = (self.root / "runs" / "repairs").iterdir()
                review = json.loads((run_dir / "attempt-1-review.json").read_text())
                self.assertEqual(review["candidate_revision"], "d" * 40)
                self.assertEqual((run_dir / "attempt-1-reviewed.patch").read_bytes(), b"candidate diff")
                return deployment

        with patch("cloudshield.controller.judge_report", side_effect=[baseline, bad]), \
             patch("cloudshield.controller.baseline_evidence", return_value={}), \
             patch("cloudshield.controller.prepare_candidate", return_value=candidate), \
             patch("cloudshield.controller.candidate_diff", return_value=b"candidate diff"), \
             patch("cloudshield.controller.run_candidate", side_effect=run_candidate):
            result = repair(self.manifest, [self.first], ["backend/app/main.py"], reviewer=reviewer)
        reviewer.assert_called_once_with(baseline, b"candidate diff")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["attempts"][0]["regressions"], [AUTH])
        self.assertEqual(result["attempts"][0]["cleanup"], "discarded")
