import asyncio
import copy

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.db import engine
from app.measurement import (
    MeasurementState, compare_documents, percentile, run_series, summarize, validate_run, write_outputs,
)
from app.observation import SQLMetrics, install_sql_events, request_sql
from app.seed import data_fingerprint


def test_percentile_interpolation_and_median():
    values = list(range(1, 31))
    assert percentile(values, 0.5) == 15.5
    assert percentile(values, 0.95) == pytest.approx(28.55)
    assert percentile(values, 1) == 30


@pytest.mark.parametrize("warmup,repeats", [(0, 30), (-1, 30), (5, 19)])
def test_invalid_series_is_rejected(warmup, repeats):
    with pytest.raises(ValueError):
        validate_run(warmup, repeats)


def test_warmup_and_state_preparation_are_not_timed():
    now = 0
    calls = 0
    cleanups = 0
    def prepare():
        nonlocal now
        now += 1_000_000_000
    def request(request_id):
        nonlocal now, calls
        calls += 1
        now += calls * 1_000_000
        return httpx.Response(200, json={"ok": True})
    def cleanup(response):
        nonlocal now, cleanups
        now += 2_000_000_000
        cleanups += 1
    rows = run_series("test", 5, 20, prepare, request, cleanup, 200, clock=lambda: now)
    assert len(rows) == 20
    assert calls == cleanups == 25
    assert [row["http_total_ms"] for row in rows] == list(range(6, 26))
    assert [row["repeat"] for row in rows] == list(range(1, 21))
    summary = summarize("test", rows)
    assert summary["p50_ms"] == 15.5
    assert summary["max_ms"] == 25


def test_cleanup_runs_if_http_status_is_wrong():
    cleaned = []
    with pytest.raises(RuntimeError):
        run_series("test", 1, 20, lambda: None,
                   lambda request_id: httpx.Response(409, text="conflict"),
                   lambda response: cleaned.append(response.status_code), 201)
    assert cleaned == [409]


def test_cleanup_runs_if_transport_raises():
    cleaned = []
    def request(request_id):
        raise httpx.ConnectError("test")
    with pytest.raises(httpx.ConnectError):
        run_series("test", 1, 20, lambda: None, request,
                   lambda response: cleaned.append(response), 200)
    assert cleaned == [None]


def test_diagnostic_uses_same_request_and_subtracts_times():
    now = 0
    def request(request_id):
        nonlocal now
        now += 10_000_000
        return httpx.Response(200, headers={
            "X-Measurement-Id": request_id, "X-SQL-Total-Ms": "3.5", "X-SQL-Query-Count": "2"
        })
    rows = run_series("test", 1, 20, lambda: None, request, lambda response: None,
                      200, diagnostic=True, clock=lambda: now)
    assert all(row["db_total_ms"] == 3.5 and row["db_query_count"] == 2
               and row["app_or_other_ms"] == 6.5 for row in rows)
    result = summarize("test", rows)
    assert result["http_total_ms"] == result["db_total_ms"] + result["app_or_other_ms"]


def test_missing_diagnostic_headers_are_rejected():
    with pytest.raises(RuntimeError, match="missing"):
        run_series("test", 1, 20, lambda: None, lambda _: httpx.Response(200),
                   lambda response: None, 200, diagnostic=True)


def test_sql_events_count_each_execute_and_are_not_installed_twice(database):
    install_sql_events(engine)
    install_sql_events(engine)
    metrics = SQLMetrics()
    token = request_sql.set(metrics)
    try:
        with database.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text("SELECT 2"))
    finally:
        request_sql.reset(token)
    assert metrics.query_count == 2
    assert metrics.total_ms > 0
    assert request_sql.get() is None


def test_failed_sql_execution_is_observed_without_changing_error(database):
    metrics = SQLMetrics()
    token = request_sql.set(metrics)
    try:
        with database.connect() as connection:
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT 1 / 0"))
    finally:
        request_sql.reset(token)
    assert metrics.query_count == 1
    assert metrics.total_ms > 0


