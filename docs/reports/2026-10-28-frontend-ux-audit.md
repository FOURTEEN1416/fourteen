# 前端全站体验审计报告（2026-10-28）

> **项目**：unique-you（唯一的你·十四）前端全站（`frontend/`，React 19 + Vite + TS + Tailwind + Zustand，19 页面 / 32 条路由）
> **审计背景**：v2 体验升级六项裁决已定基线（本报告只做补充发现与实施深化，不重复罗列裁决本身）；生产环境已有真实陌生用户在用。
> **审计方式**：六路视角只读静态走查（源码 + e2e 规格逐行阅读，**未启动 dev server、未跑浏览器/e2e/单测**）+ 四路行业调研 + 统一优先级合并。六路 = **四路用户角色旅程**（新用户旅程 / 日常使用 / 自定义配置 / 管理员旅程）+ **两路横切审计维度**（信息架构 / 视觉一致性）——后两路是审计维度而非单一用户角色，特此如实标注（章名「六路角色走查」沿惯例保留）。
> **生产数据来源声明**：报告引用的生产运行数据「近两日 5 个新注册（3 人在真实聊天）」来自**主控审计任务输入的「已核实事实」（2026-10-28 下发）**；原始统计口径（哪张表/哪个查询/什么时间窗）任务材料未附，本报告撰写环节未独立复核生产库。该数字仅作优先级论证的背景输入，正文四处引用（新用户旅程-1、信息架构-8、NEW-3、NEW-16）均指向本声明；实施前如需精确口径，请以生产库实查为准。
> **证据底座**：全部发现引用 `path:line`。**verified（已复核）** = 走查后经第二遍逐行复核确认；**复核独立性声明**：第二遍复核由上游六路走查批次内的复核环节执行（每条 verified 附复核记录），复核者与首次走查者是否同一智能体材料未标注，保守按同流程复核对待；本报告撰写环节（两轮）另做了独立取证——**P0 8 项中 4 项**（NEW-1 工具开关端点 `tools_routes.py:137-142`、NEW-4 扫码死分支 `WeChatPage.tsx:138-237`、NEW-2 logout 调用面、NEW-5② `count_facts` 定义）+ **P1 项 NEW-7 的前端行号**（`RoleSettingsTabs.tsx:820/1178/1180`）+ NEW-25 后端实况（`app_factory.py:269`）+ 2 处行号真值（`SettingsAccount.tsx:207`、`WeChatPage.tsx:190-191/:192`）+ AD-3 日志取证（`misc_routes.py` 与 `logging_setup.py` grep `session_key` 零命中）+ 调研数字复跑（见调研章首）。鉴于项目 v1.56「独立复验 4 项中 3 项打回」的教训（AGENTS v1.56），**建议 P0 其余 4 项（NEW-3/5/6 及各 P0 的实施窗口）在实施前由主控或独立窗口再验一轮**。**unconfirmed（未复核）** = 走查自查标注、尚未复核，实施前须按代码实况复核（精确盘点见实施风险 8）。行号纪律：上游材料个别行号小偏差已就地注明；撰写环节实读定真值的两处已全文统一并注明。
> **最高设计约束（已全程遵守）**：普通用户可自定义 LLM（BYOK）、语音引擎等是核心能力与用户主权——本报告全部 40 项 backlog 均为**增量修复、透明化与组织优化**，无任何收走、隐藏或降级这些能力的条目。

## 内部代号对照（供非当批读者追溯）

| 代号 | 含义与指向 |
|---|---|
| W11 / W11-D1·D4·D5 | 2026-09-27「前端用户任务与声明统一」验证批次及其契约条目，真源 `docs/verification/W11-2026-09-27-前端用户任务与声明统一-验证报告.md`；⚠️ 上游走查曾发现 W11-D4 条目定位失准（见自定义配置-2 复核注），引用其条目时以该报告原文为准 |
| W16 | 上游走查材料引用的批次代号（初始角色提示容错相关）；撰写环节 ls 实证 `frontend/e2e/` 仅五份规格（capabilities/mobile/smoke/w11_journey/w17_selfservice），**无 W16 对应规格文件**，指向需在 `LOG.md` 追溯 |
| W17 | 自服务 e2e 规格批次，对应 `frontend/e2e/w17_selfservice.spec.ts` |
| R7 | v1.56 八项裁决执行批之「R7 模式显式切换·按用户隔离」（per-user reply_mode + admin 用户下拉范式），见 AGENTS §10 v1.56 行 |
| D13 / D14 / D15 / D16 | 用户裁决编号：D13 开放注册主链路+邀请码并存；D14 追问链整体退役；D15 显式身份组；D16 谁扫码谁聊/一人一 bot 一条通道——真源 `docs/DECISION_LEDGER.md` |
| V2-N / NEW-N | 本报告 backlog 编号：V2-1~6 为已裁决 v2 基线六项，NEW-1~34 为本审计新发现 |

---

# 结论摘要

1. 本次审计以六路视角（四路用户角色旅程 + 两路横切审计维度：信息架构/视觉一致性）对前端全站做只读静态走查，共收录 **80 条发现（high 18 / medium 37 / low 25）**，其中 **18 条 verified、62 条 unconfirmed**；六路各设「值得保留的亮点」节（每路 5 条、合计 30 条），证据扎实可在 v2 实施中直接复用。
2. 最重的一类系统性问题是**状态/读数失真**：状态中心「亲密等级」把后端 0-8 等级序数按 0-100 刻度解释（关系到顶也恒显「陌生」，`StatusCenter.tsx:69-72`）、「记忆条目」因 `count_facts` 全仓无定义恒 0、「最近沉淀」卡因行键错位恒空、主动消息统计读 `h.type/h.at` 而后端返回 `kind/created_at`（统计卡全死、时间列 Invalid Date）。
3. 第二类是**危险操作无防护**：「整理记忆」按钮在会话键留空时等于对全部会话执行记忆改写且无二次确认（`RoleSettingsTabs.tsx:833-841`；**该按钮在生产是否已被点过属追溯性取证，见「生产取证待办」**）；工具仪表盘启停开关对普通用户必 403 且失败被空 catch 静默吞掉（`ToolsDashboard.tsx:171-186`，双重 verified）。
4. 第三类是**首日旅程断裂**（三条均 verified）：控制台没有任何「退出登录」入口；介绍页自称「邀请码制」而代码实况是开放注册（文案与行为相悖，偏离 D13 语义，误导首日漏斗）；扫码弹窗 error 分支是不可达死代码，微信平台故障时新用户只见无限「获取二维码中…」骨架。
5. 管理侧「拟人化按用户覆盖」要求手输 `N:wxid@im.wechat` 内部会话键，产品内**没有便捷、可发现的查键界面**（verified；日志取证路径经撰写环节取证亦未证实可行，故非「可绕行但麻烦」），该管理流实际受阻；话题状态侧后端支持数字 user_id 还原可绕行但重度不便——同 tab 的回复模式作用域已有「用户下拉+数字 id 还原」的 R7 范本可直接复制。
6. 亮点域同样突出（对应正文六路「值得保留的亮点」节共 30 条）：保存回执诚实化（以业务回执判真、不谎报成功）、导航单一真源双端一致、W11 工程纪律（读失败显式错误态+重试、非 admin 不渲染必 403 的假表单）、BYOK 用户主权在教学与边界说明上被显式保全。
7. 四路调研给出可执行选型：教学导引引 **driver.js**（MIT、2026-10 仍活跃，排除 AGPL 双许可的 shepherd.js/intro.js）+ PostHog 式自建 checklist；公告**自建不引库**（Open WebUI 字段模型 + Mastodon 时间窗 + 落库已读水位）；渐进式披露与全局运维视图照 **GitLab Admin Mode 五件套 + LobeChat 分组**——关键选型数字已于撰写环节复跑留痕并附数字容错说明（见调研章首）。
8. 统一 backlog 共 **40 项（v2 基线 6 项 + 新发现 34 项）**：P0 8 / P1 17 / P2 15，Effort 构成 S 20 / M 17 / L 3；定级映射规则（high 与 medium 两档全覆盖）、18 条 high 的逐条落级对照与 V2 基线优先级来源见 backlog 章首「定级规则（供复现）」——评级可从报告内证据复现。
9. 主要实施风险：`RoleSettingsTabs.tsx` 被 **8 项**命中（V2-3/NEW-6/NEW-7/NEW-19/NEW-24/NEW-26/NEW-32/NEW-34）、`navGroups.tsx` 被 6 项命中，须按 AGENTS §8 owner 唯一制合并批次实施；文案/结构改动会破坏既有 e2e 与组件测试锚点，须同批更新并跑前端 251 全量 + 后端分块基线——建议的六批实施顺序（含各批 Effort 构成）见「建议实施批次与顺序」节。
10. **7 个开放问题需主控裁决**，每条已附推荐方向与理由（见开放问题节）；**三个真实用户视角**（多角色切换用户、隐私与数据主权、移动端独立旅程）与**会话过期/被强制登出用户旅程**本次未走查、**三项生产追溯取证**待下一轮（见「生产取证待办」与未覆盖范围）。

---

## 六路角色走查

> 六路走查的严重度口径为三档：**high**（误导/阻断）、**medium**（显著摩擦/降级）、**low**（抛光）。复核状态标注每条之后；「已复核」表示第二遍逐行核实成立（个别行号小偏差已就地注明，不影响结论），「未复核」表示走查自查标注、实施前须按代码实况复核。六路中「信息架构」「视觉一致性」两路为横切审计维度，其余四路为用户角色旅程。

### 路一 · 信息架构（9 条：high 2 / medium 5 / low 2；verified 2）

**走查范围**：全站导航与信息组织只读走查——App.tsx 路由表、Sidebar/MobileDrawer/Breadcrumb 布局三件、19 页面页头命名、settings/admin 权限边界、死链与孤儿功能扫描，并以 `frontend/e2e` 五份规格对照。

#### 发现

**【信息架构-0】非活跃角色无任何设置入口 —— high · 已复核**
- 位置：`frontend/src/pages/RolesPage.tsx:12-67`、`frontend/src/components/layout/navGroups.tsx:32-39`
- 问题：角色卡片唯一交互是「设为活跃」按钮（`RolesPage.tsx:47-64` 活跃/非活跃两分支均无任何 navigate/Link）；「角色设置/状态中心/剧情线」侧栏入口仅在 activeRoleId 存在时渲染（`navGroups.tsx:32-39` 条件展开四入口且指向活跃角色）。想编辑非活跃角色的设定或剧情线只能先激活——而激活是切换聊天对象的全局副作用（经 `api/routers/character_routes.py:778-916` 证实：user_active_characters + 微信绑定 + 扫码人绑定 + 会话热切）。RoleSettings 页头明确支持渲染「未激活」状态（`RoleSettings.tsx:70-73`），证明设计预期可编辑非活跃角色，但入口链没接上，URL 直达（`App.tsx:147-150`）之外 UI 全断。全量 grep 前端 `roles/${` 动态导航仅侧栏（限活跃）/面包屑页内轨迹/CreateRole 新建后跳转三处。
- 建议：给 RoleCard 增加指向 `/roles/:id/settings` 的次要入口（非活跃卡也可进，且不改变活跃态），或在激活弹层中说明「切换后原角色仍可从侧栏返回编辑」。

**【信息架构-1】工具开关对普通用户渲染必失败 —— high · 已复核**
- 位置：`frontend/src/pages/ToolsDashboard.tsx:171-186`
- 问题：「工具仪表盘」对所有登录用户渲染每个工具的启停 toggle，无 isAdmin 判断（全文件未读 authStore，`App.tsx:160` 该页仅在 AuthGuard 登录守卫下、无 RoleGuard）；但后端 `POST /api/tools/{name}/toggle` 是 `require_role("admin")`（`api/routers/tools_routes.py:137-142`，`auth_jwt.py:372-376` 非 admin 403），普通用户点击必 403。更糟的是 handleToggle 的 catch 块为空（`ToolsDashboard.tsx:103-104` 注释「revert on error is implicit」），失败无任何 toast，用户只看到开关弹回。该开关是全局的（写穿 tool_state，`tools_routes.py:14-17`），却放在无 admin 条件的「系统设置」组（`navGroups.tsx:48`）。同族问题在安全页已修并留注释：`SettingsSecurity.tsx:8-10` + `:59`/`:196` 的 isAdmin 门——工具页是同一 W11-D1 契约批次里漏网的一页（代号见「内部代号对照」）。
- 建议：照 SettingsSecurity 先例给开关加 isAdmin 门（普通用户只读状态与健康提示），toggle 失败补 error toast；**不必收走工具页本身**——查看工具可用性对 BYOK 用户排障有价值。

**【信息架构-2】状态/画像页忽略 URL 角色参数 —— medium · 未复核**
- 位置：`frontend/src/pages/StatusCenter.tsx:51-58`
- 问题：路由 `/roles/:roleId/status` 与 `/roles/:roleId/psych` 带 roleId 参数，但 StatusCenter 与 PsychProfilePage 均不读 useParams，数据一律取 useActiveCharacter()（`StatusCenter.tsx:51`、`PsychProfilePage.tsx:101`）；同族的 StorylinePage 却用 useParams 解析角色（`App.tsx:59-62`）——同一路由族三种语义。Breadcrumb 又按 URL 的 roleId 显示角色名（`Breadcrumb.tsx:69-85`），访问 `/roles/A/status`（A 非活跃）时面包屑写 A、页面渲染活跃角色 B 的情绪/亲密度/记忆，标签与数据错位。触发场景：用户在角色 A 活跃时收藏深链，切换到 B 后再打开书签；`capabilities.spec.ts:91` 直接 goto 该路径，规格未覆盖 URL 与内容一致性。
- 建议：三页统一语义——要么全部改按 URL roleId 取数（推荐，与 StorylinePage 一致），要么路由去掉 `:roleId` 段并让面包屑按活跃角色命名。

**【信息架构-3】面包屑三个死 tab 映射残留 —— medium · 未复核**
- 位置：`frontend/src/components/layout/Breadcrumb.tsx:27-35`
- 问题：settings tabLabels 含 general「通用设置」与 extensions「扩展管理」，admin 映射含 logs「日志审计」，但 App.tsx 路由表只有 settings 的 llm/voice/tools/security/account/logs 六项（`App.tsx:158-163`）与 admin 的 users/providers 两项（`App.tsx:168-170`）。这三个映射是路由删除后的孤儿：直接访问对应路径落到 ProtectedLayout 之外的 NotFoundPage（`App.tsx:175`）；grep `settings/general|settings/extensions|admin/logs` 全 src 零命中（仅 Breadcrumb.tsx 自身）。
- 建议：随 v2 信息架构批一并清掉三行死映射；若「扩展管理」有回归计划应在路由表恢复而非留映射（见开放问题 7）。

**【信息架构-4】菜单与页面命名三处不一致 —— medium · 未复核**
- 位置：`frontend/src/components/layout/navGroups.tsx:25-47`
- 问题：① 微信页三种叫法：分组「连接」（`navGroups.tsx:24`）、菜单项「微信连接」（`:25`）、页面 h1「微信接入」（`WeChatPage.tsx:366`）；② 语音页菜单叫「语音引擎」（`navGroups.tsx:47`），页面 h2 却是「语音工作台」（`SettingsVoice.tsx:558`），且角色语音 tab 的引导文案让用户去「系统设置 → 语音工作台」（`RoleSettingsTabs.tsx:515,522`）——按菜单名找不到文案所指；③ NotFoundPage 按钮文案「返回仪表盘」（`NotFoundPage.tsx:11`），产品并无仪表盘页（RootRedirect 落 /wechat，`App.tsx:89-92`）。
- 建议：定名三件套（菜单/页头/交叉引用文案）各收敛为一个词；「返回仪表盘」改「返回首页」或直接指 /wechat。

**【信息架构-5】普通用户日志页无口径说明 —— medium · 未复核**
- 位置：`frontend/src/components/layout/navGroups.tsx:50`
- 问题：菜单项「日志」对普通用户与 admin 显示同名，但后端语义不同：普通用户只返回带其 user_id 的请求级日志（`api/routers/misc_routes.py:347-368` 经 UserContextFilter 注入，`observability/logging_setup.py:73-95` 过滤），调度器/供应商等后台日志不可见——隐私上正确，但前端页面无「仅显示与你相关的日志」说明（`SettingsLogs.tsx:57-64` 只透传 limit 拉取），空结果与「日志功能坏了」在 UI 上无法区分，新用户大概率理解为自己数据丢了。
- 建议：作为裁决 3「每项一句话说明」的具体落点：日志项副标写「与你的账号相关的系统日志记录」；空态给一句「暂无与你相关的日志」（两处均用「你」，对齐全站文案纪律——见视觉一致性亮点「您/你」grep 实证）。

**【信息架构-6】训练进度五端点路径前端零消费 —— medium · 未复核**
- 位置：`frontend/src/api/training.ts:4-26`
- 问题：**五个函数对应五个端点路径**（撰写环节实读 `training.ts` 全文对账：GET `/training/status`、GET `/training/progress`、POST `/training/clean`、POST `/training/test`、POST `/training/apply`）与 useTrainingProgress hook（`hooks/useQueries.ts:219-229`，含 extracting/cleaning/training 轮询逻辑）在 pages/components 全量 grep 零 UI 消费——克隆训练的「上传→训练→应用」后半段在控制台没有任何页面呈现；用户只能在 CreateRole 克隆好友 tab 完成上传（`CreateRole.tsx:7,228`）与分析预览，之后训练发生了什么不可见。
- 建议：见 NEW-25（含撰写环节补验的后端实况与二选一方向，需裁决）。

**【信息架构-7】系统设置组名对用户过重 —— low · 未复核**
- 位置：`frontend/src/components/layout/navGroups.tsx:43-51`
- 问题：「系统设置」组 6 项中，账号与数据/安全/日志实为「我的数据与记录」，LLM 配置/语音引擎实为「我的个性化配置」（BYOK 只影响自己，对照 `SettingsLLM.tsx:226` 个人供应商清单 vs `AdminProvidersPage.tsx:159-161` 平台级两个 owner 不同），仅工具是真系统级（且写入 admin-only）——分组名让新用户误以为改这里的 Key 会影响别人或不敢动手，与 BYOK 是核心卖点相悖。
- 建议：按裁决 3 落分组时把 LLM/语音归入「个性化」，account/logs 归「我的数据」，工具仪表盘留「高级」并标注管理员。

**【信息架构-8】角色子页入口无解锁引导 —— low · 未复核**
- 位置：`frontend/src/components/layout/navGroups.tsx:32-39`
- 问题：状态中心/心理画像/剧情线（产品差异化核心）在用户尚无活跃角色时整组隐藏，侧栏无任何「激活角色后解锁」提示——渐进披露本身正确，但披露条件对用户不可见，新注册用户（生产近两日 5 人，来源见头部生产数据声明）不知道这些功能存在。RolesPage 空态只有「创建角色」按钮（`RolesPage.tsx:126-135`），未提及激活后侧栏会长出什么。
- 建议：裁决 4 教学导引覆盖全部模块时，在无活跃角色用户的首页（/wechat 或 /roles）加一行「激活角色后可查看状态中心、心理画像与剧情线」。

#### 值得保留的亮点（5 条）

- 桌面侧栏与移动抽屉共用 `buildGlobalNavGroups` 单一真源（`navGroups.tsx:20-21`、`Sidebar.tsx:16`、`MobileDrawer.tsx:20`），并过滤空分组防止渲染「只有标题没有入口」的组（`navGroups.tsx:64-66`）——导航入口双端永不漂移。
- admin 边界是三层防线而非一层：菜单条件渲染（`navGroups.tsx:53-61`）+ RoleGuard 同时校验 is_active 与角色（`RoleGuard.tsx:45`）+ 后端 require_role 兜底，且 e2e 有行为级契约锁定「viewer 直调 admin-only 安全开关 → 403」（`w11_journey.spec.ts:126`）。
- 角色设置 tab 以 URL 为唯一真源、深链可分享、非法 tab 回退 basic（`RoleSettings.tsx:20-22`），`w11_journey.spec.ts:167/188` 有刷新与写回规格——全站信息架构里最扎实的一段。
- IntroPage 五步上手导引把 BYOK 放第 2 步并标「关键」徽章（`IntroPage.tsx:178-183`），邀请码获取 QQ 2053769154 已在门面页露出（`IntroPage.tsx:150-153`）——裁决 4/5 的基础已在，且用户主权（自带 Key 成本可控）被显式强调。
- 空态诚实且有替代路径：模板空态引导去创建角色（`TemplatesPage.tsx:138-144`）、微信断连态给扫码行动指引卡（`WeChatPage.tsx:113-124`）、账号清单加载失败明确报错「不拿 0 冒充」（`SettingsAccount.tsx:103-107`）。

---

### 路二 · 新用户旅程（14 条：high 3 / medium 5 / low 6；verified 3）

**走查范围**：以「刚注册第一天用户」视角按 `frontend/src/App.tsx` 路由走查：登录/注册 → 落地 /wechat → IntroPage 导引 → 微信扫码绑定 → 角色/模板选择激活 → 首条消息。只读审计（未启动 dev server/浏览器/测试，全部结论出自源码与 e2e 规格静态阅读）。已确认控制台无聊天界面（前端零 WebSocket/chat 页引用），首条消息只能在微信端发出；生产运行配置（如 byok_required 实际取值）无法从仓库确证处均已标注「推断」。e2e 参照：注册/登录/模板流程有规格（w11/w17/smoke/mobile），微信绑定 QR 流程无任何 e2e 规格（见发现 13）。

#### 发现

**【新用户旅程-0】控制台无退出登录入口 —— high · 已复核**
- 位置：`frontend/e2e/w11_journey.spec.ts:295`（e2e 自述「产品现状：控制台没有登出入口」）
- 问题：登录后全控制台（侧栏/顶栏/账号页）没有任何「退出登录」按钮；唯一登出路径是协议门的「不同意并退出登录」和注销账号时的附带登出。复核实证：全 frontend/src UI 调 logout 仅 `ConsentGate.tsx:39`（按钮文本在 :89）与 `SettingsAccount.tsx:207`（注销流程内；撰写环节实读定真值：`:206` 为注释行、`:207` 为 `await logout()` 调用行），Sidebar/Breadcrumb/MobileDrawer 三布局件与 App.tsx 32 条路由均无登出入口或 /logout，其余清态（`client.ts:176`/`AuthInit.tsx:39`）皆为被动非用户入口。生产已有真实陌生用户，共用电脑或换号场景无法自助退出会话。
- 建议：顶栏加用户菜单（身份展示+退出登录），纯增量不涉能力收缴。

**【新用户旅程-1】介绍页「邀请码制」与开放注册矛盾 —— high · 已复核**
- 位置：`frontend/src/pages/IntroPage.tsx:141`
- 问题：介绍页称「注册采用邀请码制」并引导先联系 QQ 取码（`IntroPage.tsx:141-151`），但代码层注册无条件开放——`/api/auth/register` 无邀请码校验（`api/routers/auth_routes.py:162-241`，RegisterRequest :77-81 仅四字段），grep `allow_register|open_registration|registration_enabled` 全仓 0 命中；且 `LoginPage.tsx:51-53` 证实注册页邀请码为可选勾选（不勾走普通注册），`invite_routes.py:120` 为独立并存端点——**代码实况正是 D13「开放注册主链路+邀请码并存」，是介绍页文案与行为相悖，不是代码违背 D13**。第一天用户可能被误导离开注册页去取码，造成首日漏斗损失（生产近两日 5 个新注册正走此路径，来源见头部生产数据声明；**这 5 人是否实际被误导属追溯性取证，见「生产取证待办」**）。
- 建议：文案对齐 D13 实况（可直接注册，邀请码为可选渠道），QQ 联系方式保留但改挂「反馈/人工协助」；是否保留邀请码作为注册门槛属另一方向，见开放问题 1（附推荐）。

