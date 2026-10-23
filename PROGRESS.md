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

## Team script（编剧组，分支 team/script）

| 项 | 状态 | 证据 |
|---|---|---|
| 所有 LLM 调用按环节取 `model_for(stage)`（plan/script/storyboard/reformat/summarize/remix_vision），删除 `DEFAULT_MODEL`/`LIGHT_MODEL` 硬编码 | PASS | `test_llm_routing.py` |
| `provider=openai_compat` 走 `OPENAI_COMPAT_BASE_URL` 的 `/v1/chat/completions`（`OPENAI_COMPAT_API_KEY`），未配置 → 503 | PASS（传输层桩）/ BLOCKED（无真实端点） | `test_llm_routing.py` |
| 分镜 ShotSchema：`shot_kind`、`continuous_from_prev`、`speaker`；duration 4–10 秒（提示 4–8 秒为主）；删去两处"15-30 秒长镜头"提示 | PASS | `test_orchestrator.py::test_storyboard_shots_are_short_takes` |
| `import_storyboard` 写入 shot_kind / continuous_from_prev（首镜强制 false）/ speaker_asset_id（按名称匹配人物定妆卡，未匹配为空） | PASS | `test_orchestrator.py::test_import_storyboard_writes_kind_continuity_and_speaker` |
| 两段式规划：上限 200 万字；>4 万字时每 4 段一批 summarize 摘要（并发≤3，按片段 sha256 缓存在 `series_bible_json._cache`），plan 出设定，大纲每批≤20 集（共享缓存前缀）；超大章节再切分 | PASS | `test_two_stage_plan.py`（重规划零摘要调用、改一章只重摘一段） |
| 规划进度写 `series_bible_json._progress`，`GET /projects/{id}/plan/progress`；失败写 error；`bible_text` 与 `project_view` 排除 `_` 开头键 | PASS | `test_two_stage_plan.py` |
| 每次 LLM 调用写一条 jobs：`task_type='llm.<stage>'`、`model_key='llm_<stage>'`、credit 0、payload {model, provider, usage, usd, stage}；被拒/截断的调用也记账；remix 关键帧识别也记账 | PASS | `test_llm_ledger.py`、`test_production.py` |
| 迁移 `0007_script`：插入 7 条 `llm_*` model_catalog 占位行（is_active=false，不参与路由） | PASS | 测试库 alembic upgrade head |
| LLM 单价放 `settings.llm_prices`（USD/百万 token，占位未核实，env `LLM_PRICES` 整表覆盖）；`GET /projects/{id}/llm-usage` 按环节汇总 | PASS | `test_llm_ledger.py` |
| 前端剧本页：规划时轮询进度、显示编剧 AI 花费 | PASS（单测 + tsc）/ NOT_TESTED（未截图） | `progress.test.ts` |
| 真实 Claude / OpenAI 兼容调用 | BLOCKED | 无供应商密钥 |

后端 128 passed；ruff、mypy 通过；前端 `pnpm tsc`、`pnpm test:run`（8）通过。

### 改动的旧测试（审查意见要求的行为变更）
- `test_script_engine.py`：STORYBOARD 时长 15→6；分镜请求模型从写死 `claude-opus-5-5` 改为 `model_for("storyboard").model`。
- `test_orchestrator.py`：BOARD 第 1 镜时长 20→10（le=10），对应 L1/L2 请求断言同步。

### 已知限制
- 规划仍是一次同步 HTTP 请求（长篇可能几分钟）；进度靠轮询 `_progress`。同一项目并发两次规划未加锁。
- 单价是占位值；未知模型 usd 记为 null（`unpriced_calls` 计数），不猜价。
- openai_compat 只支持纯文本 prompt（remix 关键帧识别仍需 anthropic）。

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

## 专家团 · render 组（分支 team/render，2026-10-23）

