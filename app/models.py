from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from app.config import settings


class Base(DeclarativeBase):
    metadata = MetaData(schema=settings.schema)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(200))


class Specialist(Base):
    __tablename__ = "specialists"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    specialization: Mapped[str] = mapped_column(String(200))


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (CheckConstraint("duration_minutes > 0", name="positive_duration"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    duration_minutes: Mapped[int] = mapped_column(Integer)


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="positive_interval"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    specialist_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.schema}.specialists.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.schema}.services.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("status IN ('booked', 'cancelled')", name="valid_status"),
        CheckConstraint(
            "(status = 'booked' AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND cancelled_at IS NOT NULL)",
            name="cancellation_date",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.schema}.slots.id"))
    created_by_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.schema}.users.id"))
    client_name: Mapped[str] = mapped_column(String(200))
    client_contact: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
