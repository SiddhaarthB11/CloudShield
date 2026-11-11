import hashlib
import tempfile
import unittest
from pathlib import Path

from cloudshield.candidate import candidate_diff, git, write_json
from cloudshield.checks import read_object
from cloudshield.publisher import publish
from cloudshield.verifier import verify
from test_verifier import HEALTH, report


class PublisherTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.repo = root / "target"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "lab")
        self.base_ref = "refs/heads/lab"
        self.before = self.commit("return False")
        git(self.repo, "checkout", "--detach", self.before)
        self.after = self.commit("return True")
        git(self.repo, "checkout", "lab")
        self.run = root / "001122aabbcc"
        self.run.mkdir()
        self.branch = f"refs/heads/cloudshield/repairs/{self.run.name}"

        baseline, candidate = report("c", {HEALTH: "fail"}), report("d", {})
        baseline["deployment"]["source_revision"] = self.before
        candidate["deployment"]["source_revision"] = self.after
        diff = candidate_diff(self.repo, self.before, self.after)
        digest = hashlib.sha256(diff).hexdigest()
        project = "cloudshield-candidate-001122aabbcc"
        deployment = {"status": "started", "baseline_revision": self.before, "candidate_revision": self.after,
                      "deployment": candidate["deployment"], "project": project, "patch_sha256": digest}
        review = {"decision": "approve", "candidate_revision": self.after, "diff_sha256": digest}
        attempt = {"number": 1, "outcome": "verified", "candidate_revision": self.after,
                   "project": project, "patch_sha256": digest, "review": review}
        for filename, data in {
            "repair.json": {"command": "repair", "status": "verified", "attempts": [attempt],
                            "review_required": True},
            "baseline.json": baseline, "baseline-evidence.json": {},
            "attempt-1-deployment.json": deployment, "attempt-1-judge.json": candidate,
            "attempt-1-verdict.json": verify(baseline, candidate, deployment), "attempt-1-review.json": review,
        }.items():
            write_json(self.run / filename, data)
        for filename in ("attempt-1.patch", "attempt-1-reviewed.patch"):
            (self.run / filename).write_bytes(diff)

    def commit(self, text):
        (self.repo / "main.py").write_text(text + "\n", encoding="utf-8")
        git(self.repo, "add", "main.py")
        git(self.repo, "-c", "user.name=Lab", "-c", "user.email=lab@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", text)
        return git(self.repo, "rev-parse", "HEAD").decode().strip()

    def publish(self):
        return publish(self.run / "repair.json", self.repo, self.base_ref)

    def test_publishes_exact_tested_commit_and_can_repeat(self):
        for _ in range(2):
            result = self.publish()
            self.assertEqual(result["resolved_targets"], [HEALTH])
            self.assertEqual(read_object(self.run / "publication.json"), result)
            self.assertEqual(git(self.repo, "rev-parse", self.branch).decode().strip(), self.after)
            self.assertEqual(git(self.repo, "rev-parse", "HEAD").decode().strip(), self.before)

    def test_refuses_stale_base_and_never_overwrites_a_branch(self):
        git(self.repo, "update-ref", self.branch, self.before)
        with self.assertRaisesRegex(ValueError, "already points"):
            self.publish()
        self.assertEqual(git(self.repo, "rev-parse", self.branch).decode().strip(), self.before)
        git(self.repo, "update-ref", "-d", self.branch)
        self.commit("Developer changed the base")
        with self.assertRaisesRegex(ValueError, "base branch advanced"):
            self.publish()
        self.assertFalse(git(self.repo, "for-each-ref", self.branch))

    def test_refuses_a_changed_review_or_verification_result(self):
        for filename, content, message in (
            ("attempt-1-reviewed.patch", b"Different change", "approval does not match"),
            ("attempt-1-verdict.json", b'{"verdict": "rejected"}', "do not reproduce"),
        ):
            with self.subTest(filename=filename):
                path = self.run / filename
                original = path.read_bytes()
                path.write_bytes(content)
                with self.assertRaisesRegex(ValueError, message):
                    self.publish()
                path.write_bytes(original)
        self.assertFalse(git(self.repo, "for-each-ref", self.branch))
