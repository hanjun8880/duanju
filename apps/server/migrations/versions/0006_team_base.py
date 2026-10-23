"""Hark team base: columns every workstream shares (lock/keyframe/kind/continuity/take/voice/budget/usd).

Data changes (prices, seeds) live in each team's own later migration.
"""
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE lookdev_assets ADD COLUMN locked_ref_urls JSONB NOT NULL DEFAULT '[]';
        ALTER TABLE lookdev_assets ADD COLUMN voice JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE shots ADD COLUMN shot_kind VARCHAR(16) NOT NULL DEFAULT 'dialogue';
        ALTER TABLE shots ADD COLUMN continuous_from_prev BOOLEAN NOT NULL DEFAULT false;
        ALTER TABLE shots ADD COLUMN keyframe_url VARCHAR(512);
        ALTER TABLE shots ADD COLUMN keyframe_status VARCHAR(16) NOT NULL DEFAULT 'none';
        ALTER TABLE shots ADD COLUMN selected_job_id UUID REFERENCES jobs(id) ON DELETE SET NULL;
        ALTER TABLE shots ADD COLUMN content_hash VARCHAR(64);
        ALTER TABLE projects ADD COLUMN budget_usd NUMERIC(10,2);
        ALTER TABLE episodes ADD COLUMN budget_usd NUMERIC(10,2);
        ALTER TABLE model_catalog ADD COLUMN usd_per_unit NUMERIC(10,4);
        ALTER TABLE jobs ADD COLUMN content_hash VARCHAR(64);
        CREATE INDEX jobs_content_hash_idx ON jobs(content_hash) WHERE status = 'success';
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS jobs_content_hash_idx;
        ALTER TABLE jobs DROP COLUMN content_hash;
        ALTER TABLE model_catalog DROP COLUMN usd_per_unit;
        ALTER TABLE episodes DROP COLUMN budget_usd;
        ALTER TABLE projects DROP COLUMN budget_usd;
        ALTER TABLE shots DROP COLUMN content_hash, DROP COLUMN selected_job_id, DROP COLUMN keyframe_status,
            DROP COLUMN keyframe_url, DROP COLUMN continuous_from_prev, DROP COLUMN shot_kind;
        ALTER TABLE lookdev_assets DROP COLUMN voice, DROP COLUMN locked_ref_urls;
    """)
