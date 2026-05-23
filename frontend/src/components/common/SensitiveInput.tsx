import { useState } from 'react'

interface SensitiveInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
}

export default function SensitiveInput({ value, onChange, placeholder = '', className = '' }: SensitiveInputProps) {
  const [visible, setVisible] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)

  const handleBlur = () => {
    setEditing(false)
    if (draft !== value) onChange(draft)
  }

  const handleFocus = () => {
    setEditing(true)
    setDraft(value)
  }

  return (
    <div className={`flex items-center gap-1 ${className}`}>
      <input
        type={visible || editing ? 'text' : 'password'}
        value={editing ? draft : (value || '')}
        placeholder={placeholder}
        onChange={(e) => { setDraft(e.target.value); if (!editing) onChange(e.target.value) }}
        onFocus={handleFocus}
        onBlur={handleBlur}
        className="flex-1 bg-gray-200/60 border border-gray-300/50 rounded px-2 py-1 text-xs text-gray-800 outline-none focus:border-primary-500/50"
      />
      <button
        type="button"
        onClick={() => setVisible(!visible)}
        className="text-xs text-gray-400 hover:text-gray-700 px-1 shrink-0"
        title={visible ? '隐藏' : '显示'}
      >
        {visible ? '🙈' : '👁'}
      </button>
    </div>
  )
}
