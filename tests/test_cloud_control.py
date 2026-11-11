import copy
import json
import tempfile
import re
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cloudshield import aws_adapter as cloud
from cloudshield import dashboard as ui


REGION = "us-east-1"
PREFIX = "arn:aws:ecs:us-east-1:123456789012:"
CONFIG = {"adapter": "aws-ecs", "profile": "test", "region": REGION,
          "cluster": PREFIX + "cluster/lab", "service": PREFIX + "service/lab/api",
          "approved_task_definition": PREFIX + "task-definition/lab:1", "image_digest": "sha256:" + "a" * 64,
          "desired_count": 1, "allow_restore": True, "probe_health": False, "sandbox": True}


class FakeAWS:
    def __init__(self, drift=False, converge=True, broken_health=False):
        self.definition = PREFIX + "task-definition/lab:2" if drift else CONFIG["approved_task_definition"]
        self.calls = []
        self.converge = converge
        self.broken_health = broken_health

    def __call__(self, config, service, operation, *args):
        self.calls.append((service, operation, args))
        if operation == "describe-services":
            return {"services": [{"serviceArn": CONFIG["service"], "clusterArn": CONFIG["cluster"], "status": "ACTIVE",
                                  "taskDefinition": self.definition, "desiredCount": 1, "pendingCount": 0}]}
        if operation == "list-tasks":
            return {"taskArns": [PREFIX + "task/lab/abc"]}
        if operation == "describe-tasks":
            return {"tasks": [{"taskArn": PREFIX + "task/lab/abc", "taskDefinitionArn": self.definition,
                               "lastStatus": "RUNNING", "healthStatus": "UNHEALTHY" if self.broken_health else "HEALTHY",
                               "containers": [{"name": "backend", "imageDigest": CONFIG["image_digest"]}]}]}
        if operation == "update-service":
            if self.converge:
                self.definition = args[args.index("--task-definition") + 1]
            return {}
        raise AssertionError(operation)


