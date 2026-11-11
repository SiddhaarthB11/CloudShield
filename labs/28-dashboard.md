# Phase 28 — A dashboard over the actual repair engine

## What we built and why

The UI follows the QCerebrum control panel's interaction pattern: Judge, Autofix, Plant 5, watch, progress/state and saved outcomes. CloudShield's implementation is independent: static HTML/CSS/JavaScript, a standard-library Python HTTP server, SQLite state, an independent background worker and separate job processes. It calls the existing CLI instead of implementing another fixer in the browser.

The HTTP server binds only to 127.0.0.1. The browser receives a local session cookie. Mutations require its exact local Origin and a request header. Host checks prevent DNS rebinding. Requests have a bounded body and named action allowlist; the API does not accept arbitrary shell commands. Evidence reading is restricted to known JSON report names under runs/. Secrets and sensitive lines are redacted before entering dashboard output. The dashboard is not an Internet-facing, multi-user service.

SQLite retains connections, policies, scheduled triggers and job history. The worker is separate from the web server and browser. A separate database lock allows only one worker; jobs run serially so candidate ports do not collide. One active job per target is enforced by a database index. After a worker restart, a surviving child blocks another job until it exits. A dead interrupted job records attention and pauses scheduling, rather than silently rerunning an uncertain deployment operation.

## Start it

From the CloudShield folder:

```powershell
./start-dashboard.ps1
```

Open `http://127.0.0.1:18700`. Alternatively run `& ./.venv/Scripts/python.exe -m cloudshield serve`.

Connect `examples/target.json` as the original local FastAPI subject. Initially use observation only. Click Run Judge and inspect Details. Missing Docker access, an unhealthy app or an unmatched source revision must be visible as a failure/inconclusive result. Installed capability badges are not proof of connectivity.

For a fully managed local demonstration:

1. On Self-healing, click Create managed demo target. This prepares an independent Git worktree, credentials and manifest on port 18012.
2. After preparation, Connect application and select the newly generated `runs/candidates/<project>/target.json` from that job's result. Give it a name.
3. Enable Gemini, stopped-service recovery and managed-demo recovery in this target's policy. Save it, then click Start selected demo target.
4. Run the Judge. It must pass before demonstrating recovery.
5. Turn Monitor on. It checks the running deployment and, within policy, starts stopped services, restores approved image/configuration or proposes/reviews/verifies/promotes a supported code repair. Source watching is separate: it examines changed local source snapshots without overwriting your open files.
6. In Fault lab, Plant 5 or select a scenario. These experiments use independent disposable targets. Their final `matched` status means expected behavior occurred, which can include a deliberately rejected regression or handoff. It does not mean every scenario healed.

The original phase-1 target supports observations and reviewed proposals. Automatic configuration/image restoration and code promotion are restricted to generated managed demo targets. A verified patch by itself is not a release.

For AWS, deploy/enroll with lab 26, connect its generated manifest and use Cloud connections. Enable the registered manifest's restoration policy in lab 27. The monitoring toggle schedules the ECS adapter instead of local Docker commands. App-specific tests and adapters still determine what an application supports; registering a new URL alone cannot supply a safe repair strategy.

## Understand the controls

* **Run Judge:** observes the selected local deployment.
* **Start AI repair:** runs up to three scoped attempts with an independent review and exact candidate verification. It requires AI permission and approved source files. Its verified result does not itself deploy to the original target.
* **Check & heal:** runs one monitor cycle using the selected recovery policy. Managed policy can promote a verified runtime repair with rollback.
* **Monitor:** schedules another observation after the previous check finishes plus the configured interval; it never launches overlapping checks.
* **Watch source:** fingerprints the local source every three seconds and queues a check when it changes. The watcher settles the snapshot before testing it. It can repair syntax in an isolated candidate; it does not rewrite your editor buffer.
* **Performance gate:** adds repeated JMeter checks to the eligible repair path. Requires Java and an extracted JMeter installation in JMETER_HOME. A lightweight default monitor does not load-test continuously unless this is configured.
* **Pause all automation:** cancels queued work and disables triggers. An active repair finishes verification/rollback/cleanup, then the worker exits. It does not kill a container operation midway.
* **Start/reconnect worker:** starts a worker if necessary. A second worker cannot acquire the lock. Paused monitoring remains off until explicitly re-enabled.

Unknown failures and unsuccessful jobs pause that target. This makes an exhausted repair budget visible and prevents an endless costly loop. Existing incident deduplication still applies inside the local monitor.

## Persistence lab

Run a check, refresh the browser, and confirm the same job remains. Close/reopen the page and confirm results remain. Restart the HTTP server and confirm the worker continues. Use Pause all automation before ending a demonstration. The state lives under `runs/dashboard/` and is ignored by Git.

To keep the worker and dashboard alive after host reboot on a dedicated Linux machine, templates are provided in `deploy/`. Adjust `/opt/cloudshield` and the `cloudshield` account to match your host, install the same project/Python dependencies and provision that account's authorized Docker/AWS access. Install the systemd units through your host's normal administration workflow. The worker and web service are separate. Keep the UI bound to loopback and access it with an SSH local-forward; no public dashboard port or cloud hosting is provisioned by these templates. Windows launch keeps the worker alive across browser/server restarts, but is not a Windows startup-service installer.

## Interview explanation

“CloudShield is an external testing and repair orchestrator. An adapter connects it to the deployment, tests collect evidence, and policy routes a failure to a scoped code repair or an approved operational recovery. Code changes receive independent AI review and are tested in disposable environments before promotion. The dashboard shows persistent jobs and evidence. Docker Compose is the demonstrated application integration; ECS adds approved task restoration. Other applications need their tests and adapter configuration.”

Be explicit about the difference between unit-tested logic, a successful local end-to-end demonstration, and a live AWS demonstration. Never describe an unrun cloud lab as proven.
