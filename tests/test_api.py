from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.models import Appointment, Slot
from app.seed import SIZES, data_counts, data_fingerprint, seed_database


PERIOD = {"date_from": "2026-01-01", "date_to": "2026-12-31"}


def active(db):
    return db.scalar(select(Appointment).where(Appointment.status == "booked").order_by(Appointment.id))


def free_slot(authorized, service_id=1):
    response = authorized.get("/api/slots", params={**PERIOD, "service_id": service_id})
    assert response.status_code == 200
    return response.json()["items"][0]


def book(authorized, slot_id):
    return authorized.post("/api/appointments", json={
        "slot_id": slot_id, "client_name": "Тестовый клиент", "client_contact": "test@example.test"
    })


def test_login_logout_and_cookie(client):
    assert client.post("/api/auth/login", json={"login": "absent", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"login": settings.demo_login, "password": "wrong"}).status_code == 401
    response = client.post("/api/auth/login", json={"login": settings.demo_login, "password": settings.demo_password})
    assert response.status_code == 200
    assert response.json()["full_name"] == "Родионов Сергей Олегович"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert client.get("/api/services").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/services").status_code == 401


@pytest.mark.parametrize("path", [
    "/api/services", "/api/specialists", "/api/appointments", "/api/appointments/1",
    "/api/slots?service_id=1&date_from=2026-01-01&date_to=2026-12-31",
    "/api/summary?date_from=2026-01-01&date_to=2026-12-31",
])
def test_reads_require_authorization(client, path):
    assert client.get(path).status_code == 401


def test_writes_require_authorization(client):
    assert book(client, 1).status_code == 401
    assert client.post("/api/appointments/1/cancel").status_code == 401
    assert client.post("/api/auth/logout").status_code == 401


def test_tampered_cookie_is_rejected(client):
    client.cookies.set("sergey_rodionov_session", "invalid.signature")
    assert client.get("/api/appointments").status_code == 401


def test_reference_lists(authorized):
    assert len(authorized.get("/api/services").json()) == 10
    assert len(authorized.get("/api/specialists").json()) == 10
    filtered = authorized.get("/api/specialists", params={"service_id": 1})
    assert filtered.status_code == 200
    assert filtered.json()


def test_free_slot_search(authorized, db):
    response = authorized.get("/api/slots", params={**PERIOD, "service_id": 1, "size": 100})
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["service"]["id"] == 1
        assert db.scalar(select(Appointment.id).where(
            Appointment.slot_id == item["id"], Appointment.status == "booked"
        )) is None


def test_booking_busy_slot_and_card(authorized):
    slot = free_slot(authorized)
    response = book(authorized, slot["id"])
    assert response.status_code == 201
    item = response.json()
    assert item["id"] > 300
    assert item["status"] == "booked"
    assert item["slot"]["id"] == slot["id"]
    assert item["service"]["id"] == slot["service"]["id"]
    assert item["created_by"]["full_name"] == "Родионов Сергей Олегович"
    assert book(authorized, slot["id"]).status_code == 409
    assert authorized.get("/api/appointments/" + str(item["id"])).json() == item


def test_cancellation_releases_slot_and_keeps_history(authorized, db):
    item = active(db)
    slot_id = item.slot_id
    response = authorized.post(f"/api/appointments/{item.id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancelled_at"]
    repeated = authorized.post(f"/api/appointments/{item.id}/cancel")
    assert repeated.json()["cancelled_at"] == response.json()["cancelled_at"]
    created = book(authorized, slot_id)
    assert created.status_code == 201
    assert created.json()["id"] != item.id
    assert authorized.get(f"/api/appointments/{item.id}").json()["status"] == "cancelled"


def test_filtering(authorized, db):
    item = active(db)
    slot = db.get(Slot, item.slot_id)
    params = {**PERIOD, "status": "booked", "specialist_id": slot.specialist_id,
              "service_id": slot.service_id}
    result = authorized.get("/api/appointments", params=params).json()
    assert result["total"] > 0
    assert all(row["status"] == "booked" and row["specialist"]["id"] == slot.specialist_id
               and row["service"]["id"] == slot.service_id for row in result["items"])


def test_pagination(authorized):
    first = authorized.get("/api/appointments", params={"page": 1, "size": 7}).json()
    second = authorized.get("/api/appointments", params={"page": 2, "size": 7}).json()
    assert first["total"] == second["total"] == 300
    assert len(first["items"]) == len(second["items"]) == 7
    assert {row["id"] for row in first["items"]}.isdisjoint(row["id"] for row in second["items"])
    assert authorized.get("/api/appointments?page=100&size=20").json()["items"] == []


