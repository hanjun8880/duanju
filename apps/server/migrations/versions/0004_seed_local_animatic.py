"""L0 animatic as a zero-credit local model so every render (even free ones) has an auditable job row."""
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute("INSERT INTO model_catalog (model_key, display_name, provider, task_type, api_base, credits_per_unit,"
               " max_duration, max_refs, is_nsfw) VALUES ('local_animatic', 'L0 FFmpeg animatic (local)', 'local',"
               " 'post.animatic', 'local://ffmpeg', 0, 30, 30, false)")


def downgrade() -> None:
    op.execute("DELETE FROM model_catalog WHERE model_key='local_animatic'")
