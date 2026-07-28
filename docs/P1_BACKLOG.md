# P1 Backlog - 唯一的你

**Created**: 2026-06-03
**Last Updated**: 2026-07-28 (双模式合并 + 架构债清理)
**Status**: Pending (内测后按需清理)

> ✅ 已解决：P1-3 (orphan pages), P1-12 (AGENTS.md memory), P1-16 (FF-020 CI blocking)
> ✅ 2026-07-28 架构升级：main.py 双模式合并（35.9KB→22.7KB / -297 行），`_init_mixin` 成为唯一初始化真相源（10 阶段），修复双调度器并行 bug。详见 CODE_GRAPH.md §13。

## Frontend / UI
1. **[FF-0007] authStore import admin 类型层耦合**: User explicitly excluded (P0-4).
2. **[P1-1] Settings 路由标签冲突**: sidebar="安全" vs tab="状态".
3. **[P1-2] Storyline 页面数据缺失**: Backend `/api/characters/{id}/storyline` returns 404/Empty.
4. ~~**[P1-3] 21 Orphan Pages**: Pages exist in code but have no routes or navigation entry.~~ ✅ 2026-06-02 已清零（全部注册路由）
5. **[P1-4] StatusCenter mock data**: Status center UI is still using placeholder data.
6. **[P1-5] WeChatPage placeholder**: WeChat integration UI is placeholder only.

## Backend / API
7. **[P1-6] shisi/* 旧路径清理**: Front-end `system.ts` still references old API paths.
8. **[P1-7] TTS v2 认证**: MiMo/Baidu TTS missing `X-API-Key` check.
9. **[P1-8] LLM 流式优化**: Current `process_message_stream` is a wrapper around synchronous `process_message` + chunking. Needs true async LLM streaming.
10. **[P1-9] Proxy Issue**: `http://127.0.0.1:7897` connection resets during `git push`.

## Infrastructure / Quality
11. **[P1-10] CI Cron 完整性**: Cron jobs now include Lint/Type/E2E, but need to verify `pytest --cov` still works perfectly.
12. **[P1-11] Repo Name Mismatch**: GitHub repo is `FOURTEEN1416/fourteen.git`, project is `ai-girlfriend`. (Optional rename)
13. ~~**[P1-12] `AGENTS.md` 修正**: "15 pages" is correct now, but some sections mention "35 pages". Update memory.~~ ✅ 2026-06-03 HANDOFF.md 已更新
14. **[P1-13] `AGENTS.md` 认证**: PAT auth in `AGENTS.md` might be redundant with opencode's auth.

## Security / Compliance
15. **[P1-14] `deploy/.env.production`**: `API_KEY=CHANGE_ME...` placeholder needs rotation.
16. **[P1-15] Hardcoded Secret**: `API_SECRET` in `AGENTS.md`/Memory (CDL Forger).
17. ~~**[P1-16] FF-020 CI Check**: Ruff and MyPy currently `continue-on-error: true`. Needs to be hard-fail.~~ ✅ 2026-06-03 CI 加固已从 blocking 移除 continue-on-error
