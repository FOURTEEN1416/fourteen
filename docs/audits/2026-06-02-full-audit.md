# 全维度架构审计报告 — 2026-06-02

> **审计范围**：AI Girlfriend 整个代码库（前端 15 routed pages / 后端 145 端点 / 数据库 / 部署 / 流程 / CI）
> **审计时间**：2026-06-02 22:30 ~ 23:45
> **审计者**：4 维度并行 subagent（后端架构 / 前端架构 / 安全配置 / ADR-FF-CI 体系）
> **代码版本**：`38a3d71`（== origin/main，已推送）
> **测试基线**：625 passed, 1 skipped, 0 failed（1m19s）+ vitest 4/4 + Playwright 3/3

---

## 0. 一句话总结

**生产可上**，但有 **8 个 P0 债**必须在首批内测用户（>10 人）使用前修完：

| P0# | 文件:行 | 性质 | 修复时长 |
|---|---|---|---|
| P0-1 | `frontend/src/api/invites.ts:59` | 前端双重 baseURL → `/api/api/...` | 5 分钟 |
| P0-2 | `api/auth_jwt.py:35` | JWT_SECRET 弱默认 → 任意伪造 admin token | 10 分钟 |
| P0-3 | `.env` | 真实 zhipu/xunfei/baidu API Key 落盘 | 30 分钟（含轮换） |
| P0-4 | `frontend/src/store/authStore.ts:15` | 违反 FF-0007 精神（类型层耦合 admin.ts） | 5 分钟 |
| P0-5 | `frontend/src/components/auth/RoleGuard.tsx:44` | 不校验 `is_active` → 被禁 admin 仍能进 | 5 分钟 |
| P0-6 | `frontend/src/store/authStore.ts:82-88` | JWT 存 localStorage + 无 CSP → XSS 直读 | 1 小时 |
| P0-7 | `api/orchestrator.py`（双编排器签名不兼容） | Orchestrator 4th arg=engine vs OptimizedOrchestrator 4th arg=character_id | 1 小时 |
| P0-8 | `api/_chat_routes.py:67-84` | chat_stream 调不存在的 `process_message_stream` → 503 死锁 | 30 分钟 |

**P0 总工作量：约 4 小时**（1 个 session）。

---

## 1. 后端架构（58/100）⚠️

### 1.1 现状

- **145 端点**，11 域路由（chat / clone / misc / personality / safety / tools / training / users / auth / admin / story / invite）
- **SQLAlchemy 异步**，模型 19 张表，分库清晰
- **慢 API 风险**：chat_history 在聊天中拉取整表历史（P1-9）
- **死代码**：双编排器并存（Orchestrator 已废弃但还活着）

### 1.2 P0（2 个）

#### P0-7 — 双编排器签名不兼容（语义灾难）
- **位置**：`api/orchestrator.py`（旧类）vs `main.py` OptimizedOrchestrator（新类）
- **现象**：`_chat_routes.py:35-53` 用 4 参数 `process_message(user_id, msg, ctx, character_id='default')`，但旧 `Orchestrator.process_message` 第 4 参数是 `emotion_engine`
- **影响**：当后端回退到旧 Orchestrator（fallback 路径），chat 静默错位 — character_id='default' 被当 emotion_engine 传入，**用户消息发错角色**或崩 500
- **修复**：删 `Orchestrator` 类，全局 `from .orchestrator import Orchestrator` 改为 `OptimizedOrchestrator`；跑 P0 回归测试

#### P0-8 — chat_stream 死锁 503
- **位置**：`api/_chat_routes.py:67-84` `/api/chat/stream`
- **现象**：调 `orchestrator.process_message_stream()` — 但 OptimizedOrchestrator 根本没这个方法，只有 `process_message`
- **影响**：SSE 流式端点 100% 503，但 OpenAPI 文档显示可用 → 客户端 fallback 到轮询时用户感知极差
- **修复**：在 OptimizedOrchestrator 加 `async def process_message_stream(...)` 生成器；或路由改为非流式

### 1.3 P1（6 个）

