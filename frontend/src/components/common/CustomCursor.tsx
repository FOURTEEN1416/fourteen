import { useEffect, useRef, useCallback } from 'react';

export function CustomCursor() {
  const ringRef = useRef<HTMLDivElement>(null);
  const dotRef = useRef<HTMLDivElement>(null);

  const move = useCallback((e: MouseEvent) => {
    if (ringRef.current) {
      ringRef.current.style.left = `${e.clientX}px`;
      ringRef.current.style.top = `${e.clientY}px`;
    }
    if (dotRef.current) {
      dotRef.current.style.left = `${e.clientX}px`;
      dotRef.current.style.top = `${e.clientY}px`;
    }
  }, []);

  const over = useCallback((e: MouseEvent) => {
    const target = e.target as HTMLElement;
    const hoverEl = target.closest('[data-hover]');
    if (hoverEl && ringRef.current) {
      const type = hoverEl.getAttribute('data-hover');
      ringRef.current.classList.add('hovering');
      ringRef.current.classList.remove('hovering-pink', 'hovering-blue', 'hovering-green');
      if (type === 'pink') ringRef.current.classList.add('hovering-pink');
      else if (type === 'blue') ringRef.current.classList.add('hovering-blue');
      else if (type === 'green') ringRef.current.classList.add('hovering-green');
    }
  }, []);

  const out = useCallback(() => {
    if (ringRef.current) {
      ringRef.current.classList.remove('hovering', 'hovering-pink', 'hovering-blue', 'hovering-green');
    }
  }, []);

  useEffect(() => {
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseover', over);
    document.addEventListener('mouseout', out);
    return () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseover', over);
      document.removeEventListener('mouseout', out);
    };
  }, [move, over, out]);

  return (
    <>
      <div
        ref={ringRef}
        className="cursor-ring"
        style={{
          position: 'fixed',
          width: 32,
          height: 32,
          border: '2px solid #7DD3FC',
          borderRadius: '50%',
          pointerEvents: 'none',
          zIndex: 9999,
          transition: 'width 0.25s cubic-bezier(0.34,1.56,0.64,1), height 0.25s cubic-bezier(0.34,1.56,0.64,1), border-color 0.3s, background 0.3s',
          transform: 'translate(-50%, -50%)',
        }}
      />
      <div
        ref={dotRef}
        className="cursor-dot"
        style={{
          position: 'fixed',
          width: 8,
          height: 8,
          background: '#7DD3FC',
          borderRadius: '50%',
          pointerEvents: 'none',
          zIndex: 10000,
          transform: 'translate(-50%, -50%)',
        }}
      />
      <style>{`
        @media (pointer: fine) {
          * { cursor: none !important; }
        }
        .cursor-ring.hovering { width: 56px; height: 56px; }
        .cursor-ring.hovering-pink { border-color: var(--color-macaron-pink); background: rgba(248,180,217,0.12); }
        .cursor-ring.hovering-blue { border-color: var(--color-macaron-blue-deep); background: rgba(180,217,248,0.12); }
        .cursor-ring.hovering-green { border-color: var(--color-macaron-green-deep); background: rgba(180,248,217,0.12); }
      `}</style>
    </>
  );
}
