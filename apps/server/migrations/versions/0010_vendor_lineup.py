"""Expert lineup 2026-10-23 (Victor's 7-window pricing pass, verbally checked against vendor price pages):
cheaper per-stage model_catalog rows for video/image, + an inactive Keling placeholder.

usd_per_unit derived from Victor's per-unit CNY prices at ~7.2 CNY/USD (seedance/wan: per second; seedream:
per image). Still third-party-reported, NOT independently verified against an official USD price list.
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_vendor_lineup"
down_revision = "0009_integration"

ARK = "https://ark.cn-beijing.volces.com/api/v3"
WAN = "unconfigured://wan"
KELING = "unconfigured://keling"

# (model_key, display_name, provider, task_type, api_base, credits_per_unit, max_duration, max_refs, is_nsfw,
#  is_active, usd_per_unit)
MODELS = [
    ("seedance_2_0_mini", "Seedance 2.0 mini (主力 720p)", "ark", "video.i2v", ARK, 0.6, 30, 50, True, True, 0.0694),
    ("seedance_2_0_mini_draft", "Seedance 2.0 mini 草稿 (480p)", "ark", "video.i2v", ARK, 0.3, 30, 50, True, True,
     0.0333),
    ("seedream_5_flash", "Seedream 5.0 flash (关键帧)", "ark", "image.t2i", ARK, 0.25, 0, 10, False, True, 0.0167),
    ("wan2_2_i2v_flash", "Wan 2.2 i2v flash (空镜/过场)", "wan", "video.i2v", WAN, 0.3, 10, 10, False, True, 0.0139),
    ("keling_4_0", "可灵 4.0 (打戏，接口未配置)", "keling", "video.i2v", KELING, 1.0, 10, 10, True, False, None),
]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO model_catalog (model_key, display_name, provider, task_type, api_base, credits_per_unit,"
        " max_duration, max_refs, is_nsfw, is_active, usd_per_unit) VALUES"
        " (:k,:n,:p,:t,:a,:c,:d,:r,:x,:act,:u)"),
        [{"k": k, "n": n, "p": p, "t": t, "a": a, "c": c, "d": d, "r": r, "x": x, "act": act, "u": u}
         for k, n, p, t, a, c, d, r, x, act, u in MODELS])
    # Seedream 5.0 pro's placeholder price (0.03) replaced with Victor's checked figure (0.30 元/张 ≈ 0.0417 美元)
    bind.execute(sa.text("UPDATE model_catalog SET usd_per_unit=0.0417 WHERE model_key='seedream_5_pro'"))


def downgrade() -> None:
    op.execute("UPDATE model_catalog SET usd_per_unit=0.03 WHERE model_key='seedream_5_pro'")
    op.execute("DELETE FROM model_catalog WHERE model_key IN "
              "('seedance_2_0_mini','seedance_2_0_mini_draft','seedream_5_flash','wan2_2_i2v_flash','keling_4_0')")
