"""Request-local SQL observation. Statements and parameters are never modified."""
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter_ns

from sqlalchemy import event


@dataclass
class SQLMetrics:
    total_ms: float = 0.0
    query_count: int = 0


request_sql: ContextVar[SQLMetrics | None] = ContextVar("request_sql", default=None)


def before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
    metrics = request_sql.get()
    if metrics is not None:
        metrics.query_count += 1
        context._oksp_sql_clock = (metrics, perf_counter_ns())


def finish_execution(context):
    record = getattr(context, "_oksp_sql_clock", None)
    if record is not None:
        metrics, started = record
        metrics.total_ms += (perf_counter_ns() - started) / 1_000_000
        context._oksp_sql_clock = None


def after_cursor_execute(connection, cursor, statement, parameters, context, executemany):
    finish_execution(context)


def handle_error(exception_context):
    if exception_context.execution_context is not None:
        finish_execution(exception_context.execution_context)


def install_sql_events(engine):
    for name, listener in (
        ("before_cursor_execute", before_cursor_execute),
        ("after_cursor_execute", after_cursor_execute),
        ("handle_error", handle_error),
    ):
        if not event.contains(engine, name, listener):
            event.listen(engine, name, listener)


class SQLObservationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        if scope["type"] != "http" or headers.get(b"x-measure-sql") != b"1":
            await self.app(scope, receive, send)
            return
        metrics = SQLMetrics()
        token = request_sql.set(metrics)

        async def observed_send(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", [])) + [
                    (b"x-sql-total-ms", f"{metrics.total_ms:.6f}".encode()),
                    (b"x-sql-query-count", str(metrics.query_count).encode()),
                    (b"x-measurement-id", headers.get(b"x-measurement-id", b"")),
                ]}
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            request_sql.reset(token)