| 项 | 状态 | 证据 |
|---|---|---|
| P0 首帧只来自 keyframe_status='approved' 的 keyframe_url，或 continuous_from_prev 时上一镜尾帧；其余镜头无首帧走参考图模式 | PASS | `test_orchestrator.py::test_master_chains_previous_last_frame`（改写）、`test_first_frame_only_from_approved_keyframe` |
| on_shot_rendered 只给 continuous_from_prev 的下一镜写首帧 | PASS | `test_last_frame_only_written_to_continuous_next` |
| 迟绑定参考：出片时按资产现值取参考（locked_ref_urls[:3]，空则 ref_urls[:1]），顺序 人物→场景→道具→模板；导入时另存 refs.asset_names | PASS | `test_refs_bound_late_from_locked_assets`、`test_tier_requests`（改写） |
| 路由：shot_request 用 video_stage_for + model_for 取模型/分辨率；L2 默认 720p 按镜头类型（对白→seedance，动作/空镜/插入→wan3），1080p 仅 `final=true` | PASS | `test_master_model_routed_by_shot_kind`（含 MODEL_VIDEO_ACTION 环境覆盖） |
| _failover 保留 batch/final；镜头彻底失败标 failed，其后连续镜头一并 failed，整集批次从下一个非连续镜头继续（内容拦截同样跳段，余额不足仍停） | PASS | `test_failed_chain_skips_to_next_cut`、`test_batch_failover_keeps_batch_and_dead_vendors_resume_at_cut` |
| POST /api/v1/shots/{id}/select {job_id|null}；best_clips 优先 selected_job_id；镜头列表返回 shot_kind、continuous_from_prev、keyframe_url/status、selected_job_id、takes；PATCH 可改 shot_kind/continuous_from_prev | PASS | `test_select_take_overrides_best_clip` |
| 前端 storyboard-canvas：每镜类型/「接上一镜」开关、关键帧状态、版本列表与选用 | PASS（tsc + vitest），浏览器 NOT_TESTED | 未跑 pnpm build/截图 |
| 真实供应商出片 | BLOCKED | 无密钥 |

后端 119 passed（基线 112，+7）；ruff、mypy 通过；pnpm tsc、pnpm test:run（6）通过。

### 改动的旧测试（审查意见要求的行为变更）
- `test_master_chains_previous_last_frame`：旧断言要求任何下一镜都接上一镜尾帧并丢掉参考图；改为硬切保留参考图、仅 continuous 镜头接尾帧。
- `test_render_and_callback_propagates_last_frame`：显式把第 2 镜标为连续；请求加 `final: true` 以保留原 1080p 计费断言。
- `test_tier_requests`：参考图改为迟绑定（每资产 1 张未锁定图，顺序 人物→场景→道具）；母带 1080p 断言改为 `final=True`，并新增默认 720p 断言。

### 合并注意
- `shot_orchestrator.refs_for_episode` 是临时实现，合并时换成 lookdev 组的 `lookdev/refs.py`（签名一致）。
- 共享文件改动：`gateway/router.py`（新增 `_shot_failed` 钩子、_failover 复制 batch/final、render_next 透传 final）。
- 前端 `lib/estimate.ts` 的 tierPlan 仍按 L2=1080p 估价，未改（共享文件）；合并后应按 routing 表更新，否则母带预估偏高。
- `ShotX` 本地类型补了新字段，contracts.ts 重生成后可删。

## 专家团 · 关键帧/账本组（分支 team/ledger）

| 项 | 状态 | 证据 |
|---|---|---|
| 0007_ledger：`model_catalog.usd_per_unit` 参考价（Seedance 2.5 $0.231/s、样片 $0.092/s、Wan 3 $0.083/s、MiniMax H3 $0.08/s、Seedream $0.03/张，本地 0）；新表 `stage_overrides` | PASS | `test_ledger_money.py::test_migration_seeds_usd_reference_prices_and_stage_table` |
| 美元估算：`usd_per_unit × 单位 × 分辨率系数`（`settings.usd_resolution_factors`，Seedance 1080p=0.569/0.231，Wan 1080p=0.165/0.083）；无美元价的模型回落到 积分 × `credit_usd`(0.01)；每个 job 记 `usd_estimate`、`stage` | PASS | `test_estimate_usd_*`、`test_job_carries_usd_and_stage` |
| 预算闸门（在 ledger 预扣事务里、锁住用户行后检查，无竞态）：项目 / 集 `budget_usd`、每日 `DAILY_BUDGET_USD`；已结算 + 进行中 + 本次 > 预算即拒绝，HTTP 402 中文提示；退款的不计 | PASS | `test_project_budget_*`、`test_episode_budget_and_daily_cap` |
| 内容哈希缓存：`sha256(model_key + 排序 JSON(task_type+canonical))`，同项目命中成功且产物仍在磁盘 → 0 费用 success job，`request_payload.cached_from`，复制资产行并触发后续钩子；`force=true` 跳过；产物丢失不命中；dispatch_job 对非 queued job 直接返回（不会重复付费） | PASS | `test_content_hash_cache_*`、`test_cache_skipped_when_output_file_is_gone`、`test_api_force_flag_and_cached_view` |
| 关键帧工位：`POST /shots/{id}/keyframe`（`model_for("keyframe_image")`，每个绑定资产取 `locked_ref_urls[:3]`，没锁定取 `ref_urls[:1]`），`POST /projects/{pid}/episodes/{n}/keyframes`（只补缺失/被驳回），`GET` 同路径（网格，失败的 pending 自动回 none），`approve`/`reject`；成功回调写 `shots.keyframe_url/keyframe_status=ready` | PASS | `test_keyframes.py` 4 项（假 Ark 传输层） |
| `GET /projects/{pid}/episodes/{n}/estimate?tier=&final=`（按 routing 分工，积分+美元，含关键帧张数与费用）、`GET /projects/{pid}/costs`（按环节/按集美元）、`PUT /projects/{pid}/budget`、`PUT .../episodes/{n}/budget` | PASS | `test_episode_estimate_follows_stage_routing`、`test_project_costs_by_stage_and_budget_endpoints` |
| `GET/PUT /api/v1/admin/stages`：持久化到 `stage_overrides`，启动时 `load_stage_overrides` → `routing.set_override`；model 为空即恢复默认 | PASS | `test_admin_stages_persist_and_reload` |
| 前端：账本抽屉加「花费」（美元按环节/按集、项目预算）、「模型分工」页签、模型美元单价列、任务美元/缓存标记；新 `features/keyframe-review`（关键帧网格 + 整集美元估算） | PASS（tsc + vitest） / NOT_TESTED（未真机截图，KeyframeReview 尚未挂到页面） | `money.test.ts` 3 项 |
| 真实 Seedream 关键帧出图 | BLOCKED | 无 ARK_API_KEY |

