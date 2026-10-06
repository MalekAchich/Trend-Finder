import { forwardRef, useImperativeHandle, useRef } from "react";

export interface SeamHandle {
  /** A light travels from `from` (in the dark engine) through the seam to `to` (the library), then resolves. */
  transfer: (from: DOMRect | null, to: DOMRect | null) => Promise<void>;
}

const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export const Seam = forwardRef<SeamHandle, { running: boolean }>(function Seam({ running }, ref) {
  const flash = useRef<HTMLDivElement>(null);
  const band = useRef<HTMLDivElement>(null);

  useImperativeHandle(ref, () => ({
    async transfer(from, to) {
      if (reduced() || !band.current) return;
      const b = band.current.getBoundingClientRect();
      const start = from ? { x: from.left + from.width / 2, y: from.top + from.height / 2 } : { x: b.left + b.width / 2, y: b.top };
      const end = to ? { x: to.left + 40, y: to.top + 60 } : { x: b.left + b.width / 2, y: b.bottom + 80 };
      const orb = document.createElement("div");
      orb.className = "orb";
      document.body.appendChild(orb);
      const midX = (start.x + end.x) / 2;
      const seamY = b.top + b.height / 2;
      flash.current?.style.setProperty("--x", `${((midX - b.left) / b.width) * 100}%`);
      const path = [
        { transform: `translate(${start.x - 9}px, ${start.y - 9}px) scale(.6)`, opacity: 0 },
        { transform: `translate(${start.x - 9}px, ${start.y - 9}px) scale(1.2)`, opacity: 1, offset: 0.12 },
        { transform: `translate(${midX - 9}px, ${seamY - 9}px) scale(2.4)`, opacity: 1, offset: 0.55 },
        { transform: `translate(${end.x - 9}px, ${end.y - 9}px) scale(.9)`, opacity: 0.9, offset: 0.9 },
        { transform: `translate(${end.x - 9}px, ${end.y - 9}px) scale(3)`, opacity: 0 },
      ];
      const travel = orb.animate(path, { duration: 1400, easing: "cubic-bezier(.45,.05,.25,1)" });
      flash.current?.animate([{ opacity: 0 }, { opacity: 0.9, offset: 0.55 }, { opacity: 0 }],
        { duration: 1400, easing: "ease-in-out" });
      await travel.finished.catch(() => undefined);
      orb.remove();
    },
  }));

  return (
    <div ref={band} className="seam" data-running={running} aria-hidden>
      <div className="seam-light a" />
      <div className="seam-light b" />
      <div className="seam-light c" />
      <div ref={flash} className="seam-flash" />
    </div>
  );
});
