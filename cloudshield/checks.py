import ast
import json
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path


def read_object(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Invalid JSON number: {value}")

    with Path(path).open(encoding="utf-8-sig") as stream:
        value = json.load(stream, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def check(check_id, status, detail):
    return {"id": check_id, "status": status, "detail": detail}


def summarize_checks(command, results):
    counts = Counter(result["status"] for result in results)
    if counts["error"] or counts["connectivity"]:
        overall = "inconclusive"
    elif counts["fail"]:
        overall = "fail"
    elif counts["skip"] or not counts["pass"]:
        overall = "inconclusive"
    else:
        overall = "pass"
    return {"command": command, "overall": overall, "counts": dict(counts), "checks": results}


def compare(reference, candidate, allowed):
    results = []
    for key in sorted(reference.keys() | candidate.keys()):
        if key not in reference:
            status, detail = "fail", "Unexpected key"
        elif key not in candidate:
            status, detail = "fail", "Missing key"
        elif type(reference[key]) is not type(candidate[key]):
            status, detail = "fail", "Type differs from reference"
        elif json.dumps(reference[key], sort_keys=True) == json.dumps(candidate[key], sort_keys=True):
            status, detail = "pass", "Matches reference"
        elif key in allowed:
            status, detail = "pass", "Expected value difference"
        else:
            status, detail = "fail", "Unexpected value difference"
        results.append(check(f"config/{key}", status, detail))
    return results


CONFIG_TYPES = {"DB_HOST": str, "DB_PORT": int, "DB_NAME": str, "DB_SECRET_REF": str}


def validate_config(config):
    results = []
    for key, expected in CONFIG_TYPES.items():
        value = config.get(key)
        valid = type(value) is expected
        if valid and expected is str:
            valid = bool(value.strip())
        if valid and key == "DB_PORT":
            valid = 1 <= value <= 65535
        detail = "Valid" if valid else f"Expected a valid {expected.__name__} value"
        results.append(check(f"config/{key}", "pass" if valid else "fail", detail))
    return results


def check_python(root, expected_version=None):
    if expected_version is not None:
        if not isinstance(expected_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", expected_version):
            raise ValueError("Expected Python version in major.minor format")
        actual = f"{sys.version_info.major}.{sys.version_info.minor}"
        if expected_version != actual:
            return [check("syntax/interpreter", "error", f"Target requires Python {expected_version}; runner is Python {actual}")]
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"Python source directory does not exist: {root}")
    paths = sorted(path for path in root.rglob("*.py") if path.is_file() and not path.is_symlink())
    if not paths:
        raise ValueError(f"No Python files found in {root}")
    results = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        try:
            with tokenize.open(path) as stream:
                tree = ast.parse(stream.read(), filename=name)
            compile(tree, name, "exec")
        except (SyntaxError, UnicodeError, ValueError) as exc:
            line = getattr(exc, "lineno", None)
            detail = f"Invalid Python at line {line}" if line else "Invalid Python source or encoding"
            row = check(f"syntax/{name}", "fail", detail)
            if isinstance(exc, SyntaxError):
                row.update(line=line, compiler_message=exc.msg[:240])
            results.append(row)
        except OSError:
            results.append(check(f"syntax/{name}", "error", "Could not read the file"))
        else:
            results.append(check(f"syntax/{name}", "pass", "Compiles without executing app code"))
    return results
