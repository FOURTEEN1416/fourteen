/**
 * 页面过渡动画容器。
 *
 * F2 优化：用 CSS 动画替代 framer-motion，避免将 motion 库（约 50-60kB gzip）
 * 拉入主 chunk 依赖图。页面过渡效果保持一致（opacity + translateY + scale）。
 * framer-motion 仅保留给 Toggle / ScrollProgress 等轻量交互组件使用。
 */
import { useEffect, useState } from 'react'

export default function AnimatedPage({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    // 下一帧触发动画，确保 initial 状态已渲染
    const id = requestAnimationFrame(() => setMounted(true))
    return () => cancelAnimationFrame(id)
  }, [])

  return (
    <div
      className="flex-1 flex flex-col transition-all duration-300 ease-out"
      style={{
        opacity: mounted ? 1 : 0,
        transform: mounted
          ? 'translateY(0) scale(1)'
          : 'translateY(12px) scale(0.99)',
        willChange: 'opacity, transform',
      }}
    >
      {children}
    </div>
  )
}
