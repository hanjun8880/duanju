# PROGRESS

状态词：VERIFIED（真实运行验证）/ PASS（测试通过）/ FAIL / BLOCKED / NOT_TESTED。

## Phase 1 — 基础设施、9 张核心表、账本引擎（已完成）

| 项 | 状态 | 证据 |
|---|---|---|
| docker-compose.yml（Postgres 16 / Redis 7 / MinIO） | PASS（`docker compose config -q`）；未拉起容器 | 沙箱无 Docker 守护进程，测试改用本机 Postgres 16 + Redis 7 |
| apps/server 初始化（uv + Python 3.13 + FastAPI + SQLAlchemy 2 async + Alembic） | VERIFIED | `uv sync`；`ruff check .` / `mypy src` 全通过 |
| 9 张表迁移（原文 DDL 逐字执行：`migrations/sql/0001_core_schema.sql`） | VERIFIED | 测试会话启动时真实执行 `alembic upgrade head`；ORM 与库表列逐表比对 |
| 种子：5 个模型 + 10 个模板（8 SFW / 2 Uncensored） | PASS | `test_schema_and_seed.py` |
| 账本：预扣、余额不足、日配额、并发行锁、实扣多退少补、5xx 全额退回、幂等、settle/refund 并发互斥 | PASS（12 项） | `test_credit_ledger.py`；去掉 `FOR UPDATE` 后并发测试失败（变异验证） |
| `GET /api/v1/models`（只暴露抽象模型代号） | PASS | `test_api.py` |
| apps/web 初始化（Next.js 15 App Router + Tailwind 4 + shadcn + Zustand + Zod + Vitest） | VERIFIED | `pnpm tsc --noEmit`、`pnpm build` 通过；尚无前端测试（Phase 3） |

后端合计 17 passed。运行：`cd apps/server && uv run pytest -q`（需要 TEST_DATABASE_URL 指向可写 Postgres）。

### 设计决定 / 偏差
- 模型单价、`provider`、`api_base` 是占位值（`unconfigured://…`），未经任何供应商核实。
- 账本新增流水原因 `settle_extra_charge`（实扣超出预估时补扣，余额允许为负并阻止下次预扣），以及 `timeout_refund` / `policy_reject_refund` / `cancel_refund`。
- 日配额 = 当日（数据库时区）除 `manual_topup` 外所有流水的净支出，退款会释放配额。
- `video.edit` 的计费单位（输入秒 + 输出秒）由调用方传入 `billable_units`。
- `.claude/settings.json` 的 PostToolUse hook 改为从 stdin JSON 读取文件路径（原文 `$CLAUDE_FILE_PATHS` 不存在）。
- `0002` 种子迁移的 downgrade 在已有 jobs 时会被外键拒绝（有意不级联删业务数据）。

## Phase 2 — 网关、编剧引擎、二创拆解、供应商适配器（已完成）

| 项 | 状态 | 证据 |
|---|---|---|
| `JobCreateRequest` + 预检清洗（首尾帧↔多参考互斥、空参考降级 T2V 并去掉 omni 字段、video.edit 强制 adaptive/-1、30/10/10 上限、纯音频参考拒绝） | PASS | `test_gateway_schemas.py` |
| 未成年人零容忍拦截 + 审计日志；18+ 门禁；face.swap 肖像授权声明 | PASS | `test_safety.py`、`test_router.py` |
| `CachedDirectorLLM`：`claude-opus-5-5`，system + SERIES_BIBLE 两段 1h 缓存、字节稳定、`output_config.format` json_schema、无 prefill、拒答检测、`fallbacks: "default"` | PASS（真实 Anthropic SDK 序列化，仅替换 HTTP 传输层） | `test_script_engine.py` |
| 真实 Claude 调用 | BLOCKED | 本环境无 ANTHROPIC_API_KEY |
| uncensored_nsfw 路由到私有 OpenAI 兼容 LLM（从不调 Claude） | PASS（路由）/ NOT_TESTED（真实私有模型） | 需要 `UNCENSORED_LLM_BASE_URL` |
| `LocalVideoDeconstructor`：FFmpeg 16k 单声道 + PySceneDetect + faster-whisper | VERIFIED | 真实合成 12s 三镜头视频 + JFK 语音样本：切出 3 镜头、16000Hz/1ch、转写含 "ask not … country" |
| 供应商适配器（seedance / wan / minimax / flux）+ HMAC Webhook 验签 + 5xx/超时退款并单跳降级 seedance→wan、4xx 不降级、未配置供应商取消并退款、回调幂等、按实际秒数结算 | PASS（仅替换 HTTP 传输层） | `test_router.py` |
| 真实供应商调用 | BLOCKED / NOT_TESTED | 无供应商密钥；字段名、路径、签名方式未经官方文档核实 |
| `POST /api/v1/jobs`、`POST /api/v1/webhooks/{provider}` | PASS | `test_router.py` |

