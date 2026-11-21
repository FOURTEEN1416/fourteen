# DELETION_LOG 条目模板：e2e/测试账号清理

> 本文件是模板，不是已执行的删除记录。实际条目由
> `scripts/purge_e2e_accounts.py --apply` 在 `data/archive/DELETION_LOG_ENTRY.md`
> 生成（含真实计数、备份路径与残留验证结果），确认后再摘录到 `docs/DELETION_LOG.md`。

```markdown
## [YYYY-MM-DD] e2e/测试命名空间账号清理批（脚本 `scripts/purge_e2e_accounts.py`）

### 依据
- 主动线治理审计：历史 ASE 索引键含 e2e/wbtest 账号，死会话每 5 分钟
  写 vital_signs、烧决策 LLM 与 post-draft judge；测试账号无命名空间约束。
- 本批为**一次性数据清理**，不改变正常账号注销 API 的语义。

### 目标账号
- user_id=<ID>，username=`<USERNAME>`

### 删除范围（4 库 + 控制面/通道/ASE/私有角色实例逐表计数）
- **users.db**：<TABLE>: <N> ...（`operator_audit` 不在删除范围：审计生命期长于账号）
- **sqlite.db**：<TABLE>: <N> ...
- **agent_plane.db**：event_ledger: <N>（含 `channel:<uid>:<slot>` 通道状态事件）
- **runtime_plane.db**：<N> 行（channel_desired_state / channel_presence /
  outbound_commands / control_commands / inbound_seen / followup_budget /
  effect_dedup / session_presence 合计）
- **character_instances**：私有卡文件 <N>、知识文件 <N>、精确 cid 成就行 <N>
  （公共模板/无主卡保留）
- **ase_states**：index.json 条目 <N>
- **wechat_channels**：连接器 <N>、`data/wechat_sessions/<uid>/` 目录 <0|1>
- **scheduler_config.json**：throttle 账本 <N> 条（8 个字典段 + gate_skip +
  important_dates_sent）

### 备份（`data/archive/purge_e2e_<UTC 时间戳>/`）
- `users.db` → `.../users.db`（integrity_check=ok）
- `sqlite.db` → `.../sqlite.db`（integrity_check=ok）
- `agent_plane.db` → `.../agent_plane.db`（integrity_check=ok）
- `runtime_plane.db` → `.../runtime_plane.db`（integrity_check=ok）
- `scheduler_config.json` → `.../scheduler_config.json`（JSON 配置快照）

### 验证
- 删除后跨表残留扫描：**0**（-1 = 读数失败，按残留处理；任何计数失败即判不通过、退出码非零）
- 二次执行：目标集合为空（幂等）。
- 不含聊天正文、密钥或 token；仅计数与账号 id。

> ⚠️ 执行前提：必须在**服务冷停后**运行 `--apply`。脚本启动时会读
> `runtime_plane.channel_presence` 检测目标账号的活跃连接器宿主，presence
> 活跃或不可读即拒绝执行（否则 `restore_on_boot` 会按磁盘凭证复活已删账号
> 的通道）。

**Reversible**: 从上述备份恢复 4 库与 `scheduler_config.json` 即可回滚（恢复前先停服务）。
```