后端 127 passed（新增 15 项）；`ruff`、`mypy`、`pnpm tsc`、`pnpm test:run`（9）通过。美元价格为第三方参考价，未经官方核实。

### 已知限制 / 接口约定
- 缓存会让「同参数重抽」直接返回旧结果：重抽必须传 `force`（`submit(..., force=True)` 或 `meta={"force": True}`）。分镜渲染接口 `RenderRequest` 还没有 force 字段（不在本组范围）。
- 编排器 `render_next` 走 `router.create_job` + `queue_dispatch`，若命中缓存，job 已是 success，dispatch 会直接返回但不会触发 `_after_success`，批量链会停在该镜。
- 关键帧通过后不改 `first_frame_url`（避免清空参考）；由渲染组决定 `keyframe_status == "approved"` 时如何用 `keyframe_url`。
- 估算按 routing 的未来行为（按镜头类型/720p，final 才 1080p）；当前编排器 L2 仍固定 Seedance 1080p，渲染组改完后两者一致。

## Team voice — 配音 / 成片 / 安全（分支 team/voice）

| 项 | 状态 | 证据 |
|---|---|---|
| 安全：成年语境白名单（少年将军/少年英雄/少女时代/少女感等），中文数字年龄（十五岁/七岁），minor character/minor injury 不误伤 | PASS | `test_safety_context.py` |
| 安全：未成年指示词（含白名单短语中的少年/少女）+ 性内容，两种模式下一律拦截；白名单短语仅在写明成年年龄时放行 | PASS | 同上，参数化两种 content_mode |
| docker-compose：所有端口绑 127.0.0.1；新增 api/worker/web（profile `prod`，默认不启动；api 无 APP_TOKEN 拒绝启动） | PASS | `docker compose config` 校验；未构建/未启动镜像 |
| 鉴权：APP_TOKEN Bearer 中间件（/healthz、/api/v1/auth/login 例外）；/media 接受 Bearer、路径绑定签名短期 URL（`auth.sign_media_url`）或登录下发的 12h HttpOnly cookie | PASS | `test_auth.py` |
| 前端：AuthGate 登录页 + api.ts 统一加 Authorization；401 自动回登录页 | PASS | `pnpm tsc`、`pnpm test:run`（新增 `auth.test.ts`）；未做真机浏览器验证 → NOT_TESTED |
| 配音：火山 V1 / MiniMax t2a_v2 适配器（字段 UNVERIFIED，集中在 `voice/providers.py` CONFIG，可 env 覆盖） | PASS（传输层打桩）/ 真实调用 BLOCKED（无密钥） | `test_voice.py` |
| 配音：本地 espeak-ng（缺失时按字数生成静音） | VERIFIED | VPS 上真跑 espeak-ng |
| 配音：选择顺序 角色 voice.provider → model_for("tts").provider；无密钥自动退本地并在结果里标 fallback_from；按 (text, voice) sha256 缓存 | PASS | `test_voice.py` |
| 成片：对白轨 tts/native/mix，台词对齐镜头起点(+0.1s)；竖屏 ASS 字幕（按宽度定字号、CJK 手动换行、防样式注入）；BGM 循环 + sidechain ducking | PASS | `test_post_dialogue.py`（真 FFmpeg，分频段测电平） |
| 成片：post.assemble 每镜按说话人音色 TTS → 对白轨；动画样片自带字幕不重复烧；config `VIDEO_NATIVE_AUDIO=false`（默认 tts） | PASS | `test_assemble_dialogue.py` |

后端 174 passed；ruff、mypy 通过；前端 tsc + vitest 8 passed。

