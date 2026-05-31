# HANDOFF — 工作交接

> 源会话：2026-05-28 → 2026-05-29 三体导航审计修正
> 执行流水线：startup-calibrator → triad-navigation → loop-executor → evolution-auditor
> 协调者：歆歆

---

## 接手总结

2026-05-28 正式接手 AI Girlfriend 项目。已完成：

1. ✅ 三体导航全量审计（MAP/COMPASS/CONTROL/HANDOFF）
2. ✅ 项目结构核查 — 根级扁平结构
3. ✅ glass-card CSS 已定义 — 此前P0问题已解决
4. ✅ 前端 3 个 P1 修复（见下方"本次修复"）

## 当前分支

`arch/client-split-4A`（比 main 领先约 8 个 commit）

---

## 本次修复（2026-05-29）

| 问题 | 文件 | 操作 |
|------|------|------|
| ADR-004 标签冲突 | `frontend/src/pages/SystemSettingsLayout.tsx` | `label: '状态'` → `label: '安全'` |
| SettingsVoice 全 mock | `frontend/src/pages/SettingsVoice.tsx` | 替换为真实 API：`mimoClone/mimoDesign/mimoSynthesize/mimoSetEngine/mimoSwitchVoice/getSpeakers/mimoStatus` |
| SettingsExtensions mock MCP | `frontend/src/pages/SettingsExtensions.tsx` | 删除 `MOCK_MCP_SERVERS`，默认空列表 |
| SettingsLogs build error | `frontend/src/pages/SettingsLogs.tsx` | `useRef<ReturnType<typeof setInterval>>()` → 加 `| null` |

### ADR-004 更新
COMPASS.md 已同步更新：原则体系第 7 条已验证 — sidebar 与 tab 标签保持一致。

### 跳过项（默默决策）
- v2 路由 X-API-Key 鉴权 → **跳过**，「没必要，反正本地跑」
- UsersPage 全 mock → **未修**，默默未要求
- CreateRole/CloneTrain mock → **未修**，默默未要求

---

## 当前健康度

| 维度 | 评分 | 说明 |
|------|------|------|
| 后端完整性 | 90% | 146端点齐备，525测试通过（524 passed / 1 skipped） |
| 前端路由 | 40% | 14路由注册 + 1新(storyline待加)，21 orphan待决策 |
| Mock治理 | 40% | Voice/Extensions/Logs已修，UsersPage/CreateRole/CloneTrain仍mock |
| 测试覆盖 | 5% | 后端有测试，前端零测试 |
| 设计稿一致性 | 90% | glass-card已定义，标签冲突已修复 |
| Build 健康度 | 100% | Vite 0 error 0 warning，876ms |

## 剩余待办

### P0 — 可能需修复
- [ ] UsersPage: 替换 MOCK_USERS 为真实 API（3 个假用户）
- [ ] SettingsSecurity: 全 mock（需对接用户/系统配置 API）

### P1 — 可做
- [ ] Storyline: 添加路由 + sidebar入口（组件就绪，22700B）
- [ ] CreateRole/CloneTrain: 对接 API
- [ ] 21 orphan 页面: 决策取舍
- [ ] SettingsVoice: `voiceStatus` 和 `voiceSynthesize` 导入但未使用，可清理

### P2 — 加固
- [ ] 全局 axios 错误拦截器（各页面各处理，有重复）
- [ ] Vitest + RTL 前端测试
- [ ] Bus Factor 改善：交接文档已完成，需要至少 1 人熟悉

---

## 关键架构笔记

### 语音系统
```
voiceSynthesize (system.ts) ← POST /voice/synthesize → 旧 Edge-TTS
mimoSynthesize (mimo.ts)    ← POST /api/mimo/synthesize → MiMo Cloud
```
两者互补：`system.ts` 的 `voiceStatus/getSpeakers/voiceSynthesize` 走 `/voice/*` 路由，
`mimo.ts` 的 `mimo*/mimoClone/mimoDesign/mimoSynthesize` 走 `/api/mimo/*` 路由。
前端 SettingsVoice 两个都调，优先用 `getSpeakers` 获取列表。

### 安全系统
4 个安全模块：ContentSafetyFilter / EncryptionManager / PIIAnonymizer / PromptInjectionDetector。
前端 SettingsSecurity 页面全 mock，后端安全模块实际已启用（LLM 调用链中）。

### 后端测试
```bash
# 全量 525 测试
pytest
# 跳过慢测试
pytest -m "not slow"
```

---

## 启动命令

```powershell
# 前端 (5173)
cd frontend
D:\node.exe .\node_modules\vite\bin\vite.js --port 5173 --host

# 后端 (8000)
python main.py
```

## 关键路径

| 资源 | 地址 |
|------|------|
| GitHub | https://github.com/FOURTEEN1416/knowledge-base |
| 本地前端 | http://localhost:5173 |
| 后端 API 文档 | http://localhost:8000/docs |
| 设计概念 | file:///C:/Users/FOUR/Desktop/ai-girlfriend/concepts-overview.html |
| .triad-navigation | C:\Users\FOUR\Desktop\ai-girlfriend\.triad-navigation\ |

## 设计稿保护
以下文件体现设计原则，禁止删除/修改：
- `v2/` (frontend + root 两层)
- `concepts-overview.html`
- `.superpowers/brainstorm/`
