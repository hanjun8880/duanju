"""Ledger team: USD reference prices + persisted per-stage model overrides.

usd_per_unit = USD per billable unit (second of video, or image) at the model's reference resolution (720p for
Seedance/Wan, 768p MiniMax, 480p for the Seedance draft model). Third-party reference prices 2026-10, NOT
verified against vendor price lists: edit them in the admin drawer. Resolution factors live in
settings.usd_resolution_factors (config).
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_ledger"
down_revision = "0007_script"

USD = {"seedance_2_5_spicy": 0.231, "seedance_2_5_draft": 0.092, "wan3_spicy": 0.083, "minimax_h3": 0.08,
       "seedream_5_pro": 0.03, "local_animatic": 0, "local_assemble": 0, "realesrgan_x4plus": 0}


def upgrade() -> None:
    op.get_bind().execute(sa.text("UPDATE model_catalog SET usd_per_unit=:u WHERE model_key=:k"),
                          [{"k": k, "u": u} for k, u in USD.items()])
    op.execute("""
        CREATE TABLE stage_overrides (
            stage VARCHAR(32) PRIMARY KEY,
            model VARCHAR(64) NOT NULL,
            provider VARCHAR(64),
            resolution VARCHAR(16),
            note TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE stage_overrides")
    op.execute("UPDATE model_catalog SET usd_per_unit=NULL")
