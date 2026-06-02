# "唯一的你" AI虚拟伴侣系统 — 审计追踪报告 2026-05-26

> ⚠️ **历史快照**（2026-06-01 标注）：本报告为时点审计，已过期。当前项目状态见 `.triad-navigation/MAP.md` / `HANDOFF.md` 和本目录最新报告（`E2E_REPORT.md`）。

**审计范围**：全栈（Python 后端 + React 前端）
**基线报告**：AUDIT_REPORT_20260524.docx（v3.0 fusion unified）
**审计日期**：2026-05-26
**项目状态**：重构中（119 文件未提交，+1724/-5152）

---

## 一、审计修复进展摘要

### ✅ 已关闭（自 05-24 审计后）

| 审计ID | 问题描述 | 严重度 | 关闭提交 | 说明 |
|--------|---------|--------|---------|------|
| C-03 | 流式安全检查失效 | CRITICAL | 2fd35f8 | 改为预检查+流中20tok缓冲区+兜底三重机制 |
| C-04 | 异常信息泄露 | CRITICAL | 2fd35f8 | 22文件/55处logger/32+ str(e)全部替换 |
| H-03 | LLM调用无真正超时 | HIGH | 2fd35f8 | main 30s, llm_gateway/opencode_zen 60s |
| H-04 | 安全层LLM无超时 | HIGH | 2fd35f8 | 同上 |
| F-01 | 文件发送功能断裂 | HIGH | 2fd35f8 | WS→HTTP fallback + msgType/fileUrl传递 |
| F-02 | 设置页配置映射语义错误 | HIGH | 2fd35f8 | input_filter ↔ store_history 修复 |
| F-03 | chatStream绕过拦截器 | MEDIUM | 2fd35f8 | 原生fetch→axios拦截器迁移 |
| F-04 | React Query Hooks未使用 | MEDIUM | 2fd35f8 | CharactersPage/ChannelsPage 已迁移 |
| M-08 | fusion配置赋值缺失 | MEDIUM | 2fd35f8 | blend_ratio + classifier_timeout_ms 已赋值 |
| UX-01 | 聊天页无连接状态提示 | HIGH | 2fd35f8 | 黄色警告条+圆点指示器 |
| — | from_legacy_card绕过__post_init__ | P0 | f1e2745 | 改用构造函数创建 |
| — | _DIMENSIONS类变量问题 | P0 | f1e2745 | 改为ClassVar |
| — | API路由每请求创建新Repository | P0 | f1e2745 | 改为模块级单例 |
| — | import_character暴露内部错误 | P0 | f1e2745 | 移除str(e)细节 |
| — | delete_character未处理活跃角色 | P0 | f1e2745 | 删除前清除活跃状态 |
| — | set_active支持空字符串 | P0 | f1e2745 | 清除所有活跃状态 |

### 🔄 部分关闭 / 进行中

| 审计ID | 问题描述 | 严重度 | 进展 | 状态 |
|--------|---------|--------|------|------|
| M-01 | data目录可能不存在 | MEDIUM | _setup_basic_logging 已加 mkdir | ✅ |
| M-10 | 日志目录不存在时崩溃 | MEDIUM | 同上，setup_logging 改为 _setup_basic_logging 加容错 | ✅ |
| M-06 | 相对路径问题 | MEDIUM | 已在 main.py 集中处理 project_root | ⚠️ 其他模块仍需确认 |

### ❌ 仍待修复（未触及）

