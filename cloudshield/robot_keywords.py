import json
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.parse import urlencode, urlsplit


class TargetChecks:
    def __init__(self, base_url, credentials_file):
        origin = urlsplit(base_url)
        if (origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost", "::1"}
                or origin.username or origin.password or origin.query or origin.fragment
                or origin.path not in {"", "/"}):
            raise ValueError("Phase 4 checks require a local HTTP origin with no credentials or path")
        self.host = origin.hostname
        self.port = origin.port or 80
        self.credentials_file = Path(credentials_file)

    def _request(self, path, method="GET", form=None, token=None):
        data = urlencode(form).encode() if form is not None else None
        headers = {}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection = HTTPConnection(self.host, self.port, timeout=8)
        try:
            connection.request(method, "/api/v1" + path, body=data, headers=headers)
            with connection.getresponse() as response:
                return response.status, response.read(65537)
        except (HTTPException, OSError):
            raise AssertionError("CONNECTIVITY: API did not return an HTTP response") from None
        finally:
            connection.close()

    @staticmethod
    def _json(body):
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise AssertionError("API response was not valid JSON") from None

    def health_should_respond(self):
        status, body = self._request("/utils/health-check/")
        if status != 200 or self._json(body) is not True:
            raise AssertionError(f"Expected a healthy HTTP response, got status {status}")

    def anonymous_user_lookup_should_be_denied(self):
        status, _ = self._request("/users/me")
        if status not in {401, 403}:
            raise AssertionError(f"Expected access to be denied, got HTTP {status}")

    def seeded_admin_lookup_should_succeed(self):
        token = self.seeded_admin_token()
        status, body = self._request("/users/me", token=token)
        if status != 200:
            raise AssertionError(f"Database-backed user lookup returned HTTP {status}")
        user = self._json(body)
        if not isinstance(user, dict) or user.get("email") != "admin@example.com" or user.get("is_active") is not True:
            raise AssertionError("User lookup did not return the active seeded admin")

    def seeded_admin_token(self):
        try:
            lines = self.credentials_file.read_text(encoding="utf-8-sig").splitlines()
            password = next(line.partition("=")[2] for line in lines if line.startswith("APP_ADMIN_PASSWORD="))
        except (OSError, UnicodeError, StopIteration):
            raise AssertionError("RUNNER ERROR: Missing local lab credentials") from None
        if not password:
            raise AssertionError("RUNNER ERROR: Empty local admin password")
        status, body = self._request("/login/access-token", "POST", {
            "username": "admin@example.com", "password": password,
        })
        if status != 200:
            raise AssertionError(f"Seeded admin login returned HTTP {status}")
        login = self._json(body)
        token = login.get("access_token") if isinstance(login, dict) else None
        if not isinstance(token, str) or not token:
            raise AssertionError("Login did not return an access token")
        return token
