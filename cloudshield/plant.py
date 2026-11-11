import random
import secrets
from collections import Counter
from pathlib import Path
from uuid import uuid4

from .candidate import validate_candidate_port, write_json
from .faults import CATALOG, CODE_FAULTS, run_fault


def draw(seed, include_performance=False):
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Choose an integer seed from 0 to 4294967295")
    rng = random.Random(seed)
    pool = CATALOG.keys() if include_performance else CATALOG.keys() - {"latency"}
    return rng.sample(sorted(pool), 5)


def plant(repo, emit, *, seed=None, port=18007, repair_port=18008, proposer=None, reviewer=None, jmeter_home=None):
    if proposer is not None and reviewer is None:
        raise ValueError("Plant 5 Breaks requires a Reviewer for AI repair")
    for value in (port, repair_port):
        validate_candidate_port(value)
    if port == repair_port:
        raise ValueError("Use different ports for the fault and repair deployments")

    seed = secrets.randbits(32) if seed is None else seed
    selected = draw(seed, include_performance=jmeter_home is not None)
    root = Path("runs", "plant", uuid4().hex[:12]).resolve()
    root.mkdir(parents=True)
    path = root / "plant.json"
    results = [{"scenario": name, "status": "pending"} for name in selected]
    report = {"command": "plant", "status": "running", "seed": seed, "selection": selected, "catalog_version": 2,
              "include_performance": jmeter_home is not None,
              "repair_mode": "gemini" if proposer is not None else "known_patch", "scenarios": results,
              "report": str(path)}

    def save():
        scratch = path.with_suffix(".tmp")
        write_json(scratch, report)
        scratch.replace(path)

    save()
    try:
        emit(f"Seed {seed}. Selected: {', '.join(selected)}")
        for index, row in enumerate(results, 1):
            name = row["scenario"]
            row["status"] = "running"
            save()
            emit(f"[{index}/{len(results)}] Running {name}...")
            options = {"proposer": proposer, "reviewer": reviewer} if name in {*CODE_FAULTS, "syntax"} else {}
            if name == "latency":
                options["jmeter_home"] = jmeter_home
            result = run_fault(name, repo, port, repair_port, heal=True, **options)
            row.update({key: result[key] for key in ("status", "outcome", "report", "observed", "detail")
                        if key in result})
            if result["status"] == "matched" and name in {"syntax", "startup"}:
                row["initially_blocked_checks"] = ["robot/*"]
            save()
            emit(f"[{index}/{len(results)}] {name}: {row['status']} ({row.get('outcome', 'no outcome')})")
            if result.get("interrupted"):
                raise KeyboardInterrupt
            if result.get("cleanup") == "failed" or result.get("repair_cleanup") == "failed":
                report["detail"] = "A lab deployment could not be cleaned up. The remaining faults were skipped."
                break
    except KeyboardInterrupt:
        report["detail"] = "Plant 5 Breaks was interrupted."
        report["status"] = "interrupted"
    except (OSError, ValueError) as exc:
        report["detail"] = str(exc)
        report["status"] = "inconclusive"
    finally:
        counts = Counter()
        for row in results:
            if row["status"] == "running":
                row["status"] = "inconclusive"
            if row["status"] == "pending":
                row["status"] = "not_run"
            counts[row["outcome"] if row["status"] == "matched" else row["status"]] += 1
        report["counts"] = dict(counts)
        if report["status"] == "running":
            statuses = {row["status"] for row in results}
            report["status"] = ("inconclusive" if statuses & {"inconclusive", "not_run"}
                                else "unexpected" if "unexpected" in statuses else "matched")
        save()
    return report
