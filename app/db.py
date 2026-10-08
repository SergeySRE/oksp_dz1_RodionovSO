import re
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Base
from app.observation import install_sql_events

engine = create_engine(settings.database_url)
install_sql_events(engine)


def init_db(target_engine=engine, schema=settings.schema):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", schema):
        raise ValueError("Invalid schema name")
    with target_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        Base.metadata.create_all(connection)


def get_db():
    with Session(engine) as session:
        yield session
