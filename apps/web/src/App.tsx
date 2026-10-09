import { useState, useEffect, useCallback } from "react";
import {
  Routes,
  Route,
  NavLink,
  useNavigate,
  useParams,
  useLocation,
  Outlet,
  Navigate,
} from "react-router-dom";
import {
  TreePine,
  PanelLeft,
  Search,
  Plus,
  ArrowUpRight,
  LayoutDashboard,
  Network,
  BookOpen,
  Lightbulb,
  SquareFunction,
  FlaskConical,
  Table2,
  ChartNoAxesCombined,
  FileText,
  Folder,
  Settings,
  ChevronDown,
  Command,
  Sun,
  Moon,
  Activity,
  Play,
  Pause,
  Square,
  Check,
  X,
  ArrowRight,
  Upload,
  Archive,
  Copy,
  Trash2,
  Pencil,
  Presentation,
} from "lucide-react";
import { ProgressPanel } from "./progress/ProgressPanel";
import { InterventionPanel } from "./interventions/InterventionPanel";
import { subscribeProject } from "./progress/events";
import { api, download, formatDate } from "./api";
import type { Project, Json, Run } from "./api";
import {
  UIContext,
  useUI,
  useLoad,
  Button,
  IconButton,
  Empty,
  Modal,
  Field,
  ErrorBox,
  PageHeading,
  Badge,
  Loading,
} from "./ui";
import { Workspace } from "./Workspace";
import { DemoGuide, demoLink } from "./DemoGuide";
import {
  ResearchPage,
  LibraryPage,
  TheoryPage,
  ExperimentsPage,
  DataPage,
  FiguresPage,
  PaperPage,
  FilesPage,
  SettingsPage,
} from "./pages";
const navigation = [
  ["overview", "研究概览", "Overview", LayoutDashboard],
  ["workspace", "工作台", "Workspace", Network],
  ["library", "论文库", "Library", BookOpen],
  ["ideas", "研究想法", "Ideas", Lightbulb],
  ["theory", "理论工作台", "Theory", SquareFunction],
  ["experiments", "实验", "Experiments", FlaskConical],
  ["data", "数据分析", "Data & analysis", Table2],
  ["figures", "图表工作室", "Figure studio", ChartNoAxesCombined],
  ["paper", "论文", "Paper", FileText],
  ["files", "项目文件", "Files", Folder],
] as const;
export default function App() {
  const lang = "en";
  const [theme, setTheme] = useState(
    localStorage.getItem("forest-theme") || "light",
  );
  const [toast, setToast] = useState("");
  const [palette, setPalette] = useState(false);
  const [authentication, setAuthentication] = useState(false);
  useEffect(() => {
    const request = () => setAuthentication(true);
    window.addEventListener("forest-auth-required", request);
    return () => window.removeEventListener("forest-auth-required", request);
  }, []);
  const [paletteQuery, setPaletteQuery] = useState("");
  const navigate = useNavigate();
  const location = useLocation();
  const match = location.pathname.match(/\/projects\/([^/]+)/);
  const projectId = match?.[1];
  const t = useCallback((_zh: string, en: string) => en, [lang]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.lang = "en";
    localStorage.setItem("forest-theme", theme);
    localStorage.setItem("forest-language", lang);
  }, [theme, lang]);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(""), 7000);
    return () => clearTimeout(id);
  }, [toast]);
  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setPalette((x) => !x);
      }
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, []);
  const action = async <T,>(f: () => Promise<T>, message?: string) => {
    try {
      const value = await f();
      if (message) setToast(message);
      return value;
    } catch (e) {
      setToast((e as Error).message);
      return undefined;
    }
  };
  return (
    <UIContext.Provider value={{ lang, theme, t, notify: setToast, action }}>
      <div className="app">
        <aside className="sidebar">
          <NavLink to="/projects" className="brand">
            <span className="brand-icon">
              <TreePine size={23} />
            </span>
            <span>
              FOREST<span className="brand-caption">RESEARCH WORKSPACE</span>
            </span>
          </NavLink>
          <button className="quick-search" onClick={() => setPalette(true)}>
            <Search size={15} />
            <span>{t("搜索或跳转", "Search workspace")}</span>
            <kbd>⌘ K</kbd>
          </button>
          <NavLink className="project-switch" to="/projects">
            <span className="workspace-avatar">F</span>
            <span>
              {t("我的研究空间", "My workspace")}
              <small>Personal workspace</small>
            </span>
            <ChevronDown size={14} />
          </NavLink>
          <div className="nav-caption">{t("工作空间", "WORKSPACE")}</div>
          <nav>
            {projectId ? (
              navigation.map(([path, zh, en, Icon]) => (
                <NavLink
                  key={path}
                  aria-label={t(zh, en)}
                  to={demoLink(
                    `/projects/${projectId}/${path}`,
                    location.search,
                  )}
                >
                  <Icon size={17} />
                  <span>{t(zh, en)}</span>
                  {path === "workspace" && <span className="nav-key">F</span>}
                </NavLink>
              ))
            ) : (
              <NavLink to="/projects">
                <Folder size={17} />
                <span>{t("全部项目", "All projects")}</span>
              </NavLink>
            )}
          </nav>
          <div className="sidebar-bottom">
            <NavLink className="settings-link" to="/settings">
              <Settings size={17} />
              {t("设置与连接", "Settings & connections")}
            </NavLink>
            <div className="sidebar-footer">
              <span className="owner-avatar">R</span>
              <div>
                <strong>{t("研究者", "Researcher")}</strong>
                <small>{t("本地工作空间", "Local workspace")}</small>
              </div>
              <IconButton
                label={t("切换主题", "Toggle theme")}
                onClick={() => setTheme(theme === "light" ? "dark" : "light")}
              >
                {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
              </IconButton>
            </div>
          </div>
        </aside>
        <main
          className={`main ${new URLSearchParams(location.search).get("demo") === "1" ? "demo-mode" : ""}`}
        >
          <Routes>
            <Route path="/projects" element={<Projects />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/share/:token" element={<SharedProject />} />
            <Route path="/projects/:id" element={<ProjectShell />}>
              <Route index element={<Navigate to="overview" replace />} />
              <Route path="overview" element={<Overview />} />
              <Route path="workspace" element={<Workspace />} />
              <Route path="library" element={<LibraryPage />} />
              <Route path="ideas" element={<ResearchPage resource="ideas" />} />
              <Route path="theory" element={<TheoryPage />} />
              <Route path="experiments" element={<ExperimentsPage />} />
              <Route path="data" element={<DataPage />} />
              <Route path="figures" element={<FiguresPage />} />
              <Route path="paper" element={<PaperPage />} />
              <Route path="files" element={<FilesPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/projects" replace />} />
          </Routes>
        </main>
      </div>
      {toast && (
        <div role="status" className="toast">
          <Activity size={16} />
          <span>{toast}</span>
          <IconButton label="Dismiss" onClick={() => setToast("")}>
            <X size={15} />
          </IconButton>
        </div>
      )}
      {palette && (
        <Modal title={t("前往…", "Go to…")} onClose={() => setPalette(false)}>
          <input
            autoFocus
            placeholder={t("搜索页面或命令", "Search pages or commands")}
            value={paletteQuery}
            onChange={(e) => setPaletteQuery(e.target.value)}
          />
          <div className="command-list">
            {[
              ["/projects", t("全部项目", "All projects")],
              ["/settings", t("设置与连接", "Settings & connections")],
              ...(projectId
                ? navigation.map(([p, zh, en]) => [
                    `/projects/${projectId}/${p}`,
                    t(zh, en),
                  ])
                : []),
            ]
              .filter(([, name]) =>
                name.toLowerCase().includes(paletteQuery.toLowerCase()),
              )
              .map(([path, name]) => (
                <button
                  key={path}
                  onClick={() => {
                    navigate(demoLink(path, location.search));
                    setPalette(false);
                    setPaletteQuery("");
                  }}
                >
                  <Command size={16} />
                  {name}
                  <ArrowRight size={14} />
                </button>
              ))}
          </div>
        </Modal>
      )}
      {authentication && (
        <OwnerLogin onClose={() => setAuthentication(false)} />
      )}
    </UIContext.Provider>
  );
}
export function useProject() {
  const { id } = useParams();
  return useLoad<Project>(id ? `/projects/${id}` : null, null!);
}
function ProjectShell() {
  const { id } = useParams();
  const { t, action } = useUI();
  const { data: project, reload } = useProject();
  const {
    data: runs,
    reload: reloadRuns,
    loading: runsLoading,
    error: runsError,
  } = useLoad<Run[]>(`/projects/${id}/runs`, []);
  const { data: system, reload: reloadSystem } = useLoad<Json>("/system", {});
  const active = runs.filter((r) =>
    [
      "running",
      "queued",
      "paused",
      "waiting",
      "pausing",
      "budget_exhausted",
    ].includes(r.status),
  );
  const location = useLocation();
  const branch = active[0]?.branch_id;
  useEffect(() => {
    if (!id) return;
    return subscribeProject(id, () => {
      void reload();
      void reloadRuns();
      window.dispatchEvent(new Event("forest-refresh"));
    });
  }, [id, reload, reloadRuns]);
  useEffect(() => {
    const timer = setInterval(() => void reloadSystem(), 30000);
    return () => clearInterval(timer);
  }, [reloadSystem]);
  return (
    <>
      <header className="topbar">
        <div className="topbar-project">
          <Folder size={16} />
          <span>{project?.name || t("正在载入项目…", "Loading project…")}</span>
          <span className="slash">/</span>
          <span className="topbar-page">
            {navigation.find((n) => location.pathname.endsWith(n[0]))?.[
              t("中文", "English") === "中文" ? 1 : 2
            ] || ""}
          </span>
        </div>
        <div className="topbar-actions">
          <span
            className={`connection ${system.model_connected ? "online" : ""}`}
          >
            <span />
            {system.model_connected === undefined
              ? "Checking model…"
              : system.model_connected
                ? t("模型已连接", "Model connected")
                : t("模型未连接", "Model offline")}
          </span>
          <span className="budget-label">
            {project?.budget?.seconds
              ? `${Math.round(project.budget.seconds / 60)} min`
              : t("未设预算", "No budget")}
          </span>
          {active.length > 0 && (
            <>
              <Badge status={active[0].status}>
                {active.length} {t("个任务", "tasks")}
              </Badge>
              <IconButton
                label={`${t("暂停项目中的全部活动运行", "Pause all active project runs")} ${branch || ""}`}
                onClick={() =>
                  action(async () => {
                    await Promise.all(
                      active
                        .filter((r) => r.status === "running")
                        .map((r) => api(`/runs/${r.id}/pause`, "POST", {})),
                    );
                    await reloadRuns();
                  })
                }
              >
                <Pause size={15} />
              </IconButton>
              <IconButton
                label={`${t("停止项目中的全部活动运行", "Stop all active project runs")} ${branch || ""}`}
                onClick={() =>
                  action(async () => {
                    await Promise.all(
                      active.map((r) =>
                        api(`/runs/${r.id}/cancel`, "POST", {}),
                      ),
                    );
                    await reloadRuns();
                  })
                }
              >
                <Square size={14} />
              </IconButton>
            </>
          )}
          <NavLink
            className="icon-button"
            title={t("连接设置", "Connection settings")}
            to="/settings"
          >
            <Settings size={16} />
          </NavLink>
        </div>
      </header>
      {new URLSearchParams(location.search).get("demo") === "1" && (
        <DemoGuide
          projectId={id!}
          runs={runs}
          available={!runsLoading && !runsError}
        />
      )}
      <Outlet />
    </>
  );
}
function ProjectForm({
  initial,
  onClose,
  onSaved,
}: {
  initial?: Project;
  onClose: () => void;
  onSaved: (p: Project) => void;
}) {
  const { t, action } = useUI();
  const [name, setName] = useState(initial?.name || "");
  const [goal, setGoal] = useState(initial?.goal || "");
  const [description, setDescription] = useState(initial?.description || "");
  const { data: providers } = useLoad<Json[]>("/providers", []);
  const [providerId, setProviderId] = useState(
    initial?.config?.provider_id || "",
  );
  const [allowPaid, setAllowPaid] = useState(
    initial?.budget?.allow_paid || false,
  );
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={
        initial ? t("编辑项目", "Edit project") : t("新建项目", "New project")
      }
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const result = await action(() =>
            api<Project>(
              initial ? `/projects/${initial.id}` : "/projects",
              initial ? "PATCH" : "POST",
              {
                name,
                goal,
                description,
                config: { ...initial?.config, provider_id: providerId || null },
                budget: { ...initial?.budget, allow_paid: allowPaid },
                ...(initial ? { expected_revision: initial.revision } : {}),
              },
            ),
          );
          setBusy(false);
          if (result) {
            onSaved(result);
            onClose();
          }
        }}
      >
        <Field label={t("项目名称", "Project name")}>
          <input
            required
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t(
              "例如：高效学习中的数据选择",
              "e.g. Data selection for efficient learning",
            )}
          />
        </Field>
        <Field label={t("研究问题", "Research question")}>
          <textarea
            rows={4}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder={t(
              "你想理解或验证什么？",
              "What do you want to understand or test?",
            )}
          />
        </Field>
        <Field label={t("简短描述", "Description")}>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field label="Model connection">
          <select
            value={providerId}
            onChange={(e) => setProviderId(e.target.value)}
          >
            <option value="">Workspace default</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.model}
              </option>
            ))}
          </select>
        </Field>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={allowPaid}
            onChange={(e) => setAllowPaid(e.target.checked)}
          />
          Allow paid API calls within the configured spending limits
        </label>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            {t("取消", "Cancel")}
          </Button>
          <Button className="primary" busy={busy} type="submit">
            {initial
              ? t("保存修改", "Save changes")
              : t("创建研究项目", "Create project")}
            <ArrowRight size={15} />
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function Projects() {
  const { t, action } = useUI();
  const {
    data: projects,
    error,
    loading,
    reload,
  } = useLoad<Project[]>("/projects", []);
  const [editing, setEditing] = useState<Project | null | false>(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("recent");
  const [showArchived, setShowArchived] = useState(false);
  const navigate = useNavigate();
  const list = projects
    .filter(
      (p) =>
        (showArchived || !p.archived) &&
        `${p.name} ${p.goal}`.toLowerCase().includes(query.toLowerCase()),
    )
    .sort((a, b) =>
      sort === "name"
        ? a.name.localeCompare(b.name)
        : new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    );
  return (
    <>
      <header className="topbar">
        <div className="topbar-project">
          <Folder size={16} />
          {t("我的研究空间", "My research workspace")}
        </div>
        <span className="local-label">
          <span />
          {t("本地", "Local")}
        </span>
      </header>
      <div className="page projects-page">
        <div className="projects-intro">
          <h1>{t("项目", "Projects")}</h1>
          <Button className="primary" onClick={() => setEditing(null)}>
            <Plus size={16} />
            {t("新建研究项目", "New research project")}
          </Button>
        </div>
        <div className="section-toolbar">
          <div>
            <h2>
              {t("研究项目", "Research projects")}
              <span className="count">{list.length}</span>
            </h2>
          </div>
          <div className="toolbar-actions">
            <label className="search-input">
              <Search size={15} />
              <input
                aria-label="Search projects"
                placeholder={t("搜索项目…", "Search projects…")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <select
              aria-label="Sort projects"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="recent">{t("最近编辑", "Recently edited")}</option>
              <option value="name">{t("按名称", "Name")}</option>
            </select>
            <IconButton
              label={t("显示归档", "Show archived")}
              onClick={() => setShowArchived(!showArchived)}
            >
              <Archive size={17} />
            </IconButton>
            <label className="button upload-button">
              <Upload size={15} />
              {t("导入", "Import")}
              <input
                type="file"
                accept=".zip"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f)
                    void action(async () => {
                      const body = new FormData();
                      body.append("file", f);
                      await api("/projects/import", "POST", body);
                      await reload();
                    });
                }}
              />
            </label>
          </div>
        </div>
        <ErrorBox error={error} retry={reload} />
        {loading ? (
          <Loading />
        ) : list.length ? (
          <div className="project-grid">
            {list.map((p) => (
              <article className="project-card" key={p.id}>
                <div className="project-card-top">
                  <span className="project-icon">
                    <Network size={22} />
                  </span>
                  <Badge status={p.archived ? "archived" : "idle"}>
                    {p.archived
                      ? t("已归档", "Archived")
                      : t("研究项目", "Research project")}
                  </Badge>
                </div>
                <button
                  className="project-title"
                  onClick={() => navigate(`/projects/${p.id}/workspace`)}
                >
                  {p.name}
                  <ArrowUpRight size={19} />
                </button>
                <p className="project-description">
                  {p.goal || p.description || t("未设置目标", "No goal set")}
                </p>
                <div className="project-card-bottom">
                  <span>{formatDate(p.updated_at)}</span>
                  <div>
                    <IconButton
                      label={t("编辑", "Edit")}
                      onClick={() => setEditing(p)}
                    >
                      <Pencil size={14} />
                    </IconButton>
                    <IconButton
                      label={t("复制", "Duplicate")}
                      onClick={() =>
                        action(async () => {
                          await api(`/projects/${p.id}/duplicate`, "POST", {});
                          await reload();
                        })
                      }
                    >
                      <Copy size={14} />
                    </IconButton>
                    <IconButton
                      label={t("归档或恢复", "Archive or restore")}
                      onClick={() =>
                        action(async () => {
                          await api(`/projects/${p.id}`, "PATCH", {
                            archived: !p.archived,
                          });
                          await reload();
                        })
                      }
                    >
                      <Archive size={14} />
                    </IconButton>
                    <IconButton
                      label={t("删除项目", "Delete project")}
                      onClick={() => {
                        if (
                          confirm(
                            t(
                              `删除项目“${p.name}”及其文件？`,
                              `Delete “${p.name}” and its files?`,
                            ),
                          )
                        )
                          void action(async () => {
                            await api(`/projects/${p.id}`, "DELETE");
                            await reload();
                          });
                      }}
                    >
                      <Trash2 size={14} />
                    </IconButton>
                  </div>
                </div>
              </article>
            ))}
            <button
              className="new-project-card"
              onClick={() => setEditing(null)}
            >
              <Plus size={25} strokeWidth={1.3} />
              <strong>{t("新建项目", "New project")}</strong>
            </button>
          </div>
        ) : (
          !error && (
            <Empty
              title={t("暂无项目", "No projects")}
              action={
                <Button className="primary" onClick={() => setEditing(null)}>
                  <Plus size={15} />
                  {t("新建项目", "Create project")}
                </Button>
              }
            />
          )
        )}
      </div>
      {editing !== false && (
        <ProjectForm
          initial={editing || undefined}
          onClose={() => setEditing(false)}
          onSaved={(p) => {
            void reload();
            if (!editing) navigate(`/projects/${p.id}/workspace`);
          }}
        />
      )}
    </>
  );
}
function Overview() {
  const { id } = useParams();
  const { t, action } = useUI();
  const { data: p, error, reload } = useProject();
  const { data: runs, reload: reloadRecent } = useLoad<Run[]>(
    `/projects/${id}/runs`,
    [],
  );
  useEffect(() => {
    const refresh = () => void reloadRecent();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reloadRecent]);
  const [edit, setEdit] = useState(false);
  const [budget, setBudget] = useState("");
  const [mode, setMode] = useState("assisted");
  useEffect(() => {
    if (p) {
      setBudget(JSON.stringify(p.budget, null, 2));
      setMode(p.mode || "assisted");
    }
  }, [p]);
  if (!p) return <ErrorBox error={error} retry={reload} />;
  return (
    <div className="page">
      <PageHeading
        title={p.name}
        description={p.description}
        actions={
          <>
            <NavLink
              className="button"
              to={`/projects/${id}/overview?demo=1&demo_step=A`}
            >
              <Presentation size={15} />
              {t("导览", "Guide")}
            </NavLink>
            <Button onClick={() => setEdit(true)}>
              <Pencil size={15} />
              {t("编辑目标", "Edit goal")}
            </Button>
            <NavLink
              className="button primary"
              to={demoLink(`/projects/${id}/workspace`)}
            >
              {t("打开工作台", "Open workspace")}
              <ArrowUpRight size={15} />
            </NavLink>
          </>
        }
      />
      <ProgressPanel key={id} projectId={id!} />
      <InterventionPanel key={`interventions:${id}`} projectId={id!} />
      <ResearchControls
        projectId={id!}
        runMode={p.mode || "assisted"}
        onChange={reload}
      />
      <div className="overview-grid">
        <section className="surface goal-surface">
          <div className="eyebrow">
            01 / {t("研究问题", "RESEARCH QUESTION")}
          </div>
          <h2>
            {p.goal || t("还没有设定研究问题", "No research question yet")}
          </h2>
          {p.current_direction && <p>{p.current_direction}</p>}
          <div className="inline-meta">
            <span>Revision {p.revision}</span>
          </div>
        </section>
        <section className="surface">
          <h3>{t("自主运行设置", "Research controls")}</h3>
          <Field label={t("运行模式", "Run mode")}>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="auto">Auto</option>
              <option value="assisted">Assisted</option>
              <option value="manual">Manual</option>
            </select>
          </Field>
          <Field label={t("预算配置", "Budget configuration")}>
            <textarea
              className="code-input"
              rows={5}
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
            />
          </Field>
          <p className="muted">
            {t(
              "seconds 是排队与运行中任务共享的累计 wall-time 预算。本地 worker 每 0.4 秒检查一次任务期限，并给进程 0.1 秒优雅退出；实际耗时会计入预算，主机调度或远端取消可能增加延迟。",
              "The seconds allowance is shared across queued and running tasks. The local worker checks deadlines every 0.4s and allows 0.1s for graceful shutdown; actual elapsed time is charged, and host scheduling or remote cancellation can add delay.",
            )}
          </p>
          <Button
            onClick={() =>
              action(
                async () => {
                  await api(`/projects/${id}`, "PATCH", {
                    mode,
                    budget: JSON.parse(budget),
                  });
                  await reload();
                },
                t("运行设置已保存", "Run settings saved"),
              )
            }
          >
            {t("保存运行设置", "Save controls")}
          </Button>
        </section>
      </div>
      <section className="surface">
        <div className="section-toolbar">
          <h2>{t("最近的实际运行", "Recent executions")}</h2>
          <ProjectShare projectId={id!} />
          <Button
            onClick={() =>
              action(() =>
                download(`/projects/${id}/export`, {
                  request_id: crypto.randomUUID(),
                }),
              )
            }
          >
            <Upload size={15} />
            {t("导出项目", "Export project")}
          </Button>
        </div>
        {runs.length ? (
          <div className="activity-list">
            {runs.slice(0, 8).map((r) => (
              <div key={r.id}>
                <FlaskConical size={17} />
                <strong>{r.kind}</strong>
                <span>{formatDate(r.created_at)}</span>
                <Badge status={r.status} />
              </div>
            ))}
          </div>
        ) : (
          <Empty title={t("尚无运行记录", "No executions yet")} />
        )}
      </section>
      {edit && (
        <ProjectForm
          initial={p}
          onClose={() => setEdit(false)}
          onSaved={() => void reload()}
        />
      )}
    </div>
  );
}