**【新用户旅程-2】扫码弹窗失败态永不出现 —— high · 已复核**
- 位置：`frontend/src/pages/WeChatPage.tsx:319`
- 问题：二维码弹窗的 error 分支是死代码——复核 grep 实证 setStatus 全部调用仅 :142/:164/:170/:189/:193/:219（初值 loading + connected/scanned/waiting/expired/loading），全文件无 `setStatus('error')`；三处轮询/连接 catch 均空吞错误（`:150-156`/`:172-174`/`:196-198`），error 分支 `:319-331` 不可达。后端/微信平台故障时首日用户第一步只会看到无限「获取二维码中…」骨架（loading 态无重试按钮仅有关闭 X，只能关弹窗重开）。项目 L2 教训明确「微信接口易变」，此路径在生产大概率会遇到。
- 建议：轮询连续 N 次失败置 error 并给重试按钮，loading 态也提供重试入口。

**【新用户旅程-3】BYOK 配错反馈环三处断裂 —— medium · 未复核**
- 位置：`frontend/src/pages/SettingsLLM.tsx:133`
- 问题：① 保存 API Key 无连通性校验也无「测试连接」按钮，错 Key 也显示「设置已保存」（handleSave `:133-168` 仅提交无校验；后端保存只做大小/嵌套钳制不验证 Key，`api/routers/misc_routes.py:583-586`），错误延迟到聊天时才爆；② 检测到 BYOK 403 会自动跳 `/settings/llm?byok=1`（`client.ts:123-135`），但全前端无人读取 byok 参数，用户莫名落在设置页无解释；③ byok_required 开启时微信端首条消息失败只收到角色化兜底句（`wechat_direct/wechat_connector.py:2122-2138` 走 get_fallback_line），BYOK 中文指引仅存在于 HTTP 403 detail（`api/byok.py:75-79`）。前端无聊天界面，BYOK 错误对终端用户几乎只在微信端显形。推断：生产 byok_required 取值仓库不可证（本地 `config/system.yaml:6` 为 false），若已开启则③成立——该取值列入「生产取证待办」。
- 建议：保存页加可选「测试连接」；读 `?byok=1` 显示解释横幅；BYOK 类错误在微信端改用带指引的专用兜底文案。

**【新用户旅程-4】二维码过期后界面不刷新 —— medium · 未复核**
- 位置：`frontend/src/pages/WeChatPage.tsx:190-191`
- 问题：拿到二维码后轮询即切换为连接状态轮询，不再检查 is_expired（startQrPolling 拿到 qr_image 即 clearInterval 并切入 startConnectionPolling，撰写环节实读定真值为 `:190-191`；startConnectionPolling 定义 `:158`，只识别 connected/scanned `:159-175`）；is_expired+12s 门槛仅存在于初次拉码轮询（`:192`）。用户延误扫码时失效旧码一直显示为「等待扫描」，在微信端扫码失败且无任何提示，须自己想到点「刷新二维码」。
- 建议：连接轮询内同步检查 is_expired → 自动置 expired 态并重取。

**【新用户旅程-5】登录注册错误英文直出 —— medium · 未复核**
- 位置：`frontend/src/pages/LoginPage.tsx:67`
- 问题：密码错误/账号停用/邮箱用户名冲突时，后端英文 detail 原样显示在错误框给中文用户（`LoginPage.tsx:64-69` 直接渲染 err.response.data.detail；`api/routers/auth_routes.py:265/270` "Invalid login credentials"、`:274` "Account is disabled"、`:190` "Email or username already in use"），是产品的第一屏错误态。注：密码强度已由前端中文预检拦截（`LoginPage.tsx:46-50` + `utils/passwordPolicy.ts`），英文面集中在 401/403/409。
- 建议：前端按状态码映射中文文案（保留安全模糊表述，不泄露「用户不存在 vs 密码错」的枚举差异）。

**【新用户旅程-6】初始角色仅 4 秒 toast 告知 —— medium · 未复核**
- 位置：`frontend/src/hooks/useAuth.ts:55`
- 问题：注册自动分发并激活的初始角色（后端已克隆+绑定激活）只在 4 秒 toast 里提示一次（`useAuth.ts:52-59`，errorStore.ts:26 默认 duration 4000ms），且与全屏协议门同帧出现（注册响应 needs_consent=true `auth_routes.py:239` → ConsentGate 全屏，Toast 与 Gate 同 z-[100]），注意力必被协议占据；此后控制台无任何持久位置告知「你已有角色 X 且已激活」，与 IntroPage 五步教程第 3 步仍教「创建或选择角色」脱节（`IntroPage.tsx:179` 未提注册已自动发卡）。
- 建议：角色页给初始卡「已为你准备好」角标，或将该事实纳入 v2 教学导引首页。

**【新用户旅程-7】角色分发失败全程静默无引导 —— medium · 未复核**
- 位置：`api/routers/character_template_routes.py:204`
- 问题：注册分发失败（总开关关/无可用模板/写盘失败）按契约降级 initial_character=null（`:211-241` 失败路径一律 return None），前端无名即不提示（`useAuth.ts:53-54`）——用户不知道自己没有已激活角色：角色页无「当前活跃」卡（无 user_active_characters 行时 overlay 不渲染，`character_routes.py:541-543`），微信端角色解析落 "default"（`user_scheduler.py:396-400`），首条消息可能聊到非所选人设。推断：default 卡缺失时的最终表现未逐层追完。
- 建议：initial_character=null 时前端给一次性引导（角色页高亮「先设为活跃」），不阻断注册。

**【新用户旅程-8】连接成功缺下一步行动指引 —— low · 未复核**
- 位置：`frontend/src/pages/WeChatPage.tsx:289`
- 问题：扫码成功态只有「连接成功/微信账号已接入/完成」三行文案+完成按钮（`:289-302`），没有「现在去微信给角色发第一条消息」的接力；「连接后怎么用」为静态双列说明卡（`:391-407`），成功弹窗与说明卡无联动。为已裁决 v2 第 2 项（微信页显示当前聊天角色）的相邻实施细节。
- 建议：成功态加一句行动指令（去微信发第一条消息试试）。

**【新用户旅程-9】已连接后无断开换绑入口 —— low · 未复核**
- 位置：`frontend/src/api/system.ts:194`
- 问题：已连接用户在界面上无法断开或换绑微信；前端已封装 disconnect/reconnect API（`system.ts:192/194`）但 pages/components 全量 grep 零消费，后端断开端点存在（`api/routers/wechat_channel_routes.py:203`）——绑错微信号的陌生用户无自助路径。
- 建议：已连接态提供「断开连接」入口（带确认）；涉 D16「一人一 bot 一条通道」语义边界需裁决（见开放问题 5，附推荐）。

**【新用户旅程-10】教学内容仅公开页可达 —— low · 未复核**
- 位置：`frontend/src/pages/IntroPage.tsx:168`
- 问题：五步教程+免费 API Key 申请引导全部挂在公开 /intro 页（`:168-237`），登录后控制台无任何入口（唯一链是登录页角落「了解产品」`LoginPage.tsx:232-236`；`navGroups.tsx:21-67` 全导航表无 /intro 项）；老用户与已注册用户无法回看。为已裁决 v2 第 4 项（完整功能教学导引）补充实施细节：教学除弹窗外应把这份现成内容收进控制台帮助位。
- 建议：在控制台（如侧栏分组或帮助入口）复用该教学内容。

**【新用户旅程-11】角色空态缺模板页入口 —— low · 未复核**
- 位置：`frontend/src/pages/RolesPage.tsx:126`
- 问题：角色列表为空时只给「创建角色」一条路（`:125-135`），不指向现成的「角色模板」（入口仅侧栏 `navGroups.tsx:31`）；公共池可见性见 `character_routes.py:72-85`。实机上因注册分发+公共池（无主卡全员可见）空态罕见，主要发生在空卡库/CI 场景。
- 建议：空态并列「从模板挑一个」次按钮。

**【新用户旅程-12】无激活时导航静默回退首卡 —— low · 未复核**
- 位置：`frontend/src/hooks/useQueries.ts:131`
- 问题：useActiveCharacter 在无激活角色时静默回退 characters[0]（`:131-135` `active ?? characters?.[0] ?? null`，可能是一张公共池卡），侧栏「角色设置/状态中心/剧情线/心理画像」入口随之指向并非用户激活的卡，叠加「当前活跃」徽章缺失画面易误导。推断：主要在分发失败/绑定行缺失时出现（与发现 7 同根）。
- 建议：回退场景不展示角色子页入口，或显式标「未激活」。

**【新用户旅程-13】微信绑定流程无 e2e 规格 —— low · 未复核**
- 位置：`frontend/e2e`（目录级缺口）
- 问题：注册/登录/模板/自服务/移动端均有 e2e 规格，但扫码绑定（QR 弹窗全流程 loading/waiting/scanned/connected/expired）无任何用例——grep `frontend/e2e/*.spec.ts`「扫码|qr|Qr」0 命中（仅登录成功断言跳转 /wechat，见 `smoke.spec.ts:23`、`mobile.spec.ts:26`、`capabilities.spec.ts:28`）；首日旅程最关键一环仅靠人工验证。此结论基于规格文件静态检视，非运行结果。
- 建议：补 QR 弹窗状态机 e2e（page.route mock 后端三端点即可覆盖全状态，含本批发现的 error 死分支回归）。

#### 值得保留的亮点（5 条）

- 注册即分发并激活初始角色，且失败不阻断注册、绑定失败回收孤儿卡（`api/routers/character_template_routes.py:197-242` + `auth_routes.py:229-241`）——第一天用户落地 /wechat 时名下已有可聊对象，冷启动零步。
- 微信页对未绑定用户的空态是行动引导卡而非报错，后端对无通道用户兜底返回 idle 而非错误（`WeChatPage.tsx:113-130` + `wechat_direct/connector_registry.py:215-231`）；last_activity 秒/毫秒启发式+年份守卫把 1970 显示类缺陷根治在展示层（`WeChatPage.tsx:94-111`）。
- 业务回执纪律贯穿首日旅程：模板克隆只认 status=created、激活只认 activated，HTTP 2xx 但回执不对即告警不假成功（`TemplatesPage.tsx:86-98`、`RolesPage.tsx:95-99`），并有 w17 全真实 API e2e 背书（代号见「内部代号对照」）。
- initial_character 提示按「字段存在且有可用名称才提示」容错，W16 未接线时静默而不是拿默认角色名编造（`useAuth.ts:45-59`；W16 为上游材料引用的批次代号，frontend/e2e 无对应规格文件，见「内部代号对照」），e2e 双分支断言跟着后端实况走（`w17_selfservice.spec.ts:278-306`）。
- 用户主权与教学双保全：IntroPage 免费 Key 申请引导具体到平台、额度与避坑（`IntroPage.tsx:198-236`），设置页每个供应商内嵌实时申请教程（`SettingsLLM.tsx:227-234/272-279`），BYOK 403 自动跳转设置页（`client.ts:123-135`）——符合「只能优化组织与教学、不得收走 BYOK」的最高约束，本批全部建议均为增量引导而非能力削减。

---

### 路三 · 日常使用（15 条：high 4 / medium 7 / low 4；verified 4）

**走查范围**：以「已绑定、每天聊天的普通用户」视角走查 WeChatPage / StatusCenter / RoleSettingsTabs(消息+拟人化) / PsychProfilePage / SettingsVoice 五区域的数据可理解性，前端全文走读+后端数据源逐条下钻验证，只读零改动。

#### 发现

**【日常使用-0】「亲密等级」跨刻度错位，普通用户永远显示「陌生」 —— high · 已复核**
- 位置：`frontend/src/pages/StatusCenter.tsx:69-72`
- 问题：状态中心顶部「亲密等级」卡把 dashboard 返回的 affinity 按 0-100 阶段区间解释，但后端该值是 EmotionEngine 的 0-8 等级序数——即使关系已到最高级也恒显示「陌生」。复核全链成立：dashboard 端点取 `orch.components["emotion"]` 的 state.affinity（`api/routers/misc_routes.py:162-171`；发现原文写 :186-190 有约 24 行偏移，不影响结论），该值在 `emotion_engine.py:287` 钳制为 0-8 整数（`:228-238` 九档 LEVELS），而前端经 `getAffinityLabel` 按 `constants/persona.ts:87-92` 与 shisi stage_config 同款 0-100 四档区间解释，≤8 恒落「陌生」。后端 health_check 已返回现成的 affinity_name（`emotion_engine.py:911`）未被消费；测试 mock affinity=85 钉「羁绊」（`src/tests/components/StatusCenter.test.tsx:47`），mock 刻度与生产返回刻度不一致、测试绿掩盖线上问题。
- 建议：数据源改用 0-100 刻度（shisi emotion_stage）或直接消费 health_check 的 affinity_name，前端测试同步钉生产刻度。

