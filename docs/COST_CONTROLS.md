# 岗位模型与单镜头重生成

## 这次解决的问题

- `claude/hark-model-routing` 已有模型分工，但剧本页没有设置入口。现在可以分别保存素材阅读、全剧规划、分集编剧、分镜导演和剧本整理的档位；不会因为更改设置就自动调用模型。
- 规划成功原本只保留 `_cache`、`_progress`，导致 `_quality_tiers` 丢失。现在在持有项目行锁时保留已有内部配置，后续编剧与分镜继续使用保存的档位。
- Claude 密钥检查现在依据当前岗位的实际供应商；选用其他供应商不再无条件要求 `ANTHROPIC_API_KEY`。
- OpenAI-compatible 适配器保留供应商的 API 前缀：例如 `/api/paas/v4/chat/completions`、`/api/v3/chat/completions`、`/v1/chat/completions`。只有不含路径的根地址自动补 `/v1`。之前为所有地址附加 `/v1` 会造成错误路径。
- 分镜默认渲染仍可复用相同参数的旧结果，并明确显示“没有调用生成模型”。新增草稿／母带的“重新生成”，只对当前镜头发送 `force: true`，保留旧版本供选片。
- 素材摘要和剧本导入整理也透传各自档位；部署环境／后台覆盖仍优先于项目档位。界面显示最终生效模型，避免档位名称掩盖覆盖配置。

## 为什么增加 Agent 不一定省钱

岗位可以是固定的流程节点、提示词与输入输出契约，不需要给每个岗位安装一个常驻、相互讨论的 Agent。重复发送整部小说、多个 Agent 共享全部聊天记录、无上限循环评审，反而会增加调用量。

适合当前工作台的方式：原文按片段缓存摘要；规划读取摘要；编剧读取本集原文、前情摘要与项目设定；分镜读取当前剧本。简单岗位使用适合的较低成本模型，重要内容按需精修。已有工作流已经具备这些分工的一部分，本次打通选择和持久化。

## “重新生成更便宜”必须区分三件事

| 情况 | 实际发生的工作 | 成本含义 |
| --- | --- | --- |
| 提示词缓存命中 | 重用同模型的静态输入前缀计算，仍输出新文本 | 只降低命中的输入部分；新输出仍收费。写缓存也可能有额外费用。 |
| 结果缓存命中 | 直接返回已生成的同参数素材 | 无新的视频生成费用，但画面没有变化，不是改善妆效。 |
| 只重做一个镜头 | 重用剧本、定妆、其他镜头，仅生成所选片段 | 相对整集重跑可减少工作量；新视频按供应商规则正常计费。 |