### 已知限制
- 渲染请求仍按 Seedance 默认 generate_audio=True（shot_orchestrator 属 render 组）；需要 render 组改为 `generate_audio=settings.video_native_audio`。
- TTS 不走积分账本（无 job）；靠缓存控制消耗。需要计费时由 ledger 组接。
- 每镜仅一个说话人（沿用 shots.speaker_asset_id）；镜内多人对白需要拆镜。

## Team remix — AI 连续剧二创（分支 team/remix）

| 项 | 状态 | 证据 |
|---|---|---|
| dHash 感知哈希去重相邻相似关键帧（+均色守卫，纯色帧不误合并；非相邻回切保留） | PASS | `test_remix_dedupe.py`（FFmpeg 真实出图） |
| 视觉识别预估：送图数 / 批数 / token / USD；未知模型 usd=None 不瞎估；价格未核实 | PASS | 同上 |
| 按视频 sha256 缓存拆解结果（改名重传零重算）；关键帧描述按 (sha256, 模型) 缓存 | PASS | 同上 + `test_remix_api.py`（第 2 集同文件：拆解命中缓存、视觉只调 1 次） |
| 关键帧长边限制 768px（≈440 token/张，原 1080x1920 ≈1500） | PASS | `test_remix_rhythm.py::test_keyframes_are_shrunk_before_any_vision_call` |
| 每集节奏表（镜头数、平均镜长、每分钟切数、开场 3 秒钩子、快切高潮、结尾悬念）→ `series_bible_json.rhythm` | PASS | `test_remix_rhythm.py` |
| 二创模式：换人物/换风格/改剧情/续写/番外/精简剪辑，可组合，结构化输出 SeriesPlan；模型走 `model_for("remix_rewrite")`，视觉走 `model_for("remix_vision")` | PASS（SSE 桩） | `test_remix_modes.py` |
| 一键 fork 自家项目：继承圣经、分集（含剧本）、定妆（ref/locked/voice）、空镜（establishing 镜头+关键帧），不重新生成；`meta.inherited_from` / `series_bible_json.inherited_from` | PASS | `test_remix_fork.py` |
| fork + 二创：未被模式改动的锁定定妆保留（模型重述不覆盖），被换人物/换风格的清空参考并标 `needs_lookdev` | PASS | 同上 |
| 合规：外部原片人物 `meta.redesign_required=true`、清空参考/人脸嵌入；新参考就绪后才能解除；改写指令和模型产出的人物/剧情都过 `safety.check_prompt` | PASS | `test_remix_fork.py`、`test_remix_modes.py` |
| API `/api/v1/remix/*`：多集登记、拆解进度、预估、识别、生成新项目、fork、解除重设计标记 | PASS | `test_remix_api.py`（真实 FFmpeg/PySceneDetect/whisper tiny + Postgres） |
| 前端「二创工作室」抽屉（页头入口） | PASS（tsc + vitest 6 项）/ NOT_TESTED（未截图） | `features/remix-studio/remix.test.ts` |
| 真实 Claude 视觉/改写调用 | BLOCKED | 无 ANTHROPIC_API_KEY |

后端 137 passed（新增 25 项）；`ruff`、`mypy` 通过；`pnpm tsc`、`pnpm test:run`（12）通过。

### 接口与约定
- 拆解状态不建新表：`<STORAGE_DIR>/remix/<project_id>/manifest.json`（flock 加锁）；缓存 `<STORAGE_DIR>/remix_cache/<sha256>/`。
- Celery：任务 `remix.episode` 注册在共享 `celery_app` 上，但定义在 `src/domains/remix/tasks.py`；worker 需 `celery -A src.queue worker -Q local_post -I src.domains.remix.tasks`。inline 模式用 FastAPI BackgroundTasks。
- 给 lookdev 组：`compliance.bindable_refs(asset)` 在 `redesign_required` 时返回空——绑定镜头参考时请调用或遵守该字段；`POST /remix/projects/{pid}/assets/{aid}/redesigned` 解除。
- 给 render 组：fork 出的空镜 `shots.source_remix_meta.clip_path` 是继承来的成片路径（原 job 属于父项目，`best_clips` 查不到），合成时可直接用。
- 旧 `/projects/{id}/remix` 单视频路径与 `queue.remix` 未改动（`LIGHT_MODEL` 写死仍在 queue.py，不在本组所有权内）。

### 已知限制
- 节奏表钩子是规则判断（开场 3 秒内有台词或切镜、连续 ≥2 个短于平均一半的镜头、末镜），非语义识别。
- fork 只复制 establishing 镜头，剧情镜头需重新分镜；fork+二创只传父项目大纲与圣经，不传全文。

## 集成（hark/base，2026-10-23）