**【日常使用-1】角色「数据概览」三卡是死数据：消息/记忆恒 0 —— high · 已复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:1292-1294`
- 问题：「数据」tab 的消息总数/记忆条数/平均响应三卡读 `character.stats`，但后端 GET /characters/{id}（`character_routes.py:565-603`）只返回卡文件字段+voice_config+激活态，整文件 grep stats 零命中；实测 python 扫描 `config/characters/` 42 张卡：含 stats 字段的 0 张；`types/api.ts:504-517` UnifiedCharacter 类型也无 stats（仅 `types/framework.ts:108-113` 声明了可选 stats）。用户聊几百条后永远看到 0/0/—。同文件 `:1027` 消息 tab 的「消息总数」同样恒 0。
- 建议：要么接真实统计源（chat_history 按会话/角色计数），要么删掉三卡别用占位数字误导（二选一需裁决，见开放问题 2）。

**【日常使用-2】「记忆条目」恒 0，与右侧「长期记忆」真实数同屏矛盾 —— high · 已复核**
- 位置：`frontend/src/pages/StatusCenter.tsx:73`
- 问题：顶部「记忆条目」卡依赖后端 count_facts()，但该方法全仓无定义：`misc_routes.py:181-182` 以 `hasattr(structured, "count_facts")` 守卫调用，而全仓 grep `def count_facts` 仅命中该调用处与测试桩（`tests/test_sec_p0_read_gates.py:154`），运行时 hasattr 恒 False → facts_count 恒 0 → /api/stats/dashboard 的 recent_memories 恒 0（`misc_routes.py:203/210`）。同一屏右侧「③长期记忆」卡走 /characters/{id}/memory/facts 显示真实 len(rows)（`StatusCenter.tsx:259-267`；`character_routes.py:1279`）——用户会以为记忆没存上。（复核注：发现称 structured_memory.py「只有 count_chats_today」不精确，实还有 count_owner_rows :2037，不影响结论。）
- 建议：后端补 count_facts（或改用既有事实计数真源，须保持 per-user 隔离口径），顶部卡与第③层口径对齐。

**【日常使用-3】「情景归档」时间线日期显示为内部会话键串 —— high · 已复核**
- 位置：`frontend/src/pages/StatusCenter.tsx:384`
- 问题：普通用户展开记忆第②层看到的每篇日记「日期」是 `session_id|character_id|YYYY-MM-DD` 完整内部键，不是日期。复核三证据点实读核实：`memory_pipeline.py:1016` 落库 `diary_key = f"{sid}|{cid or 'unknown'}|{date_str}"`（发现引 :1014 差 2 行内容一致）；`misc_routes.py:319` entries[].date 直接取完整键（rsplit 仅用于排序 lambda :315）；`StatusCenter.tsx:384` `<p>{e.date}</p>` 无任何截取直渲，普通登录用户经 /api/memory/diary 可达。**与已裁决 V2-6 第三子项「情绪归档键名可读化」指向同一现象，实施口径的冲突消解见 NEW-5③ 标注**。
- 建议：端点侧 rsplit 后只回纯日期（内部键不外泄），或前端截取尾段。

**【日常使用-4】普通用户的「消息」「拟人度」设置实际为空白告知页 —— medium · 未复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:845-856`
- 问题：生产已有真实陌生用户（非 admin）打开「消息」tab 只看到两段静态文字（非 admin 分支仅渲染提示文案 `:845-856`，且不请求任何接口 `:760-764`）——无法控制自己收到的主动消息频率、免打扰时段，也不能暂停；「拟人度」区同样只读（`HumanizeSection.tsx:95-103`）。后端 /api/proactive/* 一律 require_role("admin")（`training_routes.py:950`）。后端已存在按用户覆盖机制（user_humanize，`HumanizeSection.tsx:418-427` 即其管理面）可作 per-user 化的参考。推断：这是 W11-D4 有意收敛（全局配置不放假表单；代号见「内部代号对照」），但普通用户侧目前是「零可用」。
- 建议：不违背设计哲学的前提下，把免打扰/频率做成 per-user 自助项（复用 user 作用域机制），最小实现也可先给「个人免打扰时段」一个真开关；该方向是新功能立项需裁决（见开放问题 4，附推荐）。

**【日常使用-5】admin 消息统计字段错位：今日主动恒 0、时间列 Invalid Date —— medium · 未复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:820-821`
- 问题：「今日主动/最后发送/最近主动消息」读 h.type/h.at，后端实际返回 kind/created_at（`training_routes.py:961-970`）——统计卡全死（`:820` todayCount 匹配 `(h.at||'').startsWith(...)` 恒空、`:821` lastAt 恒 '—'），列表时间列渲染 Invalid Date（`:1180`）。与**【管理员旅程-0】**同根（该现象的 verified 版本）：**已裁决的「Invalid Date 两处」小修若只改格式化不修字段契约，统计仍是死的**。
- 建议：前端契约对齐 kind/created_at（含类型定义），今日计数改本地日界（详见 NEW-7）。

**【日常使用-6】心理画像「档位 low·证据强度 0」英文原文直出 —— medium · 未复核**
- 位置：`frontend/src/pages/PsychProfilePage.tsx:91`
- 问题：情绪信号卡直接渲染后端英文 level 原文（low/moderate/high/critical）与无解释的 evidence 数值，普通用户看不懂；同文件 `:72-77` RiskBadge 已有中文映射（低/中/高/极高）却未复用；`:87` total_score.toFixed(2) 亦无一句话解释。
- 建议：复用 RiskBadge 的中文映射，evidence/命中维度加一行白话说明。

**【日常使用-7】心理画像「当前显示」直接暴露内部 scope 键 —— medium · 未复核**
- 位置：`frontend/src/pages/PsychProfilePage.tsx:200`
- 问题：页头说「角色 X 对你的理解」，正文却原样渲染 `{character_id}:{session_id}` 内部键串（profile.scope 由 `persona_extractor/fusion.py:29-38` 构造、profile_summary 原样回显 `:361`），两处信息互相打架。
- 建议：展示用「角色名 × 会话序号/时间」人话替代原始键，原始键收进 title/详情。

**【日常使用-8】检测记录「稳定度」只会显示 0% 或 100% —— medium · 未复核**
- 位置：`frontend/src/pages/PsychProfilePage.tsx:320`
- 问题：快照时间线的稳定度括号位置错误：`Math.round((s).stability ?? 0) * 100`（先 round 后乘），0-1 的小数全部塌缩成 0%/100% 两档；同页 `:192` 是正确写法（先乘后 round）。stability 是 0-1 小数（`fusion.py:364` round(get_stability_score(), 3)）。
- 建议：把乘 100 移进 Math.round 括号内。

**【日常使用-9】微信接入页不显示绑定的是哪个微信号 —— medium · 未复核**
- 位置：`frontend/src/pages/WeChatPage.tsx:54-66`
- 问题：页面只显示「我的微信已连接」+运行时长/消息数，不显示绑定账号标识；多微信号用户扫错号无从察觉。后端 payload 其实已带 nickname/bot_id（`wechat_channel_routes.py:69-82`，对外已脱敏 token）；`useQueries.ts:199-211` 映射了 bot_id 但丢弃 nickname，WeChatPage 全文无 bot_id/nickname 渲染（仅 `:107`「最后活动」时间）。与 v2 项 2「显示当前聊天角色」相邻但不重复：这是账号归属标识缺口。
- 建议：状态栏补一行「绑定账号：{nickname||bot_id}」。

**【日常使用-10】「语音设计」产出的音色无法命名，列表显示 design_1730… —— medium · 未复核**
- 位置：`frontend/src/pages/SettingsVoice.tsx:284`
- 问题：克隆流可自定义音色名（`:171` `fd.append('voice_name', voiceName)`），设计流却硬编码 `` voice_name: `design_${Date.now()}` ``（`:284`，description 为英文拼接 `:285`）；VoiceList `:117` displayName 回落 name 原样显示——用户在「可用语音」和角色设置里看到一串毫秒时间戳，无法辨识哪个是自己刚建的。
- 建议：设计表单加一个名称输入框（或用风格+性别生成中文显示名），与克隆流对齐。

**【日常使用-11】useEmotionState 类型契约漂移，10s 轮询数据从未被消费 —— low · 未复核**
- 位置：`frontend/src/pages/StatusCenter.tsx:68`
- 问题：前端 EmotionState 类型对应 EmotionEngine.to_dict 形状（`types/api.ts:1-13` ↔ `emotion_engine.py:295-297`），但端点实际返回 health_check 形状（`main_routes.py:42-46`、`personality_routes.py:34-45`），`emotionData?.primary?.type` 恒 undefined，情绪显示全靠 dashboard 兜底，10 秒一次的轮询（`useQueries.ts:157` refetchInterval 10s）白打；且 `:44` 取 `health_data["affinity"]` 而 health_check 无该键（只有 affinity_level/affinity_name，`emotion_engine.py:902-913`）→ affinity 字段也恒 0。当前用户可见值恰好正确（stats.current_emotion 中文枚举兜底，`emotion_engine.py:37-46`），故仅为死通道与维护陷阱。
- 建议：端点改回 to_dict 形状或前端类型对齐 health_check，删掉死轮询；实施前先复核端点实况。

**【日常使用-12】「工作记忆/记忆条目」的归属口径与文案含混 —— low · 未复核**
- 位置：`frontend/src/pages/StatusCenter.tsx:316`
- 问题：「工作记忆」卡文案写「当前会话上下文，保留最近 20 条」，数据却是 orchestrator 全局 working.count()（`misc_routes.py:96` 无任何 session/user 参数；`:101-105` 注释明示普通用户缩量版保留该「无害全局计数」）；与顶部「记忆条目」卡（用户级事实数）也非同一口径。working.count() 是否按用户分桶未逐行核实——「含他人」为推断；计数非 per-user 归属则为实据。
- 建议：数字旁如实标注口径（全站/本会话），或改为当前用户会话的计数。

**【日常使用-13】admin 侧两处「按用户操作」交互不一致：一处下拉一处手输会话键 —— low · 未复核**
- 位置：`frontend/src/components/admin/HumanizeSection.tsx:441`
- 问题：同在「消息」tab，回复模式作用域已改成下拉选用户（R7，`RoleSettingsTabs.tsx:1102-1116`，注释 `:677-679` 明言「不让管理员手输 N:wxid@im.wechat 这种内部键」；代号见「内部代号对照」），拟人化按用户覆盖却要求手输 `2:wxid@im.wechat` 完整内部会话键（placeholder `:434/441`），因果回放还要手输 session_key（`:1014`）。
- 建议：拟人化覆盖与回放复用同一下拉选用户/会话组件，内部键只在详情里出现（详见 NEW-12）。

**【日常使用-14】WeChatPage 运行时长不进位、SSE 注释与实现漂移 —— low · 未复核**
- 位置：`frontend/src/pages/WeChatPage.tsx:38-40`
- 问题：运行时长只做分/秒除法（`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`），连接超过 1 小时显示「75 分 0 秒」；顶部注释宣称 SSE 实时推送，实际 useWechatStatusStream 已隔离为显式 no-op（`:338-340` 注释「避免误连全局 /api/channels/wechat/status-stream」），仅 15s 轮询（`useQueries.ts:214`），而 `:346` 注释仍写「SSE 实时更新缓存，轮询作为兜底（30s 一次）」——误导维护者与依赖注释理解状态时效的用户。
- 建议：时长按小时/天进位；注释改为「仅 15s 轮询」与实现一致。

#### 值得保留的亮点（5 条）

- StatusCenter 三层记忆管线的自解释设计扎实：每层标注「是什么/存什么/有多少/留存多久」，且明确注释数据全部取自既有端点、不新增后端接口（`StatusCenter.tsx:239-251`），普通用户能看懂记忆沉淀结构——第③层「长期记忆」数字真实可信。
- 心理画像页的合规与数据主权做得好：常驻免责条+24h 热线、明确告知数据落库跨设备保留且可一键清除、重置弹窗区分「该角色 N 份会话画像」的精确文案（`PsychProfilePage.tsx:159-167`、`:197-207`、`:332-343`）。
- W11 工程纪律显著减少了「谎报」类问题：读失败渲染显式错误态+重试而非默认假表单，保存一律以业务回执 status 判成功（`RoleSettingsTabs.tsx:69-77`、`HumanizeSection.tsx:115-131`、VoiceTab `:428-433`），普通用户不会被假成功误导。
- SettingsVoice 的 P0 收尾既保住用户主权又不放假按钮：普通用户侧前置跳过必 403 的全局切换、如实告知「此处选择仅用于试听；个人音色绑定走角色设置」（`SettingsVoice.tsx:539-553`、`:571-576`），克隆/设计能力对全体用户开放。
- 前端人设/情绪常量有唯一真源文件+锁定测试的机制（`constants/persona.ts` 头注声明唯一改动点、`tests/components/personaConstants.test.ts:68-70` 与后端 stage_config 逐字对齐），情绪枚举中文值直出对用户完全可读（`emotion_engine.py:37-46`）。

---

### 路四 · 自定义配置（15 条：high 4 / medium 7 / low 4；verified 4）

**走查范围**：以「想把系统调教成自己想要的进阶用户」视角，只读走查 SettingsLLM(BYOK)、SettingsVoice、RoleSettingsTabs 消息tab（拟人化/话题状态/骰子闸/回放整理）、ToolsDashboard、剧情线（StorylineEditor/StorylinePage）、SettingsSecurity 六处，评估术语解释、危险操作保护、默认值合理性与高级/日常混排；全程仅读码取证，未启动服务与浏览器。

#### 发现

**【自定义配置-0】「整理记忆」空键=全库改写无确认 —— high · 已复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:833-841`、`:1018`；`api/routers/agent_plane_routes.py:114-159`
- 问题：「整理记忆」按钮在 session_key 留空时调用 agentPlaneCurate(undefined)（handleCurate 传 `replayKey.trim() || undefined` `:836`），后端语义是对**全部会话**执行 apply=True 的记忆整理（改写事实库与派生召回）——复核成立：空键分支扫 user_facts 全部去重键并 run_curator_all_known（`agent_plane_routes.py:154-161`）对每键硬编码 apply=True 删改事实与置信度（`curator.py:195`、`:91-120`），模块注释自述「curate 空键 apply=True 更会全库改写」（`agent_plane_routes.py:4-6`）。按钮与「回放」并排 onClick 直接执行（`:1018`），该文件 ConfirmDialog 仅用于角色删除（`:1411`），无作用域提示，按钮看起来只作用于当前角色。复核缓和点（不改变结论）：删除走 recycle=True 进回收站并非严格不可撤销，全库分支有 limit=200 上限，该面为 admin-only。**追溯性问题（本审计未取证）**：生产上该按钮是否已被点过、记忆库是否已被空键 apply=true 整理改写过——列入「生产取证待办」，主人读完本条的第一追问应在实施前有答案。
- 建议：空键时弹确认并明示「将整理全部会话的记忆（进回收站）」，或强制要求先填会话键；按钮旁标注作用域。前端确认只是拦截层，后端全库语义是否收敛为显式作用域参数见实施风险 7。

**【自定义配置-1】工具开关普通用户 403 静默无反馈 —— high · 已复核**
- 位置：`frontend/src/pages/ToolsDashboard.tsx:96-108`、`:171-185`；`api/routers/tools_routes.py:138-142`；`navGroups.tsx:48`
- 问题：与【信息架构-1】同一问题域的双重 verified 记录：全文件未读 authStore、开关按钮无条件渲染（`:171-185`，toggleable 只看状态 `:148`）、后端 toggle 带 `require_role("admin")`（`tools_routes.py:142`）、handleToggle 的 catch 注释自认「revert on error is implicit」（`:103-104`）但无 toast；入口不过滤 isAdmin（`navGroups.tsx:48`）。MessageTab/SettingsSecurity 已有「非 admin 不渲染」门禁纪律，本页缺失。toggle 写全局 runtime_switches.json（真源 `tools/tool_state.py:37`；发现引用行号 37-42 有错位）。
- 建议：非 admin 改为只读状态卡（保留可见性，不收信息）；开关失败给 toast；页面顶部加「开关为全局作用域、影响所有用户」横幅。

**【自定义配置-2】语音设计音调/语速滑条是死控件 —— high · 已复核**
- 位置：`frontend/src/pages/SettingsVoice.tsx:347-379`、`:283-288`；`frontend/src/api/mimo.ts:46-60`；`api/routers/mimo_voice_routes.py:232-235`
- 问题：「语音设计」区提供音调（-12~+12）和语速（0.5~2.0x）滑条，但 handleDesign 只发送 voice_name/description/gender/age_group——用户调了完全不影响生成的音色，属「写了不生效」假控件。复核全链路实据成立：滑条采集 design.pitch/speed（`:346-379`）但 handleDesign 仅发四字段（`:283-288`），mimoDesign 形参与 FormData 均无此二参（`mimo.ts:46-56`），后端 Form 与 provider payload 亦仅四字段（`mimo_voice_routes.py:233-236`、`mimo_tts_provider.py:457-464`），design.pitch/speed 全部出现点仅为滑条展示——属静默死控件。（复核注：发现引用的「W11-D4 纪律」条目定位失准——该代号条目的真实内容以 `docs/verification/W11-2026-09-27-前端用户任务与声明统一-验证报告.md` 原文为准，见「内部代号对照」，不影响核心事实。）
- 建议：若 MiMo voicedesign 支持则接线到 description 或后端参数；不支持则移除滑条或在滑条旁如实标注「当前版本不生效」，不得保留静默死控件。

**【自定义配置-3】语音选中态失真且 admin 点击即全局切 —— high · 已复核**
- 位置：`frontend/src/pages/SettingsVoice.tsx:518`、`:539-553`、`:572-576`
- 问题：进入页面后高亮的「可用语音」只是列表第一项而非平台当前默认音色（`:518` `if (!activeVoice && list.length > 0) setActiveVoice(list[0].name)` 首项回填，未读 /mimo/status 真值——真值确在该端点返回，`mimo_voice_routes.py:376` + `mimo_tts_provider.py:514`）；admin 点击任意卡片即静默调用 mimoSwitchVoice 改全局 TTS 单例（`:539-553`，影响所有用户，`mimo_voice_routes.py:315/318/339` 自述改全局 TTS 单例），无作用域横幅、无确认。非 admin 至少有边界说明行，admin 反而没有任何提示（`:572-576` 仅 !isAdmin 渲染）。
- 建议：选中态用 /mimo/status 真值回填；admin 点击时明示「将设为全局默认音色」或加作用域横幅（对齐 MessageTab 的作用域横幅纪律），**不收能力只加透明**。

**【自定义配置-4】骰子闸无自身保存入口易丢改动 —— medium · 未复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:940-1009`、`:1144-1152`、`:790-792`
- 问题：骰子闸 10 个键改完后该区块内没有任何保存按钮；唯一保存按钮在隔了账本/统计/暂停/频率四个区块的「频率控制」区，文案叫「保存频率配置」（`:1144-1152`），完全看不出它也保存骰子闸（dice_gate 确随该 POST 发送 `:790-792`，但「一次 POST 全量随行」只写在代码注释 `:937-939` 不进 UI）——管理员极易以为未保存而重复操作、或直接离开丢失修改。
- 建议：骰子闸区块尾加一行「改动需点击下方『保存频率配置』生效」提示，或将保存按钮提升为该 tab 页级单点保存并标注覆盖全部区块。

**【自定义配置-5】覆盖与查询要求手输内部会话键 —— medium · 未复核**
- 位置：`frontend/src/components/admin/HumanizeSection.tsx:431-472`；`frontend/src/components/admin/TopicStateSection.tsx:294-330`
- 问题：同一个消息tab内两套范式：回复模式选用户已是下拉（复用 adminListUsers，R7 复核成果），而拟人化「按用户覆盖」（提示文案+手输框+placeholder 均「填入完整会话键（如 2:wxid@im.wechat）」`:433-444`）和话题状态「查看会话」（需手输会话键+角色 id 并罗列键格式示例 `:296-326`）却要求管理员手输内部键——键名不可读、易输错且无校验。
- 建议：两处复用 adminListUsers 下拉选用户、会话键由选择自动拼装，手输保留为高级入口（详见 NEW-12）。

**【自定义配置-6】θ1/θ2 等术语缺白话因果解释 —— medium · 未复核**
- 位置：`frontend/src/components/admin/TopicStateSection.tsx:266-284`；`RoleSettingsTabs.tsx:636-643`
- 问题：话题状态的 θ1/θ2 只给算法学术语「bigram 短边包含度」（tooltip `:267-269`/`:275-277`），没有一句「调大调小分别发生什么」；骰子闸「连没中加成」「每连没中一次」属生造词需要猜（`RoleSettingsTabs.tsx:638-639`）。进阶用户想调教系统却无法从文案预判参数后果。
- 建议：技术名保留作副标注，主文案补白话因果（如 θ1「调大＝更难被判定为换话题，倾向延续旧话题」）。

**【自定义配置-7】API Key 已配置状态不可见 —— medium · 未复核**
- 位置：`frontend/src/pages/SettingsLLM.tsx:113`、`:152-153`；`frontend/src/tests/components/SettingsLLM.test.tsx:48`
- 问题：BYOK 页面的 API Key 输入框永远显示为空（加载时 `'****'` 回显被转成空串 `:113`；保存时注释「GET 返回 ****。空输入表示保留原 key」`:152-153`；测试契约确认后端回显 '****' `SettingsLLM.test.tsx:48`）——「从未配置」与「已配置（留空保留）」在界面上完全一样（输入框仅 placeholder "sk-..." `:313`），进阶用户无法确认自己的 key 是否已生效，只能靠记忆或发消息试错。
- 建议：已配置时展示「已保存密钥（留空保持不变）」徽标或脱敏尾号，不回显明文（改动须连带更新钉死该行为的测试）。

**【自定义配置-8】可选中已禁用供应商且无警示 —— medium · 未复核**
- 位置：`frontend/src/pages/SettingsLLM.tsx:246-268`
- 问题：供应商清单对 enabled===false 的项只渲染灰色「已禁用」徽章（`:266-268`），radio 仍可选中（onChange 无条件 setProvider `:252-259`）并保存成功（handleSave 不校验 `:140-156`）；之后该用户对话链路大概率持续失败（推断：禁用供应商会被网关拒绝），错误要到聊天时才暴露，配置页无任何即时提示。
- 建议：选中禁用项时即时显示警示文案（**不禁用选择、保留用户自主权**），保存回执可附加「所选供应商当前已禁用」提醒。

**【自定义配置-9】参考音频无要求与云端隐私提示 —— medium · 未复核**
- 位置：`frontend/src/pages/SettingsVoice.tsx:188-253`；`api/routers/mimo_voice_routes.py:33-41`
- 问题：语音克隆区只有名称输入和文件选择，没有参考音频的理想时长/录制环境/大小上限说明（50MB 上限只存在于后端常量 `mimo_voice_routes.py:33-41`），也没有「音频将上传至 MiMo 云第三方用于克隆」的隐私声明——在情感陪伴产品里这是用户敏感操作。
- 建议：上传区加一行：建议时长与安静环境、≤50MB、音频将上传至 MiMo 云处理——对齐项目「透明操作」偏好。

**【自定义配置-10】消息tab九区块混排日常与开发级 —— medium · 未复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:885-1186`
- 问题：单个「消息」tab 平铺九个区块（顺序：LLM 主动决策 :895 → 骰子闸 :940 → 因果回放/记忆整理 :1012 → 消息统计 :1024 → 主动对话 :1040 → 频率控制 :1051 → 拟人化 :1160 → 话题状态 :1165 → 最近主动消息 :1174）：「紧迫阈值」这类日常参数与「session_key 回放」「θ1/θ2」开发级工具同层无层级，页面信息密度极高，进阶用户与日常用户都难定位。仅全局作用域有横幅（`:887-892`），区块间无分组。
- 建议：与 v2 裁决第 3 条分组思路一致：tab 内按「日常调频/拟人化节奏/话题引擎/运维工具」分组或折叠高级组，**全部能力保留不隐藏**。

**【自定义配置-11】剧情线裸分钟参数与告别默认模板 —— low · 未复核**
- 位置：`frontend/src/components/storyline/StorylineEditor.tsx:324-344`、`:385-395`、`:19-60`
- 问题：「每句话增加（分钟）」没有主语（增加的是剧情进度时间，标签 `:326-340`）；阶段编辑用裸分钟数字输入（「开始时间(min)/结束时间(min)」`:385-395`，非编辑态反而有 fmtMin 换算 `:441-443`/`:538-547`）；新启用用户直接得到一套自带「离别克制期→告别期」分手结局的五段默认模板（DEFAULT_STAGES 末两段 `:44-59`），模板意图无 UI 说明（仅存档横幅只解释了不生效，未解释模板来源）。
- 建议：时间输入复用 fmtMin 做天/小时快捷换算或辅助输入；一句话说明「默认模板为示例剧情，可整体改写」。

**【自定义配置-12】关闭内容安全开关无二次确认 —— low · 未复核**
- 位置：`frontend/src/pages/SettingsSecurity.tsx:102-127`、`:196-217`
- 问题：内容安全过滤器总开关关闭是全局生效、降低所有用户防护的操作，admin 单击即生效（handleToggle 直接 safetyConfig(next) `:102-127`，开关按钮无 ConfirmDialog `:202-216`，有回执校验但无确认步骤）；后端 /api/safety/config 为全局总开关（`safety_routes.py:120-122` 起）。开启无需确认无妨，关闭方向值得一步确认。
- 建议：仅在「开启→关闭」方向加一次确认（含「影响所有用户」文案），不影响开启路径。

**【自定义配置-13】工具卡标题显示原始英文键名 —— low · 未复核**
- 位置：`frontend/src/pages/ToolsDashboard.tsx:160-168`
- 问题：工具卡片主标题直接显示后端原始键名（`{entry.name}` `:161-162`），中文描述反而只是副行（`{TOOL_DESCRIPTIONS[entry.name] || entry.name}` `:167-169`——兜底也是原名；TOOL_DESCRIPTIONS 已有 12 个中文名 `:22-35` 却只用于副行）；未收录进映射的工具连描述都回退为英文键名。与 v2 裁决第 1/2 条「数据键名可读化」同族但页面不同，属补充面。
- 建议：中文描述升为主标题、英文键名缩小为副文本（兼容未知工具回退原名）。

**【自定义配置-14】缓存未接线但控件仍可交互保存 —— low · 未复核**
- 位置：`frontend/src/pages/SettingsLLM.tsx:396-424`
- 问题：「LLM 缓存」琥珀色横幅诚实明示未接线（`data-testid="cache-not-integrated"` `:396-400`，好设计），但下方启用开关与缓存时长仍可改动并随保存提交（Toggle 与 ttl 无禁用态 `:401-424`），制造「配置有效」的残余暗示；保存回执也不会提示这两个键无效（describeReceipt 仅翻译 restart_required/unsupported/in_sync `:20-33`；推断：save_receipt 未把 cache 列入 unsupported）。
- 建议：保留入口不删，但可置灰交互或在 describeReceipt 中追加「cache 键当前不生效」提示。

#### 值得保留的亮点（5 条）

- **保存回执诚实化是全项目亮点**：SettingsLLM 把 save_receipt 的 restart_required/unsupported/in_sync 翻译成「还需要用户知道的事」（`SettingsLLM.tsx:19-33`、`:157-160`），各页普遍以回执 status 判业务成功而非 HTTP 200（RoleSettingsTabs W11-D5 多处、`SettingsSecurity.tsx:107-116`；代号见「内部代号对照」），「保存成功≠生效」从不谎报。
- 拟人化面板的教学式默认值范式值得推广：每个滑条 tooltip 标明默认值与现行行为的等价关系（如「默认 45 = 现行 0.045s/字」，`HumanizeSection.tsx:190-217`、`:331-389`），区间互钳防把必然 400 的载荷发出去（`:136-144`），裁决固化值以「固定」徽章只读展示（`TopicStateSection.tsx:63-73`、`:286-292`）——默认值透明度极高。
- 剧情线「仅存档（未接入对话）」诚实横幅直接告知「请勿按剧情会推进来使用」（`StorylineEditor.tsx:230-246`），设置页对 LLM 缓存未接线同样如实声明「不生效、也不会节省 token」（`SettingsLLM.tsx:394-400`）——不夸大未接线能力。
- 危险操作分层保护已成形：删除角色有独立红色危险区+说明后果+ConfirmDialog+回执 status==="deleted" 校验（`RoleSettingsTabs.tsx:1399-1438`）；手动全局广播已从角色页移除并留只读记录（`:1167-1172`）；非 admin 不渲染必 403 的假表单在 MessageTab/HumanizeSection/TopicStateSection/SettingsSecurity 四处一致执行。
- 安全面板数据按用户隔离且状态诚实：普通用户只能看到本人安全事件（`api/routers/safety_routes.py:60-61` _user_filter_scope），前端空态「暂无安全事件」、错误态显式重试、缺字段补 0 不编数字（`SettingsSecurity.tsx:76-91`、`:143-161`、`:222-224`）。

---

### 路五 · 管理员旅程（14 条：high 4 / medium 5 / low 5；verified 4）

**走查范围**：以站长/admin 视角只读走查 AdminUsersPage、AdminProvidersPage、SettingsLogs 与全局数据面（状态中心、角色设置·消息 tab 的主动消息管理），评估「查用户问题/看今天谁在用/改全局配置」三大任务流、危险操作确认与审计、admin↔普通双视角心智负担。全部结论来自文件阅读与 grep 实证，未运行测试或服务（任务要求只读）。

#### 发现

**【管理员旅程-0】主动消息历史字段契约错位，统计卡全死 —— high · 已复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:708`
- 问题：前端读 h.type/h.at（类型定义 `:708`、赋值 `:746-747`、渲染 `:1178-1180`），但生产路由主路径（rp 非 None，即生产 4-worker 控制面）实际返回 `{id,kind,channel,session_key,message,status,created_at,fail_reason}`（`api/routers/training_routes.py:961-973` 显式映射），created_at 为 epoch 秒浮点（`proactive/runtime_plane.py:202-203` _now()=time.time()、outbound_commands.created_at REAL、recent_sends `:583-597` 直读）。后果：「今日主动」恒 0（`:820` 过滤永不命中，且该行用 new Date().toISOString() 取 UTC 日界，违项目 local_time 纪律）、「最后发送」恒 —（`:821`）、类型列恒空、时间列恒 Invalid Date（`:1180`）——admin「看今天主动消息发没发」的核心读数全部失真。单测 history 恒 mock 为空数组（`tests/components/RoleSettings.test.tsx:184`），契约断裂无测试捕获。（复核缓和：rp is None 的 fallback 走旧 sent_history 格式 `training_routes.py:975-978`，不影响生产主路径结论。）
- 建议：前端按真契约改读 kind/created_at 并做 epoch 秒换算与**本地日界**统计（项目有 `local_day_utc_bounds` 先例，勿用 toISOString 的 UTC 日），补一条钉住真实响应形状的单测；顺带统一「消息总数」（本角色）与「今日主动/最后发送」（全站）的口径标注（`:1027`）。**已裁决的「Invalid Date 两处」小修必须先落本项**。

**【管理员旅程-1】状态中心「最近沉淀」卡是死卡 —— high · 已复核**
- 位置：`frontend/src/pages/StatusCenter.tsx:406`
- 问题：「最近沉淀」对 recentFacts 过滤 f.content 并渲染 fact.content（`:406`/`:413`），但后端 /api/memory/facts 两个分支返回的行键都是 fact（不是 content）→ content 恒 undefined → 过滤后恒空，卡片对 admin 和普通用户都永远显示「还没有沉淀下来的记忆」，与真实记忆库存量无关。复核成立：admin 分支走 SemanticMemory.get_facts 输出键 "fact"（`misc_routes.py:335-336`；`shisi/memory/legacy/semantic_memory.py:297`，行内还带 user_key `:301`）；普通用户分支走 SELECT * FROM user_facts（`misc_routes.py:337-339`；`structured_memory.py:988-994`，列名 fact `:167`）；前端类型 MemoryFact 声明 content/type（`types/api.ts:40-47`），client.ts 拦截器仅加 Authorization 与 401 处理、无键名改写（`api/client.ts:74-80/109-225`）；单测 mock 用 content 键把错误契约钉进测试（`tests/components/StatusCenter.test.tsx:115-116`）。
- 建议：前端改读 fact 键（或后端补 content 别名），单测 mock 改用后端真实形状；顺带把返回行里的 user_key 用起来做归属标注（见发现 13 与 V2-1）。

**【管理员旅程-2】查用户问题无下钻，通道运维前端零入口 —— high · 已复核**
- 位置：`frontend/src/api/admin.ts:78-112`
- 问题：「查一个用户的问题」任务流断在第一步：用户列表只有账号字段（AdminUser `admin.ts:15-25`），无微信绑定/通道连接态/活跃角色/会话键；行操作只有编辑与删除（AdminUsersPage `:361`/`:374` 弹窗）。而后端早已具备 admin 通道总览与强制断开端点——GET /api/admin/wechat/channels 返回全部通道连接态+磁盘孤儿凭证（`api/routers/wechat_channel_routes.py:267-313`）、POST .../{user_id}/{slot}/force-disconnect（`:316-331`）、GET /api/admin/users/{user_id} 单用户详情（`admin_routes.py:118-129`）——均为活端点且前端全量 grep 零消费者（`grep -rniE 'admin/wechat|force-disconnect' frontend/src` exit=1）。站长排障只能去日志翻查（取证注：日志展示链路能否翻到 session_key 亦未证实，见发现 3 的取证注）。纯死接口。
- 建议：用户列表行展开/详情抽屉接入单用户详情+通道状态（连接徽章、slot、bot_id），force-disconnect 带确认弹窗——**后端已就绪，纯前端补面**。

**【管理员旅程-3】两处按用户配置要求手输内部会话键，「按用户调节奏」管理流无可便捷途径 —— high · 已复核**
- 位置：`frontend/src/components/admin/HumanizeSection.tsx:441`
- 问题：拟人度「按用户覆盖」（`:437-445` 提示文案+手输框+「加载该用户」）与话题状态「会话快照」（`TopicStateSection.tsx:301-309` 同款手输框）都要求管理员手输 `N:wxid@im.wechat` 内部键，而产品内没有任何**便捷、可发现**的查键界面（前端全量 grep 证实：AdminUsersPage/api/admin.ts 无 session_key 字段，SettingsAccount 仅导出本人键）——普通站长无从获得键值，「按用户调节奏」管理流实际受阻。R7 已为回复模式做了用户下拉（`RoleSettingsTabs.tsx:1102-1116`），这两处未跟进。
- **取证注（撰写环节 2026-10-29，化解本发现与发现 2 的表述张力）**：日志页展示链路本身无 session_key 字段输出（`api/routers/misc_routes.py` 与 `observability/logging_setup.py` grep `session_key` 零命中），api/ 下 consent.py/byok.py/session_manager.py 等业务文件含该字样但**是否打印进日志未逐行核证**——故「去日志翻键」的绕行路径可行性未证实，本发现的「无可便捷途径」成立；若后续取证证实日志行含 session_key，则降格为「重度不便」。**缓和点（已知）**：topic-state 后端复用 _resolve_admin_target_key 支持纯数字 user_id 还原（`topic_routes.py:73-77` → `training_routes.py:338,363-369`）且前端 `:298` 有提示，已绑微信用户可输用户管理页可见的数字 id 绕行——「查话题状态」属重度不便而非不可用；但 humanize 侧端点（`training_routes.py:677-740`）**未调用同文件已有的还原器**，对 user_key 原样使用，「按用户覆盖」侧无缓和。
- 建议：复用回复模式的 adminListUsers 用户下拉 + 后端数字 user_id→N:wxid 还原（`training_routes.py:351-369` 已支持），或至少在用户管理页提供会话键复制。

**【管理员旅程-4】供应商启停无确认，回退链不可见 —— medium · 未复核**
- 位置：`frontend/src/pages/AdminProvidersPage.tsx:285`
- 问题：「禁用/启用」按钮一键直调后端立即写配置生效（handleToggle `:69-85`、按钮 `:285-300` 无确认），无二次确认、无影响提示（禁用主供应商会改变全体用户的可用模型与回退路径）；同时 /all 接口返回的 fallback_chain 与 default_provider 前端从不展示（`:49-50` 只取 res.providers；`api/llmProviders.ts:52-54` 类型有定义但 grep 全前端仅此一处）——站长看不到主备顺序，出问题时无法判断当前回退链。删除预设的确认文案反而提到「同时从回退链移除」（`:352`），链本身却不可见。后端 toggle 即写即生效（`llm_providers_routes.py:401-416`）。
- 建议：toggle 前加确认（文案标注该供应商是否在回退链中），页头以徽章序列展示 fallback_chain 顺序。

**【管理员旅程-5】危险操作审计缺操作者身份 —— medium · 未复核**
- 位置：`api/routers/admin_routes.py:249`
- 问题：管理员建用户/改用户（含改角色、停用、重置密码）/删用户与供应商增删改启停，日志只记对象不记操作者（`admin_routes.py:165/217-218/249-251` 与 `llm_providers_routes.py:317-318/394/415/446` 的 logger 均无操作者字段），也没有结构化审计存储（grep api/ 目录 'audit' 无审计表或中间件）——多管理员或事后追责场景无法定位是谁做的。同仓微信强制断开却记录了 admin=%s（`wechat_channel_routes.py:327-329`），说明操作者入日志的基建已有先例；`observability/logging_setup.py:45-50` 已有 user_id contextvar 机制可承载。
- 建议：admin 写操作统一在 require_role 依赖处把操作者注入日志上下文（复用 UserContextFilter 模式），或落一张 admin_audit 表。

**【管理员旅程-6】停用（封禁）藏在编辑弹窗且无确认 —— medium · 未复核**
- 位置：`frontend/src/components/admin/UserForm.tsx:134`
- 问题：封禁用户的主手段是编辑弹窗里一个「活跃」checkbox（`:134-146`），取消勾选点保存即生效并立即撤销该用户全部 token（`admin_routes.py:206-212` 停用即 bump_token_version，注释自证旧 access/refresh 立即失效），全程无确认弹窗；列表页也无快捷启停（UserTable 行操作只有编辑/删除 `:202-219`）。误触成本与操作重量不匹配（删除反而有确认）。
- 建议：列表加快捷停用开关+确认弹窗，或编辑保存时检测 is_active true→false 弹确认并说明「该用户所有登录将立即失效」。

**【管理员旅程-7】无最后一个管理员保护 —— medium · 未复核**
- 位置：`api/routers/admin_routes.py:202`
- 问题：update_user 允许把任何 admin（包括自己）降权为 viewer，无「至少保留一个 active admin」守卫（`:169-219` 无 self/last-admin 校验，仅 delete 有 self 守卫 `:231-232`；`:203-205` 注释明言角色改动即时生效无需重登），前端编辑自身账号时角色下拉也可选观察者且无警示（`UserForm.tsx:124-132`）。单管理员站一次误操作即失去全部管理入口，只能改库恢复。
- 建议：后端拒绝降权/停用最后一个 active admin（或前端对自身降权追加二次确认+后果说明）；守卫须同时覆盖降权与停用两条路径。

**【管理员旅程-8】用户列表排序只作用于当前页 —— medium · 未复核**
- 位置：`frontend/src/pages/AdminUsersPage.tsx:114`
- 问题：表头排序（邮箱/角色/状态/创建时间等）只重排当前页已加载的 20 行（`:112-124` 代码注释自认此缺陷但未修），箭头却呈全局排序态；后端固定 order_by created_at.desc（`admin_routes.py:104`），无 sort/sort_dir 入参；默认还把后端序重排为 id 降序（`:38-39`）。跨页找「最早注册」「某邮箱开头」的用户时结果误导。
- 建议：后端补 sort/sort_dir 参数（用户量增长前做完），或前端排序时改为全量拉取/隐藏排序交互。

**【管理员旅程-9】日志「清屏」5 秒即回灌 —— low · 未复核**
- 位置：`frontend/src/pages/SettingsLogs.tsx:82`
- 问题：「清屏」只清空前端数组（handleClear 仅 setAllLogs([]) `:82-84`），5 秒轮询下一次 doFetch 全量替换即恢复原样（`:63-72`）——按钮行为与用户预期（清掉显示）不符且无提示；另外级别/搜索过滤纯前端做，后端本已支持 level/search 参数（`misc_routes.py:356-357`）未下推（前端只传 limit `:45`，`api/system.ts:181-183`）。
- 建议：清屏与「暂停」联动（清掉同时停轮询），或把过滤下推服务端减少无谓传输。

**【管理员旅程-10】日志页无作用域标识，双视角同文案 —— low · 未复核**
- 位置：`frontend/src/pages/SettingsLogs.tsx:111`
- 问题：同一句「查看系统运行日志、调试信息与错误记录」下，admin 看到的是全站含其他用户活动的日志（`misc_routes.py:348-350` memory_scope None=全量）、普通用户只见本人日志（`logging_setup.py:86-87` user_id 过滤），页面无任何作用域标签（组件不读 user.role `:35-41` 无 useAuthStore）——普通用户会误以为能看到全站，admin 的全局视图也无「这是全站数据」的明示。这是已裁决「admin 数据面默认收窄+显式全局开关」的相邻面：日志页也需纳入同一作用域语义。
- 建议：按角色显示「全站日志」/「我的日志」徽标，与 v2 全局开关的词汇保持一致。

**【管理员旅程-11】日志环形缓冲按 worker 隔离，只见切片 —— low · 未复核**
- 位置：`observability/logging_setup.py:54`
- 问题：/api/logs 的真源是每进程 200 条的内存 deque（RingBufferHandler 模块级单例 `:54-59`、`:100`；`misc_routes.py:362-369` 直读该缓冲），生产 4 worker 部署下 admin 查日志只能看到恰好接住该请求的那个 worker 的最后 200 条，跨 worker 请求可能整段缺章，高流量下数秒即滚没——影响「查一个用户的问题」的取证可靠性（推断：端到端复现某用户问题时可能拿不全时间线）。（引证注：4 worker 依据 AGENTS v1.51 收口条目「4 worker」记载；AGENTS v1.46 条目所记「恢复 count=4」为活跃通道数而非 worker 数，不作本项引证。）
- 建议：聚合读多 worker（如经 agent_plane/control_commands 既有跨 worker 通道）或落共享存储，至少在页面标注「仅当前 worker」。

**【管理员旅程-12】消息统计卡三指标口径混排 —— low · 未复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:1027`
- 问题：同一张「消息统计」卡里，「消息总数」是当前角色的私域计数（character.stats?.messages），「今日主动」「最后发送」却是全站主动消息口径（/api/proactive/history 跨用户全表，`training_routes.py:944-979`）——放在带「全局配置」横幅的 tab 里，admin 会把三个数都当全站读。修复发现 0 后此口径问题才会显形。
- 建议：为「消息总数」标注「本角色」或改用全站计数，三个指标同口径。

**【管理员旅程-13】全局记忆事实无归属标注 —— low · 未复核**
- 位置：`frontend/src/pages/StatusCenter.tsx:404`
- 问题：admin 视角下记忆事实来自全体用户（后端不做归属过滤，`misc_routes.py:334-336`），返回行里带着 user_key（`semantic_memory.py:301`、`structured_memory.py:989`），但「最近沉淀」只渲染内容不显示归属（`:406-413`，组件类型只声明 content `:257`）——即便落地已裁决的「admin 默认只看自己数据+显式全局开关」，切到全局视图后仍分不清哪条记忆是谁的，「按人查问题」依旧缺最后一步。**此条是对已裁决 v2 项 1 的实施细节补充，非重复提案**。
- 建议：全局视图下每条事实附可读归属标签（结合 v2 项 1 的「数据键名可读化」一起做 user_key→用户名映射）。

#### 值得保留的亮点（5 条）

- 权限分层前后端一致且被测试钉住：非 admin 不渲染表单也不发请求（`AdminUsersPage.tsx:85`、`RoleSettingsTabs.tsx:761-764`、`HumanizeSection.tsx:88-92`），e2e 有「admin 视图与 viewer 视图互不串」专项回归（`w11_journey.spec.ts:290`）与「viewer 直调 admin 端点→403」后端兜底断言（`:126`）。
- 删除用户是完整的生命周期作业而非裸 DELETE：跨存储逐 owner 清除、部分失败返回 job_id 可续跑（`admin_routes.py:239-258`），前端 danger ConfirmDialog + 前后端双重禁止自删（`UserTable.tsx:215` disabled + `admin_routes.py:231-232` 400）。
- 供应商密钥安全扎实：读出即脱敏 ****、明文只写 gitignored local 文件、tracked json 写前强制剥离（`llm_providers_routes.py:129-147`、`:181-192`、`:195-202`），编辑弹窗明示「****/留空=保留原值」（`ProviderEditModal.tsx:47`、`:222-224`）——与「不收走用户 BYOK」的设计哲学同向。
- 全局配置的作用域披露与防假成功做得好：主动消息 admin 表单顶部 amber 横幅明示「作用于全部角色…并非本角色专属设置」（`RoleSettingsTabs.tsx:887-892`），读失败显式错误态+重试绝不渲染默认假表单、保存以回执 status==='ok' 判真（`:749-757`、`:793-798`）。
- 回复模式「指定用户」作用域的落地方式值得推广为范本：前端用户下拉代替手输内部键（`RoleSettingsTabs.tsx:1102-1116`），后端把数字 user_id 经 wechat_bindings 还原成 N:wxid、无绑定宁拒绝不猜键（`training_routes.py:351-369`）——同一模式复制到拟人度/话题状态即可消掉发现 3。

