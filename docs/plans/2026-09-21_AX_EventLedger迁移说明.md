# EventLedger 与 affinity_records 迁移说明

> 方案配套 · 阶段 C 骨架  
> 现状代码：`shisi/migrations.py::affinity_records` / `shisi/affinity/enhancer.py::_record_affinity`

## 现状（实读）

```sql
affinity_records (
  id, character_id, old_value, new_value, delta,
  reason, source, created_at
)
```

- **写**：好感度变更时 INSERT（`enhancer.py` ~L126-137）  
- **读**：生产热路径 **无消费**（只写不读 → 小凌「账本」空转证据）  
- **键**：仅 `character_id`，P1 后实际关系键为 `user_id::character_id`，**记录表未带 user 维度** → 回放无法按用户切片

## 目标

| 项 | EventLedger `event_ledger` |
|----|----------------------------|
| 主键 | `event_id` + 索引 `(session_key, created_at/turn_id/event_type)` |
| 关系事件 | `event_type=affinity_delta`，payload 含 `old/new/delta/reason/scale_level` |
| 会话 | **完整 session_key**（含 `N:peer`） |
| 消费 | P1 起投影/探针可读 |

## 迁移批次

### P0（本批骨架，生产默认可不启用双写）
1. 新增 `data/agent_plane.db` + `shisi/agent_plane/event_ledger.py`  
2. **不删** `affinity_records`  
3. 文档声明：关系数值权威仍是 `shisi/affinity/scale.py` + `utils/affinity_state`  
4. 实施批可选：`AffinityEnhancer._record_affinity` 成功后追加 ledger 镜像事件（开关 `AGENT_PLANE_AFFINITY_MIRROR`）

### P1（读投影）
1. 所有关系/画像/工具事件写 ledger  
2. `affinity_records` 保留为历史；**新读路径走 ledger 投影** 或 scale 状态（二选一，禁止双读打架）  
3. 补 user 维度：ledger 的 session_key 已含用户，无需再改 affinity_records 结构

### P2+
1. 若 affinity_records 仅历史归档 → 停止双写或只写 ledger  
2. 回滚：关闭开关即可；历史表未破坏

## 兼容与回滚

| 风险 | 处理 |
|------|------|
| 双写不一致 | P0 只镜像不读；探针以 ledger 事件顺序为准 |
| SQLite 锁 | 独立 db 文件；短事务 |
| 整批回滚 | 删除 agent_plane 包 + 停双写；affinity_records 原样 |

## 明确不做
- 不在 P0 把 `affinity_records` 表结构改写成通用事件表（热路径回归风险）  
- 不用 ledger 替代 scale 数值计算逻辑
