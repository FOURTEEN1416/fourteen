export default function Section({ title, children, className = '' }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-white/60 backdrop-blur-sm border border-gray-200/60 rounded-2xl p-5 ${className}`}>
      <h3 className="text-sm font-semibold text-gray-800 mb-4 flex items-center gap-2">
        <span className="w-1 h-4 rounded-full bg-primary-400" />
        {title}
      </h3>
      {children}
    </div>
  )
}