---

### 路六 · 视觉一致性（13 条：high 1 / medium 8 / low 4；verified 1）

**走查范围**：跨页视觉与交互一致性只读走查（横切审计维度，非单一用户角色）：以 `frontend/src/App.tsx` 路由为入口静态阅读 pages/components/store/api/css 与 e2e 行为规格，覆盖公共组件采用度、加载/空/错误三态、文案语气、macaron token、响应式断点五域；全程未启动 dev server/浏览器、零文件改动（遵守任务约束）。

#### 发现

**【视觉一致性-0】查询错误被伪装成空态或零值 —— high · 已复核**
- 位置：`frontend/src/pages/RolesPage.tsx:72`
- 问题：react-query 页面普遍只消费 isLoading/data 而不消费 isError，接口失败时页面渲染成「暂无角色」「画像尚未生成」等笃定空态或 0 值；全局 toast 约 4 秒消失后页面残留误导状态，用户会误以为自己没有角色/没有数据。复核实读全部引用文件成立：RolesPage `:72` 仅解构 data/isLoading、`:125` data 为 undefined 即落「暂无角色」+创建 CTA；PsychProfilePage `:103` 只取 isLoading、`:109` status 回落 'unavailable' 落「画像尚未生成」空态（`:175-186`）；StatusCenter `:52-58` 共 7 个 query 解构全部不消费 isError（发现原文写六个、路径少一级，不改变行为结论），错误时记忆条目显示 0、亲密等级按 0 取最低档；`api/queryClient.ts:3-12` 无 QueryCache.onError 兜底；`client.ts:108-225` 全局 toast 属瞬态（`errorStore.ts:26` 默认 4000ms 自动消失）。正面对照：`RoleSettings.tsx:46-52` 与 `WeChatPage.tsx:15,27` 消费 error/isError。
- 建议：三页补 isError 分支（错误卡+重试按钮），或在 queryClient 挂全局错误兜底；空态仅在请求成功且数据为空时渲染，不改变任何能力入口。

**【视觉一致性-1】Slider 滑轨硬编码非 token 色 —— medium · 未复核**
- 位置：`frontend/src/components/shared/Slider.tsx:49`
- 问题：全站复用的 Slider 滑轨填充与拇指悬浮光圈硬编码 `#22d0df`/rgba(34,208,223)（`:49`/`:46`），既非 macaron 三色也非收编后的 blue(sky) 族（`index.css:51-53` blue 族重映射为 sky 值，不含 #22d0df），形成孤立的青色相；同页开关型控件 Toggle 却用暖黄（`Toggle.tsx:22` 选中态 bg-primary-400），开关类控件双色相语言。grep 实测 Slider 被 9 文件消费（SettingsLLM、SettingsVoice、WeChatPage、AdminUsersPage、AdminProvidersPage、SettingsLogs + TopicStateSection、HumanizeSection、RoleSettingsTabs）。
- 建议：滑轨改用 `var(--color-accent-400)` 或暖黄→海盐蓝渐变，与 Toggle/主题色对齐。

**【视觉一致性-2】主按钮三套视觉语言并存 —— medium · 未复核**
- 位置：`frontend/src/components/common/Button.tsx:12`
- 问题：基础 Button 组件几乎无人采用（grep `import Button` 仅 3 文件：`StorylineEditor.tsx:2`、`KnowledgePreview.tsx:5`、`ErrorBoundary.tsx:2`），135 处原生 button 中「主操作」分散在 .btn-macaron 类（9 文件：LoginPage/IntroPage/WeChatPage/RolesPage/TemplatesPage 等）、内联样式与 ConfirmDialog 自写按钮三种视觉之间（ConfirmDialog `:30` 自写 bg-primary-500、Button primary 为 bg-primary-600、.btn-macaron 为浅黄底深墨字 `index.css:331-333`），同一「主确认」存在三种色深与对比度。
- 建议：让 Button 组件对齐 btn-macaron 视觉后逐步替换高频主 CTA，ConfirmDialog 确认钮改用 Button；不动普通用户任何自定义能力入口。

**【视觉一致性-3】加载态呈现四种形态并存 —— medium · 未复核**
- 位置：`frontend/src/pages/SettingsVoice.tsx:96`
- 问题：同为「加载中」，角色/模板页用手写骨架卡（`RolesPage.tsx:120-124`、`TemplatesPage.tsx:122-126` 手写 animate-pulse 玻璃卡）、用户表用 Skeleton 组件（`UserTable.tsx:141-147`）、语音与角色设置用纯文字「加载中...」（`SettingsVoice.tsx:96`、`RoleSettings.tsx:38-44`）、路由切换用 App 级灰色块骨架（`App.tsx:44-56` bg-gray-200/60，玻璃语境外的中性灰），四种形态观感不统一；SettingsLLM 的结构化骨架是最佳实践（`:176-190` 结构化骨架+role="status" aria-label）但未推广。
- 建议：以 SettingsLLM 结构化骨架为范式统一列表页骨架（走 Skeleton 组件），淘汰纯文字加载态与灰块 PageLoadingSkeleton。

**【视觉一致性-4】shared 组件库存在死组件 —— medium · 未复核**
- 位置：`frontend/src/components/shared/SubTabBar.tsx:13`
- 问题：SubTabBar 全站零使用（grep `<SubTabBar` 全仓 0 命中），RoleSettings 自己实现了几乎相同且更强的 tab 条（`RoleSettings.tsx:84-99`，多出 URL 真源、min-w、overflow-x-auto、focus-visible ring）；Skeleton 全站仅 1 处使用（`UserTable.tsx:141-147`，经 shared/index.ts barrel 导入）——公共组件库与实际用法脱节，削弱其作为一致性抓手的可信度。
- 建议：删除 SubTabBar（走删除纪律）或让 RoleSettings 改用它并保留 URL 真源能力；Skeleton 推广到列表页加载态。

**【视觉一致性-5】EmptyState 未覆盖普通用户页面 —— medium · 未复核**
- 位置：`frontend/src/pages/RolesPage.tsx:126`
- 问题：EmptyState 组件只在 admin 域 3 个文件使用（`UserTable.tsx`、`AdminUsersPage.tsx`、`AdminProvidersPage.tsx`），普通用户可见页（角色/模板/语音/安全）的空态全部手写 div（`RolesPage.tsx:126-135`、`TemplatesPage.tsx:137-148`、`SettingsVoice.tsx:98`、`SettingsSecurity.tsx:223`），图标、字号、留白各自为政；各处文案质量尚可但结构漂移。EmptyState 本体自带 icon/title/description/action 结构（`EmptyState.tsx:10-21`）可承载这些场景。
- 建议：空态统一收编 EmptyState 组件（TemplatesPage 的引导文案可作范例用法下沉），保持全部功能入口可达。

**【视觉一致性-6】扫码模态自建缺可访问性 —— medium · 未复核**
- 位置：`frontend/src/pages/WeChatPage.tsx:230`
- 问题：新用户扫码绑定微信这一核心旅程的模态绕开 shared/Modal 自建（`:230-236` 自建 `fixed inset-0` 外壳），缺 ESC/遮罩点击关闭，关闭按钮无 aria-label 且触控目标远小于站内 44px 标准（`:233` 关闭按钮仅 text-gray-400 hover，X 图标 h-4 w-4 无最小热区）。对照 `Modal.tsx:17-22` ESC 监听、`:30` 遮罩点击关闭、`:35` aria-label="关闭"+h-11 w-11；grep `fixed inset-0` 全站仅 Modal/MobileDrawer/ConsentGate/此处四例（ConsentGate 为强制同意门，属合理特例）。
- 建议：迁移到 shared/Modal，或至少补 ESC、遮罩关闭、44px 关闭热区与 aria-label。

**【视觉一致性-7】Toast 缺安全区与触控标准 —— medium · 未复核**
- 位置：`frontend/src/components/common/Toast.tsx:24`
- 问题：全局 Toast 固定 `fixed top-4 right-4 z-[100]` 未适配刘海屏安全区（未用 `index.css:141` 的 .safe-area-top，而 `index.html:6` 已声明 viewport-fit=cover——刘海实际遮挡程度为推断），关闭按钮无 aria-label、触控目标小于站内 44px 标准（`:34-39`），图标用 ✕⚠i✓ 字符与全站 lucide 图标语言不一致（`:11-16` typeIcons）。对照 `Modal.tsx:35`、`MobileDrawer.tsx:75-78` 的 h-11 w-11+aria-label 站内标准。
- 建议：top 加 env(safe-area-inset-top)，关闭钮补 aria-label 与 44px 热区，图标换 lucide。

**【视觉一致性-8】用户表日期无 Invalid Date 防护 —— medium · 未复核**
- 位置：`frontend/src/components/admin/UserTable.tsx:47`
- 问题：formatDateTime/formatRelative 对非法时间串无 isNaN 兜底（`:38-45` formatDateTime 直接 new Date(iso) 后取 getFullYear 等，null 有 '-' 兜底、非法串无；`:47-56` formatRelative 中 diff=NaN 使 mins<1/<60/<24/<30 全部为 false 落入 formatDateTime 输出 NaN 串），会输出「NaN-NaN-NaN NaN:NaN」式文本。与已裁决 v2 小修「Invalid Date 两处」同族，建议并入同批收敛。站内守卫范本：`SettingsSecurity.tsx:50` `Number.isNaN(parsed.getTime())`、`WeChatPage.tsx:101-105` 秒/毫秒启发式+年份守卫。
- 建议：抽公共 formatDate 工具统一 NaN 守卫，与 v2 已裁决的 Invalid Date 两处一并修复。

**【视觉一致性-9】admin 角色徽章用未收编 rose 色 —— low · 未复核**
- 位置：`frontend/src/components/admin/UserBadges.tsx:17`
- 问题：管理员角色徽章用原生 rose 粉族（ROLE_BADGE.admin 为 `bg-rose-50 text-rose-600 ring-rose-200/50` `:17-19`），而 index.css 色板收编注记明确把 rose/pink/indigo 保留给 EMOTION_COLORS 情绪分类数据色（`index.css:61-63`）——非数据场景出现粉红脱缰色相（视觉冲突低频，属色板纪律问题）。同文件 :19 viewer 用 sky 族视觉一致。
- 建议：admin 徽章改用 tag-blue/tag-yellow/tag-mint 或 primary/accent 族，rose 留给情绪数据色场景。

**【视觉一致性-10】角色设置头卡裸露 user_id —— low · 未复核**
- 位置：`frontend/src/pages/RoleSettings.tsx:69`
- 问题：角色设置页头卡向所有登录用户展示原始 `用户: {character.user_id || '未绑定'}`，该路由 /roles/:id/settings 为普通用户可达（`App.tsx:147-148`）；是已裁决 v2 项 2（微信接入页/角色列表两处）之外的第 3 处裸键展示。
- 建议：改为可读归属文案（如「我的角色」/「公共角色」），与 v2 项 2 同批处理，不新增能力变化。

**【视觉一致性-11】面包屑顶栏误用浮层材质 —— low · 未复核**
- 位置：`frontend/src/components/layout/Breadcrumb.tsx:97`
- 问题：常驻文档流内的面包屑条使用 mat-floating 浮层玻璃材质（`className="mat-floating border-x-0 border-t-0 rounded-none …"`），与设计注释「玻璃特权只给真正悬浮之物（侧栏/弹窗/下拉）」的材质语义相悖（`index.css:152-153`；Breadcrumb 位于 `App.tsx:114-116` main 文档流内非悬浮层）；是否特意与侧栏同质属推断，视觉影响小。
- 建议：若非有意，改用 glass-card/mat-raised；或在 index.css 材质注释中明文把顶栏纳入浮层语义清单。

**【视觉一致性-12】图标语言三套混用 —— low · 未复核**
- 位置：`frontend/src/components/admin/RoleSettingsTabs.tsx:1292`
- 问题：全站图标以 lucide 为主，但 admin 角色设置统计卡用 💬🧠 emoji（`:1292-1293`）、Toast 用 ✕⚠i✓ 字符（`Toast.tsx:11-16`），三种图标语言并存；同一文件内还有两份功能重复的统计块样式分叉（`:1292-1293` 带 emoji vs `:1027` 无 emoji 用 text-blue-600）。其余组件均 lucide（`Sidebar.tsx:3`、`Modal.tsx:2` 等）。
- 建议：统一 lucide（MessageSquare/Brain 等），至少先把同文件两份统计块收敛为一份组件。

#### 值得保留的亮点（5 条）

