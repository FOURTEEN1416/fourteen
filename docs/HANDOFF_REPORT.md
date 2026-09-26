# 唯一的你·十四 — 新窗口交接（2026-09-26 本地归属重构）

## 当前实施快照（优先于下方历史批注）

- 范围：全链“谁说了什么”修复，用户授权激进重构。本地基点 `f9e27a3`；**已于 2026-09-26 22:04 上线生产**，三端一致到 `f85408f`（含上线后跨环境依赖补漏），线上已获得本批修复。
- 已落实：统一来源窗口/身份过滤/整轮事务；出站固定角色+取消版本；HTTP、WS、微信的传输确认后记账；请求级模型ContextVar及后台owner凭证；画像保序更正与逐字段来源水位、隐含指代上下文；抽取来源id水位+租约；事实严格等价去重及删除派生失效；日记昨日×会话×角色与调度接线；id历史分页、活跃锁保护。
- 现行owner：身份 `utils/character_resolver`，会话键 `utils/session_key`，请求模型 `utils/llm_bridge`+`llm_provider.select_request_llm`，后台模型 `api/byok.session_llm`，真实对话 `StructuredMemory.add_chat_turn`，日记 `_legacy_diary_summarizer`（重复模块已删）。
- SQLite新增 `memory_extraction_progress`、`fact_deletion_watermarks`；**生产已串行迁移**：冷停服务 → 整库备份 → 真实副本重复初始化演练 → 生产执行，六张业务表行数前后一致（chat_history 1148 / user_facts 32 / reflections 30 / reminders 0 / user_profile 2 / recycle_bin 0）；未改动生产既有数据。
- 最终本地回归：127个测试文件、41张角色卡、累计本会话工作树，递归四块 **502 + (450通过/1跳过) + 570 + 497 = 2020收集 / 2019通过 / 1跳过 / 0失败**，与全量collect-only吻合；运行前后源码哈希变化0。前端98/98、TypeScript通过；ruff全仓通过；六个核心文件mypy（含无注解函数体）通过；ci_gates 4/4（历史ADR文件名告警非阻断）。最终日志在outputs/stable-acceptance-*，不把重叠专项累加。角色卡gitignore，不能靠git恢复。
- 上线验收（2026-09-26）：三端 HEAD `f85408f`；本地与服务器 **81 个改动源码 blob 逐一 `git hash-object` 一致**；生产 health/ready 全绿、4 worker + supervisor 共 5 进程、`NRestarts=0`；新日志窗口 0 Traceback / 0 `database is locked` / 0 `no such column|table`；nginx 四项配置 sha256 前后一致（入口 `139.199.199.174:80` 未动）；备份 `/opt/ai-girlfriend/backups/release-20260926-220406-010259e`（约 124 MiB，含 3 库 `.backup` 与 data/config 冷包，sha256 已记录）；服务器独立源码沙箱 118 条并发/发送契约测试全通过。GitHub CI（run 36247485708）全绿。
- 仍须保留边界：发送 API/传输栈受理不是用户已读；硬崩溃时网络发送与本地记账不具备分布式原子性；同主机跨 worker 回合锁已接 OS 锁并通过独立子进程阻塞/释放验证，生产四 worker 的真实并发仍待自然流量观察；画像写入按来源消息 id 逐字段防迟到覆盖已上线但未经历真实更正序列；模型对引用、否定、相对日期的理解不能以 mock 测试证明百分之百。
- 本批还暴露并修复了一处**跨环境依赖声明遗漏**：`sqlalchemy` 未带 `asyncio` extra → 干净环境（CI）缺 `greenlet`，`sqlalchemy.ext.asyncio` 导入即失败（backend/前端 E2E/子路由三个 job 同根因）。本地与生产恰好预装该包故长期不显；已改为 `sqlalchemy[asyncio]>=2.0.0`（`f85408f`）。**教训：本地绿的依赖不等于声明完整。**
- 对照源码、机制变更详见 `LOG.md` 2026-09-26；删除记录见 `docs/DELETION_LOG.md`。继续工作先看diff，禁止回滚累计成果或把outputs整目录纳入提交。


> 上一窗口交接已归档：`docs/history/HANDOFF_REPORT-2026-09-14.md`
> 本文是**当前状态的单一真源**；日常流水见 `LOG.md`，决策见 `docs/DECISION_LEDGER.md` 与 `docs/adr/`。

