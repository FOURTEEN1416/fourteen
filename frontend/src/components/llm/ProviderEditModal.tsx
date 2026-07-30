/**
 * 供应商编辑/创建弹窗
 *
 * mode="create": 创建新供应商（需要填 key）
 * mode="edit":   编辑现有供应商（key 只读）
 */
import { useState, useEffect } from 'react'
import Modal from '../shared/Modal'
import type { ProviderOption, ProviderUpdateRequest, ProviderCreateRequest, ProviderGuide } from '../../api/llmProviders'
import { Plus, X } from 'lucide-react'

interface ProviderEditModalProps {
  open: boolean
  mode: 'create' | 'edit'
  initialData?: ProviderOption | null
  saving?: boolean
  onSubmit: (data: ProviderUpdateRequest | ProviderCreateRequest) => void
  onClose: () => void
}

const EMPTY_GUIDE: ProviderGuide = { apply_url: '', free_quota: '', steps: [], tips: [], warnings: [] }

const EMPTY_FORM: ProviderUpdateRequest = {
  name: '', model: '', api_base: '', api_key: '',
  auth_mode: 'bearer', max_tokens: 2048, temperature: 0.85,
  stream_enabled: true, description: '', enabled: true, sort_order: 50,
  guide: { ...EMPTY_GUIDE },
}

