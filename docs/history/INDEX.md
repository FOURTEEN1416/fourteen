# 产品意图演进索引 — 真值裁决表

> 创建：2026-08-24 | 维护者：随重大产品决策更新
> 用途：解决「设计文档多次迭代，哪版是正确的」问题。
>
> **裁决标准（冲突时按此优先级）：**
> 1. **代码实况**（以 `CODE_GRAPH.md` + 实际路由/端点为准）
> 2. **现行文档**（README.md v3.x / AGENTS.md / docs/P1_BACKLOG.md）
> 3. **历史文档**（本目录 `docs/history/`，仅供追溯意图）

---

## 演进时间线

### 📜 阶段一：单用户 AI 伴侣「十四」（2026-05-19）

**文档**：`history/2026-05-19-PROJECT_OVERVIEW.md`

原始开发目的：基于 LLM 的 AI 虚拟伴侣，情感模拟 + 三层记忆 + 主动交互 + 风格克隆，微信单用户接入。

| 设计要素 | 当前状态 |
|---------|---------|
| 情感引擎（8 级好感度阶梯） | ✅ 现行有效 |
| 三层记忆（工作/情景/语义） | ✅ 现行有效（shisi/memory/） |
| ASE 主动消息引擎 | ✅ 现行有效（proactive/） |
| 12 维风格分析 + LoRA 训练管线 | ⚠️ 部分取代：07-30 起 LoRA 本地训练移除，改为「本地提取→上传→服务器分析」（commit 3545643） |
| V1/V2 双版本架构（main.py / main_v2.py） | ⚠️ 已取代：07-28 双模式合并进 main.py（_init_mixin 唯一初始化真相源） |
| CowAgent 微信通道 | ⚠️ 已取代：现为 wechat_direct/ 直连方案 |

### ✅ 阶段二：多用户架构转向（2026-05-24）

**文档**：`history/2026-05-24-multi-user-design.md`

GirlfriendManager 按 user_id 隔离（记忆/情感/角色卡独立，LLM/安全/RAG/TTS 共享）。

**状态：✅ 现行架构，完全生效**（user_scheduler.py 生产运行中，多用户隔离是硬约束 AGENTS.md L3）。

### 📜 阶段三：v10 导航设计稿与差距分析（2026-05-29）

**文档**：`history/2026-05-29-design-vs-current-gap.md`

逐页对比设计稿与前端实现的差距清单（P0×3 / P1×5 / P2×3）。

| 当年差距项 | 当前状态 |
|-----------|---------|
| P0-1 SettingsSecurity 内容错位 | ✅ 已修复（现为真实内容安全面板） |
| P0-2 三层级动态侧边栏 | ❌ **用户裁决：不需要**（2026-08-24，架构已扁平化） |
| P0-3 面包屑导航 | ✅ 已实现（components/layout/Breadcrumb.tsx） |
| P1 状态中心丰富化（4 卡片+情绪分布+成就+趋势） | ⏳ 部分差距仍在（当前 3 卡片+最近记忆） |
| P1 创建角色缺实时预览/快速设置子 tab | ⏳ 差距仍在（当前为三创建方式 tab） |
| P1 微信控制台卡片 vs 表格布局 | ➖ 已被后续迭代自然演化，按现状为准 |

### ✅ 阶段四：品牌更名 + 认证体系（2026-06-01 ~ 06-04）

**文档**：`history/2026-06-04-HANDOFF.md`

- 品牌：「AI Girlfriend」→「唯一的你 / unique-you」，定位词「AI伙伴」；物理目录 ai-girlfriend 与 GitHub 仓库 fourteen.git 保留旧名
- 认证双体系：JWT 用户鉴权（管理控制台）+ X-API-Key 内部鉴权（服务间），**✅ 现行有效**
- P0 安全修复史（JWT_SECRET fail-fast、accessToken 内存闭包等）

**状态：✅ 有效，P0 修复均已在位。**

### ⭐ 阶段五：当前态（2026-08-24 生效）

**真值文件**：`AGENTS.md` v1.1（宪法）/ `README.md` v3.1.0 / `CODE_GRAPH.md`（08-01）/ `docs/P1_BACKLOG.md`（08-24 重写）/ `docs/FEATURE_MAP.md`（功能现状地图）

产品形态：**多用户微信陪伴系统**——扫码即用 + 19 页管理控制台 + 204 API 端点 + 1104 测试基线。

---

## 已知真值漂移登记簿

| 漂移描述 | 发现日期 | 状态 |
|---------|---------|------|
| README 引用不存在的 `.triad-navigation/` 与 `8-layer-code-map.md` | 08-24 | ✅ README 已修，历史地图见 `docs/history/` |
| CODE_GRAPH 称「19 页面全部注册路由」 | 08-24 | ⚠️ 实为 16 挂载 + 3 孤儿（UsersPage/UserWorkspace/BindingDetailPage），详见 FEATURE_MAP F 区 |
| progress-tracker / design-expectation 比赛文档过期 | 08-24 | ✅ 已删除（比赛信息源清理 commit 893a906） |
