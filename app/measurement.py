"""Sequential HTTP measurements for HW1. No load generation or optimization."""
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter_ns
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, text, update

from app.config import settings
from app.models import Appointment, Slot
from app.seed import SIZES, data_counts, data_fingerprint

PERIOD = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
OPERATIONS = (
    "login", "logout", "services", "specialists", "free_slots", "appointments_list",
    "appointment_detail", "appointment_create", "appointment_cancel", "summary",
)
ROOT = Path(__file__).resolve().parent.parent
BUSINESS_FILES = ("app/services.py", "app/api.py", "app/models.py",
                  "app/schemas.py", "app/auth.py", "app/seed.py")


def percentile(values, fraction):
    """Linear interpolation at (n-1)*fraction; p50 equals ordinary median."""
    if not values or not 0 <= fraction <= 1:
        raise ValueError("Nonempty values and a fraction in [0, 1] required")
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def validate_run(warmup, repeats):
    if warmup < 1:
        raise ValueError("At least one warmup is required")
    if repeats < 20:
        raise ValueError("At least 20 measured repetitions are required")


def summarize(operation, rows):
    elapsed = [row["http_total_ms"] for row in rows]
    result = {
        "operation": operation, "repeats": len(rows),
        "p50_ms": percentile(elapsed, 0.50), "p95_ms": percentile(elapsed, 0.95),
        "max_ms": max(elapsed), "http_total_ms": statistics.mean(elapsed),
    }
    if "db_total_ms" in rows[0]:
        result.update({
            "db_total_ms": statistics.mean(row["db_total_ms"] for row in rows),
            "db_query_count": statistics.mean(row["db_query_count"] for row in rows),
            "db_query_count_min": min(row["db_query_count"] for row in rows),
            "db_query_count_max": max(row["db_query_count"] for row in rows),
            "app_or_other_ms": statistics.mean(row["app_or_other_ms"] for row in rows),
        })
    return result


def run_series(operation, warmup, repeats, prepare, request, cleanup,
               expected_status, diagnostic=False, clock=perf_counter_ns):
    validate_run(warmup, repeats)
    rows = []
    for index in range(warmup + repeats):
        response = None
        request_id = uuid4().hex
        try:
            prepare()
            started = clock()
            response = request(request_id)
            elapsed_ms = (clock() - started) / 1_000_000
            # Everything below, including validation and cleanup, is untimed.
            if response.status_code != expected_status:
                raise RuntimeError(f"{operation}: HTTP {response.status_code}: {response.text[:500]}")
            row = {"operation": operation, "repeat": index - warmup + 1,
                   "request_id": request_id, "status_code": response.status_code,
                   "http_total_ms": elapsed_ms, "response_bytes": len(response.content)}
            if diagnostic:
                if response.headers.get("x-measurement-id") != request_id:
                    raise RuntimeError("SQL observation is missing or belongs to another request")
                db_ms = float(response.headers["x-sql-total-ms"])
                count = int(response.headers["x-sql-query-count"])
                if not math.isfinite(db_ms) or db_ms < 0 or count < 0 or db_ms > elapsed_ms:
                    raise RuntimeError("Inconsistent SQL observation")
                row.update(db_total_ms=db_ms, db_query_count=count,
                           app_or_other_ms=elapsed_ms - db_ms)
            if index >= warmup:
                rows.append(row)
        finally:
            cleanup(response)
    return rows


@dataclass
class Operation:
    method: str
    path: str
    expected_status: int = 200
    params: dict | None = None
    body: dict | None = None


