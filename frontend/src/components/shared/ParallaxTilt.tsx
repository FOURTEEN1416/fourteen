import { useRef, useState, useCallback } from 'react'

interface ParallaxTiltProps {
  children: React.ReactNode
  className?: string
  maxTilt?: number
  scale?: number
  perspective?: number
  speed?: number
}

export default function ParallaxTilt({
  children,
  className = '',
  maxTilt = 6,
  scale = 1.01,
  perspective = 800,
  speed = 400,
}: ParallaxTiltProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [style, setStyle] = useState<React.CSSProperties>({})
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (timerRef.current) clearTimeout(timerRef.current)
      const el = ref.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      const centerX = rect.width / 2
      const centerY = rect.height / 2
      const rotateX = ((y - centerY) / centerY) * -maxTilt
      const rotateY = ((x - centerX) / centerX) * maxTilt
      setStyle({
        transform: `perspective(${perspective}px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale(${scale})`,
        transition: 'transform 0.08s ease-out',
      })
    },
    [maxTilt, scale, perspective]
  )

  const handleMouseLeave = useCallback(() => {
    timerRef.current = setTimeout(() => {
      setStyle({
        transform: `perspective(${perspective}px) rotateX(0deg) rotateY(0deg) scale(1)`,
        transition: `transform ${speed}ms ease-out`,
      })
    }, 100)
  }, [perspective, speed])

  return (
    <div
      ref={ref}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{ transformStyle: 'preserve-3d', ...style }}
      className={className}
    >
      {children}
    </div>
  )
}
