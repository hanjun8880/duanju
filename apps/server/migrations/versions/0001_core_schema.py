"""Core 9 tables, executed verbatim from the spec DDL (migrations/sql/0001_core_schema.sql)."""
from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0001"
down_revision = None

SQL = Path(__file__).parents[1] / "sql" / "0001_core_schema.sql"
TABLES = ["credit_ledger", "assets", "jobs", "shots", "lookdev_assets", "projects", "templates",
          "model_catalog", "users"]


def upgrade() -> None:
    # asyncpg runs a multi-statement script only through its own execute() (simple query protocol)
    raw = op.get_bind().connection.driver_connection
    await_only(raw.execute(SQL.read_text()))


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
