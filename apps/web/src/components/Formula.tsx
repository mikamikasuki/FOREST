import katex from "katex";
import "katex/dist/katex.min.css";
export default function Formula({ source }: { source: string }) {
  return (
    <div
      className="formula-preview"
      dangerouslySetInnerHTML={{
        __html: katex.renderToString(source, {
          displayMode: true,
          throwOnError: false,
          trust: false,
          strict: "ignore",
        }),
      }}
    />
  );
}