> ### 📌 接手批注（2026-09-20 角色卡扩充批次）
> **凡本文出现「25 张卡 / 25 张现役卡 / 2×卡数=50+用例」处已失效**，以本批注为准：
> `config/characters` 现役 **41 张**（25 既有 + 16 文学导入：我的26岁女房客×4 / 从你的全世界路过×5 / 云边有个小卖部×3 / 某某×2 / 天堂旅行团×2）；既有 25 卡已全量补 personality/speaking_style 数值字典与 mes_example（伊蕾娜损坏字段重写）；知识索引由 `scripts/rebuild_knowledge_index.py`（已补透传 core_anchors/source_data）从权威真源重建 41 份；`CharacterKnowledgeService.search()` 双路合并改交错式。测试口径 **1251 收集 / 1247 通过 / 4 跳过 + vitest 98/98**（详见 AGENTS §4.3 四次刷新注记与 CODE_GRAPH v3.8.4 §13 行）。

> ### 📌 接手批注（2026-09-19 15:50，提交 `6780c5f`）
> 已按 §7 推进 **第 ② 项**（修 `character_card` + `search`），两项均修复、部署、生产验证。
> **§4 表格与 §5 前两项已失效，以本批注与 `LOG.md` 七十二为准。** 要点：
> - `search`：真缺陷是 **title 取到 Bing 面包屑**（非报告所写「DDG 抛异常」——
>   产线日志中该错误 0 次）。主后端已定为 Bing 直抓（实测 5/5 / 0.6s）；
>   **`ddgs` 9.x 实测 0/5（大陆全后端不可达、单次≈100s），已卸载，勿装回**。
> - `character_card`：根因是**体积校验冒充内容校验**——百度反爬壳页 HTML 95KB 但正文仅 4 字符，
>   旧代码判成功并**短路降级链**。已加内容有效性判定 + 结构归一 + 维基不可达记忆
>   （整链 33.2s → 稳态 0.7s）。
> - **测试基线更新**：收集 **1189** / **1185 passed / 4 skipped**（旧记 1105 / 1101 **已过时**）。
>   差异根因已查明：`test_persona_injection.py` 按 `config/characters/*.json` 参数化
>   （**用例数 = 2 × 卡数 + 7**），而该目录**被 gitignore**（磁盘 25 张 / git 追踪 0）
>   → 基线依赖未追踪数据。**引用基线必须同时声明卡数。**
> - **§7-①「攒埋点数据」仍待用户参与**：`[prompt]` 埋点已部署但日志 0 条 —— 部署后无真实对话。

## 0. 这个窗口做了什么（一句话）

修掉「主动消息用户收不到」的**六层谎报投递**，接着修了一批对话体验与性能问题，
并把「系统提示词分层与按需注入」立成 **ADR-0015**（含行业调研）。

## 1. 必须遵守（违反即事故）

1. **网评冻结**：线上 nginx 是 `listen 80; server_name 139.199.199.174`，**不得改动域名/端口**
   —— 评审专家靠这个裸 IP 访问。
   ⚠️ **仓库模板 `deploy/nginx-ai-girlfriend.conf` 已被另一个会话改成 `${DOMAIN}`
   占位符，与线上已不一致 —— 网评结束前不要用模板重新生成 nginx 配置。**
   （已核验：线上 `/etc/nginx/conf.d/ai-girlfriend.conf` 未被动，站点正常。）
2. **多会话并行**：主检出常有其他会话在写。**禁止 `git add -A` / `git commit -a`**；
   `AGENTS.md` / `README.md` / `LOG.md` 是混批高发区，提交前先 `git diff --numstat`
   确认改动归属。需要只提交自己的 hunk 时，用
   「从 HEAD 取 blob + 只施加自己的替换 + `git hash-object -w` + `git update-index --cacheinfo`」
   —— **不要用 `git apply --cached`**（`*.md text=auto` 行尾归一化会对不上）。
3. **部署纪律**：
   - 服务端 `git pull` 对含大二进制的提交会 **反复 `fetch-pack: unexpected disconnect` 失败**；
     改用 `git bundle create x.bundle <base>..main` + `scp` + `git pull x.bundle main --ff-only`。
   - ⚠️ **`cmd 2>&1 | tail -N && next` 会吞掉退出码** —— pull 失败仍会跑 deploy，
     服务重启在旧代码上还以为部署成功。要么 `set -o pipefail`，要么分两条执行。
   - 改 nginx：备份 → 写 → `nginx -t` → `nginx -s reload`。
