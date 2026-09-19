# P1 Backlog - 唯一的你

**Created**: 2026-06-03
**Last Updated**: 2026-09-19（前端审美升级+移动端优化批次收口：FE-0001 结案——StatusCenter 双列栅格+统一壳，宽屏留白实证收敛；其余未决项 FF-0007/P1-9~11/OBS-2/OBS-3/FE-ENV-1 不受影响）
**Previous**: 2026-09-19（前端修复批次收口：六十九号诊断五项建议已于七十号全落地 [3728a87]；新增遗留项 FE-0001 状态中心宽屏留白；其余未决项 FF-0007/P1-9~11/OBS-2/OBS-3 不受影响）
**Previous**: 2026-09-19（全仓扫描核对：未决项 FF-0007/P1-9~11/OBS-2/OBS-3 均不受 09-18/19 批次影响；09-18 nginx 整改落地 gzip/强缓存但 OBS-2 HTTPS 仍待域名）
**Previous**: 2026-09-17（三连修批次核对：本清单未决项 FF-0007/P1-9~11/OBS-2/OBS-3 均不受本轮影响，无新增未决项；`scripts/window_board.ps1` 仍缺待裁决）
**Previous**: 2026-09-15（复赛冲刺核对：pyrightconfig.json 尾逗号已修 `66c4e3e`）
**Previous**: 2026-08-24 (全面核查后重写 — 逐项实测验证)
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

6. ~~**[OBS-1] 生产后端端口直暴露**~~ ✅ **2026-08-24 已修复**：服务器 `/etc/systemd/system/ai-girlfriend.service` 的 `--host 0.0.0.0` → `127.0.0.1`（daemon-reload + restart）。实测：ss 显示仅 `127.0.0.1:8000` 监听；`/docs` 200；nginx 代理链路 200；微信桥接凭 flock 自动恢复登录。仓库模板 workers/keep-alive 已同步生产实况（4 / 30s）。
7. **[OBS-2] HTTPS 未启用**: 裸 IP 无法签发 certbot 证书，当前 HTTP 服务；`.env.production` 的 CORS 写的却是 https origin。绑定域名后按 nginx conf 注释走 certbot 即可。（用户决策：暂缓，证书+域名需费用）
8. **[OBS-3] systemd 服务以 root 运行**: 生产 service `User=root`，模板基线是 `www-data`。改运行用户涉及文件权限迁移，需停机窗口规划，暂记录待办。

## 新增未决项（2026-09-19 前端修复批次收口）

9. ~~**[FE-0001] StatusCenter 宽屏左侧留白**~~ ✅ **2026-09-19 已修复（前端审美批次）**：StatusCenter 改为双列栅格 `lg:grid-cols-[minmax(0,1fr)_400px]`（左列统计/情感/成就，右列记忆体系固定 400px），内容容器统一 `max-w-6xl` 壳；1920 宽屏实拍验证留白收敛（对比图 `docs/verification/2026-09-19-前端审美与移动端优化/{before,after}/d_status_1920.png`）。关联 LOG 六十九/七十/七十一。
10. **[FE-ENV-1] Lighthouse 本机不可用（环境约束，非产品项）**：本机 headless Chrome 无法提交帧 → NO_FCP。前端性能验证固定改用 CDP `Performance.getMetrics` + PerformanceObserver + 真机网络清单口径。关联 LOG 六十九「环境注记」。
