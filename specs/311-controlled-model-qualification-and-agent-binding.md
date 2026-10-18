# Spec 311 — 受控模型资格评测与 Agent 绑定迁移

- 版本：v2.0
- 状态：**已搁置、未批准未执行。用户要求沿用现有模型，改由Spec314提议同模型修订；下文只保留历史提案，不构成待执行队列。尚未调用候选模型或修改配置。**
- 日期：2026-10-17
- 前置：AGENTS.md、TechDesign §8–9、实时语音 Spike、Spec308/309 保护基线及 Spec310 的质量目标。

## 1. 要解决的问题与新授权边界

Spec310 的十一版 Prompt 开发候选均未取得发布资格。最后 V11 的独立全文评审仍有6条候选阻断；总结正方新6优/旧2优、反方新0优/旧8优，FREE新2优/旧6优。完整失败冻结为 `profile_output/spec310/FAILED_CANDIDATE_V11_FREEZE.json`，SHA `4d54f57790f1670ec1193ab6ff5b6fa24b89d6a9db82bb7fc4cb613494a79a5f`。原输出、评审、解盲、48份原声及各轮失败继续保留，不再通过近义措辞和同版重复抽样寻找绿色结果。

本规格提议：先固定现有写作策略，隔离比较两个明确模型候选的质量、延迟及原协议兼容性；只有一项候选取得完整资格后，才创建独立模型资源并迁移现有85个 Agent 的绑定。**Spec310 明确冻结模型及参数，现有批准不包含这次模型调用、凭据内存处理和绑定迁移；必须另获本规格批准。** 本文不自动授予执行或发布资格。

保护全部173个已安装 Core/Jobs/迁移文件、4个 Web 运行逻辑文件和生成契约。Agent runtime SHA 保持 `f174e56773bec848cf177a30338c84ca20e12d50fb193b8fd469cba1a2419abb`。不改 MatchActor、权限、发言顺序、计时、队列、重试/暂停/恢复/重置、ASR/TTS/LiveKit 协议、音色/rate/CPS、600ms收尾、归档及历史快照；不改普通10秒和 DECISION 3秒超时，不改原 `max_tokens` 或 Thinking 处理代码，不做 migration、不重启 Core/Jobs。根 PRD 缺失状态保持，沿用 Spec310 对历史 PRD 的核对结论，不恢复旧文档或采用历史模型表。

Spec310 最终 Web V4b/Linux03 构件独立保持；不再改 UI 产品范围。用户也可选择仅发布已验证的 UI 切片、继续保留已发布 V12 Prompt/Flash，此替代方案不冒称解决辩论质量，也不在本文内自动执行或改写 Spec310。

## 2. 已核实的完整目标与尚未知项

Root 新鲜生产只读投影 `profile_output/spec310/READONLY_AGENT_MODEL_BINDINGS_V11_02.json`，SHA `42cf329a7cadd2128ccad06ae8e69c46b01de03c647a74b15e1a9ef2343932c4`，实际枚举6条非历史、非 ARCHIVED 正式规则的85个唯一 ENABLED Agent：

| rule UUID | Agent数 | 规则 status/lifecycle | 当前 revision |
| --- | ---: | --- | ---: |
| 29ea9080-5a66-43ee-9c7d-984a18dbfd21 | 16 | ENABLED/ENABLED | 112 |
| 2bb01cbc-0f88-4c29-b1e8-2d61685e7f00 | 14 | READY/DISABLED | 181 |
| 9439d9e5-8fe5-4c42-850a-d02e16809197 | 14 | READY/DISABLED | 98 |
| a648c945-182e-4c5f-9b25-c53e262b1ab5 | 15 | READY/DISABLED | 191 |
| cee79f06-c2f6-401d-ab73-a7584d40af22 | 14 | READY/DISABLED | 180 |
| d2e24b41-47d1-4d33-bba8-ee6c54ff5078 | 12 | ENABLED/ENABLED | 171 |

