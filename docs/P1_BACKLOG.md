# P1 Backlog - 唯一的你

**Created**: 2026-06-03
**Last Updated**: 2026-08-24 (全面核查后重写 — 逐项实测验证)
**Status**: Active (仅保留真实未决项)

> ✅ **2026-08-24 全面核查结论**：以下条目经代码实读 + 测试实跑验证，均已在历史提交中修复，原描述过期：

## 已解决（2026-08-24 实测验证）

| 原条目 | 验证证据 |
|--------|---------|
| ~~[P1-2] Storyline 数据缺失~~ | `api/routers/storyline_routes.py` 6 端点完整；404 仅角色不存在时返回（正确语义）；空配置返回规范 `{enabled:false, configured:false}`；`test_storyline.py` 5 测试过 |
| ~~[P1-3] 21 Orphan Pages~~ | 2026-06-02 已清零 |
| ~~[P1-4] StatusCenter mock data~~ | `StatusCenter.tsx` 全接 React Query 真数据（useDashboard/useEmotionState/useMemoryFacts），零 mock |
| ~~[P1-5] WeChatPage placeholder~~ | `WeChatPage.tsx` 413 行完整实现（状态轮询/二维码/重连），6 测试过 |
| ~~[P1-6] shisi/* 旧路径~~ | `frontend/src/api/system.ts` 零 shisi 引用 |
| ~~[P1-7] TTS v2 认证缺失~~ | mimo_voice_routes(7)/voice_routes(7)/safety_routes(13) 全接入 `api/auth.py::verify_api_key_dep`；app_factory 全局 configure_auth；tts/mimo/auth/voice 124 测试过 |
| ~~[P1-8] LLM 假流式~~ | `orchestrator/_stream_mixin.py` 实现真流式优先（chat_stream token 级推送）+ 伪流式降级，属成熟设计非缺陷 |
| ~~[P1-12] AGENTS.md pages 数字~~ | 2026-06-03 已修 |
| ~~[P1-14] deploy/.env.production 占位符~~ | 2026-08-24 SSH 实测服务器 `/opt/ai-girlfriend/.env` 零 CHANGE_ME（模板占位符仅存在于仓库模板，属设计意图）；ai-girlfriend + nginx 服务 active |
| ~~[P1-15] 硬编码 Secret (CDL Forger)~~ | 2026-08-24 全局 grep 无硬编码 secret；AGENTS.md 当前版本无 API_SECRET |
| ~~[P1-16] CI continue-on-error~~ | 2026-06-03 CI 加固完成 |

## 未决项

### Frontend / UI
1. **[FF-0007] authStore import admin 类型层耦合**: User explicitly excluded (P0-4)，保持不动。

### Infrastructure
2. **[P1-9] Proxy Issue**: `http://127.0.0.1:7897` connection resets during `git push`。（本地网络环境问题，非代码）
3. **[P1-10] CI Cron 完整性**: Lint/Type/E2E 已入 cron，`pytest --cov` 待下次 CI 运行观察。
4. **[P1-11] Repo Name Mismatch**: GitHub repo `FOURTEEN1416/fourteen.git` vs 项目名 `ai-girlfriend`。（可选改名，需用户决策）
5. **[P1-13] PAT auth in AGENTS.md**: 与 opencode 内置认证可能冗余。（低优先级）

## 新增观察（2026-08-24 接管体检）

6. **[OBS-1] 生产后端端口直暴露**: 服务器 uvicorn 4 worker 监听 `0.0.0.0:8000`，公网可绕过 nginx 直达后端（nginx 限速/安全头失效）。建议改为绑定 `127.0.0.1:8000`。需生产变更授权。
7. **[OBS-2] HTTPS 未启用**: 裸 IP 无法签发 certbot 证书，当前 HTTP 服务；`.env.production` 的 CORS 写的却是 https origin。绑定域名后按 nginx conf 注释走 certbot 即可。
