import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Download,
  Presentation,
  X,
} from "lucide-react";
import type { Run } from "./api";
import { download, formatDate, uid } from "./api";
import { Badge, Button, IconButton, useUI } from "./ui";

export const demoSteps = [
  {
    id: "A",
    path: "overview",
    zh: "问题与证据",
    en: "Question & evidence",
    description: "查看研究问题、预算与已完成运行。",
    english: "Review the question, budget, and completed runs.",
    related: "library",
    relatedZh: "查看原始文献",
    relatedEn: "Original sources",
  },
  {
    id: "B",
    path: "workspace",
    zh: "研究路线",
    en: "Executed paths",
    description: "查看节点、分支与运行输出。",
    english: "Inspect nodes, branches, and run output.",
  },
  {
    id: "C",
    path: "workspace",
    zh: "编辑与影响",
    en: "Edit & impact",
    description: "编辑节点指令或配置，通过路线操作预览影响。",
    english: "Edit a node. Use Path actions to preview the impact.",
  },
  {
    id: "D",
    path: "workspace",
    zh: "分叉与新运行",
    en: "Fork & execute",
    description: "在检查器中分叉路线，再运行节点。",
    english: "Choose Fork branch in the inspector, then Run node.",
  },
  {
    id: "E",
    path: "theory",
    zh: "机制与出处",
    en: "Mechanism & sources",
    description: "查看理论假设、检查结果与文献来源。",
    english: "Review assumptions, checks, and sources.",
    related: "library",
    relatedZh: "核对文献出处",
    relatedEn: "Inspect source evidence",
  },
  {
    id: "F",
    path: "figures",
    zh: "数据与图表",
    en: "Data & figures",
    description: "查看数据，编辑并重新生成图表。",
    english: "Review data, edit a figure, and render it again.",
    related: "data",
    relatedZh: "查看实测数据",
    relatedEn: "Measured data",
  },
  {
    id: "G",
    path: "paper",
    zh: "论文与 PDF",
    en: "Manuscript & PDF",
    description: "编辑稿件并编译 PDF。",
    english: "Edit the manuscript and compile the PDF.",
  },
  {
    id: "H",
    path: "overview",
    zh: "暂停与接管",
    en: "Pause & take over",
    description: "暂停、继续研究，或切换为 Manual 模式。",
    english: "Pause or resume research, or switch to Manual.",
    related: "workspace",
    relatedZh: "打开运行控制",
    relatedEn: "Open run controls",
  },
  {
    id: "I",
    path: "overview",
    zh: "导出研究材料",
    en: "Export materials",
    description: "导出项目，或从论文页面导出源文件。",
    english: "Export the project, or export paper sources from the Paper page.",
    related: "paper",
    relatedZh: "打开论文导出",
    relatedEn: "Paper export",
  },
] as const;