class CloudTests(unittest.TestCase):
    def test_approved_healthy_service_is_never_redeployed(self):
        fake = FakeAWS()
        self.assertEqual(cloud.recover(CONFIG, fake, wait=lambda _: None)["status"], "healthy")
        self.assertFalse(any(op == "update-service" for _, op, _ in fake.calls))

    def test_drift_restores_exact_task_and_verifies(self):
        fake = FakeAWS(drift=True)
        result = cloud.recover(CONFIG, fake, wait=lambda _: None, attempts=1)
        self.assertEqual(result["status"], "recovered")
        update = next(args for _, op, args in fake.calls if op == "update-service")
        self.assertIn(CONFIG["approved_task_definition"], update)

    def test_disabled_policy_and_unknown_failure_do_not_mutate(self):
        for cfg, fake in (({**CONFIG, "allow_restore": False}, FakeAWS(drift=True)),
                          (CONFIG, FakeAWS(broken_health=True))):
            self.assertEqual(cloud.recover(cfg, fake)["status"], "handoff")
            self.assertFalse(any(op == "update-service" for _, op, _ in fake.calls))

    def test_unverified_restoration_is_not_success(self):
        result = cloud.recover(CONFIG, FakeAWS(drift=True, broken_health=True), wait=lambda _: None, attempts=1)
        self.assertEqual(result["status"], "handoff")

    def test_region_and_account_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "target.json"
            path.write_text(json.dumps({**CONFIG, "service": CONFIG["service"].replace("123456789012", "999999999999")}))
            with self.assertRaises(ValueError):
                cloud.load_target(path)

    def test_probe_failure_prevents_recovery_claim(self):
        cfg = {**CONFIG, "probe_health": True}
        with patch.object(cloud, "probe", side_effect=OSError("no connection")):
            result = cloud.observe(cfg, FakeAWS())
        self.assertEqual(result["status"], "fail")
        self.assertIn("error", [check["status"] for check in result["checks"]])

    def test_concurrent_service_change_is_not_overwritten(self):
        fake = FakeAWS(drift=True)
        reads = 0
        def concurrent(config, service, operation, *args):
            nonlocal reads
            if operation == "describe-services":
                reads += 1
                if reads == 2:
                    fake.definition = PREFIX + "task-definition/lab:3"
            return fake(config, service, operation, *args)
        result = cloud.recover(CONFIG, concurrent, wait=lambda _: None)
        self.assertEqual(result["status"], "handoff")
        self.assertFalse(any(op == "update-service" for _, op, _ in fake.calls))

    def test_aws_cli_diagnostic_never_exposes_provider_output(self):
        from types import SimpleNamespace
        with patch.object(cloud.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="private-value", stderr="An error occurred (AccessDeniedException): private-value")):
            with self.assertRaisesRegex(ValueError, "AccessDeniedException") as caught:
                cloud.aws(CONFIG, "ecs", "describe-services")
        self.assertNotIn("private-value", str(caught.exception))

    def test_cloudformation_references_and_digest_contract(self):
        template = json.loads((cloud.ROOT / "aws/sandbox.json").read_text())
        known = set(template["Resources"]) | set(template["Parameters"])
        def inspect(value):
            if isinstance(value, dict):
                if "Ref" in value:
                    self.assertTrue(value["Ref"] in known or value["Ref"].startswith("AWS::"))
                if "Fn::GetAtt" in value:
                    self.assertIn(value["Fn::GetAtt"][0], template["Resources"])
                for item in value.values():
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
        inspect(template)
        self.assertIsNotNone(re.fullmatch(template["Parameters"]["ImageUri"]["AllowedPattern"],
                                          "123456789012.dkr.ecr.us-east-1.amazonaws.com/lab@" + CONFIG["image_digest"]))
        self.assertEqual(template["Outputs"]["Cluster"]["Value"], {"Fn::GetAtt": ["Cluster", "Arn"]})

    def test_remote_probe_does_not_follow_redirects_or_report_tokens(self):
        cfg = {**CONFIG, "synthetic_login": True, "admin_secret": "arn:aws:secretsmanager:us-east-1:123456789012:secret:lab"}
        fake = lambda *args: {"SecretString": "private-password"}
        with patch.object(cloud, "endpoint", return_value="8.8.8.8"), patch.object(cloud, "request", side_effect=[
                (200, True), (401, {}), (200, {"access_token": "private-token"}), (200, {"is_active": True})]):
            checks = cloud.probe(cfg, {}, fake)
        self.assertNotIn("private", json.dumps(checks))
        self.assertTrue(all(row["status"] == "pass" for row in checks))


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "examples").mkdir()
        self.manifest = self.root / "examples/target.json"
        self.manifest.write_text(json.dumps({"source_dir": "app", "compose_file": "compose.yml", "base_url": "http://127.0.0.1:18000",
                                              "credentials_file": "credentials.env"}))
        root_patch = patch.object(ui, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.db = ui.connect(self.root / "state.sqlite3")
        self.addCleanup(self.db.close)
        self.target = ui.register(self.db, {"manifest": "examples/target.json", "name": "Demo"})

    def test_registration_and_jobs_survive_connection_restart(self):
        job = ui.enqueue(self.db, self.target, "judge")
        with closing(ui.connect(self.root / "state.sqlite3")) as other:
            self.assertEqual(other.execute("SELECT id FROM jobs").fetchone()[0], job)
            self.assertEqual(len(ui.state(other)["targets"]), 1)

    def test_duplicate_jobs_and_arbitrary_commands_rejected(self):
        ui.enqueue(self.db, self.target, "judge")
        with self.assertRaises(ValueError):
            ui.enqueue(self.db, self.target, "monitor")
        with self.assertRaises(ValueError):
            ui.enqueue(self.db, self.target, "powershell")

    def test_ai_requires_policy_and_exact_allowed_paths(self):
        with self.assertRaises(ValueError):
            ui.enqueue(self.db, self.target, "repair")
        with self.assertRaises(ValueError):
            ui.policy({"allowed_paths": ["../../secrets.py"]})

    def test_manifest_cannot_escape_or_read_credentials(self):
        with self.assertRaises(ValueError):
            ui.manifest_path("../passwords.json")
        with self.assertRaises(ValueError):
            ui.manifest_path(".env.gemini.local")

    def test_failure_pauses_automatic_retries_and_retains_evidence(self):
        job = ui.enqueue(self.db, self.target, "judge")
        self.db.execute("UPDATE targets SET monitoring=1 WHERE id=?", (self.target,))
        result = self.root / "result.json"
        result.write_text(json.dumps({"exit_code": 2, "result": {"status": "inconclusive"}}))
        ui.finish(self.db, job, result)
        self.assertEqual(self.db.execute("SELECT monitoring FROM targets").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT status FROM jobs").fetchone()[0], "attention")

    def test_redaction_removes_sensitive_values(self):
        result = ui.sanitize({"authorization": "Bearer private-value", "checks": [{"status": "pass"}], "detail": "password=hidden"})
        self.assertNotIn("private-value", json.dumps(result))
        self.assertNotIn("hidden", json.dumps(result))

    def test_scheduler_enqueues_once_per_target(self):
        self.db.execute("UPDATE targets SET monitoring=1")
        self.db.commit()
        ui.schedule(self.db)
        ui.schedule(self.db)
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