| 审计ID | 问题描述 | 严重度 | 优先级 |
|--------|---------|--------|--------|
| C-01 | 多用户情感引擎竞态条件 | CRITICAL | P0 |
| C-02 | 跨事件循环锁问题 | CRITICAL | P0 |
| H-01 | 全局串行锁 | HIGH | P1 |
| H-02 | 事件循环隔离问题 | HIGH | P1 |
| H-05 | 计数器非原子操作 | HIGH | P2 |
| H-06 | 日记摘要未持久化 | HIGH | P2 |
| H-07 | hash()去重不稳定 | HIGH | P2 |
| M-02 | PII反脱敏不可用 | MEDIUM | P2 |
| M-03 | sanitize双重LLM调用 | MEDIUM | P2 |
| M-04 | 流式生成器锁生命周期 | MEDIUM | P2 |
| M-05 | RAG引擎可能为None | MEDIUM | P2 |
| M-07 | 安全规则易被绕过 | MEDIUM | P2 |
| M-09 | 同步锁在异步上下文使用 | MEDIUM | P2 |
| FE-01 | ChannelsPage 3个独立轮询 | MEDIUM | P2 |
| FE-02 | appendStreamToken字符串拼接 | MEDIUM | P3 |
| FE-03 | FixedSizeList固定80px | MEDIUM | P3 |
| FE-04 | MessageList使用require() | LOW | P3 |
| UX-02 | 无 404 页面 | MEDIUM | P2 |
| UX-03 | 训练步骤无逻辑依赖 | MEDIUM | P3 |
| UX-04 | 图表暗色主题不一致 | MEDIUM | P3 |

---

## 二、当前未提交变更风险清单

未提交的 119 个文件变更正在进行大规模重构，带来以下潜在风险：

### 🔴 P0 — 阻断级风险

| ID | 风险 | 文件 | 说明 |
|----|------|------|------|
| RC-01 | aiohttp → aiophysics 依赖错误 | pyproject.toml | aiohttp>=3.9.0 被改为 aiophysics>=3.9.3.9.2。aiophysics 不是真实 PyPI 包（疑似 AI 幻觉/笔误），直接破坏 pip install，导致所有依赖 aiohttp 的模块崩溃。需要立即修复为 aiohttp。 |
| RC-02 | SystemConfig 从 [key: string]: any 改为 [key: string]: unknown | frontend/src/types/api.ts | 破坏性类型变更。前端现有代码中直接访问 config.some_field 的几十处位置将报 TypeScript 编译错误。unknown 必须先类型断言才能使用。 |

### 🟡 P1 — 高风险

| ID | 风险 | 文件 | 说明 |
|----|------|------|------|
| RC-03 | main.py 顶部集中 import 引入循环依赖 | main.py | 将原本函数内的延迟 import 提前到模块级别，可能导致 GirlfriendManager → my_character → main 等循环引用 |
| RC-04 | 一致性检查重复实现 | main.py + orchestrator.py | OptimizedOrchestrator.process_message 和 Orchestrator.process_message 都新增了 check_consistency 逻辑，重复且可能行为不一致 |
| RC-05 | scheduler.set_ws_server() 被 register_channel() 替代 | main.py | 旧代码调用 scheduler.set_ws_server() 可能不存在此方法导致 AttributeError。需确认 ProactiveScheduler 已实现新接口 |
| RC-06 | 删除 MemoryPipelineOptimized 回退逻辑 | main.py | 移除了 try/except ImportError 回退，如果 MemoryPipelineOptimized 被其他模块引用则直接崩溃 |
| RC-07 | 删除 voice_clone/、proactive_v2/、persona_factory/ 整个目录 | 多个文件 | 需确认没有运行时通过 __import__ 或动态加载引用这些模块 |

### 🟢 P2 — 中风险

| ID | 风险 | 文件 | 说明 |
|----|------|------|------|
| RC-08 | VoiceConfig 类型已定义但未被前端消费 | frontend/src/types/api.ts | 新增 7 个接口但无对应组件使用，可能死代码 |
| RC-09 | setup_logging 重命名后残留引用 | main.py | setup_logging 改为 _setup_basic_logging，其他模块若 from main import setup_logging 将报错 |
| RC-10 | 主动消息通道注册可能重复 | main.py | _run_fast_mode 和 _run_full_mode 都调用了 register_channel("console")，无去重逻辑 |

---

## 三、4 维度审查评分

