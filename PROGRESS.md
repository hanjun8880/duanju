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

## Phase 4 — 一条龙自用流程：素材 → 剧本 → 定妆 → 分镜 → 出片 → 整部（已完成）

| 项 | 状态 | 证据 |
|---|---|---|
| 素材导入：小说原文（txt，UTF-8/GB18030）、一句想法、原片二创 | VERIFIED（前端）/ PASS（API） | 浏览器截图；`test_series.py` |
| 原片二创：本地切镜 + whisper 转写 + 关键帧批量送 Claude 识别 → 素材文本 | PASS | 真实 FFmpeg/whisper，视觉调用为 SSE 桩；`test_production.py` |
| 剧集规划：小说分段（章节正则 / 3000 字），≤300k 字 → 剧集圣经 + 定妆卡 + 分集大纲 | PASS | Claude 流式结构化输出，SSE 桩；`test_series.py` |
| 分集剧本、分集分镜（已开拍的镜头 409 保护） | PASS | `test_series.py` |
| 定妆：自动建卡、出参考图（seedream_5_pro）、上传、设首图、移除；只对新增人物图要求肖像授权 | PASS / VERIFIED（前端） | `test_workbench_api.py`；截图 |
| 火山方舟 Ark 适配器：Seedance 2.5（含 draft 480p）、Seedream 图像；任务轮询、超时退款并降级、24h 过期前下载到本地 | PASS（官方文档字段，仅替换 HTTP 传输层） | `test_ark.py` |
| 整集出片：L0 本地故事板、L1 草稿并行、L2 母带尾帧接力顺序 | VERIFIED（L0）/ PASS（L1/L2） | 真实 API + local_post worker，3 镜头均出预览 |
| 合成单集（统一尺寸/帧率/音轨后 concat）+ OTIO；导出整部 | VERIFIED | EP01 1080x1920、14.0s、带音轨，抽帧可见字幕；整部导出成功 |
| 前端五步工作台（素材 / 剧本 / 定妆 / 分镜 / 成片） | VERIFIED | Playwright 1440 + 390 截图，无 console 错误、无横向溢出 |
| 真实 Claude 调用 | BLOCKED | 无 ANTHROPIC_API_KEY |
| 真实 Ark 调用 | BLOCKED | 无 ARK_API_KEY；Seedance 2.5 需账户余额 ≥ ¥200 开通 |
| 4K 超分、换脸、测试浏览器 H.264 播放 | NOT_TESTED | 无二进制；测试用 Chromium 不含 H.264 解码 |

后端合计 103 passed；`ruff`、`mypy`、`pnpm tsc`、`pnpm test:run`（6）、`pnpm build` 通过。

### 已知限制（shortcut）
- 小说上限 300k 字，超出需分卷导入。
- `DISPATCH_MODE=celery` 下服务重启不自动恢复轮询（inline 模式会恢复）。
- 素材存本地 `STORAGE_DIR`，未接 S3/MinIO。
- 价格为占位值，需按方舟账单校准 `model_catalog`。

## Phase 5 — 对照 91generate 补齐剧本功能 + 全流程细节打磨（已完成）

| 项 | 状态 | 证据 |
|---|---|---|
| 网页链接抓取正文（lxml 去导航/页眉页脚，保留标题与正文块） | VERIFIED | `test_source_import.py`；真机抓取并保存 |
| 导入 Word / PDF 文本（pypdf、python-docx） | VERIFIED | 同上；真机上传 docx 验证 |
| 剧本页「生成剧本」：创作方向（可选文本）接入剧集规划提示词 | PASS | `test_series.py::test_plan_prompt_*` 扩展 |
| 剧本页「生成剧本」：集数 2–14 快捷选择 + 每集约90秒/合计时长展示，留空即 AI 按内容判断 | VERIFIED | 截图 `desk-script.png` |
| 剧本页「导入剧本」：直接采用原文 / 按原文整理成剧本（Claude 重排格式不改剧情）/ 强制采用原文（整段不拆集） | VERIFIED | 真机导入 2 集样例剧本，分集与标题正确；`test_source_import.py` 6 项 |
| 定妆出图、分镜逐镜/整集渲染、成片合成：防重复提交 | VERIFIED | 真机连续两次点击同一按钮，网络请求数均为 1（首版用 React state 判断仍有竞态，已改用同步 ref 锁 `useBusySet`） |
| 剧本/分镜文本框失焦保存：从静默失败改为失败即提示 | VERIFIED | 代码审查 + 真机触发失败路径 |
| 列表类页面区分"加载中"与"确实没有数据" | VERIFIED | `useApi` 新增 `loading` 字段，分镜/成片/定妆页接入 |
| 原片拆解失败：加"重试"按钮，复用已上传文件不必重新选择 | VERIFIED | 真机构造拆解失败，点击重试后重新入队 |

