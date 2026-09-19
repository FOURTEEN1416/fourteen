/**
 * 遮罩式鼠标动效：光晕遮罩（cursor-glow）+ 内核（cursor-core）+ 拖尾粒子（cursor-trail）。
 *
 * 2026-09-18 重构（原「圆环 + 圆点」双层跟随）
 * 视觉参考：校友网站 index.astro 的 cursorGlow / cursorDot / firefly 三层结构。
 * 品牌色保持不变：海盐蓝 #7DD3FC（--color-accent-200）为主调，
 * 暖黄 #FDE68A（--color-macaron-yellow）与薄荷青 #99F6E4（--color-macaron-mint）作变体。
 *
 * ── 性能约束（均有隔离实验实测依据，勿随意放宽）──
 * 1. 位移一律走 transform: translate3d —— 禁止逐帧写 left/top（会触发布局，
 *    参考站实现正是用 left/top，本实现刻意规避）。
 * 2. 拖尾用固定对象池复用节点 —— 禁止在 mousemove 里 createElement/removeChild
 *    （参考站每帧新建节点，高频移动下会堆积 DOM 与合成层）。
 * 3. 单一 rAF 循环统一驱动位置与粒子生命周期 —— 禁止 setTimeout 堆。
 * 4. 拖尾用 radial-gradient 背景而非大面积 box-shadow ——
 *    多层大半径 box-shadow 的模糊成本随半径平方增长。
 * 5. 指针静止超过 IDLE_MS 即停帧，不在静止时空转。
 * 6. 所有「按帧」的系数都经帧时长归一化（k = dt / 16.67）——
 *    否则同一份代码在 60Hz 与 240Hz 屏上表现完全不同
 *    （初版按帧衰减，240Hz 下拖尾寿命只剩四分之一，肉眼几乎看不见）。
 * 7. 触屏（pointer: coarse）或 prefers-reduced-motion 下完全不激活。
 */
import { useEffect, useRef } from 'react'

/** 拖尾粒子池大小：同时存活的粒子上限 */
const TRAIL_POOL = 16
/** 拖尾生成间隔（ms）：按时间节流，避免高频移动下粒子爆量 */
const SPAWN_INTERVAL = 30
/** 拖尾每 1/60s 的生命衰减：单颗粒子寿命约 0.64s（实测停止移动后 601ms 消散完） */
const TRAIL_DECAY = 0.026
/** 拖尾漂移速度基准（px/帧@60fps）：约 48px/s，缓慢飘散 */
const TRAIL_DRIFT = 1.6
/** 光晕跟随系数：越小越「重」，形成遮罩被拖曳的观感 */
const GLOW_LERP = 0.09
/** 内核跟随系数：越大越贴合指针 */
const DOT_LERP = 0.38
/** 空闲停帧阈值（ms） */
const IDLE_MS = 1200
/** 单帧时间上限（ms）：切标签页/长时间挂起后回来，避免一次性大跳 */
const MAX_FRAME_MS = 50
/** 基准帧时长（ms），用于把按帧的系数归一化到 60fps */
const BASE_FRAME_MS = 1000 / 60
/** hover 态判定的可交互元素选择器 */
const INTERACTIVE =
  'a, button, [role="button"], input, select, textarea, label, summary, [data-hover]'

interface TrailNode {
  el: HTMLDivElement
  life: number
  x: number
  y: number
  vx: number
  vy: number
  scale: number
}