4. **提交前必跑**：`ruff check .`（当前 **0 错**）+ 分块 pytest + 前端 `tsc --noEmit` / `vitest`。
5. **探针/脚本进程必须加载 `.env`**：`set -a && . ./.env && set +a`，
   否则落到 mock 回复 / DEV 兜底密钥（本窗口踩过两次）。

## 2. 本窗口提交（时间序）

| commit | 内容 |
|--------|------|
| `5e7c33c` | 主动消息「静默时段空耗全天配额」根因修复（记账与投递解耦 / 免打扰前置 / 输出清洗 / 去重 / 可观测性） |
| `2411ffd` | 微信投递**五层谎报**修复（业务码 `ret` 校验 / `_send_to_all` 真实送达语义 / `context_token` 落盘） |
| `3d595a0` | **第六层**：websocket 零客户端也算送达；通道未就绪不再静默跳过 |
| `e3fb353` | `chat_sync` 复用常驻事件循环（原每次 `asyncio.run` → httpx 连接池全废 + 异常刷屏） |
| `4efc00a` | 关闭「几乎必然超时」的 LLM 安全分类（每条省 3s） |
| `a79122a` | **新增对话内追问**（回复后没等到接话自动再补一句） |
| `2b6ba47` | 追问参数改为 **web 控制端可调** |
| `bcc1675` | 注入检测「超时」不再判为「检测到注入」（原 fail-closed 把正常消息判成攻击） |
| `afdaddc` | `scenario` 降级为开场情境（场景漂移根因）+ **一句一句发** + 供应商链智谱打头 |
| `cfe0cb8` | **BYOK**：微信路径此前从不传 `user_llm_config`，用户自己的 API Key 形同虚设 |
| `114f15c` | **两个回复模式**（沉浸式真人 / 小说式，web 可切换）+ 追问改用真实上下文 |
| `91c02f7` | ADR-0015 系统提示词分层与按需注入 + 提示词规模埋点 |
| `6780c5f` | **（接手窗口）** `search` title 取到 Bing 面包屑 + `character_card` 反爬壳页谎报成功 —— 两项工具缺陷修复 |

## 3. 当前真相（均为实测）

- **测试**（2026-09-19 16:55 刷新）：`--collect-only` **1197**；分块实跑 **1193 passed / 4 skipped**。
  ⚠️ **基线依赖未追踪数据，不可跨会话复现**：`tests/test_persona_injection.py` 用
  `parametrize(sorted(Path("config/characters").glob("*.json")))`，**用例数 = 2 × 角色卡数 + 7**
  （当前 25 张 → 57 例），而 `config/characters/` **被 `.gitignore:117` 忽略**（磁盘 25 / git 追踪 0）。
  这解释了本页旧记 1105 与实际 1162 的差异。**引用基线必须同时声明卡数。**
  ⚠️ **`tests/test_llm_providers_routes.py` 是已知 flaky**：单独跑 24 passed / 41~50s，
  但**聚合态会随机挂死**（本次实测 100s 无输出）。改动后整跑变慢时先逐文件计时归因，
  不要默认是自己改坏的。同族：`test_integration.py` / `test_web_enricher.py`。
  ⚠️ **单进程整跑 `pytest -q` 会在随机位置停住**（非用例失败，属聚合态资源问题）。
  分块跑法见 `AGENTS.md` §4.3。
- **前端**：vitest **87 passed / 15 文件**，`tsc --noEmit` 0 错。
- **生产**：`139.199.199.174`，`health=200`，`unique-you-api`；
  微信投递链路**端到端验证通过**（14:15:38 实测送达）。
- **供应商链**：**智谱 → Agnes → 讯飞 → 百度 → DeepSeek**（用户决定智谱打头）。
  ⚠️ Agnes 自 14:15 起持续报错/变慢（单次 9~33s，14:27 时还是 0.8~6.8s）。
- **回复模式**：默认 `immersive`（沉浸式真人聊天）。
- **web 可调项**（`GET/POST /api/proactive/config`）：阈值 / 日上限 / 最小间隔 / 冷却 /
  免打扰起止 / **对话内追问开关·首次延迟·二次延迟·每日上限** / **回复模式**。
- ⚠️ **`.env` 里 `API_KEY_ENABLED=false`** —— 生产 API 认证处于**关闭**状态（网评期演示用）。

## 4. 工具审计（本窗口在生产环境逐个实测）