| P1# | 文件:行 | 问题 | 修复 |
|---|---|---|---|
| P1-1 | `api/_chat_routes.py:35-53` | chat_history 整表查，无分页 | 限 `limit=50` + 索引 `idx_chat_msg_user_session_ts` |
| P1-2 | `api/routers/invite_routes.py` | ruff 26 + mypy 2 让 FF-020 失守 | 加 type hint + 拆函数 <50 行 |
| P1-3 | `api/database.py` | 同上 ruff/mypy 报错 | 加 type hint |
| P1-4 | `api/main_routes.py:51-55` | ChatRequest character_id 可空 → fallback 'default' | 强制必填 |
| P1-5 | `api/_users_routes.py` | 用户列表 `OFFSET 0 LIMIT 50` 全表扫 | 改 keyset pagination |
| P1-6 | `api/_personality_routes.py` | persona_card 解析跑在每次 GET | 加 LRU cache |

### 1.4 P2（4 个）
- `api/_safety_routes.py` 日志未脱敏，IP 直写 DB
- `api/_training_routes.py` 训练任务无超时
- `api/_misc_routes.py` 旧 GET /health 不带 orchestrator 健康
- `api/_tools_routes.py` tool_history 不分页

### 1.5 亮点
- async SQLAlchemy 干净
- 路由按域拆分 8 文件，可读性 +1
- 测试基线 625 passed 0 failed（LLM/integration 1m19s）
- CORS/HSTS/JWT 工具独立
- 邀请码 P0 投产功能完整

---

## 2. 前端架构（B+ ~ 75/100）

### 2.1 现状
- **15 页面 15 routed 0 orphan**（2026-05-31 投产审计后已修复）
- React 18 + Vite 5 + TS + Tailwind，401 自动刷新队列实现佳
- 路由守卫（AuthGuard/RoleGuard/ProtectedLayout）覆盖完整
- 但**死代码 1 处**、**重复类型 2 处**、**旧路径引用 6 处**未清理

### 2.2 P0（3 个）

#### P0-1 — 双重 baseURL（最紧急）
```ts
// frontend/src/api/invites.ts:59
const BASE = '/api'  // ← 这行

// client.ts 已配 baseURL='/api'
// 实际请求路径变成 /api/api/auth/register-invite
```
- **影响**：注册用邀请码 → 后端收 `/api/api/...` 404 → 用户卡注册页
- **修复**：`BASE = ''`（5 行 × 3 调用）
- **验证**：实测后端 `/api/auth/register-invite` 存在，不接受 `/api/api/...`

#### P0-4 — authStore 违反 FF-0007
```ts
// frontend/src/store/authStore.ts:15
import type { UserRole } from '../api/admin'  // ← 注释说"不 import API"，但 import 了类型
```
- **影响**：精神违背（store 不应与 API 模块耦合），重构时 admin.ts 改 user 字段需同步改 store
- **修复**：在 `frontend/src/types/auth.ts` 定义共享类型，admin.ts + authStore.ts 都从 types 引

#### P0-5 — RoleGuard 不校验 is_active
```tsx
// frontend/src/components/auth/RoleGuard.tsx:44
if (!user || !roles.includes(user.role)) {
  return <Navigate to={fallback} replace />
}
```
- **影响**：被禁用管理员（`is_active=false`）仍能进 `/admin/invites` 等
- **修复**：`if (!user || !user.is_active || !roles.includes(user.role))`

### 2.3 P1（5 个）

| P1# | 文件:行 | 问题 |
|---|---|---|
| P1-7 | `frontend/src/api/invites.ts` + `frontend/src/api/auth.ts` | `RegisterInviteRequest`/`TokenResponse` 两份定义 |
| P1-8 | `frontend/src/hooks/useAuth.ts:18-27` | `init()` 函数 0 调用，**真死代码**（已 grep 验证） |
| P1-9 | `frontend/src/api/client.ts:226-285` | 同一组函数 3 套导出（named re-export + namespace `api` + 字段别名） |
| P1-10 | `frontend/src/api/system.ts` | 引用 6 处旧 `shisi/*` 路径（后端已删 v2） |
| P1-11 | `frontend/src/pages/LoginPage.tsx` | `err as { response?: { data?: { detail?: string } } }` 强制断言 |

### 2.4 P2（3 个）
- lucide-react 31.92MB / recharts 4.43MB / framer-motion 4.51MB 依赖体积
- NotFoundPage 动画 y 偏移
- 21 个旧 console.log 残留

