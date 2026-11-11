import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloudshield.checks import check_python, compare, read_object, validate_config
from cloudshield.evidence import collect_evidence, compose_rows, read_backend_config, redact_logs
from cloudshield.judge import parse_robot_report, run_judge, source_revision, verified_backend
from cloudshield.robot_keywords import TargetChecks


class ConfigAndSyntaxTests(unittest.TestCase):
    def test_allowlist_accepts_value_change_without_hiding_missing_key_or_type(self):
        reference = {"DB_HOST": "db", "DB_PORT": 5432, "DB_NAME": "app"}
        candidate = {"DB_HOST": "staging-db", "DB_PORT": "5432"}
        outcomes = {result["id"]: result["status"] for result in compare(reference, candidate, reference)}
        self.assertEqual(outcomes, {"config/DB_HOST": "pass", "config/DB_PORT": "fail", "config/DB_NAME": "fail"})

    def test_config_rejects_boolean_and_out_of_range_port(self):
        for value in (True, 0, 65536, "5432"):
            with self.subTest(value=value):
                result = next(result for result in validate_config({"DB_PORT": value}) if result["id"] == "config/DB_PORT")
                self.assertEqual(result["status"], "fail")

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"DB_PORT": 5432, "DB_PORT": 42}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                read_object(path)

    def test_syntax_check_compiles_without_executing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "good.py").write_text('raise RuntimeError("Do not execute")\n', encoding="utf-8")
            (root / "bad.py").write_text("return 42\n", encoding="utf-8")
            outcomes = {result["id"]: result["status"] for result in check_python(root)}
            self.assertEqual(outcomes, {"syntax/good.py": "pass", "syntax/bad.py": "fail"})

    def test_mismatched_interpreter_is_a_runner_error(self):
        result = check_python("unused", "99.0")[0]
        self.assertEqual((result["id"], result["status"]), ("syntax/interpreter", "error"))