class MeasurementState:
    """Only benchmark setup/cleanup uses direct SQL, outside HTTP clocks."""
    def __init__(self, engine, client):
        self.engine = engine
        self.client = client
        self.marker = "measure-" + uuid4().hex + "@example.test"
        self.schema = engine.get_execution_options().get("schema_translate_map", {}).get(
            settings.schema, settings.schema
        )
        with engine.connect() as connection:
            self.cancel_id = connection.scalar(select(Appointment.id).where(
                Appointment.status == "booked").order_by(Appointment.id).limit(1))
            occupied = select(Appointment.id).where(
                Appointment.slot_id == Slot.id, Appointment.status == "booked"
            ).exists()
            self.free_slot_id = connection.scalar(select(Slot.id).where(
                ~occupied).order_by(Slot.id).limit(1))
            self.sequence = connection.scalar(text(
                "SELECT pg_get_serial_sequence(:table, 'id')"
            ), {"table": f'"{self.schema}"."appointments"'})
            # Sequence relation comes from PostgreSQL, not user input.
            self.sequence_state = connection.execute(text(
                f"SELECT last_value, is_called FROM {self.sequence}"
            )).one()
        if self.cancel_id is None or self.free_slot_id is None:
            raise ValueError("Seed must contain an active appointment and a free slot")

    def login(self):
        response = self.client.post("/api/auth/login", json={
            "login": settings.demo_login, "password": settings.demo_password
        })
        if response.status_code != 200 or response.json()["id"] != 1:
            raise RuntimeError("Cannot sign in as seeded demonstration operator")

    def setup(self, name):
        self.client.cookies.clear()
        if name not in ("login", "logout"):
            self.login()

    def prepare(self, name):
        if name == "login":
            self.client.cookies.clear()
        elif name == "logout":
            self.client.cookies.clear()
            self.login()
        elif name == "appointment_cancel":
            with self.engine.begin() as connection:
                connection.execute(update(Appointment).where(
                    Appointment.id == self.cancel_id
                ).values(status="booked", cancelled_at=None))

    def cleanup(self, name, response):
        if name in ("login", "logout"):
            self.client.cookies.clear()
        elif name == "appointment_create":
            with self.engine.begin() as connection:
                # Delete only rows with this run's unique marker, even on HTTP failures.
                connection.execute(delete(Appointment).where(
                    Appointment.client_contact == self.marker
                ))
                last_value, is_called = self.sequence_state
                connection.execute(text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
                                   {"sequence": self.sequence, "value": last_value, "called": is_called})
        elif name == "appointment_cancel":
            with self.engine.begin() as connection:
                connection.execute(update(Appointment).where(
                    Appointment.id == self.cancel_id
                ).values(status="booked", cancelled_at=None))

    def operation(self, name):
        operations = {
            "login": Operation("POST", "/api/auth/login", body={
                "login": settings.demo_login, "password": settings.demo_password}),
            "logout": Operation("POST", "/api/auth/logout", 204),
            "services": Operation("GET", "/api/services"),
            "specialists": Operation("GET", "/api/specialists"),
            "free_slots": Operation("GET", "/api/slots", params={
                **PERIOD, "service_id": 1, "page": 1, "size": 20}),
            "appointments_list": Operation("GET", "/api/appointments", params={"page": 1, "size": 20}),
            "appointment_detail": Operation("GET", f"/api/appointments/{self.cancel_id}"),
            "appointment_create": Operation("POST", "/api/appointments", 201, body={
                "slot_id": self.free_slot_id, "client_name": "Измерение ДЗ1", "client_contact": self.marker}),
            "appointment_cancel": Operation("POST", f"/api/appointments/{self.cancel_id}/cancel"),
            "summary": Operation("GET", "/api/summary", params=PERIOD),
        }
        return operations[name]


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in BUSINESS_FILES}


