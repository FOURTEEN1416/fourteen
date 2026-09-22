# W3 热点知识链 · 接线契约（交主控收仓时落）

> 来源窗：`wt/hot-knowledge`（W3）。本窗禁改 `proactive/scheduler.py`、
> `orchestrator/_init_mixin.py`、`utils/session_key.py`、`shisi/api/registry.py`。
> 全链设计与选路理由见 `docs/design/2026-09-22_热点入知识库主动消息链设计.md`。
> （前序提交曾把本契约误写入 BOARD.md 追加区——BOARD owner 恒为主控，已整批还原，
> 内容迁至本文件。）

## 1. 必须落的接线：scheduler 注册采集任务（唯一硬缺口）

热点采集已做成同步幂等自限速函数，**不注册则池永不增长**（过渡期可手动跑
`python scripts/run_hot_topics_collect.py`）。

- 文件：`proactive/scheduler.py`
- 形态：仿既有 `_sync_vault_job` / `_run_vault_collect` 注册
- job id：`hot_topics_collect`；触发：`IntervalTrigger(minutes=60)`，startup 即注册
- 开关**不在 scheduler 侧重复造配置**——`enabled`/`interval_minutes` 由
  `config/hot_topics.yaml` 单一真源判（`collect_if_due` 内部自限速，注册频率高于配置
  间隔也只是空转 skip，可任意调）

```python
# proactive/scheduler.py（新增，仿 vault_collect 同构：
#   _safe_job_wrapper + misfire_grace_time + coalesce，多 worker 下 flock 池写入安全）
def _run_hot_topics_collect(self) -> None:
    from shisi.knowledge.hot_topics import collect_if_due
    collect_if_due()   # 同步、自限速、吞异常返回 dict，永不抛出
```

注册点契约：`self._register_job("hot_topics_collect", self._run_hot_topics_collect,
IntervalTrigger(minutes=60), ...)` ——以 scheduler 既有注册辅助函数实际签名为准，
本窗不代改。

## 2. 无需主控动作的既有收口（供审阅确认）

- **供出口**：`proactive/ase_engine.py::_try_knowledge_share`（本窗已改，唯一一处）——
  `get_hot_context(character_id)` 热点段前置拼入 excerpt；`orchestrator/_init_mixin.py`
  的 `_inject_ase_knowledge` 注入链（`_knowledge_share_func` / `_knowledge_character_id`）
  **零改动**，热点经既有 share 通道供出，不另造第二通道。
- **知识服务**：`CharacterKnowledgeService` 零改动（热点不进对话 RAG 域，理由见设计 §5
  方案 B 否决项）。
- **conftest**：`tests/conftest.py::isolate_runtime_state_files` 增列
  `data/hot_topics.json` 重定向（独立 try 块防连带跳过）——本窗已改（前序提交 7f976de
  已声明越出"新增文件"白名单仅此一处，收仓可单独摘除；摘除则必须确保
  `tests/test_hot_topics.py` autouse 夹具兜底在位，勿两头都无）。

## 3. 收仓后验收建议（主检出，41 卡在位口径）

1. `PYTHONPATH= python -m pytest tests/test_hot_topics.py -q` → 12 passed；
2. 全量分块回归（收集数 = 基点 + 12，卡目录按纪律先声明在位与否）；
3. 真实一轮采集冒烟：`python scripts/run_hot_topics_collect.py --force` 看
   `data/hot_topics.json` 池非空且 `pool_size > 0`；
4. scheduler 注册后观察日志出现 `hot_topics_collect` 任务且间隔窗内秒级 skip。

## 4. 本窗已验证读数与隔离声明（收仓参考）

- 本检出已从主检出补入 41 张角色卡（`config/characters/` 41 文件在位，gitignored
  不随分支复现）→ 与本批基线口径可比。
- 前序会话四分块全量实测：**收集 1876 = 二十次口径 1864 + 本窗新例 12** 精确吻合。
- 宿主 `data/hot_topics.json` 实测未生成（autouse 夹具 + conftest 双保险，零自伤）。
