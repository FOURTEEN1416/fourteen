import { Outlet } from 'react-router-dom'

export default function SystemSettingsLayout() {
  return (
    <div className="flex flex-col h-full p-5">
      <h1 className="text-base font-bold text-gray-800 mb-4">系统设置</h1>
      <div className="flex-1 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  )
}
