# 任务包 W12 · 角色模板面（B+C）— 平台无主卡的只读暴露与显式克隆

> **出具**：主控（歆歆）· 2026-09-27
> **上游真源**：`AGENTS.md`（宪法）｜`docs/board/BOARD.md`｜`docs/board/TASK_PACKAGES.md`
> **触发**：用户 2026-09-27 裁决「**采纳 B+C**」（回应主控对 W1 提交 `94ed63d` 回报的独立复验结论）
> **性质**：功能新增（新增端点 + 新增测试）。**功能代码改动须先过商讨协议五步制**（宪法 §1.3）。

---

## 0. 事实基线（主控现场核验，勿沿用历史口径）

| # | 事实 | 证据（现场直读） |
|---|------|-----------------|
| 1 | 41 张卡位于 `config/characters/*.json`，**全部 `user_id=default`** | `ls config/characters/*.json` = 41；`user_id` 分布 = `{default: 41}` |
| 2 | 内容是**历史遗留真实角色**（凌白/孙颖莎/李信/米彩/椎名真昼…），**非策展模板**；`is_active=True` 仅 **1** 张 | 现场逐卡读取 |
| 3 | `config/characters/` 被 `.gitignore` 排除 → 卡文件**不入库**，只随检出/服务器存在 | AGENTS §4.3、CODE_GRAPH §1.1 |
| 4 | `data/presets/` **不存在**（0 个预设）→ `GET /api/presets` 是**空壳**，**不可复用**为模板面 | `ls data/presets/` → No such file |
| 5 | 无主判定真源：`_UNOWNED_MARKERS = {"", "default", "system"}` + `card_owner_key()` | `api/routers/character_routes.py:54,59` |
| 6 | 归属校验真源：`card_access_allowed()` / `require_character_access()` | `api/routers/character_routes.py:62,73` |
| 7 | 生产库 `data/users.db` **token_version 列与 user_active_characters 表已存在**（迁移已发生） | 现场 PRAGMA 直读；副本演练复发验证通过 |

> **对「模板」定性的一处修正**：第 2 条表明这 41 张卡更接近「历史遗留存量卡」而非「精心策展的公共模板」。
> 因此 **B 方案的模板面必须带策展开关**（§2 第 2 条红线），不得全量无条件暴露。

---

## 1. 目标（B + C 的准确定义）

- **B · 只读模板面**：把**平台无主卡**以只读方式暴露给**已登录用户**；用户点「使用」时由**服务端克隆**为调用者私有的独立副本。
- **C · 注册分发**：新用户注册时按**策展清单**（≠ 全量 41 张）克隆 N 张，解决冷启动「列表为空」。

### 1.1 必须钉死的边界（违反即事故）

1. **克隆 ≠ 认领（最高优先）**：克隆产出**新 id + 新 `user_id`** 的独立副本，**模板文件字节不变**。
   这与被 W1 修掉的旧 bug（把同一张卡的全局 `is_active` 当个人选择 → 多人共享同一张卡互相踩）是**语义相反**的两件事。评审时须逐字确认此点。
2. **模板面 ≠ 全量暴露 41 张**：内容为历史遗留（含「猪头」等随意命名），须有策展白/黑名单，默认**不**全量暴露。
3. **不改机器面契约**：新端点要求 Bearer 登录主体（无主体 → 401）。既有机器面（无 Bearer 的管理面 `/api/stats`、数据面、E2E/部署脚本）行为**零变更**。
4. **零撞车**：**不修改**下列任一在制/已声明文件 ——
   - `api/routers/character_routes.py`（W9 已声明将改其 delete 归属粒度清理）
   - `api/routers/auth_routes.py` / `admin_routes.py` / `api/database.py`（W9 叠加区）
   - `frontend/src/**/{client.ts,queryClient.ts,useAuth.ts,pages/*}`（W11 在制）
   - W9 独占清单 20 项（见 BOARD.md 2026-09-27 W9 条目）

---

## 2. 阶段划分

### 阶段 1 · 可立即开工（零冲突 · 纯后端）

新增文件（全部全新，与在制**零交集**）：

| 文件 | 动作 | 内容 |
|------|------|------|
| `api/routers/character_template_routes.py` | **新增** | 2 个端点（见下） |
| `config/character_templates.yaml` | **新增** | 策展清单 |
| `tests/test_w12_character_templates.py` | **新增** | 红先（TDD） |
| `api/app_factory.py` | **改 1 处约 6 行** | 照既有 `try/except + _mount_required_router` 模式挂载（参照 `:362-367` 角色路由块） |

**端点契约**

