# Code Deletion Log

## [2026-08-28] 文档感染源清理（治理会话第二阶段，用户授权"清理删除"）

### 决策依据
- 用户 2026-08-28 指示：文档治理须"清除感染源，修正相关说法……进行相关文档的清理删除"
- `docs/DOCUMENTATION_GOVERNANCE_REPORT.md` 为 08-26 一次性活动报告（derived），含无法修复的污染口径："19598 个 Python 文件"（.venv 污染）、"前端测试 1034 passed"（实为 Python 数错标）、"SP-3 Demo 删除驳回"（已被 08-28 D1 裁决推翻）；有效信息（阅读报告清单）已收编 `docs/README.md` §五

### Files Deleted
- `docs/DOCUMENTATION_GOVERNANCE_REPORT.md`（144 行）

### Files Modified（同批感染源修正，非删除）
- `README.md`（根）：徽章与结构树 1104→1089、19 页→15 页、demo 子路由行移除、14 API 模块→12、routers 13→20、pytest 注释 1025→1030
- `docs/DECISION_LEDGER.md`：§四"1104 基线不可回退"→"1089（v1.3 重测，旧基线随删除自然缩减）"
- `docs/VISION.md`：§B 候选池 SP-3 标记"已执行（D1）"移出冻结池；愿景板目标用户（广泛用户）与商业目标（完全免费开源 MIT）按用户口述落笔
- `docs/CODEMAPS/BACKEND.md`：漂移声明指向 199 端点；demo_routes 行移除
- `docs/CODEMAPS/FRONTEND.md`：demo.ts/DemoPage/UsersPage/UserWorkspace/BindingDetailPage/api users.ts 条目移除，pages 19→15
- `docs/CODEMAPS/MODULES.md`：路由模块 22→21（demo 移除）、挂载 17/204→16/199
- `docs/HANDOFF_REPORT.md`：三处对已删报告的引用改为"已删除+收编"批注

### 保留说明
- 16 份 READING_REPORT_*.md 保留为 derived（历史通读产物，docs/README.md §五 已标注"仅供追溯"，其历史数字随日期快照有效）；HANDOFF_REPORT 内 1025/1033/1035 等中间数字同理保留（头部已有 08-28 接管批注）

---

## [2026-08-28] Demo 后端全删（D1 裁决：全删，后续改为产品介绍页）

### 决策依据
- 用户 2026-08-28 治理会话裁决 D1「全删」：推翻 08-26「SP-3 立论崩塌撤回」结论，与 08-27 前端下线（见上条）合并完成 Demo 全链路移除
- 后端 demo_routes.py 原「保留供未来复用」终止——产品介绍页为静态展示，不走对话/记忆链路，demo 后端无复用价值

### Files Deleted
- `api/routers/demo_routes.py`（4 端点：POST /api/demo/chat/stream、GET /api/demo/memory/recall、GET /api/demo/memory/visualization、POST /api/demo/exit）

### Files Modified
- `api/app_factory.py`：
  - 删除 `from api.routers.demo_routes import router as demo_router`（原 line 26）
  - 删除 `app.include_router(demo_router)` 挂载行（原 line 239）
  - 头部注释更新：17 include_router/204 端点 → 16 include_router/199 端点（2026-08-28 实扫）
- `tests/test_production_hardening.py`：parametrize 移除 `/api/demo/memory/recall`、`/api/demo/memory/visualization` 两行（保留 /api/mimo/status 鉴权用例）
- `tests/test_connection_lifecycle.py`：删除 `test_sse_demo_stream_closes_generator_on_disconnect`（与上方 `/api/chat/stream` 同链路用例重复覆盖）

### Impact
- API 端点：204 → **199**（create_api_app 实扫）
- include_router：17 → 16
- DECISION_LEDGER SP-3 翻案登记（附4）；FEATURE_MAP F-02 作废；CODE_GRAPH v3.3.0 同步

### Verification
- `grep -rn "demo_routes\|from api.routers.demo" api/ tests/ main.py` → 0 命中
- `python -m pytest -q` → **1030 passed + 1 skipped**（2026-08-28 实跑，107.26s）
- `npx vitest run` → **59 passed / 11 files**（2026-08-28 实跑）

