import { forwardRef, useImperativeHandle, useRef } from "react";

export interface SeamHandle {
  /** A yellow-lime pulse sweeps the seam from the engine down into the library (a video was just saved). */
  pulse: () => Promise<void>;
}

const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export const Seam = forwardRef<SeamHandle, { running: boolean }>(function Seam({ running }, ref) {
  const band = useRef<HTMLDivElement>(null);
  const wave = useRef<HTMLDivElement>(null);

  useImperativeHandle(ref, () => ({
    async pulse() {
      if (reduced() || !wave.current || !band.current) return;
      const glow = band.current.animate([{ filter: "brightness(1)" }, { filter: "brightness(1.07)", offset: 0.5 },
        { filter: "brightness(1)" }], { duration: 1900, easing: "ease-in-out" });
      const sweep = wave.current.animate([
        { transform: "translateY(-110%)", opacity: 0 },
        { transform: "translateY(-40%)", opacity: 1, offset: 0.25 },
        { transform: "translateY(60%)", opacity: 1, offset: 0.8 },
        { transform: "translateY(120%)", opacity: 0 },
      ], { duration: 1900, easing: "cubic-bezier(.45,.05,.3,1)" });
      await Promise.all([sweep.finished.catch(() => undefined), glow.finished.catch(() => undefined)]);
    },
  }));

  return (
    <div ref={band} className="seam" data-running={running} aria-hidden>
      <div className="seam-lights">
        <div className="seam-light a" />
        <div className="seam-light b" />
        <div className="seam-light c" />
      </div>
      <div ref={wave} className="seam-wave" />
      <div className="seam-grain" />
    </div>
  );
});
