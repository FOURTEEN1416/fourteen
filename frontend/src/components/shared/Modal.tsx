import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'

interface ModalProps {
  open: boolean
  title: string
  children: ReactNode
  onClose: () => void
  size?: 'sm' | 'md' | 'lg'
}

const sizeMap = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl' }

/**
 * 模块级「已打开弹窗」登记表：ESC 只由最上层实例处理。
 *
 * 用途：确认框嵌套在编辑弹窗内时（如用户编辑弹窗里叠确认停用），ESC 只应关掉最上层的
 * 确认框，而不是把两层一起关掉。层级按 DOM 嵌套深度判定——本组件不用 portal，
 * 被嵌套渲染的弹窗，其 overlay 是外层 overlay 的后代，深度更大即更靠上。
 * （不能按注册先后判定：React 的 effect 自底向上执行，子弹窗会先于父弹窗登记。）
 *
 * 栈项在弹窗关闭或**父组件直接卸载**时都会出栈，避免残留项影响后续弹窗。
 *
 * 边界：layout/MobileDrawer 自带独立 ESC 监听、不经过 Modal，「抽屉 + 弹窗同开」仍会双响应
 * （既有行为，未在本登记表范围内收敛）。
 */
interface OpenModalEntry {
  id: symbol
  depth: number
}

const openModals: OpenModalEntry[] = []

/** overlay 的弹窗嵌套深度（祖先里有多少个 overlay） */
function overlayDepth(el: HTMLElement | null): number {
  let depth = 0
  let node: HTMLElement | null = el?.parentElement ?? null
  while (node) {
    if (node.hasAttribute('data-modal-overlay')) depth += 1
    node = node.parentElement
  }
  return depth
}

function isTopModal(id: symbol): boolean {
  if (openModals.length === 0) return false
  const maxDepth = Math.max(...openModals.map((e) => e.depth))
  // 同深度并存（两个独立弹窗）时取最后登记者，保持「后开者在上」的直觉
  const topmost = openModals.filter((e) => e.depth === maxDepth).pop()
  return topmost?.id === id
}

export default function Modal({ open, title, children, onClose, size = 'md' }: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null)
  const idRef = useRef<symbol>(Symbol('modal'))
  const onCloseRef = useRef(onClose)

  useEffect(() => { onCloseRef.current = onClose }, [onClose])

  useEffect(() => {
    if (!open) return
    const id = idRef.current
    const entry: OpenModalEntry = { id, depth: overlayDepth(overlayRef.current) }
    openModals.push(entry)
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // 非最上层实例一律不响应（document 级监听无法靠 stopPropagation 拦截，必须自行判定）
      if (!isTopModal(id)) return
      onCloseRef.current()
    }
    document.addEventListener('keydown', handler)
    return () => {
      document.removeEventListener('keydown', handler)
      const idx = openModals.lastIndexOf(entry)
      if (idx >= 0) openModals.splice(idx, 1)
    }
  }, [open])

  if (!open) return null

  return (
    <div
      ref={overlayRef}
      data-modal-overlay=""
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm animate-fade-in"
      onClick={(e) => { if (e.target === overlayRef.current) onClose() }}
    >
      <div className={`${sizeMap[size]} w-[90vw] mat-floating rounded-2xl p-5 animate-scale-in`}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
          <button onClick={onClose} className="-mr-2 flex h-11 w-11 items-center justify-center text-gray-400 hover:text-gray-600 transition-colors" aria-label="关闭">
            <X className="w-4 h-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