六个分支按 script → lookdev → render → ledger → voice → remix 顺序 `--no-ff` 合并；合并前在各自 worktree 重跑全量（script 128 / lookdev 122 / render 119 / ledger 127 / voice 174 / remix 137，全绿，与各组报告一致）。每合一个跑一次全量并提交（128 → 138 → 145 → 160 → 222 → 247），接缝修完后 257。

| 接缝 | 处理 | 状态 | 证据 |
|---|---|---|---|
| 迁移链 | `0007_ledger` 改名 `0008_ledger`（down_revision=`0007_script`）；新增 `0009_integration`（`tts` 占位 model_catalog 行）。线性链 0006 → 0007_script → 0008_ledger → 0009_integration | VERIFIED | 空库 `duanju_integ_mig`：`alembic upgrade head` → `0009_integration (head)`，llm_* 7 行、usd 价 8 行、stage_overrides 表在；`downgrade 0006` 再 `upgrade head` 往返通过 |
| import_storyboard | 两组字段都保留：shot_kind / continuous_from_prev（首镜强制 false）/ speaker_asset_id + `refs.asset_names` | PASS | `test_import_storyboard_writes_kind_continuity_and_speaker`、`test_refs_bound_late_from_locked_assets` |
| 参考图唯一规则 | 删除 shot_orchestrator 临时 `refs_for_episode`，改用 `lookdev/refs.py`；`refs_for_episode` 内加 remix 合规闸门（`meta.redesign_required` → 不绑定），`compliance.bindable_refs` 同规则（未锁定只给 1 张候选）；关键帧 `keyframe_request` 改走 `orch.bind_refs_late(cap=模型 max_refs)`（迟绑定 + 分集造型 + 合规），删掉 keyframes 自己的 `asset_refs` | PASS | `test_redesign_required_binds_nothing_for_video_or_keyframe`、`test_keyframe_refs_bound_late_with_episode_look`、原 `test_keyframes.py` |
| 原生音频 | L2 请求 `generate_audio=settings.video_native_audio`（默认 false；L1 本就 false） | PASS | `test_master_generate_audio_follows_video_native_audio` |
| force + 内容哈希缓存 | `RenderRequest.force` → `_cloud_render` → `submit(force=)` → `create_job`；强制时 payload 记 `forced`，批量链 `render_next/_queue_shot/on_render_failed` 与 failover 继续透传；`_queue_shot` 命中缓存（job 已 success）时调 `router.run_cached_followups`（= 读资产 URL + `_after_success`），镜头状态更新并继续下一镜；`submit` 的缓存分支也改用同一函数。lookdev 的 `force` 也透传到 gateway（否则重抽被 ledger 缓存吞掉——合并 ledger 时 `test_generate_dedup_hits_cache_and_estimate` 因此红过） | PASS | `test_batch_cache_hit_still_advances_to_next_shot`、`test_batch_force_skips_cache_and_carries_to_later_shots`、`test_render_api_force_flag` |
| 非渲染 jobs | `RENDER_TASK`（video.* / post.* / face.swap）过滤 `best_clips`、`shot_takes`；`/shots/{id}/select` 拒绝非渲染 job；重新分镜的"已有渲染"守卫只看渲染 job（llm/tts/关键帧不再挡） | PASS | `test_non_render_jobs_stay_out_of_takes_and_count_in_costs` |
| 花费汇总 | `job_usd` 对 `llm.*` / `tts` 用 payload.usd（此前按 0 积分算成 $0）；`/projects/{id}/costs` 每个环节带 `phase`，新增 `by_phase`（编剧 script / 二创 remix / lookdev / keyframe / video / voice / post）；remix 的改写与原片识别也记 `llm.remix_*`（此前未记账，且 stage 误用默认 storyboard 路由）；remix 预估价表改读 `settings.llm_prices`（不再两份价表） | PASS | 同上 + `test_remix_rewrite_is_ledgered_on_its_stage` |
| TTS 记账 | `speak` 未命中缓存且有项目时写一条 0 积分 jobs（`task_type='tts'`，model_key `tts`，payload：provider/chars/usd/price_verified=false）；单价 `settings.tts_usd_per_char`（volc 0.00004 / minimax 0.0001 / local 0 美元每字，占位未核实，env `TTS_USD_PER_CHAR`）；assemble 对白轨、`/voice/preview`（带 asset_id 时）、`/voice/shots/{id}` 都记 | PASS | `test_tts_cache_miss_writes_one_zero_credit_job` |
| 前端 | `tierPlan` 按路由：L1=草稿路由 480p，L2 按 shot_kind 720p（对白 seedance、动作/空镜/插入 wan3），final 才 1080p，`GET /admin/stages` 覆盖生效；KeyframeReview 挂到分镜页（长卷下方）；各组临时类型（ShotX/Take、JobX/ModelX/StageRow、money 类型、Lookdev*/Remix*、PlanProgress/LlmUsage）统一进 `contracts.ts`，新增 zod `RenderRequest` 并入 contracts 测试；按惯例重生成 `openapi.json`（`app.openapi()` 紧凑 JSON）；`remixApi.remove` 改走 `api()`（原裸 fetch 不带 Bearer，开鉴权后会 401）；账本"花费"页加按大类一行 | PASS（tsc + vitest 24 + `pnpm build`）/ 浏览器 NOT_TESTED | `estimate.test.ts`（改写 L2 断言，见下）、`contracts.test.ts` |
| compose | worker 命令加 `-I src.domains.remix.tasks` | PASS | `docker compose config -q` |
| main.py | 鉴权中间件、auth/gateway/script/lookdev/storyboard/keyframes/voice/remix 路由、lifespan `load_stage_overrides` 全在 | PASS | `test_main_wires_every_router_and_auth` |

