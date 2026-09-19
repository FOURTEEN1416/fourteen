import { Routes, Route, Navigate, useParams, Outlet } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './api/queryClient'
import Sidebar from './components/layout/Sidebar'
import Breadcrumb from './components/layout/Breadcrumb'
import ToastContainer from './components/common/Toast'
import ErrorBoundary from './components/common/ErrorBoundary'
import AnimatedPage from './components/shared/AnimatedPage'
import { ParticleCanvas } from './components/common/ParticleCanvas'
import { CustomCursor } from './components/common/CustomCursor'
import SystemSettingsLayout from './pages/SystemSettingsLayout'
import { AuthGuard, RoleGuard, ConsentGate, AuthInit } from './components/auth'
import { useAuthStore } from './store/authStore'
import { useUnifiedCharacter } from './hooks/useQueries'
import { sanitizeCharacterName } from './utils/character'

const LoginPage = lazy(() => import('./pages/LoginPage'))
const IntroPage = lazy(() => import('./pages/IntroPage'))
const PsychProfilePage = lazy(() => import('./pages/PsychProfilePage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))
const WeChatPage = lazy(() => import('./pages/WeChatPage'))
const AdminUsersPage = lazy(() => import('./pages/AdminUsersPage'))
const AdminProvidersPage = lazy(() => import('./pages/AdminProvidersPage'))

const SettingsLLM = lazy(() => import('./pages/SettingsLLM'))
const SettingsVoice = lazy(() => import('./pages/SettingsVoice'))
const SettingsSecurity = lazy(() => import('./pages/SettingsSecurity'))
const ToolsDashboard = lazy(() => import('./pages/ToolsDashboard'))
const SettingsLogs = lazy(() => import('./pages/SettingsLogs'))
const RolesPage = lazy(() => import('./pages/RolesPage'))

// ScrollProgress 是 eager 图里唯一拉 framer-motion 的组件；改 lazy 后 motion chunk
// （≈45KB gz）退出首屏 modulepreload 关键路径（公开页 /intro 不再为其付费）。
// 四个重型页面同批拆出 index chunk，随各自动画路由边界惰性加载。
const ScrollProgress = lazy(() => import('./components/shared/ScrollProgress'))
const CreateRole = lazy(() => import('./pages/CreateRole'))
const RoleSettings = lazy(() => import('./pages/RoleSettings'))
const StatusCenter = lazy(() => import('./pages/StatusCenter'))
const StorylineEditor = lazy(() => import('./components/storyline/StorylineEditor'))

function PageLoadingSkeleton() {
  return (
    <div className="flex-1 p-6 space-y-6 animate-pulse">
      <div className="h-8 w-48 bg-gray-200/60 rounded-lg" />
      <div className="grid grid-cols-3 gap-4">
        <div className="h-24 bg-gray-200/60 rounded-xl" />
        <div className="h-24 bg-gray-200/60 rounded-xl" />
        <div className="h-24 bg-gray-200/60 rounded-xl" />
      </div>
      <div className="h-64 bg-gray-200/60 rounded-xl" />
    </div>
  )
}

/** 剧情线独立页：与角色设置同构的统一外壳（角色头卡 + 表单卡），编辑器组件保持可嵌入复用 */
function StorylinePage() {
  const { roleId } = useParams<{ roleId: string }>()
  const characterId = roleId ? decodeURIComponent(roleId) : ''
  const { data: character } = useUnifiedCharacter(characterId)

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6">
        {character && (
          <div className="bg-white/70 backdrop-blur-sm border border-gray-200/60 rounded-2xl p-5 mb-5">
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-primary-400 to-purple-500 flex items-center justify-center text-white text-xl font-bold shadow-sm shrink-0">
                {sanitizeCharacterName(character.name)[0]}
              </div>
              <div className="min-w-0 flex-1">
                <h1 className="text-lg font-bold text-gray-800">{sanitizeCharacterName(character.name)}</h1>
                <p className="text-sm text-gray-500 truncate">{character.description}</p>
              </div>
            </div>
          </div>
        )}
        <div className="bg-white/70 backdrop-blur-sm border border-gray-200/60 rounded-2xl p-5">
          <StorylineEditor characterId={characterId} standalone />
        </div>
      </div>
    </div>
  )
}

/** 根路径重定向：已登录 → /wechat（保持原跳转），未登录 → /intro（公开门面页，SP-11） */
function RootRedirect() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  return <Navigate to={isAuthenticated ? '/wechat' : '/intro'} replace />
}