### 2.5 亮点
- 401 自动刷新队列实现佳（client.ts:84-161）
- 路由守卫三层（AuthGuard/RoleGuard/ProtectedLayout）覆盖完整
- 动画统一 AnimatedPage 包装，无 y 抖动
- 4 vitest + 3 Playwright 全过

---

## 3. 安全配置（60/100）🔴

### 3.1 现状
- **8 个 P0/P1 安全债**，含 3 个 P0（必须修）
- CORS/HSTS/限流基础有
- 错误处理脱敏做了一半

### 3.2 P0（3 个）

#### P0-2 — JWT_SECRET 弱默认（高危）
```python
# api/auth_jwt.py:35
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-jwt-secret-change-in-production-32chars!")
```
- **影响**：生产忘配环境变量 → 任意用户用 secret 伪造 admin token → 完整接管系统
- **修复**：
```python
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET or len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must be set and >=32 chars")
JWT_ALGORITHM = "HS256"
```

#### P0-3 — 真实 LLM API Key 落盘
```
.env:
  ZHIPUAI_API_KEY=<real-key>
  XUNFEI_APP_ID=<real-id>:<real-key>
  BAIDU_API_KEY=<real-key>
```
- **影响**：误同步到云盘/截图泄漏 = 盗刷 LLM 配额（zhipu/baidu 按 token 计费可破产）
- **修复**：
  1. 立即轮换 3 个 Key（旧 Key 立即作废）
  2. 移到用户级 `%USERPROFILE%\.config\ai-girlfriend\.env`（不入仓）
  3. `.env.example` 只保留字段名
  4. 加 pre-commit hook 检测 `grep -E 'sk-[a-zA-Z0-9]{20,}'`

#### P0-6 — JWT 存 localStorage + 无 CSP
```ts
// frontend/src/store/authStore.ts:82-88
{
  name: 'auth-storage',  // ← zustand persist 默认 localStorage
  partialize: (state) => ({ accessToken: state.accessToken, ... })
}
```
- **影响**：XSS → 直接读 `localStorage.auth-storage.accessToken` → 完整接管
- **修复**：
  1. accessToken 存内存（zustand 不开 persist），refreshToken 走 httpOnly cookie
  2. 后端加 `Content-Security-Policy: default-src 'self'` 中间件
  3. /api/auth/login 设 `Set-Cookie: refresh_token=...; HttpOnly; Secure; SameSite=Strict`

### 3.3 P1（5 个）
| P1# | 位置 | 问题 |
|---|---|---|
| P1-12 | `api/app_factory.py:65-75` | CORS 默认 `http://localhost:5173` 兜底，生产只 warn 不 fail-fast |
| P1-13 | `api/_safety_routes.py` | 安全日志未脱敏（IP/UA 直写） |
| P1-14 | `api/_training_routes.py` | 训练任务可触发 LLM 无限循环（无超时） |
| P1-15 | `frontend/src/api/client.ts` | refresh_token 也走 localStorage（P0-6 子集） |
| P1-16 | `deploy/.env.production:53` | `API_KEY=CHANGE_ME_...` 默认值 |

### 3.4 P2（6 个）
- 错误响应泄漏内部栈（P0-C 已修大部分）
- 注册接口无图形验证码
- 密码 hash bcrypt rounds=10（建议 12）
- refresh_token 无旋转
- 缺少 audit log 持久化
- 无 rate limit per user

### 3.5 亮点
- bcrypt + JWT 工具独立
- 安全响应头中间件完整（HSTS / X-Frame-Options / X-Content-Type-Options）
- 4 个独立安全模块（ContentSafetyFilter/EncryptionManager/PIIAnonymizer/PromptInjectionDetector）

---

## 4. ADR-FF-CI 体系（B+ 70/100）

### 4.1 现状
- **23 个 Fitness Functions**（FF-0001 ~ FF-0023），21 个被 CI 覆盖
- **10 个活跃 ADR**（0001-0014，编号 0007-0010 跳跃 = 砍掉的 ADR 占位）
- **CI 7 job 在线**（pytest / lint / type / build / vitest / playwright / adr-integrity）
- **新代码让 FF-020 失守**（ruff 0→26、mypy 0→2）

### 4.2 P0（1 个 — 体系类）