最终：后端 257 passed，ruff、mypy（52 个源文件）通过；前端 `pnpm tsc`、`pnpm test:run`（24）、`pnpm build` 通过。真实供应商调用仍全部 BLOCKED（无密钥）。

### 改动的旧测试
- `test_orchestrator.py::test_tier_requests`：合并冲突，取 render 组版本，时长按 script 组改后的 BOARD（10 秒）。
- `test_orchestrator.py` 两个 `fake_queue` 桩加 `force=False` 形参（`_queue_shot` 新增 force 参数，断言不变）。
- `web/src/lib/estimate.test.ts`：L2 默认从 seedance 1080p 改为按路由 720p（审查要求 + render/ledger 组都已注明旧估价偏高）。

### 遗留
- 预算闸门现在把 LLM/TTS 花费也算进项目预算（tts 挂在镜头上，也算集预算）；若只想管视频钱需要再按 phase 过滤。
- fork 继承的空镜 `source_remix_meta.clip_path` 仍不进 `best_clips`（remix 组已注明），合成时用不上继承的成片。
- 动画样片（L0）仍用导入时快照的 `refs.images[0]`，不走迟绑定（免费本地出图，不影响付费参考）。
- compose worker 只监听 seedance/wan 视频队列；若在模型分工里把某环节改成 minimax_h3，需要给 worker 加 `video.minimax_h3`。
- TTS / LLM / Seedance 等单价都是占位或第三方参考价，未核实。

## 国产模型分工（2026-10-23，接在 hark/base 之后）

Victor 嫌 Claude 贵、担心中文效果，拉了"七个专家窗口"按岗位选型，口头核实过官方价目页（细节报告待补）。这一轮把
调用链路打通，但**没有改 `routing.py` 里硬编码的默认分工**——原因见下面"为什么没切默认值"。

### 专家选型结论（Victor 提供，未独立核实）

| 岗位 | 推荐模型 | 参考单价 |
|---|---|---|
| 整剧规划（读整本小说） | GLM-5.3（质量上限换 Kimi K3） | ~3.4 元/本 |
| 分集剧本 | Kimi K3 / GLM-5.3 | ~0.3 元/集 |
| 分镜表 / 定妆提示词 | 豆包 doubao-seed-2.1-lite（支持结构化 JSON） | ~0.02 元/集 |
| 二创看片 | 豆包 doubao-seed-2.1-lite（直接读视频） | 同上 |
| 摘要/拆书 | qwen3.7-flash（空闲时段 DeepSeek Flash） | — |
| 定妆图 | Seedream 5.0 pro | 0.30 元/张 |
| 关键帧 | Seedream 5.0 flash | 0.12 元/张 |
| 主力视频 | Seedance 2.0 mini 720p（草稿 480p；重点镜头 2.0/2.5） | ~2.5 元/5秒（草稿 ~1.2 元/5秒） |
| 空镜/过场 | 万相 wan2.2-i2v-flash 480p | 0.5 元/条 |
| 打戏 | 可灵 4.0（**系统里还没有密钥/接口验证**） | — |
| 配音 | 阿里 qwen-audio-3.1-tts-plus（虚构角色复刻免费） | ~2.8 元/万字 |
| 配乐音效 | 火山 seed-audio-1.0 | 1 元/分钟 |

两条硬约束：Seedance 拒收真人照片当参考图，定妆必须用 Seedream 纯文生图出脸；国内外平台都不接受露骨内容，真正卡人的是播出平台审片。

### 已打通（VERIFIED / PASS）