export default function ProviderEditModal({
  open, mode, initialData, saving, onSubmit, onClose,
}: ProviderEditModalProps) {
  const [form, setForm] = useState<ProviderUpdateRequest>(EMPTY_FORM)
  const [createKey, setCreateKey] = useState('')
  const [keyError, setKeyError] = useState('')

  // 初始化/重置表单
  /* eslint-disable react-hooks/set-state-in-effect -- reset on open/initialData change */
  useEffect(() => {
    if (!open) return
    if (mode === 'edit' && initialData) {
      const guide = initialData.guide ?? { ...EMPTY_GUIDE }
      setForm({
        name: initialData.name ?? '',
        model: initialData.model ?? '',
        api_base: initialData.api_base ?? '',
        api_key: initialData.api_key ?? '',  // 脱敏后的 ****
        auth_mode: (initialData.auth_mode as 'bearer' | 'oauth') ?? 'bearer',
        max_tokens: initialData.max_tokens ?? 2048,
        temperature: initialData.temperature ?? 0.85,
        stream_enabled: initialData.stream_enabled ?? true,
        description: initialData.description ?? '',
        enabled: initialData.enabled ?? true,
        sort_order: initialData.sort_order ?? 50,
        guide: {
          apply_url: guide.apply_url ?? '',
          free_quota: guide.free_quota ?? '',
          steps: [...(guide.steps ?? [])],
          tips: [...(guide.tips ?? [])],
          warnings: [...(guide.warnings ?? [])],
        },
        extra_payload: initialData.extra_payload ?? undefined,
      })
    } else {
      setForm({ ...EMPTY_FORM, guide: { ...EMPTY_GUIDE } })
      setCreateKey('')
      setKeyError('')
    }
  }, [open, mode, initialData])
  /* eslint-enable react-hooks/set-state-in-effect */

  const isSpecial = mode === 'edit' && initialData?.is_special

  const handleSubmit = () => {
    // 创建模式需要校验 key
    if (mode === 'create') {
      if (!createKey.trim()) {
        setKeyError('请填写供应商 key')
        return
      }
      if (!/^[a-z][a-z0-9_]*$/.test(createKey)) {
        setKeyError('key 必须为小写字母开头，只含小写字母/数字/下划线')
        return
      }
      setKeyError('')
      onSubmit({ ...form, key: createKey } as ProviderCreateRequest)
    } else {
      onSubmit(form)
    }
  }

  // ── 数组字段编辑器（steps/tips/warnings） ──
  const updateListField = (field: 'steps' | 'tips' | 'warnings', idx: number, value: string) => {
    setForm(prev => {
      const arr = [...(prev.guide?.[field] ?? [])]
      arr[idx] = value
      return { ...prev, guide: { ...prev.guide!, [field]: arr } }
    })
  }
  const addListItem = (field: 'steps' | 'tips' | 'warnings') => {
    setForm(prev => ({
      ...prev,
      guide: { ...prev.guide!, [field]: [...(prev.guide?.[field] ?? []), ''] },
    }))
  }
  const removeListItem = (field: 'steps' | 'tips' | 'warnings', idx: number) => {
    setForm(prev => {
      const arr = [...(prev.guide?.[field] ?? [])]
      arr.splice(idx, 1)
      return { ...prev, guide: { ...prev.guide!, [field]: arr } }
    })
  }

  return (
    <Modal
      open={open}
      title={mode === 'create' ? '添加新供应商' : `编辑：${initialData?.name ?? ''}`}
      onClose={onClose}
      size="lg"
    >
      <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
        {/* ── 基础信息 ── */}
        <section className="space-y-3">
          <h4 className="text-xs font-semibold text-gray-700 border-b border-gray-100 pb-1">基础信息</h4>

          {mode === 'create' && (
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                供应商 Key <span className="text-rose-500">*</span>
                <span className="text-gray-400 ml-1">（小写字母+数字+下划线，创建后不可改）</span>
              </label>
              <input
                type="text"
                value={createKey}
                onChange={(e) => { setCreateKey(e.target.value); setKeyError('') }}
                placeholder="如 groq, moonshot, siliconflow"
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 font-mono"
              />
              {keyError && <p className="mt-1 text-[10px] text-rose-500">{keyError}</p>}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-600 mb-1">显示名称 <span className="text-rose-500">*</span></label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="如 Groq"
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">描述</label>
              <input
                type="text"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="如 Groq 高速推理"
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
              />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-gray-600 mb-1">排序</label>
              <input
                type="number"
                value={form.sort_order}
                onChange={(e) => setForm({ ...form, sort_order: parseInt(e.target.value) || 50 })}
                min={0}
                max={999}
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 text-center"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">启用</label>
              <select
                value={form.enabled ? '1' : '0'}
                onChange={(e) => setForm({ ...form, enabled: e.target.value === '1' })}
                disabled={isSpecial}
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
              >
                <option value="1">启用</option>
                <option value="0">禁用</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">认证模式</label>
              <select
                value={form.auth_mode}
                onChange={(e) => setForm({ ...form, auth_mode: e.target.value as 'bearer' | 'oauth' })}
                disabled={isSpecial}
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
              >
                <option value="bearer">Bearer Token</option>
                <option value="oauth">OAuth</option>
              </select>
            </div>
          </div>
        </section>

        {/* ── 连接参数（特殊选项不显示） ── */}
        {!isSpecial && (
          <section className="space-y-3">
            <h4 className="text-xs font-semibold text-gray-700 border-b border-gray-100 pb-1">连接参数</h4>
            <div>
              <label className="block text-xs text-gray-600 mb-1">API 地址</label>
              <input
                type="text"
                value={form.api_base}
                onChange={(e) => setForm({ ...form, api_base: e.target.value })}
                placeholder="https://api.example.com/v1"
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                API Key
                {mode === 'edit' && (
                  <span className="text-gray-400 ml-1">（显示 **** 表示保留原值，留空也保留原值）</span>
                )}
              </label>
              <input
                type="text"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder="sk-..."
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 font-mono"
              />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-gray-600 mb-1">模型名</label>
                <input
                  type="text"
                  value={form.model}
                  onChange={(e) => setForm({ ...form, model: e.target.value })}
                  placeholder="llama3, glm-5.2"
                  className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-600 mb-1">Max Tokens</label>
                <input
                  type="number"
                  value={form.max_tokens}
                  onChange={(e) => setForm({ ...form, max_tokens: parseInt(e.target.value) || 2048 })}
                  min={256}
                  max={32768}
                  step={256}
                  className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 text-center"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-600 mb-1">温度</label>
                <input
                  type="number"
                  value={form.temperature}
                  onChange={(e) => setForm({ ...form, temperature: parseFloat(e.target.value) || 0.85 })}
                  min={0}
                  max={2}
                  step={0.05}
                  className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 text-center"
                />
              </div>
            </div>
          </section>
        )}

        {/* ── 申请教程 ── */}
        <section className="space-y-3">
          <h4 className="text-xs font-semibold text-gray-700 border-b border-gray-100 pb-1">申请教程</h4>

          <div className="grid grid-cols-1 gap-3">
            <div>
              <label className="block text-xs text-gray-600 mb-1">申请地址</label>
              <input
                type="text"
                value={form.guide?.apply_url ?? ''}
                onChange={(e) => setForm({ ...form, guide: { ...form.guide!, apply_url: e.target.value } })}
                placeholder="https://platform.example.com"
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50 font-mono"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">免费额度说明</label>
              <input
                type="text"
                value={form.guide?.free_quota ?? ''}
                onChange={(e) => setForm({ ...form, guide: { ...form.guide!, free_quota: e.target.value } })}
                placeholder="如 永久免费 / 每月 50 万 Token"
                className="w-full px-3 py-1.5 text-xs input-macaron rounded-lg outline-none focus:ring-2 focus:ring-primary-400/50"
              />
            </div>
          </div>

          {/* 步骤列表 */}
          <ListEditor
            label="操作步骤"
            items={form.guide?.steps ?? []}
            onChange={(idx, v) => updateListField('steps', idx, v)}
            onAdd={() => addListItem('steps')}
            onRemove={(idx) => removeListItem('steps', idx)}
            placeholder="如：打开 xxx 网站，注册账号..."
          />

          {/* 提示列表 */}
          <ListEditor
            label="小提示"
            items={form.guide?.tips ?? []}
            onChange={(idx, v) => updateListField('tips', idx, v)}
            onAdd={() => addListItem('tips')}
            onRemove={(idx) => removeListItem('tips', idx)}
            placeholder="如：支持 200K 上下文..."
          />

          {/* 警告列表 */}
          <ListEditor
            label="注意事项"
            items={form.guide?.warnings ?? []}
            onChange={(idx, v) => updateListField('warnings', idx, v)}
            onAdd={() => addListItem('warnings')}
            onRemove={(idx) => removeListItem('warnings', idx)}
            placeholder="如：付费服务，使用前需充值..."
          />
        </section>

        {/* ── 操作按钮 ── */}
        <div className="flex justify-end gap-2 pt-2 border-t border-gray-100">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-xs font-medium text-gray-600 bg-white/60 hover:bg-white/80 border border-white/30 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !form.name.trim()}
            className="px-4 py-1.5 text-xs font-medium text-white bg-primary-500 hover:bg-primary-600 disabled:bg-gray-300 rounded-lg transition-colors"
          >
            {saving ? '保存中...' : mode === 'create' ? '创建' : '保存'}
          </button>
        </div>
      </div>
    </Modal>
  )
}

// ── 子组件：数组字段编辑器 ──
function ListEditor({
  label, items, onChange, onAdd, onRemove, placeholder,
}: {
  label: string
  items: string[]
  onChange: (idx: number, value: string) => void
  onAdd: () => void
  onRemove: (idx: number) => void
  placeholder?: string
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-xs text-gray-600">{label}</label>
        <button
          onClick={onAdd}
          className="inline-flex items-center gap-0.5 px-2 py-0.5 text-[10px] font-medium text-primary-600 bg-primary-50 hover:bg-primary-100 rounded transition-colors"
        >
          <Plus className="w-3 h-3" />
          添加
        </button>
      </div>
      {items.length === 0 ? (
        <p className="text-[11px] text-gray-400 italic">暂无内容，点击"添加"开始</p>
      ) : (
        <div className="space-y-1.5">
          {items.map((item, idx) => (
            <div key={idx} className="flex items-start gap-1.5">
              <span className="shrink-0 mt-1.5 text-[10px] font-bold text-gray-400 w-4">{idx + 1}.</span>
              <textarea
                value={item}
                onChange={(e) => onChange(idx, e.target.value)}
                placeholder={placeholder}
                rows={1}
                className="flex-1 px-2 py-1 text-xs input-macaron rounded outline-none focus:ring-1 focus:ring-primary-400/50 resize-y min-h-[28px]"
              />
              <button
                onClick={() => onRemove(idx)}
                className="shrink-0 mt-1 p-0.5 text-gray-300 hover:text-rose-500 transition-colors"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
