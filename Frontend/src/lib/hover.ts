/** Starts after the pointer has rested `delay` ms (like YouTube's hover preview); leaving cancels or ends it. */
export function createHoverIntent(delay: number, onStart: () => void, onEnd: () => void) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let active = false;
  return {
    enter() {
      if (timer || active) return;
      timer = setTimeout(() => {
        timer = null;
        active = true;
        onStart();
      }, delay);
    },
    leave() {
      if (timer) clearTimeout(timer);
      timer = null;
      active = false;
      onEnd();
    },
  };
}