配置启用 8 个（`config/system.yaml` → `tools.builtin_tools`）：

| 工具 | 实测结果 |
|------|----------|
| `weather` | ✅ 真实可用（昆明：小雨 / 20℃ / 湿度 78 / 风 12） |
| `search` | ✅ **已修（`6780c5f`）**。原记录「DDG 抛异常 → 降级 Bing」与产线日志不符（该错误 0 次）；真缺陷是 **title 取到 `li.b_algo` 内第一个 `<a>`（Bing 面包屑）**。现主后端 = Bing 直抓（实测 5/5、0.6s、10 条），标题取自 `h2 a`；`health_check` 如实报 `primary`/`backends`。⚠️ **`ddgs` 9.x 实测 0/5（大陆全后端不可达、单次≈100s），已卸载，勿装回**。 |
| `calendar` | ✅ |
| `calculator` | ✅（`12*8+5` → 101） |
| `time_awareness` | ✅（需传 `action`：`current` / `holiday` / `lunar` / `workday`；实测农历「8月9」正确） |
| `set_reminder` | ✅（写入成功，返回 reminder_id） |
| `query_reminders` | ✅（只返回**待触发**项，故刚设的明日提醒返回 `[]` 属正常） |
| `character_card` | ✅ **已修（`6780c5f`）**。根因：百度反爬壳页 HTML 95 KB 但正文仅「百度百科」**4 字符**，旧代码只校验**体积**（`len(resp.text) < 2000`）便判 `success=True` → 降级链**第 1 步短路** → 可用的 `search_fetch`（1212 字符）永不执行。现加内容有效性判定 + 结构归一 + 维基不可达记忆。实测 `content_len` **4 → 118~250**，整链 33.2s → 稳态 **0.7~0.8s**。`fetch_wiki` 大陆仍不可达（**如实报错并指路**，新增 600s 不可达记忆避免反复白等）。 |

**未启用但代码存在**：`web_summary`、`image_gen`、`memory`、`scheduler`
（不在 `builtin_tools` 列表里 → 不会注册；注意「字典键名必须与 system.yaml 一致」这条注释）。

**人设增强（角色设置页的"火爬虫"入口，与对话内 `search` 工具是两回事）**：
`api/routers/knowledge_routes.py` → `persona_extractor/web_enricher.py`。
火爬虫（Firecrawl）**已于 08-27 被 Crawl4AI 主动替代**（`e1a4cec`，为免商业授权）。
⚠️ 2026-09-19 查明：`crawl4ai` **从未写进 `pyproject.toml`** → 按 pyproject 安装的生产服务器
根本没装；而 `Crawl4AISource.available` 又**写死 True**（注释还写「已预装，永远可用」），
导致 `_detect_sources()` 把它列为可用源、`search_all_sources()` 无条件调用 →
**`ModuleNotFoundError` 直接抛到 `/enrich` 端点**。已修为真探测 + 优雅降级，并补
`[project.optional-dependencies]` 的 `web-enrich` 额外项（**故意不默认安装**：
服务器内存 3.6GB / 磁盘 78%，crawl4ai 需常驻 Chromium，OOM 会连带打挂站点）。

**调用方式**：`_run_tools_if_needed()` 先用**关键词意图预筛**（`_tool_intent_names`），
再用 `llm.chat_with_tools` 调用；结果拼进 system prompt。
→ **工具能否真正触发取决于基座模型的 function calling 能力**（当前 glm-4-flash 档位）。

## 5. 未修 / 遗留风险（按优先级）

1. ~~**`character_card` 工具实际不可用**~~ → ✅ **已修（`6780c5f`）**：加内容有效性判定（体积校验≠内容校验）、
   各来源结构归一、维基不可达记忆。实测 `content_len` 4→118~250、稳态 0.7s。
2. ~~**`search` 工具脆弱**~~ → ✅ **已修（`6780c5f`）**。
   ⚠️ **原建议「改 `ddgs`」已被生产实测否决**：`ddgs` 9.x 聚合 Google/Brave/Startpage/Yahoo，
   大陆全不可达，实测 **0/5**、单次串行 ≈100s。现行方案是 Bing 直抓为主 + `duckduckgo_search` 8.1.1 为辅。