官方说明：
- [Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)：缓存前缀、最短长度、TTL 和不同模型价格条件；不能保证每次请求都会命中。
- [Seedance 2.5 样片模式](https://docs.volcengine.com/docs/ark/seedance-2-5?lang=en)：样片适合低成本试效果；基于样片生成最终视频仍属于正常计费推理。本次没有新增供应商样片转正式片或视频局部编辑接口。

妆效调整建议：先修角色定妆／关键帧并审核，再重做受影响镜头。修改参考素材不能保证模型完全遵循；局部视频编辑是否更便宜必须核对所选供应商能力与实际账单。

## 使用

1. 在“剧本 → 岗位与生成成本”选择档位，保存后再生成。可以先全部设为经济档，再只把需要的岗位提高档位。
2. 经济档需要服务器上相应供应商的密钥和可用模型。界面中的实际模型优先反映部署环境和后台覆盖。模型名称、可用性、供应商结构化输出兼容性及真实单价仍需在部署账户校准。
3. 分镜阶梯按钮优先复用已有素材；想要新画面使用该镜头下的“重新生成草稿／母带”。新版本重新计费。
4. 文本费用、图片费用、视频费用和本地处理费用应分别看；现有账本仍包含占位单价和未定价调用，本次不声称具体节省比例。

## 验证方式与边界

后端使用隔离 PostgreSQL 与 Redis；供应商 HTTP 在测试中替换，业务路由、数据库、账本和 API 按真实实现执行。`tests/test_cost_controls.py` 覆盖供应商 URL、档位持久化与非法值、部署覆盖、缺少 Claude 密钥的经济档完整工作流。既有测试覆盖 `force` 跳过缓存、批量缓存衔接和账本。

```sh
# apps/server；TEST_DATABASE_URL 必须是可重建 schema 的专用测试数据库
uv run ruff check .
uv run mypy src
uv run pytest -q

# apps/web
pnpm install --frozen-lockfile
pnpm exec tsc --noEmit
pnpm lint
pnpm test:run
pnpm build
pnpm exec playwright install chromium
pnpm start --hostname 127.0.0.1 --port 3148
# 另一终端
pnpm test:cost-controls
```

浏览器测试在 1440×900 / 390×844 验证根节点和交互控件非零尺寸、无横向溢出、成功路径零 console.error / pageerror、经济档保存及防双击、单镜头强制重出、失败重试、空配置、1000 集与长模型名、键盘焦点和减少动态效果。故障注入中的预期 HTTP 503 浏览器资源日志单独记录，不混入成功路径零错误断言。API 响应为固定测试数据，不是真实付费出片。

截图与 JSON 输出到 `/tmp/duanju-cost-controls`，可用 `BROWSER_ARTIFACTS` 改路径；目标地址可用 `BROWSER_BASE_URL` 修改。

真实供应商调用、妆效改善程度、实际账单节省比例和外部 Principal Reviewer 评分不在本地模拟测试的证明范围内。

## 本次执行结果（2026-10-24）

| 命令／检查 | 实际结果 |
| --- | --- |
| `uv run ruff check .` | exit 0，All checks passed |
| `uv run mypy src` | exit 0，53 source files |
| `uv run pytest -q` | exit 0，272 passed in 199.69s |
| `tsc --noEmit` | exit 0 |
| `eslint` | exit 0，0 errors；保留 4 个原有 warning |
| `vitest run` | exit 0，7 files / 24 tests passed |
| `next build` | exit 0，`/` 成功预渲染，First Load JS 130 kB |
| `node tests/cost-controls.browser.mjs` | exit 0；两种视口均成功，详见 [runtime.json](cost-controls-runtime.json) |

自评仅针对本次修改，不代表整个仓库或外部 Principal Reviewer 的评分。

| 维度 | 自评 /20 | 证据 |
| --- | ---: | --- |
| 视觉与排版 | 19 | `apps/web/src/features/script-room/QualityControls.tsx:63`：继承天青／黛色，响应式网格、流式字号、长模型名处理；桌面／手机截图已检查 |
| 完整状态与交互 | 19 | `apps/web/src/features/script-room/QualityControls.tsx:30`：占位、空配置、保存中、失败重试、已保存；`tests/cost-controls.browser.mjs:144`：1000 集 |
| 动效与生命周期 | 20 | `apps/web/src/app/globals.css:33`：180ms easing、reduce fallback；`QualityControls.tsx:42`：取消失效请求；本次无 3D 分配 |
| 架构与无障碍 | 19 | `apps/server/src/domains/script/api.py:357`：行锁与配置保留；`QualityControls.tsx:69`：原生控件、同步锁、类型契约；浏览器焦点检查通过 |
| 静态与运行验证 | 19 | `apps/server/tests/test_cost_controls.py:93`：实际域 API 调用链；`apps/web/tests/cost-controls.browser.mjs:148`：两视口检测输出；以上命令全部 exit 0 |

合计 **96/100（自评）**。成功路径两视口均为 `consoleErrors=0`、`pageErrors=0`、`horizontalOverflow=false`；每个视口另外注入两次预期 HTTP 503 来验证错误恢复，资源错误日志单独计数。
