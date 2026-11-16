"""Run one stage against the private DZ2 Compose project, and preserve its evidence."""

import argparse
import fcntl
import json
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

LOAD = Path(__file__).resolve().parent
ROOT = LOAD.parent
PROJECT = "sergey_rodionov_dz2"
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(LOAD / "compose.yaml")]
PSQL = ["exec", "-T", "db", "psql", "-XAt", "-v", "ON_ERROR_STOP=1",
        "-U", "sergey_rodionov", "-d", "sergey_rodionov"]
APPLY = ["exec", "-T", "app", "python", "-m", "app.cli"]
CONNECTIONS = """
SELECT json_build_object('total', count(*), 'active', count(*) FILTER (WHERE state = 'active'))
FROM pg_stat_activity
WHERE datname = current_database() AND backend_type = 'client backend'
  AND pid <> pg_backend_pid();
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def compose(*args, **kwargs):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, **kwargs)


def container_id(service):
    result = compose("ps", "--status", "running", "-q", service, capture_output=True, text=True)
    container = result.stdout.strip()
    if not container or "\n" in container:
        raise RuntimeError(f"Expected one running {service} container in {PROJECT}")
    return container


def require_separate_db(container):
    raw = subprocess.check_output(["docker", "inspect", container], text=True)
    db = json.loads(raw)[0]
    safe_mount = any(
        mount.get("Name") == f"{PROJECT}_dz2_data"
        and mount.get("Destination") == "/var/lib/postgresql/data"
        for mount in db["Mounts"]
    )
    if not safe_mount:
        raise RuntimeError("Refusing seed: PostgreSQL is not using the dedicated DZ2 volume")


def wait_until_idle(app, db):
    consecutive = 0
    for _ in range(18):
        raw = subprocess.check_output(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", app, db], text=True,
            timeout=10,
        )
        values = [float(value.removesuffix("%")) for value in raw.split()]
        consecutive = consecutive + 1 if len(values) == 2 and max(values) <= 5 else 0
        if consecutive == 2:
            return
    raise RuntimeError("App and database did not return to idle (two samples at or below 5% CPU)")


def db_samples(file, stop):
    with file.open("x", encoding="utf-8") as stream, file.with_suffix(".log").open("x") as errors:
        while not stop.is_set():
            start = time.monotonic()
            timestamp = utc_now()
            try:
                result = compose(*PSQL, "-c", CONNECTIONS, capture_output=True, text=True, timeout=4)
                sample = json.loads(result.stdout)
                print(json.dumps({"time_utc": timestamp, **sample}), file=stream, flush=True)
            except (subprocess.SubprocessError, ValueError) as error:
                print(f"{timestamp} {error}", file=errors, flush=True)
            stop.wait(max(0, 1.0 - (time.monotonic() - start)))


def docker_samples(process, file):
    decoder = json.JSONDecoder()
    with file.open("x", encoding="utf-8") as stream:
        for line in process.stdout:
            start = line.find("{")
            if start < 0:
                continue
            sample, _ = decoder.raw_decode(line[start:])
            print(json.dumps({"time_utc": utc_now(), **sample}), file=stream, flush=True)


@contextmanager
def resource_sampling(app, db, directory):
    stop = threading.Event()
    with (directory / "docker-stats.log").open("x") as errors, subprocess.Popen(
        ["docker", "stats", "--format", "{{json .}}", app, db],
        stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
    ) as stats:
        docker_thread = threading.Thread(target=docker_samples, args=(stats, directory / "docker.jsonl"))
        db_thread = threading.Thread(target=db_samples, args=(directory / "postgres.jsonl", stop))
        docker_thread.start()
        db_thread.start()
        try:
            yield
        finally:
            stop.set()
            stats.terminate()
            docker_thread.join()
            db_thread.join()


def run_k6(args, directory, metadata):
    command = COMPOSE + [
        "run", "--rm", "-T", "--no-deps", "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{directory}:/run-data",
        "-e", f"MODE={args.mode}", "-e", f"RATE={args.rate}", "-e", f"SECONDS_PER_STEP={args.seconds}",
    ]
    if args.vus:
        command += ["-e", f"VUS={args.vus}"]
    command += ["k6", "run", "--no-color", "--quiet", "--out", "json=/run-data/k6.jsonl",
                "--summary-export", "/run-data/summary.json", "/load/step.js"]
    with (directory / "k6.txt").open("x") as log, subprocess.Popen(
        command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
    ) as run:
        profile_status = None
        if args.profile:
            time.sleep(5)
            if run.poll() is not None:
                raise RuntimeError("k6 exited before profiling could start")
            metadata["profile_start_utc"] = utc_now()
            with (directory / "py-spy.log").open("x") as profile_log:
                profiler = subprocess.run(
                    COMPOSE + ["run", "--rm", "-T", "--no-deps", "-v", f"{directory}:/run-data",
                               "profiler", "record", "--pid", "1", "--duration", str(args.profile),
                               "--nonblocking", "--function", "--format", "raw", "--output", "/run-data/profile.txt"],
                    cwd=ROOT, stdout=profile_log, stderr=subprocess.STDOUT,
                )
                profile_status = profiler.returncode
            metadata["profile_finish_utc"] = utc_now()
        return run.wait(), profile_status


def run_stage(args, results):
    app = container_id("app")
    db = container_id("db")
    require_separate_db(db)
    wait_until_idle(app, db)
    directory = results / args.name
    directory.mkdir()
    with (directory / "seed.txt").open("x") as log:
        compose(*APPLY, "init-db", stdout=log, stderr=subprocess.STDOUT)
        compose(*APPLY, "seed", "--size", "working", stdout=log, stderr=subprocess.STDOUT)
    if args.mode != "heavy":
        with (LOAD / "fixtures.sql").open() as query, (directory / "fixtures.json").open("x") as output:
            compose(*PSQL, stdin=query, stdout=output)
    wait_until_idle(app, db)
    metadata = {
        "project": PROJECT, "mode": args.mode, "offered_rps": args.rate if args.mode != "smoke" else None,
        "duration_seconds": args.seconds if args.mode != "smoke" else None,
        "configured_vus": 1 if args.mode == "smoke" else (args.vus or max(20, args.rate * 8)),
        "profile_seconds": args.profile,
        "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)),
        "app_image": subprocess.check_output(["docker", "inspect", "-f", "{{.Image}}", app], text=True).strip(),
        "app_command": json.loads(subprocess.check_output(
            ["docker", "inspect", "-f", "{{json .Config.Cmd}}", app], text=True
        )),
    }
    with resource_sampling(app, db, directory):
        metadata["start_utc"] = utc_now()
        status, profile_status = run_k6(args, directory, metadata)
        metadata["finish_utc"] = utc_now()
    metadata.update(k6_exit_code=status, profile_exit_code=profile_status)
    (directory / "stage.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    with (directory / "app.txt").open("x") as log:
        compose("logs", "--no-color", "--timestamps", "--since", metadata["start_utc"],
                "app", stdout=log, stderr=subprocess.STDOUT)
    telemetry = ("docker.jsonl", "postgres.jsonl")
    for name in telemetry:
        if (directory / name).stat().st_size == 0:
            raise RuntimeError(f"Missing resource samples in {directory / name}")
    print(f"{PROJECT}: {directory.relative_to(ROOT)}, k6={status}, py-spy={profile_status}", flush=True)
    if profile_status not in (None, 0):
        return profile_status
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("smoke", "mixed", "heavy"))
    parser.add_argument("name", help="New directory name under load/results")
    parser.add_argument("--rate", type=int, default=1)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--vus", type=int)
    parser.add_argument("--profile", type=int, help="Seconds of py-spy recording during the heavy stage")
    args = parser.parse_args()
    if not args.name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in args.name):
        parser.error("name must contain only lowercase Latin letters, digits, hyphens and underscores")
    if args.rate < 1 or args.seconds < 1 or (args.vus is not None and args.vus < 1):
        parser.error("rate, seconds and vus must be positive")
    if args.profile is not None and (args.mode != "heavy" or not 10 <= args.profile <= args.seconds - 10):
        parser.error("profiling is available only in heavy mode and needs 10 s plus 10 s for startup")
    results = LOAD / "results"
    results.mkdir(exist_ok=True)
    with (results / ".stage.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run_stage(args, results)


if __name__ == "__main__":
    raise SystemExit(main())
