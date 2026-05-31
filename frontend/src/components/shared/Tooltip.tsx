import { useState, useRef, useEffect, type ReactNode } from 'react'

interface TooltipProps {
  content: string
  children: ReactNode
  position?: 'top' | 'bottom'
}

export default function Tooltip({ content, children, position = 'top' }: TooltipProps) {
  const [show, setShow] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!show) return
    const handler = () => setShow(false)
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [show])

  return (
    <div
      ref={ref}
      className="relative inline-flex"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <div
          className={`absolute z-40 px-2 py-1 text-[10px] text-white bg-gray-800/90 backdrop-blur-sm rounded-md whitespace-nowrap pointer-events-none
            ${position === 'top' ? 'bottom-full mb-1.5 left-1/2 -translate-x-1/2' : 'top-full mt-1.5 left-1/2 -translate-x-1/2'}`}
        >
          {content}
        </div>
      )}
    </div>
  )
}
