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

## Phase 2 — 网关、编剧引擎、二创拆解、供应商适配器
未开始。

## Phase 3 — 队列、前端导演工作台
未开始。