def test_request_local_metrics_do_not_leak():
    async def isolated(count):
        metrics = SQLMetrics()
        token = request_sql.set(metrics)
        try:
            await asyncio.sleep(0)
            request_sql.get().query_count = count
            await asyncio.sleep(0)
            return metrics.query_count
        finally:
            request_sql.reset(token)
    async def check():
        return await asyncio.gather(isolated(3), isolated(7))
    assert asyncio.run(check()) == [3, 7]
    assert request_sql.get() is None


def test_observation_preserves_response_and_sql_statements(authorized):
    statements = []
    def record(connection, cursor, statement, params, context, executemany):
        statements.append((statement, params))
    event.listen(engine, "before_cursor_execute", record)
    try:
        normal = authorized.get("/api/services")
        ordinary_sql = list(statements)
        statements.clear()
        measured = authorized.get("/api/services", headers={
            "X-Measure-SQL": "1", "X-Measurement-Id": "test-request"
        })
        diagnostic_sql = list(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert normal.status_code == measured.status_code == 200
    assert normal.json() == measured.json()
    assert ordinary_sql == diagnostic_sql
    assert "x-sql-total-ms" not in normal.headers
    assert measured.headers["x-measurement-id"] == "test-request"
    assert int(measured.headers["x-sql-query-count"]) == 2
    assert float(measured.headers["x-sql-total-ms"]) > 0
    another = authorized.get("/api/services", headers={"X-Measure-SQL": "1"})
    assert int(another.headers["x-sql-query-count"]) == 2


def test_write_series_restore_data_and_sequence(database, client):
    state = MeasurementState(database, client)
    fingerprint = data_fingerprint(database)
    for name in ("login", "logout", "appointment_create", "appointment_cancel"):
        state.setup(name)
        operation = state.operation(name)
        ids = []
        for _ in range(3):
            state.prepare(name)
            response = client.request(operation.method, operation.path, json=operation.body)
            try:
                assert response.status_code == operation.expected_status
                if name == "appointment_create":
                    ids.append(response.json()["id"])
            finally:
                state.cleanup(name, response)
            assert data_fingerprint(database) == fingerprint
        if ids:
            assert len(set(ids)) == 1
    with database.connect() as connection:
        assert tuple(connection.execute(text(
            f"SELECT last_value, is_called FROM {state.sequence}"
        )).one()) == tuple(state.sequence_state)


def baseline(size, p50=10):
    return {
        "status": "complete", "data_unchanged": True, "size": size, "kind": "baseline",
        "warmup": 5, "repeats": 30, "conditions": {"same": True},
        "data_counts_before": {"appointments": 300 if size == "small" else 100000},
        "aggregated": [{"operation": "summary", "p50_ms": p50, "p95_ms": p50 + 1, "max_ms": p50 + 2}],
    }


def test_candidate_selection_requires_relative_and_absolute_growth():
    small = baseline("small", 10)
    working = baseline("working", 30)
    assert compare_documents(small, working)["candidates"] == ["summary"]
    small = baseline("small", 1)
    working = baseline("working", 3)
    assert compare_documents(small, working)["candidates"] == []


def test_incomparable_or_incomplete_results_are_rejected():
    small, working = baseline("small"), baseline("working")
    altered = copy.deepcopy(working)
    altered["conditions"] = {"other": True}
    with pytest.raises(ValueError, match="conditions"):
        compare_documents(small, altered)
    altered = copy.deepcopy(working)
    altered["data_unchanged"] = False
    with pytest.raises(ValueError):
        compare_documents(small, altered)


def test_output_contains_raw_csv_aggregate_csv_and_json(tmp_path):
    rows = [{"operation": "test", "repeat": i + 1, "request_id": str(i),
             "status_code": 200, "http_total_ms": float(i + 1), "response_bytes": 2}
            for i in range(20)]
    path = tmp_path / "test.json"
    write_outputs({"aggregated": [summarize("test", rows)], "raw": rows}, path)
    assert path.exists()
    assert (tmp_path / "test_raw.csv").read_text().count("\n") == 21
    assert "p95_ms" in (tmp_path / "test_summary.csv").read_text()