- 移动端一致性有 e2e 钉死：`e2e/mobile.spec.ts` 以 375px 断言无横向溢出+桌面侧栏隐藏+汉堡抽屉开合跳转+800px 平板恢复侧栏（`:8`、`:40-84`）；MobileDrawer 本体质量高——inert 焦点治理、body 滚动锁、safe-area-x、44px 行高（`MobileDrawer.tsx:34-65,100`）。
- 文案语气纪律严格：grep 全仓「您」仅 `constants/agreement.ts` 法律协议文本 16 处，产品 UI 一律「你」0 处「您」；空态文案口语且有出路（`TemplatesPage.tsx:141`「模板库由管理员策展，稍后再来看看，或直接创建自己的角色」）。
- 设计 token 治理是真基建：index.css @theme 三色定义（`:7-92`）、原生 blue/green/gray 686 处零改动重映射收编（`:46-59`）、材质三阶梯 mat-recess/glass-card/mat-floating（`:145-194`）、移动端毛玻璃降级（`:247-259`）、prefers-reduced-motion 全局降级（`:507-514`）。
- WeChatPage 状态工程质量高：isError 横幅（`:27-35`）、时间戳多层守卫（秒/毫秒启发式+年份守卫，`:94-111`）、断开态空态构图+行动指引（`:113-130`），是新用户第一屏的可靠门面。
- 导航单一真源且服务用户主权：Sidebar 与 MobileDrawer 共用 buildGlobalNavGroups（`navGroups.tsx:20`），桌面/移动入口一致；SettingsLLM 的 BYOK 配置来源指示（`:196-200`「当前编辑：你的个人 LLM 配置（独立于其他用户）」）把自定义能力的归属讲得清楚，与设计哲学一致。

---

## 调研章

> 四路调研由上游调研环节以 GitHub API/npm downloads API/LICENSE 原文核对实测活跃度与许可（调研日期 2026-10-28），每主题按「候选项目表 → 模式要点 → 结论（recommendation）」组织。**关键选型数字已于本报告撰写环节（2026-10-29）复跑留痕**，见下方「调研数字留痕」。

### 调研数字留痕（撰写环节 2026-10-29 复跑）

上游调研环节（2026-10-28）以 gh api + npm downloads API + LICENSE 原文核对产生活跃度数字；本报告撰写环节对决定**首选/排除**的关键数字做了复跑，命令与响应摘录如下（与上游 10-28 快照并列；stars/下载量随时间自然增长，量级与排序关系一致）：

```
$ gh api repos/nilbuild/driver.js --jq '{full_name: .full_name, stars: .stargazers_count, pushed_at: .pushed_at, license: .license.spdx_id}'
{"full_name":"nilbuild/driver.js","license":"MIT","pushed_at":"2026-10-18T17:41:58Z","stars":26965}
（上游 2026-10-28 记录：26,964★ / pushed 2026-10-18 / MIT——一致；仓库确认已迁 nilbuild org，引用新地址）

$ gh api repos/open-webui/open-webui --jq '{full_name: .full_name, stars: .stargazers_count, pushed_at: .pushed_at, license: .license.spdx_id}'
{"full_name":"open-webui/open-webui","license":"NOASSERTION","pushed_at":"2026-10-28T17:23:16Z","stars":157054}
（上游 2026-10-28 记录：157,043★ / pushed 2026-10-28——一致；license 字段上游未记录，复跑显示 NOASSERTION，本主题结论为自建不引库，不受影响）

$ gh api repos/open-webui/open-webui --jq '{open_issues: .open_issues_count}'   # 第二轮修订补跑
{"open_issues":217}
（上游记 issues 220；GitHub open_issues_count 口径含 PR，±3 随时间正常波动，非笔误）

$ curl -s https://api.npmjs.org/downloads/point/last-week/driver.js
{"downloads":2790065,"start":"2026-10-21","end":"2026-10-27","package":"driver.js"}
$ curl -s https://api.npmjs.org/downloads/point/last-week/react-joyride
{"downloads":1599438,"start":"2026-10-21","end":"2026-10-27","package":"react-joyride"}
（上游 2026-10-20~26 窗口记录：driver.js 2,226,130 / react-joyride 1,259,164——窗口平移一周后两者同幅增长，driver.js ≈ react-joyride 1.7 倍的排序关系保持）
```

**数字容错说明（其余未复跑数字若失真，结论是否翻转）**：driver.js 首选基于四条件——MIT、活跃（pushed 2026-10）、零依赖、周下载最高；其中前三条与许可证结论不依赖精确数字，唯一依赖数字的「周下载最高」在 ±20% 漂移下排序不变（当前差距 1.7 倍），故即便 react-joyride 数字有误，翻转结果也只是「首选与次选互换」，不触碰 AGPL 排除结论；公告与渐进披露两主题的「自建不引库」结论依赖「无活跃适配库」的检索过程与「隔离要求落库」的产品约束，不依赖任何单一 star 数。**唯一会翻转选型的输入是 LICENSE**（AGPL 判定），已对 shepherd.js/intro.js 给出「引入前再核最新 LICENSE」的兜底。其余候选（shepherd/intro 的 stars 与 peer、LobeChat/Cal.com/VS Code/GitLab/Discourse/Metabase 各项）为上游实测记录，撰写环节未复跑。

### 主题一 · 产品内新手导引与教学（tour 库选型 + 分阶段 onboarding 模式）

**服务对象**：已裁决 v2 第 4 项「完整功能教学导引（覆盖全部模块，不是三步弹窗）」。

#### 候选项目表

| 候选 | URL | 活跃度（gh api 实测） | 提供什么 | 适配度 |
|---|---|---|---|---|
| **driver.js** | github.com/nilbuild/driver.js | 26,964★ / pushed 2026-10-18 / MIT；npm 1.10.2（2026-10-18），周下载 2,226,130（**2026-10-29 撰写环节复跑：26,965★ / MIT / pushed 同日，npm 周下载 2,790,065（10-21~27 窗口）——一致，见章首留痕**） | 零依赖 vanilla TS spotlight 引擎：任意元素高亮镂空+浮层、声明式 steps 配置、onHighlightStarted/onDeselected/onDestroyed 生命周期 hooks、高度可定制，也可当表单聚焦遮罩/上下文帮助弹层复用 | **首选**：与本地 React 19 + Vite 8.3 + TS 6.0 栈零冲突（无 React peer 锁定）、MIT 无合规负担，唯一「最活跃+许可干净」；长教学需自写步骤状态与薄 React 封装，但 checklist/进度持久化本就要自建，边际代价可控。仓库已从 kamranahmedse 迁至 nilbuild org（复跑确认重定向生效），引用新地址 |
| react-joyride (3.x) | github.com/gilbarbara/react-joyride | 7,887★ / pushed 2026-07-09 / MIT；npm 3.2.0，周下载 1,259,164（复跑 10-21~27 窗口 1,599,438，见留痕）；peerDependencies 实测 react 16.8-19 | React 原生组件化 tour：`<Joyride steps callback/>` 声明式接入、回调事件数据流、floating-ui 定位、tooltip 主题可配 | 次选：接入最省事且已明确支持 React 19（issue #1151/#1114 已 closed）；但 8 个运行时依赖、3.x 大版本重写后 React 19 适配曾滞后数月，全模块长流程与组件生命周期耦合更深，样式定制走其 theme/classNames 体系较绕 |
| shepherd.js + react-shepherd | github.com/shipshapecode/shepherd | 13,834★ / pushed 2026-10-22；npm shepherd.js 15.3.0 + react-shepherd 7.0.6（react-shepherd peer ^18.0.0 \|\| ^19.0.0 实测）；周下载 34.6 万/3.1 万 | 功能最全的 tour：floating-ui 定位、attachTo/advanceOn/事件 API、官方 React 包装 | 🔴 **排除**：AGPL-3.0 + 商业双许可（README 与 LICENSE.md 原文核对：「Commercial license required for commercial products」，上游留痕）——本项目 MIT 公开仓且生产已有真实陌生用户，商用需购许可 |
| intro.js | github.com/usablica/intro.js | 23,457★ / pushed 2026-09-21；npm 8.6.0，周下载 153,017 | 老牌 vanilla tour：DOM data-intro/data-* 属性驱动 + JS API，零 React 依赖 | 🔴 **排除**：npm license 字段实测 AGPL-3.0（上游留痕），商用同样受限；且 data-* 属性驱动老派 API 与 React 19 声明式范式配合生硬 |
| @reactour/tour (reactour) | github.com/elrumordelaluz/reactour | 4,093★ / pushed 2026-05-19；npm 3.8.0 最后发布 2025-05-07（约一年半未发版），82 open issues | React hook 式 tour（useTour + mask/popover 分包），peer react 16.x-19.x | MIT 且 peer 支持 19.x，但活跃度不达「剔除年久失修」标准，仅作兜底备选，不推荐 |

#### 模式要点

- **任务清单式 checklist 是「覆盖全部模块」的正确载体**：PostHog（40.5k★，2026-10-28 仍活跃，gh api 实测）的 onboarding 为自建 React 组件族（frontend/src/scenes/onboarding/ 下 OnboardingStep.tsx / OnboardingFlowHost.tsx，按产品模块分子目录），不依赖任何 tour 库——分阶段可跳过、可回续比一次性长 tour 更适合 19 个页面的体量。
- **声明式 steps + 生命周期 hooks 是 tour 引擎的通用内核**：每步 = 目标元素 + 浮层配置，教学内容与业务组件解耦，hooks 挂进业务生命周期。
- **spotlight 引擎本质是「全屏遮罩+目标镂空+浮层定位」**：自建工作量集中在 scrollIntoView 与 resize 后重新定位——react-joyride issue #1202（Initial step does not show spotlight + unresponsive to resize）即此类典型缺陷，无论选库还是自建都要把 resize/滚动用例纳入测试。
- **完成状态按用户持久化 + 显式重放入口**：tour/checklist 进度绑定 user_id 存后端（本项目已有按用户隔离的 API 面），侧栏放「使用帮助」可随时重放，避免首次弹窗错过即永久错过；与 v2 需求 5 的网站公告互为静态/动态两层。
- **空态即教学（上下文教学优先于全局长 tour）**：各模块空态页内联一句话说明 + 示例入口；全局长 tour 只讲一遍信息架构骨架（侧栏四分组怎么用），细节教学留给各页。
- **选型前必查 LICENSE**：主流 tour 库 AGPL 化是既成事实（shepherd.js、intro.js 均 AGPL-3.0 双许可），MIT 仓 + 生产真实用户的项目引入前必须核对该字段。

#### 结论

引 **driver.js**（MIT、零依赖、2026-10-18 仍发版、周下载最高）做 spotlight tour 引擎并自写薄 React 19 封装，任务清单式 checklist 与进度持久化按 PostHog 自建模式实现；排除 shepherd.js 与 intro.js（均 AGPL-3.0 商业双许可）和 @reactour/tour（一年半未发版）。

### 主题二 · 站点公告与使用说明

**服务对象**：已裁决 v2 第 5 项「admin 可编辑网站公告（含使用说明+用户导引+发现 bug 联系 QQ 2053769154）」。

#### 候选项目表

| 候选 | URL | 活跃度（gh api 实测） | 提供什么 | 适配度 |
|---|---|---|---|---|
| **Open WebUI（banners 模块）** | github.com/open-webui/open-webui | 157,043★ / pushed 2026-10-28 / issues 220（**2026-10-29 撰写环节复跑：157,054★ / pushed 2026-10-28 / license=NOASSERTION / open_issues 217——一致；open_issues_count 口径含 PR，±3 正常波动，见章首留痕**） | 管理端 banner CRUD（router 用 get_admin_user 守卫）+ 用户侧可关闭横幅；数据模型 BannerModel（backend/open_webui/config.py:2208-2215）：id / type(info\|warning\|error\|success) / title / content / dismissible(bool) / timestamp + i18n，存 config DB 'ui.banners' 键；用户侧 dismiss 存 localStorage['dismissedBannerIds']（Navbar.svelte:59-67） | **适配度最高**：同为自托管 LLM 聊天应用（技术栈最贴近），其「公告本体服务端配置表 + 每条 dismissible 开关」可直接照抄为 announcements 表；但 localStorage 关闭对多设备/多用户隔离不足（本项目有 user_key 隔离铁律），应改落库 |
| **Mastodon（announcements 模块）** | github.com/mastodon/mastodon | 50,457★ / AGPL-3.0 / pushed 2026-10-28 | 开源界最完整的公告子系统：公告 schema 含 text / starts_at / ends_at / all_day / published / published_at / scheduled_at（app/models/announcement.rb）；每用户关闭为 DB 表 announcement_mutes（dismiss API 即 AnnouncementMute.find_or_create_by!）；公开 API 只回 published+chronological | 功能超集参考源：时间窗+发布态+定时发送的字段设计值得全盘吸收；带 emoji 反应/通知联动等超出需求，工程量不可照搬（Ruby 代码库，仅抄模式） |
| **Discourse（banner topic + welcome banner）** | github.com/discourse/discourse | 48,025★ / GPL-2.0 / pushed 2026-10-28 | 两套公告机制：① banner_topic 把任一话题钉为全站横幅，与内容页天然关联；② 管理端可编辑 welcome banner（可控制可见页面）。每用户关闭状态落库：user_profiles.dismissed_banner_key 列；横幅支持到期自动撤：topics.bannered_until | 两个模式的价值来源：「每用户关闭水位落库」和「横幅到期自动下线」；公告↔长内容页的关联方式（横幅一句话+跳转）正对应本项目「公告含使用说明入口」的形态 |
| nextcloud/announcementcenter | github.com/nextcloud/announcementcenter | 52★ / AGPL-3.0 / pushed 2026-10-27 | 全站公告应用：Markdown 正文、评论与 @ 提及、群组权限、通知+动态+仪表盘三通道下发；作者自述「maintained with limited effort」 | 不可引入（Nextcloud 专属插件）；价值是「公告=一次性全量下发+用户各自已读」的模型参考，以及其功能清单反向说明本项目需求（一条可关横幅+一个弹窗）远小于一套公告系统 |
| BookStack | github.com/BookStackApp/BookStack | 19,111★ / MIT / pushed 2026-10-28（主仓已迁 Codeberg，GitHub 镜像仍活跃） | 独立帮助中心/文档平台：shelf/book/chapter/page 四层组织 | 回答「使用说明放哪」的参照物：若使用说明未来要长成多页帮助中心，BookStack 是自托管首选形态；但它是整套 PHP 平台（第二服务+第二库），对单公告模块过重，不引入 |
| AnythingLLM（announcements feed） | github.com/Mintplex-Labs/anything-llm | 67,678★ / MIT / pushed 2026-10-27 | LLM 桌面/Web 应用的公告 feed：extras/support/announcements/ 下按日期落 JSON（条目含 title / short_description / goto 链接 / date），前端拉取后以弹窗卡片呈现 | 仅借鉴模式：公告=「标题+一句话+深链+日期」的轻卡片；其公告内容托管在自家 CDN（前端消费链在本仓未取证到），作为依赖引入不成立 |

#### 模式要点

- **字段模型最小集**：id / type(info|warning|error) / title / content / dismissible / enabled / starts_at / ends_at / created_by——Open WebUI BannerModel 与 Mastodon 时间窗字段的并集，单表装下。
- **「可关闭」有两条成熟路线**：轻量=客户端 localStorage 存已关 id（Open WebUI 实证），正规=每用户 DB 水位（Mastodon announcement_mutes、Discourse dismissed_banner_key）——本项目生产已有真实多用户且明文要求数据隔离，应选**落库**：announcements 主表 + announcement_reads(user_key, announcement_id) 一张小表。
- **分级呈现不必拆模块**：横幅（普通，可关）/ 弹窗（重要，进站弹一次）/ 历史列表（沉淀）用一个 type 或 pinned 字段区分即可；Mastodon 把公告做成可回看的 API 资源而非纯横幅，证明了「沉淀层」的价值。
- **时间窗自动下线**：starts_at/ends_at（Mastodon）+ Discourse bannered_until——到期自动消失，管理端无需手动删，避免过期公告挂屏。
- **公告与使用说明的信息组织关系**：公告是时效性 push（生命周期以天计），使用说明是常驻 pull（独立路由页/帮助中心）；成熟做法是横幅正文一句话+「查看使用说明」深链（Discourse banner_topic、AnythingLLM goto 链接），不在横幅里塞长文——对应本项目：公告表加一个 link 字段，使用说明做成静态 /guide 页或公告正文长文型弹窗。
- **内容粒度要克制**：Open WebUI 用纯文本+i18n、announcementcenter 用 Markdown，均无富文本编辑器；本项目单语言场景 Markdown 渲染即可，QQ 联系方式等写进内容模板。
- **headless 方案与独立公告库均无必要**：gh search repos（announcement / announcement banner / 公告 三轮合计 44 命中）未发现面向通用 Web 应用且活跃的独立公告系统——命中的是 Rails 专属 gem（starburstgem/starburst 258★、thoughtbot/paul_revere 298★）与 49★ 纯 web component；Strapi/Directus/Payload 级 CMS 需第二服务+第二数据库，为一个 CRUD 模块引入不成立。
- **管理端交互照抄成熟形态**：列表+新增+启用开关即时生效+拖拽或上下移排序（Open WebUI 全部端点 get_admin_user 守卫 + Banners.svelte sortablejs 拖拽）——本项目已有 admin_routes.py 与 require_role("admin") 惯例，新增 announcements_routes.py 3~4 端点即可。

#### 结论

**自建、不引任何库**：announcements 单表（type/title/content_md/link/dismissible/starts_at/ends_at/enabled）+ announcement_reads(user_key, announcement_id) 已读水位表 + admin 3~4 端点 + 横幅/弹窗组件；字段模型照抄 Open WebUI BannerModel、生命周期与落库关闭照抄 Mastodon/Discourse；检索实证不存在适配 FastAPI+React 的活跃独立公告库，headless CMS 为一个 CRUD 模块引入属架构性过重。**涉新表，走项目迁移纪律**（冷停+三库 .backup+幂等演练+行数比对）。

### 主题三 · 复杂设置的渐进式披露：设置页信息架构（折叠/分组/设置内搜索）与 admin 全局视图切换 UX

**服务对象**：已裁决 v2 第 1 项「admin 默认只看自己数据+显式全局运维视图开关」与第 3 项「渐进式披露：侧栏分组+每项一句话说明」。

#### 候选项目表

| 候选 | URL | 活跃度（gh api 实测） | 提供什么 | 适配度 |
|---|---|---|---|---|
| **GitLab（gitlabhq 官方只读镜像）** | github.com/gitlabhq/gitlabhq | 24,548★ / pushed 2026-10-23 | Admin Mode 五件套：admin 默认普通视角；用户菜单「Enter Admin Mode」+ 密码再认证进入（spec/features/admin/admin_mode/menu_spec.rb 实证）；MAX_ADMIN_MODE_TIME = 6.hours（lib/gitlab/auth/current_user_mode.rb:24）；非 admin 视角访问 admin 作用域页面自动重定向回登录页；侧栏 Admin 入口仅在 admin mode 下出现 | **与 v2-1 语义完全同构**——默认收窄、显式进入、限时、可退出、入口条件显隐五件套全齐，需求 1 的首选自建蓝本 |
| **LobeChat** | github.com/lobehub/lobehub（原 lobe-chat，上游调研记录的仓库 slug） | 83,560★ / pushed 2026-10-28 / license=NOASSERTION（非标准 SPDX，原 AGPL 系） | 设置中心三件套：①侧栏 Accordion 分组（useCategory 分 Account/General/…/Developer 六组、常用组默认展开）；②设置域内搜索（scope 限定 categoryGroups，命中直接定位）；③SettingsTabs 约 20 个分类含 Advanced/APIKey | React+zustand+react-router 与本项目前端同栈、同为 LLM 应用（供应商/BYOK/语音等重设置域），需求 3「分组+一句话说明+功能全保留」最可直接照搬的自建蓝本；参考其模式即可，license 非标准勿直接拷码 |
| Cal.com | github.com/calcom/cal.diy | 49,478★ / MIT / pushed 2026-10-28 | 设置侧栏分组导航 VerticalTabItem（section→children 两级），布局层读 UserPermissionRole 做角色相关显隐 | React/TS/Tailwind 同栈，分组侧栏 IA 成熟，作需求 3 参考；⚠️ **诚实标注**：cal.com 的 impersonation 功能已整体退役（2026-03 迁移删表，横幅组件已不在源树）——不能作为需求 1 的横幅参考，仅作设置侧栏 IA 参考 |
| VS Code | github.com/microsoft/vscode | 194,385★ / MIT / pushed 2026-10-28（settingsEditor2.ts 97KB 在库） | 设置编辑器 IA 基准（源码行号实证）：搜索框 ariaLabel（settingsEditor2.ts:108）+ 带历史的搜索占位（:110）+ 筛选建议动作（:836）+ @modified 过滤器（:1983）+ 每项「已配置」is-configured 标记（:1842-1845） | 非 React 非 admin 台，但「设置内搜索+已修改过滤+每项一行描述」是渐进披露公认基准；v2 小修项「键名可读化」之后可直接借其「已自定义」徽章与 @modified 过滤做法 |
| Discourse | github.com/discourse/discourse | 48,025★ / GPL-2.0 / pushed 2026-10-28 | ①impersonation 常驻横幅组件（frontend/discourse/app/components/impersonation-notice.gjs）：显示「正在以谁身份+time_left 倒计时」+ 红色 btn-danger Stop 按钮，带过期时间戳与 user_history 审计；②站点设置页：分类导航 + 文本过滤与 onlyOverridden「只看已修改」开关 + 批量变更横幅 | 需求 1 的横幅/退出/审计参考 + 设置过滤搜索的另一实现；Ember/Rails 技术栈不同，仅取 UX 模式 |
| Metabase | github.com/metabase/metabase | 49,891★ / pushed 2026-10-28 / SPDX NOASSERTION（AGPL+商业双许可） | admin 设置导航文件夹式分组：SettingsNavItem folderPattern 把 8+ 认证子项收进「Authentication」文件夹，条目按套餐/能力条件渲染（hasSaml/hasScim 等，SettingsNav.tsx 实证） | 「按能力条件显隐、功能不删」与 v2「功能全保留、按权限渲染」同型；注意：其 admin 设置页没有全局搜索框（源码未找到，此前印象不成立如实更正），设置内搜索参考以 VS Code/LobeChat/Discourse 为准 |

#### 模式要点

- **显式视角切换而非隐式提权**：admin 默认与普通用户同视角，进入全局运维视图是显式动作（GitLab 用户菜单 Enter Admin Mode，可加密码再认证），带 6 小时时限与显式退出——「默认收窄」靠会话语义实现，不靠隐藏菜单。
- **特权视角必须全页常驻横幅**：内容=「正在以什么视角看什么 + 剩余时间 + 一键退出」，用高对比/危险色（Discourse impersonation-notice 的倒计时+btn-danger Stop），退出后跳回安全首页。
- **越权路由硬重定向**：非全局视角访问 admin 作用域页面时不静默 404，重定向到视角确认/登录页。
- **侧栏按用户旅程/熟练度分组而非按数据表分组**：组可折叠、常用组默认展开、全部功能保留在组内（LobeChat 六组；cal.com my_account→children 两级结构）。
- **设置域内搜索**：搜索范围限定设置项并直接定位（LobeChat scope=categoryGroups；VS Code 搜索框+@modified；Discourse 文本过滤+onlyOverridden）——「功能保留但少露出」不能变成「找不到」。
- **条件渲染披露而非删除**：按角色/能力显隐条目（Metabase hasSaml/hasScim；cal.com 布局读 UserPermissionRole），admin-only 项对普通用户隐藏但入口收敛在同一张导航定义表里，符合 v2「功能不收走」。
- **修改状态可见+变更集中确认**：已修改项打标记（VS Code is-configured/@modified；Discourse onlyOverridden），批量变更用变更横幅集中提交。
- **特权视角/impersonation 一开始就要带审计与完整退出链路**（Discourse 记 user_history）；反例教训：cal.com 2026-03 直接删除 impersonations 表、横幅组件随之消失——无审计闭环的特权辅助功能容易被整体退役。

#### 结论

