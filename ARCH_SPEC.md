# AI 短剧与连续剧全自动导演工作台系统架构规格书

> 面向极致算力节约与 Claude Code 一步到位构建的工程蓝图。
>
> 本文件为用户提供的规格书全文（仅补充 Markdown 标题与代码块格式，正文未改写）。
> 落地时的核实结论与偏差记录见文末「落地核实附注」以及 `PROGRESS.md`。

## 架构设计哲学与系统总体拓扑

面向个人导演级用户的 AI 短剧与连续剧制作工作台，其核心工程矛盾在于好莱坞级长篇叙事的连续性要求与多模态大模型高昂的 Token 及 GPU 算力消耗之间的剧烈冲突。传统 AI 视频生成工具普遍采用“单次提示词抽卡”的碎片化交互，不仅导致跨镜头人物面部、服装、道具与场景空间关系严重漂移，更在反复试错与冗余上下文传递中造成高达 80% 以上的算力浪费。本架构规格书基于工业级生成式媒体工作台（91-Generate 类架构体系）的六层解耦范式，融合本地零算力音视频逆向拆解、Anthropic 提示词前缀缓存（Prompt Caching）、原生结构化 JSON Schema 约束以及字节跳动 Seedance 2.5 与 Wan 3 等多参考长镜头视频引擎，构建一套支持常规商业短剧与无审查（Uncensored）内容自由切换的全自动导演工作台。

在系统工程层面，工作台摒弃了前端直连模型 SDK 的脆弱模式，全面采用六层解耦架构。第一层前端导演工作台负责剧本交互、人物与道具及场景三合一资产定妆、可视化分镜板（Storyboard）以及多轨粗剪预览；第二层业务控制层 API 统一接管项目状态机、防超支积分预扣与实扣账本（Credit Ledger）、任务派发与异步 Webhook 回调接收；第三层多级异构任务队列将耗时秒级的文本与定妆图任务、耗时数分钟的云端视频生成任务，以及纯本地运行的音视频二创拆解与超分后处理任务彻底物理隔离；第四层统一多模型网关通过标准化适配器（Adapter）屏蔽底层供应商参数差异，执行内容模式智能路由、多密钥轮询限流与故障自动降级；第五层混合存储层结合关系型数据库与兼容 S3 的对象存储，实现剧本元数据与海量多模态参考资产的高效寻址；第六层观测与成本治理后台实时监控单集 Token 消耗率、模型接口延迟与预算流水，确保个人创作者以最低边际成本产出影视级成片。

为使该工作台兼具极致的性能表现与对 Claude Code 自动化构建的最大亲和力，技术栈选型遵循单一后端运行时融合与强类型契约驱动原则。传统架构常将 Node.js 业务层与 Python 算法层分离，这在个人工作台中会引入不必要的跨进程远程过程调用（RPC）开销与部署复杂度；本架构统一采用 Python 3.13（通过 uv 管理依赖）与 FastAPI 构建业务 API、模型网关与本地音视频处理 Worker，直接在同一虚拟环境中原生调用 PySceneDetect、faster-whisper、FFmpeg 与 OpenTimelineIO。前端采用 Next.js（App Router）、TypeScript、TailwindCSS 与 Zustand，通过 Zod 与后端 Pydantic 模型保持 100% 严格对齐的 JSON Schema 契约。

