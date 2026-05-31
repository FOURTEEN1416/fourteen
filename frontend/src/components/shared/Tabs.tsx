import { motion } from 'framer-motion'

interface TabsProps {
  tabs: { key: string; label: string }[]
  activeKey: string
  onChange: (key: string) => void
}

export default function Tabs({ tabs, activeKey, onChange }: TabsProps) {
  return (
    <div className="flex gap-1 glass-card rounded-lg p-0.5 self-start">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={`relative px-3.5 py-1.5 text-xs font-medium rounded-md transition-colors
            ${activeKey === tab.key ? 'text-primary-700' : 'text-gray-400 hover:text-gray-600'}`}
        >
          {activeKey === tab.key && (
            <motion.div
              layoutId="tabs-bg"
              className="absolute inset-0 bg-white/80 rounded-md shadow-sm"
              transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            />
          )}
          <span className="relative z-10">{tab.label}</span>
        </button>
      ))}
    </div>
  )
}