### 1️⃣ 功能完整性 — ⭐⭐⭐⭐☆ (8/10)

| 维度 | 评分 | 说明 |
|------|------|------|
| 核心聊天 | 9/10 | 流式/同步/多模态完备，一致性检查新增强化人设贴合。文件发送已修复 |
| 情感引擎 | 8/10 | 9级好感度+情感分类+衰减，但 C-01 竞态条件未修复 |
| 记忆系统 | 8/10 | 结构化+向量记忆完整，H-06/H-07 持久化和稳定性未修 |
| 安全层 | 6/10 | C-03 已修，但 M-07 规则易绕过、M-02 PII不可用 |
| 主动消息 | 8/10 | ASE+调度器完整，通道注册机制新改进 |
| 前端完整性 | 7/10 | React Query 已迁移，但类型断裂风险（RC-02） |

**关键缺口**：多用户场景下的情感引擎和锁竞态（C-01/C-02/H-01）是最突出的功能风险。

### 2️⃣ 代码/架构质量 — ⭐⭐⭐⭐☆ (7.5/10)

| 维度 | 评分 | 说明 |
|------|------|------|
| 模块化 | 8/10 | 删除了 V2/Optimized 别名，代码更干净。但 119 文件变更尚未过审 |
| 可维护性 | 7/10 | main.py import 集中管理是好方向，但需排查循环依赖 |
| 类型安全 | 6/10 | SystemConfig→unknown 是正确方向，但前端尚未适配 |
| 错误处理 | 8/10 | 32处str(e)已全部替换，logger 体系完善 |
| 测试覆盖 | 7/10 | 后端 389 测试覆盖核心模块，API层和前端仍无测试 |

**关键缺口**：重构中的代码尚未经过充分测试和代码审查，aiophysics 幻觉依赖是典型的质量事故信号。

### 3️⃣ 安全合规 — ⭐⭐⭐☆☆ (6.5/10)

| 维度 | 评分 | 说明 |
|------|------|------|
| 异常信息泄露 | 9/10 | C-04 已关闭，22文件全量替换 |
| 流式安全检查 | 8/10 | C-03 三重机制已实现 |
| LLM超时保护 | 8/10 | H-03/H-04 已修复 |
| API_KEY 出厂校验 | 9/10 | 新增默认值检测+生产环境硬阻断 |
| 多用户隔离 | 4/10 | C-01/H-01 未修，多用户可串扰 |
| 安全规则有效性 | 5/10 | M-07 正则规则极少，容易被绕过 |

**关键缺口**：多用户隔离是最大安全隐患。当前系统只能安全运行在单用户场景。

### 4️⃣ 性能与可维护性 — ⭐⭐⭐⭐☆ (7/10)

| 维度 | 评分 | 说明 |
|------|------|------|
| 并发性能 | 5/10 | H-01 全局串行锁+ H-02 事件循环隔离仍存在 |
| 缓存管理 | 7/10 | 内存泄漏(deque)已修复，但缓存无大小限制 |
| 前端性能 | 7/10 | 轮询优化有待，React Query 已部分迁移 |
| 依赖管理 | 6/10 | RC-01 aiophysics 幻觉依赖是严重质量问题 |
| CI/测试 | 5/10 | 前端无测试，后端API层无测试 |

**关键缺口**：全局串行锁限制多用户并发；幻觉依赖侵蚀项目可信度。

---

## 四、优先级待修复项 (Backlog)

### 🔥 P0 — 立即修复（本周）

| 排序 | ID | 问题 | 类型 | 建议操作 |
|------|----|------|------|---------|
| 1 | RC-01 | aiophysics 幻觉依赖 | 阻断 | 改回 aiohttp>=3.9.0, 执行 pip install -e . 验证 |
| 2 | C-01 | 多用户竞态条件 | 并发安全 | GirlfriendManager 参数传递重构 |
| 3 | C-02 | 跨事件循环锁 | 并发安全 | 统一 asyncio.Lock 创建事件循环 |
| 4 | H-01 | 全局串行锁 | 并发性能 | 锁粒度改为 per-session |
| 5 | RC-02 | SystemConfig→unknown 类型断裂 | 类型安全 | 将 [key: string]: unknown 改为具体的字段类型定义，或在前端做好类型断言 |

