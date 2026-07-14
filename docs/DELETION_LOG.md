# Code Deletion Log

## [2026-07-14] Routing Fix — Tombstone & Dead Code Cleanup

### Dead Endpoints Removed
- `api/routers/misc_routes.py` — Removed shadowed `/api/health` endpoint (3 lines). This was superseded by `api/health_routes.py` which is mounted first in `app_factory.py`. Having two `/api/health` routes caused confusion during debugging.

### Dead Imports Removed
- `api/app_factory.py` — Removed unused `Limiter`, `SlowAPIMiddleware`, `get_remote_address` imports from slowapi. Only `RateLimitExceeded` is used (for exception handler). The custom fallback rate limiter serves as the actual enforcement mechanism.
- `api/main_routes.py` — Removed unused `APIRouter` import and dead `router = APIRouter(tags=["main"])` variable (tombstone from when `health_router` was extracted to `health_routes.py`). The `router` variable was never imported by any file.

### Test Artifacts Deleted
- `tests/_test_invite.db` — Leftover SQLite test database artifact from invite code tests.

### Documentation Updated
- `docs/CODEMAPS/BACKEND.md` — Added `health_routes.py` and `runtime_config.py` to architecture tree; updated `misc_routes.py` endpoint count (10→9); updated `main_routes.py` description to reflect it no longer contains a router instance.

### Files NOT Removed (Intentionally Retained)
- `shisi/api/v2/health_routes.py` — Defines a `/health` route in the v2 API namespace. The entire `shisi/api/v2/` module (`v2_router`) is never mounted in `app_factory.py`. However, this is part of the shisi v2 API layer and may be activated in future integration work. Left intact to avoid breaking import chains.
- `shisi/memory/legacy/` — Despite the "legacy" name, these modules are actively imported by `shisi/application/memory_service.py` and covered by `tests/test_memory.py` + `tests/test_memory_pipeline.py`. Not dead code.
- `shisi/knowledge/legacy/` — Despite the "legacy" name, `rag_engine.py` is actively imported by `shisi/knowledge/legacy/__init__.py` and tested by `tests/test_rag_engine.py`. Not dead code.

### Impact
- Lines removed: ~15 (dead code + dead imports)
- No functional changes — all removed code was either shadowed or never executed
- Tests: 77/77 passed after cleanup (test_api_routes + test_production_hardening + test_ops_lifecycle + test_invite_codes + test_connection_lifecycle + test_p0_fixes + test_config_permissions)

---

## [2026-06-03] Dead Code Cleanup Session

### Unused Dependencies Removed
- echarts@^2.15.0 - No imports in any source file; manualChunks entry in vite.config.ts also cleaned up
- @testing-library/user-event@^14.6.1 - No imports in any source or test file

### Unused Files Deleted — Frontend

**Unused common/ components (6 files):**
- src/components/common/Card.tsx - Only used by ProactiveEnginePanel (also dead, transitively dead)
- src/components/common/EmptyState.tsx - Only used by MessageList (unused chat component)
- src/components/common/ProactiveEnginePanel.tsx - No external consumers
- src/components/common/SensitiveInput.tsx - No external consumers
- src/components/common/Skeleton.tsx - No external consumers (shared/Skeleton.tsx kept — used by pages)
- src/components/common/UrgencyBadge.tsx - No external consumers

**Unused shared/ duplicates (6 files):**
- src/components/shared/AnimatedNumber.tsx - No consumers; only in barrel export
- src/components/shared/DangerButton.tsx - No consumers; only in barrel export
- src/components/shared/ParallaxTilt.tsx - No consumers; only in barrel export
- src/components/shared/ProgressBar.tsx - No consumers; only in barrel export
- src/components/shared/StaggerContainer.tsx - No consumers; only in barrel export
- src/components/shared/Tabs.tsx - No consumers; only in barrel export
- src/components/shared/Tooltip.tsx - No consumers; only in barrel export

**Unused chat components (5 files):**
- src/components/chat/ChatInput.tsx - No external imports
- src/components/chat/MessageBubble.tsx - Only used by MessageList (also dead)
- src/components/chat/MessageList.tsx - No external imports
- src/components/chat/ProactiveToast.tsx - No external imports
- src/components/chat/TypingIndicator.tsx - No external imports

**Unused hooks (4 files):**
- src/hooks/useDashboardData.ts - No external imports
- src/hooks/useSmartPoll.ts - Only used by useDashboardData (also dead)
- src/hooks/useSSE.ts - No external imports
- src/hooks/useWebSocket.ts - No external imports

**Unused stores (2 files):**
- src/store/logStore.ts - No imports anywhere
- src/store/settingsStore.ts - No imports anywhere

**Unused types (1 file):**
- src/types/sticker.ts - No imports anywhere

**Unused page (1 file):**
- src/pages/AdminInvitesPage.tsx - Not imported in App.tsx or any other file

**Unused API module (1 file):**
- src/api/invites.ts - Only used by AdminInvitesPage (also dead)

### Barrel Files Cleaned Up
- src/components/common/index.ts — Removed 6 dead re-exports (Card, Skeleton, EmptyState, UrgencyBadge, ProactiveEnginePanel, SensitiveInput)
- src/components/shared/index.ts — Removed 9 dead re-exports (Select, ProgressBar, Skeleton, Toast, EmptyState, Badge, Tabs, Tooltip, AnimatedNumber, StaggerContainer, ParallaxTilt, DangerButton). Note: Select, Skeleton, EmptyState, Badge were RESTORED after discovering pages import them via barrel.

### Pre-existing Bugs Fixed
- src/api/client.ts:256,275 — Added missing imports of psychProfile, psychSnapshots, psychReset, psychMentalHealth, psychLiwc from ./system (caused TS2304 errors)

### Impact
- Files deleted: 25
- Dependencies removed: 2
- Lines of code removed: ~3,800 (estimated)
- Bundle size reduction: recharts removed from manualChunks (~45 KB gzip savings potential)
- TypeScript errors fixed: 10 (pre-existing psych* import bugs)

### Testing
- 	sc --noEmit — 0 errors
- ite build — passed (2186 modules, 1.20s)
- itest run — 4/4 frontend tests passed
- pytest -x -q — 625/625 Python tests passed (1 skipped)

### Notes
- common/EmptyState.tsx and common/Skeleton.tsx were kept restored through shared/ because pages import them via barrel
- common/Badge.tsx kept (used by KnowledgePreview, StorylineEditor, StorylineIndicator)
- shared/Select, Skeleton, EmptyState, Badge RESTORED after initial deletion — pages use them via barrel imports
- Legacy backend pi/_*_routes.py files NOT removed — they coexist with pi/routers/ in app_factory.py; only a full endpoint diff can confirm redundancy
- eact-window and eact-virtualized-auto-sizer kept — used via equire() in MessageList.tsx even though MessageList is unused
