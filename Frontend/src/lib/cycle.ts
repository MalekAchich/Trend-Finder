/** Calls `onTick` every `ms` while running (the logo restarts its animation each tick); idle = silent. */
export function createCycle(ms: number, onTick: () => void) {
  let timer: ReturnType<typeof setInterval> | null = null;
  const stop = () => { if (timer) clearInterval(timer); timer = null; };
  return {
    set(running: boolean) {
      if (running && !timer) timer = setInterval(onTick, ms);
      if (!running) stop();
    },
    dispose: stop,
  };
}
