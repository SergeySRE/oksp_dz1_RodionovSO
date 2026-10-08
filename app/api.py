from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.auth import current_user, verify_password
from app.db import get_db
from app.models import User
from app.schemas import AppointmentFilters, BookingInput, LoginInput

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_db)]
Operator = Annotated[User, Depends(current_user)]
PositiveID = Annotated[int, Path(gt=0)]


@router.post("/auth/login")
def login(data: LoginInput, request: Request, db: DB):
    user = db.scalar(select(User).where(User.login == data.login))
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Неверный логин или пароль")
    request.session.clear()
    request.session["user_id"] = user.id
    return {"id": user.id, "login": user.login, "full_name": user.full_name}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, user: Operator):
    request.session.clear()
    return Response(status_code=204)


@router.get("/services")
def get_services(db: DB, user: Operator):
    return services.service_list(db)


@router.get("/specialists")
def get_specialists(db: DB, user: Operator, service_id: Annotated[int | None, Query(gt=0)] = None):
    return services.specialist_list(db, service_id)


@router.get("/slots")
def get_slots(db: DB, user: Operator,
              service_id: Annotated[int, Query(gt=0)], date_from: date, date_to: date,
              specialist_id: Annotated[int | None, Query(gt=0)] = None,
              page: Annotated[int, Query(ge=1)] = 1, size: Annotated[int, Query(ge=1, le=100)] = 20):
    return services.free_slots(db, service_id, date_from, date_to, specialist_id, page, size)


@router.get("/appointments")
def get_appointments(db: DB, user: Operator, filters: Annotated[AppointmentFilters, Query()]):
    return services.appointment_list(db, filters)


@router.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: PositiveID, db: DB, user: Operator):
    return services.appointment_detail(db, appointment_id)


@router.post("/appointments", status_code=201)
def book(data: BookingInput, db: DB, user: Operator):
    return services.create_appointment(db, data, user.id)


@router.post("/appointments/{appointment_id}/cancel")
def cancel(appointment_id: PositiveID, db: DB, user: Operator):
    return services.cancel_appointment(db, appointment_id)


@router.get("/summary")
def get_summary(db: DB, user: Operator, date_from: date, date_to: date):
    return services.summary(db, date_from, date_to)
