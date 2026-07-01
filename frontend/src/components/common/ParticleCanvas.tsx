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
  ['#F8B4D9', [248, 180, 217]],
  ['#B4D9F8', [180, 217, 248]],
  ['#B4F8D9', [180, 248, 217]],
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

    let animId: number;
    let lastDraw = 0;
    const FRAME_INTERVAL = 1000 / 30; // 限制 30fps，降低主线程压力
    const DIST = 120;
    const DIST_SQ = DIST * DIST;
    const particles: Particle[] = [];

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      // 重建粒子：数量随屏幕面积变化
      particles.length = 0;
      const count = reducedMotion ? 0 : particleCount(canvas.width, canvas.height);
      for (let i = 0; i < count; i++) {
        const [color, rgb] = COLORS[i % 3];
        particles.push({
          x: Math.random() * canvas.width,
          y: Math.random() * canvas.height,
          vx: (Math.random() - 0.5) * 0.4,
          vy: (Math.random() - 0.5) * 0.4,
          r: Math.random() * 2 + 1,
          color,
          rgb,
        });
      }
    };
    resize();

    const handleResize = () => {
      // 防抖动：避免窗口缩放时频繁重建
      window.cancelAnimationFrame(animId);
      resize();
      animId = requestAnimationFrame(draw);
    };
    window.addEventListener('resize', handleResize);

    const draw = (time?: number) => {
      if (document.hidden) {
        animId = requestAnimationFrame(draw);
        return;
      }

      const now = time ?? performance.now();
      if (now - lastDraw < FRAME_INTERVAL) {
        animId = requestAnimationFrame(draw);
        return;
      }
      lastDraw = now;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0 || p.x > canvas.width) p.vx *= -1;
        if (p.y < 0 || p.y > canvas.height) p.vy *= -1;

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

      animId = requestAnimationFrame(draw);
    };
    draw();

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', handleResize);
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
        contain: 'strict',
        willChange: 'transform',
      }}
    />
  );
}