### 后续计划
- 产品介绍页（原 SP-3b 设想）：公开路由静态页，展示产品定位/玩法/邀请入口，不依赖对话后端——新立项，未启动

---

## [2026-08-27] Demo 页面下线（SP-3 裁决：直接删除，系统门面后续改造为产品介绍页）

### 决策依据
- 用户 2026-08-27 明确裁决「直接删除 Demo 页面，系统门面后续做成产品介绍页面」
- SP-3（Demo 删除）属于产品功能去重，非核心价值路径
- `docs/visual-map/index.html` F-02 已确认 Demo 为独立公开页，无内部依赖

### Files Deleted
- `frontend/src/pages/DemoPage.tsx`（~250 行 Demo 体验页）
- `frontend/src/api/demo.ts`（~85 行 demo 4 个端点封装：chat/stream、memory/recall、memory/visualization、exit）

### Files Modified
- `frontend/src/App.tsx`：
  - 删除 `const DemoPage = lazy(() => import('./pages/DemoPage'))`（line 28）
  - 删除 `<Route path="/demo" ...>` 路由（line 116）
  - 更新公开路由注释：「公开路由：登录页 + Demo 体验」→「公开路由：登录页」
- `frontend/src/pages/LoginPage.tsx`：
  - 删除"Demo 入口"按钮（line 219-228，连同其 `navigate('/demo')` 调用）
  - 登录页底部无外部跳转入口
- `docs/visual-map/index.html` 后续：F-02 卡片应标记为已废弃（视觉地图静态产物，不在本次范围内）

### Impact
- 前端净删除：~335 行
- 路由数：原 19 个公开+受保护路由 → 18 个（删除 /demo）
- API 端点：原 5 个 demo 端点（`/api/demo/*`）→ 0 个；后端对应实现（`api/routers/demo_routes.py`）未触碰（保留供未来复用，无需迁移）
- 安全性：消除未鉴权公开访问入口（虽然 Demo 体验页本身不暴露敏感数据）

### Verification
- `grep -r "DemoPage\|/demo\|api/demo" frontend/src/` → 0 命中
- `npm run build` / `tsc --noEmit` 待跑（前端测试不在 Python pytest 范围）
- 后端测试基线 1033 passed, 1 skipped 无变化

### 后续计划
- 「系统门面改造为产品介绍页」作为新独立任务（暂命名 SP-3b）
- 目标：在原 /demo 路径（公开页）上做产品介绍/导航/快速演示
- 入口可能从 LoginPage 底部或 Sidebar 顶部提供

---

## [2026-08-27] 微信本地解密项目剥离（用户 08-27 批准「先把这个剥离出来」）

### 根因
微信克隆的解密程序（依赖微信进程 + Windows API）必须运行在用户本机电脑，放到云服务器上是逻辑硬伤。
虽然 `api/routers/clone_routes.py` 已在 7-27 重构时仅保留 `/api/clone/upload`，但 `clone_training/`、
`weclone_adapter/`、`voice/clone_data_manager.py` 仍保留了"调用本地解密"的旁路（wcf/wechatmsg/decrypt）。
本轮彻底剥离，确保云端 100% 不可能触发任何本地解密路径。

### Files Deleted
- `clone_training/wechat_decrypt_source.py`（300+ 行，`DecryptSource` 类 + `DecryptSourceError` + `wechat-decrypt` 适配层）

### Files Rewritten (剥离死分支)

#### `clone_training/data_extractor.py` (509 → 252 行)
**删除方法**：
- `extract_from_wcf`（来源1：WeChatFerry RPC，需本机微信进程）— 70 行
- `extract_from_wechatmsg`（来源2：WeChatMsg SQLite，已解密数据库）— 100+ 行
- `extract_from_decrypt`（来源4：wechat-decrypt 4.x 解密，调用已删除的 `DecryptSource`）— 40+ 行
- `_process_wcf_messages`（WCF 辅助）
- `_build_conversations_from_rows`（SQLite 辅助）
- `_date_to_timestamp`（辅助）— **注**：仍需保留在 `extract_from_txt` 中？检查后实际未删除

