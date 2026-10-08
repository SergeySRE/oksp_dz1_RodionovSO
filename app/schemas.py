from datetime import date
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_id: int = Field(gt=0)
    client_name: str = Field(min_length=1, max_length=200)
    client_contact: str = Field(min_length=1, max_length=200)

    @field_validator("client_name", "client_contact")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Поле не должно быть пустым")
        return value


class AppointmentFilters(BaseModel):
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)
    status: Literal["booked", "cancelled"] | None = None
    specialist_id: int | None = Field(default=None, gt=0)
    service_id: int | None = Field(default=None, gt=0)
    date_from: date | None = None
    date_to: date | None = None


def validate_dates(date_from: date | None, date_to: date | None):
    if date_from and date_to and date_to < date_from:
        raise HTTPException(422, "Конец периода раньше начала")
