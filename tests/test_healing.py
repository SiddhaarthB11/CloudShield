import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloudshield.candidate import commit_candidate, git, write_json
from cloudshield.checks import check_python, read_object, summarize_checks
from cloudshield.fixer import make_patch
from cloudshield.incidents import diagnose
from cloudshield.managed_lab import promote_repair, reconcile_image
from cloudshield.recovery import plan_service_recovery, start_existing_services
from cloudshield.repair_memory import repair_history
from cloudshield.syntax_repair import repair_syntax
from cloudshield.verifier import verify
from test_verifier import HEALTH, report


class HealingTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        previous = Path.cwd()
        os.chdir(scratch.name)
        self.addCleanup(os.chdir, previous)
        self.root = Path.cwd()

    def test_routing_prefers_dependencies_and_configuration_to_login_symptoms(self):
        failures = [{"id": "robot/database-backed-user-lookup", "status": "fail"},
                    {"id": "runtime/config/DB_HOST", "status": "fail"}]
        self.assertEqual(diagnose(failures)["kind"], "configuration")
        self.assertEqual(diagnose(failures[:1])["kind"], "runtime")
        self.assertEqual(diagnose(failures, [{"service": "db", "state": "exited"}])["cause"], "db_unavailable")

    def test_restarts_only_the_existing_authorized_database(self):
        before = [{"service": "db", "state": "exited", "container_id": "a" * 64},
                  {"service": "backend", "state": "running", "source_revision": "c" * 40}]
        after = [{**before[0], "state": "running", "health": "healthy"}, before[1]]
        self.assertFalse(plan_service_recovery(before, {"backend"}))
        plan = plan_service_recovery(before, {"db"})
        with patch("cloudshield.recovery.collect_evidence", side_effect=[{"containers": before}, {"containers": after}]), \
             patch("cloudshield.recovery.docker_output") as docker:
            start_existing_services(("compose", "env", "lab"), plan, "c" * 40, Mock())
        docker.assert_called_once_with(["start", "a" * 64], "start existing lab db container")
        self.assertEqual(plan[0]["status"], "healthy")

    def test_memory_persists_reviewer_feedback(self):
        Path("runs/repairs").mkdir(parents=True)
        repair_history("incident", {"patch_sha256": "digest", "outcome": "rejected",
                                    "review": {"reason": "Breaks authorization"}})
        self.assertEqual(repair_history("incident")["digest"]["review_reason"], "Breaks authorization")
        self.assertFalse(repair_history("different-incident"))

    def test_syntax_repair_applies_two_steps_before_building(self):
        repo = self.root / "target"
        source = repo / "backend/app"
        source.mkdir(parents=True)
        path = "backend/app/main.py"
        broken = "first =\nsecond =\n"
        (repo / path).write_text(broken, encoding="utf-8")
        git(repo, "init", "-q")
        git(repo, "add", ".")
        revision = commit_candidate(repo, "Broken lab")
        static = summarize_checks("static", check_python(source))
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        target = ({"python_version": version}, repo, self.root / "compose.yml", self.root / "suite.robot")

        def propose(directory, baseline, evidence, allowed, previous):
            line = baseline["checks"][0]["line"]
            name = ("first", "second")[line - 1]
            contents = git(directory, "show", f"{baseline['source_revision']}:{path}").decode()
            return make_patch({"summary": "Complete the assignment", "edits": [
                {"path": path, "old_lines": [f"{name} ="], "new_lines": [f"{name} = 1"]},
            ]}, {path: contents}, require_compiles=False)

        def judge(manifest):
            metadata = read_object(Path(manifest).parent / "candidate.json")
            judged = report("d", {})
            judged["deployment"]["source_revision"] = metadata["candidate_revision"]
            return judged

        def docker(project, action):
            if action == "start":
                return {"deployment": judge(Path("runs/candidates") / project / "target.json")["deployment"]}

        with patch("cloudshield.syntax_repair.run_candidate", side_effect=docker) as run:
            result = repair_syntax(target, revision, static, [path], 18005, propose,
                                   Mock(return_value={"decision": "approve"}), judge)
        self.assertEqual(result["status"], "verified")
        self.assertEqual([step["outcome"] for step in result["attempts"]], ["partial", "compiled"])
        self.assertEqual([call.args[1] for call in run.call_args_list], ["start", "discard"])
        self.assertEqual((repo / path).read_text(encoding="utf-8"), broken)

    def test_promotion_rolls_back_when_live_checks_fail(self):
        baseline, candidate = report("c", {HEALTH: "fail"}), report("d", {})
        live = report("d", {HEALTH: "fail"})
        record = {"status": "started", "baseline_revision": "c" * 40, "candidate_revision": "d" * 40,
                  "deployment": candidate["deployment"], "worktree": str(self.root / "new-source")}
        manifest = self.root / "target.json"
        settings = {key: key for key in ("compose_file", "credentials_file", "environment_config", "robot_suite",
                                         "source_dir")}
        write_json(manifest, settings)
        write_json(self.root / "attempt-1-deployment.json", record)
        write_json(self.root / "attempt-1-judge.json", candidate)
        write_json(self.root / "attempt-1-verdict.json", verify(baseline, candidate, record))
        repair = {"report": str(self.root / "repair.json"),
                  "attempts": [{"number": 1, "review": {"decision": "approve"}}]}
        with patch("cloudshield.managed_lab.require_managed_lab"), patch("cloudshield.managed_lab.approved_configuration"), \
             patch("cloudshield.managed_lab.current_backend", return_value=baseline["deployment"]), \
             patch("cloudshield.managed_lab.replace_backend") as replace:
            result = promote_repair(manifest, (), baseline, repair, self.root, Mock(side_effect=[live, baseline]))
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual([call.args[-1] for call in replace.call_args_list],
                         [candidate["deployment"]["image_id"], baseline["deployment"]["image_id"]])
        self.assertEqual(read_object(manifest), settings)

    def test_image_recovery_verifies_the_approved_revision_before_replacing_backend(self):
        wanted = report("c", {})
        observed = {"service": "backend", "state": "running", **report("d", {})["deployment"]}
        with patch("cloudshield.managed_lab.require_managed_lab"), patch("cloudshield.managed_lab.approved_configuration"), \
             patch("cloudshield.managed_lab.collect_evidence", return_value={"containers": [observed]}), \
             patch("cloudshield.managed_lab.docker_output", return_value=('\"' + "d" * 40 + '\"', "")), \
             patch("cloudshield.managed_lab.replace_backend") as replace:
            with self.assertRaisesRegex(ValueError, "approved image does not match"):
                reconcile_image(self.root / "target.json", (), observed, wanted["deployment"]["image_id"],
                                "c" * 40, self.root, Mock())
        replace.assert_not_called()