| 项 | 状态 | 证据 |
|---|---|---|
| `script_engine.OpenAICompatLLM.for_vendor(provider)`：doubao（复用 `ARK_API_KEY`）/zhipu/moonshot/qwen/deepseek/xai(Grok) 六个 vendor，各自 base URL 可用 `<PROVIDER>_BASE_URL` 覆盖，`CachedDirectorLLM.structured()` 按 `routing.model_for(stage).provider` 自动分发 | PASS | `test_llm_routing.py::test_named_vendor_stage_goes_to_its_own_base_url`、`test_named_vendor_without_key_is_blocked` |
| qwen 配音适配器（DashScope，字段 UNVERIFIED） | PASS（桩）/ 真实调用 NOT_TESTED（无 QWEN_API_KEY） | `voice/providers.py` |
| 可灵(keling) 视频适配器占位：`model_catalog` 行 `is_active=false`，没有 `KELING_API_KEY` 时 `ProviderNotConfigured`，和 wan/minimax 的占位模式一致 | PASS | `adapters/keling.py` |
| migration `0010_vendor_lineup`：新增 `seedance_2_0_mini`(_draft)、`seedream_5_flash`、`wan2_2_i2v_flash`、`keling_4_0`（inactive）；`seedream_5_pro` 单价从占位 0.03 改成 Victor 核实的 0.30 元/张 ≈ 0.0417 美元 | VERIFIED | `alembic upgrade head` 在空库跑通；`test_schema_and_seed.py`、`test_ledger_money.py` |
| 定妆纯文生图标记 + 30 天有效期：`on_lookdev_rendered(..., pure_t2i=)` 把没有图片参考的渲染打上 `meta.ref_meta[url] = {pure_t2i, expires_at}`；`lookdev/refs.py` 的 `refs_for_episode` 过滤掉过期的 | PASS | `test_lookdev.py::test_pure_t2i_ref_expires_and_is_dropped_from_refs_for_episode` |
| 真实供应商校准（只各打一次，按要求不多出）：Seedream 5.0 pro 文生图成功（2496×1664，usage.output_tokens=16224）；Seedance 2.0 mini 5 秒视频被方舟内容策略判定 `OutputVideoSensitiveContentDetected.PolicyViolation` 拒绝（和价格无关），没有重试 | VERIFIED（图）/ BLOCKED（视频，内容策略，非供应商问题） | 本轮会话记录；没有留存密钥/日志 |
| 顺带修正：之前按专家口述写的几个 Ark 模型 id 是猜的，`GET /models` 核对账号真实可用 id 后全部改成真实值（如 `doubao-seedance-2-0-mini-260615`、`doubao-seedream-5-0-flash-260915`、`doubao-seed-2-1-lite-260915`） | VERIFIED | 同上 |

后端 258 passed（新增 1 项 `test_lookdev`，`test_llm_routing` 新增 2 项抵消了同名旧文件的基线差）；`ruff`、`mypy`（53 个源文件）通过。

### 为什么没把 `routing.py` 的默认分工直接改成专家选型

试过了：把 `STAGES` 里 plan/script/storyboard/reformat/remix_vision/remix_rewrite 的默认模型/provider 从 Claude
改成 GLM-5.3/Kimi K3/豆包后，整个仓库里几十个测试（`test_llm_ledger.py`、`test_script_engine.py`、
`test_two_stage_plan.py`、`test_remix_modes.py`、`test_series.py` 等）全部是拿 Claude SSE mock 死死钉住这几个
stage 默认值的，一次性改掉默认值会连带改掉一大批测试的桩，风险和这次改动的诉求（省钱）不成比例，容易改出真的 bug
却被测试的"修复"掩盖。改为推荐：真要切换，在部署环境设 `MODEL_<STAGE>`/`PROVIDER_<STAGE>`（例如
`MODEL_PLAN=glm-5.3 PROVIDER_PLAN=zhipu MODEL_SCRIPT=kimi-k3 PROVIDER_SCRIPT=moonshot
MODEL_STORYBOARD=doubao-seed-2-1-lite-260915 PROVIDER_STORYBOARD=doubao`），或调用已有的
`PUT /api/v1/admin/stages` 按 stage 持久化覆盖，效果等价，但不碰测试基线。

### 已知限制 / 待办

- 价目表里的 GLM-5.3/Kimi K3/doubao-seed-2.1-lite/qwen3.7-flash/wan2.2-i2v-flash/qwen-audio-3.1-tts-plus/
  seed-audio-1.0 都是 Victor 口头核实的参考价，没有独立核对官方价目页 API；`settings.llm_prices` 没加这几个
  模型的每百万 token 单价（只有 Victor 给的集/本汇总费用，换算不出准确的 token 单价，没有编造）。
