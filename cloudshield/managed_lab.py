import json
import os
from pathlib import Path

from .candidate import PROJECT, write_json
from .checks import read_object, validate_config
from .evidence import IMAGE_ID, collect_evidence, deployment_config, docker_output, running_backend
from .verifier import identity, read_checks, required_checks, verify


def require_managed_lab(manifest, target):
    root = Path("runs/candidates").resolve() / target[2]
    compose = Path(target[0]).resolve()
    runner = Path(__file__).resolve().parent.parent
    trusted = {runner / "ci/compose.yml", runner.parent / "phase-1.compose.yml"}
    if (not PROJECT.fullmatch(target[2]) or root.resolve() != root or manifest.resolve() != root / "target.json"
            or Path(target[1]).resolve() != root / "candidate.env" or compose not in trusted):
        raise ValueError("Configuration recovery and promotion require a CloudShield-created disposable lab")
    metadata = read_object(root / "candidate.json")
    settings = read_object(manifest)
    if (metadata.get("project") != target[2] or settings.get("environment_config") != "expected.json"
            or settings.get("base_url") != f"http://127.0.0.1:{metadata.get('port')}"):
        raise ValueError("The managed lab manifest does not match its recorded project and port")


def compose_backend(target, root, image=None, *, drift=False):
    compose, credentials, project = target
    keys = {row.partition("=")[0].upper() for row in credentials.read_text(encoding="utf-8-sig").splitlines()}
    env = {key: value for key, value in os.environ.items() if key.upper() not in keys}
    command = ["compose", "--env-file", str(credentials), "-p", project, "-f", str(compose)]
    if image is not None:
        if not IMAGE_ID.fullmatch(image):
            raise ValueError("Expected an exact local backend image ID")
        tag = f"cloudshield-lab-release:{image.removeprefix('sha256:')}"
        docker_output(["image", "tag", image, tag], "tag verified lab image")
        output, _ = docker_output(["image", "inspect", "--format", "{{.Id}}", tag], "verify lab image tag")
        if output.strip() != image:
            raise ValueError("The lab release tag does not identify the intended image")
        overlay = root / "release.compose.json"
        service = {"image": tag}
        if drift:
            service["environment"] = {
                "DATABASE_URL": "postgresql://postgres:${POSTGRES_PASSWORD:?Required}@missing-db:5432/app",
            }
        write_json(overlay, {"services": {"backend": service}})
        command += ["-f", str(overlay)]
    return command, env


