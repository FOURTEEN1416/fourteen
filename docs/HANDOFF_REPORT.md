# 唯一的你·十四 — 新窗口交接（2026-09-14）

> **上一版交接**（2026-08-28）已归档至 `docs/history/HANDOFF_REPORT-2026-08-28.md`（保留审计线索，未删）
> 本文件是**接手必读**，随手势刷新。**读它 → 再读 `docs/stages/SPRINT_2026-09.md` → 再动手。**

---

工作目录：`D:\Desktop\ai-girlfriend`（主仓）｜`C:\Users\FOUR\.agents`（Agent 层真源）
当前日期：2026-09-17 ｜ 分支：`main`（主仓）｜ HEAD：`4f6ed29`（= origin/main 已 push；服务器已 pull + remote_deploy 全流程部署含前端构建，health 200）
当前目标：**生产体验修复已闭环**（人设绑定/emoji/主动消息三连修 + web 控制端两开关 + 死代码清洗）＋云南复赛材料用户人工项（见 BOARD）

> ⚡ **2026-09-17 增量刷新**（详情见 `LOG.md` 五十/五十一 + `docs/verification/2026-09-17-三连修复验证报告.md`）：
> ① **web 切角色→微信实时生效**（activate 同步当前用户全部 wechat_bindings + upsert_binding 实时缓存；修复 web 激活与微信人设真源两条线断裂）；② **emoji 收口**（默认每条最多 1 个仅情绪强烈，五处提示词语义化）；③ **主动消息打通**（asyncio.run 直投修非主线程投递 bug + 紧迫度回退 ASE 自身 last_chat + MultiProviderGateway 补 chat_sync；生产实证 64 触发 0 送达→1/1 "微信主动发送成功"）；④ **知识索引失效重建**（update/activate 触发，防陈旧检索）；⑤ **web 两开关**（免打扰时段 + 知识库定期采集，`data/scheduler_config.json` 跨 worker 真源）；⑥ **服务器 25 张角色卡**（24 张规范化同步 + 绑定卡 62105bca）；⑦ **死代码清洗**（Badge/chatStore/api-chat/微信指令系统，见 DELETION_LOG 09-17 条）。
> ⚠️ 测试口径（09-17 系统 Python 复测）：**1014 收集/1010 通过/4 跳过** + vitest **87/87** + tsc 0 错（文档口径 1117 的历史说明见 W4 报告 §3）。

---

## 必须遵守（违反即事故）

1. **宪法 `AGENTS.md` v1.5 是最高行为准则**；§1.2 **就是 sliver-vibe-coding 执行法则**（本技能是项目既定执行法，非外来）。
2. **§3 参赛文档三不入**：`大创赛报名以及后期发展/` 下材料**不入 git / 不入 GitHub / 不上云服务器**；仅本地 md→docx→PDF 闭环。（此为用户 2026-09-06 裁决，原话「这种文档不应该推送到云服务」）
3. **§1.3 商讨协议五步制**：功能修改必须「定位→复述→排歧→**确认**→举证」；**用户说"确认"前不许动代码**。
4. **§1.3 搜索分域 + §6 防漂移**：代码/技术类任务 **GitHub-First，调用 `WebSearch` 即零容忍违规**；信息采集类可用通用搜索。
5. **§3 三端统一（A/B 档）**：源码改动 = commit→push→服务器 pull→部署→health+blob 核验；纯文档 = 仅 commit→push。
6. **§4.4 禁止 `git add .`**，必须按白名单精确 `git add`。
7. **§1.3 真值裁决**：① 代码实况 > ② 现行文档 > ③ 历史文档。
8. **§8 多窗口 worktree 协议**：新窗口一律 `pwsh scripts/new_window_worktree.ps1 -Name <窗口名>`；跨窗信息写 `docs/board/BOARD.md`。
9. **§1.3 反对 subagent**（用户裁决）；必要例外仅限上下文 >80% 的隔离开销只读检索。

---

## Git State

