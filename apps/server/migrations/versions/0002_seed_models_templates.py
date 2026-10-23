"""Seed default model catalog + 10 zero-token templates.

shortcut: provider names, api_base and credits_per_unit are PLACEHOLDERS (not verified against any
vendor's docs or price list); edit them in the admin drawer / model_catalog once real accounts exist.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"

# (model_key, display_name, provider, task_type, api_base, credits_per_unit, max_duration, max_refs, is_nsfw)
MODELS = [
    ("seedance_2_5_spicy", "Seedance 2.5 (multi-ref long take)", "seedance", "video.i2v",
     "unconfigured://seedance", 1.0, 30, 50, True),
    ("wan3_spicy", "Wan 3 Spicy", "wan", "video.i2v", "unconfigured://wan", 0.6, 30, 50, True),
    ("minimax_h3", "MiniMax H3 (draft)", "minimax", "video.i2v", "unconfigured://minimax", 0.3, 10, 1, False),
    ("flux_uncensored", "Flux Uncensored (look-dev T2I)", "flux", "image.t2i", "unconfigured://flux",
     0.5, 0, 4, True),
    ("realesrgan_x4plus", "Real-ESRGAN x4plus (local 4K)", "local", "post.upscale", "local://realesrgan",
     0, 0, 0, False),
]
FALLBACKS = {"seedance_2_5_spicy": "wan3_spicy", "wan3_spicy": "seedance_2_5_spicy"}

NEG = "deformed hands, extra limbs, face distortion, watermark, text artifacts"
# (category, relation, pose, scene, default_prompt, duration, is_nsfw)
TEMPLATES = [
    ("standard_drama", "confrontation", "face-off across desk", "penthouse study, night",
     "Two adults face off across a walnut desk, low-key tungsten light, slow push-in", 8, False),
    ("standard_drama", "couple", "rain umbrella share", "neon street, rain",
     "Adult couple shares one umbrella, rain streaks backlit by neon, slow dolly back", 10, False),
    ("standard_drama", "solo", "window silhouette", "rooftop, dawn",
     "Lone figure at rooftop edge, dawn haze, camera arcs 90 degrees to reveal skyline", 6, False),
    ("standard_drama", "group", "boardroom reveal", "glass boardroom, day",
     "Executives turn toward the door as protagonist enters, rack focus foreground to door", 8, False),
    ("action_choreo", "confrontation", "wrist lock counter", "underground parking lot",
     "Attacker lunges, defender parries and locks wrist, handheld tracking, sodium vapor light", 6, False),
    ("action_choreo", "solo", "sword draw iaido", "bamboo forest, mist",
     "Swordsman draws in one motion, bamboo sways, low-angle slow motion", 5, False),
    ("camera_move", None, "dolly zoom", "corridor",
     "Subject static mid-frame, dolly zoom background stretches, tension build", 5, False),
    ("camera_move", None, "establishing aerial", "city skyline",
     "Aerial descent over skyline into a single lit window, golden hour", 8, False),
    ("uncensored_pose", "couple", "intimate embrace", "bedroom, low light",
     "Two consenting adult characters (25+) in a close embrace, warm practical lamps, slow push-in", 8, True),
    ("uncensored_pose", "couple", "sensual dance", "private lounge",
     "Two consenting adult characters (25+) slow dance, silk drapes, shallow depth of field", 8, True),
]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO model_catalog (model_key, display_name, provider, task_type, api_base, credits_per_unit,"
        " max_duration, max_refs, is_nsfw) VALUES (:k,:n,:p,:t,:a,:c,:d,:r,:x)"),
        [dict(zip("knptacdrx", m)) for m in MODELS])
    bind.execute(sa.text("UPDATE model_catalog SET fallback_model_key=:f WHERE model_key=:k"),
                 [{"k": k, "f": f} for k, f in FALLBACKS.items()])
    bind.execute(sa.text(
        "INSERT INTO templates (category, relation, pose, scene, default_prompt, negative_prompt,"
        " recommended_duration, refs, is_nsfw, sort_order)"
        " VALUES (:c,:rel,:p,:s,:dp,:neg,:d,CAST(:refs AS JSONB),:x,:o)"),
        [{"c": c, "rel": rel, "p": p, "s": s, "dp": dp, "neg": NEG, "d": d, "refs": json.dumps([]), "x": x, "o": i}
         for i, (c, rel, p, s, dp, d, x) in enumerate(TEMPLATES)])


def downgrade() -> None:
    op.execute("DELETE FROM templates")
    op.execute("UPDATE model_catalog SET fallback_model_key=NULL")
    op.execute("DELETE FROM model_catalog")
