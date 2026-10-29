import { useState, useCallback, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { AnimatedPage } from '../components/shared'
import { useQueryClient } from '@tanstack/react-query'
import { useWechatStatus, useUnifiedCharacters, queryKeys } from '../hooks/useQueries'
import { RefreshCw, X, QrCode, Clock, MessageSquare, AlertTriangle, CheckCircle2, Smartphone, UserRound } from 'lucide-react'
import { wechatQrCode, wechatConnectionStatus, wechatConnect } from '../api/system'
import { sanitizeCharacterName } from '../utils/character'

// ── Types ──

type QrStatus = 'loading' | 'waiting' | 'scanned' | 'connected' | 'expired' | 'error'

// ── Live Status Banner ──

function LiveStatusBanner() {
  const { data: status, isLoading, isError } = useWechatStatus()
  // 2026-10-29 V2-2：微信页显示「当前聊天角色」——此前全站无任何地方告诉用户
  // 「你微信里在跟谁聊」（用户报障：角色未激活却挂着数据/不知在聊哪个角色）。
  const { data: charData } = useUnifiedCharacters()
  const activeCharacter = charData?.characters?.find((c) => c.is_active)
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
              我的微信 {status.connected ? '已连接' : '未连接'}
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
        {/* V2-2：绑定账号 + 当前聊天角色（此前二者均不可见，扫错号/不知在聊谁无从察觉） */}
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
          <span className="flex items-center gap-1" title="当前绑定到本账号的微信机器人账号">
            <Smartphone className="h-3 w-3 text-gray-400" />
            绑定账号：{status.bot_id ? status.bot_id : '未绑定'}
          </span>
          <span className="flex items-center gap-1" title="你微信里正在对话的角色">
            <UserRound className="h-3 w-3 text-gray-400" />
            当前聊天角色：
            {activeCharacter ? (
              <span className="font-medium text-gray-700">{sanitizeCharacterName(activeCharacter.name)}</span>
            ) : (
              <Link to="/roles" className="text-macaron-blue-deep hover:underline">尚未选择，去选择角色 →</Link>
            )}
          </span>
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

      {(() => {
        // 后端 wechat_connector 用 time.time() 记录活动时刻（**秒**），
        // 直接 new Date(秒) 会被当毫秒解析 → 显示 1970-01-21（09-19 诊断 P2）。
        // 启发式：<1e12 视为秒需 ×1000；再叠年份守卫，任何 epoch 哨兵值都不外泄。
        const raw = status.last_activity;
        if (raw === null || raw === undefined || raw === '') return null;
        const n = Number(raw);
        const ms =
          Number.isFinite(n) && n > 0 ? (n < 1e12 ? n * 1000 : n) : Date.parse(String(raw));
        if (!Number.isFinite(ms) || ms <= 0) return null;
        const d = new Date(ms);
        if (d.getFullYear() < 2010) return null;
        return (
          <p className="mt-2 text-[10px] text-gray-400">
            最后活动: {d.toLocaleString('zh-CN')}
          </p>
        );
      })()}

      {/* 断开态引导卡（SP-5 P1：空态构图 + 行动指引，替代巨幅空白） */}
      {!status.connected && (
        <div className="mt-4 rounded-xl border border-dashed border-macaron-blue/30 bg-macaron-blue-light/20 p-6 text-center stagger-item">
          <QrCode className="mx-auto h-10 w-10 text-macaron-blue/60" />
          <p className="mt-3 text-sm font-medium text-gray-700">你尚未连接自己的微信</p>
          <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-gray-400">
            点击下方按钮，用你自己的微信扫码绑定（与他人通道隔离）；
            连接成功后，你即可在微信里与角色一对一对话。
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
  // NEW-4（2026-10-29）：连续失败计数——旧实现轮询 catch 静默吞错，error 分支
  // 永不可达，后端/平台故障时用户只见无限「获取二维码中…」。
  const qrFailRef = useRef(0)
  const connFailRef = useRef(0)
  // 看门狗用 ref 读码图（不进 useCallback 依赖，避免轮询被 effect 重启打断）
  const qrImageRef = useRef('')
  const qc = useQueryClient()

  /** 连续失败达阈值：停轮询并置错误态（重试按钮走 handleRefresh 重开全流程） */
  const bailToError = useCallback((what: 'qr' | 'conn') => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingRef.current = null
    setStatus('error')
    setErrorMsg(what === 'qr' ? '二维码获取失败，请检查网络后重试' : '连接状态查询失败，请检查网络后重试')
  }, [])

  const triggerConnection = useCallback(async () => {
    try {
      await wechatConnect()
    } catch {
      // 可能已连接中，忽略
    }
  }, [])

  const startConnectionPolling = useCallback(() => {
    connFailRef.current = 0
    pollingRef.current = setInterval(async () => {
      try {
        const res = await wechatConnectionStatus()
        connFailRef.current = 0
        const connData = res.data as { connected?: boolean; wxid?: string; status?: string; is_expired?: boolean }
        if (connData.connected || connData.status === 'connected') {
          setStatus('connected')
          if (pollingRef.current) clearInterval(pollingRef.current)
          // 本人通道已在后端落库，不再调用 admin 的 wechatCreateConnection
          qc.invalidateQueries({ queryKey: queryKeys.wechat.status })
          qc.invalidateQueries({ queryKey: ['wechat', 'bindings'] })
        } else if (connData.is_expired === true) {
          // NEW-4：等待扫码期间二维码过期（后端 waiting_qr/scanned 态返回时效）——
          // 旧实现此分支不存在，延误扫码后旧码永远显示「等待扫描」。
          setStatus('expired')
          if (pollingRef.current) clearInterval(pollingRef.current)
        } else if (connData.status === 'scanned') {
          setStatus('scanned')
        }
      } catch {
        connFailRef.current += 1
        if (connFailRef.current >= 3) bailToError('conn')
      }
    }, 2000)
  }, [onConnected, qc, bailToError])

  const startQrPolling = useCallback(() => {
    if (pollingRef.current) clearInterval(pollingRef.current)
    pollingStartRef.current = Date.now()
    qrFailRef.current = 0

    pollingRef.current = setInterval(async () => {
      // 看门狗：15s（启动延迟 + 数次轮询）仍无码 → 错误态，避免请求悬挂时无限 loading
      if (!qrImageRef.current && Date.now() - pollingStartRef.current > 15000) {
        bailToError('qr')
        return
      }
      try {
        const res = await wechatQrCode()
        qrFailRef.current = 0
        const data = res.data as { qr_image?: string; status?: string; is_expired?: boolean; message?: string }

        if (data.qr_image) {
          qrImageRef.current = data.qr_image
          setQrImage(data.qr_image)
          setStatus('waiting')
          if (pollingRef.current) clearInterval(pollingRef.current)
          startConnectionPolling()
        } else if (data.is_expired && (Date.now() - pollingStartRef.current) > 12000) {
          setStatus('expired')
          if (pollingRef.current) clearInterval(pollingRef.current)
        }
      } catch {
        qrFailRef.current += 1
        if (qrFailRef.current >= 3) bailToError('qr')
      }
    }, 2000)
  }, [startConnectionPolling, bailToError])

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
    qrFailRef.current = 0
    connFailRef.current = 0
    qrImageRef.current = ''
    setQrImage('')
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
          <button onClick={handleClose} className="text-gray-400 hover:text-gray-500">
            <X className="h-4 w-4" />
          </button>
        </div>

        {status === 'loading' && (
          <div className="py-10 flex flex-col items-center gap-3">
            <div className="h-48 w-48 rounded-xl bg-gray-100 animate-pulse flex items-center justify-center">
              <Smartphone className="h-8 w-8 text-gray-400" />
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
            {/* V2-4③（2026-10-29）：首日接力——成功后告诉用户下一步做什么 */}
            <p className="text-xs text-gray-500 bg-macaron-mint-light/40 rounded-lg px-3 py-2">
              现在打开微信，给你的角色发第一条消息试试吧
            </p>
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

/** 本人通道状态由 useWechatStatus 轮询驱动（隔离后不再订阅全局 SSE）。 */
function useWechatStatusStream() {
  // no-op：避免误连全局 /api/channels/wechat/status-stream
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
      <div className="px-4 py-6 sm:px-6 lg:px-8">
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

          {/* 2026-09-19 审美批次：接入页信息量小，用双列说明卡填充构图，
              替代「一条状态栏 + 整页空白」的失衡布局 */}
          <div className="mt-5 grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="glass-card rounded-xl p-5">
              <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
                <span className="section-bar" />
                连接后怎么用
              </h3>
              <ol className="mt-3 space-y-3">
                {[
                  ['扫码登录', '点击右上「扫码连接」，用微信扫码确认登录'],
                  ['发消息', '直接用你扫码的微信给角色发消息即可'],
                  ['角色回应', '按你在控制台选择的角色人设自动回复，支持文字与语音'],
                ].map(([title, desc], i) => (
                  <li key={title} className="flex items-start gap-3">
                    <span className="shrink-0 w-5 h-5 rounded-full bg-macaron-blue-light text-macaron-blue-deep text-[10px] font-bold flex items-center justify-center mt-0.5">
                      {i + 1}
                    </span>
                    <div>
                      <p className="text-xs font-medium text-gray-700">{title}</p>
                      <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">{desc}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
            <div className="glass-card rounded-xl p-5">
              <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
                <span className="section-bar" />
                连接机制
              </h3>
              <ul className="mt-3 space-y-3">
                {[
                  ['断线自动重连', '登录态失效或网络波动时系统自动尝试恢复，无需手动操作'],
                  ['账号隔离', '每位注册用户的数据严格隔离；你的记忆与亲密度只属于你自己'],
                  ['状态实时同步', '本页通过实时推送展示桥接状态；「最后活动」为最近一次收发消息的时间'],
                ].map(([title, desc]) => (
                  <li key={title} className="flex items-start gap-3">
                    <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-macaron-mint-deep/60 mt-1.5" />
                    <div>
                      <p className="text-xs font-medium text-gray-700">{title}</p>
                      <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">{desc}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>

      {showQrModal && (
        <QrCodeConnectionModal onClose={() => setShowQrModal(false)} onConnected={handleConnected} />
      )}
    </AnimatedPage>
  )
}