**主仓 `D:\Desktop\ai-girlfriend`**
```text
分支: main  |  HEAD: 91f2042（= origin/main，已 push；服务器已 pull+部署）
最近 3 提交:
  91f2042 merge: 收编 w3-code —— 多模态图片通道 + silk 语音链路 + 入口守卫放行
  d74a8e6 fix(wechat): 入口守卫放行 type 1/3/34 —— W4 三红用例转绿（13/13，全量 989/0 fail）
  79dbae3 fix(voice): 补 silk 编解码（pilk）—— 打通微信语音入站/出站

未提交 / 未跟踪（2026-09-15 复核）: `git status` 干净（.zcode/ 与 frontend/audit-tabs.mjs 已入 gitignore，31262d8）
（2026-09-14 旧快照的未跟踪清单已全部处置完毕）
```
**内部文档仓**：`D:\Desktop\知识库搭建` 下有独立 `.git`（该仓本窗口未改动）。

**Agent 层仓 `C:\Users\FOUR\.agents`**
```text
分支: master  |  HEAD: 3960836
最近 3 提交:
  3960836 chore: 接入30个modex-3-skills(软著/专利/论文) + 清理记录
  a429ce2 feat(toolchain): 接入4个MCP(github/firecrawl/crawl4ai/playwright) + TOOLCHAIN.md 五链文档
  f3ec224 chore: 清除 D:\npm（npm 11.3.0 散落副本，六条证据确认无用）

未提交: m skills/stop-slop（submodule 指针变动，非本窗口所为）
```

---

## Current Truth

- **产品边界**：「唯一的你·十四」— 微信扫码即用的 LLM 智能情感陪伴系统；核心能力见 `README.md`；技术栈 Python 3.12 / React 19 / Vite 8 / TS 6（`docs/`+`.venv` 均为 3.12.4）
- **当前阶段**：`docs/stages/SPRINT_2026-09.md` —— 三线并行（**材料** / **代码** / **证据**），状态 `plan` 待用户确认
- **主要 owner**：见宪法 §2 Owner Map（**24/24 模块本窗口实测全部真实存在**）
- **当前真源文档**：`AGENTS.md`（宪法）· `CODE_GRAPH.md`（代码实况 v3.5.0，最后核实 2026-09-01）· `LOG.md`（L2 日志）· `docs/README.md`（文档体系唯一入口）· `docs/FUNCTION_INVENTORY.md`（功能清单，商讨协议定位基准）· `docs/DECISION_LEDGER.md`（决策生死账）· `docs/stages/SPRINT_2026-09.md`（阶段真源）· `docs/board/BOARD.md`（跨窗看板）· `docs/board/TASK_PACKAGES.md`（**⚠️ 已撤回，标「⛔ 暂缓未生效」**）
- **用户确认过的非目标**：不改技术栈/框架/目录架构/部署形态；不改 DB schema/权限/支付；不把参赛文档入库；不写未实测指标；不重建 `CODE_GRAPH.md`
- **被拒绝/作废的路线**：
  - `10-调研简报…md` 的 **§3「微信接图接语音三条官方路线」已作废**（JSSDK/MediaId/小程序 —— 本项目**不在微信官方体系内**，已加 ⛔ 横幅，更正见 `11-`）
  - `TASK_PACKAGES.md` 的窗口任务分发**已撤回**（用户：「不要着急着分发任务，先将要弄什么东西确定了」）
  - `docs/FEATURE_MAP.md` **已由用户 2026-08-28 裁决删除**（理由"严重错误"，由 `FUNCTION_INVENTORY.md` 替代）→ **宪法 §1.3 仍引用它，属宪法漂移，待修**

---

## 本窗口完成（按 owner 层分组）