3. ~~**会话锁 → 罐头语**~~ → ✅ **已修（09-19）**：`process_message` / `_stream_mixin` 两处
   由「锁被占用即返回罐头语」改为**有界排队**（`_await_session_free`，上限
   `_SESSION_QUEUE_TIMEOUT=60s`），用户依次收到两条真实回复。
   ⚠️ 旧行为不只是话术问题 —— 它把用户刚发的那句**整个丢弃**。
   超 30s 得「抱歉，处理超时」那条仍存（属 provider 慢，非锁）。
4. **追问与主回复争抢 LLM**：慢 provider 下会加剧排队深度。是否需要限流仍待定。
5. **沉浸式仍有"小说感"**（09-19 部分修复）：括号旁白已消，但角色会**演面对面场景**
   （「我尝一口」「那我走」）。根因是**角色卡数据**把关系设成物理共处（如 62105bca 的
   `scenario`/`description`），已在沉浸式指令里加非共处约束 + 反例；
   ⚠️ **若仍复现，下一步该动的是卡片数据而不是指令**。
5. **ADR-0015 第 5 阶段未实施**（段落注册表 + L1 预算 + lorebook 引擎）——
   **先攒埋点数据再动手**：`_prepare_context` 已每轮输出
   `[prompt] total= character= rag= memory= summary= world= hist_msgs=`。
6. **生产 API 认证关闭**（`API_KEY_ENABLED=false`）——上线前需评估。
7. 前端 `npm audit` 有告警（未处理）。

## 6. 排查手册（本窗口踩出来的，直接抄）

**① 判断「是不是我刚改坏的」——一条命令定案**
```bash
grep -a '<故障特征>' data/app.log | sed -E 's/ \[.*//' | awk '{print $1,$2}' | sort | uniq -c
# 把故障按分钟聚合，比对部署时间戳。本次 30 秒内排除怀疑，避免盲目回滚。
```

**② 日志真源**：应用日志在 **`/opt/ai-girlfriend/data/app.log`**，
**不在 journald**（`systemctl` 的 `StandardOutput=append:` 基本无内容，
48h 内 journal 0 条 ERROR —— 只查 journal 会得出"服务无异常"的错误结论）。
日志含二进制字节，grep 要加 `-a`。

**③ 微信投递四查**（用户说"没收到"时按序查）
1. `data/wechat_context_tokens.json` 有无有效 token（**平台硬约束：用户必须 24h 内发过消息**）
2. 接口业务码：空 token 发送必得 `{"ret": -2, "errmsg": "prepare failed"}`
3. `_send_to_all` 返回值语义 = **是否真实送达**（console / 旧 `send_message_func` 不计入）
4. 是否走了真实通道（现会明确记录 `通道 X 未就绪` / `未送达任何真实通道`）

**④ 主动消息不发**：看 `data/app.log` 里 `ASE tick: ... reason=`；
`daily_limit` / `min_interval` / `quiet_hours` / `below_threshold` 直接指明原因。
⚠️ `result=True` 那行打印 `urgency=0.00` 是假象（`reset()` 副作用）。

**⑤ 签发 admin JWT 调受保护接口**
```bash
cd /opt/ai-girlfriend && set -a && . ./.env && set +a
.venv/bin/python -c "import sys;sys.path.insert(0,'.');from api.auth_jwt import create_access_token;print(create_access_token({'sub':'1'}))"
# ⚠️ 必须先注入 .env，否则 JWT_SECRET 缺失 → 落到 DEV 兜底密钥 → 签出的 token 报 Invalid token
```

**⑥ 部署：`git pull` 失败时的 bundle 通道（2026-09-19 实测修正）**
```bash
# 症状：fetch-pack: unexpected disconnect / early EOF（提交含截图二进制时必现）
git bundle create deploy.bundle 91c02f78..HEAD      # ← 用仓库内相对路径！
scp deploy.bundle swu-prod:/tmp/deploy.bundle
ssh swu-prod 'cd /opt/ai-girlfriend && git fetch /tmp/deploy.bundle HEAD && git merge --ff-only FETCH_HEAD'
```
⚠️ 三个坑：① `cmd | tail -N; echo $?` 打印的是 **tail** 的退出码（pull 失败却显示 0）；
② 用 `..HEAD` 生成 bundle 时 ref 名是 **`HEAD`**，`git pull <bundle> main` 会报
`couldn't find remote ref main`，需 `git fetch <bundle> HEAD`；git for Windows **不接受
`/c/...` 作为 bundle 输出路径**（静默不产出文件）；
③ **跨端同一性不能用 md5**（本地 CRLF / 服务器 LF 必不同）—— 比 `git hash-object`，
或直接看两侧 `git status --short` 是否为空。

