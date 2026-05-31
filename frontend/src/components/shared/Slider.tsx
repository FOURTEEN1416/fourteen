import { useId } from 'react'

interface SliderProps {
  label: string
  value: number
  min?: number
  max?: number
  step?: number
  tooltip?: string
  disabled?: boolean
  onChange: (value: number) => void
}

export default function Slider({ label, value, min = 0, max = 1, step = 0.01, tooltip, disabled, onChange }: SliderProps) {
  const id = useId()
  const pct = ((value - min) / (max - min)) * 100

  return (
    <div className="flex items-center gap-3 group">
      <label htmlFor={id} className="text-xs text-gray-500 w-14 shrink-0 truncate" title={tooltip}>
        {label}
      </label>
      <div className="flex-1 relative">
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="w-full h-1.5 appearance-none bg-gray-200/70 rounded-full cursor-pointer
            accent-primary-400
            [&::-webkit-slider-thumb]:appearance-none
            [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
            [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-full
            [&::-webkit-slider-thumb]:shadow-[0_1px_3px_rgba(0,0,0,0.15)]
            [&::-webkit-slider-thumb]:hover:shadow-[0_0_0_4px_rgba(34,208,223,0.15)]
            [&::-webkit-slider-thumb]:transition-shadow"
          style={{
            background: `linear-gradient(to right, #22d0df ${pct}%, rgba(0,0,0,0.08) ${pct}%)`,
          }}
        />
      </div>
      <span className="text-[11px] text-gray-400 w-8 text-right tabular-nums shrink-0">
        {value.toFixed(2)}
      </span>
    </div>
  )
}
