import { useState } from 'react'
import { useVoiceStatus, usePlugins, useTogglePlugin, useToolHistory } from '../hooks/useQueries'
import Card from '../components/common/Card'
import Badge from '../components/common/Badge'
import Button from '../components/common/Button'
import Skeleton from '../components/common/Skeleton'
import { Volume2, Puzzle, Wrench, Clock } from 'lucide-react'

export default function ExtensionsPage() {
  const { data: voiceStatus, isLoading: voiceLoading } = useVoiceStatus()
  const { data: plugins, isLoading: pluginsLoading } = usePlugins()
  const togglePlugin = useTogglePlugin()
  const { data: toolHistory } = useToolHistory()
  const [ttsText, setTtsText] = useState('')
  const [ttsPlaying, setTtsPlaying] = useState(false)

  const playTTS = async () => {
    if (!ttsText.trim()) return
    setTtsPlaying(true)
    try {
      const { api } = await import('../api/client')
      const r = await api.voiceSynthesize(ttsText)
      const blob = r.data as Blob
      const url = URL.createObjectURL(blob)
      const a = new Audio(url)
      a.play()
    } catch { /* ignore */ }
    setTtsPlaying(false)
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
        <Puzzle className="w-4 h-4 text-purple-500" />
        扩展管理
      </h1>

      {/* Voice TTS */}
      <Card>
        <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <Volume2 className="w-4 h-4 text-blue-500" />
          语音合成 (TTS)
        </h2>
        {voiceLoading ? <Skeleton lines={2} /> : !voiceStatus?.enabled ? (
          <p className="text-sm text-gray-400">TTS未启用，请在设置中配置语音引擎</p>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-3 text-xs text-gray-500">
              <span>当前引擎: <Badge variant="info">{voiceStatus.current_engine || '-'}</Badge></span>
              <span>可用引擎: {voiceStatus.available_engines?.join(', ') || '-'}</span>
              <span>已合成: {voiceStatus.synthesize_count || 0}次</span>
            </div>
            <div className="flex gap-2">
              <input
                value={ttsText}
                onChange={e => setTtsText(e.target.value)}
                placeholder="输入要朗读的文字..."
                className="flex-1 px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400/30"
              />
              <Button size="sm" onClick={playTTS} loading={ttsPlaying}>试听</Button>
            </div>
          </div>
        )}
      </Card>

      {/* Plugins */}
      <Card>
        <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <Puzzle className="w-4 h-4 text-purple-500" />
          插件管理
        </h2>
        {pluginsLoading ? <Skeleton lines={2} /> : !plugins || Object.keys(plugins).length === 0 ? (
          <p className="text-sm text-gray-400">暂无已安装插件</p>
        ) : (
          <div className="space-y-2">
            {Object.entries(plugins).map(([name, cfg]) => (
              <div key={name} className="flex items-center justify-between p-2 bg-gray-50 rounded-lg">
                <div>
                  <span className="text-sm text-gray-700 font-medium">{name}</span>
                  {cfg.toggled_at && <span className="text-[10px] text-gray-400 ml-2">最后操作: {cfg.toggled_at.slice(0, 16)}</span>}
                </div>
                <button
                  onClick={() => togglePlugin.mutate({ name, enabled: !cfg.enabled })}
                  className={`px-2 py-0.5 text-[11px] rounded-md font-medium transition-colors ${
                    cfg.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-200 text-gray-500'
                  }`}
                >
                  {cfg.enabled ? '已启用' : '已禁用'}
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Tool History */}
      <Card>
        <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <Wrench className="w-4 h-4 text-amber-500" />
          工具操作历史
        </h2>
        {!toolHistory?.length ? (
          <p className="text-sm text-gray-400">暂无操作记录</p>
        ) : (
          <div className="space-y-1">
            {toolHistory.slice(0, 30).map((entry, i) => (
              <div key={i} className="flex items-center gap-3 text-xs py-1 border-b border-gray-50">
                <Clock className="w-3 h-3 text-gray-400" />
                <span className="text-gray-500">{entry.timestamp?.slice(0, 19)}</span>
                <Badge variant={entry.action === 'enable' ? 'success' : 'error'}>{entry.action}</Badge>
                <span className="text-gray-700 font-medium">{entry.tool}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
