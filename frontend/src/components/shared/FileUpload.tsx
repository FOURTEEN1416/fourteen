import { useRef, useState, type DragEvent } from 'react'
import { Upload, X, File } from 'lucide-react'

interface FileUploadProps {
  accept?: string
  maxSize?: number
  multiple?: boolean
  onUpload: (files: File[]) => void
}

export default function FileUpload({ accept, maxSize = 30, multiple, onUpload }: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [files, setFiles] = useState<File[]>([])

  const addFiles = (incoming: FileList) => {
    const valid = Array.from(incoming).filter((f) => !maxSize || f.size <= maxSize * 1024 * 1024)
    const next = multiple ? [...files, ...valid] : valid.slice(0, 1)
    setFiles(next)
    onUpload(next)
  }

  const remove = (i: number) => {
    const next = files.filter((_, idx) => idx !== i)
    setFiles(next)
    onUpload(next)
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setDrag(false)
    if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files)
  }

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`glass-card rounded-xl p-6 text-center cursor-pointer transition-all border-2 border-dashed
          ${drag ? 'border-primary-400 bg-primary-50/30' : 'border-gray-200/60 hover:border-primary-300/50'}`}
      >
        <Upload className="w-6 h-6 text-gray-300 mx-auto mb-2" />
        <p className="text-xs text-gray-400">拖拽文件到此处，或点击选择</p>
        {accept && <p className="text-[10px] text-gray-300 mt-1">支持 {accept} 格式</p>}
        {maxSize && <p className="text-[10px] text-gray-300">单文件 ≤ {maxSize}MB</p>}
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple={multiple}
          className="hidden"
          onChange={(e) => e.target.files && addFiles(e.target.files)}
        />
      </div>
      {files.length > 0 && (
        <div className="mt-2 space-y-1">
          {files.map((f, i) => (
            <div key={i} className="flex items-center gap-2 glass-card rounded-lg px-3 py-1.5 text-xs">
              <File className="w-3.5 h-3.5 text-primary-400 shrink-0" />
              <span className="flex-1 text-gray-600 truncate">{f.name}</span>
              <span className="text-gray-300 shrink-0">{(f.size / 1024).toFixed(0)}KB</span>
              <button onClick={() => remove(i)} className="text-gray-300 hover:text-red-400 transition-colors">
                <X className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