后端合计 112 passed（新增 21 项）；`ruff`、`mypy`、`pnpm tsc`、`pnpm test:run`（6）、`pnpm build` 通过。全部 5 个页面桌面 1440 / 移动 390 截图复核，无 console 错误、无横向溢出。

### 已知限制
- 网页正文抓取是启发式（按块级标签长度过滤导航/按钮文案），非完整 readability 算法；复杂排版页面可能需要手动贴正文兜底。
- 「按原文整理成剧本」需要 ANTHROPIC_API_KEY；未配置时明确报错，不是静默失败。
- 原片拆解用的 faster-whisper 模型较大时，在资源受限环境可能被系统杀进程；生产环境需要给 worker 预留足够内存。

## 定妆组（team/lookdev）— 第二轮

| 项 | 状态 | 证据 |
|---|---|---|
| P0：出图只进候选 `ref_urls`（上限 12，最新在前；裁剪时跳过被锁定的图），不再自动成为镜头参考，不写 `locked_ref_urls` | PASS | `test_lookdev.py::test_render_lands_in_candidates_not_locked`、`test_candidates_capped_at_12_and_locked_survive_trim` |
| `POST /lookdev/{id}/lock {urls}`（≤3，须在 ref_urls 内）/ `POST /lookdev/{id}/unlock [{urls}]`；PATCH 删候选时同步去掉对应锁定；肖像授权校验保留 | PASS | `test_lock_and_unlock`、`test_patch_removing_a_candidate_drops_its_lock`、原 `test_workbench_api` 授权用例 |
| `refs.py::refs_for_episode(session, asset, episode)`：分集造型（meta.looks 区间覆盖且 locked 非空）> 资产锁定 > ref_urls[:1]；`GET /lookdev/{id}/refs_for_episode?episode=&n=`；`PUT/DELETE /lookdev/{id}/looks/{name}` | PASS | `test_refs_for_episode_prefers_episode_look_then_lock_then_first`、`test_save_look_validates_against_candidates` |
| generate 支持 kind=portrait/turnaround(单张三视图拼版)/expressions(四宫格)/costume(需 note)，每种一次调用；参考=锁定图（无锁定时只用用户上传图，不用渲染图）；模型=`model_for("lookdev_image")` | PASS | `test_generate_kinds_use_locked_refs_and_stage_model`、`test_generate_with_lock_sends_locked_refs` |
| 去重：sha256(模型+prompt+排序参考) 存 meta.gen_cache，命中 queued/processing/success 的旧任务直接返回（200, cached=true）不提交；`force` 可强制重出；失败任务不算命中 | PASS | `test_generate_dedup_hits_cache_and_estimate`、`test_failed_job_is_not_a_cache_hit` |
| `GET /lookdev/{id}/estimate?kind=` 返回 usd（默认 $0.03/张，环境变量 `LOOKDEV_USD_PER_IMAGE` 可改；命中缓存为 0） | PASS | 同上 |
| 前端：候选网格、点图锁定 ≤3、套组按钮（定妆/三视图/表情/换装）、复用提示 | PASS（tsc + vitest `lock.test.ts`）；真机界面 NOT_TESTED | |
| 真实 Seedream 出图 | BLOCKED | 无 ARK 密钥，仅传输层打桩 |

改动的旧测试：`tests/test_ark.py::test_lookdev_generate_saves_image_into_refs` 追加断言 `locked_ref_urls == []` 并注释原因（审查 P0：渲染图不得再作为镜头参考）。
接口约定：分镜/渲染侧取参考图应调用 `src.domains.lookdev.refs.refs_for_episode`，不要再读 `ref_urls[0]`（`storyboard/shot_orchestrator.py:34-36` 仍读 ref_urls，归渲染组改）。