function ResearchControls({
  projectId,
  runMode,
  onChange,
}: {
  projectId: string;
  runMode: string;
  onChange: () => Promise<void>;
}) {
  const { t, action } = useUI();
  const { data: graph } = useLoad<Json>(`/projects/${projectId}/graph`, {
    branches: [],
  });
  const [branch, setBranch] = useState("");
  const [state, setState] = useState<Json | null>(null);
  const [autonomous, setAutonomous] = useState(runMode !== "manual");
  const [readyParallelism, setReadyParallelism] = useState(1);
  const [metric, setMetric] = useState("");
  const [direction, setDirection] = useState("min");
  const {
    data: session,
    loading: sessionLoading,
    error: sessionError,
    reload: reloadSession,
  } = useLoad<Json>(`/projects/${projectId}/research?overview=true`, {
    controller: {},
    decisions: [],
    active_runs: [],
  });
  const sessionUnavailable = sessionLoading || !!sessionError;
  const [inspectComparisons, setInspectComparisons] = useState(false);
  const comparisons = useLoad<Json>(
    inspectComparisons ? `/projects/${projectId}/research` : null,
    { trials: [] },
  );
  useEffect(() => {
    setMetric(session.objective?.metric || "");
    setDirection(session.objective?.direction || "min");
  }, [session.objective?.metric, session.objective?.direction]);
  useEffect(() => {
    if (typeof session.controller?.autonomous === "boolean") {
      setAutonomous(session.controller.autonomous);
    } else {
      setAutonomous(runMode !== "manual");
    }
  }, [session.controller?.autonomous, runMode]);
  useEffect(() => {
    setReadyParallelism(session.controller?.ready_parallelism || 1);
  }, [session.controller?.ready_parallelism]);
  useEffect(() => {
    const timer = setInterval(() => void reloadSession(), 5000);
    return () => clearInterval(timer);
  }, [reloadSession]);
  return (
    <section className="surface research-controls">
      <div>
        <h3>{t("运行控制", "Run controls")}</h3>
      </div>
      <select
        aria-label="Research branch"
        value={branch}
        onChange={(e) => setBranch(e.target.value)}
      >
        <option value="">{t("项目全部路线", "All project paths")}</option>
        {graph.branches.map((b: Json) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </select>
      <label className="inline-actions">
        <input
          type="checkbox"
          checked={autonomous}
          disabled={sessionUnavailable}
          onChange={(e) => setAutonomous(e.target.checked)}
        />
        Autonomous planning
      </label>
      <Field label={t("独立任务并行上限", "Independent task slots")}>
        <input
          aria-label="Independent task slots"
          type="number"
          min={1}
          max={32}
          step={1}
          disabled={sessionUnavailable}
          value={readyParallelism}
          onChange={(e) => setReadyParallelism(Number(e.target.value))}
        />
      </Field>
      {sessionError && (
        <p role="alert">
          Unable to load saved controller settings. Start is disabled while
          retrying.
        </p>
      )}
      <Badge status={session.controller?.status || state?.status || "idle"} />
      {session.controller?.phase && <span>{session.controller.phase}</span>}
      {(session.controller?.reason || session.controller?.last_rationale) && (
        <p>{session.controller.reason || session.controller.last_rationale}</p>
      )}
      {session.active_runs?.map((r: Json) => (
        <div key={r.id}>
          <code>{r.id.slice(0, 8)}</code> {r.kind} · {r.status}
        </div>
      ))}
      <details>
        <summary>Comparison objective</summary>
        <Field label="Metric">
          <input
            aria-label="Comparison metric"
            value={metric}
            placeholder={
              session.objective?.metric || "Metric name from metrics.json"
            }
            onChange={(e) => setMetric(e.target.value)}
          />
        </Field>
        <select
          aria-label="Metric direction"
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
        >
          <option value="min">Lower is better</option>
          <option value="max">Higher is better</option>
        </select>
        <Button
          onClick={() =>
            action(async () => {
              await api(`/projects/${projectId}/objective`, "PATCH", {
                metric,
                direction,
              });
              await reloadSession();
            })
          }
        >
          Save objective
        </Button>
        <Button
          busy={inspectComparisons && comparisons.loading}
          onClick={() => {
            setInspectComparisons(true);
            if (inspectComparisons) void comparisons.reload();
          }}
        >
          Inspect checked comparisons
        </Button>
        <ErrorBox error={comparisons.error} />
        {!!comparisons.data.trials?.length && (
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Value</th>
                <th>Comparison</th>
              </tr>
            </thead>
            <tbody>
              {comparisons.data.trials.map((r: Json) => (
                <tr key={r.run_id}>
                  <td>
                    <code>{r.run_id.slice(0, 8)}</code>
                  </td>
                  <td>{r.value ?? "—"}</td>
                  <td>{r.disposition}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
      {session.coverage && <small>{session.coverage}</small>}
      {!!session.decisions?.length && (
        <details>
          <summary>Research decisions ({session.decisions.length})</summary>
          {session.decisions.map((d: Json) => (
            <div key={d.id}>
              <strong>{d.data.action}</strong>
              <p>{d.data.rationale}</p>
              <small>{formatDate(d.created_at)}</small>
            </div>
          ))}
        </details>
      )}
      <div className="inline-actions">
        {[
          ["start", Play, "开始 / 继续", "Start / continue"],
          ["pause", Pause, "暂停研究", "Pause research"],
          ["stop", Square, "停止研究", "Stop research"],
        ].map(([op, Icon, zh, en]) => (
          <Button
            className={op === "start" ? "primary" : ""}
            key={String(op)}
            disabled={String(op) === "start" && sessionUnavailable}
            onClick={() =>
              action(async () => {
                const result = await api(
                  `/projects/${projectId}/research/${op}`,
                  "POST",
                  {
                    branch_id: branch || null,
                    autonomous,
                    ready_parallelism: readyParallelism,
                  },
                );
                setState(result);
                if (result.process_control_errors?.length)
                  throw new Error(
                    result.process_control_errors
                      .map((e: Json) => `${e.run_id.slice(0, 8)}: ${e.error}`)
                      .join("; "),
                  );
                await onChange();
              })
            }
          >
            {typeof Icon !== "string" && <Icon size={13} />}{" "}
            {t(String(zh), String(en))}
          </Button>
        ))}
      </div>
    </section>
  );
}

function OwnerLogin({ onClose }: { onClose: () => void }) {
  const { t, action } = useUI();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={t("登录", "Sign in")} onClose={onClose}>
      <p className="muted">
        {t(
          "输入服务器配置的拥有者令牌，以访问研究项目与计算资源。",
          "Enter the owner token configured on your server to access projects and compute resources.",
        )}
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const result = await action(() =>
            api("/auth/login", "POST", { token }),
          );
          setBusy(false);
          if (result) {
            setToken("");
            location.reload();
          }
        }}
      >
        <Field label={t("拥有者令牌", "Owner token")}>
          <input
            type="password"
            required
            autoFocus
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </Field>
        <div className="modal-actions">
          <Button className="primary" busy={busy} type="submit">
            {t("连接工作空间", "Connect workspace")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ProjectShare({ projectId }: { projectId: string }) {
  const { t, action } = useUI();
  const [open, setOpen] = useState(false);
  const { data: graph } = useLoad<Json>(`/projects/${projectId}/graph`, {
    nodes: [],
  });
  const [selected, setSelected] = useState<string[]>([]);
  const [description, setDescription] = useState(false);
  const [share, setShare] = useState<Json | null>(null);
  const [revoke, setRevoke] = useState("");
  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <ArrowUpRight size={15} />
        {t("只读展示", "Read-only share")}
      </Button>
      {open && (
        <Modal
          title={t("分享选定的研究内容", "Share selected research content")}
          onClose={() => setOpen(false)}
        >
          <p className="muted">
            {t(
              "访问者只能看到选中节点的标题、类型和状态，无法运行任务或打开终端。",
              "Visitors can see only selected node titles, types, and status. They cannot execute tasks or access the terminal.",
            )}
          </p>
          <div className="share-node-list">
            {graph.nodes.map((n: Json) => (
              <label className="checkbox-label" key={n.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(n.id)}
                  onChange={(e) =>
                    setSelected((s) =>
                      e.target.checked
                        ? [...s, n.id]
                        : s.filter((id) => id !== n.id),
                    )
                  }
                />
                {n.title}
              </label>
            ))}
          </div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={description}
              onChange={(e) => setDescription(e.target.checked)}
            />
            {t("包含项目描述", "Include project description")}
          </label>
          <Button
            className="primary"
            disabled={!selected.length}
            onClick={() =>
              action(async () =>
                setShare(
                  await api(`/projects/${projectId}/share`, "POST", {
                    node_ids: selected,
                    description,
                  }),
                ),
              )
            }
          >
            {t("创建只读链接", "Create read-only link")}
          </Button>
          {share && (
            <div className="share-result">
              <input
                readOnly
                aria-label="Read-only share URL"
                value={location.origin + share.url}
              />
              <a
                className="button"
                target="_blank"
                rel="noreferrer"
                href={share.url}
              >
                {t("打开展示", "Open shared view")}
              </a>
              <Button
                onClick={() =>
                  action(
                    async () => {
                      await navigator.clipboard.writeText(
                        location.origin + share.url,
                      );
                    },
                    t("链接已复制", "Link copied"),
                  )
                }
              >
                <Copy size={14} />
                {t("复制链接", "Copy link")}
              </Button>
              <Button
                onClick={() =>
                  action(
                    async () => {
                      await api(`/shares/${share.token}`, "DELETE");
                      setShare(null);
                    },
                    t("展示链接已撤销", "Share revoked"),
                  )
                }
              >
                {t("撤销此链接", "Revoke this link")}
              </Button>
            </div>
          )}
          <details>
            <summary>
              {t("撤销已有展示链接", "Revoke an existing share")}
            </summary>
            <Field label={t("展示链接或令牌", "Share URL or token")}>
              <input
                value={revoke}
                onChange={(e) => setRevoke(e.target.value)}
              />
            </Field>
            <Button
              disabled={!revoke.trim()}
              onClick={() =>
                action(
                  async () => {
                    await api(
                      `/shares/${revoke.trim().split("/").pop()}`,
                      "DELETE",
                    );
                    setRevoke("");
                  },
                  t("链接已撤销", "Share revoked"),
                )
              }
            >
              {t("撤销访问", "Revoke access")}
            </Button>
          </details>
        </Modal>
      )}
    </>
  );
}
function SharedProject() {
  const { token } = useParams();
  const { t } = useUI();
  const { data, error, reload } = useLoad<Json>(`/shares/${token}`, null!);
  return (
    <>
      <header className="topbar">
        <div className="topbar-project">
          <TreePine size={17} />
          FOREST · {t("只读研究展示", "Read-only research view")}
        </div>
        <Badge status="idle">Read only</Badge>
      </header>
      <div className="page">
        <ErrorBox error={error} retry={reload} />
        {data && (
          <>
            <PageHeading
              eyebrow="SHARED RESEARCH"
              title={data.project.name}
              description={data.project.description}
            />
            <div className="idea-grid">
              {data.nodes.map((node: Json) => (
                <section className="surface" key={node.id}>
                  <span className="eyebrow">{node.type.toUpperCase()}</span>
                  <h2 style={{ marginTop: 18 }}>{node.title}</h2>
                  <div className="inline-actions">
                    <Badge status={node.execution_status} />
                    <Badge status={node.research_status} />
                    <Badge status={node.deliverable_status} />
                  </div>
                </section>
              ))}
            </div>
          </>
        )}
      </div>
    </>
  );
}
