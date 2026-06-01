# HANDOFF — 工作交接

> 源会话：2026-06-01 全面审计 + 修复执行
> 执行流水线：startup-calibrator → triad-navigation → domain-explorer → evolution-auditor → loop-executor → constitution-guardian
> 协调者：歆歆

---

## 接手总结

2026-06-01 全面审计执行完成 + P0/P1 修复全部落地。本次覆盖：

1. ✅ 地图精度审计（MAP.md 8层全量核查）
2. ✅ 指南针对齐审计（COMPASS.md ADR+原则+API对齐）
3. ✅ 闭环控制审计（CONTROL.md FF+合规+审计节奏）
4. ✅ 代码级交叉验证（git log / CI / tsc / 路由 / mock / shisi旧路由）
5. ✅ **P0 LoginPage 接入**（AuthGuard + ProtectedLayout + /login 路由）
6. ✅ **P1 shisi 死代码清理**（17处 /shisi/* 引用从 system.ts / client.ts / useQueries.ts / useWebSocket.ts 删除）
7. ✅ **P1 Mock 复检**（RoleSettings/StatusCenter/SettingsVoice/StorylinePage 全部真实 API）
8. ✅ **P1 FF-014/015 CI 实现**（ff-auth-endpoints + ff-route-guard 已加入 ci.yml）

## 当前分支

`main`（arch/client-split-4A 已合并到 main · 最晚 commit：用户认证模块）

---

## 本次审计关键发现

### ~~🚨 P0 — 登录页孤立~~ ✅ 已修复
| 发现 | 状态 |
|------|------|
| **问题** | App.tsx 已注册 /login 路由 + ProtectedLayout + AuthGuard | ✅ |
| **影响** | 管理控制台路由守卫完整，所有受保护路由在 ProtectedLayout 内 | ✅ |

### ⚠️ 指南针偏差
| ~~发现~~ | ~~详情~~ | 状态 |
|--------|--------|------|
| ~~ADR-0014 脱节~~ | ~~未纳入 COMPASS.md + FF-014/015 未实现~~ | ✅ 已修复 |
| ~~shisi 旧路由~~ | ~~17 处 /shisi/* 引用~~ | ✅ 已清理 |
| ADR→FF 绑定率 | 57%→提升中（FF-014/015 已实现） | ↑ |

### ✅ 整体健康度

| 维度 | 评分 | 变化 |
|------|------|------|
| 后端完整性 | 90% | 155路由+84主路由，525测试通过 | → |
| 前端路由 | 100% | 15文件全部注册路由 | ↑（LoginPage已接入） |
| Mock治理 | 100% | 4页复检全部真实API | ↑（上期80%） |
| 测试覆盖 | 5% | 后端有测试，前端零测试 | → |
| 架构治理 | 78% | 三体文件齐全，CI中7FF，ADR→FF绑定提升 | ↑（上期72%） |
| Build 健康度 | 100% | tsc 零错误 | → |

---

## 剩余待办

### ✅ 本会话已完成
- [x] **P0 LoginPage 接入 App.tsx**：AuthGuard + ProtectedLayout + /login 路由全部就绪
- [x] **P1 shisi 死代码清理**（system.ts / client.ts / useQueries.ts / useWebSocket.ts）
- [x] **P1 Mock 复检**（RoleSettings/StatusCenter/SettingsVoice/StorylinePage 全部真实 API）
- [x] **P1 FF-014/015 CI 实现**（ff-auth-endpoints + ff-route-guard 已加入 ci.yml）
- [x] **auth_routes.py 注册到 app_factory.py**（POST /api/auth/login|register|refresh|logout + GET /api/auth/me）

### P1 — 待办
- ~~auth_routes.py 未注册到 app_factory.py~~ ✅ 已修复
- [ ] CONTROL.md 中 ADR-0002/0003/0004 补充对应 FF 绑定
- [ ] 三体文件文档与代码同步（ff-route-guard / ff-auth-endpoints 已实现，需跑一次 CI 确认）

### P2 — 加固
- [ ] Vitest + RTL 前端测试
- [ ] CONTRIBUTING.md 贡献指南
- [ ] Bus Factor 改善（文档 + 知识转移）
- [ ] 前端组件目录规范化（shared/ 和 common/ 边界梳理）

---

## 关键架构笔记

### 认证系统（2026-06-01 新增）
```
JWT 用户鉴权（管理控制台）   ← 新体系
X-API-Key 内部鉴权（服务间） ← 旧体系，继续使用
```
后端已就绪（auth_routes.py 5端点 + admin_routes.py 6端点），已注册到 app_factory ✅；前端全连接（LoginPage + AuthGuard + authStore）。

### 语音系统（不变）
```
voiceSynthesize (system.ts) ← POST /voice/synthesize → 旧 Edge-TTS
mimoSynthesize (mimo.ts)    ← POST /api/mimo/synthesize → MiMo Cloud
```

### 路由规模变化
| 模块 | 上期 | 本期 |
|------|------|------|
| character_routes | ~30 | 53 |
| storyline_routes | 6 | 27 |
| auth + admin | — | 15 |

### 后端测试
```bash
pytest                  # 525 用例
pytest -m "not slow"    # 跳过慢测试
```

---

## 启动命令

```powershell
# 前端 (5173)
cd frontend; D:\node.exe .\node_modules\vite\bin\vite.js --port 5173 --host

# 后端 (8000)
python -m uvicorn api.run_api:app --reload --host 0.0.0.0 --port 8000

# 一键启动
.\start_all.cmd
```

## 关键路径

| 资源 | 地址 |
|------|------|
| GitHub | https://github.com/FOURTEEN1416/knowledge-base |
| 本地前端 | http://localhost:5173 |
| 后端 API 文档 | http://localhost:8000/docs |
| .triad-navigation | C:\Users\FOUR\Desktop\ai-girlfriend\.triad-navigation\ |
