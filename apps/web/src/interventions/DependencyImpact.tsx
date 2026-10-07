import { NavLink } from "react-router-dom";
import type { Json } from "../api";
import { useUI } from "../ui";

export function DependencyImpact({
  projectId,
  data,
}: {
  projectId: string;
  data: Json;
}) {
  const { t } = useUI();
  const stale = data.stale_dependencies || [];
  if (!stale.length) return null;
  return (
    <section
      className="surface progress-panel"
      aria-label="Changed evidence locations"
    >
      <h3>
        {t(
          "关联证据变化，需要检查以下位置",
          "Bound evidence changed; review these locations",
        )}
      </h3>
      <ul>
        {stale.map((binding: Json, index: number) => (
          <li key={index}>
            <strong>{binding.target_path || "/"}</strong> ·{" "}
            {binding.source_kind} · {binding.source_id}
            {binding.metric_pointer && <span> · {binding.metric_pointer}</span>}
            {binding.source_kind === "run" && (
              <NavLink
                to={`/projects/${projectId}/runs?run=${binding.source_id}`}
              >
                {" "}
                {t("查看运行", "Inspect run")}
              </NavLink>
            )}
            {binding.target_artifact_path && (
              <NavLink
                to={`/projects/${projectId}/files?path=${encodeURIComponent(binding.target_artifact_path)}`}
              >
                {" "}
                {t("查看原文位置", "Inspect authored artifact")}
              </NavLink>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