**保留方法**：
- `extract_from_export`（来源3：txt/csv/json 文件导入）— 云端可用
- `_extract_from_json / _csv / _txt`
- `_process_raw_messages / _is_system_message / _empty_result / save_to_json`

#### `weclone_adapter/adapter.py` (206 → 230 行)
**改动**：
- `clone()` 的 `source` 默认值：`"wcf"` → `"auto"`
- `_extract()` 移除 `wcf / wechatmsg / decrypt` 三个分支
- 拒绝调用：source 不在 `("txt", "csv", "json", "auto")` 时 logger.error + 返回 `[]`
- `health_check()` 增加 `wechat_local_decrypt_stripped: True` 与 `supported_sources: ["txt", "csv", "json", "auto"]`

#### `voice/clone_data_manager.py` (378 → 354 行)
**改动**：
- 移除 `_get_contacts_from_decrypt` 方法（line 73-86，含 `from clone_training.wechat_decrypt_source import DecryptSource`）
- 移除 `get_contacts` 中的"优先从解密数据库获取"逻辑
- 移除 `import time`（仅在已删除的缓存逻辑中使用）
- docstring 标注"剥离历史"

#### `tests/test_request_context_isolation.py` (141 → 120 行)
**删除测试**：
- `test_clone_preview_uses_injected_local_extractor` — 该函数已随 Option B 后端清理删除，测试现已是孤立代码

#### `tests/test_api_routes.py`
**测试断言更新**（不是删除，是更新数字）：
- `(clone_routes, 9, "clone/*")` → `(clone_routes, 8, "clone/* (2026-08-27 剥离 /api/clone/preview 死路径)")`
- `test_total_contribution_is_74` → `test_total_contribution_is_73`（73 = 11+11+9+7+9+6+12+8）

### Files Intentionally NOT Touched
- `wechat_direct/` 整个目录 — 这是微信消息收发通道（不是解密），保留
- `wechat_direct/wechat_connector.py`（line 22 logger）— 消息通道
- `main.py`（line 295 `from wechat_direct import WeChatConnector`）— 启动消息通道
- `api/deps.py`、`api/run_api.py`、`api/routers/chat_routes.py`、`api/routers/misc_routes.py` — 全部是消息收发，与解密无关
- `tests/test_wechat_connector.py` — 测试消息收发，不是解密

### Impact
- **代码精简**：约 -300 行（wechat_decrypt_source.py 整体 + data_extractor.py 减半 + adapter.py 微调）
- **剥离原则**：100% 云端可用的克隆路径只支持文件导入（txt/csv/json/auto），不依赖本机微信进程
- **安全性**：杜绝任何代码路径触发本机微信内存密钥提取
- **向后兼容**：API `/api/clone/upload`（已存在）保持不变，仍是生产路径
- **测试基线**：1033 passed, 1 skipped（无新增失败；2 个测试因 Option B 调整数字，已更新）

### Verification
- `python -c "from clone_training.data_extractor import DataExtractor; print([m for m in dir(DataExtractor()) if 'extract' in m])"` → `['extract_from_export']`
- `python -m pytest tests/test_api_routes.py` → 15/15 passed
- `python -m pytest tests/test_request_context_isolation.py` → 3/3 passed
- `python -m pytest tests/` → **1033 passed, 1 skipped**（全量回归无失败）

### 后续待办
- `third_party/wechat-decrypt/` 目录已 .gitignore 忽略，无需操作
- 旧数据集中标记 `source: "decrypt"` / `source: "wcf"` / `source: "wechatmsg"` 的条目仍存在（已 JSON 落盘），仅影响 `_detect_source` 返回值显示，不影响功能

---

## [2026-08-27] 接管基线收尾 — clonePreview 死代码 + 幽灵层三页（SP-9 裁决：直接删除）

### Dead Exports Removed (Frontend)
- `src/api/clone.ts` — Removed `clonePreview()` 函数。对应后端 `POST /api/clone/preview` 端点已于克隆下线（Option B）时删除，前端零调用。`ClonePersonaPreview` 接口保留（`cloneUpload` 仍在使用）。

