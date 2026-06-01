# API 全集缺口关闭报告（2026-05-27）

## 背景
统一 API（`/api/characters/*`）相对 shisi v1/v2 的功能缺口分析与关闭。

## 缺口清单

| # | 功能 | 原 shisi 端点 | 统一端点 | 状态 |
|---|------|---------------|----------|------|
| 1 | 角色 CRUD | `/api/shisi/characters` | `/api/characters` | ✅ 已有 |
| 2 | 人设 | `/api/shisi/persona` | `/api/characters/{id}/persona` | ✅ 已有 |
| 3 | 角色卡 | `/api/shisi/characters/card` | `/api/characters/{id}/persona-card*` | ✅ 已有 |
| 4 | **收藏/转发** | `/api/shisi/memory/favorite*` | `/api/characters/{id}/favorites*` | ✅ **已发现已在 memory_routes.py** |
| 5 | 剧情线 | `/api/shisi/storyline` | `/api/characters/{id}/storyline*` | ✅ 已有 |
| 6 | 音色绑定 | `/api/shisi/voice` | `/api/characters/{id}/voice*` | ✅ 已有 |
| 7 | **导入** | `/api/shisi/characters/import` | `/api/characters/import` | ✅ **本次新增** |
| 8 | **导出** | `/api/shisi/characters/export/{id}` | `/api/characters/{id}/export` | ✅ **本次新增** |

## 关键发现
- **收藏/转发端点原来已经就位**：不在 `character_routes.py`，而在 `memory_routes.py` 中（`/api/characters/{id}/favorites*`）。前端调用正常，不存在 404。
- **前端实际只留了 2 个 shisi 桥接调用**（导入/导出），其余全部已切回统一端点。
- **贴纸/亲密度/生理指标/情感阶段**：这些是 shisi 后台服务（AffinityEngine / StickerManager / VitalEngine / StageEngine），前端没有直接调它们的 REST 端点。它们走的是 shisi 内部服务调用，不在统一 API 覆盖范围内，暂不迁移。

## 本次改动

### 后端：`api/routers/character_routes.py`
- 新增 `POST /api/characters/import` — JSON 文件上传导入角色
- 新增 `GET /api/characters/{id}/export` — JSON 文件下载导出角色

### 前端：`frontend/src/api/characters.ts`
- `exportCharacter()`：`POST /api/shisi/characters/export/{id}` → `GET /api/characters/{id}/export`
- `importCharacter()`：`POST /api/shisi/characters/import` → `POST /api/characters/import`

## 验证
- ✅ 524 passed, 1 skipped (pytest)
- ✅ TypeScript 编译无错误
- ✅ 新增端点语法通过 AST 检查

## 后续
- shisi 旧导入/导出端点**保留不删**（遵守"不砍不删"规则）
- 其他 shisi 后台服务（贴纸/亲密度/生理指标/情感阶段）暂不统一 — 它们走内部调用而非 REST
- 未来如果需要统一，可在 `api/services/` 中加 service 层适配