85个 Agent 全部绑定同一源 ModelProfile `ad417b5c-f3bb-4e18-8b94-1c55819792cd`，provider model_id 为 `qwen3.7-flash-2026-07-15`；85份 Agent generation_params 全为空。源模型 ENABLED，generation_params 为空、max_concurrency=50、token_per_char=1.0，凭据密文及 nonce 存在。投影没有选择/导出凭据值或密文，未创建认证 Session、调用模型或写生产。**凭据存在不证明 Plus 已获授权或可调用。** 当时存在1个活动房间/比赛，发布必须重新核查并等待 idle。

前期 `MODEL_CONFIG_FEASIBILITY_IF_NEW_SPEC.md`（SHA `4034e012564f56b2653870aeab7bf5317cbd837b83020ebaba0c154445eb6e11`）及同名 JSON（SHA `c7b863462fca4d26ff951ae249ed504bfc085deef27b251e212129009a58bd3e`）原样保留。其“61条绑定/参数未知、源模型数k未知”已被上述完整新清单解决：当前 k=1、85条原绑定及空参数已知；其关于凭据、逐项非原子写入及旧房查询的风险仍成立。执行前仍要刷新前态，不把此投影当未来永不漂移的清单。

## 3. 冻结模型、写作策略与对照

候选顺序固定：先 `qwen3.7-plus-2026-05-26`，仅当它在当前 Host 不可用，或质量/原延迟兼容资格失败时，才评测 `qwen-plus-2025-12-01`。这是离线候选筛选顺序，**不是比赛中自动 fallback**。两者的当前 Host 授权与可用性均为 UNKNOWN；不由历史 Spike、模型目录或凭据存在推定。本规格获批后，首条预注册开发请求兼作实际可用性检查；不可用则早停该候选并保存失败和未执行项，不另换 Host、别名、供应商或反复试同一请求。

在任何替代模型输出产生前，固定 **V9 Prompt** 为共同策略：源文件取 `candidate-v9-frozen/scripts/ops/spec308_prompt_bundle.py`，SHA `41032cc0c0abf133c75edc8493edbe31e7674b020e74c9a508cd7f7d78b16bde`。它曾取得双方总结各新7优/旧1优，但仍有2条事实/条件阻断，绝不称其已合格。采用它是预先固定比较材料，不再根据 Plus 输出调措辞。

四主身份、两开发题、双方持方、真实公共合成历史、音色/rate/CPS、窗口和全部非模型参数沿用已冻结输入。V9 的四种专业偏好、语境及单层全文字符 TARGET 预算保持，不恢复180/85统一上限、固定段句、额外系数或生成后裁字。保持 ModelProfile/Agent 参数 `{}`、max_concurrency=50、token_per_char=1.0；由原适配器显式 `enable_thinking=false`。不借新模型调整温度、token factor、计时或输出协议。

每进入开发评测的候选预注册24个新 SPEECH 请求：两题×双方×四身份的16篇总结，另8篇FREE，并取得24份完整 TTS/PCM。加上后续留出4篇FREE，全12篇FREE沿用每身份10/15/30秒各一次的边际平衡，不冒称身份×双方×窗口全交叉。比较三组完整正文：V12/Flash24、V9/Flash24、V9/候选24，共72篇匿名文本，可由一次独立评阅完整比较。分别报告：

- V9/候选相对 V9/Flash：固定写作策略的模型差异。
- V9/候选相对 V12/Flash：拟发布产品组合的差异。

原 Flash 两组只复用已有实际结果，不再抽样 Flash：V9开发请求 SHA `0f491fad1ea4e3185a0046abf1fdea5376152d21c8a98c73a1aaec98801745f2`，实际24条结果 SHA `2ffd491c4ea034c5da15ac35e33c0d8ed599907473182d052b859a55ba5a8f2a`；V12/Flash 从 V1 真实结果中精确复用旧24条，原文件 SHA `d137216e0b9d52cb3a16bda19b886cc1cffe3662231cd821b1c81c3d1e7640d4`。复用前验证各自原完整请求、metadata/messages SHA、模型/参数、stop、流一致和 cleanup；不能只凭 pair_id 或正文相似复用。候选与 V9/Flash 的差异仅为已声明模型资源/model_id及相关哈希，消息、模板、身份、历史、窗口、声参和原 token 上限保持一致。旧音频的已知证据限制保留，不冒称新执行72次或72份全新音频。