@pytest.mark.parametrize("path", [
    "/api/appointments/999999", "/api/slots?service_id=999999&date_from=2026-01-01&date_to=2026-12-31",
])
def test_missing_objects(authorized, path):
    assert authorized.get(path).status_code == 404


def test_missing_write_objects(authorized):
    assert book(authorized, 999999).status_code == 404
    assert authorized.post("/api/appointments/999999/cancel").status_code == 404


@pytest.mark.parametrize("path", [
    "/api/appointments?page=0", "/api/appointments?size=101",
    "/api/appointments?status=unknown", "/api/appointments?specialist_id=-1",
    "/api/appointments?date_from=2026-12-31&date_to=2026-01-01",
    "/api/summary?date_from=bad&date_to=2026-01-01",
    "/api/summary?date_from=2026-12-31&date_to=2026-01-01",
    "/api/appointments/0",
])
def test_invalid_parameters(authorized, path):
    assert authorized.get(path).status_code == 422


def test_invalid_booking(authorized):
    assert authorized.post("/api/appointments", json={
        "slot_id": 1, "client_name": "  ", "client_contact": "test"
    }).status_code == 422
    assert authorized.post("/api/appointments", json={"slot_id": -1}).status_code == 422


def test_overlapping_booking_is_rejected(authorized, db):
    item = active(db)
    slot = db.get(Slot, item.slot_id)
    db.add(Slot(id=501, specialist_id=slot.specialist_id, service_id=slot.service_id,
                starts_at=slot.starts_at, ends_at=slot.ends_at))
    db.commit()
    assert book(authorized, 501).status_code == 409
    result = authorized.get("/api/slots", params={**PERIOD, "service_id": slot.service_id,
                                                 "specialist_id": slot.specialist_id, "size": 100}).json()
    assert 501 not in {row["id"] for row in result["items"]}


def test_unsuitable_duration_is_rejected(authorized, db):
    slot = db.get(Slot, free_slot(authorized)["id"])
    slot.ends_at += timedelta(minutes=1)
    db.commit()
    assert book(authorized, slot.id).status_code == 409


def test_summary_counts_entire_dataset(authorized):
    result = authorized.get("/api/summary", params=PERIOD).json()
    assert result["total_appointments"] == 300
    assert result["cancelled_appointments"] == 60
    assert result["cancellation_percent"] == 20
    assert len(result["specialists"]) == 10
    assert all(0 <= row["load_percent"] <= 100 for row in result["specialists"])


def test_summary_formula_and_empty_period(authorized, db):
    db.execute(delete(Appointment))
    db.execute(delete(Slot))
    start = datetime(2026, 1, 1, 8, tzinfo=ZoneInfo("Europe/Moscow"))
    for i in range(3):
        db.add(Slot(id=i + 1, specialist_id=1, service_id=3,
                    starts_at=start + timedelta(hours=i), ends_at=start + timedelta(hours=i + 1)))
    db.flush()
    db.add_all([
        Appointment(id=1, slot_id=1, created_by_id=1, client_name="A", client_contact="a",
                    status="booked", created_at=start - timedelta(days=1)),
        Appointment(id=2, slot_id=2, created_by_id=1, client_name="B", client_contact="b",
                    status="cancelled", created_at=start - timedelta(days=2),
                    cancelled_at=start - timedelta(days=1)),
    ])
    db.commit()
    result = authorized.get("/api/summary", params={"date_from": "2026-01-01", "date_to": "2026-01-01"}).json()
    first = result["specialists"][0]
    assert first["scheduled_minutes"] == 180
    assert first["booked_minutes"] == 60
    assert first["load_percent"] == 33.33
    assert result["cancellation_percent"] == 50
    empty = authorized.get("/api/summary", params={"date_from": "2027-01-01", "date_to": "2027-01-01"}).json()
    assert empty["total_appointments"] == 0
    assert empty["cancellation_percent"] == 0
    assert all(row["load_percent"] is None for row in empty["specialists"])


@pytest.mark.parametrize("path", [
    "/appointments", "/appointments/1", "/booking", "/booking?service_id=1", "/summary",
])
def test_html_pages(authorized, path):
    response = authorized.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "sergey_rodionov" in response.text
    assert "БИСТ-24-ПО-2" in response.text


def test_html_redirect_to_login(client):
    response = client.get("/appointments", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/login").status_code == 200


def test_seed_reproducibility(database):
    first = data_fingerprint(database)
    seed_database(database, "small")
    assert data_counts(database) == SIZES["small"]
    assert data_fingerprint(database) == first