### Agent 层（`~/.agents`，独立仓）
- **建四域真源**：`skills/`（243 项）· `memory/profile/PROFILE.md`（取代 5 份画像副本）· `memory/rules/RULES.md`（四平台铁律合一，v1.1 增 §13 临时产物纪律）· `settings/identity/SOUL.md` · `projects/index.json`
- **233 技能收编**：`.zcode/skills` 217 项 → 真源（**失败 0**）；**78 项目录名规范化**；`.zcode/skills` 现为空（ZCode/opencode 走 `native` 直读 `~/.agents/skills`，源码实证）
- **30 个 modex-3-skills 接入**（软著/专利/论文链，junction 零拷贝）
- **治理工具**：`tools/agentctl.py`（scan/plan/sync/verify/new-platform/vault）、`p2_adopt.py`、`p3_normalize.py`、`janitor.py`、`fix_skill_misclassify.py`、`p0_bootstrap.py`、`p5_convergence.py`
- **4 个 MCP 移植**：github / firecrawl / crawl4ai / playwright → `~/.workbuddy/mcp.json`（**详见 `~/.agents/HANDOFF.md`**）
- **环境清理 263MB**：项目缓存 189MB + `大创赛/tmp_*` 74MB → 全部回收站

### 项目层（`ai-girlfriend`）
- **唯一一处代码改动**：`cache/llm_cache.py` 装饰器 `[return-value]` 类型错（全仓 mypy 唯一错误）→ `typing.cast` 修复（纯类型，运行时零变化）
- **跨云闭环验证**：commit → push → 服务器 reset --hard → 服务重启 → `/api/health` 200 → **git blob 三端一致**
- **文档**：新建 `docs/stages/SPRINT_2026-09.md`、`docs/board/{BOARD,TASK_PACKAGES}.md`；`docs/README.md` 登记；`LOG.md` 追加 4 条（三十九~四十二）；旧交接报告归档 `docs/history/`

### 参赛层（`大创赛报名以及后期发展/`，**本地三不入**）
- `08-云南赛区复赛准备方案.md`（已修正来源错误：区分省级/兄弟院校/本校三层）
- `09-复赛冲刺阶段计划.md`、`10-调研简报…md`（§3 已作废）、`11-更正-多模态通路真实拓展路径.md`、`12-现状核查与需求台账.md`
- **软著工作区**：`D:\Desktop\软著申请-唯一的你十四\`（`user_data/` 已导入**真实源码 504 文件 / 43,412 行**，走技能模式 A）

---

## 变更文件（未提交部分）

```text
主仓:  M LOG.md
       M docs/README.md
      ?? docs/board/（BOARD.md + TASK_PACKAGES.md）
      ?? docs/stages/（SPRINT_2026-09.md）
Agent: m skills/stop-slop（submodule 指针，非本窗口改动）
已归档: docs/history/HANDOFF_REPORT-2026-08-28.md（从 docs/ 移入）
```

---

## 验证证据

**已通过（命令 + 结果）**
```bash
# 代码质量三连（解释器 D:\Desktop\ai-girlfriend\.venv\Scripts\python.exe, 3.12.4）
ruff check .                # → All checks passed!
mypy .  --ignore-missing-imports   # → Found 1 error in 1 file (checked 346 source files)；修后单文件 Success: no issues found
pytest --collect-only -q    # → 1048 tests collected in 15.78s
pytest -k cache -q          # → 31 passed, 1 skipped, 1016 deselected
（注：pytest 子进程须 PYTHONPATH= 清空，否则触发 safe-delete 护栏）