```
GET  /api/character-templates
  鉴权：Security(get_optional_principal)；无主体 → 401（新端点自持契约，不扩大机器面）
  语义：仅列「无主（card_owner_key == ""）且 通过策展白名单」的卡；返回摘要，不含 persona 正文
  返回：{"templates":[{"id","name","description","tags":[...]}], "total": N}

POST /api/character-templates/{template_id}/clone     （201）
  鉴权：同上；无主体 → 401
  语义：模板不存在 / 非无主 / 未过策展 → 404（统一 404，防状态码枚举）
        克隆 = 读模板 → 新 uuid → user_id = str(principal.user_id) → is_active = False → _save_character
  幂等：不做幂等约束（用户主动行为，允许多份）
  返回：{"id": <新 id>, "name": <name>, "status": "created"}
```

**复用而非复制**（守「不重复 owner / 无投机抽象」）—— 以 `import` 方式复用，勿抄一份：
`character_routes` 的 `_load_character` / `_save_character` / `_build_character_data` / `_list_all_characters` / `_UNOWNED_MARKERS` / `card_owner_key`。

> ⚠️ **路由前缀陷阱（必读）**：前缀**必须**独立为 `/api/character-templates`。
> 若写成 `/api/characters/templates`，会被**先前注册**的 `/api/characters/{character_id}` 抢先匹配（FastAPI 按注册顺序），
> 把字面量 `templates` 当成 `character_id` → 静默语义错误。

**策展清单格式**（`config/character_templates.yaml`）

```yaml
enabled: true          # 总开关；false 时 GET 返回空表、clone 一律 404
visible_ids: []        # 显式白名单；为空 = 开发期放行全部无主卡；生产建议填实
hidden_ids: []         # 显式排除（如历史测试遗留「猪头」）
seed_on_register: []   # 阶段 2 使用：注册时分发的模板 id 列表
```

### 阶段 2 · 挂起（等 W9 / W11 收编后再排）

- **注册分发钩子**：`auth_routes.register`（`:149`）→ 读 `seed_on_register` 克隆。**须等 W9 定版**（该文件在 W9 叠加区）。
- **前端接入**：模板选择组件（新建文件，避开 `pages/*`）+ `useQueries.ts` 增 `templates` 查询键（**公共数据，不带账号维度**——与 W1 的账号维度私有键区分）。
- **文档同步**：README 端点徽章 / `CODE_GRAPH.md` / `AGENTS.md` 的**端点计数**——新增 2 个端点，须同步（历史口径「端点 215/181」类计数会变）。

---

## 3. 红线

- ❌ 禁 `git add .` / `git commit -a`；一律显式路径提交（沿用 `multi-window-safe-commit`）
- ❌ **不得改动 `config/characters/` 下任何卡文件的一个字节**（提交后须以 `sha256sum` 复验不变）
- ❌ 不引入兜底层 / 兼容 shim / 重复 owner / 投机抽象
- ❌ 不做采样验证，须完整跑本包测试 + 邻域回归
- ✅ 门禁：`ruff` 0 错；`pytest` 基线不回归；前端 `tsc` 0 错 + `vitest` 不回归
- ✅ 提交信息用 Conventional Commits 中文，并在 message 中申报跨窗归属

---

## 4. 验收标准（可执行断言）

1. 无 Bearer → `GET /api/character-templates` 返回 **401**
2. 用户 A 登录 → 列表**含**`visible_ids` 内模板、**不含** `hidden_ids` 内模板
3. A 克隆某模板 → 201 且返回新 id；随后 A 的 `GET /api/characters` 含该新卡且 `user_id == A`；**B 登录看不到它**
4. 克隆前后对同一模板文件 `sha256sum` **完全一致**（模板零改动实证）
5. 模板卡本身 A 仍无法 `PUT` / `DELETE`（`require_character_access` 仍 404）→ 克隆是唯一获得途径
6. 机器面回归：无 Bearer 的既有端点（`/api/stats` 等）行为**与改造前逐字节一致**
7. `pytest tests/ -q` 不回归 + `ruff check` 0 错

---

## 5. 交付格式

沿用 `multi-window-safe-commit` 回报模板：
根因 → 改动文件（分本窗/他窗）→ 红转绿证据 → 真机验收（多进程/重启）→ 反证 → 未验证风险 → 迁移与回滚 → **提交 hash（明确未 push 未部署）** → 跨窗事故与待裁决项

---

## 6. 建窗

```powershell
pwsh scripts/new_window_worktree.ps1 -Name w12
# 分支 wt/w12，目录 ..\ai-girlfriend-w12
# 卸窗：pwsh scripts/new_window_worktree.ps1 -Name w12 -Remove
```

或由主检出直接开工（阶段 1 与在制零交集，两处均安全）。