export function demoLink(
  path: string,
  search = typeof window === "undefined" ? "" : window.location.search,
) {
  if (
    new URLSearchParams(search).get("demo") !== "1" ||
    !/^\/projects\/[^/]+\//.test(path)
  )
    return path;
  const [pathname, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  params.set("demo", "1");
  return `${pathname}?${params}`;
}

export function partitionDemoRuns(runs: Run[], enteredAt: number) {
  const completed = runs.filter((run) =>
    ["completed", "succeeded"].includes(run.status),
  );
  const finished = (run: Run) =>
    new Date(run.finished_at || run.created_at).getTime();
  return {
    prior: completed.filter((run) => finished(run) < enteredAt),
    completedNow: completed.filter((run) => finished(run) >= enteredAt),
    running: runs.filter((run) => run.status === "running"),
    queued: runs.filter((run) => run.status === "queued"),
    paused: runs.filter((run) => run.status === "paused"),
  };
}

export function DemoRunSummary({
  runs,
  enteredAt,
  available,
}: {
  runs: Run[];
  enteredAt: number;
  available: boolean;
}) {
  const { t } = useUI();
  if (!available)
    return (
      <span className="demo-run-summary">
        {t("正在读取真实运行状态…", "Reading actual execution status…")}
      </span>
    );
  const groups = partitionDemoRuns(runs, enteredAt);
  return (
    <div
      className="demo-run-summary"
      aria-label={t("运行时间范围", "Execution timing")}
    >
      <span>
        {t("进入展示前已完成", "Completed before entry")}:{" "}
        <strong>{groups.prior.length}</strong>
      </span>
      <span>
        {t("当前正在运行", "Running now")}:{" "}
        <strong>{groups.running.length}</strong>
      </span>
      <span>
        {t("排队 / 暂停", "Queued / paused")}:{" "}
        <strong>
          {groups.queued.length} / {groups.paused.length}
        </strong>
      </span>
      <span>
        {t("进入展示后完成", "Completed since entry")}:{" "}
        <strong>{groups.completedNow.length}</strong>
      </span>
    </div>
  );
}

export function DemoGuide({
  projectId,
  runs,
  available,
}: {
  projectId: string;
  runs: Run[];
  available: boolean;
}) {
  const { t, action } = useUI();
  const location = useLocation();
  const navigate = useNavigate();
  const panel = useRef<HTMLElement>(null);
  const [enteredAt] = useState(() => Date.now());
  const [collapsed, setCollapsed] = useState(false);
  const [busy, setBusy] = useState(false);
  const selected = new URLSearchParams(location.search).get("demo_step");
  const index = Math.max(
    0,
    selected
      ? demoSteps.findIndex((step) => step.id === selected)
      : demoSteps.findIndex((step) =>
          location.pathname.endsWith("/" + step.path),
        ),
  );
  const step = demoSteps[index];
  const link = (target: (typeof demoSteps)[number]) =>
    `/projects/${projectId}/${target.path}?demo=1&demo_step=${target.id}`;
  useEffect(() => {
    const element = panel.current;
    if (!element) return;
    const parent = element.parentElement!;
    const update = () =>
      parent.style.setProperty(
        "--demo-guide-height",
        `${element.getBoundingClientRect().height}px`,
      );
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => {
      observer.disconnect();
      parent.style.removeProperty("--demo-guide-height");
    };
  }, []);
  const close = () => {
    const query = new URLSearchParams(location.search);
    query.delete("demo");
    query.delete("demo_step");
    navigate(`${location.pathname}${query.size ? "?" + query : ""}`, {
      replace: true,
    });
  };
  return (
    <section
      className={`demo-guide ${collapsed ? "collapsed" : ""}`}
      ref={panel}
      aria-label={t("项目导览", "Project walkthrough")}
    >
      <div className="demo-guide-header">
        <Presentation size={17} />
        <strong>{t("项目导览", "Project walkthrough")}</strong>
        <div className="toolbar-spacer" />
        <IconButton
          label={t(
            collapsed ? "展开导览" : "收起导览",
            collapsed ? "Expand walkthrough" : "Collapse walkthrough",
          )}
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
        </IconButton>
        <IconButton
          label={t("退出演示模式", "Exit walkthrough")}
          onClick={close}
        >
          <X size={16} />
        </IconButton>
      </div>
      {!collapsed && (
        <>
          <nav
            className="demo-steps"
            aria-label={t("演示路线 A 至 I", "Walkthrough A to I")}
          >
            {demoSteps.map((item) => (
              <NavLink
                key={item.id}
                className={step.id === item.id ? "selected" : ""}
                to={link(item)}
                aria-current={step.id === item.id ? "step" : undefined}
              >
                <span>{item.id}</span>
                {t(item.zh, item.en)}
              </NavLink>
            ))}
          </nav>
          <div className="demo-step-detail">
            <p>{t(step.description, step.english)}</p>
            <div className="inline-actions">
              {"related" in step && (
                <NavLink
                  className="button"
                  to={`/projects/${projectId}/${step.related}?demo=1&demo_step=${step.id}`}
                >
                  {t(step.relatedZh, step.relatedEn)}
                </NavLink>
              )}
              {index > 0 && (
                <NavLink className="button" to={link(demoSteps[index - 1])}>
                  <ArrowLeft size={13} />
                  {t("上一步", "Previous")}
                </NavLink>
              )}
              {index < demoSteps.length - 1 ? (
                <NavLink
                  className="button primary"
                  to={link(demoSteps[index + 1])}
                >
                  {t("下一步", "Next")}
                  <ArrowRight size={13} />
                </NavLink>
              ) : (
                <Button
                  className="primary"
                  busy={busy}
                  onClick={async () => {
                    setBusy(true);
                    await action(() =>
                      download(`/projects/${projectId}/export`, {
                        request_id: uid(),
                      }),
                    );
                    setBusy(false);
                  }}
                >
                  <Download size={14} />
                  {t("导出当前项目", "Export current project")}
                </Button>
              )}
            </div>
          </div>
          <DemoRunSummary
            runs={runs}
            enteredAt={enteredAt}
            available={available}
          />
          {available && (
            <details className="demo-records">
              <summary>{t("运行历史", "Run history")}</summary>
              <div>
                {runs.slice(0, 12).map((run) => (
                  <span key={run.id}>
                    <code>{run.id.slice(0, 8)}</code>
                    {run.kind}
                    <Badge status={run.status} />
                    <small>
                      {formatDate(
                        run.finished_at || run.started_at || run.created_at,
                      )}
                    </small>
                  </span>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