**⑦ 探针「挂死」如何定位（2026-09-19 实测 11 分钟挂死）**
```bash
# 先看远程进程还在不在：不在 = 不是网络慢，是调用方在等一个永不返回的东西
ssh swu-prod 'ps -eo pid,etimes,args | grep -v grep | grep "\.venv/bin/python"'
# 再跑“限时探针”：写文件 → scp → timeout -s KILL <N> + python -u（无缓冲，逐步可见）
ssh swu-prod 'cd /opt/ai-girlfriend && set -a && . ./.env && set +a && timeout -s KILL 150 .venv/bin/python -u /tmp/probe.py'
```
⚠️ 排查要点：**先穷举出所有无界调用**。本模块当时唯一的无界调用是
`DDGS.text()`（无超时参数），其余全部有界（requests timeout 10~15s）。
另注意 `cloudscraper` 会把实际等待放大到约 **2×**（配 4s → 实耗 8s/域名），
**勿误判为 timeout 参数未生效**。

## 7. 下一步最安全顺序

1. **攒埋点数据（⏳ 待用户参与，仍是最优先）**：正常聊几条 → 读
   `[prompt] total=… character=… rag=… memory=…`，确定「哪一层在吃上下文」
   → 再定 ADR-0015 的预算参数（**不要凭直觉定**）。
   ⚠️ 2026-09-19 15:28 核查：埋点代码已部署，但 `data/app.log` 中 `[prompt]` **0 条**
   —— 部署后无真实对话触发，**本项无法由 AI 单方完成**，需用户先聊几条。
2. ~~修 `character_card`（可用性）与 `search`（依赖已改名）~~ → ✅ **已完成（`6780c5f`）**，见 §4 / §5。
3. 评估追问限流（避免与主回复抢 LLM）。
4. 处理会话锁罐头语（3）。⚠️ 本批已把 `character_card` 稳态耗时压到 0.7s（原 33.2s），
   对「工具调用拖长单轮」有直接缓解。
5. ADR-0015 第 5 阶段（段落注册表 + lorebook 引擎）。

## 8. 关联交接

- **Agent 层**（MCP / skills / 记忆）：`C:\Users\FOUR\.agents\HANDOFF.md`
- **架构决策**：`docs/adr/ADR-0015-系统提示词分层与按需注入.md`（含行业调研与一次自我纠正）
- **日常流水**：`LOG.md`（本窗口条目 68 号起）
- **上一窗口**：`docs/history/HANDOFF_REPORT-2026-09-14.md`

## 9. 并行会话分工与冲突规避（2026-09-19 18:25 登记）

| 归属 | 范围 | 状态 |
|---|---|---|
| **本窗口** | 后端 / 工具链 / 提示词 / 部署与生产排查 | 已交付两批（`6780c5f`…`e56e16d`），**无未提交改动** |
| **另一窗口** | **前端优化**（`frontend/`，用户 09-19 指派） | 进行中 |

⚠️ 双方都适用的三条硬约束：
1. **不碰对方目录**：本窗口不动 `frontend/`；前端窗口不要动后端文件。
2. **严禁 `git add -A` / `git commit -a`** —— 一律显式路径提交。`LOG.md` / `AGENTS.md` /
   `README.md` / `docs/HANDOFF_REPORT.md` 是**共享追加型文件**，混批高发。
   本窗口最后条目为 LOG **七十三**，前端窗口请从 **七十四** 起。
3. **服务器部署互斥**：`remote_deploy.sh` 末尾会 `systemctl restart ai-girlfriend`，
   两会话同时部署会互相打断。部署前先 `git log --oneline -1` 对齐服务器 HEAD，
   用 `dbdd6e3` 之后的**增量 bundle**（配方见 §6-⑥）。

**给前端窗口的一条关键提醒**：线上 `frontend/dist` 的 mtime 是 09-19 15:16
（上一会话跑 `remote_deploy.sh` 留下的），**内容来自 `91c02f78` 时期的前端源码**，
不含 `22375da`…`e640341` 这批前端改动 —— 即**线上 UI 目前落后于仓库源码**。
重建前务必先备份，网评期内站点不可中断：
```bash
cd /opt/ai-girlfriend/frontend && cp -r dist dist.rollback-$(date +%Y%m%d-%H%M) && npm run build
```
