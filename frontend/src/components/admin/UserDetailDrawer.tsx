import { useEffect, useState } from 'react'
import { RefreshCw, Unplug, Wifi, WifiOff, X } from 'lucide-react'
import ConfirmDialog from '../shared/ConfirmDialog'
import { RoleBadge, ActiveBadge } from './UserBadges'
import {
  adminForceDisconnect, adminGetUser, adminListChannels,
  type AdminUserDetail, type AdminChannelInfo,
} from '../../api/admin'
import { useErrorStore } from '../../store/errorStore'

/**
 * 用户详情下钻抽屉（NEW-11，2026-10-29）——「按人查问题」的第一入口。
 *
 * 此前后端早已具备单用户详情（GET /api/admin/users/{id}）与全局通道摘要
 * （GET /api/admin/wechat/channels）+ 强制断开端点，前端零消费者（纯死接口）。
 * 本组件接线：账号字段 + 该用户的微信通道状态（slot/bot_id/连接徽章）+
 * 强制断开（带确认弹窗，操作落操作员审计）。
 */
export default function UserDetailDrawer({
  user,
  onClose,
}: {
  user: { id: number; email: string; username: string; display_name: string; role: string; is_active: boolean }
  onClose: () => void
}) {
  const addToast = useErrorStore(s => s.addToast)
  const [detail, setDetail] = useState<AdminUserDetail | null>(null)
  const [channels, setChannels] = useState<AdminChannelInfo[]>([])
  const [online, setOnline] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [disconnecting, setDisconnecting] = useState<AdminChannelInfo | null>(null)

  const fetchAll = async () => {
    setLoading(true)
    setError('')
    try {
      const [d, ch] = await Promise.all([
        adminGetUser(user.id),
        adminListChannels().catch(() => ({ channels: [] as AdminChannelInfo[], online: 0, max_global: 0 })),
      ])
      setDetail(d)
      setChannels(ch.channels.filter(c => c.user_id === user.id))
      setOnline(ch.online)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void fetchAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 打开/切换用户时拉取
  }, [user.id])

  const handleDisconnect = async () => {
    if (!disconnecting) return
    try {
      const res = await adminForceDisconnect(disconnecting.user_id, disconnecting.slot)
      if (res.ok || res.status === 'disconnected') {
        addToast({ type: 'success', message: `已断开 user_id=${disconnecting.user_id} slot=${disconnecting.slot}` })
      } else {
        addToast({ type: 'warning', message: '断开指令已受理，但后端未确认成功，请刷新核实' })
      }
      setDisconnecting(null)
      await fetchAll()
    } catch (e) {
      addToast({ type: 'error', message: e instanceof Error ? e.message : '断开失败' })
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-label="用户详情">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-full max-w-md h-full bg-white shadow-xl overflow-y-auto">
        <div className="sticky top-0 bg-white/90 backdrop-blur px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h2 className="text-base font-bold text-gray-800">
            用户详情 <span className="text-xs font-normal text-gray-400 font-mono">#{user.id}</span>
          </h2>
          <div className="flex items-center gap-1">
            <button
              onClick={() => void fetchAll()}
              className="p-2 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              title="刷新"
              aria-label="刷新"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-2 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              aria-label="关闭"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="px-5 py-4 space-y-5">
          {error && (
            <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{error}</p>
          )}

          {/* ── 账号 ── */}
          <section className="space-y-2">
            <h3 className="text-xs font-semibold text-gray-400">账号</h3>
            {detail ? (
              <div className="rounded-xl bg-gray-50/70 px-4 py-3 space-y-1.5 text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-gray-800">{detail.display_name || detail.username}</span>
                  <RoleBadge role={detail.role} />
                  <ActiveBadge active={detail.is_active} />
                </div>
                <p className="text-xs text-gray-500">{detail.email}</p>
                <p className="text-[11px] text-gray-400">
                  用户名 {detail.username} · 注册 {detail.created_at ? detail.created_at.slice(0, 10) : '—'}
                  {detail.last_login_at ? ` · 最近登录 ${detail.last_login_at.slice(0, 16).replace('T', ' ')}` : ' · 从未登录'}
                </p>
              </div>
            ) : loading ? (
              <div className="h-16 rounded-xl bg-gray-50/70 animate-pulse" />
            ) : null}
          </section>

          {/* ── 微信通道 ── */}
          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-gray-400">微信通道</h3>
              <span className="text-[10px] text-gray-300">全站在线通道 {online}</span>
            </div>
            {channels.length === 0 ? (
              <p className="rounded-xl bg-gray-50/70 px-4 py-3 text-xs text-gray-400">
                {loading ? '加载中…' : '该用户没有已登记的微信通道（未扫码绑定或通道已清除）'}
              </p>
            ) : (
              <div className="space-y-2">
                {channels.map(c => (
                  <div key={`${c.user_id}-${c.slot}`} className="rounded-xl bg-gray-50/70 px-4 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        {c.connected ? (
                          <Wifi className="w-4 h-4 shrink-0 text-green-500" aria-label="已连接" />
                        ) : (
                          <WifiOff className="w-4 h-4 shrink-0 text-gray-300" aria-label="未连接" />
                        )}
                        <span className="text-xs text-gray-700">
                          slot {c.slot}
                          {c.bot_id ? <span className="ml-1 font-mono text-[10px] text-gray-400">{c.bot_id}</span> : null}
                          {c.source === 'disk' ? <span className="ml-1 text-[10px] text-gray-300">（仅磁盘凭证）</span> : null}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${c.connected ? 'bg-green-100 text-green-600' : 'bg-gray-100 text-gray-400'}`}>
                          {c.connected ? '在线' : (c.status || 'idle')}
                        </span>
                        <button
                          onClick={() => setDisconnecting(c)}
                          className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] text-rose-500 hover:bg-rose-50 transition-colors"
                          title="强制断开该通道"
                        >
                          <Unplug className="w-3 h-3" />
                          断开
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="text-[10px] text-gray-300 leading-relaxed">
              强制断开会让该通道立即下线，需用户重新扫码；操作会记入操作员审计。
            </p>
          </section>
        </div>

        <ConfirmDialog
          open={!!disconnecting}
          title="确认强制断开通道"
          message={
            disconnecting
              ? `将断开用户 #${disconnecting.user_id} 的通道 slot=${disconnecting.slot}${disconnecting.bot_id ? `（${disconnecting.bot_id}）` : ''}。该用户的微信对话会立即中断，直到重新扫码绑定。确定继续？`
              : ''
          }
          confirmText="强制断开"
          cancelText="取消"
          variant="danger"
          onConfirm={() => void handleDisconnect()}
          onCancel={() => setDisconnecting(null)}
        />
      </div>
    </div>
  )
}
