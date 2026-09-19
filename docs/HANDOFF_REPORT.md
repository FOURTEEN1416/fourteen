# 唯一的你·十四 — 新窗口交接（2026-09-19）

> 上一窗口交接已归档：`docs/history/HANDOFF_REPORT-2026-09-14.md`
> 本文是**当前状态的单一真源**；日常流水见 `LOG.md`，决策见 `docs/DECISION_LEDGER.md` 与 `docs/adr/`。

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

## 3. 当前真相（均为实测）

- **测试**：`--collect-only` **1105**；分块实跑 **1101 passed / 4 skipped**。
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
| `search` | ⚠️ 能用但脆弱：`duckduckgo_search` **已改名 `ddgs`**，DDG 抛异常 → 降级 Bing 抓取（能拿到真实结果），但日志刷 traceback |
| `calendar` | ✅ |
| `calculator` | ✅（`12*8+5` → 101） |
| `time_awareness` | ✅（需传 `action`：`current` / `holiday` / `lunar` / `workday`；实测农历「8月9」正确） |
| `set_reminder` | ✅（写入成功，返回 reminder_id） |
| `query_reminders` | ✅（只返回**待触发**项，故刚设的明日提醒返回 `[]` 属正常） |
| `character_card` | ❌ **半可用**：`fetch_wiki` 大陆网络不可达（**如实报错并指路**）；`fetch_person`（百度）返回 `success=True` 但 `content` 为空 —— **成功但无数据**，属同一类"谎报" |

**未启用但代码存在**：`web_summary`、`image_gen`、`memory`、`scheduler`
（不在 `builtin_tools` 列表里 → 不会注册；注意「字典键名必须与 system.yaml 一致」这条注释）。

**调用方式**：`_run_tools_if_needed()` 先用**关键词意图预筛**（`_tool_intent_names`），
再用 `llm.chat_with_tools` 调用；结果拼进 system prompt。
→ **工具能否真正触发取决于基座模型的 function calling 能力**（当前 glm-4-flash 档位）。

## 5. 未修 / 遗留风险（按优先级）

1. **`character_card` 工具实际不可用**（见上表）——「语义上成功、数据为空」，需要修提取逻辑或换数据源。
2. **`search` 工具脆弱**：依赖已改名的包 + Bing 抓取降级；建议改 `ddgs` 并加结果校验。
3. **会话锁 → 罐头语**：慢 provider 下连发消息得「处理中, 请稍候...」，超 30s 得「抱歉，处理超时」。
4. **追问与主回复争抢 LLM**：慢 provider 下会加剧 3。是否需要限流待定。
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

## 7. 下一步最安全顺序

1. **攒埋点数据**：正常聊几条 → 读 `[prompt] total=… character=… rag=… memory=…`，
   确定「哪一层在吃上下文」→ 再定 ADR-0015 的预算参数（**不要凭直觉定**）。
2. 修 `character_card`（可用性）与 `search`（依赖已改名）。
3. 评估追问限流（避免与主回复抢 LLM）。
4. 处理会话锁罐头语（3）。
5. ADR-0015 第 5 阶段（段落注册表 + lorebook 引擎）。

## 8. 关联交接

- **Agent 层**（MCP / skills / 记忆）：`C:\Users\FOUR\.agents\HANDOFF.md`
- **架构决策**：`docs/adr/ADR-0015-系统提示词分层与按需注入.md`（含行业调研与一次自我纠正）
- **日常流水**：`LOG.md`（本窗口条目 68 号起）
- **上一窗口**：`docs/history/HANDOFF_REPORT-2026-09-14.md`
