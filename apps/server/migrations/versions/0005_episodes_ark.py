"""Episodes (script per episode) + Volcengine Ark as the verified Seedance 2.5 / Seedream vendor.

Ark endpoints and model ids follow the official docs (docs.volcengine.com/docs/82379/1520757, /1541523,
/1521309), read 2026-10-22. credits_per_unit are still PLACEHOLDERS, not Ark's price list.
"""
from alembic import op

revision = "0005"
down_revision = "0004"

ARK = "https://ark.cn-beijing.volces.com/api/v3"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE episodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            episode_number INT NOT NULL,
            title VARCHAR(255) NOT NULL DEFAULT '',
            synopsis TEXT NOT NULL DEFAULT '',
            source_segments JSONB NOT NULL DEFAULT '[]',
            script_text TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'outlined',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (project_id, episode_number)
        )""")
    op.execute("ALTER TABLE assets DROP CONSTRAINT assets_kind_check")
    op.execute("ALTER TABLE assets ADD CONSTRAINT assets_kind_check CHECK "
               "(kind IN ('image', 'video', 'audio', 'last_frame', 'episode', 'series'))")
    op.execute(f"UPDATE model_catalog SET provider='ark', api_base='{ARK}', max_refs=50 "
               "WHERE model_key='seedance_2_5_spicy'")
    op.execute("INSERT INTO model_catalog (model_key, display_name, provider, task_type, api_base, credits_per_unit,"
               " max_duration, max_refs, is_nsfw) VALUES"
               f" ('seedance_2_5_draft', 'Seedance 2.5 样片 (480p draft)', 'ark', 'video.i2v', '{ARK}', 0.3, 30, 50,"
               " false),"
               f" ('seedream_5_pro', 'Seedream 5.0 pro (定妆出图)', 'ark', 'image.t2i', '{ARK}', 0.5, 0, 10, false),"
               " ('local_assemble', '成片拼接 (local FFmpeg + OTIO)', 'local', 'post.assemble', 'local://ffmpeg', 0,"
               " 0, 0, false)")


def downgrade() -> None:
    op.execute("DELETE FROM model_catalog WHERE model_key IN ('seedance_2_5_draft','seedream_5_pro','local_assemble')")
    op.execute("UPDATE model_catalog SET provider='seedance', api_base='unconfigured://seedance' "
               "WHERE model_key='seedance_2_5_spicy'")
    op.execute("DROP TABLE episodes")
    op.execute("DELETE FROM assets WHERE kind IN ('episode', 'series')")
    op.execute("ALTER TABLE assets DROP CONSTRAINT assets_kind_check")
    op.execute("ALTER TABLE assets ADD CONSTRAINT assets_kind_check CHECK "
               "(kind IN ('image', 'video', 'audio', 'last_frame'))")
