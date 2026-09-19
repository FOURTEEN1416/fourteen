import { Outlet } from 'react-router-dom'

/** 系统设置外壳：标题由面包屑承担（2026-09-19 审美批次：去除与面包屑重复的页内 h1），
 *  各子页在统一容器内自定义栅格。 */
export default function SystemSettingsLayout() {
  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto w-full max-w-6xl">
          <Outlet />
        </div>
      </div>
    </div>
  )
}