export function CustomCursor() {
  const glowRef = useRef<HTMLDivElement>(null)
  const coreRef = useRef<HTMLDivElement>(null)
  const trailRef = useRef<Array<HTMLDivElement | null>>([])

  useEffect(() => {
    // 触屏与减少动效偏好下不启用：既无指针可跟随，也避免无谓开销
    const coarse = window.matchMedia('(pointer: coarse)').matches
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (coarse || reduced) return

    const glow = glowRef.current
    const core = coreRef.current
    if (!glow || !core) return

    // ── 粒子池：一次性建好，之后只复用，杜绝 mousemove 中增删 DOM ──
    const pool: TrailNode[] = []
    for (const el of trailRef.current) {
      if (el) pool.push({ el, life: 0, x: 0, y: 0, vx: 0, vy: 0, scale: 1 })
    }
    let head = 0

    // ── 指针目标值 vs 各层实际位置（-9999 表示尚未入场）──
    let targetX = -9999
    let targetY = -9999
    let glowX = -9999
    let glowY = -9999
    let coreX = -9999
    let coreY = -9999

    let rafId: number | null = null
    let lastSpawn = 0
    let lastSpawnX = -9999
    let lastSpawnY = -9999
    let lastMove = 0
    let prevTime = 0

    const frame = (now: number) => {
      rafId = null

      // ── 帧时长归一化 ──
      // 所有「按帧」的系数都必须换算成 60fps 基准下的等效值，
      // 否则同一份代码在 60Hz 与 240Hz 屏上表现完全不同
      // （最初按帧衰减，导致 240Hz 下拖尾寿命只剩 1/4，几乎看不见）。
      const dt = prevTime ? Math.min(now - prevTime, MAX_FRAME_MS) : BASE_FRAME_MS
      prevTime = now
      const k = dt / BASE_FRAME_MS

      // lerp 的帧率归一化：alpha_k = 1 - (1 - alpha)^k
      const glowK = 1 - Math.pow(1 - GLOW_LERP, k)
      const coreK = 1 - Math.pow(1 - DOT_LERP, k)

      // 光晕：滞后跟随 → 遮罩被拖曳的观感
      glowX += (targetX - glowX) * glowK
      glowY += (targetY - glowY) * glowK
      glow.style.transform = `translate3d(${glowX}px, ${glowY}px, 0) translate(-50%, -50%)`

      // 内核：紧跟指针
      coreX += (targetX - coreX) * coreK
      coreY += (targetY - coreY) * coreK
      core.style.transform = `translate3d(${coreX}px, ${coreY}px, 0) translate(-50%, -50%)`

      // ── 拖尾生成 ──
      // 双闸门：① 距上次生成超过 SPAWN_INTERVAL ② 指针确实位移了
      // 只看时间不看位移的话，指针静止时仍会不断在原点堆叠粒子，
      // 且停帧时会把粒子冻结在可见态，屏幕上留下不消散的残点。
      const movedDist = Math.abs(targetX - lastSpawnX) + Math.abs(targetY - lastSpawnY)
      if (now - lastSpawn >= SPAWN_INTERVAL && movedDist > 2) {
        lastSpawn = now
        lastSpawnX = targetX
        lastSpawnY = targetY
        const node = pool[head % pool.length]
        head += 1
        node.life = 1
        node.x = targetX + (Math.random() - 0.5) * 14
        node.y = targetY + (Math.random() - 0.5) * 14
        node.vx = (Math.random() - 0.5) * TRAIL_DRIFT
        node.vy = (Math.random() - 0.5) * TRAIL_DRIFT - 0.5
        node.scale = 0.5 + Math.random() * 0.6
      }

      // 拖尾漂移与衰减（均按 k 归一化）
      let hasTrail = false
      for (const p of pool) {
        if (p.life <= 0) continue
        p.life -= TRAIL_DECAY * k
        if (p.life <= 0) {
          p.life = 0
          p.el.style.opacity = '0'
          continue
        }
        hasTrail = true
        p.x += p.vx * k
        p.y += p.vy * k
        const s = (0.35 + p.life * 0.65) * p.scale
        p.el.style.transform = `translate3d(${p.x}px, ${p.y}px, 0) translate(-50%, -50%) scale(${s.toFixed(3)})`
        p.el.style.opacity = String((p.life * 0.95).toFixed(3))
      }

      // ── 空闲停帧 ──
      // 必须「静止超时」且「拖尾已全部消散」两个条件同时满足才停，
      // 否则最后一批粒子会被冻结在屏幕上永久不灭。
      if (now - lastMove > IDLE_MS && !hasTrail) {
        prevTime = 0
        return
      }
      rafId = requestAnimationFrame(frame)
    }

    const onMove = (e: MouseEvent) => {
      targetX = e.clientX
      targetY = e.clientY
      lastMove = performance.now()
      // 首次入场：直接把各层落到指针处，避免从屏幕左上角飞入
      if (glowX < -9000) {
        glowX = coreX = targetX
        glowY = coreY = targetY
        lastSpawn = lastMove
        lastSpawnX = -9999
        lastSpawnY = -9999
        glow.style.opacity = '1'
        core.style.opacity = '1'
      }
      if (rafId === null) rafId = requestAnimationFrame(frame)
    }

    const onLeave = () => {
      glow.style.opacity = '0'
      core.style.opacity = '0'
    }
    const onEnter = () => {
      if (glowX > -9000) {
        glow.style.opacity = '1'
        core.style.opacity = '1'
      }
    }

    // hover 态：事件委托 + 只切 class，不触发 React 重渲染
    const setVariant = (el: HTMLElement, variant: string | null) => {
      if (variant) el.dataset.variant = variant
      else delete el.dataset.variant
    }

    const onOver = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null
      const hit = t?.closest?.(INTERACTIVE) as HTMLElement | null
      if (!hit) return
      core.classList.add('is-hover')
      glow.classList.add('is-hover')
      const variant = hit.getAttribute('data-hover')
      setVariant(core, variant)
      setVariant(glow, variant)
    }

    const onOut = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null
      if (!t?.closest?.(INTERACTIVE)) return
      core.classList.remove('is-hover')
      glow.classList.remove('is-hover')
    }

    document.addEventListener('mousemove', onMove, { passive: true })
    document.addEventListener('mouseover', onOver)
    document.addEventListener('mouseout', onOut)
    document.addEventListener('mouseleave', onLeave)
    document.addEventListener('mouseenter', onEnter)

    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseover', onOver)
      document.removeEventListener('mouseout', onOut)
      document.removeEventListener('mouseleave', onLeave)
      document.removeEventListener('mouseenter', onEnter)
      if (rafId !== null) cancelAnimationFrame(rafId)
    }
  }, [])

  return (
    <>
      {/* 遮罩光晕：大半径径向渐变，滞后跟随形成拖曳遮罩 */}
      <div ref={glowRef} className="cursor-glow" aria-hidden="true" />
      {/* 内核：小圆点，紧跟指针，hover 时张开 */}
      <div ref={coreRef} className="cursor-core" aria-hidden="true" />
      {/* 拖尾粒子池：固定节点，复用不增删 */}
      {Array.from({ length: TRAIL_POOL }, (_, i) => (
        <div
          key={i}
          ref={(el) => {
            trailRef.current[i] = el
          }}
          className="cursor-trail"
          aria-hidden="true"
        />
      ))}

      <style>{`
        .cursor-glow,
        .cursor-core,
        .cursor-trail {
          position: fixed;
          top: 0;
          left: 0;
          pointer-events: none;
          opacity: 0;
        }

        /* ── 遮罩光晕 ──
           本站底色是暖白→海盐蓝→薄荷的浅色渐变，与参考站（深色视频底）不同，
           同透明度下光晕会「发飘」。因此中心不透明度上调、色心加深一档，
           色相仍锁定品牌海盐蓝（#7DD3FC → #38BDF8 同族）。 */
        .cursor-glow {
          width: 200px;
          height: 200px;
          border-radius: 50%;
          background: radial-gradient(
            circle,
            rgba(56, 189, 248, 0.30) 0%,
            rgba(125, 211, 252, 0.16) 34%,
            rgba(125, 211, 252, 0.06) 58%,
            transparent 80%
          );
          z-index: 9997;
          will-change: transform;
          transition:
            opacity 0.45s cubic-bezier(0.16, 1, 0.3, 1),
            width 0.32s cubic-bezier(0.16, 1, 0.3, 1),
            height 0.32s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .cursor-glow.is-hover {
          width: 280px;
          height: 280px;
        }

        /* ── 内核 ── */
        .cursor-core {
          width: 12px;
          height: 12px;
          border-radius: 50%;
          border: 2px solid #38BDF8;
          background: rgba(125, 211, 252, 0.30);
          box-shadow: 0 0 12px rgba(56, 189, 248, 0.60);
          z-index: 9999;
          will-change: transform;
          transition:
            opacity 0.45s cubic-bezier(0.16, 1, 0.3, 1),
            width 0.22s cubic-bezier(0.34, 1.56, 0.64, 1),
            height 0.22s cubic-bezier(0.34, 1.56, 0.64, 1),
            background 0.22s ease,
            border-color 0.22s ease,
            box-shadow 0.22s ease;
        }
        .cursor-core.is-hover {
          width: 38px;
          height: 38px;
          background: rgba(125, 211, 252, 0.12);
        }

        /* ── 拖尾粒子（radial-gradient 替代大面积 box-shadow）── */
        .cursor-trail {
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: radial-gradient(
            circle,
            rgba(56, 189, 248, 1) 0%,
            rgba(125, 211, 252, 0.50) 48%,
            transparent 72%
          );
          z-index: 9998;
        }

        /* ── 配色变体：data-hover="yellow|mint"（默认蓝=无 data-variant）── */
        .cursor-core[data-variant='yellow'] {
          border-color: #FDE68A;
          background: rgba(253, 230, 138, 0.35);
          box-shadow: 0 0 10px rgba(253, 230, 138, 0.55);
        }
        .cursor-core[data-variant='mint'] {
          border-color: #99F6E4;
          background: rgba(153, 246, 228, 0.35);
          box-shadow: 0 0 10px rgba(153, 246, 228, 0.55);
        }
        .cursor-glow[data-variant='yellow'] {
          background: radial-gradient(
            circle,
            rgba(251, 191, 36, 0.30) 0%,
            rgba(253, 230, 138, 0.16) 34%,
            rgba(253, 230, 138, 0.06) 58%,
            transparent 80%
          );
        }
        .cursor-glow[data-variant='mint'] {
          background: radial-gradient(
            circle,
            rgba(45, 212, 191, 0.30) 0%,
            rgba(153, 246, 228, 0.16) 34%,
            rgba(153, 246, 228, 0.06) 58%,
            transparent 80%
          );
        }

        /* 小屏不启用（保持原有 lg 断点行为） */
        @media (max-width: 1023px) {
          .cursor-glow,
          .cursor-core,
          .cursor-trail {
            display: none;
          }
        }
      `}</style>
    </>
  )
}
