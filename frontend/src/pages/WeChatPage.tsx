import { useState, useCallback, useMemo, useEffect, useRef } from 'react'
import { AnimatedPage } from '../components/shared'
import { useWechatStatus } from '../hooks/useQueries'
import { Search, Wifi, WifiOff, Trash2, RefreshCw, X, QrCode, Clock, MessageSquare, AlertTriangle, CheckCircle2, Smartphone } from 'lucide-react'
import type { SavedConnection } from '../types/framework'
import {
  wechatCreateConnection,
  wechatDeleteConnection,
  bindWechat,
} from '../api/wechat'
import { wechatQrCode, wechatConnectionStatus, wechatConnect } from '../api/system'
import { getAccessToken } from '../store/authStore'

// ── Types ──

type QrStatus = 'loading' | 'waiting' | 'scanned' | 'connected' | 'expired' | 'error'

// ── Live Status Banner ──

function LiveStatusBanner() {
  const { data: status, isLoading, isError } = useWechatStatus()

  if (isLoading) {
    return (
      <div className="glass-card mb-5 rounded-xl p-4 animate-pulse flex items-center gap-3">
        <div className="h-3 w-3 rounded-full bg-gray-200" />
        <div className="h-4 w-32 bg-gray-200/60 rounded" />
      </div>
    )
  }

  if (isError || !status) {
    return (
      <div className="mb-5 flex items-center gap-3 rounded-xl bg-red-50 px-4 py-3 text-sm">
        <AlertTriangle className="h-4 w-4 text-red-400" />
        <span className="text-red-600">无法获取微信连接状态</span>
        <span className="text-xs text-red-400">后端可能未运行</span>
      </div>
    )
  }

  const seconds = status.uptime_seconds ?? 0
  const uptime = seconds > 0
    ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
    : '--'

  return (
    <div className="glass-card mb-5 rounded-xl p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Left: status */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex h-2.5 w-2.5 rounded-full ${
                status.connected ? 'bg-green-400' : 'bg-red-400'
              }`}
            />
            <span className="text-sm font-semibold text-gray-700">
              微信桥接 {status.connected ? '已连接' : '已断开'}
            </span>
          </div>
          <div className="flex items-center gap-3 text-xs text-gray-400">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              运行 {uptime}
            </span>
            <span className="flex items-center gap-1">
              <MessageSquare className="h-3 w-3" />
              今日 {status.messages_today ?? 0} 条消息
            </span>
          </div>
        </div>

        {/* Right: QR code / reconnect */}
        <div className="flex items-center gap-2">
          {status.qr_code && (
            <button
              onClick={() => window.open(status.qr_code!, '_blank')}
              className="flex items-center gap-1 rounded-lg bg-gray-100 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-200 transition-colors"
              title="查看二维码"
            >
              <QrCode className="h-3.5 w-3.5" />
              二维码
            </button>
          )}
          {!status.connected && (
            <span className="text-xs text-red-400">
              重连 {status.reconnect_attempts ?? 0} 次
            </span>
          )}
        </div>
      </div>

      {status.last_activity && (
        <p className="mt-2 text-[10px] text-gray-300">
          最后活动: {new Date(status.last_activity).toLocaleString('zh-CN')}
        </p>
      )}
    </div>
  )
}

// ── Stats row ──

function StatsBar({ connections }: { connections: SavedConnection[] }) {
  const total = connections.length
  const online = connections.filter((c) => c.isOnline).length
  const offline = total - online

  return (
    <div className="flex gap-4 mb-5">
      {[
        { label: '总连接', value: total, color: 'bg-gray-100 text-gray-600' },
        { label: '在线', value: online, color: 'bg-green-50 text-green-600' },
        { label: '离线', value: offline, color: 'bg-gray-50 text-gray-400' },
      ].map((s) => (
        <div
          key={s.label}
          className={`flex items-center gap-2 rounded-xl px-4 py-2.5 ${s.color}`}
        >
          <span className="text-2xl font-bold tabular-nums">{s.value}</span>
          <span className="text-xs font-medium">{s.label}</span>
        </div>
      ))}
    </div>
  )
}

// ── QR Code Connection Modal ──

function QrCodeConnectionModal({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<QrStatus>('loading')
  const [qrImage, setQrImage] = useState<string>('')
  const [errorMsg, setErrorMsg] = useState<string>('')
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startupTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollingStartRef = useRef<number>(0) // 轮询开始时间（用于过期宽限期）

  // 触发后端连接（启动 WeChatConnector 生成二维码）
  const triggerConnection = useCallback(async () => {
    try {
      await wechatConnect()
    } catch {
      // 可能已连接中，忽略
    }
  }, [])

  // 轮询连接状态（用户扫码确认后变为已连接）— 必须先于 startQrPolling 声明（被其内部引用）
  const startConnectionPolling = useCallback(() => {
    pollingRef.current = setInterval(async () => {
      try {
        const res = await wechatConnectionStatus()
        const connData = res.data as { connected?: boolean; wxid?: string; status?: string }
        if (connData.connected || connData.status === 'connected') {
          setStatus('connected')
          if (pollingRef.current) clearInterval(pollingRef.current)
          // 自动保存连接
          if (connData.wxid) {
            const wxid = connData.wxid
            wechatCreateConnection({ wxid }).catch(() => {})
            // 如果已登录，自动绑定到当前账号
            if (getAccessToken()) {
              bindWechat({ wxid }).catch(() => {})
            }
          }
        } else if (connData.status === 'scanned') {
          setStatus('scanned')
        }
      } catch {
        // 忽略轮询错误
      }
    }, 2000)
  }, [])

  // 轮询二维码（后端连接器需要时间生成二维码）
  const startQrPolling = useCallback(() => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingStartRef.current = Date.now()

    pollingRef.current = setInterval(async () => {
      try {
        const res = await wechatQrCode()
        const data = res.data as { qr_image?: string; status?: string; is_expired?: boolean; message?: string }

        if (data.qr_image) {
          setQrImage(data.qr_image)
          setStatus('waiting')
          // 有二维码了，切到轮询连接状态
          if (pollingRef.current) clearInterval(pollingRef.current)
          startConnectionPolling()
        } else if (data.is_expired && (Date.now() - pollingStartRef.current) > 12000) {
          // 后端连接器刚启动时可能尚无二维码，给 12s 宽限期后再认为过期
          setStatus('expired')
          if (pollingRef.current) clearInterval(pollingRef.current)
        }
        // 否则继续等待二维码生成
      } catch {
        // 忽略轮询错误
      }
    }, 2000)
  }, [startConnectionPolling])

  // 启动完整流程
  const startFlow = useCallback(() => {
    // 清理旧的 timer 和 polling
    if (startupTimerRef.current) clearTimeout(startupTimerRef.current)
    if (pollingRef.current) clearInterval(pollingRef.current)

    triggerConnection()
    // 延迟启动 QR 轮询，给后端连接器一点初始化时间
    startupTimerRef.current = setTimeout(() => startQrPolling(), 1500)
  }, [triggerConnection, startQrPolling])

  useEffect(() => {
    startFlow()
    return () => {
      if (startupTimerRef.current) clearTimeout(startupTimerRef.current)
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [startFlow])

  // 二维码过期或错误时重新获取
  const handleRefresh = () => {
    setStatus('loading')
    setErrorMsg('')
    startFlow()
  }

  // 关闭前清理
  const handleClose = () => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl text-center">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-gray-800">扫码连接微信</h3>
          <button onClick={handleClose} className="text-gray-300 hover:text-gray-500">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Loading */}
        {status === 'loading' && (
          <div className="py-10 flex flex-col items-center gap-3">
            <div className="h-48 w-48 rounded-xl bg-gray-100 animate-pulse flex items-center justify-center">
              <Smartphone className="h-8 w-8 text-gray-300" />
            </div>
            <p className="text-sm text-gray-400">获取二维码中...</p>
          </div>
        )}

        {/* Waiting for scan */}
        {status === 'waiting' && qrImage && (
          <div className="py-4">
            <div className="mx-auto w-56 h-56 rounded-xl border-2 border-dashed border-gray-200 p-3 flex items-center justify-center bg-white">
              <img
                src={qrImage}
                alt="微信二维码"
                className="w-full h-full object-contain"
              />
            </div>
            <p className="mt-4 text-sm font-medium text-gray-700 flex items-center justify-center gap-1.5">
              <Smartphone className="h-4 w-4 text-primary-500" />
              请用微信扫描二维码
            </p>
            <p className="mt-1 text-xs text-gray-400">
              打开微信 → 扫一扫 → 确认登录
            </p>
            {errorMsg && (
              <p className="mt-2 text-xs text-amber-500">{errorMsg}</p>
            )}
            <div className="mt-5 flex justify-center">
              <button
                onClick={handleRefresh}
                className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-4 py-2 text-xs text-gray-500 hover:bg-gray-50"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                刷新二维码
              </button>
            </div>
          </div>
        )}

        {/* Scanned on phone */}
        {status === 'scanned' && (
          <div className="py-10 flex flex-col items-center gap-3">
            <div className="h-16 w-16 rounded-full bg-amber-50 flex items-center justify-center">
              <Smartphone className="h-7 w-7 text-amber-500" />
            </div>
            <p className="text-sm font-medium text-amber-600">已扫码</p>
            <p className="text-xs text-gray-400">请在手机上确认登录</p>
          </div>
        )}

        {/* Connected! */}
        {status === 'connected' && (
          <div className="py-10 flex flex-col items-center gap-3">
            <div className="h-16 w-16 rounded-full bg-green-50 flex items-center justify-center">
              <CheckCircle2 className="h-7 w-7 text-green-500" />
            </div>
            <p className="text-sm font-medium text-green-600">连接成功</p>
            <p className="text-xs text-gray-400">微信账号已接入</p>
            <button
              onClick={handleClose}
              className="mt-2 rounded-lg bg-primary-500 px-6 py-2 text-xs font-medium text-white hover:bg-primary-600"
            >
              完成
            </button>
          </div>
        )}

        {/* Expired */}
        {status === 'expired' && (
          <div className="py-10 flex flex-col items-center gap-3">
            <AlertTriangle className="h-8 w-8 text-amber-400" />
            <p className="text-sm text-gray-600">二维码已过期</p>
            <button
              onClick={handleRefresh}
              className="flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-xs font-medium text-white hover:bg-primary-600"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              重新获取
            </button>
          </div>
        )}

        {/* Error */}
        {status === 'error' && (
          <div className="py-10 flex flex-col items-center gap-3">
            <AlertTriangle className="h-8 w-8 text-red-400" />
            <p className="text-sm text-red-500">{errorMsg || '获取失败'}</p>
            <button
              onClick={handleRefresh}
              className="flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-xs font-medium text-white hover:bg-primary-600"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              重试
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main Component ──

export default function WeChatPage() {
  const [connections, setConnections] = useState<SavedConnection[]>(() => {
    try {
      // 兼容层：先读新键，回退到旧键（老的"ai-girlfriend-wechat-connections"自动迁移）
      const raw =
        localStorage.getItem('unique-you-wechat-connections') ??
        localStorage.getItem('ai-girlfriend-wechat-connections')
      if (!raw) return []
      const list = JSON.parse(raw) as SavedConnection[]
      // 一次性迁移：读到旧键则立即写新键，后续用新键
      if (!localStorage.getItem('unique-you-wechat-connections')) {
        try {
          localStorage.setItem('unique-you-wechat-connections', raw)
          localStorage.removeItem('ai-girlfriend-wechat-connections')
        } catch {
          /* 忽略写失败 */
        }
      }
      return list
    } catch {
      return []
    }
  })

  const [searchQuery, setSearchQuery] = useState('')
  const [showQrModal, setShowQrModal] = useState(false)

  // ── Persist ──

  const persist = useCallback((list: SavedConnection[]) => {
    localStorage.setItem('unique-you-wechat-connections', JSON.stringify(list))
  }, [])

  // ── Filter ──

  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return connections
    const q = searchQuery.toLowerCase()
    return connections.filter(
      (c) =>
        c.wxid?.toLowerCase().includes(q) ||
        c.alias?.toLowerCase().includes(q)
    )
  }, [connections, searchQuery])

  // ── Handlers ──

  const handleDelete = useCallback(
    (wxid: string) => {
      const updated = connections.filter((c) => c.wxid !== wxid)
      setConnections(updated)
      persist(updated)
      wechatDeleteConnection(wxid).catch(() => {})
    },
    [connections, persist]
  )

  // ── Render ──

  return (
    <AnimatedPage>
      <div className="bg-dynamic px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-6xl">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-xl font-bold text-gray-800">微信接入</h1>
              <p className="mt-0.5 text-sm text-gray-400">
                扫码连接微信账号，管理实时状态
              </p>
            </div>
            <button
              onClick={() => setShowQrModal(true)}
              className="flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-primary-600 active:scale-[0.97]"
            >
              <QrCode className="h-4 w-4" />
              扫码连接
            </button>
          </div>

          {/* Live Status */}
          <LiveStatusBanner />

          {/* Connections Stats */}
          <StatsBar connections={connections} />

          {/* Search */}
          <div className="relative mb-4">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-300" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索 wxid 或别名..."
              className="w-full rounded-xl border border-gray-200 bg-white/60 py-2.5 pl-10 pr-4 text-sm text-gray-700 placeholder:text-gray-300 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-400/20"
            />
          </div>

          {/* Table */}
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white/80">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50/50 text-left text-xs font-medium text-gray-400">
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">wxid</th>
                  <th className="px-4 py-3">别名</th>
                  <th className="px-4 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-12 text-center text-sm text-gray-300">
                      {searchQuery ? '未找到匹配的连接' : '暂无连接，点击上方 "扫码连接" 添加'}
                    </td>
                  </tr>
                ) : (
                  filtered.map((conn) => (
                    <tr
                      key={conn.wxid}
                      className="border-b border-gray-50 transition hover:bg-gray-50/50 last:border-0"
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span
                            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                              conn.isOnline
                                ? 'bg-green-50 text-green-600'
                                : 'bg-gray-100 text-gray-400'
                            }`}
                          >
                            {conn.isOnline ? (
                              <Wifi className="h-3 w-3" />
                            ) : (
                              <WifiOff className="h-3 w-3" />
                            )}
                            {conn.isOnline ? '在线' : '离线'}
                          </span>
                          {conn.isCurrent && (
                            <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-500">
                              当前
                            </span>
                          )}
                        </div>
                      </td>

                      <td className="px-4 py-3">
                        <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600">
                          {conn.wxid}
                        </code>
                      </td>

                      <td className="px-4 py-3 text-gray-700">{conn.alias}</td>

                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => handleDelete(conn.wxid)}
                            className="rounded-lg p-1.5 text-gray-300 transition hover:bg-red-50 hover:text-red-500"
                            title="删除"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {showQrModal && (
        <QrCodeConnectionModal onClose={() => setShowQrModal(false)} />
      )}
    </AnimatedPage>
  )
}
