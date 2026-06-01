# Bus Factor 缓解计划

> 当前 Bus Factor = 1（唯一开发者：默默）
> 目标：Bus Factor ≥ 2（AI Agent 可作为第二个维护者）

## 模块可维护性分级

| 模块 | Agent 可维护？ | 依据 |
|------|:---:|------|
| `api/routers/` | 🟢 高 | 统一 FastAPI 模式，CRUD 模板化 |
| `frontend/src/pages/` | 🟢 高 | React + Ant Design 标准模式 |
| `frontend/src/api/` | 🟢 高 | 领域驱动 client 拆分，结构清晰 |
| `voice/` | 🟡 中 | 多引擎适配器模式，新引擎可复制模板 |
| `wechat_direct/` | 🟡 中 | 微信 API 封装，但认证流程需文档 |
| `my_character/` | 🟡 中 | 情感引擎逻辑复杂，但有类型注解 |
| `shisi/` | 🔴 低 | 旧架构，耦合度高，逐步废弃 |
| `api/main_routes.py` | 🟡 中 | 80+ 端点集中，但每个端点独立 |
| `main.py` | 🔴 低 | 双编排器模式 + 启动流程复杂 |
| `orchestrator.py` | 🔴 低 | 12 步流水线，依赖链深 |

## Agent 可接手条件

要让 AI Agent 能独立维护一个模块，需满足：
1. **代码地图文档**：模块在 `8-layer-code-map.md` 中有条目
2. **接口契约**：输入/输出类型明确（TypeScript 类型 / Python type hints）
3. **测试覆盖**：有可运行的测试验证行为
4. **交接笔记**：≤500 字说明模块职责 + 关键决策

## 季度审查清单

- [ ] 确认至少 3 个模块满足 Agent 可接手条件
- [ ] 更新 "Agent 可维护" 分级
- [ ] 记录本次季度审查中新增的可维护模块
- [ ] 检查 `shisi/` 废弃进度（目标：减少 🔴 模块数）

## 紧急交接包

如果 Bus Factor 突然变为 0（开发者不可用），以下是外部接手的最小路径：

1. **启动项目**：`pip install -e ".[dev]"` → `python main.py`
2. **理解架构**：读 [`docs/architecture/8-layer-code-map.md`](8-layer-code-map.md)（15 分钟）
3. **关键决策**：读 [`docs/adr/`](../adr/) 中的 6 个 ADR（20 分钟）
4. **微信对接**：扫码登录 → 微信发消息 → 观察回复
5. **修改代码**：改 `api/routers/` 或 `frontend/src/pages/`（最安全区域）
