import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from cloudshield.performance import LABEL, PLAN, run_jmeter, summarize_samples


PROFILE = {"users": 2, "ramp_seconds": 1, "measure_seconds": 2}


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def samples(self, rows):
        path = self.root / "samples.jtl"
        path.write_text("timeStamp,elapsed,label,success\n" + "".join(
            f"{start},{elapsed},{LABEL},{success}\n" for start, elapsed, success in rows
        ), encoding="utf-8")
        return path

    def test_summary_excludes_ramp_and_reports_errors_separately_from_p95(self):
        path = self.samples([(1000, 20, "true"), (2000, 40, "true"), (2500, 900, "false"),
                             (3650, 180, "true"), (4000, 500, "true")])
        result = summarize_samples(path, PROFILE)
        self.assertEqual(result, {"samples": 3, "successful": 2, "errors": 1, "error_pct": 33.333,
                                  "requests_per_second": 1.5, "successful_requests_per_second": 1.0,
                                  "p95_ms": 180, "ramp_or_outside_window_samples": 2})

    def test_partial_sample_file_is_inconclusive(self):
        path = self.samples([(1000, 20, "true"), (2000, 40, "true")])
        with self.assertRaisesRegex(ValueError, "measurement window"):
            summarize_samples(path, PROFILE)

    def test_jmeter_plan_stays_on_loopback_and_token_is_not_in_command(self):
        sampler = ElementTree.parse(PLAN).find(".//HTTPSamplerProxy")
        properties = {item.get("name"): item.text for item in sampler if item.tag == "stringProp"}
        self.assertEqual(properties["HTTPSampler.domain"], "127.0.0.1")
        self.assertEqual(properties["HTTPSampler.method"], "GET")
        self.assertEqual(properties["HTTPSampler.path"], "/api/v1/users/me")
        self.samples([(1000, 20, "true"), (2000, 40, "true"), (3800, 50, "true")])
        with patch("cloudshield.performance.subprocess.run") as process:
            process.return_value.returncode = 0
            run_jmeter("java", self.root / "ApacheJMeter.jar", self.root, PROFILE, 18009, "test-token")
        args, kwargs = process.call_args
        self.assertNotIn("test-token", " ".join(args[0]))
        self.assertEqual(kwargs["env"]["CLOUDSHIELD_LOAD_TOKEN"], "test-token")
        self.assertIn("-Jduration=3", args[0])