class JudgeTests(unittest.TestCase):
    def test_report_keeps_assertions_skips_connection_errors_and_missing_cases_distinct(self):
        xml = """<robot><suite>
          <test name="healthy"><status status="PASS"/></test>
          <test name="denied"><status status="FAIL">Unexpected HTTP 200</status></test>
          <test name="deferred"><status status="SKIP"/></test>
          <test name="offline"><status status="FAIL">CONNECTIVITY: timed out</status></test>
          <test name="credentials"><status status="FAIL">RUNNER ERROR: no lab password</status></test>
        </suite></robot>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.xml"
            path.write_text(xml, encoding="utf-8")
            results = parse_robot_report(path, ["missing"])
        self.assertEqual([result["status"] for result in results], ["pass", "fail", "skip", "connectivity", "error", "error"])

    def test_robot_setup_error_invalidates_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.xml"
            path.write_text('<robot><errors><msg level="ERROR">Library import failed</msg></errors></robot>', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "setup or runner error"):
                parse_robot_report(path, ["healthy"])

    def test_bad_static_check_prevents_runtime_invocation(self):
        manifest = Path(__file__).resolve().parents[1] / "examples" / "target.json"
        with patch("cloudshield.judge.check_python", return_value=[{"id": "syntax/demo.py", "status": "fail"}]), \
             patch("cloudshield.judge.run_robot") as robot:
            results = run_judge(manifest)
        robot.assert_not_called()
        self.assertEqual(sum(result["status"] == "skip" for result in results["checks"]), 3)

    def test_runtime_drift_and_changed_container_make_a_green_robot_run_unreliable(self):
        manifest = Path(__file__).resolve().parents[1] / "examples" / "target.json"
        deployment = {"container_id": "before", "image_id": "image", "source_revision": "revision"}
        with patch("cloudshield.judge.check_python", return_value=[]), \
             patch("cloudshield.judge.source_revision", return_value="revision"), \
             patch("cloudshield.judge.verified_backend", side_effect=[deployment, {**deployment, "container_id": "after"}]), \
             patch("cloudshield.judge.read_backend_config", return_value={"DB_HOST": "unexpected"}), \
             patch("cloudshield.judge.run_robot", return_value=[{"id": "robot/api-health", "status": "pass"}]):
            report = run_judge(manifest)
        checks = {result["id"]: result["status"] for result in report["checks"]}
        self.assertEqual(checks["runtime/config/DB_HOST"], "fail")
        self.assertEqual(checks["deployment/identity"], "error")

    def test_image_revision_and_loopback_port_both_have_to_match(self):
        revision = "a" * 40
        backend = {"service": "backend", "state": "running", "source_revision": revision,
                   "image_id": "image", "container_id": "container", "published_ports": [18000]}
        with patch("cloudshield.judge.collect_evidence", return_value={"containers": [backend]}):
            self.assertEqual(verified_backend("compose", "env", "lab", "http://127.0.0.1:18000", revision)["image_id"], "image")
            with self.assertRaisesRegex(ValueError, "does not match the source"):
                verified_backend("compose", "env", "lab", "http://127.0.0.1:18000", "b" * 40)
            with self.assertRaisesRegex(ValueError, "published loopback port"):
                verified_backend("compose", "env", "lab", "http://127.0.0.1:18001", revision)

    @unittest.skipUnless(shutil.which("git"), "Git is required for the checkout fixture")
    def test_source_revision_requires_a_clean_git_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.run(["git", "-C", directory, *args], capture_output=True,
                                      text=True, check=True).stdout.strip()

            git("init", "-q")
            source = root / "main.py"
            source.write_text("example = 1\n", encoding="utf-8")
            git("add", "main.py")
            git("-c", "user.name=CloudShield test", "-c", "user.email=test@example.invalid",
                "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={root / 'no-hooks'}",
                "commit", "-qm", "Test baseline")
            self.assertEqual(source_revision(root), git("rev-parse", "HEAD"))
            source.write_text("example = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "clean target Git checkout"):
                source_revision(root)

    def test_seeded_admin_keyword_checks_authenticated_database_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("APP_ADMIN_PASSWORD=synthetic\n", encoding="utf-8")
            keywords = TargetChecks("http://127.0.0.1:18000", path)
            with patch.object(keywords, "_request", side_effect=[
                (200, b'{"access_token":"synthetic-token"}'),
                (200, b'{"email":"admin@example.com","is_active":true}'),
            ]) as request:
                keywords.seeded_admin_lookup_should_succeed()
            request.assert_called_with("/users/me", token="synthetic-token")

    def test_lab_password_cannot_be_sent_to_remote_target(self):
        with self.assertRaisesRegex(ValueError, "local HTTP origin"):
            TargetChecks("http://example.com", ".env")

    def test_http_transport_encodes_login_and_closes_connection(self):
        keywords = TargetChecks("http://127.0.0.1:18000", ".env")
        with patch("cloudshield.robot_keywords.HTTPConnection") as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b"{}"
            self.assertEqual(keywords._request("/login/access-token", "POST", {"password": "a+b"}), (200, b"{}"))
        factory.assert_called_once_with("127.0.0.1", 18000, timeout=8)
        connection.request.assert_called_once_with("POST", "/api/v1/login/access-token", body=b"password=a%2Bb",
                                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        connection.close.assert_called_once()


class EvidenceTests(unittest.TestCase):
    def test_compose_rows_accepts_array_and_line_formats(self):
        self.assertEqual(compose_rows('[{"Service":"db"}]'), compose_rows('{"Service":"db"}\n'))
        self.assertEqual(compose_rows(""), [])

    def test_log_redaction_keeps_diagnostic_line_and_omits_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("POSTGRES_PASSWORD=lab-password-123\n", encoding="utf-8")
            result = redact_logs("connection refused at db:5432\nvalue lab-password-123\nAuthorization: Bearer opaque", path)
        self.assertEqual(result, ["connection refused at db:5432", "value [redacted]", "[sensitive log line omitted]"])

    def test_evidence_keeps_observed_identity_and_ignores_extra_fields(self):
        container_id = "a" * 64
        image_id = "sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yml"
            env = Path(directory) / ".env"
            compose.touch()
            env.touch()
            row = '{"Project":"lab","Service":"db","State":"exited","ID":"' + container_id + '","Private":"ignored"}'
            with patch("cloudshield.evidence.docker_output", side_effect=[(row, ""), (image_id, "")]):
                snapshot = collect_evidence(compose, env, "lab")
        service = snapshot["containers"][0]
        self.assertEqual((service["image_id"], service["state"]), (image_id, "exited"))
        self.assertNotIn("Private", service)

    def test_snapshot_excludes_database_credentials_and_full_url(self):
        raw = '"DATABASE_URL=postgresql://private-user:private-password@staging-db:5432/app"\n"staging/postgres"\n'
        with patch("cloudshield.evidence.docker_output", return_value=(raw, "")):
            config = read_backend_config("a" * 64)
        self.assertEqual(config, {"DB_HOST": "staging-db", "DB_PORT": 5432, "DB_NAME": "app",
                                  "DB_SECRET_REF": "staging/postgres"})
