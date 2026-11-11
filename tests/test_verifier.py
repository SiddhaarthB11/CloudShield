import copy
import unittest

from cloudshield.verifier import required_checks, verify


HEALTH = "robot/api-health"
AUTH = "robot/anonymous-user-denied"
DATABASE = "robot/database-backed-user-lookup"
PROFILE = {
    "suite_sha256": "a" * 64, "keywords_sha256": "b" * 64, "python_version": "3.14",
    "required_robot_checks": ["api-health", "anonymous-user-denied", "database-backed-user-lookup"],
}


def report(mark, statuses):
    ids = required_checks(PROFILE) | {"syntax/main.py"}
    return {
        "command": "judge", "overall": "fail" if "fail" in statuses.values() else "pass",
        "test_profile": copy.deepcopy(PROFILE),
        "deployment": {"source_revision": mark * 40, "image_id": "sha256:" + mark * 64, "container_id": mark * 64},
        "checks": [{"id": name, "status": statuses.get(name, "pass")} for name in sorted(ids)],
    }


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.baseline = report("c", {HEALTH: "fail"})
        self.candidate = report("d", {})
        self.record = {
            "status": "started", "baseline_revision": "c" * 40, "candidate_revision": "d" * 40,
            "deployment": dict(self.candidate["deployment"]),
        }

    def test_verifies_a_real_improvement_with_every_required_check_passing(self):
        result = verify(self.baseline, self.candidate, self.record)
        self.assertEqual(result["verdict"], "verified")
        self.assertEqual(result["resolved_targets"], [HEALTH])
        self.assertEqual(result["regressions"], [])

    def test_equal_failure_count_does_not_hide_a_regression(self):
        result = verify(self.baseline, report("d", {AUTH: "fail"}), self.record)
        self.assertEqual(result["verdict"], "rejected")
        self.assertEqual(result["resolved_targets"], [HEALTH])
        self.assertEqual(result["regressions"], [AUTH])

    def test_original_failure_must_clear(self):
        result = verify(self.baseline, report("d", {HEALTH: "fail"}), self.record)
        self.assertEqual(result["verdict"], "rejected")
        self.assertEqual(result["unresolved_targets"], [HEALTH])

    def test_missing_or_skipped_required_check_blocks_acceptance(self):
        for status in (None, "skip", "connectivity", "error"):
            with self.subTest(status=status):
                candidate = report("d", {})
                if status is None:
                    candidate["checks"] = [check for check in candidate["checks"] if check["id"] != DATABASE]
                else:
                    next(check for check in candidate["checks"] if check["id"] == DATABASE)["status"] = status
                self.assertEqual(verify(self.baseline, candidate, self.record)["verdict"], "inconclusive")

    def test_same_revision_with_wrong_running_container_blocks_acceptance(self):
        self.candidate["deployment"]["container_id"] = "e" * 64
        result = verify(self.baseline, self.candidate, self.record)
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertTrue(any("exact recorded candidate" in issue for issue in result["issues"]))

    def test_changed_robot_definitions_block_acceptance(self):
        self.candidate["test_profile"]["suite_sha256"] = "e" * 64
        self.assertEqual(verify(self.baseline, self.candidate, self.record)["verdict"], "inconclusive")

    def test_healthy_baseline_still_detects_phase_7_regression(self):
        result = verify(report("c", {}), report("d", {HEALTH: "fail"}), self.record)
        self.assertEqual(result["verdict"], "rejected")
        self.assertEqual(result["regressions"], [HEALTH])

    def test_two_green_builds_do_not_prove_a_repair(self):
        self.assertEqual(verify(report("c", {}), self.candidate, self.record)["verdict"], "inconclusive")
