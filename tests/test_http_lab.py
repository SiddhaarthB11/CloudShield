import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from cloudshield.robot_keywords import TargetChecks


class LabHandler(BaseHTTPRequestHandler):
    database_online = True

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        if (self.path != "/api/v1/login/access-token"
                or self.headers.get("Content-Type") != "application/x-www-form-urlencoded"
                or form != {"username": ["admin@example.com"], "password": ["synthetic"]}):
            self.send_json(400, {})
        elif not self.database_online:
            self.send_json(500, {})
        else:
            self.send_json(200, {"access_token": "test-token"})

    def do_GET(self):
        if self.path == "/api/v1/utils/health-check/":
            self.send_json(200, True)
        elif self.path != "/api/v1/users/me":
            self.send_json(404, {})
        elif self.headers.get("Authorization") != "Bearer test-token":
            self.send_json(401, {})
        else:
            self.send_json(200, {"email": "admin@example.com", "is_active": True})

    def log_message(self, *args):
        pass


class HttpLabTests(unittest.TestCase):
    def test_real_http_requests_show_shallow_health_and_database_outage(self):
        class Scenario(LabHandler):
            pass

        with tempfile.TemporaryDirectory() as scratch, ThreadingHTTPServer(("127.0.0.1", 0), Scenario) as server:
            credentials = Path(scratch) / "lab.env"
            credentials.write_text("APP_ADMIN_PASSWORD=synthetic\n", encoding="utf-8")
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                checks = TargetChecks(f"http://127.0.0.1:{server.server_port}", credentials)
                try:
                    with socket.create_connection(("127.0.0.1", server.server_port), timeout=2):
                        pass
                except PermissionError:
                    self.skipTest("This session cannot connect to a local listening socket")
                checks.health_should_respond()
                checks.anonymous_user_lookup_should_be_denied()
                checks.seeded_admin_lookup_should_succeed()
                Scenario.database_online = False
                checks.health_should_respond()
                with self.assertRaisesRegex(AssertionError, "HTTP 500"):
                    checks.seeded_admin_lookup_should_succeed()
                Scenario.database_online = True
                checks.seeded_admin_lookup_should_succeed()
            finally:
                server.shutdown()
                worker.join()
