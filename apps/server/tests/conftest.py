import os
import subprocess
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_DB = os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/duanju_test"
)
os.environ["DATABASE_URL"] = TEST_DB  # app engine (src.db) must point at the test DB too


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    """Rebuild the test schema from the real Alembic migrations (no ORM create_all shortcut)."""
    env = {**os.environ, "DATABASE_URL": TEST_DB}
    psql_url = TEST_DB.replace("+asyncpg", "")
    subprocess.run(["psql", psql_url, "-qc", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"],
                   check=True, capture_output=True)
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env, capture_output=True)


@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DB, pool_size=10)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def sessionmaker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def user_and_project(engine):
    """A fresh user (credits=100, daily_limit=60) and project per test, so tests never share balances."""
    async with engine.begin() as conn:
        uid = (await conn.execute(text(
            "INSERT INTO users (username, credits, daily_limit_credits) VALUES (:u, 100, 60) RETURNING id"
        ), {"u": f"u_{uuid.uuid4().hex[:12]}"})).scalar_one()
        pid = (await conn.execute(text(
            "INSERT INTO projects (user_id, title) VALUES (:u, 't') RETURNING id"), {"u": uid})).scalar_one()
    return uid, pid


async def balance(engine, uid) -> Decimal:
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT credits FROM users WHERE id=:u"), {"u": uid})).scalar_one()
