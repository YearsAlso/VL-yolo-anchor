/** Canvas widget that renders an image with zoom/pan and YOLO-OBB overlays. */

import { useCallback, useEffect, useRef } from "react";
import type { OBBBox } from "../types";

const CLASS_COLORS = ["#f5222d", "#fa8c16", "#52c41a", "#1890ff", "#722ed1", "#eb2f96"];

interface OBBCanvasProps {
  /** Image URL to draw. */
  imageUrl?: string;
  /** OBB boxes in normalized clockwise coordinates. */
  boxes: OBBBox[];
  /** Class id -> name mapping for the legend/labels. */
  classNames?: Record<string, string>;
  /** Canvas width in CSS pixels. */
  width?: number;
  /** Canvas height in CSS pixels. */
  height?: number;
}

/** Draw one OBB polygon on a 2D context. */
function drawBox(
  ctx: CanvasRenderingContext2D,
  box: OBBBox,
  label: string,
  w: number,
  h: number,
  scale: number,
  offsetX: number,
  offsetY: number,
) {
  const color = CLASS_COLORS[box.cls % CLASS_COLORS.length];
  ctx.strokeStyle = color;
  ctx.fillStyle = `${color}22`;
  ctx.lineWidth = 2 / scale;
  ctx.beginPath();
  for (let i = 0; i < 4; i++) {
    const x = box.points[i * 2] * w * scale + offsetX;
    const y = box.points[i * 2 + 1] * h * scale + offsetY;
    if (i === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  ctx.font = `${12 / scale}px sans-serif`;
  ctx.fillStyle = color;
  ctx.fillText(label, box.points[0] * w * scale + offsetX, box.points[1] * h * scale + offsetY - 4 / scale);
}

/** Interactive OBB canvas: zoom with wheel, pan with drag. */
export default function OBBCanvas({ imageUrl, boxes, classNames, width = 640, height = 480 }: OBBCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const viewRef = useRef({ scale: 1, offsetX: 0, offsetY: 0 });
  const dragRef = useRef<{ x: number; y: number } | null>(null);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) {
      return;
    }
    const { scale, offsetX, offsetY } = viewRef.current;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const img = imageRef.current;
    if (img) {
      ctx.drawImage(img, offsetX, offsetY, img.width * scale, img.height * scale);
      for (const box of boxes) {
        const label = classNames?.[String(box.cls)] ?? `cls ${box.cls}`;
        drawBox(ctx, box, label, img.width, img.height, scale, offsetX, offsetY);
      }
    }
  }, [boxes, classNames]);

  useEffect(() => {
    if (!imageUrl) {
      imageRef.current = null;
      redraw();
      return;
    }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      imageRef.current = img;
      redraw();
    };
    img.src = imageUrl;
  }, [imageUrl, redraw]);

  useEffect(() => {
    redraw();
  }, [redraw]);

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      style={{ border: "1px solid #d9d9d9", cursor: "grab", background: "#111" }}
      onWheel={(e) => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
        const v = viewRef.current;
        v.scale = Math.min(Math.max(v.scale * factor, 0.2), 8);
        redraw();
      }}
      onMouseDown={(e) => {
        dragRef.current = { x: e.clientX, y: e.clientY };
      }}
      onMouseMove={(e) => {
        const drag = dragRef.current;
        if (!drag) {
          return;
        }
        const v = viewRef.current;
        v.offsetX += e.clientX - drag.x;
        v.offsetY += e.clientY - drag.y;
        dragRef.current = { x: e.clientX, y: e.clientY };
        redraw();
      }}
      onMouseUp={() => {
        dragRef.current = null;
      }}
      onMouseLeave={() => {
        dragRef.current = null;
      }}
    />
  );
}