# A 档三端核验
git hash-object cache/llm_cache.py                    # 本地 7fded71e30677a3667b6d7b298cbd376adcf34d5
ssh swu-prod 'cd /opt/ai-girlfriend && git hash-object cache/llm_cache.py'  # 服务器 同 7fded71e…（一致）
ssh swu-prod 'curl -s http://127.0.0.1:8000/api/health'  # → {"status":"ok",...,"version":"3.1.0"}
ssh swu-prod 'systemctl is-active ai-girlfriend'          # → active
```

**未运行 / 未验证**
- ❌ **`myapp` 未跑全量 pytest**（只跑了 `--collect-only` + `-k cache`）→ 全量 1048 用例的通过率**未验证**（宪法声明基线 1042 全过，但本窗口未实跑全量）
- ❌ **前端 `npm test` / `npm run build` 未跑**
- ❌ **微信图片/语音端到端收包未实测**（见「已知风险」）
- ❌ **`docs/board/`、`docs/stages/`、`LOG.md` 的 B 档 commit→push 未做**
- ❌ **4 个 MCP 未激活**（需用户在连接器页点「信任」）

---

## 运行状态

**云服务器 `swu-prod`（139.199.199.174，端口 28222，User=root）**
```text
应用目录: /opt/ai-girlfriend    服务: ai-girlfriend = active（2026-09-14 22:06:24 CST 重启）
HEAD: f158e82c
监听: 127.0.0.1:8000（uvicorn --workers 4，实见 5 个 python 进程）· 0.0.0.0:80（nginx）
健康: /api/health → 200 {"status":"ok","service":"unique-you-api","version":"3.1.0"}
微信: /root/.weixin_cow_credentials.json 存在(202B, 07-27)；/tmp/ai-girlfriend-wechat-autostart.lock 存在(07-28)
      data/wechat_state.json → connected:true, bot_id=21c98b9202ae@im.bot（**文件时间 09-09，状态可能陈旧**）
      data/proactive_state.json → daily_count:8, last_sent_time=2026-09-14T04:10:55Z, **last_chat_time=null**
⚠️ 模板与线上不符：仓库 deploy/ai-girlfriend.service 写 User=www-data + ProtectHome=true，
   线上实测 User=root + ProtectHome=no（服务器 unit 被改过，模板已脱节）
```
**本机**：项目 `.venv`（Python 3.12.4）；无本项目常驻服务运行。

---

## 已知风险 / 阻塞证据 / 未决用户决策

| 级 | 项 | 现状 |
|----|----|------|
| 🔴 | **微信图片/语音是否真能到云端 —— 未实测** | 代码链路成立（`_handle_message` 已解析 type 3/34）；云端凭据+锁都在；**但当前日志（9/13–9/14）`wechat_direct` 记录 0 条**，而 `wechat_state.json` 说 connected:true → **两证据矛盾**。**60 秒验证法**：给 bot 发语音+图 → `ssh swu-prod 'tail -f /var/log/ai-girlfriend.log \| grep -E "wx\|wechat"'` 看有无 `[wx][step=receive]`。**此结果决定下一步** |
| 🔴 | **`MIMO_API_KEY` 本机与云端均缺** | `config/system.yaml` 的 `engine: mimo-tts` 是**唯一 TTS 引擎**（08-28 MiMo-only 收敛）却引用它 → **语音输出可用性未验证**。用户去 `platform.xiaomimimo.com` 取 key（TTS 限免）→ 填本机 + 云端两处 `.env` |
| 🟠 | **`asr.enabled: false` + `api_base: ""` + 无 `ASR_API_KEY`** | ASR 已实现已接线，**只是没开**（零代码，纯配置）。用户已确认 **MiMo 有 ASR**（`Xiaomi MiMo-V2.5-ASR`，GitHub `XiaomiMiMo/MiMo-V2.5-ASR` 开源）→ 可直接用 MiMo |
| 🟠 | **`image_data` 零消费者** | 图片收到即丢；`VisionHandler` 已实现却**未接线** → 缺口 = **1 处接线 + 补测试** |
| 🟠 | **无「表情识别」与「语音声学情绪」实现** | 命题任务 1 两条通道确为空缺（`VisionHandler` 只描述图片；ASR 只取 text，声学信息被丢） |
| 🟠 | **图/语音收包 0 测试覆盖** | `tests/test_wechat_connector.py::TestHandleMessage` 仅 2 用例且**只测 `type:1` 文本** |
| 🟡 | **宪法漂移** | `AGENTS.md` §1.3 仍引用**已被用户删除**的 `docs/FEATURE_MAP.md` → 应改指 `FUNCTION_INVENTORY.md`（**改宪法需用户确认**） |
| 🟡 | **未跟踪 2 项** | `.zcode/`、`frontend/audit-tabs.mjs`(2026-09-03) —— 多窗口开工前应处置 |
| 🟡 | **赛事阻断（用户已答复自解）** | B1《赛事指南》口径 / B2 省赛系统网址 / B3 指导教师实名 —— 用户 2026-09-14 明确「不算阻塞，我能解决」 |
| ⚪ | 24 个 `.old` | 全在 `.browser_profile/Default/**/LOG.old`，**浏览器日志轮转残留**，非代码债（我先前误判已纠正） |

---

## 漂移警告（下一手不要做）

- ❌ **不要**为"多模态"去研究微信公众号/小程序/H5 接入方式 —— 本项目走**第三方协议网关 `https://ilinkai.weixin.qq.com` + HTTP 长轮询**，不在微信官方体系内。
- ❌ **不要**重建 `CODE_GRAPH.md`（宪法：代码实况文档**永不从零重建**，只增量刷新漂移段）。
- ❌ **不要**用 `md5sum` 比对本机与服务器文件（**行尾 CRLF/LF 差异**）→ **用 `git hash-object` 比 blob**。
- ❌ **不要**用 `cmd | tail` 判成败 —— **管道会吃掉退出码**，须取 `PIPESTATUS[0]`。
- ❌ **不要**在无用户"确认"时改功能代码；不要 `git add .`；不要把参赛文档入库。
- ❌ **不要**把 `TASK_PACKAGES.md` 当作生效的任务分发（**已撤回**）。
- ⚠️ push 需 `-c http.proxy= -c https.proxy=`（本机 git 代理 `127.0.0.1:3128` **已失效**）；凭据用环境变量 `GITHUB_PERSONAL_ACCESS_TOKEN`（93 字符）**走 URL，不落盘**。
- ⚠️ pytest 前须 `PYTHONPATH=` 清空（否则 WorkBuddy shell 的 safe-delete 护栏会打断 tmp 轮转）。