**不引库、全部自建**：需求 1（v2-1）照 GitLab Admin Mode 五件套（默认普通视角+用户菜单显式开关+可选密码再认证+6h 时限+常驻横幅一键退出/越权重定向），特权视角带审计（见开放问题 6，附推荐：首版仅开关+横幅+越权重定向，审计先行）；需求 3（v2-3）照 LobeChat 的「Accordion 分组+设置域内搜索+分类含 Advanced」，并借 VS Code 的已配置标记/@modified 与 Discourse 的 onlyOverridden 做「已自定义」徽章与搜索定位增强。

### 主题四 · UX 审计方法论：走查发现的定级与结构化

**服务对象**：本次审计的口径声明，以及后续审计/验收的统一方法论。

#### 候选项目表

| 候选 | URL | 活跃度 | 提供什么 | 适配度 |
|---|---|---|---|---|
| **NN/g 官方方法论三篇** | nngroup.com/articles/how-to-rate-the-severity-of-usability-problems/ 等（ten-usability-heuristics、how-to-conduct-a-heuristic-evaluation） | 方法论源头权威文档（非 GitHub 仓库），WebFetch 实抓成功 | ①十大启发式完整清单（系统状态可见性/贴近真实世界/用户控制/一致性/错误预防/识别而非回忆/灵活高效/极简/错误恢复/帮助文档）；②严重度 0-4 原始定义（Nielsen 1994）：4=可用性灾难（发布前必须修复）、3=严重（高优先级）、2=轻微、1=外观、0=不认为是问题；定级三因素=频率×影响×持续性；③评估流程：3-5 名评估者独立评估、两遍法、亲和图聚合、范围收窄、输出记录结构 | 本次报告分级口径与检查维度的最终引用标准；「3-5 名评估者」对单人 AI 走查可降级为「分轮独立走查+逐条合并」，两遍法与范围收窄可直接照搬 |
| **paulunemoon/ux-audit-skill** | github.com/paulunemoon/ux-audit-skill | 10★ / pushed 2026-08-01 / MIT / 未归档 | 16 个检查维度文件（每维度写「查什么·为何重要·常见失败特征·修复长什么样」）；finding 九字段 schema（ID=维度前缀稳定编号/维度/严重度/置信度/位置/证据/用户后果/建议/工作量 S-M-L）；四级严重度+两条校准规则（按用户后果定级、频率放大：高频页 Medium 胜过设置页 High）；置信度三档 Observed/Inferred/Hypothesis；报告模板五段 | **与本次 v2 审计最贴合的单一参考**：渐进式披露/侧栏分组落在其「IA 与导航」维度、教学导引/公告落在「首启与导引」、键名可读化落在「内容与文案」，三个定制点均有现成检查文件；置信度三档与「stakes 升档」（真实陌生用户在用=情感信任风险升档）直接适配本项目 |
| EliaAlberti/ux-audit-skill | github.com/EliaAlberti/ux-audit-skill | 21★ / pushed 2026-06-12 / MIT / 未归档 | 截图走查式启发式评估（Nielsen 0-4）四遍法；严重度表（4=Critical 阻断/3=Major 多数用户挣扎/2=Minor 可感知快速恢复/1=Cosmetic）+证据规则（每条发现必须指向命名可见物、禁止推断当事实、sev3-4 必须同时有启发式引用+可见证据）；正例 P-01 机制与「8 条强发现优于 25 条注水」质量线 | 提供 0-4 制最干净的操作化中文翻译底本与「以用户目标锚定严重度」的定级纪律；其证据规则与本项目「先证明再宣称」行为域铁律同构 |
| Bergschloss/product-ux-audit | github.com/Bergschloss/product-ux-audit | 9★ / pushed 2026-06-25 / 无 LICENSE 文件 | 七阶段只读审计协议；两条核心编排纪律：范围纪律（一次只审一个 user flow，明令禁止整仓一遍过——上下文过载、发现浅、幻觉率高）与多智能体双跑流水线（每范围 runA/runB 独立并发跑，人工合并，最终跨范围去重按严重度排序） | 本项目 19 个管理页+用户控制台体量大，其「按 flow 拆轮+双独立跑后人工合并」的编排模式可直接借鉴；其余七阶段超出本次范围 |
| dungnotnull/mobile-app-uxui-audit-agent-skill | github.com/dungnotnull/mobile-app-uxui-audit-agent-skill | 6★ / pushed 2026-06-30 / 无 LICENSE 文件 | 多基线对照审计（Nielsen 0-4 + Apple HIG + Material 3 + WCAG 2.1 Mobile + ISO 9241-110）；量化评分公式 Severity=(Frequency×Impact×Persistence)/16；三条质量门（每条发现必须引用基线条款、纯主观偏好自动拒收、评级必须能从给定证据复现） | 移动端导向与本项目网页端不对口，仅取两点：「频率×影响×持续性」显式量化与「评级可复现」质量门思想；不作为维度来源 |
| denysosadchyi/ux-auditor | github.com/denysosadchyi/ux-auditor | 25★ / pushed 2026-04-23 / 无 LICENSE 文件 | 面向转化审计的完整 playbook（research→capture→findings→标注→质量门）；六条核心原则：锚定北极星指标、先研究后分析、DOM 实证（getBoundingClientRect/getComputedStyle 代替目测）、第一人称单数行文、建议必须工程师可执行（精确到类名/属性/文案/布局规则）、行业论断必须引用 | 「建议必须工程可执行」与「DOM 实证」两条直接适配本项目前端走查的证据纪律（React 组件源码可核对）；其余内容相关度低，只取原则不取流程 |

#### 模式要点

- **分级口径直接采用 Nielsen 0-4 原始定义并写入报告开头**；每条定级必须过三因素频率×影响×持续性，paulunemoon 校准规则补充频率放大：高频页面的 Medium 胜过设置页的 High（适配「聊天页 vs 高级设置页」的权重关系）。
- **严重度锚定用户目标**：先以真实用户身份把任务走一遍（两遍法第一遍只熟悉不评估），与当前目标无关的启发式违反很少超过 2 级；动手定级前先声明 stakes（真实陌生用户在用→情感与信任类问题按升档处理）。
- **finding 统一九字段**：ID（维度前缀+稳定编号，跨报告可比）/维度/严重度/置信度（Observed=亲眼所见、Inferred=由代码或截图推断、Hypothesis=待验证假设）/位置（页面·组件·file:line）/证据（引用可见原文，不虚构）/用户后果（禁止拿规则编号当理由）/建议（具体到文案与交互）/工作量 S-M-L。
- **证据硬规则**：只读前端源码的发现其渲染行为只能标 Inferred 不得当断言；sev3-4 必须同时有启发式编号引用+可见证据；时延/键盘/读屏等交互类性质单独列「本次走查不可评估」清单而不是猜。
- **检查维度=Nielsen 十条打底，按本项目补三组定制**：信息架构与导航（渐进式披露/侧栏四组/全员可达）、首启与导引（教学导引/公告/一句话说明）、内容与可读性（键名可读化/Invalid Date/开发者警告隐藏）。
- **范围纪律**：一次只审一个 flow 或页面群，禁止整站一遍过；体量大时按 flow 拆多轮、双独立跑后人工合并再按严重度全局排序（Bergschloss 模式）。
- **结构化收尾件固定五段**：范围与证据底座（审了什么/没审什么及原因）→执行摘要→Quick wins 表→按维度分节（零发现维度也写一行查了什么，防跳过嫌疑）→优先级 backlog；另写 2-5 条正例做校准。
- **发现三分类防混级**：Defect（坏了/误导/阻断，可断言）·Opportunity（能用但可更好）·Taste（合理人士会分歧——明确标 taste 且禁止定 Blocker/High）；尊重团队有意取舍——正好承载「BYOK/语音引擎用户主权不可收走只能优化组织」的设计哲学约束。
- **反注水质量门**：8 条有证据的强发现优于 25 条凑数；零发现的框架就写零发现；纯主观偏好不入册；评级必须能从给定证据复现。

#### 结论

不引任何库或依赖，后续审计报告直接采用组合口径——分级用 NN/g Nielsen 0-4 原文定义（附三因素频率×影响×持续性校准与频率放大规则），发现结构用 paulunemoon 九字段 schema（置信度三档区分「亲眼所见/代码推断/待验证假设」），检查维度用 Nielsen 十条打底加 IA 导航、首启导引、内容可读性三组定制维度，执行上按 flow 拆轮两遍法走查并引入 taste/defect 三分类承载 BYOK 用户主权不可收走的设计哲学约束。

> **口径注记**：本次六路走查实际采用的是 **high/medium/low 三档**（材料生成时口径），未逐条携带启发式编号与九字段中的工作量标注；主题四的 0-4 制与九字段 schema 作为后续审计与验收的**统一口径升级建议**，不回溯改写本次发现的定级。backlog 的 P0/P1/P2 映射规则已在章首显式给出（high 与 medium 两档全覆盖）以满足「评级可复现」质量门。

---

## 统一优化 backlog

> 合并六路走查与四路调研为统一清单：**40 项 = v2 已裁决基线 6 项（V2-1~V2-6）+ 新发现 34 项（NEW-1~NEW-34）**。**Effort 构成：S 20 / M 17 / L 3**（明细见各表；报告不建模人日——执行时长取决于实施窗口模式，六批构成见「建议实施批次与顺序」供主控折算排期）。全部 40 项均为增量修复/透明化/组织优化，**无任何收走或降级 BYOK、语音引擎等用户能力的条目**。

### 定级规则（供复现）

- **P0** = 满足任一：① 对生产真实用户（含普通用户）产生**错误信息、阻断核心路径或基本自助能力缺失**，且修复方向明确无需裁决；② **不可逆或全局作用域的危险操作无防护**；③ **首日旅程阻断**。
- **P1** = high 但**影响面限于 admin 单一视角**，或**修复方向需裁决/属功能决策**；或 medium 且命中**面广/高频**（全体用户路径）或**后果不可逆**（须改库或不可自助恢复）。
- **P2** = medium 且**可逆、低频、纯打磨**；或 low 档全部。
- **medium 档判别示例（同族对照）**：NEW-17（最后管理员保护，medium）落 P1——一次误操作**后果不可逆**（锁死管理入口只能改库恢复，无自助恢复路径）；NEW-20（供应商启停/封禁/关安全开关缺确认，medium）落 P2——三个操作**全部可逆**（重新启用/解封/重开开关），缺的只是确认步骤且为 admin 低频操作。同族「危险操作缺确认」因可逆性不同分落 P1/P2，非裁量例外。

**18 条 high 的逐条落级对照**（可按上规则复现，无裁量例外）：

| 发现（合并后 ID） | high 依据 | 落级 | 命中规则 |
|---|---|---|---|
| IA-1 + CC-1（NEW-1） | 普通用户必 403 且静默，开关全局作用域（verified×2） | **P0** | ②危险全局操作无防护 |
| NU-0（NEW-2） | 全产品无登出入口（verified） | **P0** | ①基本自助能力缺失，修复 S 级无争议 |
| NU-1（NEW-3） | 文案误导首日漏斗（verified） | **P0** | ①错误信息+修复方向明确（文案） |
| NU-2+4+13（NEW-4） | 扫码失败态死分支+过期不刷新（NU-2 verified） | **P0** | ③首日旅程阻断（故障路径） |
| DU-0/2/3 + AD-1（NEW-5） | 亲密等级错刻度/记忆恒 0/内部键外泄/死卡，双视角同屏（verified×4） | **P0** | ①核心页面错误信息 |
| CC-0（NEW-6） | 空键=全库改写无确认（verified） | **P0** | ②不可逆全局操作无防护 |
| AD-0（NEW-7） | 主动消息统计全死（verified 核心） | P1 | 影响面限 admin 单一视角 |
| AD-2（NEW-11） | 排障任务流断在第一步（verified） | P1 | 影响面限 admin 单一视角 |
| AD-3（NEW-12） | humanize 按用户覆盖管理流无可便捷途径（verified） | P1 | 影响面限 admin 单一视角 |
| IA-0（NEW-10） | 非活跃角色无设置入口（verified） | P1 | 未阻断——激活后即可达，属绕路非断路 |
| CC-2/3（NEW-14） | 死滑条+全局切无提示（verified×2） | P1 | 修复方向需裁决（MiMo voicedesign 能力未证实，「接线 vs 移除」待定） |
| VC-0（NEW-9） | 错误伪装空态（verified） | P1 | 触发条件为接口失败的非常态路径，且修复方案二选一（页级 vs queryClient 全局兜底） |
| DU-1（NEW-19） | 数据概览三卡恒 0（verified） | P1 | 修复路径属功能决策（接真源 M / 删卡 S，开放问题 2） |

**medium 档主要 P1/P2 落级对照**（与 high 对照表共同构成全档可复现口径）：

| ID | 严重度 | 落级 | 命中规则 |
|---|---|---|---|
| NEW-8 | medium | P1 | 面广——深链/书签/切换场景均触发，且落在产品差异化核心页（状态中心/心理画像），修复含三页语义重构 |
| NEW-13 | medium（六处断裂） | P1 | 面广——BYOK 配置反馈环覆盖保存/跳转/微信端三条路径，直接削弱核心卖点 |
| NEW-16 | medium | P1 | 面广——每个新注册用户都经过该分支（生产近两日 5 人） |
| NEW-17 | medium | P1 | 后果不可逆升档——一次误操作锁死管理入口只能改库恢复（对照 NEW-20 可逆落 P2） |
| NEW-18 | medium | P1 | 面广——全体用户必经登录页，第一屏错误态 |
| NEW-20 | medium×3 | P2 | 三操作全部可逆（重启用/解封/重开），admin 低频，缺的仅确认步骤 |
| NEW-4 分支 NU-4、NEW-5 分支 DU-12 等 | medium/low | 随主项 | 分支随合并项主级执行，主项级见表 |

**同族分级说明（NEW-5 vs NEW-19）**：同为 verified「恒 0/死卡」，NEW-5 进 P0 因为它是**状态中心核心页面的五处合批**（含情感数据被系统性错译、内部键外泄、与同屏真实数互相矛盾三类问题），普通用户与管理员双视角同屏受影响；NEW-19 是**单点展示层误导**且修复二选一需主人拍板（接真实统计源 M / 删卡 S），按规则「修复方向需裁决」落 P1——裁决后按所选路径执行，不存在降格放过。

**V2 基线优先级来源**：V2-1/V2-2→P0、V2-3~V2-6→P1 是**主控在 v2 修改清单裁决时的既定排序**（本审计任务输入，非本次走查推导），报告转述并给出合理性论证：1=管理数据面误导直接面向生产真实用户、2=归属错认直接面向生产、3/4/5 为组织/教学/公告类建设项、6 为小修但直接输出 NaN/原始键等坏文本故定 P1 而非 P2。

### P0（8 项：误导/阻断级）

| ID | 标题 | Effort | 来源 / v2 映射 | 受影响文件（要点） | 核心理由与实施要点 |
|---|---|---|---|---|---|
| V2-1 | admin 默认自见数据 + 显式全局运维视图开关 + 数据键名可读化 | L | **v2 裁决 1**（优先级为主控既定，见定级规则块） | AdminUsersPage / AdminProvidersPage / SettingsLogs / ToolsDashboard / StatusCenter / navGroups / components/admin/ | 已裁决基线；并入走查两处补充面：①全局记忆事实无归属标注（`StatusCenter.tsx:404`，返回行带 user_key 但不展示，全局视图仍分不清哪条记忆是谁的）；②工具卡标题显示英文原始键名（`ToolsDashboard.tsx:160-168`，未收录工具连描述都回退键名）。交互模式照 GitLab Admin Mode：显式开关+常驻视角横幅+越权路由硬重定向；特权视角须带审计（Discourse user_history 先例、cal.com 无审计导致 impersonation 整体退役的反例）——**NEW-21 审计为本项前置**。全部功能保留可达，不收走任何能力 |
| V2-2 | 微信接入页显示当前聊天角色 + 角色列表「聊天中」徽章 + 公共卡可读化 | M | **v2 裁决 2**（优先级为主控既定，见定级规则块） | WeChatPage / RolesPage / RoleSettings / navGroups | 已裁决基线；并入走查两处同族裸键/归属缺口：①角色设置页头卡向所有登录用户展示原始「用户: {user_id}」（`RoleSettings.tsx:69`，第 3 处裸键）；②微信页只显示「我的微信已连接」不显示绑定账号（`WeChatPage.tsx:54-66`，后端 payload 已带 nickname/bot_id），多微信号用户扫错号无从察觉，补一行「绑定账号：{nickname\|\|bot_id}」 |
| NEW-1 | 工具仪表盘启停开关对普通用户必 403 且静默无反馈 | S | 信息架构-1 + 自定义配置-1（**双重 verified**） | ToolsDashboard / navGroups / tools_routes.py | 本会话已复核：「工具仪表盘」对所有登录用户渲染启停 toggle（`ToolsDashboard.tsx:171-186`，无 isAdmin 判断），但 `POST /api/tools/{name}/toggle` 确为 require_role("admin")（`api/routers/tools_routes.py:137-142`），普通用户点击必 403；handleToggle 的 catch 块为空（`:96-108`），失败无任何 toast。开关是全局作用域（写 runtime_switches.json）却放在全员可达菜单（`navGroups.tsx:48`）。修复：非 admin 改只读状态卡（保留工具可用性可见性，对 BYOK 用户排障有价值，**不收信息**）+ toggle 失败补 error toast + 页面加「开关为全局作用域、影响所有用户」横幅 |
| NEW-2 | 控制台无退出登录入口 | S | 新用户旅程-0（verified） | components/layout/ / w11_journey.spec.ts | 走查 verified，本会话 grep 复核成立：logout 函数在 useAuth.ts:90，但全前端 UI 调用仅两处——ConsentGate.tsx:39（协议门）与 SettingsAccount.tsx:**207**（注销账号附带登出；撰写环节实读定真值：`:206` 为注释行、`:207` 为调用行），侧栏/顶栏/账号页零独立入口。生产已有真实陌生用户，共用电脑或换号场景无法自助退出会话。修复：顶栏加用户菜单（身份展示+退出登录），纯增量不涉能力收缴 |
| NEW-3 | 介绍页「邀请码制」文案与开放注册实况矛盾，误导首日漏斗 | S | 新用户旅程-1（verified） | IntroPage | 走查 verified：介绍页称「注册采用邀请码制」并引导先联系 QQ 取码（`IntroPage.tsx:141`），而**代码实况符合 D13「开放注册主链路+邀请码并存」**——/api/auth/register 无门（=主链路开放，`auth_routes.py:162-241`）、注册页邀请码可选勾选（`LoginPage.tsx:51-53`）、invite 独立端点并存（`invite_routes.py:120`）。问题**仅是介绍页把可选通道说成制度前提**，文案与行为相悖（不是代码违背 D13）。每个新访客可能被误导离开注册页去取码——生产近两日 5 个新注册正走此路径（来源见头部生产数据声明；是否实际被误导见「生产取证待办」）。修复：文案对齐（S 级，可直接做）；是否保留邀请码为注册门槛（后端补门）属另一方向，见开放问题 1（推荐仅改文案）。QQ 联系方式保留，改挂为「反馈/人工协助」 |
| NEW-4 | 扫码弹窗失败态死分支 + 拿码后过期永不刷新（补状态机 e2e） | M | 新用户旅程-2（verified）+ 4（unconfirmed）+ 13（unconfirmed）合并 | WeChatPage / frontend/e2e/ | 本会话亲验 WeChatPage.tsx：①全文 setStatus 仅五态、无任何路径置 'error'，error 分支死代码，轮询失败被空 catch 吞掉（`:172-174`、`:196-198`），后端/微信平台故障时首日用户第一步只见无限「获取二维码中…」骨架；②is_expired 检查只存在于取码轮询（`:192`，实读确认），拿到二维码即 clearInterval 切入连接轮询（实读 `:190-191`），后者完全不检查过期 → 延误扫码后旧码永远显示「等待扫描」。项目 L2 教训明示微信接口易变。修复：连接轮询同步检查 is_expired 自动置 expired 重取、连续 N 次失败置 error + 重试按钮、loading 态给重试入口；补 QR 弹窗状态机 e2e（page.route mock 后端三端点即可覆盖全状态，含 error 分支回归）。⚠️ 合并项含 unconfirmed 分支（二维码过期/e2e 缺口），实施前按批 0 复核 |
| NEW-5 | 状态中心死数据/错刻度五处（亲密等级、记忆条目、情景归档键、最近沉淀、工作记忆口径） | M | 日常使用-0/2/3（verified）+ 管理员旅程-1（verified）+ 日常使用-12（unconfirmed） | StatusCenter / api/routers/ | 状态中心四处死数据/错刻度 + 一处口径含混：①「亲密等级」把后端 0-8 等级序数按 0-100 阶段区间解释，关系到顶也恒显「陌生」（`StatusCenter.tsx:69-72`）——改用 0-100 刻度或直接消费 health_check 的 affinity_name；②「记忆条目」依赖的 count_facts() 全仓无定义（本会话 grep 确认仅 misc_routes.py:181-182 以 hasattr 守卫调用、无任何 def count_facts），数字恒 0，与同屏右侧「长期记忆」真实数矛盾——补计数真源（**须保持 per-user 隔离口径，普通用户不得见全库计数**）并与第③层口径对齐；③「情景归档」日期渲染 session_id\|character_id\|YYYY-MM-DD 完整内部键（`StatusCenter.tsx:384`）——端点 rsplit 只回纯日期，内部键不外泄。⚠️ **与已裁决 V2-6 第三子项「情绪归档键名可读化」为同一现象，实施口径以此处为准**（见下条冲突消解）；④「最近沉淀」过滤 f.content 但 /api/memory/facts 两分支行键都是 fact，恒空成死卡（`StatusCenter.tsx:406`）——改读 fact 键并顺带用 user_key 做归属标注（与 V2-1 同批）；⑤「工作记忆」文案写「当前会话」数据却是全局计数（`:316`）——标注口径或改会话级计数。普通用户与管理员双视角都受影响，核心页面误导集中地。⚠️ 分支⑤为 unconfirmed，实施前按批 0 复核。**③与 V2-6 的冲突消解**：两者修复方向不同（③=端点侧净化使内部键不出 API；V2-6 字面=前端键名可读化）——统一为一个实施点：按 ③ 的端点侧 rsplit 回纯日期（数据源头净化），V2-6 的「前端可读化」对该子项随之自动满足（纯日期即的可读形态），**不重复做两遍**；V2-6 其余两子项（Invalid Date、剧情线警告）不受影响。同落批 2 的同批次内一次做完 |
| NEW-6 | 「整理记忆」空键 = 全库改写且无确认 | S | 自定义配置-0（verified） | RoleSettingsTabs / agent_plane_routes.py | 走查 verified：「整理记忆」按钮在 session_key 留空时调用 agentPlaneCurate(undefined)（`RoleSettingsTabs.tsx:833-841`、`:1018`），后端语义是对全部会话执行 apply=true 的记忆整理——改写事实库与派生召回（`agent_plane_routes.py:114-159`），但 UI 无二次确认、无作用域文案，按钮看起来只作用于当前角色。不可逆全局写操作必须二次确认并明示「将整理全部会话的记忆」，或强制先填会话键 + 按钮旁标注作用域。⚠️ **追溯性取证待办**：生产上该按钮是否已被点过、记忆库是否已被空键 apply=true 改写过，本审计未取证（需生产 event_ledger/agent_plane 账本访问），见「生产取证待办」。注意：前端确认只是拦截层，后端全库语义是否收敛为显式作用域参数见实施风险 7 |

### P1（17 项：显著体验提升）

