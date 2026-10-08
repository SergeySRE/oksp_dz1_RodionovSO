import hashlib
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, insert, select, text

from app.auth import hash_password
from app.config import settings
from app.models import Appointment, Base, Service, Slot, Specialist, User

SIZES = {
    "small": {"users": 1, "specialists": 10, "services": 10, "slots": 500, "appointments": 300},
    "working": {"users": 1, "specialists": 200, "services": 30, "slots": 200_000, "appointments": 100_000},
}
ANCHOR = datetime(2026, 1, 1, 8, tzinfo=ZoneInfo("Europe/Moscow"))


def seed_rows(size):
    random.seed(42)
    counts = SIZES[size]
    # Fixed salt is only for the reproducible demonstration account.
    salt = hashlib.sha256(b"sergey_rodionov_demo").digest()[:16]
    yield User.__table__, [{
        "id": 1, "login": settings.demo_login,
        "password_hash": hash_password(settings.demo_password, salt),
        "full_name": "Родионов Сергей Олегович",
    }]
    specialists = [{
        "id": i + 1, "full_name": f"Специалист {i + 1:03d}",
        "specialization": ("Консультации", "Диагностика", "Практические занятия")[i % 3],
    } for i in range(counts["specialists"])]
    yield Specialist.__table__, specialists
    services = [{
        "id": i + 1, "name": f"Услуга {i + 1:02d}",
        "duration_minutes": (30, 45, 60)[i % 3],
    } for i in range(counts["services"])]
    yield Service.__table__, services
    slots = []
    for i in range(counts["slots"]):
        specialist_index = i % counts["specialists"]
        ordinal = i // counts["specialists"]
        # One-hour grid, maximum service duration is one hour: slots never overlap.
        start = ANCHOR + timedelta(days=ordinal // 8, hours=ordinal % 8)
        service = services[(specialist_index + ordinal) % counts["services"]]
        slots.append({
            "id": i + 1, "specialist_id": specialist_index + 1,
            "service_id": service["id"], "starts_at": start,
            "ends_at": start + timedelta(minutes=service["duration_minutes"]),
        })
    yield Slot.__table__, slots
    chosen_slots = random.sample(slots, counts["appointments"])
    appointments = []
    for i, slot in enumerate(chosen_slots):
        cancelled = i % 5 == 0
        created = slot["starts_at"] - timedelta(days=7, minutes=i % 60)
        appointments.append({
            "id": i + 1, "slot_id": slot["id"], "created_by_id": 1,
            "client_name": f"Клиент {i + 1:06d}",
            "client_contact": f"client{i + 1:06d}@example.test",
            "status": "cancelled" if cancelled else "booked",
            "created_at": created,
            "cancelled_at": created + timedelta(days=1) if cancelled else None,
        })
    yield Appointment.__table__, appointments


def seed_database(engine, size):
    schema = engine.get_execution_options().get("schema_translate_map", {}).get(
        settings.schema, settings.schema
    )
    tables = ", ".join(f'"{schema}"."{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY"))
        for table, rows in seed_rows(size):
            for offset in range(0, len(rows), 2000):
                connection.execute(insert(table), rows[offset:offset + 2000])
        # Explicit seeded IDs must not collide with IDs of new bookings.
        for table in (User, Specialist, Service, Slot, Appointment):
            name = table.__tablename__
            connection.execute(text(
                f"SELECT setval(pg_get_serial_sequence('"
                f'\"{schema}\".\"{name}\"'
                f"', 'id'), (SELECT MAX(id) FROM \"{schema}\".\"{name}\"), true)"
            ))


def data_counts(engine):
    with engine.connect() as connection:
        return {table.name: connection.scalar(select(func.count()).select_from(table))
                for table in Base.metadata.sorted_tables}


def data_fingerprint(engine):
    digest = hashlib.sha256()
    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            digest.update(table.name.encode())
            for row in connection.execute(select(table).order_by(table.c.id)):
                values = [value.isoformat() if isinstance(value, datetime) else value for value in row]
                digest.update(repr(values).encode())
    return digest.hexdigest()
