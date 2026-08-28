# FUNCTION INVENTORY — 功能清单（代码实况 × 历史意图）

> 创建：2026-08-28 | 性质：**truth**（商讨协议定位基准，替代已清除的 FEATURE_MAP）
> **基准声明（08-28 用户裁决）**：功能对齐的意图基准 = `docs/history/` 历史设计文档（05-19 立项核心特性、05-24 多用户设计——记录了产品所有者的原始想法）；实况基准 = 本清单所引代码。实拍截图不作对齐依据。
> **编号规则**：`<页面KEY>-<序号>`，如 `ROLES-2`。对话时直接报编号 + 期望。
> **维护纪律**：页面功能变更时同步本清单对应条目；新页面入册必须带代码证据（文件:行）。

---

## 0. 全局能力对照（历史六特性 × 代码实况）

来自 `docs/history/2026-05-19-PROJECT_OVERVIEW.md` §1.2 核心特性 + §1.1 多通道：

| 历史意图 | 代码实况 | 对齐判定 |
|---------|---------|---------|
| 情感引擎（8 级好感 + 实时状态机） | ✅ `useEmotionState`/`useDashboard` → STATUS-1~2；五维性格滑条 ROLES-CREATE 预览与 SETTINGS-BASIC | 对齐 |
| 记忆系统（三层：工作/情景/语义） | ✅ 后端三层管线在产（CODE_GRAPH §4.3）；前端消费仅 STATUS-3 记忆条目 + 最近事实列表（前两层无独立 UI） | 部分（前端呈现薄） |
| 主动消息（情境感知主动关怀） | ✅ MESSAGE-1 三参数滑条（日上限/最小间隔/冷却）；后端 ASE 引擎在产 | 对齐（前端仅参数面） |
| 风格克隆（从微信聊天记录克隆说话风格） | ✅ CREATE-CLONE-1~4（智能体任务书 + JSON 上传分析，08-28 收敛）；历史设想的"服务端直接解密"路径已按合规裁决删除 | 对齐（方式变更已裁决） |
| 多通道接入（微信个人号/**企业微信**/控制台/WebSocket API） | ⚠️ 微信个人号 + 控制台 + REST/WS 在产；**企业微信通道无代码**（仅历史文档提及；wxwork 解密在 wechat-decrypt 工具侧） | 差距（企微未实现） |
| 安全机制（内容过滤/PII 脱敏/提示注入检测） | ✅ SECURITY-1~3（统计/日志/开关）+ 后端四件套在产 | 对齐 |
| 多用户隔离（05-24 设计） | ✅ user_id 全链路隔离；LOGIN-2 邀请码注册；ADMIN-USERS 全套；用户级 LLM Key 隔离 LLM-2 | 对齐 |

---

## A. 公开域（无需登录）

### LOGIN — 登录页 `/login`（LoginPage.tsx, 222 行）
| 编号 | 功能点（代码证据） |
|------|------------------|
| LOGIN-1 | 账号密码登录（useAuth → `/api/auth/login`），登录后跳 `/wechat` |
| LOGIN-2 | 邀请码注册（`/api/auth/register-invite` 链路，注册/登录双 tab） |

## B. 微信连接域

### WECHAT — 微信接入 `/wechat`（WeChatPage.tsx, 413 行）
| 编号 | 功能点 |
|------|--------|
| WECHAT-1 | 实时连接状态横幅：连接状态/运行时长/今日消息数/重连次数（`useWechatStatus` 5s 轮询） |
| WECHAT-2 | 扫码连接弹窗：创建连接 → 获取二维码 → 刷新二维码 → 完成确认（wechatCreateConnection/wechatQrCode） |
| WECHAT-3 | 断开态引导卡（QrCode 图标+连接指引+「立即扫码连接」按钮，stagger 入场）；状态字段语义化（未运行/今日暂无消息替代裸值） |

## C. 角色域