| 系统层级 | 核心模块组成 | 生产级技术选型 | 核心架构职责与边界约束 |
|---|---|---|---|
| ① 前端导演台 | 剧本工作区、三合一定妆台、分镜板、二创检视器、粗剪时间线 | Next.js 15 + TypeScript + TailwindCSS + Shadcn/UI + Zustand | 仅通过 /api/v1/* 与后端通信；只展示抽象模型代号，永不暴露底层供应商密钥或直连第三方 SDK。 |
| ② 业务控制层 | 项目/剧集状态机、预算账本、任务下发器、Webhook 接收器 | Python 3.13 + FastAPI + Pydantic v2 + SQLAlchemy 2.0 (Async) | 负责生成前积分/预算预扣（Pre-deduction）、异步回调签名校验、实扣多退少补结算及短时效签名 URL 签发。 |
| ③ 异构任务队列 | 高优短任务队列、按模型拆分的视频主队列、本地后处理队列、死信队列 | Redis 7 + Celery / BullMQ + Flower 监控面板 | 严格隔离耗时 3–10 分钟的 I2V 任务与秒级定妆任务；按供应商拆分子队列，防止单一模型阻塞全局流水线。 |
| ④ 统一模型网关 | 路由分发器、Provider 适配器、提示词缓存中间件、熔断降级器 | 自研 ModelGateway + Anthropic SDK + 各视频平台 REST Adapter | 统一封装 /api/generate；强制注入 1 小时 TTL 的 Prompt Caching 标头；当主模型 5xx 或超时自动切备用模型。 |
| ⑤ 混合存储层 | 关系型元数据与账本库、分布式限流缓存、多媒体对象存储 | PostgreSQL 16 + Redis 7 + MinIO / Cloudflare R2 (S3 兼容) | 存储剧集资产树、定妆参考图数组 refs[]、任务状态与预算流水；所有原片与成片采用私有桶短时签名防盗链。 |
| ⑥ 本地算力引擎 | 视频二创拆解器、语音转写与说话人分离、超分去水印、时间线拼片 | FFmpeg + PySceneDetect + faster-whisper / WhisperX + Real-ESRGAN | 100% 运行于本地 CPU/GPU，实现零云端 Token 消耗的视频逆向拆镜、4K 超分辨率放大与多镜头无损合成。 |

## 全链路导演业务流：从小说与视频二创到连续剧成片

### 路径一：长篇小说与创意灵感的分集剧本拆解引擎

当操作员导入整本小说文本或输入核心创意梗概时，系统并不直接要求大模型一次性输出分镜脚本，而是采用从世界观圣经提取（Series Bible）、分集大纲（Episode Arc）、场次拆解（Scene Breakdown）到结构化分镜表（Shot List）的四阶递进式编译流。在第一阶段，大语言模型扫描输入文本，自动提取全剧常驻角色表（涵盖外貌特征、年龄段、核心服装与性格标签）、关键剧情道具表（如贯穿全剧的信物、武器与载具）以及核心常驻场景表（包含空间布局、光影基调与时代风格），并将其持久化至 PostgreSQL 的资产定妆表与全局“剧集圣经”上下文块中。随后，编剧引擎根据短剧黄金叙事节奏（每集 60 至 120 秒，包含前 3 秒强冲突钩子、中段情绪递进与结尾悬念卡点），将小说章节拆解为标准化单集剧本。

针对不同创作题材的合规与尺度需求，每个项目在创建时均带有显式的 content_mode 属性，支持在常规模式（standard_sfw）与无审查模式（uncensored_nsfw）之间按需配置。当处于常规短剧模式时，编剧与拆镜引擎默认路由至 Claude Sonnet 4.6 或 Opus 4.6，利用其卓越的戏剧结构把控能力生成细腻的视听语言描述。当项目切换至无审查模式或调用特定题材模板时，网关自动将剧本扩写与镜头提示词生成任务无缝路由至 Grok 或本地及私有化部署的无审查大语言模型（如 Llama-3.1-70B），从而规避通用商业 API 的内容拒绝响应（Refusal），保证连续剧创作流的完整性与稳定性。

### 路径二：现有视频导入与零 Token 浪费的二创逆向工程流水线

视频二创（Secondary Creation / Remix）是短剧制作中的高频需求，广泛涵盖爆款实拍短剧的风格化重制、保留原片运镜与表演的跨角色替换以及原片剧情续写三大场景。如果将原始视频直接切帧并全量发送给云端多模态视觉大模型（VLM），一部 3 分钟的 30fps 视频将产生 5,400 张图像，消耗数百万视觉 Token 并导致高昂账单与上下文窗口溢出。为此，本架构设计了本地多模态逆向拆解流水线（Local Multimodal Deconstruction Pipeline），在触碰任何云端计费 API 之前，于本地硬件完成 99% 的冗余信息过滤。

该流水线首先执行单次扫描音画分离与规格归一化，由本地 Worker 接收上传的源视频后，调用 FFmpeg 在单次 I/O 扫描中完成视频流标准化，并同步剥离出 16kHz 单声道 WAV 纯音频流供语音引擎处理。紧接着，系统启动自适应内容切点检测与关键帧精简提取，弃用固定时间窗口抽帧或死板的 FFmpeg 全局阈值过滤（如 gt(scene,0.4) 无法适应渐隐渐显与暗光镜头），转而调用 PySceneDetect 的 ContentDetector(threshold=27.0, min_scene_len=15) 算法。该算法将图像帧转换至 HSV 色彩空间，综合计算色相（Hue）、饱和度（Saturation）、亮度（Luminance）与边缘特征的加权差异，并维护滑动窗口自适应识别物理镜头切点（Hard Cuts）。对于识别出的每个独立镜头片段（Shot Segment），系统仅提取首帧、中位关键帧与末帧（每个镜头仅保留 1 至 2 张代表帧），将视觉分析样本量直接压缩 98% 以上。

在视觉切点提取的同时，并行管线利用本地 GPU 加速的 faster-whisper（Large-v3 模型）结合 WhisperX 说话人分离（Speaker Diarization）技术处理音频流，输出带有精确起止时间戳（start_time, end_time）及唯一说话人标识（speaker_id）的结构化台词文本。最后，本地引擎将每个镜头的时间范围、对应的说话人台词以及代表性关键帧（Base64 编码）组装为结构化批次对象，单次批量请求 Claude Vision 或本地视觉模型（如 Qwen3-VL、InternVL3 或 Florence-2）。模型在一次 API 调用中同时完成景别分类、运镜反推、人物动作描述、画面 OCR 字幕提取与安全分级，直接逆向生成可编辑的标准化分镜表。

进入二创重生成阶段后，工作台根据导演意图自动匹配四类重绘策略。第一种是原样动捕换角（Motion Control），将源视频镜头作为动作引导参考（reference_video_urls），结合新定妆的角色图调用 Seedance 2.5 Motion Control 接口，让新角色精准复刻原片演员的微表情、肢体动作与摄影机运动轨迹。第二种是局部指令式改写（Video Edit），路由至专用编辑接口，在保持原片主体锁定的前提下修改特定服装或背景元素。第三种是剧情无缝续写（Video Extend），基于源视频末帧与自然语言提示词自动向后延展 4 至 30 秒的新镜头。第四种则是全分镜重拍，仅保留逆向提取的分镜剧本与台词卡点节奏，使用全新定妆资产彻底重绘整集画面。

### 三合一资产定妆台：人物、道具、场景与零成本动作构图模板库

连续剧之所以区别于单条短视频，核心在于跨集、跨场景的视觉锚定（Visual Anchoring）。工作台设立独立的“定妆中心（Look-Dev Studio）”，将全剧视觉元素抽象为人物（Character）、道具（Prop）与场景（Scene）三大受控资产实体，每类实体均支持操作员本地上传参考图与调用文生图或图生图模型抽卡生成双模式。

在人物定妆环节，操作员输入外貌描述、服装设定与可选草图，网关调用 Flux Uncensored、SDXL Pony 或 Seedream 等模型生成包含正面特写、半身、全身三视图及不同光影下的多张候选图。操作员选定满意方案后，系统将其锁定为该角色的有序参考图数组 ref_urls[]（通常包含 3 至 5 张多角度标准照），并提取面部特征向量供后续 InsightFace 一致性校验使用。在道具定妆环节，针对剧中关键交互物品（如特定样式的怀表、专属武器或核心商品），系统生成白底多角度展示图或带比例尺的手持参考图，确保不同镜头中道具的几何结构与材质完全统一。在场景定妆环节，系统生成核心叙事空间（如顶层豪宅书房夜景或雨夜街道）的宽幅空镜基准图（Establishing Reference），从而锁定空间透视、建筑陈设与全局色彩基调。

此外，在涉及复杂双人交互、特定武打招式或特定姿态构图时，纯靠文本提示词极易导致生成模型出现人体结构崩坏。为此，工作台在 PostgreSQL 数据库中内置了零 Token 消耗的动作与构图模板库（templates 静态配置表），预置了按人物关系类型（relation）、姿态构图（pose）、环境类型（scene）、默认正负向提示词（default_prompt）、推荐时长与预置静帧参考图（refs[]）组织的结构化配置。前端直接读取 JSON 配置而不消耗任何大模型 Token；当分镜绑定某一姿态模板时，系统优先提取模板库预置的动作静帧，若无匹配静帧，才调用定妆模型结合角色定妆图生成一张“动作关键帧（Action Keyframe）”，随后将该关键帧与角色及场景定妆图共同压入视频生成接口的 refs[] 队列。

### 自动分镜与多参考长镜头视频生成主路径

完成剧本拆解与资产定妆后，系统进入分镜视频渲染阶段。传统工作流将每个 2 至 4 秒的碎镜头独立提交生成，不仅在拼片时产生严重的画面闪跳感，而且每次调用都需要重复支付模型冷启动与上下文开销。本架构充分利用 Seedance 2.5 的代际技术突破——该模型支持单次生成 4 至 30 秒的连续长镜头（Seedance 2.0 上限仅为 15 秒），且单次请求最高支持挂载 50 个多模态参考文件，具体涵盖最多 30 张参考图（reference_image_urls，而 2.0 仅支持 9 张）、10 段参考视频（reference_video_urls）以及 10 段参考音频（reference_audio_urls）。

在实际生产调度中，自动分镜引擎会将同一场次内连续的戏剧动作合并为一个 15 至 30 秒的“单镜到底（Single-Take）”综合调度包。系统自动从定妆库中检索当前场次出场的全部角色定妆图、核心道具图、场景空镜图与色彩参考图（合计控制在 30 张以内），并在生成的提示词中严格按照角色标签点名各个参考图的控制职责（例如明确指定参考图 1 至 3 控制女主面部与风衣、参考图 4 控制咖啡杯道具、参考图 5 至 6 控制天台晨曦环境），防止多参考特征相互竞争污染。同时，提示词引擎严格遵循五段式连续动作弧线（5-Part Continuous Action Arc）编写描述，依次涵盖起始画面构图、摄影机运动轨迹、主体连续动作与交互、环境光影动态变化以及结束定格画面，并同步开启原生音画生成开关（generate_audio: true）。

| 视频渲染子场景 | 首选模型代号 (model_key) | 备用/降级模型 (fallback_key) | 关键参数策略与网关路由规则 |
|---|---|---|---|
| 核心剧情长镜头 (I2V / Multi-Ref) | seedance_2_5_spicy | wan3_spicy | 挂载最多 30 张定妆图 + 10 段音视频参考；时长 10–30s；分辨率 1080p；动作自然且跨场景角色一致性最强。 |
| 高性价比长镜头 / 复杂交互 | wan3_spicy | seedance_2_5_spicy | 支持最长 30s 档位与 Uncensored 深度微调动作，作为主镜头高性价比主力或 Seedance 故障时的无缝热备。 |
| 参数短测与分镜动态草稿 (Draft) | minimax_h3 / seedance_2_5_turbo | spicy_v1 | 强制锁定 480p 分辨率与 4–5s 最短时长；用于低成本验证运镜与构图，确认无误后再升级母带渲染。 |
| 纯环境空镜与转场镜头 (T2V) | wan3_t2v / seedance_2_5_t2v | minimax_h3 | 无人物露脸或远景氛围镜头；自动剥离 omni_reference_task_type 参数，省去定妆图挂载开销。 |
| 成片面部一致性修复 (Face Swap) | insightface_swap_pro | 本地 roop_onnx 脚本 | 仅在成片抽帧面部余弦相似度低于阈值时触发；属于后处理修补，绝不替代前置 I2V 定妆参考。 |
| 4K 交付超分与无损拼片 | 本地 realesrgan_x4plus | seedance_2_0_4k | 因 Seedance 2.5 原生止步于 1080p 且无内置超分标志，1080p 母带统一交由本地队列免费超分至 4K。 |

## 极致 Token 与算力节约机制：好莱坞级品质的成本工程

要实现“以最小消耗制作好莱坞级巨作”，绝不能仅靠手工缩减提示词字数，而必须在系统底层构建一套数学上可验证的多维成本压缩引擎。本架构通过提示词前缀缓存、原生结构化输出、四级阶梯渲染漏斗以及网关参数防呆拦截四大核心机制，将全剧制作周期的 LLM Token 成本降低 85% 以上，并将视频 GPU 算力浪费压缩 70% 以上。

### 机制一：Anthropic 提示词前缀缓存（Prompt Caching）与静态上下文锚定

在连续剧生产过程中，一套包含世界观设定、数十位角色的详细外貌衣着参数、全剧场景列表、摄影机运动词典以及 JSON Schema 工具定义的系统提示词（System Prompt 与 Series Bible），其体积往往高达 15,000 至 40,000 Tokens。如果按常规方式在拆解每一集、修改每一个分镜时重复发送这些上下文，输入 Token 将呈指数级消耗。本架构在模型网关层强制部署 Anthropic Prompt Caching 中间件。

由于提示词缓存依赖于完全一致的前缀匹配（Exact Prefix Matching），网关对所有发往 Claude 的请求实施严格的四层静态前缀组装策略。第一层为工具与结构定义层，固定挂载分镜拆解、资产提取与提示词优化的 tools 数组并开启 strict: true；只要工具定义保持不变，Anthropic 会自动缓存工具定义与编译后的 JSON 语法树（Compiled Grammar），使后续请求自动享受缓存折扣。第二层为系统指令与影视工程词典，包含导演视听语言规范、Seedance 2.5 五段式提示词编写准则及安全分级规则，并在末尾标记缓存断点。第三层为项目静态圣经层（Series Bible），包含当前连续剧的所有已定妆角色、道具、场景 JSON 清单及前序剧集摘要，附加 `cache_control: {"type": "ephemeral", "ttl": "1h"}` 标头。Anthropic API 仅支持 "5m"（5 分钟）和 "1h"（1 小时）两种 TTL 档位；在密集编剧与分镜精修阶段，网关自动启用 "1h" 长效缓存，而在单次零散查询时切换为 "5m"。第四层为动态增量层，仅放置当前正在处理的单场戏小说原文或单个镜头的微调指令。

通过该前缀锚定架构，除首次写入缓存需支付基础输入价格 25% 的写入费外，后续所有分镜拆解、台词润色与镜头重绘请求中，占总体积 90% 以上的前三层静态上下文仅按基础输入 Token 价格的 10% 计费（直接节约 90% 输入成本），同时因跳过了前缀嵌入向量的重复计算，首字响应延迟大幅下降。

### 机制二：原生结构化输出（Native JSON Schema）与零废话 Token 压缩

大模型输出端（Output Tokens）的单价通常是输入端的数倍，且扩展思维链（Extended Thinking 的 budget_tokens）同样按输出 Token 计费。为了彻底消灭模型输出中的寒暄语、Markdown 解释性段落以及因 JSON 格式解析失败导致的整轮重试浪费，系统在网关层实施严格的格式与推理预算治理。

首先，系统全面弃用旧式的助手消息预填充（Assistant Prefill），因为在 Claude 4.6 及更高版本中预填助手响应会直接触发 HTTP 400 错误；所有数据提取与分镜生成接口统一采用 Messages API 原生的 `output_config: {"format": {"type": "json_schema", "schema": ...}}`，而所有触发下游动作的指令均采用带 strict: true 的 Tool Use。其次，系统采用短键名与枚举化 Schema 设计，保持 JSON Schema 紧凑且结构稳定，避免动态修改 Schema 字段导致底层编译语法缓存失效；在 Schema 内部使用高信息密度的枚举值（如景别仅限 ["ECU", "CU", "MCU", "MS", "WS", "EWS"]）替代冗长的自然语言描述，并在应用层通过 Pydantic 与 Zod 执行跨字段业务规则二次校验。此外，网关按需分配动态思维预算，仅在“全剧大纲架构”与“复杂悬疑反转设计”阶段开启高预算 Extended Thinking，而在常规“单镜头视觉提示词翻译”与“定妆属性提取”阶段关闭扩展思考或路由至轻量级模型，从而避免无谓的推理 Token 消耗。

### 机制三：四级阶梯式视频算力漏斗（L0–L3 Tiered Rendering Funnel）

视频生成是整个工作台算力消耗的最大中心。以 Seedance 2.5 为例，虽然其 480p 基础档位仅需 $0.90 / 5 秒（按秒折算），但一段 30 秒 1080p 的高规格长镜头渲染耗时高达 10 分 17 秒，一旦因运镜描述不当或角色穿帮而作废，将造成巨大的资金与时间双重沉没成本。为此，系统在业务 API 与任务队列层强制执行从 L0 到 L3 的四级阶梯渲染漏斗。

在 L0 零算力模板与动态故事板预演阶段（Zero-Compute Animatic），分镜表生成后系统首先检索 PostgreSQL 的 templates 表，直接复用预置的姿态与场景静帧及默认提示词，实现零 Token 消耗。随后，本地 FFmpeg 队列将各镜头的角色及场景定妆图与本地对白音频自动合成为带字幕的动态故事板视频，使导演以零美元成本完成全集剪辑节奏与台词卡点的审核。

进入 L1 极速低分辨率草稿试拍阶段（480p Draft Verification）后，对于需要验证复杂运镜或多人交互的关键镜头，系统禁止直接提交 1080p 长任务，而是自动将请求路由至 minimax_h3、spicy_v1 或 seedance_2_5 的 480p、4 至 5 秒草稿档进行快速参数试拍；对于纯环境空镜或远景无脸镜头，网关自动切换为纯文生视频（video.t2v），省去多图参考的计算开销。

当草稿验证通过后，导演一键升级至 L2 尾帧链式锚定与 30 秒单镜到底母带渲染阶段（Chained Master Rendering）。系统在此阶段采用长镜头合并策略，将同一场景的连续动作合并为 15 至 30 秒单次生成，避免多段 5 秒视频拼接产生的割裂感；同时启用首尾帧无缝接力（Frame-to-Frame Chaining），在提交第 N 个镜头时显式传入 return_last_frame: true，待 API 完成渲染并返回该镜头的精确末帧 URL 后，系统随即将该 URL 作为第 N+1 个镜头的 first_frame_url 注入，实现跨镜头像素级连续衔接。

最后，在 L3 本地零额度修复与 4K 超分交付阶段（Zero-Credit Local Post-Processing），若 1080p 成片整体运镜良好而仅有个别帧出现轻微面部变形，系统绝不浪费积分重新调用视频生成接口，而是将视频推入后处理队列调用 face.swap（InsightFace）替换统一角色面部。由于 Seedance 2.5 原生最高支持 1080p 且不提供内置 4K 放大标志，所有 1080p 母带统一由本地后处理队列调用 Real-ESRGAN 脚本与 FFmpeg 完成 4K 超分、去水印与整集无损拼片，全程不占用任何生成式模型计费额度。

### 机制四：网关级预扣实扣账本与防呆参数拦截器

即使在个人自用场景下，对接多家商业与无审查第三方视频 API（如 Krea、ApiPass、WaveSpeedAI、EvoLink、MakeUGC 等）时，账户余额失控与隐性计费陷阱依然频发。工作台内置企业级积分与美元成本映射账本（credit_ledger 与 cost_map），并在网关层设立防参数冲突自检器（Pre-Flight Parameter Sanitizer）。

由于不同供应商对 Seedance 2.5 等模型的参数校验极为严苛，任何非法组合都会导致请求失败或误触发高价模式，网关在发起 HTTP 请求前执行三项确定性清洗：第一是参考模式互斥校验，first_frame_url 及 last_frame_url 与多模态参考数组（reference_image_urls、reference_video_urls、reference_audio_urls）严禁同时存在，若任务为首尾帧过渡则自动剥离多参考数组，若为多参考控场则自动清空首尾帧字段；第二是空参考自动降级，若请求仅含文本提示词而无任何图片、视频或音频参考资产，网关自动移除 omni_reference_task_type 字段并将其路由至标准 T2V 端点，防止触发参数无效报错；第三是视频编辑模式自适应强制转换，在二创模式下若提示词包含修改原片指令，Seedance 2.5 会自动将其识别为视频编辑（Video Edit）任务并按“输入时长加输出时长”双向计费，此时若传入常规宽高比与固定时长会直接报错，故网关检测到编辑任务时强制覆写 aspect_ratio: "adaptive" 且 duration: -1，并路由至独立的 video.edit 端点。

在账本结算闭环方面，操作员点击生成瞬间，业务 API 根据模型单价、时长与分辨率倍率估算消耗并执行数据库行级锁预扣，余额或日配额超限则直接拒绝执行。由于 30 秒 1080p 视频生成耗时长达 10 分钟以上，系统严禁使用长连接阻塞等待，全面采用异步 X-Webhook-URL 或 callback_url 回调结合后台短轮询机制。回调成功且下载成片至私有对象存储后，系统按实际渲染秒数实扣并执行多退少补；若遇供应商 5xx 错误、超时进入死信队列或被内容策略拒生成，系统自动按规则回滚预扣额度并触发备用模型（如从 seedance_2_5_spicy 切换至 wan3_spicy）无缝降级。

## 核心数据库 Schema 与统一多模型网关契约

### 关系型数据库核心表设计（PostgreSQL DDL）

以下数据模型在 91-Generate 标准核心表集的基础上，针对连续剧分集结构、人物与道具及场景三合一定妆以及视频二创溯源进行了完备扩展，Claude Code 在初始化后端时可直接执行此迁移脚本：

```sql
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
```

### 统一任务下发与多模型网关核心实现（Python FastAPI）

业务侧下发生成任务统一调用 POST /api/v1/jobs，模型网关负责完成参数校验、互斥规则清洗、Anthropic 缓存注入以及多供应商路由适配。以下为可直接投入生产的核心网关请求契约代码：

```python
# apps/server/src/gateway/schemas.py
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator
from uuid import UUID

class MultimodalRefs(BaseModel):
    image_urls: List[str] = Field(default_factory=list, max_length=30)
    video_urls: List[str] = Field(default_factory=list, max_length=10)
    audio_urls: List[str] = Field(default_factory=list, max_length=10)

class JobCreateRequest(BaseModel):
    project_id: UUID
    shot_id: Optional[UUID] = None
    task_type: Literal[
        "image.t2i", "image.i2i", "video.i2v", "video.t2v",
        "video.edit", "video.extend", "face.swap", "post.upscale"
    ]
    model_key: str = Field(..., examples=["seedance_2_5_spicy", "wan3_spicy", "minimax_h3", "flux_uncensored"])
    prompt: str = Field(..., min_length=1)
    negative_prompt: Optional[str] = None
    duration: int = Field(default=5, description="4 to 30 seconds, or -1 for adaptive video edit")
    resolution: Literal["480p", "720p", "1080p"] = "720p"
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"] = "9:16"
    first_frame_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    return_last_frame: bool = True
    generate_audio: bool = True
    refs: MultimodalRefs = Field(default_factory=MultimodalRefs)
    callback_url: Optional[str] = None

    @model_validator(mode="after")
    def enforce_seedance_2_5_constraints(self) -> "JobCreateRequest":
        # 规则 1: last_frame_url 必须与 first_frame_url 同时使用
        if self.last_frame_url and not self.first_frame_url:
            raise ValueError("last_frame_url cannot be used without first_frame_url.")

        # 规则 2: 首尾帧模式与多模态参考数组互斥 (防止 ApiPass/Seedance 2.5 400 报错)
        has_multimodal_refs = bool(self.refs.image_urls or self.refs.video_urls or self.refs.audio_urls)
        if self.first_frame_url and has_multimodal_refs:
            # 优先保留首尾帧链式接力，或由网关拆分为多参考请求
            self.refs = MultimodalRefs()

        # 规则 3: 视频编辑模式强制 adaptive 宽高比与 -1 时长
        if self.task_type == "video.edit":
            self.aspect_ratio = "adaptive"
            self.duration = -1
        elif self.duration != -1 and not (4 <= self.duration <= 30):
            raise ValueError("Standard video generation duration must be between 4 and 30 seconds.")
        return self
```

在 LLM 剧本与分镜生成侧，网关通过 Anthropic 官方 SDK 结合 json_schema 与 cache_control 块实现零废话、90% 缓存折扣的结构化拆镜调用：

```python
# apps/server/src/services/script_engine.py
import json
from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field
from typing import List, Literal

class ShotSchema(BaseModel):
    shot_order: int
    shot_scale: Literal["ECU", "CU", "MCU", "MS", "WS", "EWS"]
    duration: int = Field(ge=4, le=30)
    camera_motion: str
    five_part_visual_prompt: str = Field(
        description="Continuous arc: 1.Opening frame 2.Camera move 3.Subject action 4.Environment shift 5.Final frame"
    )
    dialogue: str
    character_names: List[str]
    prop_names: List[str]
    scene_name: str
    suggested_pose_template: str

class EpisodeStoryboardOutput(BaseModel):
    episode_title: str
    hook_summary: str
    shots: List[ShotSchema]

class CachedDirectorLLM:
    def __init__(self, api_key: str):
        self.client = AsyncAnthropic(api_key=api_key)

    async def compile_episode_storyboard(
        self, system_rules: str, series_bible_json: dict, episode_raw_text: str
    ) -> EpisodeStoryboardOutput:
        # 使用 1h TTL 缓存系统指令与全剧定妆圣经，后续调用仅按 10% 基础输入价格计费
        response = await self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=[
                {
                    "type": "text",
                    "text": system_rules,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}
                },
                {
                    "type": "text",
                    "text": f"SERIES_BIBLE_LOCKED_ASSETS:\n{json.dumps(series_bible_json, ensure_ascii=False)}",
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"将以下小说章节/二创大纲拆解为连续剧分镜表（优先合并为15-30秒长镜头）：\n{episode_raw_text}"
                }
            ],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": EpisodeStoryboardOutput.model_json_schema()
                }
            }
        )
        raw_json = response.content[0].text
        return EpisodeStoryboardOutput.model_validate_json(raw_json)
```

对于本地视频二创逆向拆解器，Worker 直接组合 PySceneDetect 与 faster-whisper，无需任何云端预处理开支：

```python
# apps/server/src/workers/video_remix_deconstructor.py
import subprocess
from pathlib import Path
from scenedetect import detect, ContentDetector
from faster_whisper import WhisperModel

class LocalVideoDeconstructor:
    def __init__(self, whisper_size: str = "large-v3", device: str = "cuda"):
        self.whisper = WhisperModel(whisper_size, device=device, compute_type="float16")

    def deconstruct_for_remix(self, video_path: str, output_dir: str) -> list[dict]:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        wav_path = out_path / "audio_16k.wav"

        # 1. FFmpeg 单次扫描剥离 16kHz 单声道音频
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav_path)
        ], check=True, capture_output=True)

        # 2. PySceneDetect HSV 自适应内容切点检测 (默认阈值 27.0, 最小镜头长度 15 帧)
        scene_list = detect(video_path, ContentDetector(threshold=27.0, min_scene_len=15))

        # 3. Faster-Whisper 本地语音转写与时间戳提取
        segments, _ = self.whisper.transcribe(str(wav_path), word_timestamps=True)
        transcript_list = [
            {"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments
        ]

        # 4. 为每个自然切点镜头提取中位关键帧并对齐台词
        shots_metadata = []
        for idx, (start_tc, end_tc) in enumerate(scene_list):
            s_sec, e_sec = start_tc.get_seconds(), end_tc.get_seconds()
            mid_sec = (s_sec + e_sec) / 2.0
            kf_file = out_path / f"shot_{idx:04d}_keyframe.jpg"
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(mid_sec), "-i", video_path,
                "-frames:v", "1", "-q:v", "2", str(kf_file)
            ], check=True, capture_output=True)

            aligned_lines = [
                t["text"] for t in transcript_list
                if not (t["end"] <= s_sec or t["start"] >= e_sec)
            ]
            shots_metadata.append({
                "shot_order": idx + 1,
                "start_time": round(s_sec, 2),
                "end_time": round(e_sec, 2),
                "duration": max(4, min(30, round(e_sec - s_sec))),
                "keyframe_path": str(kf_file),
                "dialogue": " ".join(aligned_lines)
            })
        return shots_metadata
```

## 合规与安全防护边界（支持 Uncensored 模式的本地与云端治理）

由于工作台同时支持常规短剧与 Uncensored 连续剧创作，架构必须在赋予个人导演最大创作自由与严守法律及安全红线之间建立硬性技术护栏。

首先，系统实施严格的年龄门与内容分级硬拦截（Hard Content Gate），在系统启动及项目开启 uncensored_nsfw 模式时强制验证 18+ 确认状态；在网关提示词预检层（Pre-Flight Guard）内置本地正则与轻量级分类器，对任何涉及未成年人相关的违规词条与图像特征执行零容忍硬拦截，直接阻断任务并记录审计日志，确保系统仅允许生成虚构成年人角色。

其次，系统建立真人面部授权与隐私边界保护机制，在定妆台上传自定义人物参考图或调用 face.swap 接口时，强制要求操作员确认“虚构合成角色或已获完整肖像授权”声明，严禁未授权的真人换脸操作；对于高度敏感的个人创作项目，网关支持一键切换为纯本地闭环模式，通过调用本地网络边界内自建的开源视觉模型（如 LLaVA 或 InternVL）、本地语音转写（Whisper）及本地开源推理核心（Llama 3 或 Mistral）处理全部数据，确保所有私密剧本、定妆图与成片 100% 留存在个人局域网内。

最后，系统执行严格的密钥隔离与短时签名防盗链策略，所有第三方供应商 API Key（如 Krea、ApiPass、WaveSpeedAI、Higgsfield、Anthropic）仅加密存储于服务端环境变量或数据库密钥表中，前端工作台永不下发任何密钥；所有存放在 S3 兼容对象存储中的定妆原片与分镜成片均通过短时有效的签名 URL（Presigned URL）访问，同时完整保留 jobs 与 credit_ledger 审计流水以便对账与故障回溯。

## Claude Code 一步到位全自动构建工程规范

将复杂的多模态全栈架构直接交由 AI 编程代理（Claude Code）构建时，最常见的失败模式是上下文窗口被冗长文档撑爆导致指令遗忘、跳过规划直接编写代码造成前后端契约脱节，以及为了通过测试而篡改测试用例或编写虚假 Mock。实证研究表明，大模型在一个上下文窗口内仅能可靠遵循约 150 至 200 条独立指令，而 Claude Code 自身的系统提示词已占用约 50 个槽位，留给项目级配置的有效指令槽位仅有 100 至 150 个；一旦配置文件超过 200 行，注意力机制就会开始丢弃关键架构约束。因此，要让 Claude Code 真正做到“发给它就直接一步到位做出成品”，必须采用按业务领域组织的单仓目录结构（Domain-Driven Monorepo）、高度凝练的百行以内 CLAUDE.md 行为契约、零例外确定性 Hooks（.claude/settings.json）以及基于测试驱动开发（TDD）的分阶段自动执行脚本。

### 领域驱动的单仓目录结构规范（Domain-Driven Repository Layout）

按业务领域（Domains）而非单纯按技术层级组织代码，能够显著降低 Claude Code 的文件检索冗余与上下文 Token 消耗。Claude Code 初始化仓库时必须严格创建以下模块结构：

| 仓库路径 | 所属模块领域 | 核心文件与职责说明 |
|---|---|---|
| CLAUDE.md | Agent 行为治理契约 | 严格控制在 100 行以内的根级指令文件，定义构建命令、架构红线与验证标准。 |
| .claude/settings.json | 确定性生命周期钩子 | 配置 PostToolUse 自动执行代码检查与类型校验，PreToolUse 拦截危险命令。 |
| docker-compose.yml | 基础设施编排 | 一键拉起 PostgreSQL 16、Redis 7、MinIO（S3 兼容存储）及前后端开发容器。 |
| apps/server/src/domains/script/ | 剧本与二创拆解领域 | 含 script_engine.py（带 1h Prompt Caching 的 Claude 结构化拆镜）与 video_remix.py（FFmpeg + PySceneDetect + faster-whisper 本地逆向管线）。 |
| apps/server/src/domains/lookdev/ | 三合一定妆与模板领域 | 含 asset_manager.py（人物/道具/场景参考图入库）与 template_service.py（零 Token 读取姿态与构图 JSON 模板）。 |
| apps/server/src/domains/storyboard/ | 分镜调度与阶梯渲染 | 含 shot_orchestrator.py（L0 动态故事板合成、L1 480p 短测、L2 30s 尾帧接力长镜头、L3 本地 ESRGAN 超分与 InsightFace 换脸）。 |
| apps/server/src/domains/gateway/ | 统一模型网关与账本 | 含 router.py、adapters/seedance.py、adapters/wan.py、adapters/flux.py 及 credit_ledger.py（预扣、Webhook 验签、实扣与故障降级）。 |
| apps/server/tests/ | 后端端到端验证集 | 含参数互斥校验测试、账本并发预扣退回测试、本地二创切点提取测试（严禁 Mock 核心逻辑）。 |
| apps/web/src/features/ | 前端导演工作台界面 | 含 project-importer/（小说与二创导入）、lookdev-studio/（三合一定妆卡片）、storyboard-canvas/（分镜板与生成控制）、admin-ledger/（模型开关与消耗大盘）。 |

### 生产级 CLAUDE.md 根级治理契约（直接保存至仓库根目录）

以下 CLAUDE.md 严格控制在 85 行以内，剔除一切模型可通过阅读代码自行推断的语言常识，仅保留高信噪比构建命令与不可违背的架构红线：

```markdown
# AI Short-Drama & Series Director Workbench - Agent Governance Contract

## 1. Core Architecture & Domain Rules (NON-NEGOTIABLE)
- Use the simplest possible approach: Do not write unsolicited abstractions or premature wrappers. Organize code strictly by domain (script, lookdev, storyboard, gateway).
- Unified Gateway Only: Frontend (apps/web) MUST ONLY call POST /api/v1/jobs and domain REST endpoints. Never call third-party model SDKs from the frontend or expose provider API keys.
- Anthropic API Rules (Claude 4.6+):
  - NEVER use assistant message prefill (returns HTTP 400 on Claude 4.6+).
  - ALWAYS use output_config.format with type: "json_schema" for typed script/storyboard extraction, and strict: true for tool definitions.
  - ALWAYS attach cache_control: {"type": "ephemeral", "ttl": "1h"} to System Prompts and the SERIES_BIBLE context block to enforce the 90% Prompt Caching read discount.
- Seedance 2.5 & Video Gateway Rules:
  - Enforce mutual exclusivity: first_frame_url/last_frame_url MUST NOT coexist with reference_image_urls, reference_video_urls, or reference_audio_urls (max 30 images, 10 videos, 10 audios).
  - If a request has no reference assets, strip omni_reference_task_type and route as pure T2V.
  - If task_type == "video.edit", force aspect_ratio = "adaptive" and duration = -1.
  - Video generation is strictly asynchronous: pre-deduct credits in PostgreSQL -> enqueue Redis job -> handle X-Webhook-URL / callback_url -> download to S3 -> settle actual credits (or full refund on 5xx/timeout and failover from seedance_2_5_spicy to wan3_spicy).
- Video Remix Zero-Token Preprocessing:
  - Never send raw video frames directly to cloud VLMs. Always run local FFmpeg (16kHz mono WAV) + PySceneDetect (ContentDetector(threshold=27.0, min_scene_len=15)) + faster-whisper first, extracting only 1 keyframe per detected shot.

## 2. Build, Lint & Verification Commands
- Backend setup & test: cd apps/server && uv sync && uv run pytest -v
- Backend type & lint check: cd apps/server && uv run ruff check . && uv run mypy src
- Frontend setup & build: cd apps/web && pnpm install && pnpm tsc --noEmit && pnpm test:run && pnpm build
- Full stack services: docker compose up -d postgres redis minio
- Database migration: cd apps/server && uv run alembic upgrade head

## 3. TDD & Context Hygiene Workflow
- Write failing tests first: For every domain module, write pytest or vitest tests based on expected input/output schemas, confirm they fail, commit tests, then implement code until all tests pass.
- NEVER modify tests to make them pass: Fix the underlying implementation root cause.
- Document & Clear: After completing each Milestone phase, write progress to PROGRESS.md so the session can be safely reset with /clear.
```

### 确定性执行钩子配置（.claude/settings.json）

与具有指导性质的 CLAUDE.md 不同，Claude Code 的 Hooks 是确定性触发的硬性门禁，能够确保代码检查在每次文件修改后无一例外地自动执行。将以下配置保存为 .claude/settings.json，即可在每次文件编辑后自动执行语法与类型检查，并在会话停止前强制验证测试通过率，同时拦截破坏性删除命令：

```json
{
  "permissions": {
    "allow": [
      "Bash(uv *)",
      "Bash(pnpm *)",
      "Bash(docker compose *)",
      "Bash(ffmpeg *)",
      "Bash(git status*)",
      "Bash(git diff*)",
      "Bash(git add *)",
      "Bash(git commit *)"
    ],
    "deny": [
      "Bash(rm -rf *)",
      "Bash(git push --force*)"
    ]
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'if [[ \"$CLAUDE_FILE_PATHS\" == *.py ]]; then cd apps/server && uv run ruff check --fix $CLAUDE_FILE_PATHS && uv run mypy src; elif [[ \"$CLAUDE_FILE_PATHS\" == *.ts* ]]; then cd apps/web && pnpm tsc --noEmit; fi'"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'cd apps/server && uv run pytest -q && cd ../web && pnpm tsc --noEmit'"
          }
        ]
      }
    ]
  }
}
```

### 一键式全自动构建引导提示词与执行脚本（One-Shot Turnkey Execution Script）

为彻底避免长会话中途上下文衰减（Context Loss），工程最佳实践是将本规格书保存为仓库根目录下的 ARCH_SPEC.md，并配合上述 CLAUDE.md 与 .claude/settings.json，通过以下分阶段自动验证脚本驱动构建。该脚本利用 ultrathink 关键字触发 Claude Code 的最高级别扩展思考预算，并在每个里程碑通过 pytest 与 tsc 门禁后自动提交代码，实现零人工干预的一步到位交付：

```bash
#!/usr/bin/env bash
# save as: build_director_workbench.sh
# 用法: chmod +x build_director_workbench.sh && ./build_director_workbench.sh
set -euo pipefail

echo "=== Phase 1: 基础设施、PostgreSQL 9大核心表与预扣实扣账本引擎 ==="
claude -p "Read @ARCH_SPEC.md and @CLAUDE.md. ultrathink and execute Phase 1 following strict TDD:
1. Create docker-compose.yml (PostgreSQL 16, Redis 7, MinIO) and initialize apps/server (Python 3.13 + FastAPI + SQLAlchemy 2.0 Async + Alembic) and apps/web (Next.js App Router + Tailwind + Shadcn + Zustand).
2. Implement the exact 9 PostgreSQL tables (users, model_catalog, templates, projects, lookdev_assets, shots, jobs, assets, credit_ledger) and seed default models (seedance_2_5_spicy, wan3_spicy, minimax_h3, flux_uncensored, realesrgan_x4plus) plus 10 initial SFW/Uncensored templates.
3. Write pytest tests for credit_ledger pre-deduction, concurrency row locking, actual settlement, and 5xx full refund before implementing the ledger service. Run pytest until 100% green and commit Phase 1."

echo "=== Phase 2: 统一多模型网关、Prompt Caching 编剧引擎与本地视频二创拆解器 ==="
claude -p "Read @ARCH_SPEC.md and @CLAUDE.md. ultrathink and execute Phase 2 following strict TDD:
1. Implement JobCreateRequest with all Seedance 2.5 mutual exclusivity validators (first_frame_url vs reference_image_urls/video_urls/audio_urls, video.edit adaptive ratio and -1 duration, stripping omni_reference_task_type when refs are empty).
2. Implement CachedDirectorLLM using Anthropic native output_config.format json_schema and 1h TTL cache_control on System Prompt and SERIES_BIBLE, plus fallback routing to Uncensored LLM when content_mode == 'uncensored_nsfw'.
3. Implement LocalVideoDeconstructor using FFmpeg (16kHz mono WAV), PySceneDetect ContentDetector(threshold=27.0, min_scene_len=15), and faster-whisper timestamp alignment.
4. Implement Provider Adapters (Seedance 2.5, Wan 3 Spicy, MiniMax H3, Flux Uncensored) with async Webhook callback verification (/api/v1/webhooks/{provider}) and automatic failover from seedance_2_5_spicy to wan3_spicy on timeout/5xx.
5. Write comprehensive pytest suites covering all edge cases without mocking validation logic. Run tests until green and commit Phase 2."

echo "=== Phase 3: 三合一定妆台、L0-L3 阶梯分镜渲染队列与 Next.js 导演工作台前端 ==="
claude -p "Read @ARCH_SPEC.md and @CLAUDE.md. ultrathink and execute Phase 3 following strict TDD:
1. Implement the 3-tier Redis/Celery worker queues (high-priority T2I/face-swap queue, per-model isolated video queue, and zero-cost local post-processing queue for L0 FFmpeg animatics, InsightFace swap, Real-ESRGAN 4K upscale, and OpenTimelineIO episode assembly).
2. Build the complete Next.js Director Workbench UI in apps/web:
   - Project & Novel/Video Remix Importer (with SFW / Uncensored 18+ mode toggle).
   - Three-in-One Look-Dev Studio for Characters, Props, and Scenes (supporting custom image upload, T2I generation, and zero-token Pose Template selector).
   - Interactive Storyboard Canvas showing L0 Animatic -> L1 480p Draft -> L2 1080p 30s Single-Take Master progression, frame-to-frame chaining toggle (return_last_frame -> first_frame_url), and real-time credit burn estimator.
   - Admin & Cost Governance Drawer (model switches, unit pricing, task monitor, manual retry/refund).
3. Verify frontend build with 'pnpm tsc --noEmit && pnpm build' and backend integration tests with 'uv run pytest'. Fix all issues until zero errors remain and commit final release."
```

## 架构落地执行路线与工程演进结论

综合全链路技术分析，将该 AI 短剧与连续剧导演工作台从架构蓝图转化为生产级成品的核心路径，在于严格遵循模板库与三合一定妆锚定、零 Token 本地预处理与前缀缓存拆镜、L1 草稿短测到 L2 多参考 30 秒长镜头尾帧接力，以及本地 4K 超分与换脸兜底的确定性工业流水线。在通过 Claude Code 落地时，开发者只需在仓库根目录放置上述 ARCH_SPEC.md（即本架构书全文）、CLAUDE.md 与 .claude/settings.json，随后运行分阶段构建脚本；系统即可按照“先跑通单家主力视频 API（Seedance 2.5）与积分预扣实扣账本，再接入零成本模板库与人物、道具及场景定妆，最后扩展多模型网关降级、视频二创逆向管线与后台监控”的渐进验证顺序，一次性交付兼具好莱坞级叙事连续性与极致算力经济性的个人导演工作台。

---

## 落地核实附注（实现方补充，非原文）

- Claude 模型：默认改用当前模型 ID（`claude-opus-5-5`，轻量任务 `claude-sonnet-5-5`），可通过环境变量覆盖；`claude-sonnet-4-6` 仍可配置但不再作为默认。
- `output_config.format` / `cache_control.ttl="1h"` / 禁止 assistant prefill：已对照 Anthropic 官方 SDK 文档核实。注意 1h TTL 缓存写入价为基础输入价的 2 倍（+25% 是 5m TTL 的价格）。
- 视频供应商（Seedance 2.5、Wan 3、MiniMax H3、Flux Uncensored 等）的模型名、字段名、端点、单价、30s/30 图上限均未能从官方文档核实，全部放在适配器层作为可配置项，标注 NOT_TESTED。
- `.claude/settings.json` 中原文的 `$CLAUDE_FILE_PATHS` 不是 Claude Code 提供的变量（hook 通过 stdin JSON 接收 `tool_input.file_path`），已改为读取 stdin 的脚本实现，语义不变。
