"""Integration: a 'tts' placeholder model_catalog row so every paid (cache-miss) TTS line can leave a jobs row.

Same pattern as 0007_script's llm_<stage> rows: jobs.model_key is a FK to model_catalog; the real provider, character
count and USD estimate live in jobs.request_payload. Inactive, zero credits, so the router never picks it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_integration"
down_revision = "0008_ledger"


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "INSERT INTO model_catalog (model_key, display_name, provider, task_type, api_base, credits_per_unit,"
        " max_duration, max_refs, is_nsfw, is_active) VALUES ('tts', 'TTS (character voices)', 'tts', 'tts',"
        " 'internal://tts', 0, 0, 0, false, false)"))


def downgrade() -> None:
    op.execute("DELETE FROM model_catalog WHERE model_key = 'tts'")
