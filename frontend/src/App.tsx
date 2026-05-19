import { Routes, Route } from 'react-router-dom'
import Sidebar from './components/layout/Sidebar'
import MobileNav from './components/layout/MobileNav'
import ToastContainer from './components/common/Toast'
import ErrorBoundary from './components/common/ErrorBoundary'
import DashboardPage from './pages/DashboardPage'
import ChatPage from './pages/ChatPage'
import PersonaPage from './pages/PersonaPage'
import MemoryPage from './pages/MemoryPage'
import AdminPage from './pages/AdminPage'
import TrainingPage from './pages/TrainingPage'
import ChannelsPage from './pages/ChannelsPage'
import SettingsPage from './pages/SettingsPage'
import LogsPage from './pages/LogsPage'

export default function App() {
  return (
    <ErrorBoundary>
      <div className="flex h-screen bg-slate-950 overflow-hidden">
        <Sidebar />
        <main className="flex-1 flex flex-col min-w-0 pb-16 lg:pb-0">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/persona" element={<PersonaPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/training" element={<TrainingPage />} />
            <Route path="/channels" element={<ChannelsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/admin" element={<AdminPage />} />
          </Routes>
        </main>
        <MobileNav />
        <ToastContainer />
      </div>
    </ErrorBoundary>
  )
}
