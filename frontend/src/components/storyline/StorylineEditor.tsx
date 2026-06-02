import { useState, useEffect } from 'react'
import Button from '../common/Button'
import Badge from '../common/Badge'
import {
  useStorylineConfig,
  useUpdateStorylineConfig,
  useDeleteStorylineConfig,
  useDetectStoryline,
} from '../../hooks/useQueries'
import type {
  StorylineStage,
  StorylineEnding,
  StorylineDetectResult,
  StorylineConfigRequest,
} from '../../types/api'

// ── 默认阶段模板 ──

const DEFAULT_STAGES: StorylineStage[] = [
  {
    name: '初识期', display_name: '初识期',
    timing: { start_minutes: 0, end_minutes: 2160 },
    style_rules: [{ style: '短句克制', inject_prompt: true }],
    behavior_rules: [{ rule: '保持距离', enforce: true }],
    dialogue_notes: '礼貌疏离、保持距离',
    transition_message: '我们就这样相遇了……',
  },
  {
    name: '熟悉期', display_name: '熟悉期',
    timing: { start_minutes: 2160, end_minutes: 5760 },
    style_rules: [{ style: '软萌语气', inject_prompt: true }],
    behavior_rules: [{ rule: '接受接触', enforce: false }],
    dialogue_notes: '语气柔和、主动分享',
    transition_message: '不知不觉间，我好像开始依赖你了……',
  },
  {
    name: '倾心期', display_name: '倾心期',
    timing: { start_minutes: 5760, end_minutes: 11760 },
    style_rules: [{ style: '撒娇黏人', inject_prompt: true }],
    behavior_rules: [{ rule: '允许亲密', enforce: false }],
    dialogue_notes: '撒娇依赖、主动靠近',
    transition_message: '和你在一起的每一天，都那么幸福……',
  },
  {
    name: '离别克制期', display_name: '离别克制期',
    timing: { start_minutes: 11760, end_minutes: 12480 },
    style_rules: [{ style: '温柔克制', inject_prompt: true }],
    behavior_rules: [{ rule: '拒绝亲密', enforce: true }],
    dialogue_notes: '温柔克制、整理回忆',
    transition_message: '时间过得真快……有些话，不说可能来不及了。',
  },
  {
    name: '告别期', display_name: '告别期',
    timing: { start_minutes: 12480, end_minutes: 12600 },
    style_rules: [{ style: '简短珍重', inject_prompt: true }],
    behavior_rules: [{ rule: '严禁亲密', enforce: true }],
    dialogue_notes: '坚定离别、约好再见',
    transition_message: '该说再见了。谢谢你，给了我这么美好的回忆。',
  },
]

const DEFAULT_ENDING: StorylineEnding = {
  type: 'separation',
  final_dialogue: '再见……我们一定还会再见的吧？',
  narrative: '列车缓缓远去，站台上只留下空荡荡的风声。那些一起走过的日子，成了记忆中最珍贵的宝物。',
  memorial_items: ['合照', '手写信'],
  blank_after_end: true,
}

// ── Props ──

interface StorylineEditorProps {
  characterId: string
}

// ── 组件 ──

