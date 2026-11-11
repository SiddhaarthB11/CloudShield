import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .candidate import git


def memory_key(source, baseline):
    repository = git(source, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    failed = sorted(row["id"] for row in baseline["checks"] if row["status"] == "fail")
    return hashlib.sha256(json.dumps([repository, baseline["deployment"]["source_revision"], failed]).encode()).hexdigest()


def repair_history(key, attempt=None):
    path = Path("runs/repairs/memory.sqlite3")
    with closing(sqlite3.connect(path, timeout=10)) as database, database:
        database.execute("CREATE TABLE IF NOT EXISTS rejected (incident TEXT, digest TEXT, detail TEXT, "
                         "PRIMARY KEY (incident, digest))")
        if attempt is not None:
            feedback = {field: attempt[field] for field in ("outcome", "regressions", "unresolved_targets")
                        if field in attempt}
            feedback["review_reason"] = attempt.get("review", {}).get("reason", "")[:500]
            database.execute("INSERT OR REPLACE INTO rejected VALUES (?, ?, ?)",
                             (key, attempt["patch_sha256"], json.dumps(feedback)))
        return {digest: json.loads(detail) for digest, detail in database.execute(
            "SELECT digest, detail FROM rejected WHERE incident = ? ORDER BY rowid DESC LIMIT 5", (key,))}