- qwen 配音、可灵视频、Grok(xai) 文本都是接口占位，没有对应密钥，真实调用全部 NOT_TESTED/BLOCKED。
- `seed-audio-1.0`（配乐音效）和可灵的打戏视频还没有 model_catalog/stage 挂点。
- 后台管理页（供应商/模型/价格/用量，带鉴权）是 Victor 这轮追加的需求，按约定顺序排在下一步，还没开始。
- 提示词盘点：剧本理解/规划/分集/分镜/导入整理/二创改写/二创关键帧识别/定妆出图 都已固定成模板，但没有版本号，
  且分散在 `script/series.py`、`script/api.py`、`remix/modes.py` 三个文件（`DIRECTOR_RULES` 单独在 api.py）。
  统一目录+版本号计划并入后台管理页一起做。
- Grok(xai) 现在是 `plan`/`script`/`remix_rewrite` 三个 stage 的 `tier="overseas"` 选项（见下一节），但
  `XAI_API_KEY` 没配，真实调用 NOT_TESTED；没有专门前端入口，目前只能通过 `PlanRequest.tier`/`StageCallOptions.tier`
  传 `"overseas"` 或 `PUT .../quality-tiers` 设成项目默认值来用。

### 质量档位（草稿/标准/高质量，2026-10-23，接在上面之后）

每模块可选质量档位，复用现有 `routing.py`/成本预估基础设施，不建平行系统：

- `routing.QUALITY_TIERS`：按 stage 配置 draft/high 档（standard 就是 `STAGES` 的默认值，不重复存），只覆盖
  `model`/`provider`，`price_note` 只给前端展示用，不接入真实计费。覆盖了 summarize/plan/script/storyboard/
  reformat/remix_vision/remix_rewrite/lookdev_image/keyframe_image/tts 十个 stage；视频 stage 不在这里，因为
  它们已经有自己的 L0-L3 档位（`video_draft`/`video_dialogue`/.../`video_final`）。
- `routing.model_for(stage, tier)`：优先级 env(`MODEL_<STAGE>`/`PROVIDER_<STAGE>`) > 后台 `_runtime` 覆盖 >
  tier 选择 > `STAGES` 默认值——tier 是最低优先级,不会绕过运维已经设置的覆盖。`routing.tiers_for(stage)` 返回
  包含 standard 的全部档位，给前端下拉框用。
- 调用链路：`CachedDirectorLLM.structured(..., tier=)` -> `compile_episode_storyboard(..., tier=)` ->
  `series.two_stage_plan(..., tier=)`（逐段摘要本身不接受 tier，始终用 summarize 的默认档——它是便宜的后台
  步骤，不面向用户选择）。`lookdev/api.py` 的 `_plan()`/`generate_ref()`/`estimate()` 并行接入，`estimate()`
  现在用 `model_catalog.usd_per_unit` 返回真实价格和 `model_key`，不再只是 env 占位 flat 价。
- 项目级默认档位存在 `Project.series_bible_json["_quality_tiers"]`（和既有的 `_cache`/`_progress` 一样的
  bookkeeping 字段），对 LLM 不可见（`bible_text()` 本来就剔除所有 `_` 前缀键），对前端 API 视图也不可见
  （`project_view()` 同样剔除）。
- 新端点：`GET /api/v1/stage-tiers`（全部 stage 的可选档位+价格展示）、
  `PUT /api/v1/projects/{id}/quality-tiers`（设置项目默认档位，未知 stage 名返回 422）。
  `PlanRequest`/`StageCallOptions`（script 的 write/storyboard 调用体）都加了可选 `tier` 字段，
  显式传入优先于项目默认值。

VERIFIED：`test_llm_routing.py::test_model_for_tier_precedence`/`test_tiers_for_includes_standard_and_configured_tiers`
（tier 覆盖、后台覆盖优先于 tier、env 优先于后台覆盖的三层顺序）；
`test_series.py::test_stage_tiers_and_project_quality_tiers`（端点契约 + `_quality_tiers` 对 LLM 和前端视图
都不可见 + 非法 stage 名 422）；`test_lookdev.py::test_generate_dedup_hits_cache_and_estimate`（`estimate()`
返回真实 `model_key`/价格，`tier="draft"` 切到 seedream_5_flash/0.0167 美元）。ruff / mypy（53 个源文件）通过。

另加了一档 `tier="overseas"`（`plan`/`script`/`remix_rewrite` 三个文本 stage），指向 Grok(`grok-4`/`xai`，
复用已有的 `script_engine.VENDOR_BASE_URL["xai"]`/`VENDOR_API_KEY_ENV["xai"]="XAI_API_KEY"` 管线）；对应
Victor 的"偏海外剧情用 Grok"。`PlanRequest.tier`/`StageCallOptions.tier` 的 Literal 加了 `"overseas"`。
价格未核实（没有 Grok 官方价目核对，没有配 `XAI_API_KEY`），标记 NOT_TESTED。
VERIFIED（路由逻辑，非真实调用）：`test_llm_routing.py::test_overseas_tier_routes_script_to_grok`。