| ID | 标题 | Effort | 来源 / v2 映射 | 受影响文件（要点） | 核心理由与实施要点 |
|---|---|---|---|---|---|
| V2-3 | 渐进式披露：侧栏四分组 + 每项一句话说明（功能全保留） | L | **v2 裁决 3** | navGroups / Sidebar / MobileDrawer / SettingsLogs / RoleSettingsTabs / TopicStateSection | 已裁决基线；走查提供现成论据与落点，实施时一并消化：①「系统设置」组名对用户过重（`navGroups.tsx:43-51`，6 项中 5 项实为个人数据/个性化，与 BYOK 卖点相悖）；②日志菜单项无口径说明（普通用户仅见本人请求级日志，`api/routers/misc_routes.py:347-368`）；③日志页双视角无作用域标识（`SettingsLogs.tsx:111`）；④消息 tab 九区块日常与开发级混排、θ1/θ2 缺白话因果（`RoleSettingsTabs.tsx:885-1186`、`TopicStateSection.tsx:266-284`）——tab 内按「日常调频/拟人化节奏/话题引擎/运维工具」分层或折叠高级组，参数白话做主文案、术语降为副标注。参考：LobeChat（Accordion 分组+设置域内搜索）、VS Code（@modified 已配置标记）、Discourse（onlyOverridden） |
| V2-4 | 完整功能教学导引（覆盖全部模块）+ 控制台帮助位可重放 | L | **v2 裁决 4** | IntroPage / navGroups / WeChatPage / RolesPage / e2e | 已裁决基线；实施落点：①五步教程+免费 Key 申请引导现成于公开 /intro（`IntroPage.tsx:168`），登录后控制台零入口，应收进侧栏「使用帮助」并可随时重放（进度按 user_id 持久化，避免首次错过即永久错过）；②无活跃角色时「状态中心/心理画像/剧情线」整组隐藏且无解锁提示（`navGroups.tsx:32-39`），教学首页需一句话点明；③扫码成功态缺「现在去微信发第一条消息」接力（`WeChatPage.tsx:289`），纳入首日导引链。载体用 checklist 任务清单而非一次性长 tour：19 页体量下分阶段可跳过可回续更合适；spotlight 细节教学留给各页空态。选型必须核 LICENSE（driver.js MIT，撰写环节已复跑复核；shepherd.js/intro.js 均 AGPL-3.0 双许可须排除），resize/滚动重定位用例纳入测试（react-joyride issue #1202 教训） |
| V2-5 | admin 可编辑网站公告（使用说明 + 用户导引 + QQ 反馈渠道） | M | **v2 裁决 5** | api/routers/ / frontend api / components / pages | 已裁决基线；调研结论为**自建不引库**（无适配 FastAPI+React 的活跃独立公告库，headless CMS 过重）：announcements 单表（type/title/content_md/link/dismissible/starts_at/ends_at/enabled）+ announcement_reads(user_key, announcement_id) 已读水位表（生产有真实多用户且要求隔离，选落库而非 localStorage）+ admin 3~4 端点（复用 require_role("admin") 惯例）+ 横幅/弹窗组件；时间窗自动下线免手动删；横幅正文一句话 +「查看使用说明」深链（link 字段指向 V2-4 的 guide 页），与教学导引互为 push/pull 两层。**涉及新表，走项目迁移纪律** |
| V2-6 | 小修包：Invalid Date 收敛 + 剧情线开发者警告用户态隐藏 + 情绪归档键名可读化 | S | **v2 裁决 6** | UserTable / StatusCenter / StorylineEditor | 已裁决基线；并入同族一处：用户表 formatDateTime/formatRelative 对非法时间串无 isNaN 守卫，会输出「NaN-NaN-NaN」式文本（`UserTable.tsx:47`），建议抽公共 formatDate 工具与已裁决两处一并收敛。定 P1 而非 P2：直接向用户输出 NaN/原始键等坏文本属误导级展示。⚠️ **第三子项「情绪归档键名可读化」与 NEW-5③ 为同一现象**——实施口径以 NEW-5③ 的端点侧净化为准（冲突消解见 NEW-5③ 行），本项对该子项仅做前端适配验证，不重复实现；其余两子项（Invalid Date 依赖 NEW-7 先落、剧情线警告）照常执行 |
| NEW-7 | 主动消息历史字段契约错位：kind/created_at，统计卡全死 | M | 管理员旅程-0（verified）+ 日常使用-5 + 管理员旅程-12（后两条 unconfirmed 分支） | RoleSettingsTabs | 前端读 h.type/h.at（`:708` 类型、`:746-747` 赋值、`:1178/:1180` 渲染，本会话已复核前端侧），生产路由实际返回 {kind, created_at}（epoch 秒浮点）→「今日主动」恒 0（`:820`，且该行用 new Date().toISOString() 取 UTC 日界，违项目 local_time 纪律）、「最后发送」恒 —（`:821`）、时间列恒 Invalid Date。⚠️ 已裁决 V2-6 的「Invalid Date」若只改格式化不修此契约，统计仍是死的——本项是前置。修复：按真契约改读 kind/created_at + epoch 秒换算 + 本地日界统计（local_day_utc_bounds 先例）+ 补钉真实响应形状的单测；顺带统一「消息总数」（本角色）与「今日主动/最后发送」（全站）的口径标注（`:1027`，对应管理员旅程-12 unconfirmed 分支），修复后此口径问题才显形 |
| NEW-8 | 角色上下文语义三处不一致：URL 参数被忽略 + 静默回退首卡 + 面包屑错位 | M | 信息架构-2 + 新用户旅程-12（均 unconfirmed） | StatusCenter / PsychProfilePage / useQueries / Breadcrumb | /roles/:roleId/status 与 /psych 带路由参数，但 StatusCenter/PsychProfilePage 均不读 useParams、数据一律取 useActiveCharacter()（`StatusCenter.tsx:51`、`PsychProfilePage.tsx:101`），同族 StorylinePage 却按 URL 解析角色——同一路由族三种语义；且 useActiveCharacter 在无激活时静默回退 characters[0]（`useQueries.ts:131`，可能是公共池卡），而 Breadcrumb 按 URL 的 roleId 显示角色名（`Breadcrumb.tsx:69-85`）→ 访问 /roles/A/status（A 非活跃）时面包屑写 A、页面渲染 B 的情绪/亲密度，标签与数据错位。修复：三页统一按 URL roleId 取数（推荐，与 StorylinePage 一致），回退场景不展示角色子页入口或显式标「未激活」。P1 依据：medium 面广（深链/书签/切换场景均触发、产品差异化核心页），见 medium 落级对照表。**实施前先按实况复核（批 0）** |
| NEW-9 | 查询错误被伪装成空态或零值（isError 未消费） | M | 视觉一致性-0（verified） | RolesPage / PsychProfilePage / StatusCenter | 走查 verified：react-query 页面普遍只消费 isLoading/data 不消费 isError（`RolesPage.tsx:72` 为代表），接口失败时页面渲染成「暂无角色」「画像尚未生成」等笃定空态或 0 值；全局 toast 约 4 秒消失后页面残留误导状态。修复：三页补 isError 分支（错误卡+重试按钮），或在 queryClient 挂全局错误兜底；空态仅在请求成功且数据为空时渲染。不改变任何能力入口。P1 依据：触发条件为接口失败的非常态路径 + 修复方案二选一（见 high 落级对照表） |
| NEW-10 | 角色页入口补全：非活跃角色无设置入口 + 空态缺模板入口 | S | 信息架构-0（verified）+ 新用户旅程-11（unconfirmed） | RolesPage / navGroups | 角色卡片唯一交互是「设为活跃」（`RolesPage.tsx:12-67`），而「角色设置/状态中心/剧情线」侧栏入口仅在 activeRoleId 存在时渲染（`navGroups.tsx:32-39`）→ 想编辑非活跃角色只能先激活——而激活是切换聊天对象的全局副作用；RoleSettings 页头本就支持渲染「未激活」状态（`RoleSettings.tsx:70-73`），证明设计预期可编辑非活跃角色，只是入口链没接上，URL 直达之外 UI 全断。修复：RoleCard 增加指向 /roles/:id/settings 的次要入口（不改变活跃态）；顺带角色空态并列「从模板挑一个」次按钮（`RolesPage.tsx:126`，unconfirmed 分支）。P1 依据：未阻断（激活后即可达），见 high 落级对照表 |
| NEW-11 | 查用户问题无下钻，通道运维前端零入口（后端已就绪） | M | 管理员旅程-2（verified） | api/admin.ts / AdminUsersPage / components/admin/ | 走查 verified：「查一个用户的问题」任务流断在第一步：用户列表只有账号字段（AdminUser，`admin.ts:15-25`），行操作只有编辑与删除；而后端早已具备 admin 通道总览与强制断开端点（`wechat_channel_routes.py:267-331`、`admin_routes.py:118-129`），前端全量 grep 零消费者——纯死接口，站长排障缺下钻入口。修复：用户列表行展开/详情抽屉接入单用户详情+通道状态（连接徽章、slot、bot_id），force-disconnect 带确认弹窗——后端已就绪，纯前端补面，直接服务「按人查问题」主线（与 V2-1 全局视图、NEW-5④归属标注互为拼图，V2-1 的 user_key→用户名映射是公共底座） |
| NEW-12 | 拟人化要求手输内部会话键且无可便捷查键途径，管理流实际受阻（话题状态可绕行但重度不便） | M | 管理员旅程-3（verified）+ 自定义配置-5 + 日常使用-13（后两条 unconfirmed） | HumanizeSection / TopicStateSection / training_routes.py | 拟人化「按用户覆盖」要求管理员手输 N:wxid@im.wechat 完整内部会话键（`HumanizeSection.tsx:441`），而产品内没有任何**便捷、可发现**的查键界面、humanize 端点亦未做还原（`training_routes.py:677-740` 对 user_key 原样使用；**还原器 `_resolve_admin_target_key` 就在同文件 `:338,363-369`，humanize 端点未调用**）——普通站长无从获得键值，「按用户调节奏」管理流实际受阻（verified；日志取证路径亦未证实可行，见管理员旅程-3 取证注）。话题状态「会话快照」同款手输框（`TopicStateSection.tsx:301-309`），但后端复用该还原器（`topic_routes.py:73-77`）且前端 `:298` 有提示——已绑微信用户可输用户管理页可见的数字 id 绕行，**属重度不便而非不可用**。同 tab 的回复模式已用 adminListUsers 用户下拉 + 后端数字 id→N:wxid 还原、无绑定宁拒绝不猜键（`RoleSettingsTabs.tsx:1102-1116`、`training_routes.py:351-369`），**复制该范式即可**；内部键只在详情里出现，与 V2-1 键名可读化同向（V2-1 的 user_key→用户名映射是公共底座）。因果回放手输 session_key（`:441` 同区）同批收敛 |
| NEW-13 | SettingsLLM 配置反馈环六处断裂（BYOK 测试连接/byok 参数/微信端指引/Key 状态/禁用供应商/缓存键） | M | 新用户旅程-3 + 自定义配置-7/8/14（均 unconfirmed） | SettingsLLM / client.ts | BYOK 是核心能力与用户主权（最高约束），本项全部为**增量引导与透明化、不收走任何能力**：①保存 API Key 无连通性校验也无「测试连接」，错 Key 也显示「设置已保存」，错误延迟到聊天才爆（`SettingsLLM.tsx:133`）；②403 自动跳 /settings/llm?byok=1 但全前端无人读取该参数（`client.ts:123-135`）；③byok_required 时微信端首条消息失败只有角色化兜底句，无「去控制台配 Key」指引；④API Key 已配置状态不可见——'****' 回显被转空串，「从未配置」与「已配置」界面完全一样（`:113`、`:152-153`，SettingsLLM.test.tsx:48 钉了该行为，改时连带测试），补「已保存密钥」徽标不回显明文；⑤已禁用供应商仍可选中并保存成功、无即时警示（`:246-268`）；⑥缓存未接线横幅诚实但下方控件仍可交互保存（`:396-424`），可置灰或回执追加「cache 键当前不生效」。P1 依据：medium 面广（覆盖保存/跳转/微信端三条路径），见 medium 落级对照表 |
| NEW-14 | 语音设置页四处：死滑条 + 选中态失真且 admin 点击即全局切 + 音色无法命名 + 参考音频无隐私提示 | M | 自定义配置-2/3（verified）+ 日常使用-10 + 自定义配置-9（后两条 unconfirmed） | SettingsVoice / mimo.ts / mimo_voice_routes.py | 语音是用户主权能力，修复全部为**透明化与补能力**：①「语音设计」音调/语速滑条采集后从未发送，纯死控件（`SettingsVoice.tsx:347-379`、`mimo.ts:46-60`、`mimo_voice_routes.py:232-235` 只收四字段）——支持则接线、不支持则如实标注「当前版本不生效」，不得保留静默假控件；②「可用语音」高亮只是列表第一项而非平台当前默认（`:518`），admin 点击即静默调 mimoSwitchVoice 改全局 TTS 单例影响所有用户、无作用域提示（`:539-553`）——选中态用 /mimo/status 真值回填 + 点击时明示「将设为全局默认音色」（对齐 MessageTab 作用域横幅纪律，**不收能力只加透明**）；③设计流音色硬编码 design_${Date.now()} 无法命名（`:284`），加名称输入与克隆流对齐；④克隆参考音频无理想时长/50MB 上限/「音频将上传 MiMo 云第三方」隐私提示（`:188-253`），对齐项目「透明操作」偏好。P1 依据：分支①的修复方向需裁决（MiMo voicedesign 能力未证实），见 high 落级对照表 |
| NEW-15 | 心理画像可读性三处：英文 level 直出 + 内部 scope 键暴露 + 稳定度恒 0%/100% | S | 日常使用-6/7/8（均 unconfirmed） | PsychProfilePage | ①情绪信号卡直接渲染后端英文 level 原文与无解释的 evidence 数值（`PsychProfilePage.tsx:91`），同文件 RiskBadge 已有中文映射未复用；②页头说「角色 X 对你的理解」，正文却原样渲染 {character_id}:{session_id} 内部键串（`:200`）——改「角色名 × 会话序号/时间」人话、原始键收进 title；③快照时间线稳定度先四舍五入再乘 100，只会显示 0%/100% 两档（`:320`），把乘 100 移进 Math.round 括号内。P1 理由：该页是产品差异化核心且面向普通用户，内部键暴露与英文直出同属键名可读化家族。**实施前先复核（批 0）** |
| NEW-16 | 初始角色状态无持久告知：成功仅 4 秒 toast、失败全程静默 | M | 新用户旅程-6/7（均 unconfirmed） | RolesPage / useAuth | 注册自动分发并激活的初始角色（后端已克隆+绑定激活）只在 4 秒 toast 提示一次，且与全屏协议门同帧出现（`useAuth.ts:55`）；此后控制台无任何持久位置告知「你已有角色 X 且已激活」，与 IntroPage 教程第 3 步仍教「创建或选择角色」脱节。反向分支同样静默：分发失败按契约降级 initial_character=null（`character_template_routes.py:204`），前端无名即不提示，微信端角色解析落 "default"。修复：角色页给初始卡「已为你准备好」角标 + initial_character=null 时给一次性「先设为活跃」引导（不阻断注册），与 V2-4 教学首页互补。P1 依据：medium 面广——每个新注册用户都经过此分支（生产近两日 5 人，来源见头部生产数据声明），见 medium 落级对照表 |
| NEW-17 | 无最后一个管理员保护，一次误操作即锁死管理入口 | S | 管理员旅程-7（unconfirmed） | admin_routes.py / UserForm | update_user 允许把任何 admin（包括自己）降权为 viewer、无「至少保留一个 active admin」守卫（`admin_routes.py:202`），前端编辑自身账号时角色下拉也可选观察者且无警示。当前单管理员站一次误操作即失去全部管理入口，只能改库恢复。修复：后端拒绝降权/停用最后一个 active admin（S 级），或至少前端对自身降权追加二次确认+后果说明；守卫须同时覆盖降权与停用两条路径。P1 依据：medium 后果不可逆升档（对照 NEW-20 可逆落 P2），见 medium 落级对照表 |
| NEW-18 | 登录注册错误英文直出，产品第一屏错误态不本地化 | S | 新用户旅程-5（unconfirmed） | LoginPage | 密码错误/账号停用/邮箱用户名冲突时，后端英文 detail 原样显示在错误框给中文用户（`LoginPage.tsx:67`），是产品的第一屏错误态。修复：前端按状态码映射中文文案，保留安全模糊表述（不泄露「用户不存在 vs 密码错」的枚举差异）。P1 依据：medium 面广——全体用户必经登录页，见 medium 落级对照表 |
| NEW-19 | 角色「数据概览」三卡是死数据：消息/记忆恒 0 | S | 日常使用-1（verified） | RoleSettingsTabs | 「数据」tab 的消息总数/记忆条数/平均响应三卡读 character.stats，但后端角色详情根本不返回 stats（`RoleSettingsTabs.tsx:1292-1294`），用户聊几百条后永远看到 0/0/—。接真实统计源（chat_history 按会话/角色计数，M）或删卡（S）二选一，需裁决（开放问题 2，附推荐：先 S 级止血、真源另立批次）；无论哪条路都不得保留占位数字误导。P1 依据：修复路径属功能决策（见 high 落级对照表） |

### P2（15 项：打磨）