## 4. 内容、延迟与未见边界验收

独立评阅逐篇核对完整权威历史和正文，再解盲；保留原评审、完整引文验证、映射和解盲。评价真实回应、正面证明、同尺度比较、适用条件/代价、口语表达与身份贡献，不按长短、关键词、招牌句或固定格式选胜。捏造事实/交锋、翻方、关键主体/否定/例外反转独立阻断，不能用均分或少数好篇掩盖。

开发与留出分别要求相对已发布 V12：正反双方总结各自“改善数−退步数”大于0，四身份无跨题持续性退步；FREE有实际推进且允许自然长短与论证/叙事/类比等变化，不能以格式差异冒充贡献。V9模型对照的得失另列，不能把换 Prompt 的收益全归给模型。每篇的普通可争论价值立场与真正事实阻断区分，标准预先固定，不随输出不断加严或放宽。

候选开发成功后锁定一个固定模型/写作组合，才执行原先从未调用的第3题留出：8篇总结＋4篇FREE的12个候选请求、12个 V12/Flash基线请求，独立匿名评审。原留出内容不在开发阶段展示或用于修改。候选累计36篇全部取得完整 TTS/PCM；留出基线12篇是首次真实调用，不能写成复用。留出若失败，保留全部结果；修改策略须另建新的未见验证边界，不能反复抽取同12条换绿。本规格不授权无限模型/提示词搜索；两候选均失败则报告原受保护条件下尚未找到合格组合，另行提范围。

真实生成保留 public QA 全文 delta、finish_reason、usage、首 token/流停顿/总耗时与实际参数；`length`、未知结束或主错误不能当正常 stop。按实际 delta 连续送原 TTS，取得全文句尾确认、task-finished、完整解码 PCM、调用/关闭证据；完整PCM样本数为时长依据。task-finished/全文ack只证明供应商流事实，不证明物理音素或观众实听完整。

按原10/15/30秒FREE及120/180秒总结分别记录实际PCM与600ms收尾，不以长度较短选胜，不设95%/100%自然结束硬率。少量合法 TIME_LIMIT逐条保留；常规窗口反复未读完、固定总结普遍截停或多数Agent被截停仍不合格。未知的取消后完整上游结果记 UNKNOWN，不续跑补造；字幕/存档正文不能证明音频已读完。

## 5. 模型绑定扩大用途后的必要验证

**85条模型绑定会影响新房中这些 Agent 的全部固定 SPEECH、FREE SPEECH、FREE DECISION，以及含 agent_profile_id 的 Agent 应急占位；不是只影响195槽。** 其他475槽的模板字节不变，模型行为也仍会改变。generic 真人应急、6条 rule default、规则judge和全局JudgeProfile维持原资源，不把应急用途一概称为不变。未来新增Agent仍按原rule default继承，85条现有集合不等于今后所有Agent自动使用新模型。

对入选候选在实际 `_load_config`/render/assemble 路径核全部670槽：195个V9 SPEECH模板、475个原模板及所有变量/输出契约，身份/席位/持方和 typed history不变；只声明新模型绑定差异。四主身份之外的其余实例也必须按完整85条真实清单及UUID全量验证路由，不用四主模型夹具替代其前态核验。670路由检查不等于所有身份语义质量均已抽样。

预注册12个原生 DECISION 兼容请求（四主身份×3轮有界公共历史），使用原 JSON 契约、3秒连接/首 token/无新 token 超时、原128/64 token及一次重试/最终跳过语义，记录真实 attempts、用量、延迟和 JSON/闭流结果；不强制 apply=true，不改真人优先或兜底算法。必须证明候选能在原预算内正常完成这些请求，不以放宽超时或依赖最终失败兜底取得资格。一次重试按原策略分列，不把它当新增随机抽样；若改评第二候选，使用预先固定的同一组输入另行验证，不在运行时切换。

