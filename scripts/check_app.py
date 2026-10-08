"""Functional checks of a running app. No performance series are collected."""
import argparse

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models import Appointment
from app.seed import SIZES, data_counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--size", choices=SIZES, required=True)
    args = parser.parse_args()
    temporary_ids = []
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        try:
            assert data_counts(engine) == SIZES[args.size]
            assert client.get("/api/appointments").status_code == 401
            assert client.get("/login").status_code == 200
            login = client.post("/api/auth/login", json={
                "login": settings.demo_login, "password": settings.demo_password
            })
            assert login.status_code == 200, login.text
            period = {"date_from": "2026-01-01", "date_to": "2026-12-31"}
            for path in ("/api/services", "/api/specialists", "/api/appointments"):
                response = client.get(path)
                assert response.status_code == 200, response.text
                print("OK", path)
            listing = client.get("/api/appointments?page=2&size=10").json()
            assert listing["total"] == SIZES[args.size]["appointments"]
            assert len(listing["items"]) == 10
            assert client.get("/api/appointments", params={"status": "cancelled"}).json()["total"] == (
                SIZES[args.size]["appointments"] // 5
            )
            assert client.get("/api/appointments/1").status_code == 200
            slots = client.get("/api/slots", params={**period, "service_id": 1})
            assert slots.status_code == 200, slots.text
            slot_id = slots.json()["items"][0]["id"]
            booking_data = {"slot_id": slot_id, "client_name": "Проверка стенда",
                            "client_contact": "smoke@example.test"}
            created = client.post("/api/appointments", json=booking_data)
            assert created.status_code == 201, created.text
            temporary_ids.append(created.json()["id"])
            appointment_id = temporary_ids[-1]
            assert client.post("/api/appointments", json=booking_data).status_code == 409
            assert client.post(f"/api/appointments/{appointment_id}/cancel").status_code == 200
            repeated = client.post("/api/appointments", json=booking_data)
            assert repeated.status_code == 201, repeated.text
            temporary_ids.append(repeated.json()["id"])
            assert client.get("/api/appointments/999999999").status_code == 404
            assert client.get("/api/appointments?page=0").status_code == 422
            print("OK booking, conflict, cancellation, reuse, 404/422")
            # Remove only this script's records before checking summary totals.
            with Session(engine) as db:
                db.execute(delete(Appointment).where(Appointment.id.in_(temporary_ids)))
                db.commit()
            temporary_ids.clear()
            stats = client.get("/api/summary", params=period)
            assert stats.status_code == 200, stats.text
            assert stats.json()["total_appointments"] == SIZES[args.size]["appointments"]
            assert stats.json()["cancellation_percent"] == 20
            print("OK /api/summary")
            for path in ("/appointments", "/appointments/1", "/booking",
                         "/booking?service_id=1", "/summary"):
                response = client.get(path)
                assert response.status_code == 200, response.text
                assert "text/html" in response.headers["content-type"]
                assert "sergey_rodionov" in response.text
                assert "БИСТ-24-ПО-2" in response.text
                print("OK HTML", path)
            for path in ("/static/style.css", "/static/app.js", "/openapi.json"):
                assert client.get(path).status_code == 200
            assert client.post("/api/auth/logout").status_code == 204
            assert client.get("/api/appointments").status_code == 401
            assert data_counts(engine) == SIZES[args.size]
            print("ALL CHECKS PASSED:", args.size, data_counts(engine))
        finally:
            if temporary_ids:
                with Session(engine) as db:
                    db.execute(delete(Appointment).where(Appointment.id.in_(temporary_ids)))
                    db.commit()


if __name__ == "__main__":
    main()