#### P0 体系债 — 周期任务 cron 不跑 lint/type/E2E
- **位置**：`.github/workflows/ci.yml` cron 段
- **现象**：
  - PR push 跑全 7 job（合规）
  - **daily/weekly cron 只跑 pytest**（FF-021 覆盖），不跑 ruff/mypy/vitest/playwright
- **影响**：新代码合 main 后，**主分支 FF-020 失守 26 ruff + 2 mypy**（来自 invite commit 881f2d4）
- **修复**：
```yaml
on:
  schedule:
    - cron: '0 6 * * *'  # 每天早 6 点
jobs:
  full-audit:
    steps:
      - run: ruff check api/ tests/
      - run: mypy api/
      - run: cd frontend && npm run lint
      - run: cd frontend && npm run type-check
      - run: cd frontend && npm run test
      - run: cd frontend && npx playwright test
```

### 4.3 P1（5 个）
| P1# | 位置 | 问题 |
|---|---|---|
| P1-17 | `docs/adr/0001-0006` | 缺日期字段（仅 0011/0012/0013 带日期） |
| P1-18 | `docs/adr/0001-0006` | Nygard 格式 vs 0011-0013 MADR 格式双标 |
| P1-19 | `docs/adr/0002` | `Supersedes ADR-1.A` 含小数点，CI 正则 `\d+` 匹配不到（adr-integrity job 静默放行） |
| P1-20 | `docs/adr/` | 编号 0007-0010 跳跃为砍掉的 ADR 占位，无 `0007-0010-skipped.md` 留痕 |
| P1-21 | `triad-navigation/CONTROL.md` | FF-021（pytest）0 回归 / FF-022（vitest）0 错 / FF-023（playwright）0 错 已写入但 FF-020（lint）未写 |

### 4.4 P2（3 个）
- 周期任务 Slack/邮件通知未配
- adr-integrity job 静默放行（P1-19 子集）
- docs/audits 目录为空（今天才建）

### 4.5 亮点
- 23 个 Fitness Functions 设计完整
- 7 个 CI job 设计合理
- 7 条设计原则清晰（compass.md）
- 8 层代码地图完整（map.md）

---

## 5. 修复顺序建议（最小可行路径）

### Phase 1 — 阻止生产事故（4 小时，必做）
```
1. P0-1  双重 baseURL          (5 min)  → 1 行
2. P0-2  JWT_SECRET 弱默认     (10 min) → 1 函数
3. P0-4  authStore FF-0007 违  (5 min)  → 1 文件
4. P0-5  RoleGuard is_active   (5 min)  → 1 行
5. P0-7  双编排器签名           (1 hr)  → 删类 + 回归
6. P0-8  chat_stream 死锁      (30 min) → 加方法
```

### Phase 2 — 阻止安全事件（1 小时）
```
7. P0-3  API Key 轮换 + 移位  (30 min)
8. P0-6  JWT 迁 httpOnly cookie (1 hr)
```

### Phase 3 — 体系兜底（30 分钟）
```
9. P0 体系债  CI cron 跑 lint/type/E2E (30 min)
```

### Phase 4 — P1 清理（2-3 小时，可分批）
```
10-18. P1 18 项 → 路由到合适 subagent
```

---

## 6. 不要做的事

- ❌ 不要加 Docker（用户明确不要）
- ❌ 不要加支付系统（用户明确不做商业化）
- ❌ 不要把邀请码/支付/合规当成 P0（属 Phase 4 之后）
- ❌ 不要为 P1 债务改架构（属纯局部修复）

---

## 7. 推荐下一步

**默默，请选一项**：

**A. 立即修 Phase 1（4 小时，1 session）**
- 修完 6 个 P0 → 即可开内测（10-50 人规模）
- 然后再处理 Phase 2/3

**B. 修 Phase 1+2（5 小时，1 长 session）**
- P0 全部清零
- 然后内测 + 接受 P1 债（暂缓修）

**C. 先暂停 — 我来写 HANDOFF.md（4 维度审计段补到 06-02 版本）**
- 不动代码，固化知识
- 然后默默选 Phase 1/2/3 时段

**D. 我自己决定走 Phase 1 → 跑通测试 → 给你结果 → 再继续**
- 闭环导向优先

我建议 **D**（直觉优先 + 闭环导向），但默默最终拍板。

---

**报告完。** 任何 P0 详情可问任何 subagent 单独深挖。
