interface SubTab {
  key: string
  label: string
}

interface SubTabBarProps {
  tabs: SubTab[]
  activeKey: string
  onChange: (key: string) => void
  className?: string
}

export default function SubTabBar({ tabs, activeKey, onChange, className = '' }: SubTabBarProps) {
  return (
    <div className={`flex gap-1 p-1 bg-gray-100/50 rounded-xl ${className}`}>
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onChange(t.key)}
          className={`flex-1 px-4 py-2 text-sm font-medium rounded-lg transition-all duration-200 ${
            activeKey === t.key
              ? 'bg-white text-primary-700 shadow-sm'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}
