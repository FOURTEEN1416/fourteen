# 黄金集（Golden Set）— R6 2026-10-21

聊天质量的**可回归**基线：把用户反复报障的缺陷形态（生产原句）固化成用例，
每次改 prompt / 守卫 / 模型链都能一条命令验证「没退化」。

## 为什么是「确定性优先」

| 层 | 来源 | 是否进 pytest 基线 |
|---|---|---|
| 守卫类断言（复述/归属/元记忆/反问/模式/时间词/标记） | `utils.prompt_sanitize.terminal_reply_guard()` **公共入口** | **进**（fail 即红） |
| 注入侧断言（知识库括号、prompt 措辞回潮、主动模板池） | `CharacterAggregate.build_system_prompt` / 模块常量 | **进** |
| 网关可观测（降级事件） | `llm_provider.multi_provider_gateway` 源码结构化事件 | **进** |
| LLM 判定（自然度/话题/温度） | `judges.py`（默认关闭） | **不进**（borderline 不计 fail） |

**纪律**：黄金集**不实现第二份判定逻辑**。它只构造输入、调用被测真源、比对输出。
守卫改版时只有「行为变了」才会红，不会因为内部函数改名而误红。

## 目录

```
tests/golden/
  README.md                    本文件
  cases/*.jsonl                用例（每行一条，8 个文件）
  runner.py                    加载 + 判定 + CLI + Markdown 报告
  test_golden_set.py           pytest 入口（逐例参数化 + 自检）
  judges.py                    可选 LLM 判定（默认关闭）
```

## 用例 Schema

```json
{
  "id": "GS-ECHO-001",
  "category": "复述与归属",
  "source": "AGENTS v1.55 / chat_history:2873（生产取证）",
  "input": {
    "user_msg": "今天加班好累啊",
    "character_name": "十四",
    "reply_mode": "immersive"
  },
  "candidate_reply": "今天加班好累啊\n那你早点休息哦",
  "expect": {"guard_equals": "那你早点休息哦"},
  "assertions": [{"type": "strip_verbatim_echo", "must_trigger": true}],
  "llm_judge": null,
  "live_llm": false
}
```

### `input` 允许的键

**公共守卫参数**（直接透传 `terminal_reply_guard`）：
`user_msg` / `character_name` / `session_key` / `character_id` /
`last_turn_age_seconds` / `reply_mode`

**runner 专用**（不传给守卫）：
`history`（话题锚点）、`now`（ISO 串，时间窗基准）、
`character_id_from_name`（按角色名解析真实 id，卡缺失则 skip）、
`requires_character_card`（缺卡 skip 开关）、`knowledge_context`（注入侧用例）。

混入其它键 → `evaluate()` 直接判红（防黄金集绑死私有实现）。

### `expect` 支持的键

`guard_equals` / `guard_not_contains` / `guard_contains` / `guard_empty` /
`guard_non_empty` / `forbidden_patterns`（正则）。

### `assertions[].type` 白名单（22 种，实现见 `runner._CHECKERS`）

`strip_verbatim_echo` · `no_meta_memory` · `no_attribution_flip` ·
`no_ungrounded_reciprocal` · `no_stage_direction` · `no_system_marker` ·
`time_recalibrated` · `guard_passthrough` · `guard_empty` · `non_empty` ·
`guard_stable`（幂等）· `vocative_stripped` · `topic_anchored` ·
`custom_regex_forbid` / `custom_regex_require` · `hard_violation_stripped` ·
`paragraph_breaks_preserved` · `prompt_regex_forbid` ·
`proactive_template_forbid` · `gateway_log_present` ·
`knowledge_context_stripped` · `extra_error_sentinel`

## 怎么跑

```bash
# ① pytest（进基线；live 用例自动 skip）
python -m pytest tests/golden/test_golden_set.py -q

# ② 独立 CLI（部署后生产冒烟，输出 Markdown 报告）
python tests/golden/runner.py --report tests/golden/latest.md
python tests/golden/runner.py --only GS-ECHO      # 只跑某类

# ③ 在线判定（结果不计基线）
GOLDEN_LIVE=1 python -m pytest tests/golden/test_golden_set.py -q   # 确定性 + 客服腔词表，不出网
GOLDEN_LIVE=1 GOLDEN_PROVIDER=agnes python -m pytest ...            # 再加真 LLM 判定（真出网）
python tests/golden/runner.py --live --provider agnes               # CLI 等价入口
```

退出码：`0` 全绿 / `1` 有失败 / `2` 用例集为空或加载失败。

### live 判定的语义（2026-10-22 修复后）

- **LLM 判定不是门禁**：`judges` 给出 `fail` 时用例记为 `JUDGE-FAIL`（pytest 侧
  记为 `xfail`），**不改退出码、不进基线** —— 否则既有缺口会让 live 通道恒红、
  没人再跑（该通道 2026-10-21 首版就因此腐烂过一次）。
- **判不了不算不合格**：无 LLM（未设 `GOLDEN_PROVIDER`）、调用失败、输出不可解析、
  置信度 `< 0.7` 一律 `borderline` 且通过。
- 唯一「确定性 fail」来自 `judges.SERVICE_TONE_MARKERS`（客服腔词表，不出网）——
  GS-DEG-001 即靠它判出，见下方「已知偏差」第 1 条。
- 🔴 **修复记录（2026-10-22 独立复验 P1）**：`runner.evaluate` 旧写
  `from tests.golden import judges`，而 `tests/` 无 `__init__.py` 且本机
  site-packages 有同名常规包 `tests` ⇒ **恒 `ModuleNotFoundError`**，即上面 ③
  的两条路径写进 README 却从未跑通（`GOLDEN_LIVE=1 pytest` → 3 failed；
  `runner.py --live` → 退出码 1）。现改为 `runner._load_judges()` 按**文件路径**
  加载（与 `test_golden_set.py` 同纪律），并补 5 条回归用例（含真供应商冒烟）。

