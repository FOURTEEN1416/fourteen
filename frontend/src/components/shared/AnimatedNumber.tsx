import { useEffect, useRef, useState } from 'react'
import { useInView } from '../../hooks'

interface AnimatedNumberProps {
  value: number
  duration?: number
  decimals?: number
  prefix?: string
  suffix?: string
}

export default function AnimatedNumber({ value, duration = 1.5, decimals = 0, prefix = '', suffix = '' }: AnimatedNumberProps) {
  const [ref, inView] = useInView()
  const [display, setDisplay] = useState(0)
  const frameRef = useRef<number | null>(null)

  useEffect(() => {
    if (!inView) return
    let start: number | null = null
    const from = 0

    const step = (ts: number) => {
      if (!start) start = ts
      const elapsed = (ts - start) / 1000
      const progress = Math.min(elapsed / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      setDisplay(from + (value - from) * eased)
      if (progress < 1) frameRef.current = requestAnimationFrame(step)
    }

    frameRef.current = requestAnimationFrame(step)
    return () => { if (frameRef.current) cancelAnimationFrame(frameRef.current) }
  }, [inView, value, duration])

  return (
    <span ref={ref}>
      {prefix}{display.toFixed(decimals)}{suffix}
    </span>
  )
}
