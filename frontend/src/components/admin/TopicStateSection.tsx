/**
 * 控制台「话题状态」面板（消息 tab 子区块）。
 *
 * 设计契约（2026-10-27 实施计划 §9.2）：
 *  - 门禁沿用 MessageTab：非 admin 不渲染表单、不请求 admin-only 接口；
 *  - 11 个配置键全部可见：可调项走滑条，Q1/Q4/Q5/Q6 裁决固化项只读展示；
 *  - 会话键 + 角色 id 查询只读快照：正在聊 / 没聊完 / 已结束 / 下一次对话计划；
 *  - 读失败显式错误态 + 重试；保存以回执 `status === 'ok'` 判成功，dirty 显式标注。
 */
import { useEffect, useState } from 'react'
import Section from './RoleSettingsSection'
import Slider from '../shared/Slider'
import Toggle from '../shared/Toggle'
import { useAuthStore } from '../../store/authStore'
import {
  topicStateIsDirty,
  useTopicStateStore,
} from '../../store/topicStateStore'
import type {
  TopicStateConfig,
  TopicStateItem,
  TopicStatePlan,
} from '../../store/topicStateStore'
import {
  topicStateGetConfig,
  topicStateSnapshot,
  updateTopicStateConfig,
} from '../../api/system'
import { adminListUsers } from '../../api/admin'

interface NumberRowProps {
  label: string
  value: number
  min: number
  max: number
  step: number
  unit?: string
  tooltip?: string
  onChange: (value: number) => void
}

function NumberRow({ label, value, min, max, step, unit, tooltip, onChange }: NumberRowProps) {
  return (
    <div className="flex items-center gap-4">
      <div className="flex-1">
        <Slider
          label={label}
          tooltip={tooltip}
          value={value}
          min={min}
          max={max}
          step={step}
          showValue={false}
          onChange={onChange}
        />
      </div>
      <span className="w-20 shrink-0 text-right text-xs font-mono text-gray-400">
        {value}{unit ? ` ${unit}` : ''}
      </span>
    </div>
  )
}

function FixedRow({ label, value, unit }: { label: string; value: number; unit: string }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg bg-gray-50/70 px-3 py-2">
      <span className="text-xs text-gray-600">{label}</span>
      <span className="text-xs font-mono text-gray-500">
        {value} {unit}
        <span className="ml-2 rounded bg-gray-200/60 px-1.5 py-0.5 text-[10px] text-gray-500">固定</span>
      </span>
    </div>
  )
}

