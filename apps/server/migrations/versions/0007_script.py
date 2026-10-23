"""Script team: llm_<stage> placeholder model_catalog rows so every LLM call can leave a jobs row.

jobs.model_key is a FK to model_catalog; the real model id is in jobs.request_payload. Inactive, zero credits,
so the video/image router never picks them.
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_script"
down_revision = "0006"

STAGES = ["summarize", "plan", "script", "storyboard", "reformat", "remix_vision", "remix_rewrite"]


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "INSERT INTO model_catalog (model_key, display_name, provider, task_type, api_base, credits_per_unit,"
        " max_duration, max_refs, is_nsfw, is_active) VALUES (:k, :n, 'llm', 'llm', 'internal://llm', 0, 0, 0,"
        " false, false)"), [{"k": f"llm_{s}", "n": f"LLM ({s})"} for s in STAGES])


def downgrade() -> None:
    op.execute("DELETE FROM model_catalog WHERE model_key LIKE 'llm\\_%'")
