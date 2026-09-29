import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import { useUI } from "../ui";
(globalThis as any).MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });
export default function CodeEditor({
  value,
  onChange,
  language = "python",
  height = "100%",
  readOnly = false,
}: {
  value: string;
  onChange?: (v: string) => void;
  language?: string;
  height?: string;
  readOnly?: boolean;
}) {
  const { theme } = useUI();
  return (
    <Editor
      height={height}
      value={value}
      onChange={(v) => onChange?.(v || "")}
      language={language}
      theme={theme === "dark" ? "vs-dark" : "vs"}
      options={{
        fontSize: 12,
        fontFamily: '"SFMono-Regular", Consolas, monospace',
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        padding: { top: 16 },
        wordWrap: "on",
        automaticLayout: true,
        readOnly,
      }}
    />
  );
}
