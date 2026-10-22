-- 启用 UUID 与 JSONB 索引扩展
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. 用户与全局预算配额表
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(64) UNIQUE NOT NULL,
    credits NUMERIC(12, 4) NOT NULL DEFAULT 1000.0000,
    daily_limit_credits NUMERIC(12, 4) NOT NULL DEFAULT 200.0000,
    plan VARCHAR(32) NOT NULL DEFAULT 'director_pro',
    age_verified_18plus BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. 模型目录与网关路由表 (后台动态可控开关与单价)
CREATE TABLE model_catalog (
    model_key VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(128) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    task_type VARCHAR(32) NOT NULL, -- image.t2i | image.i2i | video.i2v | video.t2v | video.edit | face.swap | post.upscale
    api_base VARCHAR(255) NOT NULL,
    credits_per_unit NUMERIC(10, 4) NOT NULL, -- 每秒或每张基础积分
    resolution_multipliers JSONB NOT NULL DEFAULT '{"480p": 1.0, "720p": 1.8, "1080p": 3.2}',
    max_duration INT NOT NULL DEFAULT 30,
    max_refs INT NOT NULL DEFAULT 50,
    is_nsfw BOOLEAN NOT NULL DEFAULT FALSE,
    fallback_model_key VARCHAR(64) REFERENCES model_catalog(model_key),
    rate_limit_qps INT NOT NULL DEFAULT 2,
    max_concurrency INT NOT NULL DEFAULT 5,
    health_score NUMERIC(5, 2) NOT NULL DEFAULT 100.00,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- 3. 零 Token 消耗模板库 (关系/姿态/场景/预置静帧)
CREATE TABLE templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category VARCHAR(32) NOT NULL, -- standard_drama | action_choreo | uncensored_pose | camera_move
    relation VARCHAR(64),          -- solo | couple | confrontation | group
    pose VARCHAR(128) NOT NULL,
    scene VARCHAR(128) NOT NULL,
    default_prompt TEXT NOT NULL,
    negative_prompt TEXT,
    recommended_duration INT NOT NULL DEFAULT 5,
    refs JSONB NOT NULL DEFAULT '[]', -- 预置动作静帧或构图骨架 URL 数组
    is_nsfw BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order INT NOT NULL DEFAULT 0
);

-- 4. 连续剧项目表 (含世界观圣经缓存块)
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    title VARCHAR(255) NOT NULL,
    category VARCHAR(32) NOT NULL DEFAULT 'series', -- series | remix | short
    content_mode VARCHAR(32) NOT NULL DEFAULT 'standard_sfw', -- standard_sfw | uncensored_nsfw
    aspect_ratio VARCHAR(16) NOT NULL DEFAULT '9:16',
    series_bible_json JSONB NOT NULL DEFAULT '{}', -- 用于 Anthropic Prompt Caching 的全局静态圣经
    source_novel_text TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. 三合一定妆资产库 (人物 Character / 道具 Prop / 场景 Scene)
CREATE TABLE lookdev_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_type VARCHAR(16) NOT NULL CHECK (asset_type IN ('character', 'prop', 'scene')),
    name VARCHAR(128) NOT NULL,
    role_tag VARCHAR(64), -- 如 protagonist, antagonist, key_item, main_location
    visual_prompt TEXT NOT NULL,
    ref_urls JSONB NOT NULL DEFAULT '[]', -- 选定入库的定妆多角度标准图 URL 有序数组
    face_embedding_url VARCHAR(512),      -- 供 InsightFace 校验与换脸使用
    meta JSONB NOT NULL DEFAULT '{}',     -- 服装、音色参考 audio_url 等扩展属性
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. 剧集与分镜表 (支持单剧集多镜头与二创溯源)
CREATE TABLE shots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    episode_number INT NOT NULL DEFAULT 1,
    shot_order INT NOT NULL,
    template_id UUID REFERENCES templates(id),
    render_tier VARCHAR(16) NOT NULL DEFAULT 'L0_animatic', -- L0_animatic | L1_draft | L2_master | L3_post
    duration INT NOT NULL DEFAULT 5 CHECK (duration = -1 OR (duration >= 4 AND duration <= 30)),
    resolution VARCHAR(16) NOT NULL DEFAULT '720p',
    shot_prompt TEXT NOT NULL, -- 五段式连续动作提示词
    dialogue_transcript TEXT,
    speaker_asset_id UUID REFERENCES lookdev_assets(id),
    first_frame_url VARCHAR(512),
    last_frame_url VARCHAR(512),
    refs JSONB NOT NULL DEFAULT '{"images": [], "videos": [], "audios": []}', -- 绑定的定妆图/二创原片片段
    source_remix_meta JSONB, -- 二创源视频切点 start_time, end_time, keyframe_urls
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. 异步生成任务表
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shot_id UUID REFERENCES shots(id) ON DELETE SET NULL,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_type VARCHAR(32) NOT NULL,
    model_key VARCHAR(64) NOT NULL REFERENCES model_catalog(model_key),
    provider_job_id VARCHAR(128),
    request_payload JSONB NOT NULL,
    credit_estimate NUMERIC(10, 4) NOT NULL DEFAULT 0,
    actual_credits NUMERIC(10, 4),
    status VARCHAR(32) NOT NULL DEFAULT 'queued', -- queued | processing | success | failed | cancelled | dead_letter
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- 8. 产出多媒体素材表
CREATE TABLE assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    s3_key VARCHAR(512) NOT NULL,
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('image', 'video', 'audio', 'last_frame')),
    meta JSONB NOT NULL DEFAULT '{}', -- 分辨率、时长、面部相似度得分等
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9. 积分与算力成本流水账本
CREATE TABLE credit_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    job_id UUID REFERENCES jobs(id),
    delta NUMERIC(12, 4) NOT NULL, -- 负数表示预扣/实扣，正数表示充值/失败退回
    balance NUMERIC(12, 4) NOT NULL,
    reason VARCHAR(64) NOT NULL,   -- pre_deduct | settle_refund | provider_5xx_refund | manual_topup
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
