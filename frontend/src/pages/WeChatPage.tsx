import { useState, useCallback, useEffect, useRef } from 'react'
import { AnimatedPage } from '../components/shared'
import { useQueryClient } from '@tanstack/react-query'
import { useWechatStatus, queryKeys } from '../hooks/useQueries'
import type { WeChatStatus } from '../types/api'
import { RefreshCw, X, QrCode, Clock, MessageSquare, AlertTriangle, CheckCircle2, Smartphone } from 'lucide-react'
import { wechatCreateConnection } from '../api/wechat'
import { wechatQrCode, wechatConnectionStatus, wechatConnect } from '../api/system'

// ── Types ──

type QrStatus = 'loading' | 'waiting' | 'scanned' | 'connected' | 'expired' | 'error'

// ── Live Status Banner ──

function LiveStatusBanner() {
  const { data: status, isLoading, isError } = useWechatStatus()
  const [showQrModal, setShowQrModal] = useState(false)

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
    <div className="glass-card mb-5 rounded-xl p-4 stagger-item">
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
              {status.connected ? `运行 ${uptime}` : '未运行'}
            </span>
            <span className="flex items-center gap-1">
              <MessageSquare className="h-3 w-3" />
              {status.connected ? `今日 ${status.messages_today ?? 0} 条消息` : '今日暂无消息'}
            </span>
          </div>
        </div>

        {/* Right: QR code / reconnect */}
        <div className="flex items-center gap-2">
          {status.qr_code && (
            <button
              onClick={() => {
                const qrUrl = status.qr_code!
                if (qrUrl.startsWith('http://') || qrUrl.startsWith('https://')) {
                  window.open(qrUrl, '_blank', 'noopener,noreferrer')
                }
              }}
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

      {/* 断开态引导卡（SP-5 P1：空态构图 + 行动指引，替代巨幅空白） */}
      {!status.connected && (
        <div className="mt-4 rounded-xl border border-dashed border-macaron-blue/30 bg-macaron-blue-light/20 p-6 text-center stagger-item">
          <QrCode className="mx-auto h-10 w-10 text-macaron-blue/60" />
          <p className="mt-3 text-sm font-medium text-gray-700">微信尚未连接</p>
          <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-gray-400">
            点击下方按钮扫码登录微信；连接成功后系统自动保持在线（断线自动重连），
            你的好友即可与角色开始对话。
          </p>
          <button
            onClick={() => setShowQrModal(true)}
            className="btn-macaron mx-auto mt-4 flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-medium"
          >
            <QrCode className="h-3.5 w-3.5" />
            立即扫码连接
          </button>
        </div>
      )}

      {showQrModal && (
        <QrCodeConnectionModal onClose={() => setShowQrModal(false)} />
      )}
    </div>
  )
}

// ── QR Code Connection Modal ──

function QrCodeConnectionModal({ onClose, onConnected }: { onClose: () => void; onConnected?: (wxid: string) => void }) {
  const [status, setStatus] = useState<QrStatus>('loading')
  const [qrImage, setQrImage] = useState<string>('')
  const [errorMsg, setErrorMsg] = useState<string>('')
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startupTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollingStartRef = useRef<number>(0)
  const connectedRef = useRef(false)
  const qc = useQueryClient()

  const triggerConnection = useCallback(async () => {
    try {
      await wechatConnect()
    } catch {
      // 可能已连接中，忽略
    }
  }, [])

  const startConnectionPolling = useCallback(() => {
    pollingRef.current = setInterval(async () => {
      try {
        const res = await wechatConnectionStatus()
        const connData = res.data as { connected?: boolean; wxid?: string; status?: string }
        if (connData.connected || connData.status === 'connected') {
          setStatus('connected')
          if (pollingRef.current) clearInterval(pollingRef.current)
          if (connData.wxid && !connectedRef.current) {
            connectedRef.current = true
            const wxid = connData.wxid
            try {
              await wechatCreateConnection({ wxid })
            } catch {
              // 忽略保存失败
            }
            onConnected?.(wxid)
            qc.invalidateQueries({ queryKey: queryKeys.wechat.status })
            qc.invalidateQueries({ queryKey: ['wechat', 'bindings'] })
          }
        } else if (connData.status === 'scanned') {
          setStatus('scanned')
        }
      } catch {
        // 忽略轮询错误
      }
    }, 2000)
  }, [onConnected, qc])

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
          if (pollingRef.current) clearInterval(pollingRef.current)
          startConnectionPolling()
        } else if (data.is_expired && (Date.now() - pollingStartRef.current) > 12000) {
          setStatus('expired')
          if (pollingRef.current) clearInterval(pollingRef.current)
        }
      } catch {
        // 忽略轮询错误
      }
    }, 2000)
  }, [startConnectionPolling])

  const startFlow = useCallback(() => {
    if (startupTimerRef.current) clearTimeout(startupTimerRef.current)
    if (pollingRef.current) clearInterval(pollingRef.current)

    triggerConnection()
    startupTimerRef.current = setTimeout(() => startQrPolling(), 1500)
  }, [triggerConnection, startQrPolling])

  useEffect(() => {
    startFlow()
    return () => {
      if (startupTimerRef.current) clearTimeout(startupTimerRef.current)
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [startFlow])

  const handleRefresh = () => {
    setStatus('loading')
    setErrorMsg('')
    startFlow()
  }

  const handleClose = () => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl text-center">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-gray-800">扫码连接微信</h3>
          <button onClick={handleClose} className="text-gray-300 hover:text-gray-500">
            <X className="h-4 w-4" />
          </button>
        </div>

        {status === 'loading' && (
          <div className="py-10 flex flex-col items-center gap-3">
            <div className="h-48 w-48 rounded-xl bg-gray-100 animate-pulse flex items-center justify-center">
              <Smartphone className="h-8 w-8 text-gray-300" />
            </div>
            <p className="text-sm text-gray-400">获取二维码中...</p>
          </div>
        )}

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

        {status === 'scanned' && (
          <div className="py-10 flex flex-col items-center gap-3">
            <div className="h-16 w-16 rounded-full bg-amber-50 flex items-center justify-center">
              <Smartphone className="h-7 w-7 text-amber-500" />
            </div>
            <p className="text-sm font-medium text-amber-600">已扫码</p>
            <p className="text-xs text-gray-400">请在手机上确认登录</p>
          </div>
        )}

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

/** SSE 实时订阅微信状态，更新 React Query 缓存，避免轮询抖动。 */
function useWechatStatusStream() {
  const qc = useQueryClient()
  const reconnectRef = useRef(0)

  useEffect(() => {
    // Node.js / 测试环境无 EventSource，优雅跳过
    if (typeof EventSource === 'undefined') return

    let es: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let closed = false

    const connect = () => {
      if (closed) return
      const apiKey = import.meta.env.VITE_API_KEY || ''
      const url = apiKey
        ? `/api/channels/wechat/status-stream?api_key=${encodeURIComponent(apiKey)}`
        : '/api/channels/wechat/status-stream'
      es = new EventSource(url)

      es.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as WeChatStatus
          qc.setQueryData(queryKeys.wechat.status, payload)
          reconnectRef.current = 0
        } catch {
          // 忽略非 JSON 数据
        }
      }

      es.onerror = () => {
        if (closed) return
        if (es) {
          es.close()
          es = null
        }
        // 指数退避重连：1s / 2s / 4s / 8s，最大 30s
        const delay = Math.min(1000 * 2 ** reconnectRef.current, 30000)
        reconnectRef.current = Math.min(reconnectRef.current + 1, 5)
        reconnectTimer = setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (es) es.close()
    }
  }, [qc])
}

// ── Main Component ──

export default function WeChatPage() {
  const qc = useQueryClient()
  // SSE 实时更新缓存，轮询作为兜底（30s 一次）
  useWechatStatusStream()

  const [showQrModal, setShowQrModal] = useState(false)

  const handleConnected = useCallback(
    (_wxid: string) => {
      qc.invalidateQueries({ queryKey: queryKeys.wechat.status })
      qc.invalidateQueries({ queryKey: ['wechat', 'bindings'] })
    },
    [qc]
  )

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
              className="btn-macaron flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium shadow-sm"
            >
              <QrCode className="h-4 w-4" />
              扫码连接
            </button>
          </div>

          {/* Live Status */}
          <LiveStatusBanner />
        </div>
      </div>

      {showQrModal && (
        <QrCodeConnectionModal onClose={() => setShowQrModal(false)} onConnected={handleConnected} />
      )}
    </AnimatedPage>
  )
}
