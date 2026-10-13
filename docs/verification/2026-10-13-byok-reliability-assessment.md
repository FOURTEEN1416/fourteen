# BYOK 机制可靠性评估（2026-10-13，用户裁决「机制确实存在，但是否可靠可用需要评估」）

评估方法：五环代码级追踪（配置入口→存储→取用→网关构造→附件透传）+ 专项测试实跑（test_byok 9 例 + test_llm_config_verification 10 例全绿）。

## 五环取证结论

| 环节 | 实现 | 判定 |
|------|------|------|
| ① 配置入口 | `POST /api/user/llm-config`（misc_routes:539）：认证后写 `users.llm_config`；空 api_key 合并保留旧值；写后 `invalidate_user_llm` 清缓存 | ✅ 可靠 |
| ② 存储 | `users.llm_config` JSON 列（users.db）；读取经 `_sanitize_config` 递归脱敏（SEC-P0 修复在案，providers 子层不回显） | ✅ 可靠 |
| ③ 网关取用 | `get_user_llm`：按用户缓存 `ReloadableLLMGateway`，fingerprint 变更重建；**`inherit_platform=False`**（用户网关物理上拿不到平台文件/环境凭证）；显式配置失败 `raise ValueError` **绝不回落平台 key**（AGENTS §1.2「显式用户配置错误禁止回落平台凭证」有实现背书） | ✅ 可靠（fail-closed） |
| ④ 链路覆盖 | web 聊天（chat_routes:283）与**微信聊天**（user_scheduler `_get_user_llm_config`，09-19 修复的对称性缺口）都传 `user_llm_config`；后台任务走 `byok.session_llm` 同策略；BYOK_REQUIRED 开启时 `ensure_user_has_key` 前置 403 | ✅ 可靠 |
| ⑤ 附件透传 | 用户 auto 网关 = `MultiProviderGateway(inherit_platform=False)`，与平台网关同一 `chat(attachments=...)` 入口 → 图片直传对 BYOK 用户**天然可用**（供应商支持视觉即走 direct，否则降级 describe） | ✅ 天然覆盖 |

## 发现的两个「可靠性打折」点（不阻塞，登记）

1. **BYOK 默认关闭**：`llm.byok_required` 默认 false——当前生产实际是「平台 key 服务所有人」模式；BYOK 是**可选开关**不是现行强制。用户填自己的 key 后立即走自己 key（select_request_llm 优先用户配置），但没填的用户仍在用平台额度。**是否开启强制模式是产品决策**（开启后未填 key 的用户 403）。
2. **供应商白名单窄**：用户可选 agnes/zhipu/xunfei/baidu/deepseek/custom——无 openai/anthropic 等国际家（搜索分域裁决禁提的几家刻意不在列）；custom 兜底任意 OpenAI 兼容端点，覆盖面够用。视觉能力跟随用户选的 model（无 vision 模型清单校验——配了纯文本模型发图会静默降级 describe，不报错）。

## 结论

**机制本体可靠可用**：fail-closed 设计（绝不借平台凭证）、配置变更即时生效（缓存失效）、web/微信双链对称、图片附件天然透传。测试 19/19 绿。
**需要产品决策的只有一件事**：`llm.byok_required` 是否开启（开启=用户必须自带 key 才能用）。
