import { Routes, Route, Navigate, useParams, Outlet } from 'react-router-dom'
import { lazy, Suspense, useEffect } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './api/queryClient'
import Sidebar from './components/layout/Sidebar'
import Breadcrumb from './components/layout/Breadcrumb'
import MobileNav from './components/layout/MobileNav'
import ToastContainer from './components/common/Toast'
import ErrorBoundary from './components/common/ErrorBoundary'
import AnimatedPage from './components/shared/AnimatedPage'
import ScrollProgress from './components/shared/ScrollProgress'
import UserWorkspace from './pages/UserWorkspace'
import SystemSettingsLayout from './pages/SystemSettingsLayout'
import CreateRole from './pages/CreateRole'
import RoleSettings from './pages/RoleSettings'
import StatusCenter from './pages/StatusCenter'
import StorylineEditor from './components/storyline/StorylineEditor'
import AuthGuard from './components/auth/AuthGuard'
import { useAuth } from './hooks/useAuth'

const LoginPage = lazy(() => import('./pages/LoginPage'))
const WeChatPage = lazy(() => import('./pages/WeChatPage'))
const UsersPage = lazy(() => import('./pages/UsersPage'))

const SettingsLLM = lazy(() => import('./pages/SettingsLLM'))
const SettingsVoice = lazy(() => import('./pages/SettingsVoice'))
const SettingsSecurity = lazy(() => import('./pages/SettingsSecurity'))
const ToolsDashboard = lazy(() => import('./pages/ToolsDashboard'))
const SettingsLogs = lazy(() => import('./pages/SettingsLogs'))

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

function AnimatedSuspense({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<PageLoadingSkeleton />}>
      <AnimatedPage>
        {children}
      </AnimatedPage>
    </Suspense>
  )
}

/** 认证初始化：App 启动时 init() 一次 */
function AuthInit({ children }: { children: React.ReactNode }) {
  const { init } = useAuth()
  useEffect(() => { init() }, [init])
  return <>{children}</>
}

/** 受保护的管理控制台布局（含侧边栏+顶栏+AuthGuard） */
function ProtectedLayout() {
  return (
    <AuthGuard>
      <div className="flex h-screen overflow-hidden bg-dynamic bg-orbs">
        <ScrollProgress />
        <Sidebar />
        <main className="flex-1 flex flex-col min-w-0 pb-16 lg:pb-0 overflow-y-auto">
          <Breadcrumb />
          <Outlet />
        </main>
        <MobileNav />
        <ToastContainer />
      </div>
    </AuthGuard>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
    <ErrorBoundary>
    <AuthInit>
      <Routes>
        {/* ═══ 公开路由：登录页（无侧边栏） ═══ */}
        <Route path="/login" element={<Suspense fallback={<PageLoadingSkeleton />}><LoginPage /></Suspense>} />

        {/* ═══ 受保护路由：管理控制台 ═══ */}
        <Route element={<ProtectedLayout />}>
          <Route path="/" element={<Navigate to="/wechat" replace />} />

          {/* Global Level */}
          <Route path="/wechat" element={<AnimatedSuspense><WeChatPage /></AnimatedSuspense>} />
          <Route path="/users" element={<AnimatedSuspense><UsersPage /></AnimatedSuspense>} />

          {/* User Level */}
          <Route path="/users/:userId" element={<UserWorkspace />}>
            <Route path="roles/create" element={<AnimatedPage><CreateRole /></AnimatedPage>} />
            <Route path="roles/:roleId/settings" element={<AnimatedPage><RoleSettings /></AnimatedPage>} />
            <Route path="roles/:roleId/settings/:tab" element={<AnimatedPage><RoleSettings /></AnimatedPage>} />
            <Route path="roles/:roleId/status" element={<AnimatedPage><StatusCenter /></AnimatedPage>} />
            <Route path="roles/:roleId/storyline" element={<AnimatedPage><StorylinePage /></AnimatedPage>} />
          </Route>

          {/* System Settings */}
          <Route path="/settings" element={<Navigate to="/settings/llm" replace />} />
          <Route path="/settings" element={<SystemSettingsLayout />}>
            <Route path="llm" element={<AnimatedSuspense><SettingsLLM /></AnimatedSuspense>} />
            <Route path="voice" element={<AnimatedSuspense><SettingsVoice /></AnimatedSuspense>} />
            <Route path="tools" element={<AnimatedSuspense><ToolsDashboard /></AnimatedSuspense>} />
            <Route path="security" element={<AnimatedSuspense><SettingsSecurity /></AnimatedSuspense>} />
            <Route path="logs" element={<AnimatedSuspense><SettingsLogs /></AnimatedSuspense>} />
          </Route>
        </Route>

        {/* 兜底 */}
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </AuthInit>
    </ErrorBoundary>
    </QueryClientProvider>
  )
}
