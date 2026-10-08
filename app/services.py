from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session, aliased

from app.models import Appointment, Service, Slot, Specialist, User
from app.schemas import AppointmentFilters, BookingInput, validate_dates

MOSCOW = ZoneInfo("Europe/Moscow")


def period_bounds(date_from: date | None, date_to: date | None):
    validate_dates(date_from, date_to)
    lower = datetime.combine(date_from, time.min, MOSCOW) if date_from else None
    upper = datetime.combine(date_to + timedelta(days=1), time.min, MOSCOW) if date_to else None
    return lower, upper


def start_filters(date_from, date_to):
    lower, upper = period_bounds(date_from, date_to)
    conditions = []
    if lower:
        conditions.append(Slot.starts_at >= lower)
    if upper:
        conditions.append(Slot.starts_at < upper)
    return conditions


def service_list(db: Session):
    return [dict(id=s.id, name=s.name, duration_minutes=s.duration_minutes)
            for s in db.scalars(select(Service).order_by(Service.id))]


def specialist_list(db: Session, service_id=None):
    query = select(Specialist).order_by(Specialist.id)
    if service_id is not None:
        query = query.where(select(Slot.id).where(
            Slot.specialist_id == Specialist.id, Slot.service_id == service_id
        ).exists())
    return [dict(id=s.id, full_name=s.full_name, specialization=s.specialization)
            for s in db.scalars(query)]


def slot_query():
    return select(Slot, Specialist, Service).join(
        Specialist, Slot.specialist_id == Specialist.id
    ).join(Service, Slot.service_id == Service.id)


def slot_data(slot, specialist, service):
    return {
        "id": slot.id, "starts_at": slot.starts_at, "ends_at": slot.ends_at,
        "specialist": {"id": specialist.id, "full_name": specialist.full_name},
        "service": {"id": service.id, "name": service.name,
                    "duration_minutes": service.duration_minutes},
    }


def free_slots(db, service_id, date_from, date_to, specialist_id=None, page=1, size=20):
    if db.get(Service, service_id) is None:
        raise HTTPException(404, "Услуга не найдена")
    if specialist_id and db.get(Specialist, specialist_id) is None:
        raise HTTPException(404, "Специалист не найден")
    occupied = aliased(Slot)
    conflict = select(Appointment.id).join(
        occupied, Appointment.slot_id == occupied.id
    ).where(
        Appointment.status == "booked",
        occupied.specialist_id == Slot.specialist_id,
        occupied.starts_at < Slot.ends_at,
        occupied.ends_at > Slot.starts_at,
    ).exists()
    query = slot_query().where(
        Slot.service_id == service_id, ~conflict,
        extract("epoch", Slot.ends_at - Slot.starts_at) == Service.duration_minutes * 60,
        *start_filters(date_from, date_to),
    )
    if specialist_id:
        query = query.where(Slot.specialist_id == specialist_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Slot.starts_at, Slot.id).offset((page - 1) * size).limit(size))
    return {"items": [slot_data(*row) for row in rows], "total": total, "page": page, "size": size}


def appointment_query():
    return select(Appointment, Slot, Specialist, Service, User).join(
        Slot, Appointment.slot_id == Slot.id
    ).join(Specialist, Slot.specialist_id == Specialist.id).join(
        Service, Slot.service_id == Service.id
    ).join(User, Appointment.created_by_id == User.id)


def appointment_data(appointment, slot, specialist, service, user):
    return {
        "id": appointment.id, "status": appointment.status,
        "client_name": appointment.client_name, "client_contact": appointment.client_contact,
        "created_at": appointment.created_at, "cancelled_at": appointment.cancelled_at,
        "slot": {"id": slot.id, "starts_at": slot.starts_at, "ends_at": slot.ends_at},
        "specialist": {"id": specialist.id, "full_name": specialist.full_name,
                       "specialization": specialist.specialization},
        "service": {"id": service.id, "name": service.name,
                    "duration_minutes": service.duration_minutes},
        "created_by": {"id": user.id, "full_name": user.full_name},
    }