### ROLES — 角色配置 `/roles`（RolesPage.tsx, 125 行）
| 编号 | 功能点 |
|------|--------|
| ROLES-1 | 角色卡网格：名称/**摘要化描述**（deriveCardSummary：方括号字段提取/「你是」转第三人称/≤60 字）/**语义色锚点**（anchorTone：关系=黄 设定=蓝 风格=青）/激活态徽章 |
| ROLES-2 | 设为活跃（activateCharacter + queryClient 失效）；活跃卡置顶排序 |
| ROLES-3 | 创建角色入口（网格尾虚线卡 + 空态 CTA）；骨架屏加载态 |
| ROLES-4 | 搜索框（>6 卡显示，匹配名称/描述/标签）+ 无结果空态 |
| ROLES-5 | 入场动效（stagger CSS 级联 ≤12×60ms，prefers-reduced-motion 降级） |
| ⚠️ | SP-5 在册：prompt 原文直出（`[姓名:x]`/「你是」类 description 未摘要化，卡库实扫 7+10 张）；53 卡无搜索无分组 |

### CREATE — 创建角色 `/roles/create`（CreateRole.tsx, 701 行）
| 编号 | 功能点 |
|------|--------|
| CREATE-AI-1 | 「和十四聊一会儿」AI 对话提取人设（previewCharacterFromDescription，SSE） |
| CREATE-AI-2 | 三方式横向分段：AI 对话 / 克隆好友 / 文件导入 |
| CREATE-CLONE-1 | 克隆 tab 步骤①：准备智能体（OpenCode 推荐卡 + 通用智能体说明） |
| CREATE-CLONE-2 | 克隆 tab 步骤②：一键复制智能体任务书（constants/cloneAgentGuide.ts） |
| CREATE-CLONE-3 | 克隆 tab 步骤③：被克隆者名称 + JSON 文件选择（仅 .json） |
| CREATE-CLONE-4 | 上传分析（cloneUpload → `/api/clone/upload`）：进度条/分析中/完成态对话预览（前 5 条）→ 人设写入右侧预览卡 |
| CREATE-PRESET-1 | 预设角色快捷选择（listPresets/getPreset） |
| CREATE-IMPORT-1 | 文件导入角色卡（importCharacter，JSON/PNG SillyTavern 兼容） |
| CREATE-PREVIEW-1 | 右侧常驻实时预览卡（PersonaPreviewCard：名称/描述/锚点/五维性格条，`lg:grid-cols-[2fr_1fr]`） |

### SETTINGS — 角色设置 `/roles/:id/settings[/:tab]`（RoleSettings.tsx 84 行 + RoleSettingsTabs.tsx 528 行，六 tab）
| 编号 | 功能点 |
|------|--------|
| BASIC-1 | 基础信息：五维性格滑条/锚点编辑/口头禅/描述 |
| VOICE-TAB-1 | 语音 tab：MiMo 模型三选（基础/克隆/设计）（⚠️ G-06 在册：保存接口标开发中，前端仅预览） |
| MESSAGE-1 | 消息 tab：主动消息三滑条（每日上限 1-50/最小间隔 5-120min/冷却 5-240min）+ 统计卡（消息总数真实，今日触发/最后发送为 `—` 占位 ⚠️） |
| DATA-1 | 数据 tab：概览统计（消息/记忆条数）+ 网络人设增强按钮（/api/characters/{id}/enrich）+ RAG 静态统计区（⚠️ G-07 在册：占位数据未接真实接口） |
| STICKERS-1 | 表情包 tab |
| TIMELINE-1 | 剧情时间线 tab（内嵌 StorylineEditor） |

### STATUS — 状态中心 `/roles/:id/status`（StatusCenter.tsx, 77 行）
| 编号 | 功能点 |
|------|--------|
| STATUS-1 | 三卡：当前情绪（useEmotionState）/亲密等级（affinity→初识/熟悉/亲密三档）/记忆条目数 |
| STATUS-2 | 无活跃角色兜底态（引导先创建/激活） |
| STATUS-3 | 最近记忆列表（useMemoryFacts 前 5 条） |
| ⚠️ | SP-1 挂起：情绪分布图/成就/趋势缺（数据层 useEmotionTrend 已就绪零消费） |

## D. 系统设置域（SystemSettingsLayout 嵌套 Outlet）

### LLM — LLM 设置 `/settings/llm`（SettingsLLM.tsx, 392 行）
| 编号 | 功能点 |
|------|--------|
| LLM-1 | 供应商清单（listProviders：预设/自定义/特殊三态）+ 连接参数 + 生成参数 + 缓存配置 |
| LLM-2 | **用户级配置隔离**：普通用户 saveUserLlmConfig（回退全局时明确提示来源）；admin 走全局 saveConfig |
| LLM-3 | 供应商申请教程弹窗（BookOpen） |

### VOICE — 语音工作台 `/settings/voice`（SettingsVoice.tsx, 496 行）
| 编号 | 功能点 |
|------|--------|
| SVOICE-1 | 引擎卡（mimoStatus/mimoSetEngine，MiMo 唯一引擎 08-28 收敛）+ 可用音色列表（mimoSwitchVoice） |
| SVOICE-2 | 语音克隆：上传参考音频 → mimoClone（voiceclone 模型） |
| SVOICE-3 | 语音设计：性别/风格/年龄/音调/语速 → mimoDesign（voicedesign 模型） |
| SVOICE-4 | 合成试听（mimoSynthesize） |

### TOOLS — 工具面板 `/settings/tools`（ToolsDashboard.tsx, 178 行）
| 编号 | 功能点 |
|------|--------|
| TOOLS-1 | 内置工具健康状态（tools/toolsHealth：可用性+错误提示+配置指引）+ 启停开关（toggleTool） |

### SECURITY — 安全设置 `/settings/security`（SettingsSecurity.tsx, 208 行）
| 编号 | 功能点 |
|------|--------|
| SEC-1 | 安全统计：今日拦截/总检测/拦截率（safetyStats） |
| SEC-2 | 过滤器开关（safetyConfig）+ 最近安全日志（safetyLog 前 10 条，拦截/放行） |

### LOGS — 日志查看 `/settings/logs`（SettingsLogs.tsx, 205 行）
| 编号 | 功能点 |
|------|--------|
| LOGS-1 | 级别过滤/关键词搜索/5s 轮询（可暂停）/清屏/TXT 导出（system.logs） |

## E. 管理后台（admin 角色，RoleGuard）

### ADMIN-USERS — 用户管理 `/admin/users`（AdminUsersPage.tsx, 380 行）
| 编号 | 功能点 |
|------|--------|
| AUSER-1 | 注册账号 CRUD（adminCreateUser/Update/Delete）+ 角色分配（ROLES/ROLE_LABELS）+ 启停 |
| AUSER-2 | 400ms 防抖搜索 + 表单校验（validateEmail/Password/Username）+ 确认弹窗；不能删自己 |

### ADMIN-PROVIDERS — 供应商管理 `/admin/providers`（AdminProvidersPage.tsx, 366 行）
| 编号 | 功能点 |
|------|--------|
| APROV-1 | 供应商全生命周期：清单/启停/编辑/删除/添加自定义 OpenAI 兼容源 + 申请教程外链 |

## F. 其他

### 404 — NotFoundPage：返回首页链接。

---

## 与历史意图的已知差距汇总（对齐核查产出）

| # | 差距 | 历史出处 | 处置建议 |
|---|------|---------|---------|
| ~~GAP-1~~ | ~~企业微信通道未实现~~ | 05-19 §1.2 | **❌ 08-28 用户裁决：只做个人微信，其他通道不需要**（Non-Goal） |
| GAP-2 | 记忆三层仅"条目数+最近事实"入 UI，工作记忆/情景时间线无呈现 | 05-19 §1.2 记忆系统 | 与 SP-1（状态中心丰富化）合并决策 |
| GAP-3 | MESSAGE-1 统计卡"今日触发/最后发送"为占位 `—`（数据未接） | 主动消息可观测 | 小改动，可并入 SP-5 批次 |
| GAP-4 | VOICE-TAB-1 / DATA-1 的保存接口标"开发中"、RAG 区静态占位（G-06/G-07） | 页面内实况标注 | 需后端补端点或接既有端点，立项裁决 |
| GAP-5 | 状态中心缺情绪分布/成就/趋势（SP-1 挂起，数据层就绪） | 05-29 差距分析 | SP-1 已在冻结池 |

> 本清单由代码读出（App.tsx 路由 × 15 页面组件 × api/*.ts 消费），历史意图对照 `docs/history/`。条目变更随代码同步。
