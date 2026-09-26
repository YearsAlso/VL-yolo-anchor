import { useCallback, useEffect, useRef, useState } from "react";

interface Viewport {
  scale: number;
  offsetX: number;
  offsetY: number;
}

/** Canvas viewport state with zoom/pan controls. */
export function useCanvasViewport(initialScale = 1): {
  viewport: Viewport;
  zoomIn: () => void;
  zoomOut: () => void;
  reset: () => void;
  panBy: (dx: number, dy: number) => void;
} {
  const [viewport, setViewport] = useState<Viewport>({ scale: initialScale, offsetX: 0, offsetY: 0 });
  const viewportRef = useRef(viewport);
  viewportRef.current = viewport;

  const zoomIn = useCallback(() => {
    setViewport((v) => ({ ...v, scale: Math.min(v.scale * 1.2, 8) }));
  }, []);

  const zoomOut = useCallback(() => {
    setViewport((v) => ({ ...v, scale: Math.max(v.scale / 1.2, 0.2) }));
  }, []);

  const reset = useCallback(() => {
    setViewport({ scale: initialScale, offsetX: 0, offsetY: 0 });
  }, [initialScale]);

  const panBy = useCallback((dx: number, dy: number) => {
    setViewport((v) => ({ ...v, offsetX: v.offsetX + dx, offsetY: v.offsetY + dy }));
  }, []);

  return { viewport, zoomIn, zoomOut, reset, panBy };
}

/** Polls a callback at a fixed interval while `active` is true. */
export function useInterval(callback: () => void, delayMs: number | null): void {
  const saved = useRef(callback);
  saved.current = callback;

  useEffect(() => {
    if (delayMs === null) {
      return;
    }
    const id = window.setInterval(() => saved.current(), delayMs);
    return () => window.clearInterval(id);
  }, [delayMs]);
}
