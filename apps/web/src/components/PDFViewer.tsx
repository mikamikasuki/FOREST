import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import { IconButton, ErrorBox } from "../ui";
pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;
export default function PDFViewer({ url, initialPage = 1 }: { url: string; initialPage?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [scale, setScale] = useState(1.25);
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    setError("");
    setPdf(null);
    const loading = pdfjs.getDocument({ url, withCredentials: true });
    loading.promise
      .then((doc) => {
        if (current) {
          setPdf(doc);
          setPage(Math.max(1, Math.min(initialPage, doc.numPages)));
        }
      })
      .catch((e) => {
        if (current) setError(e.message);
      });
    return () => {
      current = false;
      void loading.destroy();
    };
  }, [url]);
  useEffect(() => { if (pdf) setPage(Math.max(1, Math.min(initialPage, pdf.numPages))); }, [initialPage, pdf]);
  useEffect(() => {
    let cancelled = false;
    let task: pdfjs.RenderTask | undefined;
    if (pdf && canvas.current)
      pdf
        .getPage(page)
        .then((p) => {
          if (cancelled || !canvas.current) return;
          const viewport = p.getViewport({ scale });
          const context = canvas.current.getContext("2d")!;
          canvas.current.height = viewport.height;
          canvas.current.width = viewport.width;
          task = p.render({ canvasContext: context, viewport });
          return task.promise;
        })
        .catch((e) => {
          if (!cancelled && e.name !== "RenderingCancelledException")
            setError(e.message);
        });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, page, scale]);
  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <IconButton
          label="Previous page"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
        >
          <ChevronLeft size={16} />
        </IconButton>
        <span>
          {page} / {pdf?.numPages || "—"}
        </span>
        <IconButton
          label="Next page"
          disabled={!pdf || page >= pdf.numPages}
          onClick={() => setPage(page + 1)}
        >
          <ChevronRight size={16} />
        </IconButton>
        <IconButton
          label="Zoom out"
          onClick={() => setScale(Math.max(0.5, scale - 0.15))}
        >
          <ZoomOut size={16} />
        </IconButton>
        <IconButton
          label="Zoom in"
          onClick={() => setScale(Math.min(3, scale + 0.15))}
        >
          <ZoomIn size={16} />
        </IconButton>
      </div>
      <ErrorBox error={error} />
      <div className="pdf-pages">
        <canvas ref={canvas} />
      </div>
    </div>
  );
}
