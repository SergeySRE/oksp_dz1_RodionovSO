from datetime import date
from pathlib import Path as FilePath
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import services
from app.auth import current_user
from app.db import get_db
from app.models import User
from app.schemas import AppointmentFilters

router = APIRouter()
templates = Jinja2Templates(directory=str(FilePath(__file__).parent / "templates"))
templates.env.filters["localtime"] = lambda value: (
    value.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M") if value else "—"
)
templates.env.filters["status_label"] = lambda value: {"booked": "Действующая", "cancelled": "Отменена"}[value]
DB = Annotated[Session, Depends(get_db)]
Operator = Annotated[User, Depends(current_user)]


def pagination(request, result):
    pages = max(1, (result["total"] + result["size"] - 1) // result["size"])
    params = dict(request.query_params)
    def link(page):
        return str(request.url.path) + "?" + urlencode({**params, "page": page})
    return {"pages": pages, "previous": link(result["page"] - 1) if result["page"] > 1 else None,
            "next": link(result["page"] + 1) if result["page"] < pages else None}


@router.get("/")
def home():
    return RedirectResponse("/appointments", status_code=303)


@router.get("/login")
def login_page(request: Request, db: DB):
    user_id = request.session.get("user_id")
    if isinstance(user_id, int) and db.get(User, user_id):
        return RedirectResponse("/appointments", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"user": None})


@router.get("/appointments")
def appointments_page(request: Request, db: DB, user: Operator,
                      filters: Annotated[AppointmentFilters, Query()]):
    data = services.appointment_list(db, filters)
    return templates.TemplateResponse(request=request, name="appointments.html", context={
        "user": user, "data": data, "filters": filters,
        "services": services.service_list(db), "specialists": services.specialist_list(db),
        "pagination": pagination(request, data),
    })


@router.get("/appointments/{appointment_id}")
def appointment_page(request: Request, appointment_id: Annotated[int, Path(gt=0)],
                     db: DB, user: Operator):
    return templates.TemplateResponse(request=request, name="appointment.html",
                                      context={"user": user, "item": services.appointment_detail(db, appointment_id)})


@router.get("/booking")
def booking_page(request: Request, db: DB, user: Operator,
                 service_id: Annotated[int | None, Query(gt=0)] = None,
                 specialist_id: Annotated[int | None, Query(gt=0)] = None,
                 date_from: date = date(2026, 1, 1), date_to: date = date(2026, 12, 31),
                 page: Annotated[int, Query(ge=1)] = 1, size: Annotated[int, Query(ge=1, le=100)] = 20):
    data = services.free_slots(db, service_id, date_from, date_to, specialist_id, page, size) if service_id else None
    return templates.TemplateResponse(request=request, name="booking.html", context={
        "user": user, "data": data, "services": services.service_list(db),
        "specialists": services.specialist_list(db, service_id), "service_id": service_id,
        "specialist_id": specialist_id, "date_from": date_from, "date_to": date_to,
        "pagination": pagination(request, data) if data else None,
    })


@router.get("/summary")
def summary_page(request: Request, db: DB, user: Operator,
                 date_from: date = date(2026, 1, 1), date_to: date = date(2026, 12, 31)):
    return templates.TemplateResponse(request=request, name="summary.html", context={
        "user": user, "data": services.summary(db, date_from, date_to),
    })
