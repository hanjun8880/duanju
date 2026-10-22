import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import settings

URL = os.environ.get("DATABASE_URL", settings.database_url)


def run(sync_conn):
    context.configure(connection=sync_conn, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        await conn.run_sync(run)
        await conn.commit()
    await engine.dispose()


asyncio.run(main())
