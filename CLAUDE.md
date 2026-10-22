# AI Short-Drama & Series Director Workbench - Agent Governance Contract

Full spec: @ARCH_SPEC.md. Progress / verified status: @PROGRESS.md.

## 1. Core Architecture & Domain Rules (NON-NEGOTIABLE)
- Use the simplest possible approach: no unsolicited abstractions or premature wrappers. Organize code strictly by domain (script, lookdev, storyboard, gateway) under apps/server/src/domains/.
- Unified Gateway Only: Frontend (apps/web) MUST ONLY call POST /api/v1/jobs and domain REST endpoints. Never call third-party model SDKs from the frontend or expose provider API keys.
- Anthropic API Rules (Claude 4.6+):
  - Default models: `claude-opus-5-5` (script/storyboard), `claude-sonnet-5-5` (light extraction). Override via env, never hard-code elsewhere.
  - NEVER use assistant message prefill (HTTP 400 on Claude 4.6+).
  - ALWAYS use output_config.format with type "json_schema" for typed extraction, and strict: true for tool definitions.
  - ALWAYS attach cache_control {"type": "ephemeral", "ttl": "1h"} to the system prompt and the SERIES_BIBLE block. Keep those blocks byte-stable (sorted JSON, no timestamps).
- Seedance 2.5 & Video Gateway Rules:
  - first_frame_url/last_frame_url MUST NOT coexist with reference image/video/audio arrays (max 30 / 10 / 10).
  - No reference assets -> strip omni_reference_task_type and route as pure T2V.
  - task_type == "video.edit" -> force aspect_ratio = "adaptive" and duration = -1.
  - Video generation is async: pre-deduct credits (row lock) -> enqueue Redis job -> webhook/callback -> download to S3 -> settle actual credits (full refund on 5xx/timeout, failover seedance_2_5_spicy -> wan3_spicy).
  - Provider field names / endpoints / prices are UNVERIFIED: keep them in adapter config, never scattered in business code.
- Video Remix Zero-Token Preprocessing: never send raw frames to cloud VLMs. Run local FFmpeg (16kHz mono WAV) + PySceneDetect ContentDetector(threshold=27.0, min_scene_len=15) + faster-whisper first; 1 keyframe per shot.
- Safety: uncensored_nsfw requires users.age_verified_18plus; minors-related content is hard-blocked with audit log; real-face uploads / face.swap require a likeness-consent declaration.

## 2. Build, Lint & Verification Commands
- Local services: docker compose up -d postgres redis minio  (or local postgres 16 + redis 7)
- Backend setup & test: cd apps/server && uv sync && uv run pytest -v   (needs TEST_DATABASE_URL, default postgresql+asyncpg://postgres:postgres@localhost:5432/duanju_test)
- Backend lint & types: cd apps/server && uv run ruff check . && uv run mypy src
- Migrations: cd apps/server && uv run alembic upgrade head
- Frontend: cd apps/web && pnpm install && pnpm tsc --noEmit && pnpm test:run && pnpm build

## 3. TDD & Context Hygiene Workflow
- Write failing tests first for every domain module, confirm they fail, then implement until green.
- NEVER modify tests to make them pass; fix the root cause. No mocks of validation / ledger logic; external HTTP may be stubbed at the transport layer only.
- Status words: VERIFIED / PASS / FAIL / BLOCKED / NOT_TESTED. Never claim a provider call works without a real call.
- After each milestone phase, update PROGRESS.md so the session can be reset with /clear.
