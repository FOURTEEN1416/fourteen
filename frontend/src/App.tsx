import { Routes, Route, useNavigate } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './api/queryClient'
import Sidebar from './components/layout/Sidebar'
import MobileNav from './components/layout/MobileNav'
import ToastContainer from './components/common/Toast'
import ErrorBoundary from './components/common/ErrorBoundary'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ChatPage = lazy(() => import('./pages/ChatPage'))
const PersonaPage = lazy(() => import('./pages/PersonaPage'))
const MemoryPage = lazy(() => import('./pages/MemoryPage'))
const AdminPage = lazy(() => import('./pages/AdminPage'))
const TrainingPage = lazy(() => import('./pages/TrainingPage'))
const CloneDataPage = lazy(() => import('./pages/CloneDataPage'))
const ChannelsPage = lazy(() => import('./pages/ChannelsPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const LogsPage = lazy(() => import('./pages/LogsPage'))
const CharactersPage = lazy(() => import('./pages/CharactersPage'))
const MonitorPage = lazy(() => import('./pages/MonitorPage'))
const StatsPage = lazy(() => import('./pages/StatsPage'))
const PersonaEditorPage = lazy(() => import('./pages/PersonaEditorPage'))
const StickersPage = lazy(() => import('./pages/StickersPage'))
const PsychProfilePage = lazy(() => import('./pages/PsychProfilePage'))
const SafetyPage = lazy(() => import('./pages/SafetyPage'))
const KnowledgeBasePage = lazy(() => import('./pages/KnowledgeBasePage'))
const ExtensionsPage = lazy(() => import('./pages/ExtensionsPage'))
const FavoritesPage = lazy(() => import('./pages/FavoritesPage'))
const UsersPage = lazy(() => import('./pages/UsersPage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))

function PageLoadingSkeleton() {
  return (
    <div className="flex-1 p-6 space-y-6 animate-pulse">
      <div className="h-8 w-48 bg-gray-200 rounded" />
      <div className="grid grid-cols-3 gap-4">
        <div className="h-24 bg-gray-200 rounded-lg" />
        <div className="h-24 bg-gray-200 rounded-lg" />
        <div className="h-24 bg-gray-200 rounded-lg" />
      </div>
      <div className="h-64 bg-gray-200 rounded-lg" />
      <div className="flex gap-4">
        <div className="h-40 flex-1 bg-gray-200 rounded-lg" />
        <div className="h-40 flex-1 bg-gray-200 rounded-lg" />
      </div>
    </div>
  )
}

export default function App() {
  const navigate = useNavigate()

  return (
    <QueryClientProvider client={queryClient}>
    <ErrorBoundary>
      <div className="flex h-screen bg-gray-50 overflow-hidden">
        <Sidebar />
        <main className="flex-1 flex flex-col min-w-0 pb-16 lg:pb-0">
          <Suspense fallback={<PageLoadingSkeleton />}>
            <div className="animate-fade-in">
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/persona" element={<PersonaPage />} />
                <Route path="/memory" element={<MemoryPage />} />
                <Route path="/training" element={<TrainingPage />} />
                <Route path="/clone-data" element={<CloneDataPage />} />
                <Route path="/channels" element={<ChannelsPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/logs" element={<LogsPage />} />
                <Route path="/admin" element={<AdminPage />} />
                <Route path="/characters" element={<CharactersPage />} />
                <Route path="/monitor" element={<MonitorPage />} />
                <Route path="/stats" element={<StatsPage />} />
                <Route path="/persona-editor" element={<PersonaEditorPage />} />
                <Route path="/stickers" element={<StickersPage />} />
                <Route path="/psych" element={<PsychProfilePage />} />
                <Route path="/safety" element={<SafetyPage />} />
                <Route path="/knowledge" element={<KnowledgeBasePage />} />
                <Route path="/extensions" element={<ExtensionsPage />} />
                <Route path="/favorites" element={<FavoritesPage />} />
                <Route path="/users" element={<UsersPage />} />
                <Route path="*" element={<NotFoundPage />} />
              </Routes>
            </div>
          </Suspense>
        </main>
        <MobileNav />
        <ToastContainer />
      </div>
    </ErrorBoundary>
    </QueryClientProvider>
  )
}