最终组合新跑隔离 normal＋recovery 两场：每队2真人＋2Agent，原全部阶段/主持/发言时长，含原205秒真人测试音频和双方四辩；实际Core、独立PG、owned LiveKit、真实LLM/TTS/ASR及管理/观众DOM。复用原暂停/恢复、重置/恢复、应急安全继续场景，不能缩短场次、改时钟或将旧Spec308两场冒称此次结果。若原恢复场只覆盖真人应急占位，另加一个受控暂停下 Agent 应急占位正常API小场景，验证实际Agent模型解析与清理；该占位不调用TTS、不计正式已播发言或末句覆盖，不修改原两场假称已覆盖。

两场必须保留来源/speech/attempt/generation/epoch/context隔离、所有owner/lease/task/连接清零、无未知capture/闭流错误及原时长/最终host/stage记录。合计四个精确Agent各至少一条合格 ORIGINAL/FINALIZED/COMPLETED、非truncated 的原尾匹配覆盖，逐场分列。原NCC 0.60与最后6帧各0.45门槛、source绑定与aggregate规则不变；TIME_LIMIT/RESET不计完整覆盖。逐条审查全部保留NCC失败，不能用四ID覆盖掩盖新增长段缺尾、严重失配或未知source/owner，也不以ASR相同作OR。所有旧失败原分数不改判。

## 6. 新资源、凭据与历史房间的真实约束

资格评测使用隔离QA资源，不为研究先改生产。只在全部资格通过后，才通过正常 `POST /api/admin/catalog/models` 创建**一个独立新资源**。正常POST自动分配UUID，默认状态应为 ENABLED并强验回读；Create不接受status额外字段，不能伪造payload。与源配置相比，provider model_id是唯一有意改变的执行字段，name/config_ref使用独立普通标记；base_url、capability_schema、并发50、token_per_char1.0及参数`{}`均保真。config_ref只是元数据，不是key_ref、克隆凭据或环境解析能力，不传虚构key_ref。

未来获批的受控工具仅为该源资源在内存解密现有凭据，按完全相同Host和凭据语义传入正常POST的 SecretStr api_key；不得读取 api.md/.env、输出/落盘密钥、密文、nonce、header、last4、秘密哈希/指纹或原始响应。认证和服务环境也只在内存使用 `Settings(_env_file=None)`；不把数据写入普通临时参数文件、shell argv或trace。正常服务既有加密/审计行为不修改，工具只保存白名单非秘密回执。没有获准且验证过的内存注入路径，或同一Host不接受该模型时，不得假装新资源可用。

新房会冻结模型UUID、路由与参数，但现有runtime取凭据仍经 live Agent.model_profile_id。故旧房可能继续使用旧冻结Flash路由/参数，同时从新绑定资源取凭据；**不能声称旧房零影响。** promotion前在内存直接核同Host、同凭据，不将比较值或其哈希写文件；在独立夹具保留真实旧房冻结配置，通过绑定迁移前/后的旧Model有界canary验证实际解析、原参数和可调用性，保留原共享资源。禁止改旧房JSON修复不兼容；生产已有房间只读保护，不对用户比赛插入测试调用。

QA ModelProfile UUID与生产正常POST分配UUID不会预设相等。资格/安装guard必须绑定同一已选model_id、Host、capability、数值、参数、Credential内存等同性及实际POST UUID映射；不能靠替换收据中的UUID假称同一对象，也不能因QA临时UUID不同无条件拒绝真实等价资源。Judge和generic emergency继续解析旧资源。

## 7. 受控发布：正常API最少281次写入

发布前生成审阅包，冻结入选模型、V9策略、195模板、670槽映射、85条绑定前态、依赖/源、Web构件及逐操作回滚。只读刷新活动房间/比赛/租约；有用户房间则等待，不强制结束。确认可核的发布维护窗口及现成新房入口隔离条件；仅停止Web不能证明Core API绝无并发创建，无法控制或观察并发时不得承诺安全批量切换。

| 正常管理操作 | 次数 | 精确变化 |
| --- | ---: | --- |
| POST独立ModelProfile | 1 | 上节指定新资源，不PATCH原共享资源 |
| PATCH `/api/admin/rules/{rule_id}/agents/{agent_id}` | 85 | 完整RuleAgentUpdate中仅model_profile_id有意改变；原generation_params={}及status=ENABLED回传并核对 |
| 正常AgentPromptOverride PUT | 195 | 110总结＋85FREE写入冻结V9正文；其余475模板字节保持 |