function TopicList({ title, items, showCounts }: {
  title: string
  items: TopicStateItem[]
  showCounts?: boolean
}) {
  return (
    <div data-testid={`topic-state-list-${title}`}>
      <p className="text-xs font-medium text-gray-600 mb-1.5">{title}（{items.length}）</p>
      {items.length === 0 ? (
        <p className="text-[11px] text-gray-400">暂无</p>
      ) : (
        <div className="space-y-1.5">
          {items.map((item, index) => (
            <div
              key={`${item.title}-${index}`}
              className="rounded-lg border border-gray-100 bg-white/70 px-3 py-1.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-gray-700 truncate">{item.title || '未命名话题'}</span>
                <span className="text-[10px] text-gray-400 shrink-0">显著度 {item.salience.toFixed(2)}</span>
              </div>
              <div className="mt-0.5 text-[10px] text-gray-400">
                {showCounts && (
                  <span>第 {item.turn_count} 轮 / 提及 {item.mention_count} 次 · </span>
                )}
                最近 {item.last_active_at || '—'}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PlanBlock({ plan }: { plan: TopicStatePlan | null }) {
  return (
    <div data-testid="topic-state-next-plan">
      <p className="text-xs font-medium text-gray-600 mb-1.5">下一次对话计划</p>
      {!plan ? (
        <p className="text-[11px] text-gray-400">暂无计划（决策线尚未产出下一次话题）</p>
      ) : (
        <div className="rounded-lg border border-gray-100 bg-white/70 px-3 py-2 text-[11px] text-gray-500 space-y-0.5">
          <p>话题：<span className="text-gray-700">{plan.topic || '—'}</span></p>
          <p>意图：<span className="text-gray-700">{plan.intent || '—'}</span></p>
          <p>计划于：{plan.planned_at || '—'} · 到点：{plan.next_at || '—'}</p>
          <p>过期：{plan.expires_at || '—'} · 消费：{plan.consumed_at || '—'}</p>
        </div>
      )}
    </div>
  )
}

export default function TopicStateSection() {
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const config = useTopicStateStore((s) => s.config)
  const snapshot = useTopicStateStore((s) => s.snapshot)
  const status = useTopicStateStore((s) => s.status)
  const errorMessage = useTopicStateStore((s) => s.error)
  const saving = useTopicStateStore((s) => s.saving)
  const savedAt = useTopicStateStore((s) => s.savedAt)
  const load = useTopicStateStore((s) => s.load)
  const patch = useTopicStateStore((s) => s.patch)
  const save = useTopicStateStore((s) => s.save)
  const reset = useTopicStateStore((s) => s.reset)
  const scopeKey = useTopicStateStore((s) => s.scopeKey)
  const scopeCharacterId = useTopicStateStore((s) => s.scopeCharacterId)
  const snapshotStatus = useTopicStateStore((s) => s.snapshotStatus)
  const snapshotError = useTopicStateStore((s) => s.snapshotError)
  const topicSnapshot = useTopicStateStore((s) => s.topicSnapshot)
  const loadSnapshot = useTopicStateStore((s) => s.loadSnapshot)

  const [sessionKey, setSessionKey] = useState('')
  const [characterId, setCharacterId] = useState('default')
  // NEW-12（2026-10-29）：用户下拉代替手输会话键（后端按绑定数字 id 还原）
  const [adminUsers, setAdminUsers] = useState<Array<{ id: number; display_name?: string; username?: string; email?: string }>>([])

  useEffect(() => {
    if (!isAdmin) return
    void load(topicStateGetConfig)
    void adminListUsers(1, 100)
      .then(res => setAdminUsers(res.users ?? []))
      .catch(() => setAdminUsers([]))
  }, [isAdmin, load])

  if (!isAdmin) {
    return (
      <Section title="话题状态">
        <p className="text-xs text-gray-500 leading-relaxed">
          话题状态机记录跨轮对话进度（正在聊 / 没聊完 / 已结束）并驱动主动复提，仅管理员可查看与调整。
        </p>
      </Section>
    )
  }

  if (status === 'loading' || status === 'idle') {
    return (
      <Section title="话题状态">
        <div className="flex items-center justify-center py-8">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
        </div>
      </Section>
    )
  }

  if (status === 'error') {
    return (
      <Section title="话题状态">
        <div className="rounded-xl border border-red-200/40 bg-red-50/40 p-5 text-center space-y-3">
          <p className="text-sm text-red-600">{errorMessage || '话题状态配置加载失败'}</p>
          <button
            type="button"
            onClick={() => { void load(topicStateGetConfig) }}
            className="rounded-lg border border-red-200 bg-white/80 px-4 py-1.5 text-xs text-red-600 hover:bg-white transition-colors"
          >
            重试
          </button>
        </div>
      </Section>
    )
  }

  const dirty = topicStateIsDirty(config, snapshot)
  const snapshotEmpty = topicSnapshot
    ? topicSnapshot.active.length === 0
      && topicSnapshot.parked.length === 0
      && topicSnapshot.closed.length === 0
      && !topicSnapshot.next_plan
    : true

  const setTheta1 = (value: number) => patch({ switch_theta1: value })
  const setTheta2 = (value: number) => patch({ switch_theta2: value })
  const querySnapshot = () => { void loadSnapshot(topicStateSnapshot, sessionKey, characterId) }

  return (
    <Section title="话题状态">
      <p className="text-xs text-gray-500 mb-3 leading-relaxed">
        话题状态机按会话记录「正在聊 / 没聊完 / 已结束」，稀疏 LLM 每 4 轮校准一次；
        关闭后主链不注入状态块、主动线不复提。改动即时对全部 worker 生效。
      </p>

      <div className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-gray-700">启用话题状态机</p>
            <p className="text-[11px] text-gray-400 mt-0.5">关闭时六个状态 API 零 DB 访问，prompt 逐字回落旧规则</p>
          </div>
          <Toggle
            checked={config.enabled}
            onChange={(value) => patch({ enabled: value })}
          />
        </div>

        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-gray-700">稀疏 LLM 判定</p>
            <p className="text-[11px] text-gray-400 mt-0.5">每 4 轮后台判定一次，8 秒硬超时，失败保留确定性的原状态</p>
          </div>
          <Toggle
            checked={config.llm_judge_enabled}
            onChange={(value) => patch({ llm_judge_enabled: value })}
          />
        </div>

        <div className="pt-3 border-t border-gray-100 space-y-2">
          <p className="text-xs text-gray-600 font-medium">可调参数</p>
          <NumberRow
            label="并行上限"
            tooltip="同时保留的进行中话题上限（Q6：1–3，不能放宽到 4+）"
            value={config.active_cap}
            min={1}
            max={3}
            step={1}
            unit="条"
            onChange={(value) => patch({ active_cap: value })}
          />
          <NumberRow
            label="复提间隔"
            tooltip="没聊完（parked）话题至少隔多少小时才允许主动复提（Q3：下限 6 小时）"
            value={config.recall_after_hours}
            min={6}
            max={168}
            step={1}
            unit="小时"
            onChange={(value) => patch({ recall_after_hours: value })}
          />
          <NumberRow
            label="复提条数"
            tooltip="单次主动复提最多带几条 parked 候选（Q3：最多 2 条）"
            value={config.recall_max}
            min={1}
            max={2}
            step={1}
            unit="条"
            onChange={(value) => patch({ recall_max: value })}
          />
          {/* V2-3④：白话做主文案、术语降为副标注（θ 缺白话因果 finding） */}
          <NumberRow
            label="算「接着聊」的灵敏度（θ1）"
            tooltip="术语：接续判定阈值——当前消息与 active 标题/锚点的 bigram 短边包含度 ≥ θ1 视为继续。调高=更难算继续话题，调低=更容易顺势聊下去"
            value={config.switch_theta1}
            min={0}
            max={1}
            step={0.05}
            onChange={setTheta1}
          />
          <NumberRow
            label="翻旧话题的门槛（θ2）"
            tooltip="术语：复提判定阈值——与 parked 的重叠达到 θ2 才恢复为 active；不得小于 θ1。调高=更难主动翻旧话题"
            value={config.switch_theta2}
            min={0}
            max={1}
            step={0.05}
            onChange={setTheta2}
          />
        </div>

        <div className="pt-3 border-t border-gray-100 space-y-2">
          <p className="text-xs text-gray-600 font-medium">裁决固定值（只读）</p>
          <FixedRow label="LLM 判定频率" value={config.judge_every_turns} unit="轮/次" />
          <FixedRow label="每日 LLM 预算" value={config.daily_llm_budget} unit="（0=不限）" />
          <FixedRow label="状态保留期" value={config.retention_days} unit="天" />
          <FixedRow label="空闲转「没聊完」" value={config.park_idle_hours} unit="小时" />
        </div>

        {/* 会话查看区：作用域必须显式（完整键或可确定性还原的数字 user_id） */}
        <div className="pt-3 border-t border-gray-100 space-y-2" data-testid="topic-state-scope">
          <p className="text-sm text-gray-700">查看会话话题</p>
          <p className="text-[11px] text-gray-400">
            选择用户（后端按绑定自动还原会话键，宁拒绝不猜键）；完整键可走下方高级输入。
          </p>
          <div className="flex flex-col sm:flex-row gap-2">
            <select
              aria-label="选择用户"
              value={/^\d+$/.test(sessionKey) ? sessionKey : ''}
              onChange={(e) => setSessionKey(e.target.value === '' ? '' : e.target.value)}
              className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/50"
            >
              <option value="">选择用户或手输…</option>
              {adminUsers.map(u => (
                <option key={u.id} value={u.id}>
                  {u.display_name || u.username || u.email || `#${u.id}`}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={/^\d+$/.test(sessionKey) ? '' : sessionKey}
              onChange={(e) => setSessionKey(e.target.value)}
              placeholder="高级：2:wxid@im.wechat"
              aria-label="会话键"
              data-testid="topic-state-session-key"
              className="flex-1 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-mono focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/50"
            />
            <input
              type="text"
              value={characterId}
              onChange={(e) => setCharacterId(e.target.value)}
              placeholder="default"
              aria-label="角色 ID"
              data-testid="topic-state-character-id"
              className="w-32 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-mono focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/50"
            />
            <button
              type="button"
              onClick={querySnapshot}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50 transition-colors"
            >
              查看该会话
            </button>
          </div>
          <p className="text-[11px] text-gray-400">
            当前作用域：{scopeKey ? `会话 ${scopeKey}` : '未查询'} / 角色 {scopeCharacterId || 'default'}
          </p>
        </div>

        {/* 只读快照 */}
        <div className="rounded-xl border border-gray-200/70 bg-gray-50/40 p-3 space-y-3">
          {snapshotStatus === 'idle' && (
            <p className="text-[11px] text-gray-400">输入会话键后点「查看该会话」，这里会显示该会话的话题快照。</p>
          )}
          {snapshotStatus === 'loading' && (
            <div className="flex items-center justify-center py-6">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
            </div>
          )}
          {snapshotStatus === 'error' && (
            <div className="rounded-lg border border-red-200/50 bg-red-50/40 p-3 space-y-2">
              <p className="text-xs text-red-600">{snapshotError || '话题快照读取失败'}</p>
              <button
                type="button"
                onClick={querySnapshot}
                className="rounded-lg border border-red-200 bg-white/80 px-3 py-1 text-xs text-red-600 hover:bg-white transition-colors"
              >
                重试
              </button>
            </div>
          )}
          {snapshotStatus === 'ready' && topicSnapshot && (
            <div className="space-y-3" data-testid="topic-state-snapshot">
              {!topicSnapshot.enabled && (
                <p className="text-[11px] text-amber-600">
                  话题状态机当前未启用：不会有新的状态写入；下方是历史残留（如有）。
                </p>
              )}
              {topicSnapshot.degraded && (
                <p className="text-[11px] text-amber-600">状态库暂不可读，当前快照已降级为空。</p>
              )}
              <TopicList title="正在聊" items={topicSnapshot.active} showCounts />
              <TopicList title="没聊完" items={topicSnapshot.parked} />
              <TopicList title="已结束" items={topicSnapshot.closed} />
              <PlanBlock plan={topicSnapshot.next_plan} />
              {snapshotEmpty && (
                <p className="text-[11px] text-gray-400">当前没有记录。</p>
              )}
            </div>
          )}
        </div>

        {/* 保存 / 撤销 */}
        <div className="flex items-center justify-end gap-2">
          {dirty && (
            <span className="rounded-lg bg-amber-50 border border-amber-100 px-2 py-1 text-[11px] text-amber-700">
              未保存
            </span>
          )}
          <button
            type="button"
            onClick={reset}
            disabled={!dirty || saving}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-40"
          >
            撤销修改
          </button>
          <button
            type="button"
            onClick={() => { void save(updateTopicStateConfig) }}
            disabled={saving || !dirty}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors shadow-sm disabled:opacity-50"
          >
            {saving ? '保存中…' : savedAt && !dirty ? `已保存（${savedAt}）` : '保存话题状态配置'}
          </button>
        </div>
      </div>
    </Section>
  )
}

/** 供测试/调用方复用 */
export type { TopicStateConfig }
