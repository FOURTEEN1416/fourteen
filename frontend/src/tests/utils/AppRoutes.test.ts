// App.tsx 路由静态守卫：合并批次曾产生逐字重复的 /templates Route（React Router
// 首个匹配生效，第二行是死码且掩盖冲突信号）。守卫钉死「同一 path 不得声明两次」。
// 读源文件走 Vite ?raw（零依赖，不引 @types/node）。
import appSrc from '../../App.tsx?raw'
import navSrc from '../../components/layout/navGroups.tsx?raw'
import { describe, expect, it } from 'vitest'

describe('App.tsx 路由声明守卫', () => {
  it('Route path 无重复声明', () => {
    const paths = [...appSrc.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => m[1])
    const dup = paths.filter((p, i) => paths.indexOf(p) !== i)
    expect(dup, `重复的 Route path: ${dup.join(', ')}`).toHaveLength(0)
  })

  it('导航引用的路由必须存在', () => {
    const navPaths = [...navSrc.matchAll(/to:\s*'\/([^']+)'|to:\s*"\/([^"]+)"/g)].map(
      (m) => m[1] ?? m[2],
    )
    expect(navPaths.length, 'navGroups 应至少解析出一个导航路径（解析失效即守卫失效）').toBeGreaterThan(0)
    const all = [...appSrc.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => m[1])
    const absolute = new Set(all.filter((r) => r.startsWith('/')).map((r) => r.replace(/:\w+/g, 'x')))
    const relative = new Set(all.filter((r) => !r.startsWith('/')).map((r) => r.replace(/:\w+/g, 'x')))
    // 形态匹配：声明路由的动态段（:roleId 等）可吃掉导航里的任意一段具体值
    // （如 /roles/default/status ↔ /roles/:roleId/status，2026-11-07 无激活态入口）
    const segsOf = (p: string) => p.split('/').filter(Boolean)
    const absoluteSegs = all.filter((r) => r.startsWith('/')).map(segsOf)
    for (const p of navPaths) {
      const full = '/' + p
      const segs = segsOf(full)
      const idx = full.lastIndexOf('/')
      const parent = full.slice(0, idx)
      const leaf = full.slice(idx + 1)
      const shapeMatch = absoluteSegs.some(
        (d) => d.length === segs.length && d.every((s, i) => s.startsWith(':') || s === segs[i]),
      )
      const reachable =
        shapeMatch ||
        absolute.has(full) ||
        (relative.has(leaf) && absolute.has(parent)) ||
        (absolute.has(parent) && leaf === '')
      expect(reachable, `navGroups 引用的 /${p} 在 App.tsx 无对应 Route`).toBe(true)
    }
  })
})
