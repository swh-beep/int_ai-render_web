import { forwardRef, useImperativeHandle, useRef, useState } from "react";

export type MaskCanvasHandle = {
  exportMaskBlob: () => Promise<Blob | null>;
  clear: () => void;
};

export const MaskCanvas = forwardRef<MaskCanvasHandle>(function MaskCanvas(_, ref) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hasMask, setHasMask] = useState(false);

  useImperativeHandle(ref, () => ({
    exportMaskBlob: async () => {
      if (!hasMask || !canvasRef.current) return null;
      return new Promise((resolve) => canvasRef.current?.toBlob((blob) => resolve(blob), "image/png"));
    },
    clear: () => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (canvas && ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
      setHasMask(false);
    },
  }));

  function markMask(event: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const rect = canvas.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * canvas.width;
    const y = ((event.clientY - rect.top) / rect.height) * canvas.height;
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.beginPath();
    ctx.arc(x, y, 18, 0, Math.PI * 2);
    ctx.fill();
    setHasMask(true);
  }

  return (
    <div className="rounded-lg border border-black/10 bg-[#252525] p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-white">Edit Mask</span>
        <span className="text-xs text-white/70">{hasMask ? "Mask ready" : "Optional"}</span>
      </div>
      <canvas
        ref={canvasRef}
        width={640}
        height={360}
        onPointerDown={markMask}
        onPointerMove={(event) => {
          if (event.buttons === 1) markMask(event);
        }}
        className="aspect-video w-full cursor-crosshair rounded-lg border border-white/15 bg-black/20"
        aria-label="Edit mask canvas"
      />
    </div>
  );
});
