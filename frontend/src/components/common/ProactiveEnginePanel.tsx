import { useState } from 'react'
import Card from './Card'
import Button from './Button'
import UrgencyBadge from './UrgencyBadge'
import { api } from '../../api/client'
import { useErrorStore } from '../../store/errorStore'
import type { ProactiveEngineState } from '../../types/api'

interface ProactiveEnginePanelProps {
  state: ProactiveEngineState | null
  onConfigUpdated?: () => void
}

export default function ProactiveEnginePanel({ state, onConfigUpdated }: ProactiveEnginePanelProps) {
  const config = state?.config
  const [threshold, setThreshold] = useState(config?.threshold ?? 5)
  const [maxDaily, setMaxDaily] = useState(config?.max_daily ?? 5)
  const [minInterval, setMinInterval] = useState(config?.min_interval_minutes ?? 30)
  const [cooldown, setCooldown] = useState(config?.cooldown_after_reply_minutes ?? 10)
  const [saving, setSaving] = useState(false)
  const addToast = useErrorStore((s) => s.addToast)

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.updateProactiveConfig({
        threshold,
        max_daily: maxDaily,
        min_interval_minutes: minInterval,
        cooldown_after_reply_minutes: cooldown,
      })
      addToast({ type: 'success', message: '主动发言配置已更新' })
      onConfigUpdated?.()
    } catch {
      addToast({ type: 'error', message: '配置更新失败' })
      if (state?.config) {
        setThreshold(state.config.threshold)
        setMaxDaily(state.config.max_daily)
        setMinInterval(state.config.min_interval_minutes)
        setCooldown(state.config.cooldown_after_reply_minutes)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="space-y-4">
      <h3 className="text-sm font-semibold text-gray-800">主动发言引擎</h3>

      {state ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">紧迫度:</span>
            <UrgencyBadge urgency={state.urgency} />
          </div>
          <div className="flex gap-4 text-xs text-gray-400">
            <span>今日已发: {state.daily_count}</span>
            <span>上次发言: {state.last_proactive_at ?? '—'}</span>
          </div>
        </div>
      ) : (
        <p className="text-xs text-gray-400">引擎状态未知</p>
      )}

      <div className="space-y-3 pt-2 border-t border-gray-200">
        <div>
          <label className="text-xs text-gray-500 block mb-1">
            触发阈值: {threshold}
          </label>
          <input
            type="range" min={1} max={10} step={0.5}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            className="w-full accent-primary-500"
          />
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div>
            <label className="text-xs text-gray-500 block mb-1">每日上限</label>
            <input
              type="number" min={1} max={100}
              value={maxDaily}
              onChange={(e) => setMaxDaily(Number(e.target.value))}
              className="w-full bg-gray-200/60 border border-gray-300/50 rounded px-2 py-1 text-xs text-gray-800"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">最小间隔(分)</label>
            <input
              type="number" min={1} max={1440}
              value={minInterval}
              onChange={(e) => setMinInterval(Number(e.target.value))}
              className="w-full bg-gray-200/60 border border-gray-300/50 rounded px-2 py-1 text-xs text-gray-800"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">回复冷却(分)</label>
            <input
              type="number" min={1} max={1440}
              value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))}
              className="w-full bg-gray-200/60 border border-gray-300/50 rounded px-2 py-1 text-xs text-gray-800"
            />
          </div>
        </div>

        <Button size="sm" loading={saving} onClick={handleSave} className="w-full">
          保存配置
        </Button>
      </div>
    </Card>
  )
}