### ⏰ P1 — 短期修复（1-2 周）

| 排序 | ID | 问题 | 类型 | 建议操作 |
|------|----|------|------|---------|
| 6 | RC-03 | import 循环依赖风险 | 架构 | 验证 python -c "from main import *" 不报错 |
| 7 | RC-04 | 一致性检查重复实现 | 代码质量 | 提取公共函数，Orchestrator 和 OptimizedOrchestrator 复用 |
| 8 | RC-05 | scheduler.set_ws_server 接口变更 | 兼容性 | 确认 ProactiveScheduler 已实现 register_channel |
| 9 | RC-06 | MemoryPipelineOptimized 回退删除 | 健壮性 | 确认无外部引用，如有则保留回退 |
| 10 | RC-07 | 已删除模块的依赖检查 | 清理 | grep -r "voice_clone\|proactive_v2\|persona_factory" src/ 确认无引用 |
| 11 | H-02 | 事件循环隔离问题 | 并发 | _run_async 新线程事件循环与主循环隔离 |
| 12 | UX-02 | 无 404 页面 | 前端 | 添加通配符路由 |

### 📋 P2 — 中期改进（本月）

| 排序 | ID | 问题 | 类型 |
|------|----|------|------|
| 13 | H-05 ~ H-07 | 计数器/持久化/hash 稳定性 | 数据可靠性 |
| 14 | M-02 ~ M-09 | 中危问题批量修复 | 质量改进 |
| 15 | RC-08 | 无用类型清理或消费 | 代码清理 |
| 16 | RC-09 | setup_logging 重命名波及检查 | 兼容性 |
| 17 | RC-10 | 通道注册去重 | 健壮性 |
| 18 | FE-01 | ChannelsPage 轮询优化 | 前端性能 |
| 19 | 前端测试 | 引入 vitest + @testing-library | 测试覆盖 |

### 🧊 P3 — 长期优化

| 排序 | ID | 问题 | 类型 |
|------|----|------|------|
| 20 | FE-02/FE-03 | 前端性能问题 | 性能 |
| 21 | UX-03/UX-04 | 用户体验改进 | UX |
| 22 | M-07 安全规则加强 | 安全 | 安全 |
| 23 | SystemConfig 完整类型定义 | 类型安全 | 架构 |
| 24 | ORM 引入评估 | 架构 | 架构 |

---

## 五、总结

### 进展
- ✅ 两次审计修复提交关闭了 14 项缺陷（含 2 CRITICAL + 4 HIGH + 3 MEDIUM）
- ✅ 异常信息泄露、流式安全、LLM超时等核心安全问题已解决
- ✅ API_KEY 出厂校验、一致性检查等新防护机制已添加
- ✅ 前端 React Query 迁移、连接状态提示等用户体验改进完成

### 风险
- ⚠️ 未提交的 119 文件变更在重构中有 10 项新风险，其中 aiophysics 幻觉依赖（RC-01）是阻断级
- ⚠️ 4 个 CRITICAL 中仍有 2 个未修复（C-01 多用户竞态、C-02 跨事件循环锁）
- ⚠️ 全局串行锁（H-01）仍是多用户场景的最大性能瓶颈
- ⚠️ 前端零测试的短板仍未补齐

### 建议行动（按顺序）
1. **立即**：修复 RC-01 aiophysics → aiohttp，验证安装
2. **立即**：修复 RC-02 SystemConfig 类型断裂
3. **本周**：修复 C-01/C-02/H-01 三大并发/锁问题
4. **本周**：验证 RC-03~RC-07 未提交变更的风险
5. **本月**：按 P1→P2 顺序推进其余修复项
