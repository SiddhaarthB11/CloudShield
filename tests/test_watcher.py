import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloudshield.candidate import git, write_json
from cloudshield.watcher import capture, check_snapshot, watch


class WatcherTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.repo = self.root / "target"
        self.app = self.repo / "backend" / "app"
        self.app.mkdir(parents=True)
        self.code = self.app / "main.py"
        self.code.write_text("HEALTH = True\n", encoding="utf-8")
        (self.repo / "backend" / "Dockerfile").touch()
        git(self.repo, "init", "-q")
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=Lab", "-c", "user.email=lab@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", "Original")
        self.config = self.root / "config.json"
        write_json(self.config, {"DB_HOST": "db", "DB_PORT": 5432, "DB_NAME": "app", "DB_SECRET_REF": "lab"})
        self.compose = self.root / "compose.yml"
        self.suite = self.root / "target.robot"
        self.compose.touch()
        self.suite.touch()
        self.manifest = {"environment_config": "config.json", "source_dir": "target/backend/app",
                         "compose_file": "compose.yml", "robot_suite": "target.robot",
                         "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
                         "base_url": "http://127.0.0.1:18000"}
        write_json(self.root / "target.json", self.manifest)

    def test_captures_uncommitted_bytes_and_blocks_bad_syntax_before_docker(self):
        _, before, _, _ = capture(self.repo, self.config)
        self.code.write_text("HEALTH =\n", encoding="utf-8")
        _, after, _, _ = capture(self.repo, self.config)
        self.assertNotEqual(before, after)
        seen = Mock()
        with patch("cloudshield.watcher.run_candidate") as docker:
            result = watch(self.root / "target.json", seen, debounce=0.1, once=True, runtime_port=18004)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["snapshot_sha256"], after)
        docker.assert_not_called()
        seen.assert_called_once_with(result)

    def test_runtime_uses_frozen_snapshot_and_leaves_developer_head_and_files_alone(self):
        self.code.write_text("HEALTH = False\n", encoding="utf-8")
        snapshot = capture(self.repo, self.config)
        self.code.write_text("HEALTH = None\n", encoding="utf-8")
        with patch("cloudshield.watcher.run_candidate") as docker, \
             patch("cloudshield.watcher.judge_report", return_value={"overall": "fail", "checks": []}) as judge:
            result = check_snapshot((self.manifest, self.repo, self.compose, self.suite), snapshot, runtime_port=18004)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["cleanup"], "discarded")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").decode().strip(), snapshot[0])
        self.assertEqual(self.code.read_text(encoding="utf-8"), "HEALTH = None\n")
        revision = result["snapshot_revision"]
        self.assertNotEqual(revision, snapshot[0])
        self.assertEqual(git(self.repo, "show", f"{revision}:backend/app/main.py").strip(), b"HEALTH = False")
        project = result["project"]
        self.assertEqual([call.args for call in docker.call_args_list], [(project, "start"), (project, "discard")])
        self.assertIn(project, judge.call_args.args[0])

    def test_ai_mode_uses_existing_controller_and_review_gate(self):
        proposer, reviewer = Mock(), Mock()
        allowed = ["backend/app/main.py"]
        self.code.write_text("HEALTH = False\n", encoding="utf-8")
        with patch("cloudshield.watcher.run_candidate"), patch("cloudshield.watcher.repair") as repair:
            repair.return_value = {"status": "verified", "report": "repair.json", "attempts": []}
            result = watch(self.root / "target.json", Mock(), debounce=0.1, once=True, runtime_port=18004,
                           repair_port=18005, allowed=allowed, proposer=proposer, reviewer=reviewer)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["repair_report"], "repair.json")
        manifest, patches, paths, port = repair.call_args.args
        self.assertIn(result["project"], manifest)
        self.assertEqual((patches, paths, port), (None, allowed, 18005))
        self.assertEqual(repair.call_args.kwargs,
                         {"proposer": proposer, "reviewer": reviewer, "max_attempts": 3, "judge": None})