def appointment_list(db, filters: AppointmentFilters):
    query = appointment_query().where(*start_filters(filters.date_from, filters.date_to))
    if filters.status:
        query = query.where(Appointment.status == filters.status)
    if filters.specialist_id:
        query = query.where(Slot.specialist_id == filters.specialist_id)
    if filters.service_id:
        query = query.where(Slot.service_id == filters.service_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Slot.starts_at.desc(), Appointment.id.desc()).offset(
        (filters.page - 1) * filters.size
    ).limit(filters.size))
    return {"items": [appointment_data(*row) for row in rows],
            "total": total, "page": filters.page, "size": filters.size}


def appointment_detail(db, appointment_id):
    row = db.execute(appointment_query().where(Appointment.id == appointment_id)).first()
    if row is None:
        raise HTTPException(404, "Запись не найдена")
    return appointment_data(*row)


def intervals_overlap(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def duration_is_suitable(slot, service):
    return (slot.ends_at - slot.starts_at).total_seconds() == service.duration_minutes * 60


def create_appointment(db: Session, data: BookingInput, user_id: int):
    try:
        slot = db.scalar(select(Slot).where(Slot.id == data.slot_id).with_for_update())
        if slot is None:
            raise HTTPException(404, "Слот не найден")
        service = db.get(Service, slot.service_id)
        if not duration_is_suitable(slot, service):
            raise HTTPException(409, "Длительность слота не соответствует услуге")
        conflict = db.scalar(select(Appointment.id).join(
            Slot, Appointment.slot_id == Slot.id
        ).where(
            Appointment.status == "booked", Slot.specialist_id == slot.specialist_id,
            Slot.starts_at < slot.ends_at, Slot.ends_at > slot.starts_at,
        ).limit(1))
        if conflict is not None:
            raise HTTPException(409, "Слот занят или пересекается с действующей записью")
        appointment = Appointment(
            slot_id=slot.id, created_by_id=user_id,
            client_name=data.client_name, client_contact=data.client_contact,
            status="booked", created_at=datetime.now(timezone.utc), cancelled_at=None,
        )
        db.add(appointment)
        db.flush()
        appointment_id = appointment.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    return appointment_detail(db, appointment_id)


def cancel_appointment(db: Session, appointment_id: int):
    try:
        appointment = db.get(Appointment, appointment_id)
        if appointment is None:
            raise HTTPException(404, "Запись не найдена")
        db.scalar(select(Slot).where(Slot.id == appointment.slot_id).with_for_update())
        db.refresh(appointment)
        if appointment.status != "cancelled":
            appointment.status = "cancelled"
            appointment.cancelled_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return appointment_detail(db, appointment_id)


def merged_seconds(intervals):
    if not intervals:
        return 0
    intervals = sorted(intervals)
    start, end = intervals[0]
    total = 0
    for next_start, next_end in intervals[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += (end - start).total_seconds()
            start, end = next_start, next_end
    return total + (end - start).total_seconds()


def summary(db, date_from: date, date_to: date):
    lower, upper = period_bounds(date_from, date_to)
    scheduled = defaultdict(list)
    booked = defaultdict(list)
    for specialist_id, start, end in db.execute(select(
        Slot.specialist_id, Slot.starts_at, Slot.ends_at
    ).where(Slot.starts_at < upper, Slot.ends_at > lower)):
        scheduled[specialist_id].append((max(start, lower), min(end, upper)))
    total = cancelled = 0
    for status, specialist_id, start, end in db.execute(select(
        Appointment.status, Slot.specialist_id, Slot.starts_at, Slot.ends_at
    ).join(Slot, Appointment.slot_id == Slot.id).where(
        Slot.starts_at < upper, Slot.ends_at > lower
    )):
        total += 1
        if status == "cancelled":
            cancelled += 1
        else:
            booked[specialist_id].append((max(start, lower), min(end, upper)))
    items = []
    for specialist in db.scalars(select(Specialist).order_by(Specialist.id)):
        scheduled_minutes = merged_seconds(scheduled[specialist.id]) / 60
        booked_minutes = merged_seconds(booked[specialist.id]) / 60
        items.append({
            "specialist_id": specialist.id, "full_name": specialist.full_name,
            "scheduled_minutes": scheduled_minutes, "booked_minutes": booked_minutes,
            "load_percent": round(booked_minutes / scheduled_minutes * 100, 2)
                            if scheduled_minutes else None,
        })
    return {
        "date_from": date_from, "date_to": date_to, "specialists": items,
        "total_appointments": total, "cancelled_appointments": cancelled,
        "cancellation_percent": round(cancelled / total * 100, 2) if total else 0.0,
    }
