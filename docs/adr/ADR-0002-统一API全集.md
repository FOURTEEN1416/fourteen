# ADR-0002：采用统一 API 全集

## 状态
Accepted

## 上下文
项目存在三套角色/功能 API 并行运行：
1. **shisi v1** (`/api/shisi/...`) — 旧版完整模块（49 端点），耦合度高
2. **shisi v2** (`/v2/...`) — 新版架构，CharacterService + SQLite 存储，但前端未对接
3. **unified** (`/api/characters/...`) — 前端当前对接的 API，功能不完整

三套并行导致：
- 新增功能需改 3 个地方
- 前端需理解两到三套数据格式
- 测试覆盖分散

## 决策
**不砍任何一套，而是构建"全集"**：对外暴露一套统一的 API 路由，内部实现从三套现有体系中各取所需，功能不重不漏。

具体做法：
- 前端只对接 `/api/` 前缀的路由（当前 unified 的扩展）
- shisi v1 的路由逐步将实现迁移到统一后端服务层，前端调用从旧路由切到新路由
- shisi v2 的良好设计（CharacterService + Repository 模式）作为统一内部实现的参考，不暴露 `/v2/` 前缀
- 当某功能在三套中都有实现时，以 unified 的实现为准

## 影响
- 正面：前端只需理解一套 API 格式
- 正面：新增功能只改一个入口
- 正面：保留 shisi v2 的好设计，不丢失
- 代价：迁移期间需要维护"双写"（新旧路由都可用，直到所有消费者切换完）
- 代价：需要完整编目现有 147 端点，确认"全集"不遗漏

## 废弃
Supersedes ADR-1.A（原"砍掉 shisi API"决策，已作废）

## 对应 Fitness Function
FF-0003：前端代码中禁止直接 import 旧 API 文件（`shisiClient` / `characterApi` / `memoryApi` 等）
