# Code Deletion Log

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
