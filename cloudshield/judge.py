import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .checks import check, check_python, compare, read_object, summarize_checks, validate_config
from .evidence import REVISION, collect_evidence, read_backend_config, running_backend


def parse_robot_report(path, required):
    root = ElementTree.parse(path).getroot()
    if root.tag != "robot" or root.findall("./errors/msg[@level='ERROR']"):
        raise ValueError("Robot reported a suite setup or runner error")
    results = {}
    for case in root.findall(".//test"):
        name = case.get("name")
        status = case.find("status")
        if not name or name in results or status is None:
            raise ValueError("Robot report has an invalid or duplicate check ID")
        outcome = {"PASS": "pass", "FAIL": "fail", "SKIP": "skip"}.get(status.get("status"), "error")
        message = (status.text or "").strip()
        if outcome == "fail" and message.startswith("CONNECTIVITY:"):
            outcome = "connectivity"
        elif outcome == "fail" and message.startswith("RUNNER ERROR:"):
            outcome = "error"
        results[name] = check(f"robot/{name}", outcome, message[:240] or outcome)
    for name in required:
        if name not in results:
            results[name] = check(f"robot/{name}", "error", "Required check did not run")
    return list(results.values())


def run_robot(suite, base_url, credentials_file, required):
    if importlib.util.find_spec("robot") is None:
        raise ValueError("Robot Framework is missing. Install requirements.txt first.")
    with TemporaryDirectory(prefix="cloudshield-robot-") as scratch:
        output = Path(scratch) / "output.xml"
        command = [sys.executable, "-m", "robot", "--output", str(output), "--log", "NONE",
                   "--report", "NONE", "--variable", f"BASE_URL:{base_url}",
                   "--variable", f"CREDENTIALS_FILE:{credentials_file}", str(suite)]
        process = subprocess.run(command, capture_output=True, timeout=90, check=False)
        if not 0 <= process.returncode <= 250 or not output.is_file():
            raise ValueError(f"Robot did not complete a usable run (exit {process.returncode})")
        results = parse_robot_report(output, required)
        if process.returncode and not any(result["status"] != "pass" for result in results):
            raise ValueError("Robot exit code contradicts the test report")
        return results


def source_revision(source_dir):
    def git(*args):
        try:
            result = subprocess.run(["git", "-C", str(source_dir), *args], capture_output=True,
                                    text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Could not identify the target Git checkout") from None
        if result.returncode:
            raise ValueError("Expected a Git checkout for the target source directory")
        return result.stdout.strip()

    revision = git("rev-parse", "--verify", "HEAD")
    if not REVISION.fullmatch(revision) or git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Use a clean target Git checkout so the results refer to a specific source revision")
    return revision


def verified_backend(compose_file, credentials_file, project, base_url, expected_revision):
    origin = urlsplit(base_url)
    if (origin.scheme != "http" or origin.hostname != "127.0.0.1" or origin.username is not None
            or origin.query or origin.fragment or origin.path not in {"", "/"} or not origin.port):
        raise ValueError("Expected a published loopback URL such as http://127.0.0.1:18000")
    backend = running_backend(collect_evidence(compose_file, credentials_file, project))
    if backend["source_revision"] != expected_revision:
        raise ValueError("Running backend image revision does not match the source checkout. Rebuild using the Phase 1 guide.")
    if origin.port not in backend["published_ports"]:
        raise ValueError("Target URL does not match the backend's published loopback port")
    return {key: backend[key] for key in ("container_id", "image_id", "source_revision")}


def test_profile(suite, required, python_version):
    return {
        "suite_sha256": hashlib.sha256(suite.read_bytes()).hexdigest(),
        "keywords_sha256": hashlib.sha256(Path(__file__).with_name("robot_keywords.py").read_bytes()).hexdigest(),
        "required_robot_checks": sorted(required),
        "python_version": python_version,
    }


def run_judge(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_object(manifest_path)
    fields = ("base_url", "source_dir", "environment_config", "credentials_file", "robot_suite", "python_version",
              "compose_file", "compose_project")
    for field in fields:
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"Manifest requires a nonempty {field}")
    required = manifest.get("required_robot_checks")
    if (not isinstance(required, list) or not required or
            not all(isinstance(name, str) and name.strip() for name in required) or
            len(required) != len(set(required))):
        raise ValueError("Manifest requires unique, nonempty Robot check names")

    def path_for(key):
        return (manifest_path.parent / manifest[key]).resolve()

    def skipped(reason):
        return [check(f"robot/{name}", "skip", reason) for name in required]

    source_dir = path_for("source_dir")
    config = read_object(path_for("environment_config"))
    results = check_python(source_dir, manifest["python_version"]) + validate_config(config)
    if any(result["status"] != "pass" for result in results):
        return {"checks": results + skipped("Static checks blocked runtime tests")}

    credentials = path_for("credentials_file")
    revision_args = (path_for("compose_file"), credentials, manifest["compose_project"], manifest["base_url"])
    try:
        revision = source_revision(source_dir)
        deployment = verified_backend(*revision_args, revision)
        runtime = read_backend_config(deployment["container_id"])
    except (OSError, ValueError) as exc:
        results.append(check("deployment/identity", "error", str(exc)))
        return {"checks": results + skipped("Deployment identity or settings could not be checked")}

    for result in compare(config, runtime, ()):
        result["id"] = "runtime/" + result["id"]
        results.append(result)
    suite = path_for("robot_suite")
    profile = None
    try:
        profile = test_profile(suite, required, manifest["python_version"])
        results.extend(run_robot(suite, manifest["base_url"], credentials, required))
    except (ValueError, OSError, subprocess.TimeoutExpired, ElementTree.ParseError) as exc:
        detail = str(exc) if type(exc) is ValueError else "Robot could not produce a usable report"
        results.append(check("robot/runner", "error", detail))
        results.extend(skipped("Robot run unavailable"))
    else:
        try:
            if test_profile(suite, required, manifest["python_version"]) != profile:
                raise ValueError("Test definitions changed during testing")
        except (OSError, ValueError):
            results.append(check("runner/test-profile", "error", "Could not confirm the test definitions stayed the same"))

    try:
        if source_revision(source_dir) != revision or verified_backend(*revision_args, revision) != deployment:
            raise ValueError("Source or backend changed during testing. Rerun the Judge.")
    except (OSError, ValueError) as exc:
        results.append(check("deployment/identity", "error", str(exc)))
    else:
        results.append(check("deployment/identity", "pass", "Source and backend identity match before and after testing"))
    return {"checks": results, "deployment": deployment, "test_profile": profile}


def judge_report(manifest):
    try:
        judged = run_judge(manifest)
    except (OSError, ValueError) as exc:
        judged = {"checks": [check("runner/input", "error", str(exc))]}
    report = summarize_checks("judge", judged["checks"])
    for field in ("deployment", "test_profile"):
        if judged.get(field):
            report[field] = judged[field]
    return report
