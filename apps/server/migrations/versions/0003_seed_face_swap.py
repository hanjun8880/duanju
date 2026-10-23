"""Seed local face-swap model (spec table: insightface_swap_pro, local post-processing, zero credits)."""
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute("INSERT INTO model_catalog (model_key, display_name, provider, task_type, api_base, credits_per_unit,"
               " max_duration, max_refs, is_nsfw) VALUES ('insightface_swap_pro', 'InsightFace swap (local)',"
               " 'local', 'face.swap', 'local://insightface', 0, 30, 1, false)")


def downgrade() -> None:
    op.execute("DELETE FROM model_catalog WHERE model_key='insightface_swap_pro'")