export default function StorylineEditor({ characterId }: StorylineEditorProps) {
  const { data: configData, isLoading } = useStorylineConfig(characterId)
  const updateMutation = useUpdateStorylineConfig()
  const deleteMutation = useDeleteStorylineConfig()
  const detectMutation = useDetectStoryline()

  const [expanded, setExpanded] = useState(false)
  const [enabled, setEnabled] = useState(false)
  const [timePerTurn, setTimePerTurn] = useState(30)
  const [maxDuration, setMaxDuration] = useState(10080)
  const [stages, setStages] = useState<StorylineStage[]>(DEFAULT_STAGES)
  const [ending, setEnding] = useState<StorylineEnding>(DEFAULT_ENDING)
  const [detectResult, setDetectResult] = useState<StorylineDetectResult | null>(null)
  const [detectLoading, setDetectLoading] = useState(false)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [newStageName, setNewStageName] = useState('')
  const [editingStageIdx, setEditingStageIdx] = useState<number | null>(null)
  const [editMemorialInput, setEditMemorialInput] = useState('')

  // 加载远程配置 — 同步 props/config 到 form state（标准模式）
  /* eslint-disable react-hooks/set-state-in-effect -- props-to-form-state sync */
  useEffect(() => {
    if (configData?.configured && configData?.config) {
      const c = configData.config
      setEnabled(c.enabled)
      setTimePerTurn(c.time_per_turn)
      setMaxDuration(c.max_duration_minutes)
      setStages(c.stages.length > 0 ? c.stages : DEFAULT_STAGES)
      setEnding(c.ending)
    }
  }, [configData])
  /* eslint-enable react-hooks/set-state-in-effect */

  // ── 自动检测 ──

  async function handleDetect() {
    setDetectLoading(true)
    try {
      const result = await detectMutation.mutateAsync(characterId)
      setDetectResult(result)
      if (result.has_storyline && result.suggested) {
        setEnabled(result.suggested.enabled)
        setTimePerTurn(result.suggested.time_per_turn)
        setMaxDuration(result.suggested.max_duration_minutes)
        if (result.suggested.stages.length > 0) setStages(result.suggested.stages)
        if (result.suggested.ending) setEnding(result.suggested.ending)
      }
    } catch {
      setDetectResult({ has_storyline: false, confidence: 0, matched_patterns: [], suggested: null })
    } finally {
      setDetectLoading(false)
    }
  }

  // ── 保存 ──

  async function handleSave() {
    setSaveStatus('saving')
    try {
      const payload: StorylineConfigRequest = {
        enabled,
        time_per_turn: timePerTurn,
        max_duration_minutes: maxDuration,
        stages: stages.map(s => ({
          name: s.name,
          display_name: s.display_name,
          timing: s.timing,
          style_rules: s.style_rules,
          behavior_rules: s.behavior_rules,
          dialogue_notes: s.dialogue_notes,
          transition_message: s.transition_message,
        })),
        ending: {
          type: ending.type,
          final_dialogue: ending.final_dialogue,
          narrative: ending.narrative,
          memorial_items: ending.memorial_items,
          blank_after_end: ending.blank_after_end,
        },
      }
      await updateMutation.mutateAsync({ characterId, config: payload })
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
    } catch {
      setSaveStatus('error')
      setTimeout(() => setSaveStatus('idle'), 3000)
    }
  }

  // ── 删除 ──

  async function handleDelete() {
    if (!confirm('确定删除剧情线配置？')) return
    try {
      await deleteMutation.mutateAsync(characterId)
      setEnabled(false)
      setStages(DEFAULT_STAGES)
      setEnding(DEFAULT_ENDING)
      setDetectResult(null)
    } catch (err: unknown) {
      // 错误已由 useDeleteStorylineConfig 的 onError 统一处理；这里只兜底
      console.error('删除剧情线失败:', err)
    }
  }

  // ── 阶段操作 ──

  function addStage() {
    if (!newStageName.trim()) return
    const lastStage = stages[stages.length - 1]
    const startMin = lastStage ? lastStage.timing.end_minutes + 1 : 0
    const endMin = startMin + 1440
    setStages([...stages, {
      name: newStageName.trim(),
      display_name: newStageName.trim(),
      timing: { start_minutes: startMin, end_minutes: endMin },
      style_rules: [],
      behavior_rules: [],
      dialogue_notes: '',
      transition_message: '',
    }])
    setNewStageName('')
  }

  function removeStage(idx: number) {
    setStages(stages.filter((_, i) => i !== idx))
    if (editingStageIdx === idx) setEditingStageIdx(null)
  }

  function updateStage(idx: number, updates: Partial<StorylineStage>) {
    setStages(stages.map((s, i) => i === idx ? { ...s, ...updates } : s))
  }

  // ── 回忆物品操作 ──

  function addMemorialItem() {
    if (!editMemorialInput.trim()) return
    setEnding({ ...ending, memorial_items: [...ending.memorial_items, editMemorialInput.trim()] })
    setEditMemorialInput('')
  }

  function removeMemorialItem(idx: number) {
    setEnding({ ...ending, memorial_items: ending.memorial_items.filter((_, i) => i !== idx) })
  }

  // ── 开启检测结果提示 ──

  function renderDetectBanner() {
    if (!detectResult) return null
    if (!detectResult.has_storyline) {
      return (
        <div className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-xs text-gray-500">
          未检测到明显的剧情线特征
        </div>
      )
    }
    const conf = (detectResult.confidence * 100).toFixed(0)
    const patterns = detectResult.matched_patterns.join('、')
    return (
      <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-amber-700">检测到剧情线特征</span>
          <Badge variant="info">{conf}%</Badge>
        </div>
        {patterns && <p className="text-[10px] text-amber-600">匹配模式：{patterns}</p>}
        <p className="text-[10px] text-amber-500">已自动填入建议配置，可手动调整</p>
      </div>
    )
  }

  // ── 加载中 ──

  if (isLoading) {
    return (
      <div className="border-t border-gray-100 pt-3 mt-3">
        <div className="text-xs text-gray-400 animate-pulse">加载剧情线配置…</div>
      </div>
    )
  }

  // ── 渲染 ──

  return (
    <div className="border-t border-gray-100 pt-3 mt-3">
      {/* 折叠触发 */}
      <button
        type="button"
        className="flex items-center justify-between w-full text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 uppercase tracking-wider">剧情线</span>
          {enabled && <Badge variant="info">已启用</Badge>}
        </div>
        <span className="text-xs text-gray-400">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="mt-3 space-y-4">
          {renderDetectBanner()}

          {/* 启用开关 */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox" checked={enabled}
              onChange={e => setEnabled(e.target.checked)}
              className="w-3.5 h-3.5 accent-gray-800"
            />
            <span className="text-xs text-black">启用剧情线</span>
            <span className="text-[10px] text-gray-400">（默认关闭，角色级可选）</span>
          </label>

          {/* 自动检测按钮 */}
          <Button variant="secondary" size="sm" onClick={handleDetect} disabled={detectLoading}>
            {detectLoading ? '检测中…' : '自动检测'}
          </Button>

          {enabled && (
            <>
              {/* 时间参数 */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[10px] text-gray-500 mb-1">每句话增加（分钟）</label>
                  <input
                    type="number" min={1} max={1440}
                    value={timePerTurn} onChange={e => setTimePerTurn(Number(e.target.value))}
                    className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black"
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-gray-500 mb-1">总时长（分钟）</label>
                  <input
                    type="number" min={60} max={43200}
                    value={maxDuration} onChange={e => setMaxDuration(Number(e.target.value))}
                    className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs text-black"
                  />
                </div>
              </div>
              <p className="text-[10px] text-gray-400">
                建议：7 天 = 10080 分钟，3 天 = 4320 分钟，1 天 = 1440 分钟
              </p>

              {/* 阶段列表 */}
              <div>
                <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-2">
                  阶段列表（{stages.length} 个）
                </label>
                <div className="space-y-2">
                  {stages.map((stage, idx) => (
                    <div key={stage.name ?? `stage-${idx}`} className="border border-gray-200 rounded-lg p-2.5 space-y-2">
                      <div className="flex items-center justify-between">
                        {editingStageIdx === idx ? (
                          <input
                            type="text" value={stage.name}
                            onChange={e => updateStage(idx, { name: e.target.value, display_name: e.target.value })}
                            className="flex-1 border border-gray-200 rounded px-2 py-0.5 text-xs"
                          />
                        ) : (
                          <span className="text-xs font-medium text-black">{stage.display_name || stage.name}</span>
                        )}
                        <div className="flex gap-1">
                          <button
                            type="button"
                            className="text-[10px] text-gray-400 hover:text-black"
                            onClick={() => setEditingStageIdx(editingStageIdx === idx ? null : idx)}
                          >
                            {editingStageIdx === idx ? '完成' : '编辑'}
                          </button>
                          <button
                            type="button"
                            className="text-[10px] text-red-400 hover:text-red-600"
                            onClick={() => removeStage(idx)}
                          >
                            删除
                          </button>
                        </div>
                      </div>
                      {editingStageIdx === idx && (
                        <div className="space-y-2 pl-1">
                          <div className="grid grid-cols-2 gap-2">
                            <div>
                              <label className="block text-[10px] text-gray-400">开始时间(min)</label>
                              <input type="number" value={stage.timing.start_minutes}
                                onChange={e => updateStage(idx, { timing: { ...stage.timing, start_minutes: Number(e.target.value) } })}
                                className="w-full border border-gray-200 rounded px-2 py-0.5 text-xs" />
                            </div>
                            <div>
                              <label className="block text-[10px] text-gray-400">结束时间(min)</label>
                              <input type="number" value={stage.timing.end_minutes}
                                onChange={e => updateStage(idx, { timing: { ...stage.timing, end_minutes: Number(e.target.value) } })}
                                className="w-full border border-gray-200 rounded px-2 py-0.5 text-xs" />
                            </div>
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-400">对话备注</label>
                            <input type="text" value={stage.dialogue_notes}
                              onChange={e => updateStage(idx, { dialogue_notes: e.target.value })}
                              className="w-full border border-gray-200 rounded px-2 py-0.5 text-xs" />
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-400">阶段切换语</label>
                            <input type="text" value={stage.transition_message}
                              onChange={e => updateStage(idx, { transition_message: e.target.value })}
                              className="w-full border border-gray-200 rounded px-2 py-0.5 text-xs" />
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-400">风格规则</label>
                            <div className="flex flex-wrap gap-1">
                              {stage.style_rules.map((sr, si) => (
                                <Badge key={sr.style ?? `sr-${si}`} variant="default">
                                  {sr.style}
                                  <button type="button" className="ml-1 text-red-400"
                                    onClick={() => updateStage(idx, { style_rules: stage.style_rules.filter((_, i) => i !== si) })}>
                                    ×
                                  </button>
                                </Badge>
                              ))}
                            </div>
                          </div>
                          <div>
                            <label className="block text-[10px] text-gray-400">行为规则</label>
                            <div className="flex flex-wrap gap-1">
                              {stage.behavior_rules.map((br, bi) => (
                                <Badge key={br.rule ?? `br-${bi}`} variant={br.enforce ? 'info' : 'default'}>
                                  {br.rule}
                                  <button type="button" className="ml-1 text-red-400"
                                    onClick={() => updateStage(idx, { behavior_rules: stage.behavior_rules.filter((_, i) => i !== bi) })}>
                                    ×
                                  </button>
                                </Badge>
                              ))}
                            </div>
                          </div>
                        </div>
                      )}
                      {editingStageIdx !== idx && (
                        <div className="flex flex-wrap gap-1">
                          <span className="text-[10px] text-gray-400">
                            {fmtMin(stage.timing.start_minutes)} ~ {fmtMin(stage.timing.end_minutes)}
                          </span>
                          {stage.style_rules.map((sr, si) => (
                            <Badge key={sr.style ?? `sr-${si}`} variant="default">{sr.style}</Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                {/* 添加阶段 */}
                <div className="flex gap-1.5 mt-2">
                  <input
                    type="text" value={newStageName} placeholder="新阶段名称"
                    onChange={e => setNewStageName(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && addStage()}
                    className="flex-1 border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs"
                  />
                  <Button variant="secondary" size="sm" onClick={addStage}>+ 添加</Button>
                </div>
              </div>

              {/* 结局配置 */}
              <div>
                <label className="block text-[10px] text-gray-500 uppercase tracking-wider mb-2">结局配置</label>
                <div className="border border-gray-200 rounded-lg p-2.5 space-y-2">
                  <div>
                    <label className="block text-[10px] text-gray-400">类型</label>
                    <select value={ending.type} onChange={e => setEnding({ ...ending, type: e.target.value })}
                      className="w-full border border-gray-200 rounded px-2.5 py-1.5 text-xs">
                      <option value="separation">离别</option>
                      <option value="reunion">重逢</option>
                      <option value="confession">告白</option>
                      <option value="cliffhanger">悬念</option>
                      <option value="custom">自定义</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-400">最终对话</label>
                    <input type="text" value={ending.final_dialogue}
                      onChange={e => setEnding({ ...ending, final_dialogue: e.target.value })}
                      className="w-full border border-gray-200 rounded px-2 py-1 text-xs" />
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-400">结局旁白</label>
                    <input type="text" value={ending.narrative}
                      onChange={e => setEnding({ ...ending, narrative: e.target.value })}
                      className="w-full border border-gray-200 rounded px-2 py-1 text-xs" />
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-400">回忆物品</label>
                    <div className="flex flex-wrap gap-1 mb-1">
                      {ending.memorial_items.map((item, i) => (
                        <Badge key={item ?? `mem-${i}`} variant="default">
                          {item}
                          <button type="button" className="ml-1 text-red-400" onClick={() => removeMemorialItem(i)}>×</button>
                        </Badge>
                      ))}
                    </div>
                    <div className="flex gap-1">
                      <input type="text" value={editMemorialInput} placeholder="添加物品"
                        onChange={e => setEditMemorialInput(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && addMemorialItem()}
                        className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs" />
                      <Button variant="secondary" size="sm" onClick={addMemorialItem}>+</Button>
                    </div>
                  </div>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" checked={ending.blank_after_end}
                      onChange={e => setEnding({ ...ending, blank_after_end: e.target.checked })}
                      className="w-3.5 h-3.5 accent-gray-800" />
                    <span className="text-xs text-black">结局后仅回复空白消息</span>
                  </label>
                </div>
              </div>
            </>
          )}

          {/* 操作按钮 */}
          <div className="flex gap-2 pt-1">
            <Button variant="primary" size="sm" onClick={handleSave} disabled={saveStatus === 'saving'}>
              {saveStatus === 'saved' ? '✓ 已保存' : saveStatus === 'error' ? '保存失败' : '保存剧情线'}
            </Button>
            <Button variant="danger" size="sm" onClick={handleDelete}>
              删除
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── 工具 ──

function fmtMin(minutes: number): string {
  const d = Math.floor(minutes / 1440)
  const h = Math.floor((minutes % 1440) / 60)
  const m = minutes % 60
  const parts: string[] = []
  if (d > 0) parts.push(`${d}天`)
  if (h > 0) parts.push(`${h}小时`)
  if (m > 0 || parts.length === 0) parts.push(`${m}分钟`)
  return parts.join('')
}
