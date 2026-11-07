# 对抗验收报告 — 恢复默认人设根治批（2e9a2bb）

> 验收人：独立对抗验收（adversarial-acceptance skill，模式 A）
> 被验收对象：`2e9a2bb` feat(角色): 恢复默认人设根治——deactivate 端点 + 角色页固定入口 + default 语义收口
> 验收日期：2026-11-07
> **判定：通过**（保留意见见文末）

## 逐项检查结果

| # | 步骤 | 预期 | 实际 | 证据 |
|---|------|------|------|------|
| 1 | 病灶复现（HEAD~1 worktree 行为级，非 diff 审读） | 旧代码上四项病灶全部重现 | **全部确认**：反向端点 `POST /characters/deactivate` → **405**（落在 `DELETE /{character_id}` 模板上、无 POST 处理器，比预期 404 更强）；角色列表不含内置 `default`；显式 `default` 被卡片级 `is_active`（card01）顶替 | `repro_old.py` 在 `../ai-girlfriend-acc`（9d76529）实跑输出 |
| 2 | 新代码路由冲突探针 | `POST /characters/deactivate` 不被 `/{character_id}` 吞 | 200（作者 5 例 + 验收 4 例全绿） | 两测试文件 9 passed |
| 3 | 执行位矩阵·多条微信绑定 | 2 条绑定全复位 + upsert 全刷 + 扫码会话键 `{uid}:{wxid}` 热切 | 通过（作者用例只造 1 条，本格为验收新增） | `test_deactivate_resets_every_binding_of_user` |
| 4 | 执行位矩阵·零绑定用户 | 200，仅用户键热切，无 upsert | 通过（wxids=[] 分支作者未测） | `test_deactivate_without_bindings_ok` |
| 5 | 执行位矩阵·`deps.gf=None` | 200，DB 层复位不依赖 gf | 通过 | `test_deactivate_without_gf_ok` |
| 6 | 执行位矩阵·API Key 启用 | 匿名恢复 **401** 且全局 `is_active` 不被清；带 `X-API-Key` 机器面可恢复 | 通过（安全门成立：机器面不能匿名清全局标记） | `test_deactivate_machine_face_rejected_when_auth_enabled` |
| 7 | 突变 M1：个人激活行不删 | ≥1 测试变红 | **2 failed** | 突变脚本实跑 |
| 8 | 突变 M2：微信绑定不复位不刷 | ≥1 红 | **2 failed** | 同上 |
| 9 | 突变 M3：扫码会话键不热切 | ≥1 红 | **1 failed** | 同上 |
| 10 | 突变 M4：`_resolve_character_id` 显式 default 分支删除 | ≥1 红 | **1 failed** | 同上 |
| 11 | git 实物对宣称 | 提交 = 宣称的 10 文件 | `git show --stat 2e9a2bb` = 10 files, 562+/24-，一致 | git show |
| 12 | `default` 字面量审计 | deactivate 函数体零手写 `"default"`（真源 `BUILTIN_CHARACTER_ID`） | 0 命中 | grep |
| 13 | 文档四件口径 | 254/208 一致 | AGENTS×2 / HANDOFF×1 / CODE_GRAPH×4 / LOG×1，全一致 | grep -c |
| 14 | `deactivate` 全仓交叉引用 | 全部为本批新增，无悬空/无语义冲突 | 10 处命中全为本批文件（旧 `test_set_active_deactivates_others` 等为无关同名语义） | grep |
| 15 | E2E 消费面（修 A 破 B） | E2E 断言不依赖角色身份 | `smoke.spec.ts:79-81` 只断言 200 + reply 非空；登录面新行为（default→内置十四）不破坏断言 | 源码审读 |
| 16 | 运维脚本消费面 | 机器面契约不变 | `remote_check.py` 无认证头 → principal=None → `default` 仍走全局 `is_active` 旧契约，零行为变化 | 源码审读 |
| 17 | 后端全量（上轮未验证项） | 0 失败 | **4507 passed + 2 skipped = 4509 collected 精确吻合，0 失败**（1142+1007+1137+1221 四分块，find 递归 278 文件） | `/tmp/acc_repro/full_run.log` |
| 18 | 前端全量 + tsc | 全绿 | **356 passed（47 文件）** + tsc 0 | vitest/tsc 实跑 |
| 19 | 中文提交信息存储 | UTF-8 完好 | `od -c` 字节序列正常（`0xE6 0x8A 0x92`="角"） | git log + od |
| 20 | ruff | 0 错 | 全仓 All checks passed（含验收新增文件） | ruff check . |

## 发现清单

- **发现#1（验收工具自身缺陷，当场修）**：验收脚本 `_activate` 参数错位（session_factory 传到 chars_dir 位）+ async 测试内调 `asyncio.run` → 4 红。修复后 4/4 绿。**非产品缺陷**。
- **发现#2（测试覆盖缺口 → 发现即修）**：作者测试未覆盖执行位矩阵 5 格（多绑定 / 零绑定 / gf=None / API Key 401 / 扫码会话键）→ 补 `tests/test_character_deactivate_adversarial.py` 4 例，全绿，随验收提交。
- **发现#3（预先存在死引用，登记不修）**：`scripts/remote_check.py:108` 调 `/api/demo/chat/stream`，**后端全仓无此端点**（唯一命中是脚本自身）——运维 LLM 连通性检查是死路径。定性：真孤儿（非本批引入，HEAD~1 同样存在）；建议单独批处置（删步骤或改打 `/api/chat`）。
- **发现#4（口径澄清，非缺陷）**：病灶复现中旧端点返回 405 而非预估 404——路径模板被 DELETE 占位所致，病灶结论不变（无反向路径）。

## 未验证项（如实声明）

- **生产真机验证**：需部署后在真实控制台点「恢复默认」端到端确认（本地为 ASGI/单测级）。
- **前端组件未做突变验红**：vitest 断言已验「点击→调端点→invalidate」行为，但未对组件做突变（低风险：逻辑为纯编排）。
- **大创赛软著快照副本**（`大创赛报名.../user_data/`）：冻结存档，按既有惯例不同步不动。

## 保留意见

- 全量后端基线已由本轮补跑刷新：**4507 passed + 2 skipped / 0 失败 / 收集 4509**（含本批 +9 例），可替代「沿用 4500」口径写入后续文档。
- `remote_check.py` 死引用为预先存在问题，与本批无关，不阻塞判定。