def approved_configuration(manifest, target, root):
    expected = read_object(manifest.parent / "expected.json")
    if any(row["status"] != "pass" for row in validate_config(expected)):
        raise ValueError("The lab has no valid approved configuration")
    command, env = compose_backend(target, root)
    rendered, _ = docker_output([*command, "config", "--format", "json"], "render lab configuration", env=env)
    try:
        backend = json.loads(rendered)["services"]["backend"]
        actual = deployment_config(backend["environment"]["DATABASE_URL"],
                                   backend["labels"]["io.cloudshield.database-secret-ref"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Could not validate the rendered database configuration") from None
    if actual != expected:
        raise ValueError("The Compose inputs do not render the approved settings. Recovery stopped.")


def replace_backend(target, root, image, *, drift=False):
    command, env = compose_backend(target, root, image, drift=drift)
    docker_output([*command, "up", "-d", "--no-deps", "--force-recreate", "--no-build", "--pull", "never",
                   "--wait", "--wait-timeout", "60", "backend"], "replace lab backend", env=env, timeout=90)


def current_backend(target):
    return identity(running_backend(collect_evidence(*target)))


def approved_image(manifest):
    settings = read_object(manifest)
    image = settings.get("managed_release")
    if image is None:
        image = read_object(manifest.parent / "deployment.json").get("deployment", {}).get("image_id")
    if not isinstance(image, str) or not IMAGE_ID.fullmatch(image):
        raise ValueError("The managed lab has no recorded approved image")
    return image


def reconcile_image(manifest, target, observed, wanted, revision, root, judge):
    require_managed_lab(manifest, target)
    approved_configuration(manifest, target, root)
    before = running_backend(collect_evidence(*target))
    if any(before.get(key) != observed.get(key) for key in ("container_id", "image_id", "source_revision")):
        raise ValueError("The deployment changed before image recovery")
    label, _ = docker_output(["image", "inspect", "--format",
                              '{{json (index .Config.Labels "org.opencontainers.image.revision")}}', wanted],
                             "verify approved image revision")
    if json.loads(label) != revision:
        raise ValueError("The approved image does not match the expected source revision")
    journal = root / "image-action.json"
    write_json(journal, {"state": "applying", "image_id": wanted})
    replace_backend(target, root, wanted)
    result = judge(manifest)
    after = result.get("deployment", {})
    if after.get("image_id") != wanted or after.get("source_revision") != revision:
        raise ValueError("The restored deployment did not identify as the approved image")
    write_json(journal, {"state": "checked", "deployment": after})
    return result


def reconcile_config(manifest, target, baseline, root, judge):
    require_managed_lab(manifest, target)
    approved_configuration(manifest, target, root)
    before = identity(baseline["deployment"])
    if current_backend(target) != before:
        raise ValueError("The deployment changed before configuration recovery")
    write_json(root / "configuration-action.json", {"state": "applying", "image_id": before["image_id"]})
    replace_backend(target, root, before["image_id"])
    result = judge(manifest)
    after = result.get("deployment", {})
    if after.get("image_id") != before["image_id"] or after.get("source_revision") != before["source_revision"]:
        raise ValueError("The configuration recovery did not preserve the original image and source revision")
    write_json(root / "configuration-action.json", {"state": "checked", "deployment": after})
    return result


def promote_repair(manifest, target, baseline, repair, root, judge):
    journal = root / "promotion.json"
    result = {"status": "handoff", "promotion": str(journal)}
    started = False
    try:
        require_managed_lab(manifest, target)
        approved_configuration(manifest, target, root)
        before = identity(baseline["deployment"])
        if current_backend(target) != before:
            raise ValueError("The running app changed before promotion")
        attempt = repair["attempts"][-1]
        if attempt.get("review", {}).get("decision") != "approve":
            raise ValueError("Automatic promotion requires an approved repair")
        folder = Path(repair["report"]).parent
        prefix = f"attempt-{attempt['number']}"
        candidate = read_object(folder / f"{prefix}-deployment.json")
        judged = read_object(folder / f"{prefix}-judge.json")
        verdict = verify(baseline, judged, candidate)
        if verdict["verdict"] != "verified" or verdict != read_object(folder / f"{prefix}-verdict.json"):
            raise ValueError("The verified candidate evidence does not match this deployment")
        wanted = verdict["candidate_deployment"]
        source = Path(candidate["worktree"]) / "backend/app"
        settings = read_object(manifest)
        verification = {**settings, "source_dir": str(source)}
        for key in ("compose_file", "credentials_file", "environment_config", "robot_suite"):
            verification[key] = str((manifest.parent / settings[key]).resolve())
        write_json(root / "promotion-target.json", verification)
        write_json(journal, {**result, "status": "applying", "previous": before, "candidate": wanted})
        started = True
        replace_backend(target, root, wanted["image_id"])
        after = judge(root / "promotion-target.json")
        write_json(root / "promotion-judge.json", after)
        live = identity(after.get("deployment"))
        checks = read_checks(after)
        if (after["overall"] != "pass" or live["image_id"] != wanted["image_id"]
                or live["source_revision"] != wanted["source_revision"] or current_backend(target) != live
                or after.get("test_profile") != judged.get("test_profile")
                or required_checks(after["test_profile"]) - checks.keys()
                or read_checks(judged).keys() - checks.keys() or set(checks.values()) != {"pass"}):
            raise ValueError("The promoted app did not pass verification on the expected image")
        temporary = manifest.with_suffix(".promotion.tmp")
        write_json(temporary, {**settings, "source_dir": str(source), "managed_release": live["image_id"]})
        temporary.replace(manifest)
        result.update(status="recovered", deployed_revision=live["source_revision"], deployment=live)
    except (OSError, ValueError, KeyboardInterrupt) as exc:
        result["detail"] = str(exc) or "Promotion interrupted"
        if isinstance(exc, KeyboardInterrupt):
            result["interrupted"] = True
        if started:
            try:
                replace_backend(target, root, before["image_id"])
                rollback = judge(manifest)
                write_json(root / "rollback-judge.json", rollback)
                restored = identity(rollback.get("deployment"))
                checked = read_checks(rollback)
                prior_passes = {key for key, value in read_checks(baseline).items() if value == "pass"}
                if (restored["source_revision"] != before["source_revision"] or restored["image_id"] != before["image_id"]
                        or not all(checked.get(key) == "pass" for key in prior_passes)):
                    raise ValueError("The previous image did not return to its prior tested state")
                result["status"] = "rolled_back"
            except (OSError, ValueError) as rollback_error:
                result.update(status="inconclusive", cleanup="failed", rollback_detail=str(rollback_error))
    write_json(journal, result)
    return result
