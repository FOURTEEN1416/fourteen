import { Routes, Route, Navigate, useParams, Outlet } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './api/queryClient'
import Sidebar from './components/layout/Sidebar'
import Breadcrumb from './components/layout/Breadcrumb'
import ToastContainer from './components/common/Toast'
import ErrorBoundary from './components/common/ErrorBoundary'
import AnimatedPage from './components/shared/AnimatedPage'
import ScrollProgress from './components/shared/ScrollProgress'
import { ParticleCanvas } from './components/common/ParticleCanvas'
import { CustomCursor } from './components/common/CustomCursor'
import SystemSettingsLayout from './pages/SystemSettingsLayout'
import CreateRole from './pages/CreateRole'
import RoleSettings from './pages/RoleSettings'
import StatusCenter from './pages/StatusCenter'
import StorylineEditor from './components/storyline/StorylineEditor'
import { AuthGuard, RoleGuard, ConsentGate, AuthInit } from './components/auth'
import { useAuthStore } from './store/authStore'

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

function StorylinePage() {
  const { roleId } = useParams<{ roleId: string }>()
  return <StorylineEditor characterId={roleId!} />
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
      <div className="flex h-[100dvh] overflow-hidden bg-dynamic bg-orbs">
        <ScrollProgress />
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
        <Route path="/psych" element={<Suspense fallback={<PageLoadingSkeleton />}><PsychProfilePage /></Suspense>} />
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
