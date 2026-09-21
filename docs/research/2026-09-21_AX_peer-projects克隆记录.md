# peer-projects 克隆记录（任务包 AX · 阶段 A）

> 日期：2026-09-21  
> 路径：`D:\Desktop\peer-projects\`（**不入本仓 git，不上服务器**）  
> 方式：GitHub-First 浅克隆 `git clone --depth 1`  
> 宪法依据：交接书 §3.A；对照仓仅作只读学习

| 本地目录 | GitHub | 许可证 | Stars(检索时) | branch | HEAD | 活跃度(检出时) | 学习优先级 |
|---------|--------|--------|---------------|--------|------|----------------|-----------|
| nana | [mewamew/nana](https://github.com/mewamew/nana) | MIT | 136 | main | `99a3b3e` | 2026-09-18 | **P0** memory_extract / reply JSON / heartbeat |
| Artemis | [momori777/Artemis](https://github.com/momori777/Artemis) | NOASSERTION | 344 | master | `91b0d16` | 2026-09-20 | **P0** AgentRuntime / curator / ContextPolicy |
| MetaPact | [Lovappen/MetaPact](https://github.com/Lovappen/MetaPact) | 见仓内 | 88 | main | `cd33272` | — | **P0** Agent Pack 形态 / 阶段间隔 / memory-write |
| my-raze | [Do-fei/my-raze](https://github.com/Do-fei/my-raze) | MIT | 7 | main | `a51eb79` | 2026-09-05 | **P0** near-dup reinforce / k(level) / scoreMemory |
| SillyTavern | [SillyTavern/SillyTavern](https://github.com/SillyTavern/SillyTavern) | AGPL-3.0 | 33606 | release | `06bde93` | 活跃 | 跳过深读（v1.15 已对齐 prompt 序列） |
| awesome-ai-companion | [DasterProkio/awesome-ai-companion](https://github.com/DasterProkio/awesome-ai-companion) | 见仓内 | 714 | main | `3251808` | 2026-09-20 | 生态地图，不深读代码 |
| jiwen | [ClaraShafiq/jiwen](https://github.com/ClaraShafiq/jiwen) | MIT | 158 | main | `1c15a1f` | 活跃 | **P1** 五轴内驱确定性漂移 |
| WrenWen | [ssxl0126/WrenWen](https://github.com/ssxl0126/WrenWen) | 文档仓 | 43+ | main | `b987ae0` | 2026-09-19 | 文档主轴参考（无代码可搬） |
| generative_agents | [joonspk-research/generative_agents](https://github.com/joonspk-research/generative_agents) | Apache-2.0 | 22130 | main | `fe05a71` | 论文代码 | **P2** 记忆流 importance 打分（可选） |

## 关键实现路径（学习入口）

```
nana/backend/
  conversation.py          # 三层记忆：turns / summaries.json / user_profile.json
  main_agent.py            # reply_stream + tool loop MAX=3 + 状态回写
  emotional_state.py       # valence/arousal + 阶段 hint + JSON 持久化
  heartbeat.py             # 主动消息门控
  prompts/memory_extract.md
  prompts/reply.md         # 槽位 + 强制 JSON 回写
  prompts/tool_decision.md

Artemis/skills/sakura/app/
  agent/memory.py          # MemoryStore 分层 + importance/confidence
  agent/memory_curator.py  # 第一人称整理：create/update/archive 操作
  agent/memory_recall.py   # 召回阈值 0.3 / trust / token_budget
  agent/runtime.py         # 工具循环（读文件时抓 _run_tool_loop）
  agent/runtime_limits.py  # step≤4 / step_tools≤3 / turn_tools≤8 / result≤6000
  llm/prompts/runtime.py   # ContextPolicy 预算 + untrusted 信封
  llm/prompts/types.py

MetaPact/nako/agent/
  MEMORY.md HEARTBEAT.md SOUL.md
  scripts/memory-write.sh  # 短期 5 条滚动 + affinity 阈值
  scripts/heartbeat-check.sh

my-raze/
  server/memory.ts         # extract → bigram near-dup reinforce → capacity
  shared/memory.ts         # memoryCapacity / memoryInjectionK / scoreMemory
  shared/intimacy.ts

jiwen/jiwen.js             # 五轴 connection/pride/valence/arousal/immersion
```

## 本机约束提醒

- 对照仓 **不入 git、不进服务器**；学习结论写进 `docs/research/` 即可复审。
- Artemis 许可证为 `NOASSERTION`，**禁止拷贝代码进主仓**；只吸收机制思想 + 自研实现。
- SillyTavern AGPL **禁止直接引入源码**；只作 prompt 序列对照。
- 若 `D:\Desktop\peer-projects\` 再次丢失，按本表 URL 重克隆即可复审。