最少为 **1＋85＋195＝281次正常配置操作**，外加明确分列的只读、认证、验证与审计；不是86次，也不是“195-only模型不变”。不得PATCH rule basic/default、judge、原共享模型、其他Agent字段、音色、数字校准或历史规则。现有逐项事务不是281次原子提交；规则revision按每次真实API操作正常增加，不预设每规则只加1，不回写旧revision。

每写前后重新核源、catalog/不可变历史、idle、精确前态与预期revision；保存脱敏意图/结果journal并正常回读，允许的审计/配置revision增量明确列出。当前Agent API无原子expected_revision/CAS字段，外部预检不能冒称数据库原子CAS。出现并发编辑、unexpected pending、源/历史漂移或部分写入就停止；请求超时而提交未知时记 UNKNOWN_COMMIT，先只读对账，不自动重试POST/PATCH/PUT或重复建资源。只恢复已证实由本计划成功写入的子集；未能核清状态不继续猜测写入。

每个管理HTTP操作有界≤60秒，owned Session在finally按正常撤销路径≤10秒清理；主错误与各资源cleanup错误分列，不因cleanup覆盖主错或留下pending。此处不是改认证TTL/权限。DB备份仅作私有灾难证据，不输出路径/凭据、不整库回灌。

生产只安装最终同一V4b/Linux03 Web构件，使用已审仓库 `pnpm build` 产物，不在生产裸跑Next构建。只允许Web受控停止/重启；Core/Jobs MainPID、NRestarts、173源/迁移、4Web运行文件、0049、原协议与voice/CPS前后精确核对。归档路径/普通文件/无符号链接/native UID/backup护栏及生产8100检查沿用已审窄发布边界，新281配置范围须新增纯负控与独立工具审阅，不能直接沿用只允许195写的旧发布器。

## 8. 回滚与安装后核验

回滚通过正常API恢复实际写入子集的85绑定前态和195模板前态，以当前revision重新写入并正常递增、逐项回读；Web可独立回滚至此前构件。不回写旧revision、不恢复整库、不删历史、不降schema、不撤销Spec309、不交换音色。

新模型资源即使回滚后也保留：旧/新房冻结JSON、FK或应急/裁判解析可能依赖它。只有额外核实所有在用及历史引用、正常管理预检允许后，才可停用已无依赖的新资源；不把删除ModelProfile作为回滚步骤。原共享资源、规则默认和judge始终保留。

安装后实际观察至少5分钟：公网/服务健康、MainPID/重启、模型与绑定回读、670模板/全部非目标字段、历史与owner/lease，确认无未知错误。用安装后的真实浏览器验证19个既有UI fixture、五音色正常试听至真实ended/duration、28头像库选择/取消/刷新且不保存；这些不得充作新模型内容或物理音箱验收。按实际执行报告完整阶段、ASR质量可评/不可评及TIME_LIMIT，不能把一次闭流当全部内容通过。

50路LLM、五场压力、11音色真人盲听、物理麦克风/会场音箱若未实际完成仍列未验收；并发参数50不代表做过50路测试。既有后端标准门禁只能按源/依赖和适用范围明确复用，新增模型/配置/QA路径须真实验证，不能将旧2437项记作本次新测。

## 9. 批准后执行顺序与当前状态

顺序为：冻结策略及全部候选输入/评分表 → 第一候选开发及独评（失败才进入第二候选）→ 锁定入选组合 → 原生12 DECISION兼容及未见12对验证 → 最终组合两场原时长混合赛与必要Agent应急小场景 → 独立资格审查/新资源与281操作审阅包 → fresh idle/保护核验 → 正常API受控迁移及同一Web安装 → 安装后实际核验。任一阶段失败保存原证据并停止对应promotion，不自动换模型、扩大参数或改核心。

当前只有此待批准文档及既有只读证据；Plus调用、DECISION/留出/混合赛、新资源、85绑定迁移、195模板生产写、安装及安装后验收均未执行。未经新批准不开始实施；Root完成范围审阅后再向用户呈现具体方案。此时不更新 MEMORY.md、specs/README.md或任何已冻结候选。