---

## 下一步（最安全顺序）

**A. 零代码即可做的两件（价值最高）**
1. **打开 ASR**：填 `ASR_API_KEY` + `api_base`（可用 MiMo），`config/system.yaml` 置 `asr.enabled: true` → 语音从"占位"变"可用"
2. **接线图片通道**：`wechat_direct/wechat_connector.py` 的 `_handle_message` 把 `image_data` 送 `VisionHandler.process()`，描述并入 `text`；**补 `type:3/34` 的收包测试**（当前 0 覆盖）

**B. 必做的验证（决定后续路径）**
3. **60 秒微信收发实测**（见上表 🔴 第一行）—— 链路活 = 只改接线；链路死 = 先修连接

**C. 用户已下令的产出线**
4. **软著**：工作区已就位（`D:\Desktop\软著申请-唯一的你十四\`）→ 写 `CLAUDE.md` → 产出 8 类草稿 + 5 个门禁 JSON → `copyright-build/scripts/build_docx_from_md.py` 生成正式 Word/TXT → **用户去中国版权保护中心提交**（主体=个人）
5. **论文**：目标刊已定 **《心理学进展》**（汉斯，开放获取；同题先例 袁小雅/刘仪辉 2025）→ 用 `paper-write-zh(-docx)` + `arxiv` + `auto-paper-improvement-loop`
6. **PPT + 商业计划书**（可并行）
7. **实操视频**（最后）

**D. 收尾**
8. B 档提交：`git add LOG.md docs/README.md docs/board docs/stages` → commit → push（**不加** `.zcode/`、`frontend/audit-tabs.mjs`）
9. 全量 `pytest` + 前端 `npm test`/`build`（本窗口未跑，属**未验证**）

---

## 关联交接

**Agent 层（MCP / skills / 记忆与设定）的完整交接见**：`C:\Users\FOUR\.agents\HANDOFF.md`
—— 4 个 MCP 的配置与激活步骤、243 项技能的三库同步机制、memory/settings 四域现状与**待补页清单**，全部在那里。