def conditions(engine, base_url):
    cpu_model = ""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    memory = ""
    if Path("/proc/meminfo").exists():
        memory = Path("/proc/meminfo").read_text().splitlines()[0]
    cgroups = {}
    for name in ("cpu.max", "memory.max"):
        path = Path("/sys/fs/cgroup") / name
        cgroups[name] = path.read_text().strip() if path.exists() else "unavailable"
    with engine.connect() as connection:
        postgres = connection.scalar(text("SELECT version()"))
        postgres_settings = {name: connection.scalar(text(f"SHOW {name}"))
                             for name in ("shared_buffers", "work_mem", "max_connections")}
    return {
        "base_url": base_url, "machine": platform.uname()._asdict(),
        "cpu_model": cpu_model, "logical_cpus": os.cpu_count(),
        "memory": memory, "cgroups": cgroups,
        "python": platform.python_version(),
        "packages": {name: importlib.metadata.version(name) for name in (
            "fastapi", "uvicorn", "SQLAlchemy", "psycopg", "Jinja2", "httpx")},
        "postgres": postgres, "postgres_settings": postgres_settings,
        "business_source_sha256": source_hashes(),
        "runner_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "observation_source_sha256": hashlib.sha256((ROOT / "app/observation.py").read_bytes()).hexdigest(),
        "account": settings.demo_login, "account_id": 1,
        "http_transport": "HTTPX HTTP/1.1, keep-alive, trust_env=False, fully read response",
        "client_location": "CLI process in the running application container",
        "concurrency": 1, "date_parameters": PERIOD,
        "clock": "time.perf_counter_ns",
        "percentile_method": "linear interpolation at (n-1)*p",
        "sql_scope": "before_cursor_execute to after_cursor_execute; includes driver/network; "
                     "excludes commit/rollback calls, ORM processing and later fetch work",
        "preparation": "Login/reset cookie, direct SQL restoration of mutation state and "
                       "appointment sequence are outside timed HTTP requests",
    }


SUMMARY_FIELDS = ("operation", "repeats", "p50_ms", "p95_ms", "max_ms", "http_total_ms",
                  "db_total_ms", "db_query_count", "db_query_count_min", "db_query_count_max",
                  "app_or_other_ms")
RAW_FIELDS = ("operation", "repeat", "request_id", "status_code", "http_total_ms",
              "response_bytes", "db_total_ms", "db_query_count", "app_or_other_ms")