## 用例清单（45 条；其中 GS-DEG-001 标记 `live_llm`，默认 skip ⇒ 44 条确定性进基线）

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `echo_attribution.jsonl` | 6 | 逐字复述（含标点不敏感/纯复述）、归属翻转 + 两条负向保护 |
| `meta_memory.jsonl` | 4 | 元记忆解释（生产原句/句中嵌/自贬形态）+ 正常关怀负向 |
| `mode_boundary.jsonl` | 7 | 沉浸式括号/星号剥除、小说模式保留、剥空 fail-closed、知识库第二通道（注入侧） |
| `ungrounded_reciprocal.jsonl` | 5 | 虚构反问截断、裸「你呢」整行、有由头/陈述句/主动链三条负向 |
| `topic_awareness.jsonl` | 5 | 24h 时间窗、纯寒暄/空应答排除、当前原话恒入、说话人指认 |
| `degradation_quality.jsonl` | 4 | 降级原句守卫幂等（**GS-DEG-001 = live_llm，默认 skip**）、`quality_degraded` 事件在场、降级仍过守卫、缺口登记 |
| `guard_regression.jsonl` | 8 | 系统标记/间隔注记/时间词降级+负向/哨兵/退化名/非法称呼/词边界 |
| `aiyu_contrast.jsonl` | 6 | 已删激励措辞不得回潮、元话术回潮、主动模板池打卡句、分段、自称 AI、自然度 |

（合计 45 条；1 条 live_llm 默认 skip，44 条确定性进 pytest 基线。）

## 已知偏差与缺口（必须读）

1. **客服腔不被出站守卫覆盖**（GS-DEG-001/004）：实测
   `terminal_reply_guard("好的，注意保暖哦")` **原样返回** —— 客服腔不在 v1.54
   五类语义守卫（复述/归属/元记忆/反问/动作段）里。GS-DEG-004 把「零改动」钉成
   事实，防止后人误以为已覆盖；真正兜底只能靠 `judges.SERVICE_TONE_MARKERS`
   （`--live`）或未来新增守卫规则。**实测（2026-10-22，agnes 真调用）**：
   `--live` 下 GS-DEG-001 计为 `JUDGE-FAIL`（明细「客服腔词表命中
   ['好的，注意保暖']」），按上节语义**不改退出码**；该缺口仍为产品级未决项。
2. **沉浸式守卫会把连续空行压成单换行**（GS-AIYU-004）：实测
   `"A\n\nB"` → `"A\nB"`（`strip_stage_directions` 的 `splitlines()` 重组）。
   属既有行为、不在本任务文件白名单内；黄金集按真实语义断言「段落内容不丢、
   段数不减少」，README 在此登记。
3. **「>3 段强制分段」规则不存在**：设计原稿对照爱语 idx 21 写了这条，但本仓库
   没有该生成侧硬规则（属 prompt 增强）。黄金集不含该断言，避免无限红。
4. **生产原句以 AGENTS 版本头转述为准**：本地无生产库，`source` 里标注的
   `chat_history:2873/2875` 是 AGENTS v1.55 版本头的取证编号。拿到生产库后应
   逐字核对并更新 `candidate_reply`。
5. **爱语对照是方向参照不是硬门槛**：`D:\Desktop\爱语逆向\work\产物\aiyu_prompts_full.json`
   为 2.8.8 逆向产物（43 条 list，字段 `{cls, line, len, text}`），爱语升级后对照会漂。
6. **角色卡浮动**：`config/characters/` 被 `.gitignore` 忽略（AGENTS §4.3），
   GS-GUARD-007/008 依赖「林挽夏」卡 → 缺卡时**只 skip 该例**，不让整批红。

## 为什么放在 `tests/golden/`（而非 `eval/`）

1. `tests/**` owner 恒为 W4 验证窗口（AGENTS §2/§8），黄金集天然继承 owner 纪律；
2. `tests/` 属 **A 档部署相关**（AGENTS §3），服务器可消费、随 git 三端一致，
   黄金集需要在生产 venv 做冒烟 —— 新建 `eval/` 要重新裁决分档，无谓增加决策点；
3. 既有分块基线（`find tests -name "test_*.py"`）自动覆盖，零基建改动；
4. **注意**：`tests/` 没有 `__init__.py`，且本机 site-packages 存在同名包
   `tests`（`C:\Program Files\Python312\Lib\site-packages\tests\__init__.py`）——
   `import tests.golden` 会解析到 site-packages 而失败。故 `test_golden_set.py`
   按**文件路径**注入 `sys.path` 后 `import runner`（见该文件头部注释）。

## 维护规程

1. **新增用例**：只加 JSONL 行，不改判定逻辑；必须写 `source`。
2. **守卫改版**：先跑 `pytest tests/golden -q`；红的用例逐条确认是「预期行为变更」
   还是「回归」。预期变更请更新 `candidate_reply`/`expect` 并在 `source` 注明原因。
3. **新增断言类型**：必须同时改 `runner.ASSERTION_TYPES` + `_CHECKERS` +
   `test_case_files_exist_and_are_non_empty` 之外的 `test_no_case_asserts_private_internals...`
   （类型白名单自查）。
4. **不得**在黄金集里 import 守卫私有函数（`_strip_*`）做断言 —— 只经公共入口。
   例外：`topic_anchored` 用 `_extract_user_anchor_sentences`（该函数无公共包装）。
5. **分块跑法**：黄金集随全量四分块跑（AGENTS §4.3），不得与其它 pytest 并发。
