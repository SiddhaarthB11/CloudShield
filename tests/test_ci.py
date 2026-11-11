import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloudshield.ci import run_ci
from cloudshield.checks import check


class CITests(unittest.TestCase):
    def test_different_checkout_blocks_build(self):
        with patch("cloudshield.ci.source_revision", return_value="b" * 40), \
             patch("cloudshield.ci.create_candidate_worktree") as worktree:
            with self.assertRaisesRegex(ValueError, "exact commit"):
                run_ci(".", "a" * 40)
        worktree.assert_not_called()

    def test_push_repair_receives_the_exact_baseline_and_pr_only_runs_judge(self):
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as scratch:
            previous = Path.cwd()
            os.chdir(scratch)
            try:
                for enable_repair in (False, True):
                    with self.subTest(enable_repair=enable_repair), \
                         patch("cloudshield.ci.source_revision", return_value=revision), \
                         patch("cloudshield.ci.create_candidate_worktree", return_value=Path("sandbox")) as worktree, \
                         patch("cloudshield.ci.check_python", return_value=[check("syntax/app.py", "pass", "valid")]), \
                         patch("cloudshield.ci.candidate_deployment", return_value={
                             "project": "snapshot", "manifest": "snapshot.json",
                         }) as deployment, \
                         patch("cloudshield.ci.run_candidate") as docker, \
                         patch("cloudshield.ci.judge_report", return_value={"overall": "pass"}) as judge, \
                         patch("cloudshield.ci.repair", return_value={
                             "status": "verified", "report": "repair.json", "attempts": [],
                         }) as repair:
                        allowed = ["backend/app/api/routes/utils.py"]
                        proposer, reviewer = Mock(), Mock()
                        options = {"allowed": allowed, "proposer": proposer, "reviewer": reviewer} if enable_repair else {}
                        result = run_ci(".", revision, **options)
                        self.assertEqual(result["event_revision"], revision)
                        self.assertEqual(worktree.call_args.args[1], revision)
                        self.assertEqual(deployment.call_args.args[-3:], (revision, revision, 18004))
                        self.assertEqual([call.args for call in docker.call_args_list],
                                         [("snapshot", "start"), ("snapshot", "discard")])
                        if enable_repair:
                            repair.assert_called_once_with("snapshot.json", None, allowed, 18005, max_attempts=3,
                                                           proposer=proposer, reviewer=reviewer)
                            judge.assert_not_called()
                            self.assertEqual(result["status"], "verified")
                        else:
                            judge.assert_called_once_with("snapshot.json")
                            repair.assert_not_called()
                            self.assertEqual(result["status"], "pass")
            finally:
                os.chdir(previous)