### Ghost Pages Deleted (SP-9, 用户 08-27 批准「直接删除」)
三页互链完整但路由已全部摘除（App.tsx 零挂载），属微信↔角色绑定功能的半成品：
- `src/pages/UsersPage.tsx` + `src/tests/components/UsersPage.test.tsx`
- `src/pages/BindingDetailPage.tsx` + `src/tests/components/BindingDetailPage.test.tsx`
- `src/pages/UserWorkspace.tsx` + `src/tests/components/UserWorkspace.test.tsx`

### Transitively Dead Code Removed (跟随幽灵层失去全部消费者)
- `src/api/users.ts` — 整文件删除（listUsers/getUserDetail/getUserChatHistory/getUserEmotion/setUserRole/resetUser/deleteUser/toUserDisplay 及相关接口；消费者仅 UsersPage 与 client 聚合导出）。注意：管理后台 AdminUsersPage 使用独立的 `api/admin.ts`，不受影响。
- `src/api/wechat.ts` — 删除绑定管理区块（bindWechat/listMyBindings/updateBinding/unbindWechat + WechatBindingDTO）。后端 `/api/wechat/bind*` 端点保留未动，未来复用无需迁移。
- `src/hooks/useQueries.ts` — 删除零消费者的 `useWechatBindings()` hook。
- `src/api/client.ts` — 同步清理 users 组导入/导出与绑定函数聚合。
- `src/tests/components/WeChatPage.test.tsx` — 移除 mock 工厂中的 bindWechat/unbindWechat 字段及过时注释。

### Impact
- 前端净删除约 -1000 行；活页 WeChatPage/AdminUsersPage 零影响
- 后端无任何改动
- 验证：tsc --noEmit 通过 + vitest 全量通过（数字见 commit 时点）

---

## [2026-07-14] Routing Fix — Tombstone & Dead Code Cleanup

### Dead Endpoints Removed
- `api/routers/misc_routes.py` — Removed shadowed `/api/health` endpoint (3 lines). This was superseded by `api/health_routes.py` which is mounted first in `app_factory.py`. Having two `/api/health` routes caused confusion during debugging.

### Dead Imports Removed
- `api/app_factory.py` — Removed unused `Limiter`, `SlowAPIMiddleware`, `get_remote_address` imports from slowapi. Only `RateLimitExceeded` is used (for exception handler). The custom fallback rate limiter serves as the actual enforcement mechanism.
- `api/main_routes.py` — Removed unused `APIRouter` import and dead `router = APIRouter(tags=["main"])` variable (tombstone from when `health_router` was extracted to `health_routes.py`). The `router` variable was never imported by any file.

### Test Artifacts Deleted
- `tests/_test_invite.db` — Leftover SQLite test database artifact from invite code tests.

### Documentation Updated
- `docs/CODEMAPS/BACKEND.md` — Added `health_routes.py` and `runtime_config.py` to architecture tree; updated `misc_routes.py` endpoint count (10→9); updated `main_routes.py` description to reflect it no longer contains a router instance.

### Files NOT Removed (Intentionally Retained)
- `shisi/api/v2/health_routes.py` — Defines a `/health` route in the v2 API namespace. The entire `shisi/api/v2/` module (`v2_router`) is never mounted in `app_factory.py`. However, this is part of the shisi v2 API layer and may be activated in future integration work. Left intact to avoid breaking import chains.
- `shisi/memory/legacy/` — Despite the "legacy" name, these modules are actively imported by `shisi/application/memory_service.py` and covered by `tests/test_memory.py` + `tests/test_memory_pipeline.py`. Not dead code.
- `shisi/knowledge/legacy/` — Despite the "legacy" name, `rag_engine.py` is actively imported by `shisi/knowledge/legacy/__init__.py` and tested by `tests/test_rag_engine.py`. Not dead code.

### Impact
- Lines removed: ~15 (dead code + dead imports)
- No functional changes — all removed code was either shadowed or never executed
- Tests: 77/77 passed after cleanup (test_api_routes + test_production_hardening + test_ops_lifecycle + test_invite_codes + test_connection_lifecycle + test_p0_fixes + test_config_permissions)

---

## [2026-06-03] Dead Code Cleanup Session

### Unused Dependencies Removed
- echarts@^2.15.0 - No imports in any source file; manualChunks entry in vite.config.ts also cleaned up
- @testing-library/user-event@^14.6.1 - No imports in any source or test file

