import { useRef, useState, useEffect, useMemo } from 'react'

export function useInView(options?: IntersectionObserverInit): [React.RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement | null>(null)
  const [inView, setInView] = useState(false)

  // 序列化 options 为依赖键，避免对象引用变化导致 effect 重复创建 observer
  const optionsKey = useMemo(
    () => JSON.stringify([options?.threshold, options?.rootMargin, options?.root]),
    [options?.threshold, options?.rootMargin, options?.root]
  )

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setInView(true)
        observer.unobserve(el)
      }
    }, { threshold: 0.1, ...options })
    observer.observe(el)
    return () => observer.disconnect()
    // options 由 optionsKey（序列化）代理追踪
  }, [optionsKey, options])

  return [ref, inView]
}
