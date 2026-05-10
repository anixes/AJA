import { useEffect, useRef } from 'react';
import anime from 'animejs';

/**
 * Animate an element on mount using Anime.js.
 * Returns a ref to attach to the target element.
 */
export function useFadeInUp<T extends HTMLElement>(delay = 0) {
  const ref = useRef<T>(null);

  useEffect(() => {
    if (!ref.current) return;
    anime({
      targets: ref.current,
      opacity: [0, 1],
      translateY: [12, 0],
      duration: 400,
      delay,
      easing: 'easeOutCubic',
    });
  }, [delay]);

  return ref;
}

/**
 * Animate a numeric counter from 0 to `value`.
 * Returns a ref to the element and call `trigger()` to restart.
 */
export function useCountUp(value: number, duration = 800) {
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!ref.current || typeof value !== 'number') return;
    const obj = { val: 0 };
    anime({
      targets: obj,
      val: value,
      round: 1,
      duration,
      easing: 'easeOutExpo',
      update: () => {
        if (ref.current) ref.current.textContent = String(obj.val);
      },
    });
  }, [value, duration]);

  return ref;
}

/**
 * Stagger-animate a list of children on mount.
 * Returns a ref to attach to the parent container.
 */
export function useStaggerChildren<T extends HTMLElement>(selector = ':scope > *', delay = 0) {
  const ref = useRef<T>(null);

  useEffect(() => {
    if (!ref.current) return;
    anime({
      targets: ref.current.querySelectorAll(selector),
      opacity: [0, 1],
      translateY: [8, 0],
      duration: 350,
      delay: anime.stagger(60, { start: delay }),
      easing: 'easeOutCubic',
    });
  }, [selector, delay]);

  return ref;
}

/**
 * Pulse a badge/indicator element whenever `trigger` changes.
 * Returns a ref to attach to the target element.
 */
export function usePulse<T extends HTMLElement>(trigger: unknown) {
  const ref = useRef<T>(null);

  useEffect(() => {
    if (!ref.current) return;
    anime({
      targets: ref.current,
      scale: [1, 1.12, 1],
      duration: 300,
      easing: 'easeInOutQuad',
    });
  }, [trigger]);

  return ref;
}
