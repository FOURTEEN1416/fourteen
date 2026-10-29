import { useEffect } from 'react'
import { Globe, X } from 'lucide-react'
import { getGlobalView, setGlobalView } from '../../api/admin'
import { useAuthStore } from '../../store/authStore'
import { useGlobalViewStore } from '../../store/globalViewStore'
import { useErrorStore } from '../../store/errorStore'

/**
 * 全局运维视图常驻横幅（V2-1，GitLab Admin Mode 范式）。
 *
 * 开启期间顶栏常驻提示「你看到的是所有用户的数据」，并提供一键关闭；
 * 关闭走服务端 setGlobalView（落 operator_prefs + 审计）后写回 store。
 * 非 admin 恒不渲染。（FF-0007：API 调用在本组件内完成，store 只存状态）
 */
export default function GlobalViewBanner() {
  const isAdmin = useAuthStore(s => s.user?.role === 'admin')
  const { enabled, loaded, setEnabled } = useGlobalViewStore()
  const addToast = useErrorStore(s => s.addToast)

  useEffect(() => {
    if (isAdmin && !loaded) {
      void getGlobalView()
        .then(res => setEnabled(!!res.enabled))
        .catch(() => setEnabled(false))
    }
  }, [isAdmin, loaded, setEnabled])

  if (!isAdmin || !enabled) return null

  return (
    <div
      data-testid="global-view-banner"
      className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-amber-300/60 bg-amber-50 px-4 py-2.5 text-sm"
    >
      <Globe className="h-4 w-4 shrink-0 text-amber-600" />
      <span className="text-amber-800">
        <span className="font-semibold">全局运维视图已开启</span>
        <span className="ml-1 text-amber-700">
          —— 记忆 / 日记等数据面显示的是【所有用户】的数据，且该视角已留痕审计。
        </span>
      </span>
      <button
        onClick={() => {
          void setGlobalView(false)
            .then(res => {
              setEnabled(!!res.enabled)
              addToast({ type: 'success', message: '已关闭全局运维视图，数据面恢复本人范围' })
            })
            .catch(() => addToast({ type: 'error', message: '关闭失败，请重试' }))
        }}
        className="ml-auto inline-flex items-center gap-1 rounded-lg border border-amber-300 bg-white/70 px-2.5 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100 transition-colors"
      >
        <X className="h-3.5 w-3.5" />
        关闭全局视图
      </button>
    </div>
  )
}
