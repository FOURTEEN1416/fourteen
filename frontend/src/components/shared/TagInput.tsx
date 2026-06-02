import { useState, type KeyboardEvent } from 'react'
import { X } from 'lucide-react'

interface TagInputProps {
  tags: string[]
  placeholder?: string
  onChange: (tags: string[]) => void
}

export default function TagInput({ tags, placeholder = '输入后回车添加', onChange }: TagInputProps) {
  const [input, setInput] = useState('')

  const add = () => {
    const v = input.trim()
    if (v && !tags.includes(v)) {
      onChange([...tags, v])
    }
    setInput('')
  }

  const remove = (idx: number) => {
    onChange(tags.filter((_, i) => i !== idx))
  }

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') { e.preventDefault(); add() }
    if (e.key === 'Backspace' && !input && tags.length > 0) {
      remove(tags.length - 1)
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5 glass-card rounded-lg min-h-[36px]">
      {tags.map((tag) => (
        <span key={tag} className="inline-flex items-center gap-1 bg-primary-100/50 text-primary-700 text-xs rounded-md px-2 py-0.5">
          {tag}
          <button onClick={() => remove(tags.indexOf(tag))} className="hover:text-primary-500 transition-colors">
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKey}
        onBlur={add}
        placeholder={tags.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[80px] text-xs outline-none bg-transparent text-gray-700 placeholder:text-gray-300"
      />
    </div>
  )
}