def write_outputs(document, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    for suffix, fields, rows in (
        ("summary", SUMMARY_FIELDS, document["aggregated"]),
        ("raw", RAW_FIELDS, document["raw"]),
    ):
        with path.with_name(path.stem + "_" + suffix + ".csv").open(
                "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)


def print_table(rows):
    fields = ["operation", "repeats", "p50_ms", "p95_ms", "max_ms"]
    if rows and "db_total_ms" in rows[0]:
        fields += ["http_total_ms", "db_total_ms", "db_query_count", "app_or_other_ms"]
    print(" | ".join(f"{name:>20}" for name in fields))
    for row in rows:
        print(" | ".join(f"{row[name]:20.3f}" if isinstance(row[name], float)
                         else f"{row[name]:>20}" for name in fields), flush=True)


def measure(engine, size, warmup=5, repeats=30, base_url="http://127.0.0.1:8080",
            operations=None, diagnostic=False, output=None):
    validate_run(warmup, repeats)
    names = list(operations or OPERATIONS)
    if len(names) != len(set(names)) or any(name not in OPERATIONS for name in names):
        raise ValueError("Unknown or duplicate operations")
    counts = data_counts(engine)
    if counts != SIZES[size]:
        raise ValueError(f"Run seed --size {size} first; actual counts={counts}")
    initial_fingerprint = data_fingerprint(engine)
    started = datetime.now(timezone.utc)
    output = Path(output or ROOT / "measurements" /
                  f'{size}_{started.strftime("%Y%m%dT%H%M%S")}_{uuid4().hex[:6]}_'
                  f'{"diagnostic" if diagnostic else "baseline"}.json')
    document = {
        "format_version": 1, "kind": "diagnostic" if diagnostic else "baseline",
        "size": size, "warmup": warmup, "repeats": repeats,
        "started_at_utc": started.isoformat(), "data_counts_before": counts,
        "data_fingerprint_before": initial_fingerprint, "conditions": conditions(engine, base_url),
        "scenario": {}, "aggregated": [], "raw": [], "status": "running",
    }
    try:
        with httpx.Client(base_url=base_url, timeout=120, trust_env=False,
                          follow_redirects=False) as client:
            state = MeasurementState(engine, client)
            for name in names:
                state.setup(name)
                operation = state.operation(name)
                # Save reproducible parameters, but never credentials or cookie contents.
                document["scenario"][name] = {
                    "method": operation.method, "path": operation.path,
                    "params": operation.params, "expected_status": operation.expected_status,
                    "body_fields": sorted(operation.body) if operation.body else [],
                    "slot_id": state.free_slot_id if name == "appointment_create" else None,
                }
                def request(request_id):
                    headers = {"X-Measurement-Id": request_id}
                    if diagnostic:
                        headers["X-Measure-SQL"] = "1"
                    return client.request(operation.method, operation.path, params=operation.params,
                                          json=operation.body, headers=headers)
                rows = run_series(
                    name, warmup, repeats, lambda: state.prepare(name), request,
                    lambda response: state.cleanup(name, response),
                    operation.expected_status, diagnostic,
                )
                document["raw"].extend(rows)
                aggregated = summarize(name, rows)
                document["aggregated"].append(aggregated)
                print_table([aggregated])
                write_outputs(document, output)
        document["data_counts_after"] = data_counts(engine)
        document["data_fingerprint_after"] = data_fingerprint(engine)
        document["data_unchanged"] = (
            document["data_counts_after"] == counts and
            document["data_fingerprint_after"] == initial_fingerprint
        )
        if not document["data_unchanged"]:
            raise RuntimeError("Dataset changed during measurement; results cannot be accepted")
        document["status"] = "complete"
    except Exception as error:
        document["status"] = "failed"
        document["error"] = str(error)
        raise
    finally:
        document["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_outputs(document, output)
        print("Results:", output, flush=True)
    return document


def compare_documents(small, working, growth_threshold=2.0, min_delta_ms=5.0):
    for document, size in ((small, "small"), (working, "working")):
        if document.get("status") != "complete" or not document.get("data_unchanged"):
            raise ValueError("Only complete measurements with unchanged datasets can be compared")
        if document["size"] != size or document["kind"] != "baseline":
            raise ValueError("Expected SMALL and WORKING baseline results")
    if small["conditions"] != working["conditions"]:
        raise ValueError("Machine, runtime, account, code or measurement conditions differ")
    if (small["warmup"], small["repeats"]) != (working["warmup"], working["repeats"]):
        raise ValueError("Warmup or repetition count differs")
    left = {row["operation"]: row for row in small["aggregated"]}
    right = {row["operation"]: row for row in working["aggregated"]}
    if left.keys() != right.keys():
        raise ValueError("Operation sets differ")
    rows = []
    for name, first in left.items():
        second = right[name]
        growth = second["p50_ms"] / first["p50_ms"]
        delta = second["p50_ms"] - first["p50_ms"]
        rows.append({
            "operation": name, "small_p50_ms": first["p50_ms"],
            "small_p95_ms": first["p95_ms"], "small_max_ms": first["max_ms"],
            "working_p50_ms": second["p50_ms"], "working_p95_ms": second["p95_ms"],
            "working_max_ms": second["max_ms"], "p50_growth": growth,
            "p95_growth": second["p95_ms"] / first["p95_ms"], "p50_delta_ms": delta,
            "candidate": growth >= growth_threshold and delta >= min_delta_ms,
        })
    return {"format_version": 1, "criterion": {
        "p50_growth_at_least": growth_threshold, "p50_delta_ms_at_least": min_delta_ms,
        "meaning": "Transparent descriptive selection rule, not a statistical significance test",
    }, "rows": rows, "candidates": [row["operation"] for row in rows if row["candidate"]],
        "operational_table_growth": working["data_counts_before"]["appointments"] /
                                    small["data_counts_before"]["appointments"]}


def compare_files(small_path, working_path, output, growth_threshold=2.0, min_delta_ms=5.0):
    result = compare_documents(json.loads(Path(small_path).read_text()),
                               json.loads(Path(working_path).read_text()),
                               growth_threshold, min_delta_ms)
    result["small_file"] = str(small_path)
    result["working_file"] = str(working_path)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with path.with_suffix(".csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, list(result["rows"][0]))
        writer.writeheader()
        writer.writerows(result["rows"])
    for row in result["rows"]:
        print(f'{row["operation"]:22} p50 x{row["p50_growth"]:.3f}, '
              f'delta {row["p50_delta_ms"]:.3f} ms, candidate={row["candidate"]}')
    print("Candidates:", ",".join(result["candidates"]))
    return result
