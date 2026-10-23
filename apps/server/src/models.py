"""ORM mapping of the 9 core tables. The schema itself is owned by migrations/sql/0001_core_schema.sql;
tests/test_schema_and_seed.py fails if these columns drift from the migrated database."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import ForeignKey, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Json = dict[str, Any]


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB, uuid.UUID: UUID(as_uuid=True)}


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))


def created() -> Mapped[datetime]:
    return mapped_column(server_default=text("now()"))


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    username: Mapped[str] = mapped_column(String(64), unique=True)
    credits: Mapped[Decimal] = mapped_column(Numeric(12, 4), server_default="1000")
    daily_limit_credits: Mapped[Decimal] = mapped_column(Numeric(12, 4), server_default="200")
    plan: Mapped[str] = mapped_column(String(32), server_default="director_pro")
    age_verified_18plus: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = created()


class ModelCatalog(Base):
    __tablename__ = "model_catalog"
    model_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(32))
    api_base: Mapped[str] = mapped_column(String(255))
    credits_per_unit: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    resolution_multipliers: Mapped[Json]
    max_duration: Mapped[int]
    max_refs: Mapped[int]
    is_nsfw: Mapped[bool]
    fallback_model_key: Mapped[str | None] = mapped_column(ForeignKey("model_catalog.model_key"))
    rate_limit_qps: Mapped[int]
    max_concurrency: Mapped[int]
    health_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    is_active: Mapped[bool]
    usd_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[uuid.UUID] = pk()
    category: Mapped[str] = mapped_column(String(32))
    relation: Mapped[str | None] = mapped_column(String(64))
    pose: Mapped[str] = mapped_column(String(128))
    scene: Mapped[str] = mapped_column(String(128))
    default_prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    recommended_duration: Mapped[int] = mapped_column(server_default="5")
    refs: Mapped[list[Any]] = mapped_column(server_default="[]")
    is_nsfw: Mapped[bool] = mapped_column(server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(server_default="0")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32), server_default="series")
    content_mode: Mapped[str] = mapped_column(String(32), server_default="standard_sfw")
    aspect_ratio: Mapped[str] = mapped_column(String(16), server_default="9:16")
    series_bible_json: Mapped[Json] = mapped_column(server_default="{}")
    source_novel_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), server_default="draft")
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = created()


class LookdevAsset(Base):
    __tablename__ = "lookdev_assets"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    asset_type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
    role_tag: Mapped[str | None] = mapped_column(String(64))
    visual_prompt: Mapped[str] = mapped_column(Text)
    ref_urls: Mapped[list[Any]] = mapped_column(server_default="[]")
    face_embedding_url: Mapped[str | None] = mapped_column(String(512))
    meta: Mapped[Json] = mapped_column(server_default="{}")
    locked_ref_urls: Mapped[list[Any]] = mapped_column(server_default="[]")
    voice: Mapped[Json] = mapped_column(server_default="{}")
    created_at: Mapped[datetime] = created()


class Episode(Base):
    __tablename__ = "episodes"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    episode_number: Mapped[int]
    title: Mapped[str] = mapped_column(String(255), server_default="")
    synopsis: Mapped[str] = mapped_column(Text, server_default="")
    source_segments: Mapped[list[Any]] = mapped_column(server_default="[]")
    script_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), server_default="outlined")
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = created()


class Shot(Base):
    __tablename__ = "shots"
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    episode_number: Mapped[int] = mapped_column(server_default="1")
    shot_order: Mapped[int]
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id"))
    render_tier: Mapped[str] = mapped_column(String(16), server_default="L0_animatic")
    duration: Mapped[int] = mapped_column(server_default="5")
    resolution: Mapped[str] = mapped_column(String(16), server_default="720p")
    shot_prompt: Mapped[str] = mapped_column(Text)
    dialogue_transcript: Mapped[str | None] = mapped_column(Text)
    speaker_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lookdev_assets.id"))
    first_frame_url: Mapped[str | None] = mapped_column(String(512))
    last_frame_url: Mapped[str | None] = mapped_column(String(512))
    refs: Mapped[Json] = mapped_column(server_default='{"images": [], "videos": [], "audios": []}')
    source_remix_meta: Mapped[Json | None]
    status: Mapped[str] = mapped_column(String(32), server_default="pending")
    shot_kind: Mapped[str] = mapped_column(String(16), server_default="dialogue")
    continuous_from_prev: Mapped[bool] = mapped_column(server_default=text("false"))
    keyframe_url: Mapped[str | None] = mapped_column(String(512))
    keyframe_status: Mapped[str] = mapped_column(String(16), server_default="none")
    selected_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created()


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = pk()
    shot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shots.id", ondelete="SET NULL"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_type: Mapped[str] = mapped_column(String(32))
    model_key: Mapped[str] = mapped_column(ForeignKey("model_catalog.model_key"))
    provider_job_id: Mapped[str | None] = mapped_column(String(128))
    request_payload: Mapped[Json]
    credit_estimate: Mapped[Decimal] = mapped_column(Numeric(10, 4), server_default="0")
    actual_credits: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    status: Mapped[str] = mapped_column(String(32), server_default="queued")
    error_message: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created()
    completed_at: Mapped[datetime | None]


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[uuid.UUID] = pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    s3_key: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    meta: Mapped[Json] = mapped_column(server_default="{}")
    created_at: Mapped[datetime] = created()


class CreditLedger(Base):
    __tablename__ = "credit_ledger"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    delta: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    reason: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created()
