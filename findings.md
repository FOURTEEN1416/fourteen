# 修复发现

## 已确认根因
- `AI_GF_ENV=prod` 未被安全代码读取，运行时仍为 `debug=True` 并使用开发 JWT。
- SlowAPI 已安装但未配置默认限额/中间件/异常处理，61 次请求全部返回 200。
- 应用读取 `APP_DATABASE_URL`，部署与备份统一写 `DATABASE_URL`，运行时退回 SQLite。
- MiMo TTS 全部路由未认证；Demo 记忆路由读取全局记忆。
- Ruff 报告多个 F821；后端测试为 977 passed / 5 failed / 2 errors。

## 不可误删项
- `shisi/memory/legacy/_legacy_*.py` 被 `memory_pipeline.py` 直接导入。
- `character_card/models.py` 虽标记 deprecated，仍被 parser/integration/orchestrator 调用。

## 官方文档结论
- SlowAPI 仅创建 `Limiter` 不会自动保护路由；需默认限额或装饰器，并挂载 ASGI 中间件与 429 处理器。
