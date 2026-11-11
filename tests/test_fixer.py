import json
import os
import unittest
from unittest.mock import PropertyMock, patch

from cloudshield.fixer import DEFAULT_MODEL, load_gemini_key, make_patch, propose_gemini, request_gemini


SOURCE = "backend/app/api/routes/utils.py"
BROKEN = "def health_check():\n    return False\n"
PROPOSAL = {"summary": "Restore healthy response", "edits": [
    {"path": SOURCE, "old_lines": ["    return False"], "new_lines": ["    return True"]},
]}


class FixerTests(unittest.TestCase):
    def test_local_key_is_used_when_shell_key_is_absent(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), patch("cloudshield.fixer.Path") as path:
            path.return_value.read_text.return_value = "# local credentials\nGEMINI_API_KEY=AQ.synthetic-file-key\n"
            self.assertEqual(load_gemini_key(), "AQ.synthetic-file-key")
            path.assert_called_once_with(".env.gemini.local")
            self.assertEqual(os.environ["GEMINI_API_KEY"], "")

    def test_shell_key_takes_precedence_over_file(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic-shell-key"}), patch("cloudshield.fixer.Path") as path:
            self.assertEqual(load_gemini_key(), "synthetic-shell-key")
            path.assert_not_called()

    def test_proposal_becomes_a_normal_git_diff(self):
        diff, summary = make_patch(PROPOSAL, {SOURCE: BROKEN})
        self.assertIn(b"--- a/" + SOURCE.encode(), diff)
        self.assertIn(b"-    return False\n+    return True", diff)
        self.assertEqual(summary, "Restore healthy response")

    def test_unapproved_ambiguous_and_invalid_syntax_edits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "approved file"):
            make_patch(PROPOSAL, {"backend/app/other.py": BROKEN})
        with self.assertRaisesRegex(ValueError, "exactly one block"):
            make_patch(PROPOSAL, {SOURCE: BROKEN + BROKEN})
        invalid = {**PROPOSAL, "edits": [{**PROPOSAL["edits"][0], "new_lines": ["    return ("]}]}
        with self.assertRaisesRegex(ValueError, "invalid Python"):
            make_patch(invalid, {SOURCE: BROKEN})

    def test_model_gets_only_selected_source_check_and_service_metadata(self):
        baseline = {"checks": [{"id": "robot/api-health", "status": "fail", "detail": "got status 200 private"}],
                    "deployment": {"source_revision": "a" * 40}}
        evidence = {"containers": [{"service": "db", "state": "running", "health": "healthy", "logs": ["secret"]}]}
        with patch("cloudshield.fixer.source_context", return_value={SOURCE: BROKEN}), \
             patch("cloudshield.fixer.request_gemini", return_value=PROPOSAL) as request:
            diff, _ = propose_gemini("source", baseline, evidence, [SOURCE], [], model=DEFAULT_MODEL)
        self.assertIsNotNone(diff)
        context = request.call_args.args[1]
        self.assertEqual(context["failed_checks"][0]["observed_http_status"], "200")
        self.assertNotIn("private", json.dumps(context))
        self.assertNotIn("secret", json.dumps(context))

    def test_stopped_database_is_handed_off_without_a_model_request(self):
        baseline = {"checks": [{"id": "robot/database-backed-user-lookup", "status": "fail"}],
                    "deployment": {"source_revision": "a" * 40}}
        with patch("cloudshield.fixer.source_context", return_value={SOURCE: BROKEN}), \
             patch("cloudshield.fixer.request_gemini") as request:
            diff, summary = propose_gemini("source", baseline, {"containers": []}, [SOURCE], [], model=DEFAULT_MODEL)
        self.assertIsNone(diff)
        self.assertIn("database", summary)
        request.assert_not_called()

    def test_gemini_request_uses_header_and_parses_structured_response(self):
        envelope = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"thought": True, "text": "ignore"}, {"text": json.dumps(PROPOSAL)},
        ]}}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic-key"}), \
             patch("cloudshield.fixer.HTTPSConnection") as connect:
            response = connect.return_value.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps(envelope).encode()
            self.assertEqual(request_gemini(DEFAULT_MODEL, {"source": {}}), PROPOSAL)
            call = connect.return_value.request.call_args
            self.assertEqual(call.args[1], f"/v1beta/models/{DEFAULT_MODEL}:generateContent")
            self.assertEqual(call.kwargs["headers"]["x-goog-api-key"], "synthetic-key")
            payload = json.loads(call.kwargs["body"])
            self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
            self.assertNotIn("synthetic-key", call.kwargs["body"].decode())

    def test_gemini_retries_only_temporary_unavailability(self):
        envelope = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": json.dumps(PROPOSAL)},
        ]}}]}
        for statuses, expected_delays in (((503, 200), [1]), ((503, 503, 503), [1, 2]), ((403,), [])):
            with self.subTest(statuses=statuses), \
                 patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic-key"}), \
                 patch("cloudshield.fixer.HTTPSConnection") as connect, \
                 patch("cloudshield.fixer.sleep") as wait:
                response = connect.return_value.getresponse.return_value.__enter__.return_value
                with patch.object(type(response), "status", new_callable=PropertyMock, create=True) as status:
                    status.side_effect = statuses
                    response.read.return_value = json.dumps(envelope).encode()
                    if statuses[-1] == 200:
                        self.assertEqual(request_gemini(DEFAULT_MODEL, {}), PROPOSAL)
                    else:
                        with self.assertRaisesRegex(ValueError, f"HTTP {statuses[-1]}"):
                            request_gemini(DEFAULT_MODEL, {})
                self.assertEqual(connect.call_count, len(statuses))
                self.assertEqual([call.args[0] for call in wait.call_args_list], expected_delays)
