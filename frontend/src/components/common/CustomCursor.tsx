import { useEffect, useRef } from 'react'

export function CustomCursor() {
  const ringRef = useRef<HTMLDivElement>(null)
  const dotRef = useRef<HTMLDivElement>(null)
  const rafRef = useRef<number | null>(null)
  const pendingRef = useRef<{ x: number; y: number } | null>(null)

  useEffect(() => {
    // 触控设备不启用自定义光标，避免遮挡与性能开销
    const isTouch = window.matchMedia('(pointer: coarse)').matches
    if (isTouch) return

    const ring = ringRef.current
    const dot = dotRef.current
    if (!ring || !dot) return

    const update = () => {
      rafRef.current = null
      const pos = pendingRef.current
      if (!pos) return
      const x = pos.x
      const y = pos.y
      // 用 transform 代替 left/top，避免每帧触发 layout
      ring.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%)`
      dot.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%)`
    }

    const move = (e: MouseEvent) => {
      pendingRef.current = { x: e.clientX, y: e.clientY }
      if (rafRef.current === null) {
        rafRef.current = requestAnimationFrame(update)
      }
    }

    const over = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      const hoverEl = target.closest('[data-hover]')
      if (hoverEl && ring) {
        const type = hoverEl.getAttribute('data-hover')
        ring.classList.add('hovering')
        ring.classList.remove('hovering-pink', 'hovering-blue', 'hovering-green')
        if (type === 'pink') ring.classList.add('hovering-pink')
        else if (type === 'blue') ring.classList.add('hovering-blue')
        else if (type === 'green') ring.classList.add('hovering-green')
      }
    }

    const out = () => {
      if (ring) {
        ring.classList.remove('hovering', 'hovering-pink', 'hovering-blue', 'hovering-green')
      }
    }

    document.addEventListener('mousemove', move, { passive: true })
    document.addEventListener('mouseover', over)
    document.addEventListener('mouseout', out)

    return () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseover', over)
      document.removeEventListener('mouseout', out)
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  return (
    <>
      <div
        ref={ringRef}
        className="cursor-ring hidden lg:block"
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: 32,
          height: 32,
          border: '2px solid #7DD3FC',
          borderRadius: '50%',
          pointerEvents: 'none',
          zIndex: 9999,
          transition: 'width 0.25s cubic-bezier(0.34,1.56,0.64,1), height 0.25s cubic-bezier(0.34,1.56,0.64,1), border-color 0.3s, background 0.3s',
          willChange: 'transform',
        }}
      />
      <div
        ref={dotRef}
        className="cursor-dot hidden lg:block"
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: 8,
          height: 8,
          background: '#7DD3FC',
          borderRadius: '50%',
          pointerEvents: 'none',
          zIndex: 10000,
          willChange: 'transform',
        }}
      />
      <style>{`
        /* 不再强制隐藏系统光标：自定义光标可能因层级/初始化问题不可见，保留系统光标保证可用性 */
        .cursor-ring.hovering { width: 56px; height: 56px; }
        .cursor-ring.hovering-pink { border-color: var(--color-macaron-pink); background: rgba(248,180,217,0.12); }
        .cursor-ring.hovering-blue { border-color: var(--color-macaron-blue-deep); background: rgba(180,217,248,0.12); }
        .cursor-ring.hovering-green { border-color: var(--color-macaron-green-deep); background: rgba(180,248,217,0.12); }
      `}</style>
    </>
  )
}