| ID | 标题 | Effort | 来源 | 受影响文件（要点） | 核心理由与实施要点 |
|---|---|---|---|---|---|
| NEW-20 | 高危管理操作缺二次确认与后果说明（供应商启停/封禁用户/关闭内容安全） | M | 管理员旅程-4/6 + 自定义配置-12 | AdminProvidersPage / UserForm / SettingsSecurity | 三类高危全局操作一键直生效无确认：①供应商禁用/启用（`AdminProvidersPage.tsx:285`，禁用主供应商改变全体用户可用模型与回退路径；且 /all 返回的 fallback_chain 与 default_provider 前端从不展示，页头应加回退链徽章序列）；②封禁用户藏在编辑弹窗「活跃」checkbox，保存即生效并立即撤销该用户全部 token、全程无确认（`UserForm.tsx:134`，删除反而有确认）；③内容安全过滤器总开关关闭全局生效、降低所有用户防护（`SettingsSecurity.tsx:102-127`）。修复：仅在危险方向（禁用/关闭/停用）加一次确认 + 后果说明，不动开启路径，不收能力。P2 依据：三操作全部可逆、admin 低频（对照 NEW-17 不可逆落 P1），见 medium 落级对照表 |
| NEW-21 | admin 危险操作审计缺操作者身份 | M | 管理员旅程-5 | admin_routes.py | 建用户/改用户/删用户与供应商增删改启停的日志只记对象不记操作者（`admin_routes.py:249`），也无结构化审计存储；同仓微信强制断开却记录了 admin=%s（先例在案）。修复：admin 写操作统一在 require_role 依赖处把操作者注入日志上下文（复用 UserContextFilter 模式），或落 admin_audit 表。P2 理由：当前单管理员风险低，但**实施顺序上排在 V2-1 之前**（批 3 先审计后开视图，调研教训：特权辅助功能无审计闭环容易被整体退役） |
| NEW-22 | 用户列表排序只作用于当前页 | M | 管理员旅程-8 | AdminUsersPage | 表头排序只重排当前页已加载的 20 行，箭头却呈全局排序态（`AdminUsersPage.tsx:114`，代码注释自认此缺陷未修）；跨页找「最早注册」「某邮箱开头」的用户时结果误导。修复：后端补 sort/sort_dir 参数（用户量增长前做完），或前端排序时改为全量拉取/隐藏排序交互 |
| NEW-23 | 日志页：清屏 5 秒回灌 + 环形缓冲按 worker 隔离无标注 | S | 管理员旅程-9/11 | SettingsLogs / logging_setup.py | ①「清屏」只清空前端数组，5 秒轮询下一次 doFetch 全量替换即恢复原样（`SettingsLogs.tsx:82`）——与「暂停」联动；级别/搜索过滤可下推服务端 level/search 参数减少无谓传输。②/api/logs 真源是每进程 200 条内存 deque（`logging_setup.py:54`），生产 4 worker 部署下 admin 只能看到接住该请求的 worker 的切片，高流量下数秒滚没——至少页面标注「仅当前 worker」，跨 worker 聚合读留待后续立项 |
| NEW-24 | 骰子闸区块无保存入口指引，改动易丢失 | S | 自定义配置-4 | RoleSettingsTabs | 骰子闸 10 个键改完后该区块内没有任何保存按钮；唯一保存按钮在隔四个区块的「频率控制」区，文案叫「保存频率配置」（`RoleSettingsTabs.tsx:940-1009`、`:1144-1152`），完全看不出它也保存骰子闸。修复：区块尾加「改动需点击下方『保存频率配置』生效」提示，或提升为 tab 页级单点保存并标注覆盖全部区块 |
| NEW-25 | 训练进度五端点路径前端零消费（死 API 面；后端路由仍注册） | S | 信息架构-6 | api/training.ts / useQueries / client.ts / training_routes.py | 前端：**五个函数对应五个端点路径**（撰写环节实读 `training.ts` 全文对账：trainingStatus→GET `/training/status`、trainingProgress→GET `/training/progress`、trainingClean→POST `/training/clean`、trainingTest→POST `/training/test`、trainingApply→POST `/training/apply`；上游材料「四端点」系计数笔误，本版已对账）与 useTrainingProgress hook 在 pages/components 全量 grep 零 UI 消费。**后端实况（撰写环节实读补验）**：training_router 仍注册且与 proactive 端点**同挂一个 router**（`api/app_factory.py:269` 注释「13 端点: training/* + proactive/*（含手动控制）」、`:35` import），training_status 端点存活（`api/routers/training_routes.py:71`）——后端非死码，但 training/* 无 UI 入口。二选一：删前端 API 面（training.ts + hook + client.ts 转出口）+ 后端仅收敛 training/* 端点（**不能删整 router**，proactive 端点同挂），走 DELETION_LOG；或角色设置数据 tab 补训练进度只读卡。方向需裁决（开放问题 3，附推荐） |
| NEW-26 | 导航命名不一致 + 面包屑三个死映射残留 | S | 信息架构-3/4 | Breadcrumb / navGroups / WeChatPage / NotFoundPage / RoleSettingsTabs | ①面包屑三个死 tab 映射：settings 的 general/extensions 与 admin 的 logs（`Breadcrumb.tsx:27-35`），对应路由已删、直接访问落 NotFoundPage、全站无其他引用——随 V2-3 信息架构批一并清掉；「扩展管理」若有回归计划应恢复路由而非留映射（开放问题 7，附推荐：默认删）。②命名三件套不一致：微信页「连接/微信连接/微信接入」三种叫法（`navGroups.tsx:24-25` vs `WeChatPage.tsx:366`）；语音菜单「语音引擎」vs 页面「语音工作台」且角色语音 tab 文案让用户按菜单名找不到（`RoleSettingsTabs.tsx:515,522`）；NotFoundPage 按钮「返回仪表盘」但产品无仪表盘页（`NotFoundPage.tsx:11`）。各收敛一个词，交叉引用文案与菜单名对齐 |
| NEW-27 | 已连接后无断开/换绑微信自助入口 | S | 新用户旅程-9 | WeChatPage / system.ts | 已连接用户在界面上无法断开或换绑微信：前端已封装 disconnect/reconnect API（`system.ts:194`）但零页面消费，后端断开端点存在——绑错微信号的陌生用户无自助路径。修复：已连接态提供「断开连接」入口（带确认）。涉 D16「一人一 bot 一条通道」语义，断开后重新扫码的边界需裁决（开放问题 5，附推荐） |
| NEW-28 | 微信页运行时长不进位 + SSE 注释与实现漂移 | S | 日常使用-14 | WeChatPage | ①运行时长只做分/秒除法，连接超过 1 小时显示「75 分 0 秒」（`WeChatPage.tsx:38-40`），按小时/天进位；②顶部注释宣称 SSE 实时推送，实际已隔离为 no-op、仅 15s 轮询——注释改为与实现一致，避免误导维护者与依赖注释理解状态时效的用户 |
| NEW-29 | useEmotionState 类型契约漂移，10s 轮询数据从未被消费 | S | 日常使用-11 | StatusCenter | 前端 EmotionState 类型对应 to_dict 形状，但端点实际返回 health_check 形状（`StatusCenter.tsx:68`），emotionData?.primary?.type 恒 undefined，情绪显示全靠 dashboard 兜底，10 秒一次的轮询白打。修复：端点改回 to_dict 形状或前端类型对齐 health_check，删掉死轮询；**实施前先复核端点实况（批 0）** |
| NEW-30 | 剧情线裸分钟参数 + 默认分手结局模板无来源说明 | S | 自定义配置-11 | StorylineEditor | 「每句话增加（分钟）」没有主语（增加的是剧情进度时间），阶段编辑用裸分钟数字输入（`StorylineEditor.tsx:324-344`、`:385-395`）；新启用用户直接得到一套自带「离别克制期→告别期」分手结局的五段默认模板（`:19-60`），模板意图无 UI 说明。修复：时间输入复用 fmtMin 做天/小时快捷换算 + 一句话「默认模板为示例剧情，可整体改写」 |
| NEW-31 | 组件库收编：Button/EmptyState/Skeleton 推广 + SubTabBar 死组件处置 + 加载态统一 | M | 视觉一致性 #2（Button 三套语言）/#3（加载态四形态）/#4（死组件）/#5（EmptyState）——**四条来源均 unconfirmed** | shared/ / Button.tsx / SettingsVoice / RolesPage | ①SubTabBar 全站零使用、Skeleton 仅 1 处（`SubTabBar.tsx:13`）——删除或让 RoleSettings 改用并保留 URL 真源能力，死组件走删除纪律；②基础 Button 几乎无人采用，135 处原生 button 主操作分散三种视觉（`Button.tsx:12`）——Button 对齐 btn-macaron 后逐步替换高频主 CTA、ConfirmDialog 确认钮改用 Button；③加载态四形态并存（手写骨架/Skeleton/纯文字/App 级灰块，`SettingsVoice.tsx:96` 为例），以 SettingsLLM 结构化骨架为范式统一；④EmptyState 只在 admin 域 3 文件使用，普通用户页空态手写漂移（`RolesPage.tsx:126`），统一收编。全部为组织方式优化，不动任何能力入口。**实施前按批 0 复核** |
| NEW-32 | 设计 token 与视觉语言统一（Slider 青色/rose 徽章/面包屑材质/三套图标） | M | 视觉一致性 #1/#9/#11/#12（均 unconfirmed） | Slider / UserBadges / Breadcrumb / RoleSettingsTabs / index.css | ①全站复用 Slider 滑轨硬编码 #22d0df 孤立青色相（`Slider.tsx:49`），改 var(--color-accent-400) 与 Toggle/主题对齐；②admin 角色徽章用未收编 rose 族（`UserBadges.tsx:17`，色板注记把 rose 留给 EMOTION_COLORS 情绪数据色），改 tag-blue/tag-yellow/tag-mint 族；③常驻文档流的面包屑误用 mat-floating 浮层材质（`Breadcrumb.tsx:97`，与「玻璃特权只给悬浮之物」的材质语义相悖），改 glass-card/mat-raised 或在 index.css 明文纳入清单；④emoji/字符/lucide 三套图标混用且同文件两份统计块样式分叉（`RoleSettingsTabs.tsx:1292`），统一 lucide 并收敛统计块组件。**实施前按批 0 复核** |
| NEW-33 | 可访问性与移动端安全区（扫码模态 + Toast） | S | 视觉一致性 #6/#7（均 unconfirmed） | Toast / WeChatPage / shared/ | ①新用户扫码绑定这一核心旅程的模态绕开 shared/Modal 自建（`WeChatPage.tsx:230`），缺 ESC/遮罩点击关闭、关闭钮无 aria-label 且触控目标远小于站内 44px 标准——迁移 shared/Modal 或补齐四项；②全局 Toast 固定 top-4 right-4 未适配刘海屏安全区、关闭钮无 aria-label 且触控目标偏小、图标用字符与全站 lucide 语言不一致（`Toast.tsx:24`）。与 NEW-4 同处一个弹窗但缺陷不同维（NEW-4 修状态机，本项修可访问性），可同批实施（已排入批 4）。**实施前按批 0 复核** |
| NEW-34 | 普通用户的「消息/拟人度」设置实际为空白告知页 | S | 日常使用-4 | RoleSettingsTabs / HumanizeSection | 生产已有真实陌生用户（非 admin）打开「消息」tab 只看到两段静态文字——无法控制自己的主动消息频率/免打扰时段/暂停（`RoleSettingsTabs.tsx:845-856`），「拟人度」区同样只读（`HumanizeSection.tsx:95-103`）。最小实现先给诚实空态文案（「以下为站长级全局设置，个人节奏调节即将支持」）消除「页面是坏的」错觉；进阶方向是把免打扰/频率做成 per-user 自助项（复用 user_reply_mode/user_humanize 既有按用户作用域机制）——**该方向是增加用户能力而非收走，符合设计哲学，但是新功能立项需裁决**（开放问题 4，附推荐：先空态文案） |

---

## 建议实施批次与顺序（供主控裁决）

> 依赖关系汇总自各条目行内标注与调研教训；批次为建议顺序，主控可整批复核或调整粒度。每批收尾跑前端 251 全量 + 受影响 e2e + ruff（口径声明角色卡数与工作树状态）。**同文件串行约束**：`navGroups.tsx` 横跨批 1/3/4/5，`RoleSettingsTabs.tsx` 横跨批 2/3/5/6——涉及同文件的相邻批严格串行（同窗口或顺序执行），禁止并行窗口改同文件（AGENTS §8）。

| 批次 | 内容（按序） | Effort 构成 | 前置依赖 | 主要文件 |
|---|---|---|---|---|
| **批 0 · 复核（只读）** | 实施风险 8 的 unconfirmed 精确盘点（NEW 全 unconfirmed 21 项 + 合并项 unconfirmed 分支 6 项 + V2 基线并入点中的 unconfirmed 发现）按代码实况逐项复核，产出复核结论后再进入对应批次 | 只读 | 无 | grep/读码 |
| **批 1 · P0 止血** | NEW-2（退出登录）→ NEW-1（工具开关门禁+toast）→ NEW-6（整理记忆确认）→ NEW-3（介绍页文案，按开放问题 1 推荐方向）→ V2-2（微信页角色显示+绑定账号+裸键） | S×4 + M×1 | 前四项全 verified 不依赖批 0；V2-2 并入补充面过批 0 | layout/、ToolsDashboard、RoleSettingsTabs、IntroPage、WeChatPage、RolesPage、RoleSettings |
| **批 2 · 契约修复** | NEW-7（kind/created_at 契约+本地日界）→ V2-6（Invalid Date 收敛+情绪归档键名按 NEW-5③ 口径，**依赖 NEW-7**）→ NEW-5（状态中心五处；分支⑤待批 0）→ NEW-9（isError） | S×1 + M×3 | NEW-7 先于 V2-6；③与 V2-6 子项冲突消解见 NEW-5③ | RoleSettingsTabs、UserTable、StatusCenter、api/routers/、PsychProfilePage、RolesPage |
| **批 3 · admin 数据面** | NEW-21（操作者审计，**先于 V2-1**）→ V2-1（全局视图+键名可读化，落 user_key→用户名映射底座）→ NEW-11（用户下钻）→ NEW-12（手输键收敛，复用底座） | L×1 + M×3 | 审计先行（cal.com 无审计退役反例）；V2-1 映射为 NEW-11/12 公共底座；NEW-21/12 过批 0 | admin_routes、AdminUsersPage、AdminProvidersPage、SettingsLogs、StatusCenter、HumanizeSection、TopicStateSection |
| **批 4 · 首日旅程与教学** | NEW-4（扫码状态机+e2e，与 NEW-33 同弹窗同批）→ NEW-16（初始角色告知）→ NEW-13（BYOK 反馈环）→ V2-4（教学导引：driver.js+checklist）与 V2-5（公告）——**两者新存储同批规划迁移**，走迁移纪律 | L×1 + M×4 + S×1 | V2-4 选型按调研章结论（driver.js MIT）；V2-4/5 与 NEW-13/16 过批 0 | WeChatPage、IntroPage、navGroups、SettingsLLM、client.ts、api/routers/、（新表×2） |
| **批 5 · 个性化与信息架构** | V2-3（侧栏四分组+口径说明+消息 tab 分组+θ 白话）→ NEW-14（语音四处）→ NEW-8（角色上下文语义）→ NEW-10（角色页入口） | L×1 + M×2 + S×1 | 批 0 复核 NEW-8/14 分支后实施 | navGroups、RoleSettingsTabs、TopicStateSection、SettingsVoice、StatusCenter、PsychProfilePage |
| **批 6 · 收尾** | **P1 尾四项：NEW-15/17/18/19（其中 NEW-19 按开放问题 2 结论执行）+ P2 其余 13 项：NEW-20/22/23/24/25/26/27/28/29/30/31/32/34**（NEW-21 已前置批 3、NEW-33 已并入批 4，故 P2 在本批为 13 项；需裁决项按开放问题 3/4/5/7 的推荐结论执行；含 unconfirmed 分支的先过批 0） | S×13 + M×4 | 批 0 复核 + 对应开放问题裁决 | 见 backlog P1/P2 表 |

> 六批 Effort 构成合计：S 4+1+0+1+1+13=**20**、M 1+3+3+4+2+4=**17**、L 0+0+1+1+1+0=**3**，与 40 项总量对账闭合。时长预估需主控按执行窗口模式折算（报告不建模人日）。

---

## 实施风险与开放问题

### 实施风险

1. **高频文件并发踩踏**：`RoleSettingsTabs.tsx` 被 **8 项**命中（V2-3 / NEW-6 / NEW-7 / NEW-19 / NEW-24 / NEW-26 / NEW-32 / NEW-34——含 NEW-26 的语音 tab 文案 `:515,522` 与 NEW-32 的统计块图标 `:1292`），`navGroups.tsx` 被 6 项命中（V2-2 / V2-3 / V2-4 / NEW-1 / NEW-10 / NEW-26）——按 AGENTS §8 并行纪律登记 owner、**同文件项合并为一个批次实施**（批次切分见「建议实施批次与顺序」节），禁止多窗口同时改，避免 v1.46.1 式互相回滚。
2. **回归面**：侧栏分组/文案/键名改动会破坏 e2e `w11_journey` 与 `mobile.spec` 的选择器与导航断言、组件测试（`SettingsLLM.test.tsx:48` 钉死空回显行为、personaConstants 锁定测试等）——文案与结构改动须同批更新规格并跑前端 251 全量 + 后端分块基线（口径须声明角色卡数与工作树状态）。
3. **契约类修复**（NEW-5 / NEW-7 / NEW-29）改后端返回形状会连带前端类型与 mock 测试；后端补计数/统计真源时**必须保持 per-user 隔离口径**（普通用户不得看到全库计数），admin 全局视图的归属标注（user_key→用户名映射）应与 V2-1 同批设计，避免两套键名可读化实现。
4. **权限收紧类**（NEW-1 isAdmin 门、V2-1 默认收窄）触及既有 403 契约测试（w11 viewer 直调 admin 端点→403 断言）与后端 require_role 面；收紧的同时**不得隐藏普通用户的只读信息**（设计哲学红线：工具可见性对 BYOK 排障有价值，只收写不收看）。
5. **生产真实用户在用**：V2-5 announcements 建表等涉 DB 迁移项须走项目迁移纪律（冷停 + 三库 .backup + 幂等演练 + 行数比对）；B 档文档与 A 档代码三端同步纪律照旧，文案类改动灰度后观察 app.log 与 chat_history 反馈。
6. **教学导引（V2-4）选型必须核 LICENSE**（driver.js MIT 已于撰写环节复跑复核；shepherd.js/intro.js 为 AGPL-3.0 双许可须排除），tour 引擎的 resize/滚动重定位用例要进测试（react-joyride issue #1202 教训）；checklist 进度按用户持久化会新增后端存储，注意与 V2-5 公告表同批规划迁移。
7. **NEW-6 仅加前端确认不改变后端全库语义**——根治需 agent_plane_routes 区分显式作用域参数，注意既有调用方（回放/整理链路）兼容；NEW-17 最后管理员守卫要**同时覆盖降权与停用两条路径**，防止只堵一处。
8. **证据底座与复核清单**：本汇总为只读审计的合并结果，verified/unconfirmed 状态沿用六路走查自查标注；两轮撰写环节独立取证清单见头部证据底座。**40 项复核状态精确盘点**（批 0 的执行清单）：
   - **V2 基线 6 项**：裁决内容本身不依赖批 0，但其**并入补充面**含 unconfirmed 发现——V2-1 并入 AD-13/CC-13、V2-2 并入 DU-9/VC-10、V2-3 并入 IA-5/IA-7/AD-10/CC-10/CC-6、V2-6 并入 VC-8——实施这些并入点前按批 0 复核；
   - **NEW 全 verified 7 项**（可直接实施，不依赖批 0）：NEW-1、NEW-2、NEW-3、NEW-6、NEW-9、NEW-11、NEW-19；
   - **NEW 核心 verified、附带 unconfirmed 分支 6 项**（核心断言已复核可动手，分支实施前过批 0）：NEW-4（NU-4 二维码过期、NU-13 e2e 缺口）、NEW-5（DU-12 工作记忆口径）、**NEW-7（DU-5 统计字段、AD-12 口径混排——同根现象的补充面，核心契约修复来自 AD-0 verified）**、NEW-10（NU-11 空态模板入口）、NEW-12（CC-5 话题状态手输、DU-13 交互不一致）、NEW-14（DU-10 音色命名、CC-9 隐私提示）；
   - **NEW 全 unconfirmed 21 项**（整项过批 0）：NEW-8、NEW-13、NEW-15、NEW-16、NEW-17、NEW-18、NEW-20、NEW-21、NEW-22、NEW-23、NEW-24、NEW-25、NEW-26、NEW-27、NEW-28、NEW-29、NEW-30、NEW-31、NEW-32、NEW-33、NEW-34。
9. **优先级口径**：P0/P1/P2 的完整映射规则（high 与 medium 两档全覆盖）、18 条 high 与 medium 主要条目的逐条落级对照、同族可逆性判别（NEW-17 vs NEW-20）与 V2 基线优先级来源声明，见「统一优化 backlog」章首**「定级规则（供复现）」**块——评级可从报告内证据复现（对应主题四「评级必须能从给定证据复现」质量门）。

### 开放问题（需主控裁决，各附推荐方向与理由）

1. **NEW-3 注册口径**：**背景**：代码实况已符合 D13「开放注册主链路+邀请码并存」（注册无门=主链路开放、邀请码可选勾选、invite 端点独立并存），唯一问题是介绍页文案把可选通道说成制度前提。**推荐：仅改文案**——介绍页改「可直接注册；邀请码为可选协助渠道（QQ 2053769154，亦可用于反馈/人工协助）」。理由：与 D13 一致、S 级零后端风险、直接消除首日漏斗误导；后端补邀请码门属收紧注册的新功能，与 D13「主链路开放」相悖，除非未来注册策略变化再做。
2. **NEW-19 角色「数据概览」三卡**：**推荐：先 S 级止血（删卡或改「暂无统计」诚实空态），真实统计源另立功能批**。理由：占位 0 值属误导级展示应立即止血；真实统计需 chat_history 按「会话×角色」口径设计计数（M 级、涉查询性能与隔离口径），不应阻塞小修；三卡从未显示过真值，删卡不损失任何可用能力。若主人希望保留卡片位，选「诚实空态」变体。
3. **NEW-25 训练死 API**：**推荐：确认训练管线退役后前后端一并收敛**（前端 training.ts+hook+client.ts 转出口；后端仅收敛 training/* 端点——撰写环节实读确认 proactive 端点与 training 同挂一个 router（`app_factory.py:269`），不能整删）。理由：AGENTS §0 已载「克隆收敛为本地提取+JSON 上传」，前端零消费实证在案；保留无 UI 入口的端点面扩大攻击面且违背反死码纪律；git 历史可随时取回。若主人确认训练管线仍是现役规划，则改走「补训练进度只读卡」。
4. **NEW-34 普通用户消息/拟人度**：**推荐：先做诚实空态文案（S），per-user 免打扰/频率自助另立功能批**。理由：空态文案立即消除「页面是坏的」错觉且零风险；per-user 自助虽可复用 user_reply_mode/user_humanize 作用域机制，但涉新端点+前端配置面+与全局调度的叠加语义设计，属功能立项应走完整商讨协议，不宜夹带在体验批。
5. **NEW-27 断开/换绑微信自助入口**：**推荐：开放（带确认+后果说明），实施前由主控确认断开后的通道回收语义**。理由：绑错微信号的陌生用户无自助路径是生产真实痛点，前端 API 已封装、后端端点已在（纯前端补面）；D16「一人一 bot 一条通道」下断开=释放该绑定、重新扫码即重新绑定，模型自洽；唯一未取证项是断开时 bot 侧会话/token 窗口的清理行为，确认后再实施，避免留下半开通道。
6. **V2-1 全局运维视图是否加密码再认证与 6h 时限**：**推荐：首版仅显式开关+常驻横幅+越权重定向，不做密码再认证与时限；但把 NEW-21 审计提前做完**。理由：GitLab 的再认证与限时是多人管理团队场景的设计，本项目当前单管理员，加时限反增日常运维摩擦；审计先行满足「特权辅助必须带审计闭环」的调研教训（cal.com 反例），为未来多人化留好扩展点，届时补再认证与时限成本极低。
7. **NEW-26 面包屑「扩展管理」死映射**：**推荐：默认删三行死映射（保守清理），除非主人确认「扩展管理」有回归计划**。理由：映射对应路由已删、全站无引用，留着是纯死码；删除成本极低（git 可恢复）；若确有回归计划，正确动作是恢复路由而非保留孤儿映射——请主人一并裁决「回归计划有无」即可。

---

## 生产取证待办（下一轮，需生产库/账本访问，静态走查不可为）

> 以下三项是主人读完 P0 即会追问的追溯性问题，本审计为静态走查无法回答，须在生产环境取证后回填本报告或写入实施批次前置结论：

1. **「整理记忆」空键全库改写是否已发生过**（NEW-6 追溯）：核查生产 agent_plane 账本/event_ledger 中 curate 调用记录（session_key 为空的 apply=true 调用是否存在、影响哪些 user_key 的事实行）；若已发生，评估是否需要记忆库回滚（回收站机制可辅助）。
2. **近两日 5 名新注册是否踩中任何 P0**（NEW-3/NEW-4 追溯）：回放 5 人的注册→落地→绑定时间线（是否在扫码环节遇到故障态死分支、是否因「邀请码制」文案离开注册页）；结论直接影响批 1 的实施紧迫度排序。
3. **生产 byok_required 实际取值**（NU-3③ 前提）：仓库不可证（本地 config/system.yaml:6 为 false），若生产已开启则 BYOK 微信端指引缺失（NEW-13③）为现役问题。

---

## 未覆盖范围（如实声明）

- **静态代码走查未实跑浏览器**：本次全部走查为只读静态走查（源码 + e2e 规格逐行阅读），未启动 dev server、未运行浏览器/e2e/单测——所有「渲染行为」结论由源码推断，未经运行时验证；个别推断（如 default 卡缺失的最终表现、working.count() 是否按用户分桶、刘海屏对 Toast 的实际遮挡）已逐条标注「推断」。
- **移动端真机与无障碍深度未审**：未做真机验证、读屏器（screen reader）走查、键盘导航全流程、色彩对比度（WCAG 对比度比值）实测；仅覆盖了静态可访问性结构（aria-label 缺失、触控目标尺寸、safe-area 适配三类，见视觉一致性 #6/#7）。
- **四个真实用户视角未走查，建议下一轮补审**：①**多角色切换用户**——本报告已证实激活是切换聊天对象的全局副作用（信息架构-0，`character_routes.py:778-916`），D16 又把控制台切角色定为核心能力，但「我有多个角色、日常切换」视角下的切换确认、切换后反馈、数据隔离感无一路走查；②**隐私与数据主权视角**——数据隐私是项目硬约束（AGENTS §3），但「我的数据谁能看到/能否导出/删除覆盖面」无独立走查，本次仅在语音上传第三方（NEW-14④）与心理画像合规亮点两处触及；③**移动端独立旅程**——mobile.spec 钉了响应式与抽屉交互，但移动端的信息架构与用户旅程未作为独立走查对象；④**会话过期/被强制登出用户**——与 NEW-2（无登出入口）、NEW-17（停用即全 token 失效）直接相邻：`client.ts` 拦截器含 401 处理（`api/client.ts:109-225`）已在证据中引用，但「会话中途过期后用户落到哪、丢什么、输入是否保留」未走查（本属静态可审范围，登记为下一轮补审缺口）。
- **后端仅在归属处核对**：为验证前端发现而下钻了相关后端数据源（dashboard/memory/facts/proactive history 等端点的返回形状与权限注解），另于撰写环节补验 training 路由注册实况（app_factory.py:269）——但这不构成后端全面审计；后端自身的 API 设计、性能、数据一致性未在本次范围。生产 4-worker 下日志环形缓冲的实际切片影响为基于架构的推断（`logging_setup.py:54`；4 worker 引证 AGENTS v1.51 条目）。
- **62 条 unconfirmed 发现未经第二遍复核**：verified 18 条经上游复核环节确认（独立性声明见头部证据底座）；其余 62 条沿用走查自查标注，**实施任何一项前须先按代码实况复核**（精确盘点见实施风险 8；个别发现的行号存在小偏差，已在正文就地注明，不影响结论）。
- **生产运行数据的口径未取证**：「近两日 5 个新注册（3 人在真实聊天）」来自主控任务输入（见头部生产数据来源声明），统计表/查询/时间窗未附，撰写环节未独立复核生产库；相关追溯性取证已列「生产取证待办」。
- **性能与工程性维度不在本次范围**：首屏/加载时长、bundle 体积、react-query 缓存策略、i18n 完整性、暗色模式适配、SEO 均未审计。
- **微信端体验不在前端仓库审计范围**：前端控制台无聊天界面（聊天发生在微信 iLink 通道内），微信端消息质量、兜底文案的实际观感仅在前端侧的关联处（BYOK 兜底指引缺失等）触及，未做微信端独立走查。
- **测试均未在本报告撰写环节运行**：本报告为审计文档整理产物，撰写时未运行任何 pytest/vitest/e2e/构建命令（仅执行了本报告引用的取证类只读命令：gh api ×3、npm downloads API ×2、grep/实读定位行号与 training.ts 对账、session_key 日志取证、e2e 目录 ls）；实施批次的质量门槛以 AGENTS §4.3 现行口径（前端 251/251、后端分块基线）为准。

---

*报告生成：2026-10-28 审计批次（报告撰写员整理，材料来自六路走查 + 四路行业调研 + 优先级合并）；2026-10-29 第一轮修订（12 条：生产数据来源声明、定级规则可复现化、AD-3 缓和点同步、D13 口径统一、亮点计数更正、unconfirmed 清单、调研数字复跑留痕、视角定义修正、开放问题补推荐、实施批次新增、复核独立性声明、行号实读定真值）；2026-10-29 第二轮修订（15 条：批 6 构成修正、RoleSettingsTabs 命中数 6→8、风险 8 算术重排、medium 档定级闭合、引用计数 3→4、跨路引用更正、AD-3 日志取证化解表述张力、NEW-5③ 与 V2-6 冲突消解、五函数五端点对账、笔误修复、人称统一、内部代号对照表、引证精度三处、会话过期视角补登、生产取证待办与 Effort 汇总新增）。所有发现引用 `path:line` 以撰写时工作树为准；本文件为 B 档纯文档，按三端纪律 commit→push 即为完成，不上服务器。*