function AnimatedSuspense({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<PageLoadingSkeleton />}>
      <AnimatedPage>
        {children}
      </AnimatedPage>
    </Suspense>
  )
}

/** 受保护的管理控制台布局（含侧边栏+顶栏+AuthGuard） */
function ProtectedLayout() {
  return (
    <AuthGuard>
      <div className="flex h-[100dvh] overflow-hidden">
        <Suspense fallback={null}>
          <ScrollProgress />
        </Suspense>
        <ParticleCanvas />
        <Sidebar />
        <main className="flex-1 flex flex-col min-w-0 pt-5 pb-6 overflow-y-auto">
          <Breadcrumb />
          <Outlet />
        </main>
        <ToastContainer />
        <CustomCursor />
      </div>
    </AuthGuard>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
    <ErrorBoundary>
    <AuthInit>
      <ConsentGate />
      <Routes>
        {/* ─── 公开路由：产品介绍 + 登录页 + 根路径分流 ─── */}
        <Route path="/intro" element={<Suspense fallback={<PageLoadingSkeleton />}><IntroPage /></Suspense>} />
        <Route path="/login" element={<Suspense fallback={<PageLoadingSkeleton />}><LoginPage /></Suspense>} />
        <Route path="/" element={<RootRedirect />} />

        {/* ─── 受保护路由：管理控制台 ─── */}
        <Route element={<ProtectedLayout />}>
          {/* 连接 */}
          <Route path="/wechat" element={<AnimatedSuspense><WeChatPage /></AnimatedSuspense>} />

          {/* 角色 */}
          <Route path="/roles" element={<AnimatedSuspense><RolesPage /></AnimatedSuspense>} />
          <Route path="/roles/create" element={<AnimatedSuspense><CreateRole /></AnimatedSuspense>} />
          <Route path="/roles/:roleId/settings" element={<AnimatedSuspense><RoleSettings /></AnimatedSuspense>} />
          <Route path="/roles/:roleId/settings/:tab" element={<AnimatedSuspense><RoleSettings /></AnimatedSuspense>} />
          <Route path="/roles/:roleId/status" element={<AnimatedSuspense><StatusCenter /></AnimatedSuspense>} />
          <Route path="/roles/:roleId/storyline" element={<AnimatedSuspense><StorylinePage /></AnimatedSuspense>} />
          {/* 心理画像需登录：2026-09-19 审美批次并入控制台外壳（此前独立路由无侧栏/面包屑，
              未登录时由 AuthGuard 拦截，请求不会发出） */}
          <Route path="/psych" element={<AnimatedSuspense><PsychProfilePage /></AnimatedSuspense>} />

          {/* 系统设置 */}
          <Route path="/settings" element={<SystemSettingsLayout />}>
            <Route index element={<Navigate to="/settings/llm" replace />} />
            <Route path="llm" element={<AnimatedSuspense><SettingsLLM /></AnimatedSuspense>} />
            <Route path="voice" element={<AnimatedSuspense><SettingsVoice /></AnimatedSuspense>} />
            <Route path="tools" element={<AnimatedSuspense><ToolsDashboard /></AnimatedSuspense>} />
            <Route path="security" element={<AnimatedSuspense><SettingsSecurity /></AnimatedSuspense>} />
            <Route path="logs" element={<AnimatedSuspense><SettingsLogs /></AnimatedSuspense>} />
          </Route>

          {/* 管理后台 */}
          <Route element={<RoleGuard roles={['admin'] as const} />}>
            <Route path="/admin" element={<Navigate to="/admin/users" replace />} />
            <Route path="/admin/users" element={<AnimatedSuspense><AdminUsersPage /></AnimatedSuspense>} />
            <Route path="/admin/providers" element={<AnimatedSuspense><AdminProvidersPage /></AnimatedSuspense>} />
          </Route>
        </Route>

        {/* 兜底：未匹配的路径 */}
        <Route path="*" element={<Suspense fallback={<PageLoadingSkeleton />}><NotFoundPage /></Suspense>} />
      </Routes>
    </AuthInit>
    </ErrorBoundary>
    </QueryClientProvider>
  )
}
