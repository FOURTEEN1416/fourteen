/**
 * 主动消息引擎 · 管理员运维页（2026-11-07 P5）。
 *
 * 用户裁决：管理员「在基础的每一个用户的体验上加上网站运维」——本页承载
 * 全局**出厂默认值**（新用户×新角色首次物化时的初始值）与引擎运维面
 * （LLM 决策 / 骰子闸 / 话题引擎 / 账本回放）。普通用户的角色页不再出现
 * 这些全局块；路由层有 admin 门，组件内保留既有 isAdmin 二次防线。
 */
import { Activity } from 'lucide-react'
import { ProactiveOpsTab } from '../components/admin/RoleSettingsTabs'

export default function SettingsProactivePage() {
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-macaron-blue/30 bg-macaron-blue-light/30 px-4 py-3">
        <Activity className="h-4 w-4 shrink-0 text-macaron-blue-deep mt-0.5" />
        <div className="text-xs text-gray-500 leading-relaxed">
          <p className="font-medium text-gray-700">出厂默认值与引擎运维（仅管理员）</p>
          <p className="mt-0.5">
            这里的值是「出厂默认」：每个用户×角色的设置在首次打开时按它初始化；
            已物化的用户不受后续修改影响。看某个用户的具体设置请让其本人在角色设置页查看。
          </p>
        </div>
      </div>
      <ProactiveOpsTab />
    </div>
  )
}