### Unused Files Deleted — Frontend

**Unused common/ components (6 files):**
- src/components/common/Card.tsx - Only used by ProactiveEnginePanel (also dead, transitively dead)
- src/components/common/EmptyState.tsx - Only used by MessageList (unused chat component)
- src/components/common/ProactiveEnginePanel.tsx - No external consumers
- src/components/common/SensitiveInput.tsx - No external consumers
- src/components/common/Skeleton.tsx - No external consumers (shared/Skeleton.tsx kept — used by pages)
- src/components/common/UrgencyBadge.tsx - No external consumers

**Unused shared/ duplicates (6 files):**
- src/components/shared/AnimatedNumber.tsx - No consumers; only in barrel export
- src/components/shared/DangerButton.tsx - No consumers; only in barrel export
- src/components/shared/ParallaxTilt.tsx - No consumers; only in barrel export
- src/components/shared/ProgressBar.tsx - No consumers; only in barrel export
- src/components/shared/StaggerContainer.tsx - No consumers; only in barrel export
- src/components/shared/Tabs.tsx - No consumers; only in barrel export
- src/components/shared/Tooltip.tsx - No consumers; only in barrel export

**Unused chat components (5 files):**
- src/components/chat/ChatInput.tsx - No external imports
- src/components/chat/MessageBubble.tsx - Only used by MessageList (also dead)
- src/components/chat/MessageList.tsx - No external imports
- src/components/chat/ProactiveToast.tsx - No external imports
- src/components/chat/TypingIndicator.tsx - No external imports

**Unused hooks (4 files):**
- src/hooks/useDashboardData.ts - No external imports
- src/hooks/useSmartPoll.ts - Only used by useDashboardData (also dead)
- src/hooks/useSSE.ts - No external imports
- src/hooks/useWebSocket.ts - No external imports

**Unused stores (2 files):**
- src/store/logStore.ts - No imports anywhere
- src/store/settingsStore.ts - No imports anywhere

**Unused types (1 file):**
- src/types/sticker.ts - No imports anywhere

**Unused page (1 file):**
- src/pages/AdminInvitesPage.tsx - Not imported in App.tsx or any other file

**Unused API module (1 file):**
- src/api/invites.ts - Only used by AdminInvitesPage (also dead)

### Barrel Files Cleaned Up
- src/components/common/index.ts — Removed 6 dead re-exports (Card, Skeleton, EmptyState, UrgencyBadge, ProactiveEnginePanel, SensitiveInput)
- src/components/shared/index.ts — Removed 9 dead re-exports (Select, ProgressBar, Skeleton, Toast, EmptyState, Badge, Tabs, Tooltip, AnimatedNumber, StaggerContainer, ParallaxTilt, DangerButton). Note: Select, Skeleton, EmptyState, Badge were RESTORED after discovering pages import them via barrel.

### Pre-existing Bugs Fixed
- src/api/client.ts:256,275 — Added missing imports of psychProfile, psychSnapshots, psychReset, psychMentalHealth, psychLiwc from ./system (caused TS2304 errors)

### Impact
- Files deleted: 25
- Dependencies removed: 2
- Lines of code removed: ~3,800 (estimated)
- Bundle size reduction: recharts removed from manualChunks (~45 KB gzip savings potential)
- TypeScript errors fixed: 10 (pre-existing psych* import bugs)

### Testing
- 	sc --noEmit — 0 errors
- ite build — passed (2186 modules, 1.20s)
- itest run — 4/4 frontend tests passed
- pytest -x -q — 625/625 Python tests passed (1 skipped)

### Notes
- common/EmptyState.tsx and common/Skeleton.tsx were kept restored through shared/ because pages import them via barrel
- common/Badge.tsx kept (used by KnowledgePreview, StorylineEditor, StorylineIndicator)
- shared/Select, Skeleton, EmptyState, Badge RESTORED after initial deletion — pages use them via barrel imports
- Legacy backend pi/_*_routes.py files NOT removed — they coexist with pi/routers/ in app_factory.py; only a full endpoint diff can confirm redundancy
- eact-window and eact-virtualized-auto-sizer kept — used via equire() in MessageList.tsx even though MessageList is unused
