import { useEffect, useRef } from 'react';

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  color: string;
  rgb: [number, number, number];
}

const COLORS: Array<[string, [number, number, number]]> = [
  ['#FDE68A', [253, 230, 138]],
  ['#B4D9F8', [180, 217, 248]],
  ['#99F6E4', [153, 246, 228]],
];

/** 根据屏幕面积估算粒子数量，避免大屏过度绘制。 */
function particleCount(width: number, height: number): number {
  const area = width * height;
  // 以 1920×1080 为基准，最多 18 个；小屏最少 8 个
  return Math.max(8, Math.min(18, Math.floor(area / 115_200)));
}

export function ParticleCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    let animId: number | null = null;
    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    let lastDraw = 0;
    const FRAME_INTERVAL = 1000 / 30; // 限制 30fps，降低主线程压力
    const DIST = 120;
    const DIST_SQ = DIST * DIST;
    const particles: Particle[] = [];
    // 绘制坐标统一用 CSS 逻辑像素；backing store 按 dpr 放大（09-19 诊断 P2：
    // 原实现 canvas.width=innerWidth 未乘 dpr，HiDPI 屏整幅被拉伸 → 粒子发糊）。
    let logicalW = window.innerWidth;
    let logicalH = window.innerHeight;

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /** 重建画布与粒子；尺寸未变化时直接跳过，避免无谓的重分配。 */
    const rebuild = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      // dpr 上限 2：3x/4x 屏像素数平方级暴涨，16GB 机不值得为粒子付这个渲染成本。
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const bw = Math.round(w * dpr);
      const bh = Math.round(h * dpr);
      if (canvas.width === bw && canvas.height === bh) return;
      canvas.width = bw;
      canvas.height = bh;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      logicalW = w;
      logicalH = h;
      particles.length = 0;
      const count = reducedMotion ? 0 : particleCount(w, h);
      for (let i = 0; i < count; i++) {
        const [color, rgb] = COLORS[i % 3];
        particles.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.4,
          vy: (Math.random() - 0.5) * 0.4,
          r: Math.random() * 2 + 1,
          color,
          rgb,
        });
      }
    };
    rebuild();

    const drawFrame = () => {
      ctx.clearRect(0, 0, logicalW, logicalH);

      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0 || p.x > logicalW) p.vx *= -1;
        if (p.y < 0 || p.y > logicalH) p.vy *= -1;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.fill();
      }

      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const dSq = dx * dx + dy * dy;
          if (dSq < DIST_SQ) {
            const d = Math.sqrt(dSq);
            const [r, g, b] = particles[i].rgb;
            ctx.beginPath();
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${0.12 * (1 - d / DIST)})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }
    };

    // 帧循环：animId 始终指向"待执行的下一帧"。
    // 旧实现由 resize 直接 requestAnimationFrame，与正在运行的那一帧各自续帧，
    // 会派生出两条并行 rAF 循环 → 重复绘制、CPU 翻倍。
    const loop = (time?: number) => {
      const now = time ?? performance.now();
      if (now - lastDraw >= FRAME_INTERVAL) {
        lastDraw = now;
        drawFrame();
      }
      animId = requestAnimationFrame(loop);
    };

    const start = () => {
      if (animId !== null) return; // 已在运行，避免重复启动
      animId = requestAnimationFrame(loop);
    };

    const stop = () => {
      if (animId === null) return;
      cancelAnimationFrame(animId);
      animId = null;
    };

    const handleResize = () => {
      // 防抖：移动端滚动时地址栏收缩/展开会高频触发 resize，
      // 每次都重建画布会造成粒子跳变与主线程抖动
      if (resizeTimer !== null) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        resizeTimer = null;
        rebuild();
      }, 150);
    };
    window.addEventListener('resize', handleResize);

    const handleVisibility = () => {
      if (document.hidden) stop();
      else start();
    };
    document.addEventListener('visibilitychange', handleVisibility);

    if (!document.hidden) start();

    return () => {
      stop();
      if (resizeTimer !== null) clearTimeout(resizeTimer);
      window.removeEventListener('resize', handleResize);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        zIndex: 0,
        pointerEvents: 'none',
        contain: 'layout paint',
      }}
    />
  );
}
