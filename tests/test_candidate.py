import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudshield.candidate import allowed_source_path, run_candidate


class CandidateTests(unittest.TestCase):
    def test_allowlist_paths_exclude_tests_and_path_escapes(self):
        self.assertTrue(allowed_source_path("backend/app/api/routes/utils.py"))
        for path in ("backend/app/tests/test_routes.py", "backend/app/../../suites/target.robot",
                     "backend/app/../settings.py", "backend/Dockerfile", "C:/backend/app/route.py"):
            with self.subTest(path=path):
                self.assertFalse(allowed_source_path(path))

    def test_start_uses_candidate_project_and_ignores_baseline_build_settings(self):
        project = "cloudshield-candidate-0123456789ab"
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as scratch:
            parent = Path(scratch) / "runs" / "candidates"
            root = parent / project
            root.mkdir(parents=True)
            compose = root / "compose.yml"
            compose.touch()
            (root / "candidate.json").write_text(json.dumps({
                "project": project, "candidate_revision": revision, "port": 18002,
            }))
            (root / "target.json").write_text(json.dumps({
                "compose_project": project, "compose_file": str(compose), "base_url": "http://127.0.0.1:18002",
            }))
            (root / "candidate.env").write_text("POSTGRES_PASSWORD=synthetic\nAPP_HOST_PORT=18002\n")
            completed = subprocess.CompletedProcess([], 0, "", "")
            identity = {"source_revision": revision, "image_id": "candidate-image", "container_id": "candidate-container"}
            with patch("cloudshield.candidate.Path", side_effect=lambda *parts: parent if parts == ("runs", "candidates") else Path(*parts)), \
                 patch.dict(os.environ, {"POSTGRES_PASSWORD": "baseline", "APP_BUILD_CONTEXT": "wrong", "APP_HOST_PORT": "18000"}), \
                 patch("cloudshield.candidate.source_revision", return_value=revision), \
                 patch("cloudshield.candidate.subprocess.run", return_value=completed) as docker, \
                 patch("cloudshield.candidate.verified_backend", return_value=identity):
                result = run_candidate(project, "start")

            commands = [call.args[0] for call in docker.call_args_list]
            self.assertEqual([command[8] for command in commands], ["build", "up", "run", "up"])
            self.assertTrue(all(command[6:8] == ["-p", project] for command in commands))
            for call in docker.call_args_list:
                environment = call.kwargs["env"]
                self.assertNotIn("POSTGRES_PASSWORD", environment)
                self.assertEqual(environment["APP_HOST_PORT"], "18002")
                self.assertEqual(environment["APP_BUILD_CONTEXT"], str(root / "source"))
            self.assertEqual(result["deployment"], identity)
            self.assertTrue((root / "deployment.json").is_file())

    def test_discard_rejects_the_baseline_project(self):
        with self.assertRaisesRegex(ValueError, "candidate project ID"):
            run_candidate("cloudshield-phase1", "discard")
