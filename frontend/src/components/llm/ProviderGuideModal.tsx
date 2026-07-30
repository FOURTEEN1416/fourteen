/**
 * 供应商申请教程弹窗 — 详细分步骤展示如何申请 API Key
 *
 * 用法：
 *   <ProviderGuideModal provider={provider} open={open} onClose={...} />
 */
import { useState } from 'react'
import Modal from '../shared/Modal'
import type { ProviderOption } from '../../api/llmProviders'
import { ExternalLink, Copy, Check, Gift, Lightbulb, AlertTriangle, CheckCircle2 } from 'lucide-react'

interface ProviderGuideModalProps {
  provider: ProviderOption | null
  open: boolean
  onClose: () => void
}

export default function ProviderGuideModal({ provider, open, onClose }: ProviderGuideModalProps) {
  const [copied, setCopied] = useState(false)

  if (!provider) return null
  const guide = provider.guide
  if (!guide) return null

  const handleCopyApplyUrl = () => {
    if (!guide.apply_url) return
    navigator.clipboard.writeText(guide.apply_url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <Modal open={open} title={`${provider.name} — 申请教程`} onClose={onClose} size="lg">
      <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
        {/* ── 免费额度 ── */}
        {guide.free_quota && (
          <div className="bg-gradient-to-r from-green-50 to-emerald-50 border border-green-200 rounded-lg p-3 flex items-start gap-2">
            <Gift className="w-4 h-4 text-green-600 mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold text-green-800 mb-0.5">免费额度</p>
              <p className="text-xs text-green-700">{guide.free_quota}</p>
            </div>
          </div>
        )}

        {/* ── 申请地址 ── */}
        {guide.apply_url && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
            <p className="text-xs font-semibold text-blue-800 mb-2">申请地址</p>
            <div className="flex items-center gap-2">
              <a
                href={guide.apply_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 text-xs text-blue-600 hover:underline break-all"
              >
                {guide.apply_url}
              </a>
              <button
                onClick={handleCopyApplyUrl}
                className="shrink-0 px-2 py-1 text-[10px] font-medium text-blue-700 bg-white rounded hover:bg-blue-50 transition-colors flex items-center gap-1"
              >
                {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                {copied ? '已复制' : '复制'}
              </button>
              <a
                href={guide.apply_url}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 px-3 py-1 text-[10px] font-medium text-white bg-blue-500 rounded hover:bg-blue-600 transition-colors flex items-center gap-1"
              >
                <ExternalLink className="w-3 h-3" />
                打开
              </a>
            </div>
          </div>
        )}

        {/* ── 操作步骤 ── */}
        {guide.steps.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-700 mb-2 flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5 text-primary-500" />
              操作步骤
            </p>
            <ol className="space-y-2">
              {guide.steps.map((step, idx) => (
                <li key={idx} className="flex gap-2 text-xs text-gray-600">
                  <span className="shrink-0 w-5 h-5 rounded-full bg-primary-100 text-primary-700 text-[10px] font-bold flex items-center justify-center">
                    {idx + 1}
                  </span>
                  <span className="flex-1 pt-0.5 whitespace-pre-wrap">{step}</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* ── 提示 ── */}
        {guide.tips.length > 0 && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
            <p className="text-xs font-semibold text-amber-800 mb-2 flex items-center gap-1">
              <Lightbulb className="w-3.5 h-3.5" />
              小提示
            </p>
            <ul className="space-y-1">
              {guide.tips.map((tip, idx) => (
                <li key={idx} className="text-xs text-amber-700 flex gap-1.5">
                  <span className="text-amber-400">•</span>
                  <span className="flex-1">{tip}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* ── 警告 ── */}
        {guide.warnings.length > 0 && (
          <div className="bg-rose-50 border border-rose-200 rounded-lg p-3">
            <p className="text-xs font-semibold text-rose-800 mb-2 flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" />
              注意事项
            </p>
            <ul className="space-y-1">
              {guide.warnings.map((w, idx) => (
                <li key={idx} className="text-xs text-rose-700 flex gap-1.5">
                  <span className="text-rose-400">⚠</span>
                  <span className="flex-1">{w}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* ── 关闭按钮 ── */}
        <div className="flex justify-end pt-2 border-t border-gray-100">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-xs font-medium text-white bg-primary-500 hover:bg-primary-600 rounded-lg transition-colors"
          >
            我知道了
          </button>
        </div>
      </div>
    </Modal>
  )
}