后端合计 68 passed；`ruff check .`、`mypy src` 通过。

### 核实结论
- Seedance 2.5、Wan 3.0（Alibaba `wan3.0-video`）、MiniMax H3（`MiniMax-H3`，4–15s）均确有其模型，但只找到转售商文档，字段名互相矛盾（如 `first_frame_url` vs `start_image`，`reference_image_urls` vs `reference_images`），所以字段映射、路径、模型 ID 都放在 `adapters/*.py` 的 `ProviderConfig` 里。
- Prompt Caching：1h TTL 的写入价是基础输入价的 2 倍（规格写的 +25% 是 5m TTL 的价格），读取约 0.1 倍（Opus 5.5 为 0.05 倍）；至少 3 次复用才回本。
- Claude 4.6+ 禁止 prefill、`output_config.format`：已按 SDK（anthropic 2.1）核实。Opus 5.5 不支持 `budget_tokens`，改用 `output_config.effort`。

### 偏差 / 未做
- 新增 `insightface_swap_pro`（本地，0 积分）种子模型；本地模型任务暂不分发（Phase 3 本地队列）。
- 回调成功后产出素材 `s3_key` 先记为 `pending/...`，下载到 MinIO 放 Phase 3（沙箱无 MinIO）。
- 二创关键帧批量送 VLM 反推分镜未实现。
- 依赖：`av<17`（faster-whisper 1.2 与 PyAV 17+ 不兼容）。

## Phase 3 — 队列、前端导演工作台（已完成）

| 项 | 状态 | 证据 |
|---|---|---|
| Celery 队列（high_priority / video.<model_key> / local_post），`DISPATCH_MODE=celery\|inline` | VERIFIED | `test_queue.py`：真实 Redis + 真实 worker 往返 |
| 本地 L0 Animatic（FFmpeg 拼帧 + 中文字幕烧录） | VERIFIED | uvicorn + 真实 local_post worker 出片，抽帧检查中文字幕可见 |
| 分镜编排：导入分镜、绑定定妆参考（≤30）、L1 minimax_h3 480p / L2 seedance 1080p + 尾帧接力、回调后挂接下一镜 | PASS | `test_orchestrator.py`、`test_workbench_api.py` |
| 成片拼接（OTIO + concat） | PASS | `test_post.py` |
| 4K 超分（Real-ESRGAN）、换脸（FACE_SWAP_CMD） | NOT_TESTED | 沙箱无二进制；缺失时返回 ToolMissing 并全额退款 |
| 管理后台：模型价格/启停、任务监控、手动退款（二次 409）、重试、账本流水 | PASS | `test_workbench_api.py` |
| 前端导演台（剧本导入 / 定妆 / 分镜长卷 / 账本与模型抽屉），宋式青瓷配色 | VERIFIED | Playwright 1440 桌面 + 390 移动端截图人工复核；无横向溢出、无 console 错误 |
| 前端契约：Zod 与后端 openapi.json 字段/必填项一致；积分预估 | PASS | vitest 6 passed |
| 真实供应商 / Claude / MinIO | BLOCKED | 无密钥、无 Docker 守护进程 |

后端合计 87 passed；`ruff`、`mypy`、`pnpm tsc`、`pnpm build` 通过。

### 未做
- 二创关键帧批量送 VLM 反推分镜。
- 用户鉴权（目前单一默认导演账号 `director`）。
- S3/MinIO 预签名上传与素材转存（目前本地 `storage/` + `/media` 静态目录）。
