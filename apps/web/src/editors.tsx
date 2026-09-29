import { lazy, Suspense } from "react";
import type { ComponentProps } from "react";
const LazyCodeEditor = lazy(() => import("./components/CodeEditor"));
export function CodeEditor(props: ComponentProps<typeof LazyCodeEditor>) {
  return (
    <Suspense fallback={<div className="editor-loading">Loading…</div>}>
      <LazyCodeEditor {...props} />
    </Suspense>
  );
}
const LazyPDFViewer = lazy(() => import("./components/PDFViewer"));
export function PDFViewer(props: ComponentProps<typeof LazyPDFViewer>) {
  return (
    <Suspense fallback={<div className="editor-loading">Loading…</div>}>
      <LazyPDFViewer {...props} />
    </Suspense>
  );
}
const LazyLiveTerminal = lazy(() => import("./components/LiveTerminal"));
export function LiveTerminal(props: ComponentProps<typeof LazyLiveTerminal>) {
  return (
    <Suspense fallback={<div className="editor-loading">Loading…</div>}>
      <LazyLiveTerminal {...props} />
    </Suspense>
  );
}
const LazyFormula = lazy(() => import("./components/Formula"));
export function Formula(props: ComponentProps<typeof LazyFormula>) {
  return (
    <Suspense fallback={<div className="editor-loading">Loading…</div>}>
      <LazyFormula {...props} />
    </Suspense>
  );
}
