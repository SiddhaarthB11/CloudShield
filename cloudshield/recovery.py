from time import monotonic, sleep

from .evidence import CONTAINER_ID, collect_evidence, docker_output


def lab_services(containers):
    services = {}
    for row in containers:
        name = row["service"]
        if name in {"db", "backend"}:
            if name in services:
                raise ValueError("Recovery requires exactly one container per lab service")
            services[name] = row
    return services


def plan_service_recovery(containers, allowed):
    services = lab_services(containers)
    if services.keys() != {"db", "backend"}:
        return []
    database = services["db"]
    if database["state"] != "exited" and (database["state"], database.get("health")) != ("running", "healthy"):
        return []
    if database["state"] == "exited" and "db" not in allowed:
        return []
    return [{"service": name, "container_id": services[name]["container_id"]}
            for name in ("db", "backend") if name in allowed and services[name]["state"] == "exited"]


def start_existing_services(target, plan, expected_revision, record):
    for action in plan:
        name, container = action["service"], action["container_id"]
        services = lab_services(collect_evidence(*target)["containers"])
        current = services.get(name, {})
        if (not CONTAINER_ID.fullmatch(container) or current.get("container_id") != container
                or current.get("state") != "exited"
                or services.get("backend", {}).get("source_revision") != expected_revision):
            raise ValueError("Deployment changed after the recovery plan was recorded")
        database = services.get("db", {})
        if name == "backend" and (database.get("state"), database.get("health")) != ("running", "healthy"):
            raise ValueError("The backend depends on a healthy lab database")
        action["status"] = "starting"
        record()
        docker_output(["start", container], f"start existing lab {name} container")
        deadline = monotonic() + 60
        while True:
            services = lab_services(collect_evidence(*target)["containers"])
            current = services.get(name, {})
            if current.get("container_id") != container or current.get("state") in {"exited", "dead"}:
                raise ValueError(f"The {name} container stopped or changed during recovery")
            if (current.get("state"), current.get("health")) == ("running", "healthy"):
                action["status"] = "healthy"
                record()
                break
            if monotonic() >= deadline:
                raise ValueError(f"The {name} container did not become healthy within 60 seconds")
            sleep(2)
