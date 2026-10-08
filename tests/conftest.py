import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine, get_db, init_db
from app.main import app
from app.seed import seed_database


@pytest.fixture(scope="session")
def test_engine():
    schema = "sergey_rodionov_test_" + uuid.uuid4().hex[:12]
    translated = engine.execution_options(schema_translate_map={settings.schema: schema})
    init_db(translated, schema)
    yield translated
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def database(test_engine):
    seed_database(test_engine, "small")
    return test_engine


@pytest.fixture
def db(database):
    with Session(database) as session:
        yield session


@pytest.fixture
def client(database):
    def test_db():
        with Session(database) as session:
            yield session
    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def authorized(client):
    response = client.post("/api/auth/login", json={
        "login": settings.demo_login, "password": settings.demo_password
    })
    assert response.status_code == 200
    return client
