# HANDOFF — 工作交接

> 源会话：2026-05-31 全面审计
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

`arch/client-split-4A`（比 main 领先约 20 个 commit，265 文件变更）

---

## 本次审计（2026-05-31）

| 检查项 | 结果 |
|--------|------|
| tsc --noEmit | ✅ 零错误 |
| 后端测试 | ✅ 524 passed, 1 skipped (527 items) |
| 后端路由 | ✅ 162 条 |
| 前端页面 | ✅ 13 页面全注册路由 |
| StorylinePage | ✅ 路由已注册，组件就绪 |
| shisi 旧路由 | ⚠️ system.ts 中 26 处残留 |
| RoleSettings mock | 🔴 29 处 MOCK_CHARACTER |
| StatusCenter | 🔴 空壳（51行） |
| ADR 体系 | ⚠️ 编号不连续，COMPASS ADR 无物理文件 |

---

## 当前健康度

| 维度 | 评分 | 说明 |
|------|------|------|
| 后端完整性 | 92% | 162路由齐备，524/527测试通过 |
| 前端路由 | 85% | 13页面全注册，StorylinePage就绪 |
| Mock治理 | 60% | RoleSettings仍全mock，其余已修 |
| 测试覆盖 | 5% | 后端有测试，前端零测试 |
| 架构治理 | 70% | 三体文件齐全，ADR→FF映射67% |
| Build 健康度 | 100% | tsc 零错误 |

## 剩余待办

### P0 — 立即修复
- [ ] RoleSettings: 替换 MOCK_CHARACTER 为真实 API（29处引用，564行）
- [ ] StatusCenter: 接入 stats/dashboard API（当前空壳51行）

### P1 — 本周
- [ ] system.ts: 清理 26 处 /shisi/* 旧路由 → 迁移到新 /api/* 端点
- [ ] COMPASS.md ADR-011/012/013 补物理文件到 docs/adr/
- [ ] 统一 ADR 编号（0001-0006 vs 011-013）
- [ ] 修复 llm_providers.json UTF-8 BOM

### P2 — 加固
- [ ] Vitest + RTL 前端测试
- [ ] FF-011/012 自动化脚本
- [ ] CONTRIBUTING.md 贡献指南
- [ ] Bus Factor 改善（文档 + 知识转移）

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
| .triad-navigation | C:\Users\FOUR\Desktop\ai-girlfriend\.triad-navigation\ |

## 设计稿保护
已清除（ADR-012）。前端设计以代码为准。
