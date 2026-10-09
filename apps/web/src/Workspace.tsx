import { RunEvidence } from "./RunEvidence";
import { ExecutionSettings } from "./ExecutionSettings";
import { useState, useEffect, useMemo, useCallback } from "react";
import { useParams, NavLink } from "react-router-dom";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  applyNodeChanges,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import type { NodeProps, Node as FlowNode, NodeChange } from "@xyflow/react";
import {
  Plus,
  Play,
  GitBranch,
  PanelRightClose,
  ChevronDown,
  ChevronUp,
  Network,
  Focus,
  FileText,
  Search,
  Undo2,
  Redo2,
  Workflow,
  SquareTerminal,
  ListTree,
  X,
  FlaskConical,
  BookOpen,
  Target,
  Lightbulb,
  Check,
  Clock,
  AlertTriangle,
  Square,
  Pause,
  RefreshCw,
  ArrowUpRight,
  Send,
  Settings2,
  Copy,
  Trash2,
  Scissors,
  Layers,
  ArrowDownToLine,
  Split,
  ChevronRight,
  Code,
  MessageSquare,
  Download,
  GitCompare,
} from "lucide-react";
import {
  api,
  uid,
  hasCycle,
  parseJson,
  formatDate,
  executionLinks,
} from "./api";
import type { Graph, ResearchNode, Run, Json } from "./api";
import {
  useUI,
  useLoad,
  Button,
  IconButton,
  Badge,
  Modal,
  Field,
  Empty,
  ErrorBox,
  JsonView,
  Tabs,
} from "./ui";
import { CodeEditor, LiveTerminal } from "./editors";
import { PaperPage } from "./pages";
import "@xyflow/react/dist/style.css";
const nodeKinds = [
  ["goal", "研究目标", "Research goal"],
  ["literature", "文献检索", "Literature"],
  ["idea", "研究想法", "Idea"],
  ["hypothesis", "研究假设", "Hypothesis"],
  ["theory", "理论推导", "Derivation"],
  ["baseline", "基线复现", "Baseline"],
  ["implementation", "实现修改", "Implementation"],
  ["experiment", "实验", "Experiment"],
  ["evaluation", "评价", "Evaluation"],
  ["analysis", "统计分析", "Analysis"],
  ["verification", "证据验证", "Evidence verification"],
  ["figure", "学术图表", "Figure"],
  ["paper", "论文", "Paper"],
  ["review", "审核", "Review"],
  ["decision", "人工决策", "Decision"],
  ["milestone", "里程碑", "Milestone"],
] as const;
const icons: Record<string, any> = {
  goal: Target,
  literature: BookOpen,
  idea: Lightbulb,
  hypothesis: Lightbulb,
  theory: Code,
  baseline: FlaskConical,
  experiment: FlaskConical,
  paper: FileText,
};
function ResearchCard({ data, selected }: NodeProps) {
  const node = data.node as ResearchNode;
  const { t } = useUI();
  const Icon = icons[node.type] || Workflow;
  const kind = nodeKinds.find((k) => k[0] === node.type);
  const metric = Object.entries(node.config?.metrics || {}).slice(0, 1);
  return (
    <div
      className={`research-node ${selected ? "selected" : ""} node-${node.execution_status}`}
    >
      <Handle type="target" position={Position.Top} />
      <div className="node-type">
        <span className={`node-icon type-${node.type}`}>
          <Icon size={14} />
        </span>
        {kind ? t(kind[1], kind[2]) : node.type}
        <span className="node-revision">r{node.revision}</span>
      </div>
      <h3>{node.title}</h3>
      {node.instructions && <p>{node.instructions}</p>}
      {metric.length > 0 && (
        <div className="node-metric">
          {metric.map(([key, value]) => (
            <span key={key}>
              {key} <b>{String(value)}</b>
            </span>
          ))}
        </div>
      )}
      <div className="node-footer">
        <Badge status={node.execution_status} />
        <span>
          {node.research_status === "not_evaluated"
            ? t("待评价", "Not evaluated")
            : node.research_status}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
const nodeTypes = { research: ResearchCard };
const operations = [
  "add_node",
  "edit_node",
  "delete_node",
  "add_dependency",
  "remove_dependency",
  "fork_branch",
  "clone_subtree",
  "insert_before",
  "insert_after",
  "reparent_subtree",
  "merge_branches",
  "split_node",
  "group_nodes",
  "prune_branch",
  "restore_branch",
  "set_main_branch",
  "apply_instruction_patch",
  "undo",
  "redo",
];
const templates: Record<string, Json> = {
  add_node: {
    type: "experiment",
    title: "New experiment",
    instructions: "",
    config: {},
  },
  delete_node: { strategy: "reconnect" },
  add_dependency: { source: "", target: "", relation: "depends_on" },
  remove_dependency: { source: "", target: "" },
  fork_branch: {
    name: "New route",
    copy_policy: { code: true, data: "reference", results: false },
  },
  clone_subtree: {
    copy_policy: { code: true, data: "reference", results: false },
  },
  insert_before: { node: { type: "analysis", title: "Analysis" } },
  insert_after: { node: { type: "analysis", title: "Analysis" } },
  reparent_subtree: { parent_id: "" },
  merge_branches: { left: "", right: "", resolution: {} },
  split_node: {
    parts: [
      { title: "Step one", instructions: "" },
      { title: "Step two", instructions: "" },
    ],
  },
  group_nodes: { title: "Subflow" },
  prune_branch: { branch_id: "" },
  restore_branch: { branch_id: "" },
  set_main_branch: { branch_id: "" },
  apply_instruction_patch: { instructions: "" },
  edit_node: { title: "" },
};
export function Workspace() {
  return (
    <ReactFlowProvider>
      <WorkspaceInner />
    </ReactFlowProvider>
  );
}
function WorkspaceInner() {
  const { id } = useParams();
  const { t, action } = useUI();
  const {
    data: graph,
    setData: setGraph,
    error,
    reload,
  } = useLoad<Graph>(`/projects/${id}/graph`, {
    project_id: id!,
    revision: 0,
    nodes: [],
    edges: [],
    branches: [],
  });
  const { data: runs, reload: reloadRuns } = useLoad<Run[]>(
    `/projects/${id}/runs?limit=500`,
    [],
  );
  const [selected, setSelected] = useState<string>("");
  const [selection, setSelection] = useState<string[]>([]);
  const [view, setView] = useState("forest");
  const [inspector, setInspector] = useState(
    () => !window.matchMedia("(max-width: 820px)").matches,
  );
  const [dock, setDock] = useState(true);
  const [dockTab, setDockTab] = useState("runs");
  const [create, setCreate] = useState(false);
  const [command, setCommand] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [commandParams, setCommandParams] = useState<Json | undefined>();
  const [operation, setOperation] = useState("fork_branch");
  const [query, setQuery] = useState("");
  const [focusTarget, setFocusTarget] = useState("");
  const [revealedNode, setRevealedNode] = useState("");
  const [branch, setBranch] = useState("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () =>
      new Set(
        JSON.parse(localStorage.getItem(`forest-collapsed-${id}`) || "[]"),
      ),
  );
  useEffect(
    () =>
      localStorage.setItem(
        `forest-collapsed-${id}`,
        JSON.stringify([...collapsed]),
      ),
    [collapsed, id],
  );
  const [flowNodes, setFlowNodes] = useState<FlowNode[]>([]);
  const [suggest, setSuggest] = useState("");
  const [scope, setScope] = useState("node");
  const [busy, setBusy] = useState(false);
  const [impact, setImpact] = useState<Json | null>(null);
  const [pending, setPending] = useState<Json | null>(null);
  const flow = useReactFlow();
  const initialFitViewOptions = useMemo(
    () => ({
      minZoom: window.innerWidth < 820 ? 0.75 : 0.7,
      maxZoom: 1,
      padding: 0.2,
    }),
    [],
  );
  const node = graph.nodes.find((n) => n.id === selected);
  const request = useCallback(
    async (
      op: string,
      targets: string[] = [],
      params: Json = {},
      preview = false,
      expectedRevision = graph.revision,
    ) => {
      const result = await api(
        `/projects/${id}/graph/${preview ? "preview" : "commands"}`,
        "POST",
        {
          request_id: uid(),
          expected_revision: expectedRevision,
          operation: op,
          targets,
          params,
          run: false,
        },
      );
      if (!preview) {
        if (result.graph) setGraph(result.graph);
        else await reload();
        void reloadRuns();
      }
      return result;
    },
    [id, graph.revision, setGraph, reload, reloadRuns],
  );
  useEffect(() => {
    const refresh = () => {
      void reload();
      void reloadRuns();
    };
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reload, reloadRuns]);
  useEffect(() => {
    if (!selected && graph.nodes.length) setSelected(graph.nodes[0].id);
  }, [graph.nodes, selected]);
  const linkedEdges = useMemo(
    () => executionLinks(graph),
    [graph.nodes, graph.edges],
  );
  const hidden = useMemo(() => {
    const out = new Set<string>();
    for (const root of collapsed) {
      const todo = linkedEdges
        .filter((e) => e.source === root)
        .map((e) => e.target);
      while (todo.length) {
        const target = todo.pop()!;
        if (out.has(target)) continue;
        out.add(target);
        linkedEdges
          .filter((e) => e.source === target)
          .forEach((e) => todo.push(e.target));
      }
    }
    return out;
  }, [linkedEdges, collapsed]);
  useEffect(() => {
    setFlowNodes(
      graph.nodes
        .filter(
          (n) =>
            (!n.archived || n.id === revealedNode) &&
            !hidden.has(n.id) &&
            (branch === "all" || n.branch_id === branch),
        )
        .map((n, index) => ({
          id: n.id,
          type: "research",
          position: n.position || {
            x: 300 + (index % 3) * 290,
            y: 100 + Math.floor(index / 3) * 220,
          },
          data: { node: n },
          selected: n.id === selected,
        })),
    );
  }, [graph.nodes, selected, hidden, branch, revealedNode]);
  useEffect(() => {
    if (!focusTarget || !flow.viewportInitialized) return;
    const target = flowNodes.find((n) => n.id === focusTarget);
    if (!target) return;
    // Offscreen cards may never have been measured. Center from their stored
    // position after the filters and inspector have rendered, then let viewport
    // virtualization mount the card. fitView cannot reliably fit that card yet.
    const frame = requestAnimationFrame(() => {
      const measured = flow.getNode(target.id)?.measured;
      setFocusTarget("");
      void flow.setCenter(
        target.position.x + (measured?.width ?? 246) / 2,
        target.position.y + (measured?.height ?? 220) / 2,
        { zoom: 1, duration: 250 },
      );
    });
    return () => cancelAnimationFrame(frame);
  }, [focusTarget, flowNodes, flow]);
  const revealSearchResult = (target: ResearchNode) => {
    setSelected(target.id);
    setRevealedNode(target.id);
    setInspector(true);
    setQuery("");
    if (branch !== "all" && branch !== target.branch_id) setBranch("all");
    setCollapsed((current) => {
      if (!current.size) return current;
      const parents = new Map<string, string[]>();
      for (const edge of linkedEdges) {
        const existing = parents.get(edge.target) || [];
        existing.push(edge.source);
        parents.set(edge.target, existing);
      }
      const ancestors = new Set<string>();
      const pending = [...(parents.get(target.id) || [])];
      while (pending.length) {
        const ancestor = pending.pop()!;
        if (ancestors.has(ancestor)) continue;
        ancestors.add(ancestor);
        pending.push(...(parents.get(ancestor) || []));
      }
      return new Set([...current].filter((id) => !ancestors.has(id)));
    });
    setFocusTarget(target.id);
  };
  const edges = useMemo(
    () =>
      linkedEdges.map((e) => ({
        ...e,
        type: "smoothstep",
        animated: runs.some(
          (r) => r.node_id === e.target && r.status === "running",
        ),
        label: e.implicit
          ? "input binding"
          : ["depends_on", "consumes"].includes(e.relation)
            ? undefined
            : e.relation,
        style: {
          stroke: e.relation === "depends_on" ? "#9bad9e" : "#a7b2bd",
          strokeWidth: 1.5,
          strokeDasharray: [
            "history",
            "derived_from",
            "references",
            "evidence",
            "cites",
            "group",
          ].includes(e.relation)
            ? "5 5"
            : undefined,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 15,
          height: 15,
          color: "#9bad9e",
        },
      })),
    [linkedEdges, runs],
  );
  const onSelectionChange = useCallback(
    ({ nodes }: { nodes: FlowNode[] }) =>
      setSelection((previous) => {
        const next = nodes.map((n) => n.id);
        return previous.length === next.length &&
          previous.every((id, index) => id === next[index])
          ? previous
          : next;
      }),
    [],
  );
  const run = async (runScope = "single") => {
    if (!node) return;
    setBusy(true);
    await action(
      async () => {
        await api(`/nodes/${node.id}/run`, "POST", {
          request_id: uid(),
          scope: runScope,
        });
        await reloadRuns();
        setDock(true);
        setDockTab("runs");
      },
      t("任务已加入队列", "Task queued"),
    );
    setBusy(false);
  };
  const layout = () =>
    action(async () => {
      let current = graph;
      const depth = new Map<string, number>();
      const visit = (id: string, trail = new Set<string>()): number => {
        if (trail.has(id)) return 0;
        if (depth.has(id)) return depth.get(id)!;
        const next = new Set(trail).add(id);
        const parents = current.edges.filter(
          (e) =>
            e.target === id && ["depends_on", "consumes"].includes(e.relation),
        );
        const level = parents.length
          ? 1 + Math.max(...parents.map((e) => visit(e.source, next)))
          : 0;
        depth.set(id, level);
        return level;
      };
      const rows: Record<number, number> = {};
      for (const n of current.nodes) {
        const y = visit(n.id);
        const x = rows[y] || 0;
        rows[y] = x + 1;
        const result = await api(`/projects/${id}/graph/commands`, "POST", {
          request_id: uid(),
          expected_revision: current.revision,
          operation: "edit_node",
          targets: [n.id],
          params: { position: { x: 80 + x * 300, y: 60 + y * 230 } },
          run: false,
        });
        current = result.graph || (await api(`/projects/${id}/graph`));
      }
      setGraph(current);
      setTimeout(() => flow.fitView({ padding: 0.25 }), 150);
    });
  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if (
        ["INPUT", "TEXTAREA"].includes((e.target as HTMLElement)?.tagName) ||
        (e.target as HTMLElement)?.closest(".monaco-editor")
      )
        return;
      if ((e.metaKey || e.ctrlKey) && e.key === "z") {
        e.preventDefault();
        void action(() => request(e.shiftKey ? "redo" : "undo"));
      }
      if (e.key === "Escape") setSelected("");
      if (e.key === "f") void flow.fitView({ padding: 0.2 });
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [request, flow, action]);
  return (
    <div className="workspace">
      <div className="workspace-toolbar">
        <div className="view-switch">
          <button
            className={view === "forest" ? "active" : ""}
            onClick={() => setView("forest")}
          >
            <Network size={15} />
            Forest
          </button>
          <button
            className={view === "focus" ? "active" : ""}
            onClick={() => setView("focus")}
          >
            <Focus size={15} />
            Focus
          </button>
          <button
            className={view === "paper" ? "active" : ""}
            onClick={() => setView("paper")}
          >
            <FileText size={15} />
            Paper
          </button>
        </div>
        <span className="toolbar-divider" />
        <GitBranch size={14} />
        <select
          aria-label="Current branch"
          className="branch-select"
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
        >
          <option value="all">{t("全部研究路线", "All research paths")}</option>
          {graph.branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.is_main ? "● " : ""}
              {b.name}
            </option>
          ))}
        </select>
        <IconButton
          label={t("比较研究路线", "Compare research paths")}
          disabled={graph.branches.length < 2}
          onClick={() => setCompareOpen(true)}
        >
          <GitCompare size={16} />
        </IconButton>
        <div className="toolbar-spacer" />
        <IconButton
          label={t("撤销图编辑", "Undo graph edit")}
          onClick={() => action(() => request("undo"))}
        >
          <Undo2 size={16} />
        </IconButton>
        <IconButton
          label={t("重做图编辑", "Redo graph edit")}
          onClick={() => action(() => request("redo"))}
        >
          <Redo2 size={16} />
        </IconButton>
        {selection.length > 1 && (
          <Button
            onClick={() =>
              action(async () => {
                await api(`/projects/${id}/runs/selected`, "POST", {
                  node_ids: selection,
                  request_id: uid(),
                });
                await reloadRuns();
                setDock(true);
              })
            }
          >
            <Play size={13} />
            {t("运行选中", "Run selected")} ({selection.length})
          </Button>
        )}
        <Button onClick={() => setCreate(true)}>
          <Plus size={15} />
          {t("添加节点", "Add node")}
        </Button>
        <Button
          className="primary"
          busy={busy}
          disabled={!node}
          onClick={() => run()}
        >
          <Play size={13} />
          {t("运行节点", "Run node")}
        </Button>
        <IconButton
          label={t("切换检查器", "Toggle inspector")}
          aria-expanded={inspector && view === "forest"}
          aria-controls="node-inspector"
          onClick={() => setInspector(!inspector)}
        >
          <PanelRightClose size={17} />
        </IconButton>
      </div>
      <ErrorBox error={error} retry={reload} />
      {view === "paper" ? (
        <div className="workspace-paper">
          <PaperPage embedded />
        </div>
      ) : (
        <div className="workspace-main">
          <div className="canvas-and-dock">
            <div className="canvas-region">
              {view === "forest" ? (
                <>
                  <ReactFlow
                    onlyRenderVisibleElements
                    proOptions={{ hideAttribution: true }}
                    nodes={flowNodes}
                    edges={edges}
                    nodeTypes={nodeTypes}
                    fitView
                    fitViewOptions={initialFitViewOptions}
                    minZoom={0.15}
                    maxZoom={2}
                    onNodesChange={(changes: NodeChange[]) =>
                      setFlowNodes((n) => applyNodeChanges(changes, n))
                    }
                    onNodeClick={(_, n) => setSelected(n.id)}
                    onNodeDoubleClick={(_, n) => {
                      setSelected(n.id);
                      setView("focus");
                    }}
                    onNodeContextMenu={(event, n) => {
                      event.preventDefault();
                      setSelected(n.id);
                      setOperation("fork_branch");
                      setCommand(true);
                    }}
                    onSelectionChange={onSelectionChange}
                    onNodeDragStop={(_, n) =>
                      action(() =>
                        request("edit_node", [n.id], { position: n.position }),
                      )
                    }
                    onConnect={(c) => {
                      if (c.source && c.target) {
                        if (hasCycle(graph, c.source, c.target)) {
                          void action(async () => {
                            throw new Error(
                              t(
                                "此连线会形成执行依赖环。",
                                "This connection would create a dependency cycle.",
                              ),
                            );
                          });
                          return;
                        }
                        void action(() =>
                          request("add_dependency", [], {
                            source: c.source,
                            target: c.target,
                            relation: "depends_on",
                          }),
                        );
                      }
                    }}
                    deleteKeyCode={null}
                  >
                    <Background color="#cbd4cb" gap={24} size={1} />
                    <Controls showInteractive={false} />
                    <MiniMap
                      pannable
                      zoomable
                      nodeColor="#94b59c"
                      maskColor="rgba(242,245,240,.7)"
                    />
                  </ReactFlow>
                  <div className="canvas-topline">
                    <label className="search-input">
                      <Search size={14} />
                      <input
                        aria-label="Search nodes"
                        placeholder={t("查找节点…", "Find a node…")}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                    </label>
                    {query && (
                      <div className="node-search-results">
                        {graph.nodes
                          .filter((n) =>
                            n.title.toLowerCase().includes(query.toLowerCase()),
                          )
                          .map((n) => (
                            <button
                              key={n.id}
                              onClick={() => revealSearchResult(n)}
                            >
                              {n.title}
                              <ArrowUpRight size={13} />
                            </button>
                          ))}
                      </div>
                    )}
                  </div>
                  {!graph.nodes.length && (
                    <div className="canvas-empty">
                      <Empty
                        title={t("暂无节点", "No nodes")}
                        action={
                          <Button
                            className="primary"
                            onClick={() => setCreate(true)}
                          >
                            <Plus size={15} />
                            {t("添加节点", "Add node")}
                          </Button>
                        }
                      />
                    </div>
                  )}
                  <div className="canvas-actions">
                    <Button onClick={layout}>
                      <Workflow size={14} />
                      {t("整理布局", "Auto layout")}
                    </Button>
                    <Button
                      disabled={!selected}
                      onClick={() =>
                        setCollapsed((s) => {
                          const next = new Set(s);
                          if (next.has(selected)) next.delete(selected);
                          else next.add(selected);
                          return next;
                        })
                      }
                    >
                      <Layers size={14} />
                      {collapsed.has(selected)
                        ? t("展开", "Expand")
                        : t("折叠", "Collapse")}
                    </Button>
                    <Button
                      onClick={() => {
                        setOperation("fork_branch");
                        setCommand(true);
                      }}
                    >
                      <Settings2 size={14} />
                      {t("路线操作", "Path actions")}
                    </Button>
                  </div>
                  <div className="canvas-legend">
                    <span>
                      <i />
                      {t("执行依赖", "Execution")}
                    </span>
                    <span>
                      <i className="dashed" />
                      {t("来源 / 引用", "Source / reference")}
                    </span>
                    <span>rev {graph.revision}</span>
                  </div>
                </>
              ) : node ? (
                <div className="focus-editor">
                  <div className="focus-heading">
                    <span className="eyebrow">
                      FOCUS / {node.type.toUpperCase()}
                    </span>
                    <h1>{node.title}</h1>
                    <div>
                      <Badge status={node.execution_status} />
                      <Badge status={node.research_status} />
                    </div>
                  </div>
                  <NodeInspector
                    key={`${node.id}-focus`}
                    node={node}
                    graph={graph}
                    projectId={id!}
                    onSave={async (p, expected) =>
                      request("edit_node", [node.id], p, false, expected)
                    }
                    onRun={run}
                    runs={runs}
                    request={request}
                  />
                </div>
              ) : (
                <Empty
                  title={t("选择一个研究节点", "Select a research node")}
                />
              )}
            </div>
            <div className={`run-dock ${dock ? "open" : ""}`}>
              <div className="dock-header">
                <button
                  className={dockTab === "runs" ? "active" : ""}
                  onClick={() => {
                    setDockTab("runs");
                    setDock(true);
                  }}
                >
                  <ListTree size={14} />
                  {t("运行记录", "Runs")}
                  <span className="count">{runs.length}</span>
                </button>
                <button
                  className={dockTab === "terminal" ? "active" : ""}
                  onClick={() => {
                    setDockTab("terminal");
                    setDock(true);
                  }}
                >
                  <SquareTerminal size={14} />
                  {t("终端", "Terminal")}
                </button>
                <span className="toolbar-spacer" />
                <IconButton
                  label={t("切换运行面板", "Toggle run panel")}
                  onClick={() => setDock(!dock)}
                >
                  {dock ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
                </IconButton>
              </div>
              {dock &&
                (dockTab === "terminal" ? (
                  <LiveTerminal projectId={id!} />
                ) : (
                  <RunPanel
                    runs={runs}
                    reload={reloadRuns}
                    nodes={graph.nodes}
                  />
                ))}
            </div>
          </div>
          {inspector && view === "forest" && (
            <aside
              id="node-inspector"
              className="inspector"
              aria-label={t("节点检查器", "Node inspector")}
            >
              <div className="inspector-heading">
                <span>
                  <Focus size={15} />
                  {t("节点检查器", "Node inspector")}
                </span>
                <IconButton
                  label="Close inspector"
                  onClick={() => setInspector(false)}
                >
                  <X size={15} />
                </IconButton>
              </div>
              {node ? (
                <NodeInspector
                  key={node.id}
                  node={node}
                  graph={graph}
                  projectId={id!}
                  onSave={async (p, expected) =>
                    request("edit_node", [node.id], p, false, expected)
                  }
                  onRun={run}
                  runs={runs}
                  request={request}
                />
              ) : (
                <Empty title={t("选择一个节点", "Select a node")} />
              )}
              <div className="assistant-box">
                <div>
                  <span className="assistant-mark">✳</span>
                  <strong>Path request</strong>
                  <select
                    aria-label="Copilot scope"
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                  >
                    <option value="node">{t("当前节点", "Node")}</option>
                    <option value="branch">{t("当前路线", "Branch")}</option>
                    <option value="project">{t("整个项目", "Project")}</option>
                  </select>
                </div>
                <textarea
                  value={suggest}
                  onChange={(e) => setSuggest(e.target.value)}
                  placeholder={t("输入请求…", "Enter a request…")}
                />
                <div>
                  <IconButton
                    label={t("生成路线建议", "Suggest a path")}
                    disabled={!suggest.trim()}
                    onClick={() =>
                      action(async () => {
                        await api("/research/suggest-paths", "POST", {
                          project_id: id,
                          node_id: node?.id,
                          prompt: suggest,
                          scope,
                        });
                        setSuggest("");
                        await reloadRuns();
                        setDock(true);
                      })
                    }
                  >
                    <Send size={16} />
                  </IconButton>
                </div>
              </div>
            </aside>
          )}
        </div>
      )}
      {create && (
        <AddNode
          onClose={() => setCreate(false)}
          onCreate={async (values) => {
            const result = await request("add_node", [], {
              ...values,
              position: {
                x: 80 + (graph.nodes.length % 3) * 300,
                y: 60 + Math.floor(graph.nodes.length / 3) * 230,
              },
              ...(branch !== "all" ? { branch_id: branch } : {}),
            });
            setCreate(false);
            const added = result.graph?.nodes?.find(
              (n: ResearchNode) => !graph.nodes.some((old) => old.id === n.id),
            );
            if (added) setSelected(added.id);
          }}
        />
      )}
      {compareOpen && (
        <BranchComparison
          graph={graph}
          onClose={() => setCompareOpen(false)}
          onMerge={(left, right) => {
            setCommandParams({
              left,
              right,
              name: "Merged research path",
              resolution: {},
            });
            setOperation("merge_branches");
            setCommand(true);
            setCompareOpen(false);
          }}
        />
      )}
      {command && (
        <CommandModal
          initialParams={commandParams}
          operation={operation}
          selected={selection.length ? selection : selected ? [selected] : []}
          graph={graph}
          onClose={() => {
            setCommand(false);
            setCommandParams(undefined);
          }}
          onPreview={async (op, targets, params) => {
            const result = await request(op, targets, params, true);
            setPending({
              op,
              targets,
              params,
              expectedRevision: graph.revision,
            });
            setImpact(result.impact || result);
          }}
        />
      )}
      {impact && (
        <Modal
          wide
          title={t("变更影响", "Change impact")}
          onClose={() => {
            setImpact(null);
            setPending(null);
          }}
        >
          <p className="muted">
            {t(
              "以下对象来自当前研究图的影响分析。配置修改用于下一次运行。",
              "These objects are computed from the current graph. Configuration changes apply to the next run.",
            )}
          </p>
          <ImpactReview
            impact={impact}
            graph={graph}
            pending={pending}
            onChange={setPending}
          />
          <div className="modal-actions">
            <Button
              onClick={() => {
                setImpact(null);
                setPending(null);
              }}
            >
              {t("返回编辑", "Back to editing")}
            </Button>
            <Button
              className="primary"
              onClick={() =>
                action(async () => {
                  if (pending)
                    await request(
                      pending.op,
                      pending.targets,
                      pending.params,
                      false,
                      pending.expectedRevision,
                    );
                  setImpact(null);
                  setPending(null);
                  setCommand(false);
                })
              }
            >
              {t("应用变更", "Apply changes")}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
function AddNode({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (p: Json) => Promise<void>;
}) {
  const { t, action } = useUI();
  const [type, setType] = useState("goal");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={t("添加研究节点", "Add research node")} onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          await action(() =>
            onCreate({
              type,
              title,
              instructions,
              ...(type === "verification"
                ? { config: { kind: "verification" } }
                : {}),
            }),
          );
          setBusy(false);
        }}
      >
        <Field label={t("节点类型", "Node type")}>
          <select value={type} onChange={(e) => setType(e.target.value)}>
            {nodeKinds.map(([id, zh, en]) => (
              <option key={id} value={id}>
                {t(zh, en)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("节点名称", "Node title")}>
          <input
            required
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t(
              "这一步要解决什么？",
              "What should this step resolve?",
            )}
          />
        </Field>
        <Field label={t("执行指令", "Instructions")}>
          <textarea
            rows={5}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder={t(
              "说明目标、输入和预期产物…",
              "Define the objective, inputs, and expected artifacts…",
            )}
          />
        </Field>
        <div className="modal-actions">
          <Button onClick={onClose} type="button">
            {t("取消", "Cancel")}
          </Button>
          <Button type="submit" className="primary" busy={busy}>
            <Plus size={15} />
            {t("创建节点", "Create node")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function CommandModal({
  operation,
  selected,
  graph,
  onClose,
  onPreview,
  initialParams,
}: {
  initialParams?: Json;
  operation: string;
  selected: string[];
  graph: Graph;
  onClose: () => void;
  onPreview: (op: string, targets: string[], params: Json) => Promise<void>;
}) {
  const { t, action } = useUI();
  const [op, setOp] = useState(operation);
  const [targets, setTargets] = useState(selected);
  const [params, setParams] = useState(
    JSON.stringify(initialParams || templates[operation] || {}, null, 2),
  );
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={t("研究路线操作", "Research path actions")}
      onClose={onClose}
      wide
    >
      <div className="command-form">
        <Field label={t("操作", "Operation")}>
          <select
            value={op}
            onChange={(e) => {
              setOp(e.target.value);
              setParams(
                JSON.stringify(templates[e.target.value] || {}, null, 2),
              );
            }}
          >
            {operations.map((o) => (
              <option key={o}>{o}</option>
            ))}
          </select>
        </Field>
        <Field
          label={t("作用节点（可多选）", "Target nodes (multiple selection)")}
        >
          <select
            multiple
            value={targets}
            onChange={(e) =>
              setTargets([...e.target.selectedOptions].map((o) => o.value))
            }
          >
            {graph.nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.title}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={t("操作参数", "Parameters")}
          hint={t("节点和分支 ID", "Node and branch IDs")}
        >
          <textarea
            className="code-input"
            rows={10}
            value={params}
            onChange={(e) => setParams(e.target.value)}
          />
        </Field>
        <details>
          <summary>{t("节点与分支 ID", "Node and branch IDs")}</summary>
          <JsonView
            value={{
              nodes: graph.nodes.map((n) => ({ id: n.id, title: n.title })),
              branches: graph.branches,
            }}
          />
        </details>
      </div>
      <div className="modal-actions">
        <Button onClick={onClose}>{t("取消", "Cancel")}</Button>
        <Button
          className="primary"
          busy={busy}
          onClick={async () => {
            setBusy(true);
            await action(() => onPreview(op, targets, parseJson(params)));
            setBusy(false);
          }}
        >
          {t("预览影响", "Preview impact")}
          <ArrowUpRight size={14} />
        </Button>
      </div>
    </Modal>
  );
}
function NodeInspector({
  node,
  graph,
  projectId,
  onSave,
  onRun,
  runs,
  request,
}: {
  node: ResearchNode;
  graph: Graph;
  projectId: string;
  onSave: (p: Json, expected?: number) => Promise<any>;
  onRun: (scope?: string) => Promise<void>;
  runs: Run[];
  request: (op: string, targets?: string[], params?: Json) => Promise<any>;
}) {
  const { t, action } = useUI();
  const [tab, setTab] = useState("instructions");
  const [title, setTitle] = useState(node.title);
  const [instructions, setInstructions] = useState(node.instructions || "");
  const [config, setConfig] = useState(
    JSON.stringify(node.config || {}, null, 2),
  );
  const executionConfig = useMemo<Json | null>(() => {
    try {
      const parsed = JSON.parse(config);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed
        : null;
    } catch {
      return null;
    }
  }, [config]);
  const [inputs, setInputs] = useState(
    JSON.stringify(node.inputs || [], null, 2),
  );
  const [overrides, setOverrides] = useState(
    JSON.stringify(node.context_overrides || {}, null, 2),
  );
  const [comment, setComment] = useState("");
  const [stopCurrent, setStopCurrent] = useState(false);
  const [base, setBase] = useState({
    revision: graph.revision,
    nodeRevision: node.revision,
    content: editableNode(node),
  });
  const [conflict, setConflict] = useState(false);
  const [context, setContext] = useState<Json | null>(null);
  const [busy, setBusy] = useState(false);
  const related = runs.filter((r) => r.node_id === node.id);
  const save = async () => {
    if (JSON.stringify(editableNode(node)) !== JSON.stringify(base.content)) {
      setConflict(true);
      return;
    }
    setBusy(true);
    const saved = await action(
      () =>
        onSave(
          {
            title,
            instructions,
            config: parseJson(config),
            inputs: parseJson(inputs),
            context_overrides: parseJson(overrides),
            stop_current_run: stopCurrent,
          },
          base.revision,
        ),
      t(
        "节点已保存；已有运行保留启动配置。",
        "Node saved; existing runs retain their starting configuration.",
      ),
    );
    if (saved)
      setBase({
        revision: saved.revision,
        content: editableNode(
          saved.graph?.nodes?.find((n: ResearchNode) => n.id === node.id) ||
            node,
        ),
        nodeRevision:
          saved.graph?.nodes?.find((n: ResearchNode) => n.id === node.id)
            ?.revision ?? node.revision,
      });
    setBusy(false);
  };
  return (
    <div className="node-inspector-content">
      {conflict && (
        <Modal
          wide
          title={t("节点已在另一处修改", "Node changed elsewhere")}
          onClose={() => setConflict(false)}
        >
          <p className="muted">
            {t(
              "当前编辑已保留。比较最新节点后，重新载入或保留你的编辑。",
              "Your edit is preserved. Compare the latest node, then reload or keep your changes.",
            )}
          </p>
          <JsonView
            value={{
              current_server: {
                title: node.title,
                instructions: node.instructions,
                config: node.config,
              },
              your_edit: { title, instructions, config },
            }}
          />
          <div className="modal-actions">
            <Button
              onClick={() => {
                setTitle(node.title);
                setInstructions(node.instructions);
                setConfig(JSON.stringify(node.config, null, 2));
                setInputs(JSON.stringify(node.inputs, null, 2));
                setOverrides(JSON.stringify(node.context_overrides, null, 2));
                setBase({
                  revision: graph.revision,
                  nodeRevision: node.revision,
                  content: editableNode(node),
                });
                setConflict(false);
              }}
            >
              {t("载入最新内容", "Load latest content")}
            </Button>
            <Button
              className="primary"
              onClick={() => {
                setBase({
                  revision: graph.revision,
                  nodeRevision: node.revision,
                  content: editableNode(node),
                });
                setConflict(false);
              }}
            >
              {t("保留我的编辑，刷新基准", "Keep my edit and update base")}
            </Button>
          </div>
        </Modal>
      )}
      <div className="inspector-title">
        <span className="eyebrow">
          {node.type.toUpperCase()} · r{node.revision}
        </span>
        <input
          aria-label="Node title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <div>
          <Badge status={node.execution_status} />
          <Badge status={node.research_status} />
        </div>
      </div>
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          ["instructions", "指令", "Prompt"],
          ["config", "配置", "Config"],
          ["context", "上下文", "Context"],
          ["inputs", "输入", "Inputs"],
          ["results", "结果", "Results"],
          ["runs", "运行", "Runs"],
          ["comments", "意见", "Notes"],
        ].map(([id, zh, en]) => ({ id, label: t(zh, en) }))}
      />
      <div className="inspector-body">
        {tab === "instructions" && (
          <>
            <Field label={t("执行指令", "Instructions")}>
              <textarea
                rows={9}
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder={t(
                  "清晰描述目标、方法和预期产物。",
                  "Describe the objective, method, and expected outputs.",
                )}
              />
            </Field>
            <div className="inspector-meta">
              <span>{t("所属路线", "Branch")}</span>
              <strong>
                {graph.branches.find((b) => b.id === node.branch_id)?.name ||
                  "—"}
              </strong>
              <span>{t("产物状态", "Deliverable")}</span>
              <strong>{node.deliverable_status || "draft"}</strong>
              <span>{t("输入 / 输出", "Inputs / outputs")}</span>
              <strong>
                {node.inputs?.length || 0} / {node.outputs?.length || 0}
              </strong>
            </div>
            <Button
              className="full-width"
              onClick={() =>
                action(() =>
                  request("fork_branch", [node.id], {
                    name: `${node.title} · ${t("新路线", "new path")}`,
                    copy_policy: {
                      code: true,
                      data: "reference",
                      results: false,
                    },
                  }),
                )
              }
            >
              <GitBranch size={15} />
              {t("分叉路线", "Fork branch")}
            </Button>
          </>
        )}
        {tab === "config" && (
          <>
            {executionConfig ? (
              <ExecutionSettings
                config={executionConfig}
                onChange={(next) => setConfig(JSON.stringify(next, null, 2))}
              />
            ) : (
              <p className="muted" role="status">
                Execution controls are available when config is a valid JSON
                object.
              </p>
            )}
            <Field
              label={t(
                "Agent / 模型 / 工具 / 预算 / 超参数",
                "Agent / model / tools / budget / parameters",
              )}
            >
              <textarea
                className="code-input"
                rows={16}
                value={config}
                onChange={(e) => setConfig(e.target.value)}
              />
            </Field>
            <small className="muted">
              {t(
                "真实命令可配置为 command，实验脚本可配置为 code。",
                "Set command or code.",
              )}
            </small>
          </>
        )}
        {tab === "context" && (
          <>
            <div className="inline-actions">
              <Button
                onClick={() =>
                  action(async () =>
                    setContext(await api(`/nodes/${node.id}/context`)),
                  )
                }
              >
                {t("查看实际上下文", "View context")}
              </Button>
              <Button
                onClick={() =>
                  action(async () => {
                    const rebuilt = await api(
                      `/nodes/${node.id}/context/rebuild`,
                      "POST",
                      {
                        ...parseJson(overrides),
                        expected_revision: base.revision,
                      },
                    );
                    setContext(rebuilt);
                    setBase((current) => ({
                      ...current,
                      revision: rebuilt.graph_revision,
                    }));
                  })
                }
              >
                <RefreshCw size={13} />
                {t("重建", "Rebuild")}
              </Button>
            </div>
            <Field label={t("上下文覆盖设置", "Context overrides")}>
              <textarea
                className="code-input"
                rows={6}
                value={overrides}
                onChange={(e) => setOverrides(e.target.value)}
              />
            </Field>
            {context && <JsonView value={context} />}
          </>
        )}
        {tab === "inputs" && (
          <>
            <Field label={t("输入引用", "Input references")}>
              <textarea
                rows={12}
                className="code-input"
                value={inputs}
                onChange={(e) => setInputs(e.target.value)}
              />
            </Field>
            <JsonView
              value={executionLinks(graph).filter((e) => e.target === node.id)}
            />
          </>
        )}
        {tab === "results" && (
          <>
            {related.length ? (
              <>
                <h4>{t("上次实际运行", "Last execution")}</h4>
                <Badge status={related[0].status} />
                <JsonView value={related[0].metrics || {}} />
                <RunEvidence runId={related[0].id} />
                <p className="muted">
                  {t("执行所用节点版本", "Executed node revision")}:{" "}
                  {related[0].node_revision}
                </p>
                {related[0].node_revision !== node.revision && (
                  <div className="instruction-note">
                    {t(
                      "当前内容已修改；上述结果来自之前的运行配置。",
                      "Current content was edited; these results come from the earlier run configuration.",
                    )}
                  </div>
                )}
              </>
            ) : (
              <Empty title={t("尚无运行结果", "No execution results")} />
            )}
            <h4>{t("输出引用", "Output references")}</h4>
            <JsonView value={node.outputs || []} />
          </>
        )}
        {tab === "runs" && (
          <>
            <div className="scope-actions">
              {[
                ["single", "只运行此节点", "Run this node"],
                ["ancestors", "运行到这里", "Run to here"],
                ["descendants", "从这里继续", "Continue from here"],
                ["affected", "重跑受影响节点", "Rerun affected nodes"],
              ].map(([scope, zh, en]) => (
                <Button key={scope} onClick={() => onRun(scope)}>
                  <Play size={13} />
                  {t(zh, en)}
                </Button>
              ))}
            </div>
            {related.map((r) => (
              <div className="inspector-run" key={r.id}>
                <Badge status={r.status} />
                <small>{formatDate(r.created_at)}</small>
                <code>{r.id.slice(0, 8)}</code>
              </div>
            ))}
          </>
        )}
        {tab === "comments" && (
          <>
            {(node.comments || []).map((c, index) => (
              <div className="comment" key={index}>
                {typeof c === "string" ? c : c.text || JSON.stringify(c)}
              </div>
            ))}
            <textarea
              rows={5}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={t(
                "记录研究判断或人工意见…",
                "Add a research judgment or note…",
              )}
            />
            <Button
              disabled={!comment.trim()}
              onClick={() =>
                action(async () => {
                  await onSave({
                    comments: [
                      ...(node.comments || []),
                      { text: comment, created_at: new Date().toISOString() },
                    ],
                  });
                  setComment("");
                })
              }
            >
              <MessageSquare size={14} />
              {t("添加意见", "Add note")}
            </Button>
          </>
        )}
      </div>
      {[
        "running",
        "queued",
        "pausing",
        "paused",
        "waiting",
        "waiting_input",
        "budget_exhausted",
      ].includes(node.execution_status) && (
        <label className="checkbox-label stop-apply">
          <input
            type="checkbox"
            checked={stopCurrent}
            onChange={(e) => setStopCurrent(e.target.checked)}
          />
          {t("保存时停止当前运行", "Stop current run when saving")}
        </label>
      )}
      <div className="inspector-save">
        <Button className="primary" busy={busy} onClick={save}>
          <Check size={14} />
          {t("保存修改", "Save changes")}
        </Button>
      </div>
    </div>
  );
}
export function RunPanel({
  runs,
  reload,
  nodes = [],
}: {
  runs: Run[];
  reload: () => Promise<void>;
  nodes?: ResearchNode[];
}) {
  const { t, action } = useUI();
  const [selected, setSelected] = useState("");
  const [output, setOutput] = useState("");
  const run = runs.find((r) => r.id === selected) || runs[0];
  const status = run?.status || "";
  const terminal = [
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "skipped",
  ].includes(status);
  const allowedActions: Record<string, boolean> = {
    pause: ["queued", "waiting", "budget_exhausted", "running"].includes(
      status,
    ),
    resume: ["paused", "waiting_input", "waiting", "budget_exhausted"].includes(
      status,
    ),
    cancel: [
      "queued",
      "running",
      "pausing",
      "paused",
      "waiting_input",
      "waiting",
      "budget_exhausted",
    ].includes(status),
    retry: terminal,
  };
  useEffect(() => {
    setOutput("");
    if (!run) return;
    let active = true;
    let offset = 0;
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const result = await api(`/runs/${run.id}/output?offset=${offset}`);
        if (active) {
          setOutput((o) => (o + result.text).slice(-2_000_000));
          offset = result.offset;
        }
      } catch (e) {
        if (active) setOutput((e as Error).message);
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = setInterval(load, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [run?.id]);
  if (!runs.length)
    return (
      <div className="dock-empty">
        <span className="subtle-icon">
          <SquareTerminal size={22} />
        </span>
        <div>
          <strong>{t("暂无运行记录", "No runs")}</strong>
        </div>
      </div>
    );
  return (
    <div className="run-panel">
      <div className="run-list">
        {runs.map((r) => (
          <button
            className={run?.id === r.id ? "active" : ""}
            key={r.id}
            onClick={() => setSelected(r.id)}
          >
            <span className={`run-indicator status-${r.status}`} />
            <span>
              {nodes.find((n) => n.id === r.node_id)?.title || r.kind}
              <small>
                {r.id.slice(0, 8)} · {formatDate(r.created_at)}
              </small>
            </span>
            <Badge status={r.status} />
          </button>
        ))}
      </div>
      <div className="run-output">
        <div className="run-output-toolbar">
          <code>{run?.id.slice(0, 8)}</code>
          <span>
            PID {run?.pid || "—"} · exit {run?.exit_code ?? "—"}
          </span>
          <span className="toolbar-spacer" />
          {run &&
            [
              ["pause", Pause],
              ["resume", Play],
              ["cancel", Square],
              ["retry", RefreshCw],
            ].map(([op, Icon]) => (
              <IconButton
                key={String(op)}
                label={String(op)}
                disabled={!allowedActions[String(op)]}
                onClick={() =>
                  action(async () => {
                    await api(`/runs/${run.id}/${op}`, "POST", {});
                    await reload();
                  })
                }
              >
                {typeof Icon !== "string" && <Icon size={13} />}
              </IconButton>
            ))}
        </div>
        {run && <RunEvidence runId={run.id} />}
        <pre>
          {output ||
            run?.error ||
            (terminal
              ? t("没有输出", "No output")
              : t("等待执行器输出…", "Waiting for executor output…"))}
        </pre>
      </div>
    </div>
  );
}

function ImpactReview({
  impact,
  graph,
  pending,
  onChange,
}: {
  impact: Json;
  graph: Graph;
  pending: Json | null;
  onChange: (p: Json) => void;
}) {
  const { t } = useUI();
  const resolve = (path: string, value: any) => {
    if (!pending) return;
    const resolution = { ...pending.params.resolution };
    if (path === "@config") resolution.config = value;
    else resolution.files = { ...resolution.files, [path]: value };
    onChange({ ...pending, params: { ...pending.params, resolution } });
  };
  return (
    <div className="impact-review">
      {[
        ["changed_nodes", "当前内容改变", "Edited objects"],
        ["rerun_nodes", "需重新执行", "Rerun"],
        ["refresh_nodes", "需刷新上下文或产物", "Refresh context or artifact"],
      ].map(([key, zh, en]) => (
        <section key={key}>
          <h3>
            {t(zh, en)}{" "}
            <span className="count">{(impact[key] || []).length}</span>
          </h3>
          {(impact[key] || []).length ? (
            <ul>
              {impact[key].map((id: string) => (
                <li key={id}>
                  <strong>
                    {graph.nodes.find((n) => n.id === id)?.title || id}
                  </strong>
                  <small>{(impact.reasons?.[id] || []).join("; ")}</small>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t("无", "None")}</p>
          )}
        </section>
      ))}
      {(impact.conflicts || []).map((conflict: Json) => (
        <section className="merge-conflict" key={conflict.path}>
          <h3>{conflict.path}</h3>
          {conflict.diff ? (
            <pre className="json-view">{conflict.diff}</pre>
          ) : (
            <JsonView value={{ left: conflict.left, right: conflict.right }} />
          )}
          <Field label={t("冲突解决方式", "Conflict resolution")}>
            <select
              value={
                conflict.path === "@config"
                  ? pending?.params.resolution?.config || ""
                  : typeof pending?.params.resolution?.files?.[
                        conflict.path
                      ] === "object"
                    ? "content"
                    : pending?.params.resolution?.files?.[conflict.path] || ""
              }
              onChange={(e) =>
                resolve(
                  conflict.path,
                  e.target.value === "content"
                    ? { content: "" }
                    : e.target.value,
                )
              }
            >
              <option value="">{t("选择内容", "Select content")}</option>
              <option value="left">{t("采用左侧", "Use left")}</option>
              <option value="right">{t("采用右侧", "Use right")}</option>
              {conflict.path !== "@config" && (
                <>
                  <option value="delete">
                    {t("删除此文件", "Delete file")}
                  </option>
                  <option value="content">
                    {t("手动编辑合并内容", "Edit merged content")}
                  </option>
                </>
              )}
            </select>
          </Field>
          {typeof pending?.params.resolution?.files?.[conflict.path] ===
            "object" && (
            <textarea
              className="code-input"
              rows={8}
              value={pending.params.resolution.files[conflict.path].content}
              onChange={(e) =>
                resolve(conflict.path, { content: e.target.value })
              }
            />
          )}
        </section>
      ))}
      <details>
        <summary>
          {t(
            "完整影响、文件复用与输出信息",
            "Full impact, file reuse, and outputs",
          )}
        </summary>
        <JsonView value={impact} />
      </details>
    </div>
  );
}
function BranchComparison({
  graph,
  onClose,
  onMerge,
}: {
  graph: Graph;
  onClose: () => void;
  onMerge: (left: string, right: string) => void;
}) {
  const { t, action } = useUI();
  const [left, setLeft] = useState(graph.branches[0]?.id || "");
  const [right, setRight] = useState(graph.branches[1]?.id || "");
  const [comparison, setComparison] = useState<Json | null>(null);
  return (
    <Modal
      wide
      title={t("比较研究路线", "Compare research paths")}
      onClose={onClose}
    >
      <div className="form-row">
        <Field label={t("左侧路线", "Left path")}>
          <select value={left} onChange={(e) => setLeft(e.target.value)}>
            {graph.branches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("右侧路线", "Right path")}>
          <select value={right} onChange={(e) => setRight(e.target.value)}>
            {graph.branches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Button
        disabled={!left || !right || left === right}
        onClick={() =>
          action(async () =>
            setComparison(
              await api(`/branches/compare?left=${left}&right=${right}`),
            ),
          )
        }
      >
        <GitCompare size={15} />
        {t("比较", "Compare")}
      </Button>
      {comparison && (
        <>
          <h3 className="comparison-heading">
            {t("文件差异", "File differences")}
          </h3>
          {(comparison.files || []).map((file: Json) => (
            <details key={file.path}>
              <summary>
                {file.path} · {file.status}
              </summary>
              <pre className="json-view">
                {file.diff || JSON.stringify(file, null, 2)}
              </pre>
            </details>
          ))}
          <details>
            <summary>
              {t(
                "配置、运行结果与资源",
                "Configuration, results, and resources",
              )}
            </summary>
            <JsonView value={comparison} />
          </details>
          <div className="modal-actions">
            <Button className="primary" onClick={() => onMerge(left, right)}>
              {t("合并路线并预览影响", "Merge paths and preview impact")}
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}

function editableNode(node: ResearchNode) {
  return {
    title: node.title,
    instructions: node.instructions,
    config: node.config,
    inputs: node.inputs,
    context_overrides: node.context_overrides,
  };
}
