from datetime import date
import json
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from app.db import engine
from app.schemas import AppointmentFilters, BookingInput
from app.services import appointment_list, create_appointment, free_slots

# Capture the actual SQL from the same service functions and parameters.
# Stop before the INSERT. The following EXPLAIN ANALYZE calls execute SELECT only.
class StopBeforeInsert(Exception):
    pass

with Session(engine) as db:
    slot_id = db.scalar(text("""
        SELECT s.id FROM sergey_rodionov.slots s
        WHERE NOT EXISTS (
            SELECT 1 FROM sergey_rodionov.appointments a
            WHERE a.slot_id=s.id AND a.status='booked')
        ORDER BY s.id LIMIT 1
    """))
    assert slot_id is not None

checks = [
    ("free_slots", lambda session: free_slots(session, 1, date(2026,1,1), date(2026,12,31))),
    ("appointments_list", lambda session: appointment_list(session, AppointmentFilters(page=1, size=20))),
    ("appointment_create", lambda session: create_appointment(
        session, BookingInput(slot_id=slot_id, client_name="DZ2 explain", client_contact="dz2@example.test"), 1)),
]
out = {"dataset": "WORKING", "mode": "separate EXPLAIN ANALYZE on selected read queries",
       "slot_id": slot_id, "operations": {}}
for name, action in checks:
    calls = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        sql = statement.lstrip().upper()
        if sql.startswith("INSERT"):
            raise StopBeforeInsert()
        if sql.startswith("SELECT") and "FOR UPDATE" not in sql:
            calls.append((statement,parameters))
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with Session(engine) as session:
            try:
                action(session)
            except StopBeforeInsert:
                assert name == "appointment_create"
            else:
                assert name != "appointment_create"
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    out["operations"][name] = []
    for sql, params in calls:
        if name == "appointment_create" and "appointments.status" not in sql:
            continue
        with engine.connect() as connection:
            plan = connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).scalar_one()[0]
        out["operations"][name].append({"sql": sql, "parameters": params, "plan": plan})
    assert len(out["operations"][name]) >= 1
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))

