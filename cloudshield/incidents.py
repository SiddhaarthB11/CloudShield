def diagnose(checks, containers=()):
    """Group confirmed failures, with dependency evidence taking priority."""
    failed = sorted(row["id"] for row in checks if row["status"] in {"fail", "connectivity"})
    services = {row["service"]: row for row in containers}
    if services:
        for name in ("db", "backend"):
            service = services.get(name, {})
            if (service.get("state"), service.get("health")) != ("running", "healthy"):
                return {"kind": "deployment", "cause": f"{name}_unavailable", "checks": failed,
                        "service": name, "observed_state": service.get("state")}
    groups = (
        ("configuration", ("config/", "runtime/config/")),
        ("deployment", ("deployment/",)),
        ("syntax", ("syntax/",)),
        ("runtime", ("robot/",)),
        ("performance", ("performance/",)),
    )
    for kind, prefixes in groups:
        relevant = [name for name in failed if name.startswith(prefixes)]
        if relevant:
            return {"kind": kind, "cause": f"{kind}_checks_failed", "checks": relevant,
                    "dependent_checks": [name for name in failed if name not in relevant]}
    return {"kind": "unknown" if failed or any(row["status"] != "pass" for row in checks) else "healthy",
            "checks": failed}
