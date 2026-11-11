"""Real loopback requests: session, CSRF, persistence, bounded report access."""
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from cloudshield.dashboard import ROOT


class DashboardHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                cls.port = sock.getsockname()[1]
        except OSError as exc:
            cls.temp.cleanup()
            raise unittest.SkipTest("Loopback binding unavailable") from exc
        cls.process = subprocess.Popen([sys.executable, "-m", "cloudshield", "serve", "--no-worker", "--port", str(cls.port),
                                        "--database", str(Path(cls.temp.name) / "state.sqlite3")],
                                       cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                c = HTTPConnection("127.0.0.1", cls.port, timeout=1)
                c.request("GET", "/")
                response = c.getresponse()
                cls.cookie = response.getheader("Set-Cookie").split(";")[0]
                response.read()
                c.close()
                return
            except (OSError, AttributeError):
                time.sleep(.1)
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.temp.cleanup()
        raise RuntimeError("Dashboard did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request(method, path, json.dumps(body) if body is not None else None, headers or {})
            response = c.getresponse()
            data = response.read()
            return response.status, data
        finally:
            c.close()

    def headers(self):
        return {"Cookie": self.cookie, "Origin": f"http://127.0.0.1:{self.port}", "X-CloudShield": "local", "Content-Type": "application/json"}

    def test_api_requires_session_and_mutations_require_origin(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 403)
        headers = self.headers()
        headers["Origin"] = "https://untrusted.example"
        self.assertEqual(self.request("POST", "/api/targets", {}, headers)[0], 403)

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "untrusted.example"})[0], 403)

    def test_static_files_and_registered_target_roundtrip(self):
        self.assertEqual(self.request("GET", "/app.js")[0], 200)
        status, data = self.request("POST", "/api/targets", {"name": "HTTP test", "manifest": "examples/target.json"}, self.headers())
        self.assertEqual(status, 200, data)
        target_id = json.loads(data)["target_id"]
        status, data = self.request("GET", "/api/state", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200)
        self.assertTrue(any(t["id"] == target_id for t in json.loads(data)["targets"]))

    def test_report_endpoint_cannot_read_secret_files(self):
        code, data = self.request("GET", "/api/report?path=.env.gemini.local", headers={"Cookie": self.cookie})
        self.assertEqual(code, 400)
        self.assertNotIn(b"GEMINI_API_KEY=", data)

    def test_action_allowlist_rejects_shell_commands(self):
        status, data = self.request("POST", "/api/jobs", {"target_id": "not-registered", "action": "powershell"}, self.headers())
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
