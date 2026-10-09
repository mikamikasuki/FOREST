import { SourceExplorer } from "./progress/SourceExplorer";
import { DependencyImpact } from "./interventions/DependencyImpact";
import { useState, useEffect, useRef } from "react";
import { useParams, NavLink } from "react-router-dom";
import {
  Plus,
  Search,
  ArrowUpRight,
  BookOpen,
  Download,
  Trash2,
  Pencil,
  Play,
  RefreshCw,
  FileText,
  Upload,
  Check,
  ExternalLink,
  Lightbulb,
  FlaskConical,
  ChartNoAxesCombined,
  SquareFunction,
  Settings,
  PlugZap,
  Server,
  KeyRound,
  Folder,
  File,
  Save,
  Send,
  Code,
  Image,
  Split,
  Table2,
  GitCompare,
  ChevronRight,
  ArrowLeft,
  X,
  Copy,
  Link as LinkIcon,
} from "lucide-react";
import { api, download, uid, parseJson, formatDate } from "./api";
import type { Json, RecordItem, Run } from "./api";
import { ProviderUsage } from "./ProviderUsage";
import { ExecutionSettings } from "./ExecutionSettings";
import {
  PaperLayoutSettings,
  PaperPreflight,
  paperLayoutDefaults,
  paperTemplate,
} from "./PaperLayout";
import type { PaperTemplate, PaperLayoutConfig } from "./PaperLayout";
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
  PageHeading,
  Loading,
} from "./ui";
import { CodeEditor, PDFViewer, Formula } from "./editors";
import { RunPanel } from "./Workspace";
import { demoLink } from "./DemoGuide";
function useProjectRecords(resource: string) {
  const { id } = useParams();
  const result = useLoad<RecordItem[]>(`/${resource}?project_id=${id}`, []);
  useEffect(() => {
    const refresh = () => void result.reload();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [result.reload]);
  return { ...result, id };
}
function RecordEditor({
  resource,
  item,
  projectId,
  initial = {},
  onClose,
  onSaved,
}: {
  resource: string;
  item?: RecordItem;
  projectId: string;
  initial?: Json;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t, action } = useUI();
  const [title, setTitle] = useState(item?.title || "");
  const [data, setData] = useState(
    JSON.stringify(item?.data || initial, null, 2),
  );
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      wide
      title={
        item ? t("编辑内容", "Edit record") : t("新建内容", "Create record")
      }
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const result = await action(() =>
            api(
              item ? `/${resource}/${item.id}` : `/${resource}`,
              item ? "PATCH" : "POST",
              {
                project_id: projectId,
                title,
                data: parseJson(data),
                ...(item ? { expected_revision: item.revision } : {}),
              },
            ),
          );
          setBusy(false);
          if (result) {
            onSaved();
            onClose();
          }
        }}
      >
        <Field label={t("标题", "Title")}>
          <input
            autoFocus
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Field>
        <Field label={t("研究内容与配置", "Research content & configuration")}>
          <textarea
            className="code-input"
            rows={17}
            value={data}
            onChange={(e) => setData(e.target.value)}
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            {t("取消", "Cancel")}
          </Button>
          <Button className="primary" type="submit" busy={busy}>
            <Save size={14} />
            {t("保存", "Save")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function RecordActions({
  resource,
  item,
  reload,
  onEdit,
}: {
  resource: string;
  item: RecordItem;
  reload: () => void;
  onEdit: () => void;
}) {
  const { t, action } = useUI();
  return (
    <>
      <IconButton label={t("编辑", "Edit")} onClick={onEdit}>
        <Pencil size={14} />
      </IconButton>
      <IconButton
        label={t("删除", "Delete")}
        onClick={() => {
          if (confirm(t("删除此条内容？", "Delete this record?")))
            void action(async () => {
              await api(`/${resource}/${item.id}`, "DELETE");
              reload();
            });
        }}
      >
        <Trash2 size={14} />
      </IconButton>
    </>
  );
}
export function LibraryPage() {
  const {
    data: papers,
    id,
    reload,
    error,
    loading,
  } = useProjectRecords("library");
  const { t, action } = useUI();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("crossref");
  const [results, setResults] = useState<Json[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [selected, setSelected] = useState<RecordItem | null>(null);
  const [editing, setEditing] = useState<RecordItem | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [webUrl, setWebUrl] = useState("");
  const [webScreenshot, setWebScreenshot] = useState(false);
  const [webResult, setWebResult] = useState<Json | null>(null);
  const [webImporting, setWebImporting] = useState(false);
  const webImportLock = useRef(false);
  const [passages, setPassages] = useState<Json | null>(null);
  const [tab, setTab] = useState("abstract");
  const [filter, setFilter] = useState("");
  const searchRequest = useRef(0);
  const performSearch = async () => {
    const request = ++searchRequest.current;
    const query = search;
    setSearching(true);
    setResults([]);
    setSearched(true);
    try {
      await action(async () => {
        const r = await api("/library/search", "POST", {
          query,
          source,
          limit: 8,
        });
        if (request === searchRequest.current) setResults(r.results || []);
      });
    } finally {
      if (request === searchRequest.current) setSearching(false);
    }
  };
  return (
    <div className="page">
      <PageHeading
        title={t("文献", "Library")}
        actions={
          <Button
            onClick={() =>
              action(() =>
                download(
                  `/library/export-bibtex?project_id=${id}`,
                  undefined,
                  "references.bib",
                ),
              )
            }
          >
            <Download size={15} />
            BibTeX
          </Button>
        }
      />
      <section className="literature-search surface">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void performSearch();
          }}
        >
          <Search size={20} />
          <input
            aria-label="Literature search"
            required
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t(
              "搜索论文标题、作者或研究问题…",
              "Search titles, authors, or a research question…",
            )}
          />
          <select
            aria-label="Literature source"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          >
            <option value="crossref">Crossref</option>
            <option value="arxiv">arXiv</option>
          </select>
          <Button className="primary" type="submit" busy={searching}>
            {t("检索文献", "Search literature")}
            <ArrowUpRight size={15} />
          </Button>
        </form>
        <div className="identifier-import">
          <input
            aria-label="DOI or arXiv identifier"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="DOI / arXiv ID / URL"
          />
          <Button
            disabled={!identifier.trim()}
            onClick={() =>
              action(async () => {
                await api("/library/import", "POST", {
                  project_id: id,
                  identifier,
                });
                setIdentifier("");
                await reload();
              })
            }
          >
            <Plus size={14} />
            {t("导入", "Import")}
          </Button>
        </div>
        <details className="web-import">
          <summary>{t("导入公开网页或文档", "Import a web page")}</summary>
          <div className="inline-actions">
            <input
              aria-label="Public page URL"
              type="url"
              value={webUrl}
              onChange={(e) => setWebUrl(e.target.value)}
              placeholder="https://…"
            />
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={webScreenshot}
                onChange={(e) => setWebScreenshot(e.target.checked)}
              />
              {t("保存截图", "Capture screenshot")}
            </label>
            <Button
              disabled={!webUrl.trim() || webImporting}
              busy={webImporting}
              onClick={() =>
                action(async () => {
                  if (webImportLock.current) return;
                  webImportLock.current = true;
                  setWebImporting(true);
                  try {
                    setWebResult(
                      await api("/browser/read", "POST", {
                        project_id: id,
                        url: webUrl,
                        screenshot: webScreenshot,
                      }),
                    );
                    await reload();
                  } finally {
                    webImportLock.current = false;
                    setWebImporting(false);
                  }
                })
              }
            >
              <BookOpen size={14} />
              {t("读取并保存", "Read & save")}
            </Button>
          </div>
          {webResult && <JsonView value={webResult} />}
        </details>
      </section>
      <ErrorBox error={error} retry={reload} />
      {searched && (
        <section className="surface">
          <div className="section-toolbar">
            <h2>
              {t("检索结果", "Search results")}
              <span className="count">{results.length}</span>
            </h2>
            <IconButton
              label="Close results"
              onClick={() => setSearched(false)}
            >
              <X size={16} />
            </IconButton>
          </div>
          {results.length ? (
            results.map((p, i) => (
              <div className="paper-row" key={i}>
                <span className="paper-number">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3>{p.title}</h3>
                  <p>
                    {Array.isArray(p.authors)
                      ? p.authors
                          .map((a: any) =>
                            typeof a === "string"
                              ? a
                              : a.name || `${a.given || ""} ${a.family || ""}`,
                          )
                          .join(", ")
                      : p.authors}{" "}
                    · {p.year || "—"}
                  </p>
                  <small>{p.doi || p.arxiv_id}</small>
                </div>
                {p.url && (
                  <a
                    className="icon-button"
                    href={p.url}
                    target="_blank"
                    rel="noreferrer"
                    title="Source"
                  >
                    <ExternalLink size={15} />
                  </a>
                )}
                <Button
                  onClick={() =>
                    action(
                      async () => {
                        await api("/library/import", "POST", {
                          project_id: id,
                          paper: p,
                        });
                        await reload();
                      },
                      t("已添加到论文库", "Added to library"),
                    )
                  }
                >
                  <Plus size={14} />
                  {t("保存", "Save")}
                </Button>
              </div>
            ))
          ) : (
            <Empty title={t("没有找到匹配文献", "No matching papers")} />
          )}
        </section>
      )}
      <div className="section-toolbar">
        <h2>
          {t("项目论文库", "Project library")}
          <span className="count">{papers.length}</span>
        </h2>
        <label className="search-input">
          <Search size={14} />
          <input
            placeholder={t("筛选已保存文献", "Filter saved papers")}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </label>
      </div>
      {loading ? (
        <Loading />
      ) : papers.length ? (
        <div className="surface paper-list">
          {papers
            .filter((p) => p.title.toLowerCase().includes(filter.toLowerCase()))
            .map((p, i) => (
              <div className="paper-row" key={p.id}>
                <span className="paper-number">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <button
                    className="text-title"
                    onClick={() => {
                      setSelected(p);
                      setPassages(null);
                    }}
                  >
                    {p.title}
                  </button>
                  <p>
                    {Array.isArray(p.data.authors)
                      ? p.data.authors
                          .map((a: any) =>
                            typeof a === "string"
                              ? a
                              : a.name || `${a.given || ""} ${a.family || ""}`,
                          )
                          .join(", ")
                      : p.data.authors}{" "}
                    · {p.data.year || "—"}
                  </p>
                  <small>
                    {p.data.doi || p.data.arxiv_id || p.data.source}
                  </small>
                </div>
                <Badge status={p.data.pdf_path ? "completed" : "idle"}>
                  {p.data.pdf_path
                    ? t("全文可用", "Full text")
                    : t("文献信息", "Metadata")}
                </Badge>
                <RecordActions
                  resource="library"
                  item={p}
                  reload={reload}
                  onEdit={() => setEditing(p)}
                />
              </div>
            ))}
        </div>
      ) : (
        <Empty
          icon={<BookOpen size={28} />}
          title={t("暂无文献", "No papers")}
        />
      )}{" "}
      {selected && (
        <Modal wide title={selected.title} onClose={() => setSelected(null)}>
          <Tabs
            value={tab}
            onChange={setTab}
            items={[
              { id: "abstract", label: t("摘要与来源", "Abstract & source") },
              { id: "fulltext", label: t("全文", "Full text") },
              { id: "citation", label: "BibTeX" },
              { id: "passages", label: t("原文定位", "Passages") },
            ]}
          />
          {tab === "abstract" && (
            <div className="paper-detail">
              <p>
                {selected.data.abstract ||
                  t("来源未提供摘要。", "No abstract provided by the source.")}
              </p>
              <p>{selected.data.notes}</p>
              {selected.data.url && (
                <a
                  className="button"
                  href={selected.data.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink size={14} />
                  {t("打开原始来源", "Open original source")}
                </a>
              )}
            </div>
          )}
          {tab === "fulltext" &&
            (selected.data.pdf_path ? (
              <div className="modal-pdf">
                <PDFViewer
                  url={`/api/projects/${id}/download?path=${encodeURIComponent(selected.data.pdf_path)}`}
                />
              </div>
            ) : (
              <Empty
                title={t("全文尚未导入", "Full text not imported")}
                description={t(
                  "可通过项目文件上传 PDF；在文献内容中设置 pdf_path。",
                  "Upload the PDF through project files and set pdf_path in this reference.",
                )}
                action={
                  <Button
                    onClick={() => {
                      setEditing(selected);
                      setSelected(null);
                    }}
                  >
                    {t("编辑文献路径", "Edit reference path")}
                  </Button>
                }
              />
            ))}
          {tab === "citation" && (
            <pre className="json-view">
              {selected.data.bibtex ||
                t("此来源尚无 BibTeX", "No BibTeX available")}
            </pre>
          )}
          {tab === "passages" && (
            <>
              <Button
                onClick={() =>
                  action(async () =>
                    setPassages(await api(`/library/${selected.id}/passages`)),
                  )
                }
              >
                {t("读取定位段落", "Load source passages")}
              </Button>
              {passages && <JsonView value={passages} />}
            </>
          )}
        </Modal>
      )}
      {editing && (
        <RecordEditor
          resource="library"
          item={editing}
          projectId={id!}
          onClose={() => setEditing(null)}
          onSaved={reload}
        />
      )}
    </div>
  );
}
export function ResearchPage({ resource }: { resource: string }) {
  const {
    data: records,
    id,
    error,
    reload,
    loading,
  } = useProjectRecords(resource);
  const { t, action } = useUI();
  const [editing, setEditing] = useState<RecordItem | null | false>(false);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="page">
      <PageHeading
        title={t("想法", "Ideas")}
        actions={
          <Button onClick={() => setEditing(null)}>
            <Plus size={15} />
            {t("记录想法", "Add idea")}
          </Button>
        }
      />
      <section className="surface idea-prompt">
        <div>
          <h3>{t("研究问题", "Research question")}</h3>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t(
              "描述希望探索的问题…",
              "Describe the question to explore…",
            )}
          />
          <Button
            className="primary"
            busy={busy}
            disabled={!prompt.trim()}
            onClick={async () => {
              setBusy(true);
              await action(
                async () => {
                  await api("/research/ideas", "POST", {
                    project_id: id,
                    prompt,
                    count: 3,
                  });
                  setPrompt("");
                  await reload();
                },
                t("研究任务已排队", "Research queued"),
              );
              setBusy(false);
            }}
          >
            <Lightbulb size={15} />
            {t("生成候选方向", "Generate directions")}
          </Button>
        </div>
      </section>
      <ErrorBox error={error} retry={reload} />
      {loading ? (
        <Loading />
      ) : records.length ? (
        <div className="idea-grid">
          {records.map((r) => (
            <article key={r.id} className="surface idea-card">
              <div className="section-toolbar">
                <Badge status={r.status} />
              </div>
              <h2>{r.title}</h2>
              {[
                ["hypothesis", "可证伪命题", "Falsifiable hypothesis"],
                ["mechanism", "作用机制", "Mechanism"],
                ["nearest_work", "最近邻工作", "Nearest work"],
                ["decisive_experiment", "区分实验", "Decisive experiment"],
                ["best_estimate", "当前最佳判断", "Best estimate"],
              ].map(
                ([key, zh, en]) =>
                  r.data[key] && (
                    <div className="idea-field" key={key}>
                      <span>{t(zh, en)}</span>
                      <p>
                        {typeof r.data[key] === "string"
                          ? r.data[key]
                          : JSON.stringify(r.data[key])}
                      </p>
                    </div>
                  ),
              )}
              {r.data.probability_range && (
                <div className="estimate">
                  <span>
                    {t(
                      "有意义改进的估计概率",
                      "Estimated probability of meaningful improvement",
                    )}
                  </span>
                  <strong>{String(r.data.probability_range)}</strong>
                </div>
              )}
              {Array.isArray(r.data.commands) && (
                <ProposalCommands record={r} onApplied={reload} />
              )}
              <details>
                <summary>{t("查看完整分析", "Full analysis")}</summary>
                <JsonView value={r.data} />
              </details>
              <div className="record-footer">
                <RecordActions
                  resource={resource}
                  item={r}
                  reload={reload}
                  onEdit={() => setEditing(r)}
                />
                <Button
                  className="primary"
                  disabled={Array.isArray(r.data.commands)}
                  onClick={() =>
                    action(
                      async () => {
                        await api(`/ideas/${r.id}/adopt`, "POST", {});
                        await reload();
                      },
                      t("已采用为研究节点", "Adopted as research nodes"),
                    )
                  }
                >
                  {t("采用此路线", "Adopt direction")}
                  <ArrowUpRight size={14} />
                </Button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty title={t("暂无想法", "No ideas")} />
      )}{" "}
      {editing !== false && (
        <RecordEditor
          resource={resource}
          item={editing || undefined}
          projectId={id!}
          initial={{
            hypothesis: "",
            mechanism: "",
            nearest_work: "",
            decisive_experiment: "",
            best_estimate: "",
            probability_range: "",
            confidence: "",
            against: "",
            resources: {},
          }}
          onClose={() => setEditing(false)}
          onSaved={reload}
        />
      )}
    </div>
  );
}
export function TheoryPage() {
  const { data: records, id, error, reload } = useProjectRecords("theories");
  const { t, action } = useUI();
  const [expression, setExpression] = useState("");
  const [variable, setVariable] = useState("x");
  const [kind, setKind] = useState("simplify");
  const [values, setValues] = useState("{}");
  const [result, setResult] = useState<Json | null>(null);
  const [editing, setEditing] = useState<RecordItem | null | false>(false);
  const [busy, setBusy] = useState(false);
  return (
    <div className="page">
      <PageHeading
        title={t("理论", "Theory")}
        actions={
          <>
            <Button onClick={() => setEditing(null)}>
              <Plus size={15} />
              {t("新建推导", "New derivation")}
            </Button>
            <NavLink
              className="button"
              to={demoLink(`/projects/${id}/experiments`)}
            >
              {t("打开实验工作台", "Open experiments")}
              <ArrowUpRight size={14} />
            </NavLink>
          </>
        }
      />
      <div>
        <section className="surface">
          <h3>
            <SquareFunction size={18} />
            {t("数学检查", "Mathematical check")}
          </h3>
          <Field label={t("表达式", "Expression")}>
            <textarea
              className="code-input"
              rows={5}
              value={expression}
              onChange={(e) => setExpression(e.target.value)}
              placeholder="(x + 1)**2 - (x**2 + 2*x + 1)"
            />
          </Field>
          {kind === "numeric" && (
            <Field
              label={t("数值代入（JSON）", "Numeric substitutions (JSON)")}
            >
              <input
                className="code-input"
                value={values}
                onChange={(e) => setValues(e.target.value)}
                placeholder='{"x": 1}'
              />
            </Field>
          )}
          <div className="form-row">
            <Field label={t("运算", "Operation")}>
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="simplify">Simplify</option>
                <option value="differentiate">Differentiate</option>
                <option value="solve">Solve</option>
                <option value="numeric">Numeric</option>
              </select>
            </Field>
            <Field label={t("变量", "Variable")}>
              <input
                value={variable}
                onChange={(e) => setVariable(e.target.value)}
              />
            </Field>
          </div>
          <Button
            className="primary"
            disabled={!expression.trim()}
            busy={busy}
            onClick={async () => {
              setBusy(true);
              await action(async () => {
                setResult(
                  await api("/theory/check", "POST", {
                    project_id: id,
                    expression,
                    variable,
                    kind,
                    values: parseJson(values),
                  }),
                );
                await reload();
              });
              setBusy(false);
            }}
          >
            <Play size={14} />
            {t("执行检查", "Run check")}
          </Button>
          {result && <JsonView value={result} />}
        </section>
      </div>
      <ErrorBox error={error} retry={reload} />
      {records.length ? (
        <div className="record-list">
          {records.map((r) => (
            <section key={r.id} className="surface">
              <div className="section-toolbar">
                <h3>{r.title}</h3>
                <RecordActions
                  resource="theories"
                  item={r}
                  reload={reload}
                  onEdit={() => setEditing(r)}
                />
              </div>
              <p className="muted">
                {typeof r.data.assumptions === "object"
                  ? JSON.stringify(r.data.assumptions)
                  : r.data.assumptions || r.data.expression}
              </p>
              {r.data.latex && <Formula source={r.data.latex} />}
              <pre className="derivation">
                {r.data.derivation ||
                  r.data.result ||
                  r.data.output ||
                  JSON.stringify(r.data, null, 2)}
              </pre>
              <Badge status={r.status} />
            </section>
          ))}
        </div>
      ) : (
        <Empty title={t("暂无推导", "No derivations")} />
      )}{" "}
      {editing !== false && (
        <RecordEditor
          resource="theories"
          item={editing || undefined}
          projectId={id!}
          initial={{
            symbols: {},
            assumptions: "",
            claim: "",
            derivation: "",
            counterexamples: [],
            experiment_links: [],
          }}
          onClose={() => setEditing(false)}
          onSaved={reload}
        />
      )}
    </div>
  );
}
function ExperimentForm({
  projectId,
  onClose,
  onSaved,
  item,
}: {
  projectId: string;
  onClose: () => void;
  onSaved: () => void;
  item?: RecordItem;
}) {
  const { t, action } = useUI();
  const [title, setTitle] = useState(item?.title || "");
  const [command, setCommand] = useState(item?.data.command || "");
  const [code, setCode] = useState(item?.data.code || "");
  const [dataset, setDataset] = useState(item?.data.dataset || "");
  const [seeds, setSeeds] = useState((item?.data.seeds || [0, 1, 2]).join(","));
  const [params, setParams] = useState(
    JSON.stringify(item?.data.parameters || {}, null, 2),
  );
  const [seconds, setSeconds] = useState(
    String(item?.data.timeout ?? item?.data.budget?.seconds ?? ""),
  );
  const [duty, setDuty] = useState(item?.data.duty || "effectiveness");
  const [executionConfig, setExecutionConfig] = useState<Json>(
    item?.data || {},
  );
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      wide
      title={t("实验配置", "Experiment configuration")}
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const r = await action(() =>
            api(
              item ? `/experiments/${item.id}` : "/experiments",
              item ? "PATCH" : "POST",
              {
                project_id: projectId,
                title,
                data: {
                  ...item?.data,
                  ...executionConfig,
                  command,
                  code,
                  dataset,
                  seeds: seeds.split(",").map(Number),
                  parameters: parseJson(params),
                  budget: seconds.trim() ? { seconds: Number(seconds) } : {},
                  timeout: seconds.trim() ? Number(seconds) : null,
                  duty,
                },
                ...(item ? { expected_revision: item.revision } : {}),
              },
            ),
          );
          setBusy(false);
          if (r) {
            onSaved();
            onClose();
          }
        }}
      >
        <div className="form-row">
          <Field label={t("实验名称", "Experiment name")}>
            <input
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <Field label={t("论证职责", "Argumentative duty")}>
            <select value={duty} onChange={(e) => setDuty(e.target.value)}>
              <option value="effectiveness">
                {t("证明有效性", "Effectiveness")}
              </option>
              <option value="mechanism">
                {t("验证关键机制", "Mechanism")}
              </option>
              <option value="scenario">
                {t("验证场景价值", "Scenario value")}
              </option>
              <option value="alternative">
                {t("排除替代解释", "Alternative explanations")}
              </option>
            </select>
          </Field>
        </div>
        <Field label={t("数据路径或来源", "Dataset path or source")}>
          <input value={dataset} onChange={(e) => setDataset(e.target.value)} />
        </Field>
        <ExecutionSettings
          config={executionConfig}
          onChange={setExecutionConfig}
        />
        <Field label={t("实际执行命令", "Execution command")}>
          <input
            className="code-input"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder="python experiment.py"
          />
        </Field>
        <Field label={t("Python 代码（可选）", "Python code (optional)")}>
          <div className="bounded-editor">
            <CodeEditor value={code} onChange={setCode} />
          </div>
        </Field>
        <div className="form-row">
          <Field
            label={t("随机种子（逗号分隔）", "Random seeds (comma separated)")}
          >
            <input value={seeds} onChange={(e) => setSeeds(e.target.value)} />
          </Field>
          <Field label="Time budget (seconds, optional)">
            <input
              type="number"
              min="1"
              placeholder="No time limit"
              value={seconds}
              onChange={(e) => setSeconds(e.target.value)}
            />
          </Field>
        </div>
        <Field
          label={t("超参数 / 扫描配置", "Parameters / sweep configuration")}
        >
          <textarea
            rows={4}
            className="code-input"
            value={params}
            onChange={(e) => setParams(e.target.value)}
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            {t("取消", "Cancel")}
          </Button>
          <Button className="primary" type="submit" busy={busy}>
            <Save size={14} />
            {t("保存实验配置", "Save experiment")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
export function ExperimentsPage() {
  const {
    data: experiments,
    id,
    error,
    reload,
  } = useProjectRecords("experiments");
  const { data: runs, reload: reloadRuns } = useLoad<Run[]>(
    `/projects/${id}/runs`,
    [],
  );
  const { t, action } = useUI();
  const [editing, setEditing] = useState<RecordItem | null | false>(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Json | null>(null);
  useEffect(() => {
    const f = () => void reloadRuns();
    window.addEventListener("forest-refresh", f);
    return () => window.removeEventListener("forest-refresh", f);
  }, [reloadRuns]);
  return (
    <div className="page">
      <PageHeading
        title={t("实验", "Experiments")}
        actions={
          <Button className="primary" onClick={() => setEditing(null)}>
            <Plus size={15} />
            {t("新建实验", "New experiment")}
          </Button>
        }
      />
      <ErrorBox error={error} retry={reload} />
      {experiments.length ? (
        <div className="experiment-grid">
          {experiments.map((e) => (
            <section key={e.id} className="surface experiment-card">
              <div className="section-toolbar">
                <span className="experiment-icon">
                  <FlaskConical size={19} />
                </span>
                <Badge status={e.status} />
              </div>
              <h2>{e.title}</h2>
              <p>
                {e.data.duty ||
                  t("尚未填写论证职责", "No argumentative duty set")}
              </p>
              <div className="experiment-meta">
                <span>
                  Seeds<strong>{(e.data.seeds || []).join(", ") || "—"}</strong>
                </span>
                <span>
                  {t("预算", "Budget")}
                  <strong>
                    {e.data.budget?.seconds ? `${e.data.budget.seconds}s` : "—"}
                  </strong>
                </span>
              </div>
              <code className="command-preview">
                {e.data.command || t("Python 代码执行", "Python execution")}
              </code>
              <div className="record-footer">
                <RecordActions
                  resource="experiments"
                  item={e}
                  reload={reload}
                  onEdit={() => setEditing(e)}
                />
                <Button
                  className="primary"
                  onClick={() =>
                    action(
                      async () => {
                        await api(`/experiments/${e.id}/launch`, "POST", {
                          request_id: uid(),
                        });
                        await reloadRuns();
                      },
                      t(
                        "实验已加入真实执行队列",
                        "Experiment queued for execution",
                      ),
                    )
                  }
                >
                  <Play size={13} />
                  {t("启动实验", "Launch")}
                </Button>
              </div>
            </section>
          ))}
        </div>
      ) : (
        <Empty
          icon={<FlaskConical size={28} />}
          title={t("暂无实验", "No experiments")}
          action={
            <Button onClick={() => setEditing(null)}>
              <Plus size={15} />
              {t("配置实验", "Configure experiment")}
            </Button>
          }
        />
      )}
      <section className="surface">
        <div className="section-toolbar">
          <h2>
            {t("任务与结果", "Tasks & results")}
            <span className="count">{runs.length}</span>
          </h2>
          <Button
            disabled={selected.length < 2}
            onClick={() =>
              action(async () =>
                setComparison(
                  await api("/experiments/compare", "POST", {
                    run_ids: selected,
                  }),
                ),
              )
            }
          >
            <GitCompare size={15} />
            {t("比较选中运行", "Compare runs")} ({selected.length})
          </Button>
        </div>
        {runs.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th />
                  <th>{t("运行", "Run")}</th>
                  <th>{t("状态", "Status")}</th>
                  <th>{t("执行时间", "Started")}</th>
                  <th>{t("实际指标", "Measured metrics")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.includes(r.id)}
                        onChange={(e) =>
                          setSelected((s) =>
                            e.target.checked
                              ? [...s, r.id]
                              : s.filter((x) => x !== r.id),
                          )
                        }
                        aria-label={`Select ${r.id}`}
                      />
                    </td>
                    <td>
                      <strong>{r.kind}</strong>
                      <small>{r.id.slice(0, 8)}</small>
                    </td>
                    <td>
                      <Badge status={r.status} />
                    </td>
                    <td>{formatDate(r.started_at)}</td>
                    <td>
                      <code>{compactMetrics(r.metrics || {})}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="experiment-runs">
          <RunPanel runs={runs} reload={reloadRuns} />
        </div>
      </section>
      {comparison && (
        <Modal
          wide
          title={t("运行结果比较", "Run comparison")}
          onClose={() => setComparison(null)}
        >
          <JsonView value={comparison} />
        </Modal>
      )}
      {editing !== false && (
        <ExperimentForm
          projectId={id!}
          item={editing || undefined}
          onClose={() => setEditing(false)}
          onSaved={reload}
        />
      )}
    </div>
  );
}
export function DataPage() {
  const { id } = useParams();
  const { t, action } = useUI();
  const {
    data: runs,
    error,
    reload,
  } = useLoad<Run[]>(`/projects/${id}/runs`, []);
  useEffect(() => {
    const refresh = () => void reload();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reload]);
  const { data: analyses, reload: reloadAnalyses } = useLoad<RecordItem[]>(
    `/analyses?project_id=${id}`,
    [],
  );
  const [selected, setSelected] = useState<string[]>([]);
  const [metric, setMetric] = useState("accuracy");
  const [baseline, setBaseline] = useState("");
  const [status, setStatus] = useState("all");
  const [detail, setDetail] = useState<RecordItem | null>(null);
  const metrics = [
    ...new Set(
      runs.flatMap((r) =>
        Object.entries(r.metrics || {})
          .filter(([, value]) => typeof value === "number")
          .map(([key]) => key),
      ),
    ),
  ];
  const visible = runs.filter(
    (r) =>
      status === "all" ||
      (status === "succeeded"
        ? r.status === "completed" || r.status === "succeeded"
        : r.status === status),
  );
  const exportCSV = () => {
    const fields = ["run_id", "kind", "status", ...metrics];
    const cell = (v: any) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const text = [
      fields.map(cell).join(","),
      ...visible.map((r) =>
        [r.id, r.kind, r.status, ...metrics.map((k) => r.metrics?.[k])]
          .map(cell)
          .join(","),
      ),
    ].join("\n");
    const a = document.createElement("a");
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/csv;charset=utf-8" }),
    );
    a.href = url;
    a.download = "measured-runs.csv";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="page">
      <PageHeading
        title={t("数据分析", "Data & analysis")}
        actions={
          <Button disabled={!runs.length} onClick={exportCSV}>
            <Download size={15} />
            CSV
          </Button>
        }
      />
      <ErrorBox error={error} retry={reload} />
      <MeasuredResults runs={runs} />
      <RawDataBrowser runs={runs} />
      <section className="surface analysis-controls">
        <Field label={t("主指标", "Primary metric")}>
          <input
            list="metrics"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          />
          <datalist id="metrics">
            {metrics.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </datalist>
        </Field>
        <Field label={t("基线运行或方法", "Baseline run or method")}>
          <input
            value={baseline}
            onChange={(e) => setBaseline(e.target.value)}
            placeholder={t("运行 ID 或方法名", "Run ID or method name")}
          />
        </Field>
        <Field label={t("筛选状态", "Filter status")}>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">
              {t("全部（含缺失/失败）", "All (including failed)")}
            </option>
            <option value="succeeded">{t("成功", "Succeeded")}</option>
            {["failed", "running", "queued"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
        <Button
          className="primary"
          disabled={!runs.length}
          onClick={() =>
            action(
              async () => {
                await api("/analysis/run", "POST", {
                  project_id: id,
                  run_ids: selected.length
                    ? selected
                    : runs
                        .filter((r) => Object.keys(r.metrics || {}).length)
                        .map((r) => r.id),
                  baseline,
                  metric,
                });
                await reloadAnalyses();
              },
              t("统计分析已排队", "Analysis queued"),
            )
          }
        >
          <Play size={14} />
          {t("重新计算统计", "Recompute statistics")}
        </Button>
      </section>
      {runs.length ? (
        <section className="surface table-surface">
          <table>
            <thead>
              <tr>
                <th />
                <th>{t("运行 / 方法", "Run / method")}</th>
                <th>{t("状态", "Status")}</th>
                {metrics.map((m) => (
                  <th key={m}>{m}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.id}>
                  <td>
                    <input
                      aria-label={`Select ${r.id}`}
                      type="checkbox"
                      checked={selected.includes(r.id)}
                      onChange={(e) =>
                        setSelected((s) =>
                          e.target.checked
                            ? [...s, r.id]
                            : s.filter((x) => x !== r.id),
                        )
                      }
                    />
                  </td>
                  <td>
                    {r.kind}
                    <small>{r.id.slice(0, 8)}</small>
                  </td>
                  <td>
                    <Badge status={r.status} />
                  </td>
                  {metrics.map((m) => (
                    <td className="numeric" key={m}>
                      {r.metrics?.[m] === undefined ? (
                        <span className="missing">{t("缺失", "Missing")}</span>
                      ) : typeof r.metrics[m] === "number" ? (
                        r.metrics[m].toPrecision(5)
                      ) : (
                        JSON.stringify(r.metrics[m])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : (
        <Empty
          icon={<Table2 size={28} />}
          title={t("暂无数据", "No data")}
          action={
            <NavLink
              className="button"
              to={demoLink(`/projects/${id}/experiments`)}
            >
              {t("前往实验", "Open experiments")}
              <ArrowUpRight size={14} />
            </NavLink>
          }
        />
      )}
      <div className="section-toolbar">
        <h2>{t("已保存分析", "Saved analyses")}</h2>
        <NavLink className="button" to={demoLink(`/projects/${id}/figures`)}>
          <ChartNoAxesCombined size={15} />
          {t("打开图表工作室", "Open Figure studio")}
        </NavLink>
      </div>
      <div className="record-list">
        {analyses.map((a) => (
          <button
            className="surface analysis-row"
            key={a.id}
            onClick={() => setDetail(a)}
          >
            <ChartNoAxesCombined size={20} />
            <div>
              <strong>{a.title}</strong>
              <p>{formatDate(a.updated_at)}</p>
            </div>
            <Badge status={a.status} />
            <ArrowUpRight size={17} />
          </button>
        ))}
      </div>
      {detail && (
        <Modal wide title={detail.title} onClose={() => setDetail(null)}>
          <JsonView value={detail.data} />
        </Modal>
      )}
    </div>
  );
}
export function FiguresPage() {
  const { data: figures, id, error, reload } = useProjectRecords("figures");
  const { data: runs, reload: reloadRuns } = useLoad<Run[]>(
    `/projects/${id}/runs`,
    [],
  );
  const { t, action } = useUI();
  const [selected, setSelected] = useState("");
  const [creating, setCreating] = useState(false);
  const figure = figures.find((f) => f.id === selected) || figures[0];
  useEffect(() => {
    const refresh = () => void reloadRuns();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reloadRuns]);
  return (
    <div className="page studio-page">
      <PageHeading
        title={t("图表", "Figures")}
        actions={
          <Button className="primary" onClick={() => setCreating(true)}>
            <Plus size={15} />
            {t("新建图表", "New figure")}
          </Button>
        }
      />
      <ErrorBox error={error} retry={reload} />
      {figures.length ? (
        <>
          <div className="figure-selector">
            {figures.map((f, i) => (
              <button
                key={f.id}
                className={figure?.id === f.id ? "active" : ""}
                onClick={() => setSelected(f.id)}
              >
                <ChartNoAxesCombined size={15} />
                <span>Fig. {i + 1}</span>
                {f.title}
                <Badge status={f.status} />
              </button>
            ))}
          </div>
          {figure && (
            <FigureEditor
              key={figure.id}
              figure={figure}
              projectId={id!}
              runs={runs}
              reload={reload}
            />
          )}
        </>
      ) : (
        <Empty
          icon={<ChartNoAxesCombined size={28} />}
          title={t("暂无图表", "No figures")}
          action={
            <Button onClick={() => setCreating(true)}>
              <Plus size={15} />
              {t("创建图表", "Create figure")}
            </Button>
          }
        />
      )}{" "}
      {creating && (
        <RecordEditor
          resource="figures"
          projectId={id!}
          initial={{
            kind: "bar",
            run_ids: [],
            metric: "accuracy",
            style: {
              title: "",
              xlabel: "Method",
              ylabel: "Accuracy",
              color: "#3f7659",
              width: 7,
              height: 4,
              font_size: 11,
            },
            code: "",
            caption: "",
            outputs: {},
          }}
          onClose={() => setCreating(false)}
          onSaved={reload}
        />
      )}
    </div>
  );
}
function FigureEditor({
  figure,
  projectId,
  runs,
  reload,
}: {
  figure: RecordItem;
  projectId: string;
  runs: Run[];
  reload: () => Promise<void>;
}) {
  const { t, action } = useUI();
  const [style, setStyle] = useState<Json>(figure.data.style || {});
  const [code, setCode] = useState(figure.data.code || "");
  const [kind, setKind] = useState(figure.data.kind || "bar");
  const [metric, setMetric] = useState(figure.data.metric || "");
  const [runIds, setRunIds] = useState<string[]>(figure.data.run_ids || []);
  const [caption, setCaption] = useState(figure.data.caption || "");
  const [dataText, setDataText] = useState(
    JSON.stringify(figure.data.data || {}, null, 2),
  );
  const [purpose, setPurpose] = useState(
    figure.data.purpose || "effectiveness",
  );
  const [imagePrompt, setImagePrompt] = useState(
    figure.data.image_prompt || "",
  );
  const [instruction, setInstruction] = useState("");
  const [tab, setTab] = useState("style");
  const [region, setRegion] = useState<Json | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const [regionRevision, setRegionRevision] = useState(figure.revision);
  const [editRevision, setEditRevision] = useState(figure.revision);
  const outputs = figure.data.outputs || {};
  const imagePath =
    outputs.svg || outputs.png || figure.data.svg_path || figure.data.png_path;
  const save = async () => {
    const r = await api(`/figures/${figure.id}`, "PATCH", {
      expected_revision: editRevision,
      data: {
        ...figure.data,
        kind,
        style,
        code,
        run_ids: runIds,
        metric,
        caption,
        data: parseJson(dataText),
        purpose,
        image_prompt: imagePrompt,
        code_origin:
          code !== (figure.data.code || "")
            ? "custom"
            : figure.data.code_origin,
      },
    });
    setEditRevision(r.revision);
    await reload();
    return r;
  };
  return (
    <div className="figure-workbench">
      <section className="surface figure-canvas">
        <div className="section-toolbar">
          <span className="muted">
            {t("预览", "Preview")} · r{figure.revision}
          </span>
          <div className="inline-actions">
            {Object.entries(outputs)
              .filter(([, v]) => typeof v === "string")
              .map(([format, path]) => (
                <Button
                  key={format}
                  onClick={() =>
                    action(() =>
                      download(
                        `/projects/${projectId}/download?path=${encodeURIComponent(String(path))}`,
                        undefined,
                        `figure.${format}`,
                      ),
                    )
                  }
                >
                  <Download size={13} />
                  {format.toUpperCase()}
                </Button>
              ))}
          </div>
        </div>
        <div
          className="figure-image"
          onPointerDown={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            start.current = {
              x: (e.clientX - rect.left) / rect.width,
              y: (e.clientY - rect.top) / rect.height,
            };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerUp={(e) => {
            if (!start.current) return;
            const rect = e.currentTarget.getBoundingClientRect();
            const end = {
              x: Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
              y: Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)),
            };
            setRegion({
              x: Math.min(start.current.x, end.x),
              y: Math.min(start.current.y, end.y),
              width: Math.abs(end.x - start.current.x),
              height: Math.abs(end.y - start.current.y),
            });
            setRegionRevision(figure.revision);
            start.current = null;
          }}
        >
          {imagePath ? (
            <img
              src={`/api/projects/${projectId}/download?path=${encodeURIComponent(imagePath)}&revision=${figure.revision}`}
              alt={figure.title}
              draggable={false}
            />
          ) : (
            <Empty title={t("暂无预览", "No preview")} />
          )}{" "}
          {region && (
            <div
              className="selection-region"
              style={{
                left: `${region.x * 100}%`,
                top: `${region.y * 100}%`,
                width: `${region.width * 100}%`,
                height: `${region.height * 100}%`,
              }}
            />
          )}
        </div>
        <div className="figure-caption">
          <strong>{figure.title}</strong>
          <p>{caption || t("无图注", "No caption")}</p>
        </div>
        <div className="figure-revise">
          <Field
            label={t(
              "局部修改指令（在预览中拖拽选区）",
              "Revision instruction (drag a region in the preview)",
            )}
          >
            <textarea
              rows={2}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder={t(
                "例如：增大图例字号。",
                "e.g. Increase the legend font size.",
              )}
            />
          </Field>
          <div className="inline-actions">
            {region && (
              <Button onClick={() => setRegion(null)}>
                <X size={13} />
                {t("清除选区", "Clear region")}
              </Button>
            )}
            <Button
              disabled={
                !instruction.trim() ||
                (!!region && regionRevision !== figure.revision)
              }
              onClick={() =>
                action(async () => {
                  await api(`/figures/${figure.id}/revise`, "POST", {
                    instruction,
                    expected_revision: figure.revision,
                    region,
                  });
                  setInstruction("");
                  await reload();
                })
              }
            >
              <Send size={14} />
              {t("应用修改指令", "Apply revision")}
            </Button>
          </div>
        </div>
      </section>
      <aside className="surface figure-properties">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { id: "style", label: t("样式", "Style") },
            { id: "data", label: t("数据", "Data") },
            { id: "code", label: t("代码", "Code") },
          ]}
        />
        {tab === "style" && (
          <>
            <Field label={t("图形类型", "Figure type")}>
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="bar">Bar</option>
                <option value="line">Line</option>
                <option value="calibration">Calibration</option>
                <option value="method">Method diagram</option>
                <option value="image">Conceptual illustration</option>
                <option value="heatmap">Comparison heatmap</option>
                <option value="forest">Effect and interval plot</option>
                <option value="scatter">Paired/scenario analysis</option>
              </select>
            </Field>
            {kind === "image" && (
              <Field
                label={t("论文图片提示词", "Scientific illustration prompt")}
                hint={t(
                  "描述真实机制和组件；实验图表使用真实数据绘图。图片模型在连接设置中配置。",
                  "Describe the actual mechanism and components. Use measured-data plots for results. Configure the image model in connection settings.",
                )}
              >
                <textarea
                  rows={5}
                  value={imagePrompt}
                  onChange={(e) => setImagePrompt(e.target.value)}
                />
              </Field>
            )}
            {[
              "title",
              "xlabel",
              "ylabel",
              "color",
              "width",
              "height",
              "font_size",
            ].map((key) => (
              <Field key={key} label={key}>
                <input
                  type={
                    ["width", "height", "font_size"].includes(key)
                      ? "number"
                      : key === "color"
                        ? "color"
                        : "text"
                  }
                  value={style[key] || ""}
                  onChange={(e) =>
                    setStyle({
                      ...style,
                      [key]: ["width", "height", "font_size"].includes(key)
                        ? Number(e.target.value)
                        : e.target.value,
                    })
                  }
                />
              </Field>
            ))}
            <Field label={t("图注", "Caption")}>
              <textarea
                rows={3}
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
              />
            </Field>
            <Field label={t("论证职责", "Scientific purpose")}>
              <select
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
              >
                <option value="overview">Introduction overview</option>
                <option value="mechanism">Mechanism</option>
                <option value="effectiveness">Effectiveness</option>
                <option value="scenario_value">Scenario value</option>
                <option value="alternative_explanation">
                  Alternative explanation
                </option>
              </select>
            </Field>
          </>
        )}
        {tab === "data" && (
          <>
            <Field label={t("指标", "Metric")}>
              <input
                value={metric}
                onChange={(e) => setMetric(e.target.value)}
              />
            </Field>
            <Field label={t("绑定运行", "Bound runs")}>
              <select
                multiple
                size={10}
                value={runIds}
                onChange={(e) =>
                  setRunIds([...e.target.selectedOptions].map((o) => o.value))
                }
              >
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.kind} · {r.id.slice(0, 8)} · {r.status}
                  </option>
                ))}
              </select>
            </Field>
            <JsonView value={{ run_ids: runIds, metric }} />
            <Field
              label={t(
                "真实数据或方法图结构 JSON",
                "Observed data or method graph JSON",
              )}
              hint={t(
                "方法图使用 nodes 和 edges；留空时由设计 agent 根据研究上下文生成结构。",
                "Use nodes and edges for method diagrams; an empty graph invokes the design agent.",
              )}
            >
              <textarea
                rows={10}
                value={dataText}
                onChange={(e) => setDataText(e.target.value)}
              />
            </Field>
          </>
        )}
        {tab === "code" && (
          <div className="figure-code">
            <CodeEditor value={code} onChange={setCode} language="python" />
          </div>
        )}
        <div className="inline-actions">
          <Button
            onClick={() =>
              action(save, t("图表配置已保存", "Figure configuration saved"))
            }
          >
            <Save size={14} />
            {t("保存", "Save")}
          </Button>
          <Button
            className="primary"
            onClick={() =>
              action(
                async () => {
                  await save();
                  await api(`/figures/${figure.id}/render`, "POST", {
                    request_id: uid(),
                  });
                  await reload();
                },
                t("绘图任务已排队", "Figure render queued"),
              )
            }
          >
            <Play size={13} />
            {t("生成图表", "Render figure")}
          </Button>
        </div>
        {figure.data.visual_review_status && (
          <p className="muted">
            {t("独立图片评审", "Independent visual review")}:{" "}
            {figure.data.visual_review_status}
          </p>
        )}
        {figure.data.visual_selection && (
          <details>
            <summary>
              {t("候选图片与评选理由", "Candidates and selection rationale")}
            </summary>
            <JsonView value={figure.data.visual_selection} />
          </details>
        )}
      </aside>
    </div>
  );
}
export function PaperPage({ embedded = false }: { embedded?: boolean }) {
  const { id } = useParams();
  const { t, action } = useUI();
  const {
    data: paper,
    error,
    reload,
  } = useLoad<RecordItem>(`/papers/${id}`, null!);
  const [source, setSource] = useState("");
  const [bibtex, setBibtex] = useState("");
  const [tab, setTab] = useState("source");
  const [rightTab, setRightTab] = useState("pdf");
  const [issues, setIssues] = useState<Json | null>(null);
  const [instruction, setInstruction] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revising, setRevising] = useState(false);
  const revisingRef = useRef(false);
  const [editingRevision, setEditingRevision] = useState<number | undefined>();
  const [template, setTemplate] = useState<PaperTemplate>("article");
  const [layout, setLayout] = useState<PaperLayoutConfig>(
    paperLayoutDefaults(),
  );
  const [layoutDirty, setLayoutDirty] = useState(false);
  const [layoutRun, setLayoutRun] = useState<string | null>(null);
  useEffect(() => {
    if (paper && !dirty) {
      setSource(paper.data.source || "");
      setBibtex(paper.data.bibtex || "");
      setEditingRevision(paper.revision);
    }
  }, [paper, dirty]);
  useEffect(() => {
    if (!paper) return;
    const currentLayout = paperLayoutDefaults(paper.data);
    const currentTemplate = paperTemplate(paper.data);
    if (!layoutDirty) {
      if (template !== currentTemplate) setTemplate(currentTemplate);
      if (JSON.stringify(layout) !== JSON.stringify(currentLayout))
        setLayout(currentLayout);
    } else if (
      currentTemplate === template &&
      JSON.stringify(currentLayout) === JSON.stringify(layout)
    ) {
      setLayoutDirty(false);
    }
  }, [paper, layoutDirty, template, layout]);
  useEffect(() => {
    const f = () => void reload();
    window.addEventListener("forest-refresh", f);
    return () => window.removeEventListener("forest-refresh", f);
  }, [reload]);
  const save = async () => {
    const saved = await api(`/papers/${id}`, "PATCH", {
      expected_revision: editingRevision,
      data: { source, bibtex },
    });
    setEditingRevision(saved.revision);
    setDirty(false);
    await reload();
    return saved.revision as number;
  };
  const compile = async () => {
    setBusy(true);
    await action(
      async () => {
        if (dirty) await save();
        await api(`/papers/${id}/compile`, "POST", { request_id: uid() });
        await reload();
      },
      t("论文编译已加入队列", "Paper compilation queued"),
    );
    setBusy(false);
  };
  const applyLayout = async () => {
    setBusy(true);
    try {
      await action(async () => {
        const revision = dirty ? await save() : editingRevision;
        const run = await api<Run>(`/papers/${id}/layout`, "POST", {
          request_id: uid(),
          expected_revision: revision,
          template,
          layout,
        });
        setLayoutRun(run.id);
        setRightTab("checks");
        await reload();
      }, "Layout compilation queued");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={embedded ? "paper-page embedded" : "page paper-page"}>
      {!embedded && <PageHeading title={t("论文", "Paper")} />}
      <ErrorBox error={error} retry={reload} />
      <div className="paper-actions">
        <span className="paper-filename">
          <FileText size={16} />
          manuscript.tex{" "}
          <span>
            {dirty
              ? t("有未保存修改", "Unsaved changes")
              : `r${paper?.revision || 0}`}
          </span>
        </span>
        <div className="toolbar-spacer" />
        <Button
          disabled={busy || !paper}
          onClick={() =>
            action(async () => {
              if (dirty) await save();
              setIssues(await api(`/papers/${id}/check`, "POST", {}));
              setRightTab("checks");
            })
          }
        >
          <Check size={14} />
          {t("检查引用与主张", "Check evidence")}
        </Button>
        <Button
          disabled={busy || !paper}
          onClick={() => action(save, t("论文已保存", "Paper saved"))}
        >
          <Save size={14} />
          {t("保存", "Save")}
        </Button>
        <Button
          className="primary"
          busy={busy}
          disabled={!paper}
          onClick={compile}
        >
          <Play size={13} />
          {t("编译 PDF", "Compile PDF")}
        </Button>
        <Button
          onClick={() =>
            action(() =>
              download(
                `/papers/${id}/export`,
                { request_id: uid() },
                "paper-source.zip",
              ),
            )
          }
        >
          <Download size={14} />
          {t("导出", "Export")}
        </Button>
      </div>
      {paper && id && <DependencyImpact projectId={id} data={paper.data} />}
      <PaperLayoutSettings
        template={template}
        layout={layout}
        onTemplateChange={(value) => {
          setTemplate(value);
          if (value === "iclr2027")
            setLayout((current) => ({ ...current, columns: "single" }));
          setLayoutDirty(true);
        }}
        onChange={(value) => {
          setLayout(value);
          setLayoutDirty(true);
        }}
        onApply={applyLayout}
        busy={busy}
        disabled={!paper}
      />
      {paper && (
        <PaperGeneration
          projectId={id!}
          template={template}
          layout={layout}
          dirty={dirty}
          save={save}
          reload={reload}
        />
      )}
      {paper && (
        <PaperFigureInsertion
          projectId={id!}
          revision={editingRevision}
          source={source}
          dirty={dirty}
          save={save}
          reload={reload}
        />
      )}
      <div className="paper-editors">
        <div className="paper-source">
          <Tabs
            value={tab}
            onChange={setTab}
            items={[
              { id: "source", label: "LaTeX" },
              { id: "bibtex", label: "references.bib" },
              { id: "bindings", label: t("图表与指标绑定", "Bindings") },
            ]}
          />
          {tab === "source" ? (
            <CodeEditor
              value={source}
              language="latex"
              onChange={(v) => {
                setSource(v);
                setDirty(true);
              }}
            />
          ) : tab === "bibtex" ? (
            <CodeEditor
              value={bibtex}
              language="bibtex"
              onChange={(v) => {
                setBibtex(v);
                setDirty(true);
              }}
            />
          ) : (
            <JsonView value={paper?.data.bindings || []} />
          )}
        </div>
        <div className="paper-preview">
          <Tabs
            value={rightTab}
            onChange={setRightTab}
            items={[
              { id: "pdf", label: t("论文预览", "PDF preview") },
              { id: "log", label: t("编译日志", "Compile log") },
              { id: "checks", label: t("检查结果", "Checks") },
            ]}
          />
          {rightTab === "pdf" &&
            (paper?.data.pdf_path ? (
              <PDFViewer
                url={`/api/projects/${id}/download?path=${encodeURIComponent(paper.data.pdf_path)}&revision=${paper.revision}`}
              />
            ) : (
              <Empty
                icon={<FileText size={28} />}
                title={t("暂无 PDF", "No PDF")}
              />
            ))}
          {rightTab === "log" && (
            <pre className="compile-log">
              {paper?.data.log || t("尚无编译日志。", "No compilation log")}
            </pre>
          )}
          {rightTab === "checks" && (
            <div className="paper-checks">
              {layoutRun && (
                <p className="muted">
                  Requested layout run: {layoutRun.slice(0, 8)}
                </p>
              )}
              <PaperPreflight
                report={paper?.data.layout_preflight}
                plan={paper?.data.layout_plan}
              />
              {issues ? (
                <details open>
                  <summary>Evidence checks</summary>
                  <JsonView value={issues} />
                </details>
              ) : null}
            </div>
          )}
        </div>
      </div>
      <div className="paper-instruction">
        <input
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder={t("描述需要修改的内容…", "Describe the revision…")}
        />
        <Button
          busy={revising}
          disabled={!instruction.trim() || busy || revising || !paper}
          onClick={() => {
            if (revisingRef.current) return;
            revisingRef.current = true;
            setRevising(true);
            void action(
              async () => {
                try {
                  const revision = dirty ? await save() : editingRevision;
                  await api(`/papers/${id}/revise`, "POST", {
                    instruction,
                    expected_revision: revision,
                  });
                  setInstruction("");
                  await reload();
                } finally {
                  revisingRef.current = false;
                  setRevising(false);
                }
              },
              t("论文修订任务已排队", "Paper revision queued"),
            );
          }}
        >
          <Send size={14} />
          {t("修订", "Revise")}
        </Button>
      </div>
      <PaperRevisions
        projectId={id!}
        revision={paper?.revision}
        reload={reload}
      />
    </div>
  );
}
function PaperGeneration({
  projectId,
  template,
  layout,
  dirty,
  save,
  reload,
}: {
  projectId: string;
  template: PaperTemplate;
  layout: PaperLayoutConfig;
  dirty: boolean;
  save: () => Promise<number>;
  reload: () => Promise<void>;
}) {
  const { t, action } = useUI();
  const { data: runs } = useLoad<Run[]>(
    `/projects/${projectId}/runs?include_manuscript_evidence=true`,
    [],
  );
  const { data: figures } = useLoad<RecordItem[]>(
    `/figures?project_id=${projectId}`,
    [],
  );
  const [runIds, setRunIds] = useState<string[]>([]);
  const [figureIds, setFigureIds] = useState<string[]>([]);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const completed = runs.filter(
    (run) =>
      run.status === "completed" &&
      ["experiment", "command", "agent"].includes(run.kind) &&
      run.manuscript_evidence?.ready === true,
  );
  const reviewed = figures.filter(
    (figure) =>
      figure.status === "ready_for_review" &&
      (figure.data.outputs?.pdf || figure.data.outputs?.png),
  );
  const toggle = (values: string[], id: string) =>
    values.includes(id)
      ? values.filter((value) => value !== id)
      : [...values, id];
  return (
    <details className="paper-layout">
      <summary>
        {t(
          "从真实实验生成完整论文",
          "Generate a full manuscript from actual experiments",
        )}
      </summary>
      <p className="muted">
        {t(
          "默认完整投稿篇幅；图片由独立 agent 评选和定位，编译后检查实际位置。实验或文献缺项会保留待补工作。",
          "Uses the full submission profile. Independent agents select and place visuals; compiled locations are checked. Evidence gaps remain explicit work to complete.",
        )}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          void action(
            async () => {
              if (dirty) await save();
              await api(`/papers/${projectId}/generate`, "POST", {
                request_id: uid(),
                run_ids: runIds,
                figure_ids: figureIds,
                manuscript_type: "full_paper",
                template,
                layout,
                ...(instructions.trim() ? { instructions } : {}),
              });
              await reload();
            },
            t("完整论文生成任务已排队", "Full manuscript generation queued"),
          ).finally(() => setBusy(false));
        }}
      >
        <Field
          label={t(
            "已完成的实验与测量",
            "Completed experiments and measurements",
          )}
        >
          {completed.map((run) => (
            <label key={run.id}>
              <input
                type="checkbox"
                checked={runIds.includes(run.id)}
                onChange={() => setRunIds((current) => toggle(current, run.id))}
              />
              {run.kind} · {run.id.slice(0, 8)}
            </label>
          ))}
          {!completed.length && (
            <p>
              {t(
                "先完成能生成可读数值指标的实验。",
                "Complete an experiment that produces readable numeric metrics.",
              )}
            </p>
          )}
        </Field>
        <Field label={t("必须插入的已评审图片", "Reviewed figures to include")}>
          {reviewed.map((figure) => (
            <label key={figure.id}>
              <input
                type="checkbox"
                checked={figureIds.includes(figure.id)}
                onChange={() =>
                  setFigureIds((current) => toggle(current, figure.id))
                }
              />
              {figure.title}
            </label>
          ))}
        </Field>
        <Field
          label={t(
            "研究问题与写作要求",
            "Research question and writing requirements",
          )}
        >
          <textarea
            rows={3}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
          />
        </Field>
        <Button
          className="primary"
          busy={busy}
          disabled={busy || !runIds.length}
        >
          {t("生成完整论文", "Generate full manuscript")}
        </Button>
      </form>
    </details>
  );
}
function PaperFigureInsertion({
  projectId,
  revision,
  source,
  dirty,
  save,
  reload,
}: {
  projectId: string;
  revision?: number;
  source: string;
  dirty: boolean;
  save: () => Promise<number>;
  reload: () => Promise<void>;
}) {
  const { t, action } = useUI();
  const { data: figures } = useLoad<RecordItem[]>(
    `/figures?project_id=${projectId}`,
    [],
  );
  const [figureId, setFigureId] = useState("");
  const [anchor, setAnchor] = useState("");
  const ready = figures.filter(
    (figure) =>
      figure.status === "ready_for_review" &&
      (figure.data.outputs?.pdf || figure.data.outputs?.png),
  );
  return (
    <details className="paper-layout">
      <summary>
        {t(
          "在文章中插入已评审图片",
          "Insert a reviewed figure in the manuscript",
        )}
      </summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void action(
            async () => {
              const currentRevision = dirty ? await save() : revision;
              await api(`/papers/${projectId}/figures`, "POST", {
                expected_revision: currentRevision,
                figure_id: figureId,
                anchor_text: anchor,
              });
              setAnchor("");
              await reload();
            },
            t(
              "图片已插入，请编译查看位置",
              "Figure inserted; compile to inspect placement",
            ),
          );
        }}
      >
        <Field label={t("选择图片", "Figure")}>
          <select
            required
            value={figureId}
            onChange={(event) => setFigureId(event.target.value)}
          >
            <option value="">
              {t(
                "选择已生成和评审的图片",
                "Select a rendered and reviewed figure",
              )}
            </option>
            {ready.map((figure) => (
              <option key={figure.id} value={figure.id}>
                {figure.title}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={t("图片前的文章段落", "Preceding manuscript paragraph")}
          hint={t(
            "粘贴当前正文中唯一的一段文字；图片和交叉引用将紧接其后。",
            "Paste a unique paragraph from the current source; the figure and reference follow it.",
          )}
        >
          <textarea
            required
            rows={3}
            value={anchor}
            onChange={(event) => setAnchor(event.target.value)}
          />
        </Field>
        <Button
          className="primary"
          disabled={!figureId || !anchor.trim() || !source.includes(anchor)}
        >
          {t("插入图片", "Insert figure")}
        </Button>
      </form>
    </details>
  );
}
export function FilesPage() {
  const { id } = useParams();
  const { t, action } = useUI();
  const { data, error, reload } = useLoad<{ files: Json[] }>(
    `/projects/${id}/files`,
    { files: [] },
  );
  const [path, setPath] = useState("");
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [revision, setRevision] = useState<number | undefined>();
  const [filter, setFilter] = useState("");
  const [newFile, setNewFile] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [diff, setDiff] = useState(false);
  const [tablePreview, setTablePreview] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [conflict, setConflict] = useState<
    | { kind: "changed"; content: string; revision: number }
    | { kind: "missing"; path: string }
    | null
  >(null);
  const extension = path.split(".").pop()?.toLowerCase();
  const image = ["png", "jpg", "jpeg", "svg", "webp", "gif"].includes(
    extension || "",
  );
  const binary = ["parquet", "zip", "pkl", "npy", "npz"].includes(
    extension || "",
  );
  const url = `/api/projects/${id}/download?path=${encodeURIComponent(path)}`;
  const open = async (p: string) => {
    if (dirty && !confirm(t("放弃未保存修改？", "Discard unsaved changes?")))
      return;
    setPath(p);
    setDiff(false);
    setTablePreview(false);
    setDirty(false);
    if (/\.(csv|tsv)$/i.test(p)) {
      setTablePreview(true);
      setContent("");
      setOriginal("");
      setRevision(undefined);
      return;
    }
    if (!/\.(pdf|png|jpe?g|svg|webp|gif|parquet|zip|pkl|npy|npz)$/i.test(p)) {
      const r = await api(`/projects/${id}/file?path=${encodeURIComponent(p)}`);
      setContent(r.content);
      setOriginal(r.content);
      setRevision(r.revision);
    }
  };
  const save = async (force = false) => {
    try {
      const r = await api(
        `/projects/${id}/file?path=${encodeURIComponent(path)}`,
        "PUT",
        { path, content, ...(force ? {} : { expected_revision: revision }) },
      );
      setRevision(r.revision);
      setOriginal(content);
      setDirty(false);
      setConflict(null);
      await reload();
    } catch (e) {
      if ((e as any).status === 409) {
        try {
          const latest = await api(
            `/projects/${id}/file?path=${encodeURIComponent(path)}`,
          );
          setConflict({
            kind: "changed",
            content: latest.content,
            revision: latest.revision,
          });
        } catch (readError) {
          if ((readError as any).status !== 404) throw readError;
          setConflict({ kind: "missing", path });
        }
        return;
      }
      throw e;
    }
  };
  const upload = async (files: FileList | File[]) => {
    const selected = [...files].map((file) => {
      const relativePath = file.webkitRelativePath || file.name;
      const parts = relativePath.split("/");
      const safeParts = parts.every((part) => part && part !== "." && part !== "..");
      const path = `uploads/${safeParts ? relativePath : file.name}`;
      return {
        file,
        path,
        directory: path.split("/").slice(0, -1).join("/"),
      };
    });
    const existing = new Set(
      data.files.filter((item) => !item.is_dir).map((item) => item.path),
    );
    const seen = new Set<string>();
    const conflicts = new Set<string>();
    for (const item of selected) {
      if (existing.has(item.path) || seen.has(item.path)) conflicts.add(item.path);
      seen.add(item.path);
    }
    if (conflicts.size) {
      const paths = [...conflicts];
      const listed = paths.slice(0, 5).join(", ");
      const more = paths.length > 5 ? ` (+${paths.length - 5})` : "";
      if (
        !confirm(
          t(
            `这些目标路径已存在或在所选文件中重复：${listed}${more}。继续将覆盖目标内容；同一目标选了多个文件时保留最后一个。继续吗？`,
            `These upload destinations already exist or repeat in this selection: ${listed}${more}. Continuing replaces their contents; if multiple selected files share a destination, the last one is kept. Continue?`,
          ),
        )
      )
        return;
    }
    for (const item of selected) {
      const body = new FormData();
      body.append("file", item.file);
      const overwrite = conflicts.has(item.path);
      await api(
        `/projects/${id}/upload?directory=${encodeURIComponent(item.directory)}&overwrite=${overwrite}`,
        "POST",
        body,
      );
    }
    await reload();
  };
  return (
    <div
      className="page files-page"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        void action(() => upload(e.dataTransfer.files));
      }}
    >
      <PageHeading
        title={t("文件", "Files")}
        actions={
          <>
            <label className="button upload-button">
              <Upload size={15} />
              {t("上传文件", "Upload")}
              <input
                type="file"
                multiple
                onChange={(e) => {
                  if (e.target.files)
                    void action(() => upload(e.target.files!));
                }}
              />
            </label>
            <Button className="primary" onClick={() => setNewFile(true)}>
              <Plus size={15} />
              {t("新建文件", "New file")}
            </Button>
          </>
        }
      />
      <SourceExplorer key={id} projectId={id!} />
      <ErrorBox error={error} retry={reload} />
      <div className="file-workbench">
        <aside className="file-tree">
          <label className="search-input">
            <Search size={14} />
            <input
              placeholder={t("查找文件…", "Find files…")}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
          <div>
            {data.files
              .filter((f) =>
                f.path.toLowerCase().includes(filter.toLowerCase()),
              )
              .map((f) => (
                <button
                  key={f.path}
                  className={path === f.path ? "active" : ""}
                  onClick={() => {
                    if (!f.is_dir) void action(() => open(f.path));
                  }}
                  title={f.path}
                >
                  {f.is_dir ? <Folder size={15} /> : <File size={14} />}
                  <span>{f.path}</span>
                  <small>
                    {f.is_dir
                      ? ""
                      : f.size < 1024
                        ? `${f.size} B`
                        : `${(f.size / 1024).toFixed(1)} K`}
                  </small>
                </button>
              ))}
          </div>
          {!data.files.length && <Empty title={t("暂无文件", "No files")} />}
        </aside>
        <section className="file-editor">
          {path ? (
            <>
              <div className="file-toolbar">
                <span>
                  <FileText size={15} />
                  {path}
                  {dirty ? " ●" : ""}
                </span>
                <div className="toolbar-spacer" />
                {["csv", "tsv"].includes(extension || "") && (
                  <IconButton
                    label={t(
                      "切换已保存数据表预览",
                      "Toggle saved table preview",
                    )}
                    onClick={() => {
                      if (tablePreview) {
                        if (dirty) {
                          setTablePreview(false);
                        } else {
                          void action(async () => {
                            const result = await api(
                              `/projects/${id}/file?path=${encodeURIComponent(path)}`,
                            );
                            setContent(result.content);
                            setOriginal(result.content);
                            setRevision(result.revision);
                            setTablePreview(false);
                          });
                        }
                      } else setTablePreview(true);
                    }}
                  >
                    <Table2 size={15} />
                  </IconButton>
                )}
                {!image && !binary && !tablePreview && extension !== "pdf" && (
                  <>
                    <IconButton
                      label={t("比较编辑差异", "Compare changes")}
                      onClick={() => setDiff(!diff)}
                    >
                      <GitCompare size={15} />
                    </IconButton>
                    <Button onClick={() => action(() => save())}>
                      <Save size={14} />
                      {t("保存", "Save")}
                    </Button>
                  </>
                )}
                <IconButton
                  label={t("下载", "Download")}
                  onClick={() =>
                    action(() =>
                      download(
                        `/projects/${id}/download?path=${encodeURIComponent(path)}`,
                        undefined,
                        path.split("/").pop(),
                      ),
                    )
                  }
                >
                  <Download size={15} />
                </IconButton>
                <IconButton
                  label={t("重命名", "Rename")}
                  onClick={() => {
                    const next = prompt(t("新文件路径", "New file path"), path);
                    if (next && next !== path)
                      void action(async () => {
                        const renamed = await api(`/projects/${id}/file/rename`, "POST", {
                          path,
                          new_path: next,
                        });
                        setPath(next);
                        setRevision(renamed.revision);
                        await reload();
                      });
                  }}
                >
                  <Pencil size={15} />
                </IconButton>
                <IconButton
                  label={t("删除文件", "Delete file")}
                  onClick={() => {
                    if (confirm(t(`删除 ${path}？`, `Delete ${path}?`)))
                      void action(async () => {
                        await api(
                          `/projects/${id}/file?path=${encodeURIComponent(path)}`,
                          "DELETE",
                        );
                        setPath("");
                        await reload();
                      });
                  }}
                >
                  <Trash2 size={15} />
                </IconButton>
              </div>
              {extension === "pdf" ? (
                <PDFViewer url={url} />
              ) : image ? (
                <div className="file-image">
                  <img src={url} alt={path} />
                </div>
              ) : extension === "parquet" ||
                (["csv", "tsv"].includes(extension || "") && tablePreview) ? (
                <FileTablePreview
                  key={`${path}-${revision}`}
                  projectId={id!}
                  path={path}
                />
              ) : binary ? (
                <Empty
                  title={t("无法预览", "Preview unavailable")}
                  action={
                    <a className="button" href={url} download>
                      <Download size={15} />
                      {t("下载文件", "Download file")}
                    </a>
                  }
                />
              ) : diff ? (
                <div className="diff-columns">
                  <div>
                    <span>{t("保存的内容", "Saved content")}</span>
                    <CodeEditor
                      value={original}
                      readOnly
                      language={extension}
                    />
                  </div>
                  <div>
                    <span>{t("当前编辑", "Current edit")}</span>
                    <CodeEditor
                      value={content}
                      onChange={(v) => {
                        setContent(v);
                        setDirty(true);
                      }}
                      language={extension}
                    />
                  </div>
                </div>
              ) : (
                <CodeEditor
                  value={content}
                  onChange={(v) => {
                    setContent(v);
                    setDirty(true);
                  }}
                  language={
                    extension === "py"
                      ? "python"
                      : extension === "tex"
                        ? "latex"
                        : extension === "md"
                          ? "markdown"
                          : extension === "ts"
                            ? "typescript"
                            : extension
                  }
                />
              )}
            </>
          ) : (
            <Empty
              icon={<Folder size={30} />}
              title={t("未选择文件", "No file selected")}
            />
          )}
        </section>
      </div>
      {newFile && (
        <Modal
          title={t("新建文件", "Create file")}
          onClose={() => setNewFile(false)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action(async () => {
                await api(
                  `/projects/${id}/file?path=${encodeURIComponent(newPath)}`,
                  "PUT",
                  { path: newPath, content: "" },
                );
                await reload();
                await open(newPath);
                setNewFile(false);
                setNewPath("");
              });
            }}
          >
            <Field label={t("项目内路径", "Path within project")}>
              <input
                required
                autoFocus
                value={newPath}
                onChange={(e) => setNewPath(e.target.value)}
                placeholder="experiments/method.py"
              />
            </Field>
            <div className="modal-actions">
              <Button className="primary" type="submit">
                {t("创建", "Create")}
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {conflict && (
        <Modal
          wide
          title={
            conflict.kind === "missing"
              ? t("文件已被删除或改名", "File deleted or renamed")
              : t("文件已被另一处修改", "File changed elsewhere")
          }
          onClose={() => setConflict(null)}
        >
          <p>
            {conflict.kind === "missing"
              ? t(
                  "此路径已不存在。你的草稿仍保留在编辑器中，可以另存到新路径。",
                  "This path no longer exists. Your draft remains in the editor and can be saved to a new path.",
                )
              : t(
                  "比较服务端内容，再选择覆盖或另存；你的当前编辑保留在编辑器中。",
                  "Compare the server content, then overwrite or save separately. Your current edit remains in the editor.",
                )}
          </p>
          {conflict.kind === "changed" && (
            <pre className="json-view">{conflict.content}</pre>
          )}
          <div className="modal-actions">
            {conflict.kind === "changed" && (
              <>
                <Button
                  onClick={() => {
                    setOriginal(conflict.content);
                    setRevision(conflict.revision);
                    setConflict(null);
                    setDiff(true);
                  }}
                >
                  {t("在差异视图中合并", "Merge in comparison view")}
                </Button>
                <Button onClick={() => action(() => save(true))}>
                  {t("用当前内容覆盖", "Overwrite with current content")}
                </Button>
              </>
            )}
            <Button
              className="primary"
              onClick={() => {
                const next = prompt(
                  t("另存路径", "Save as path"),
                  `${path}.copy`,
                );
                if (next)
                  void action(async () => {
                    const saved = await api(
                      `/projects/${id}/file?path=${encodeURIComponent(next)}`,
                      "PUT",
                      { path: next, content },
                    );
                    setConflict(null);
                    setPath(next);
                    setRevision(saved.revision);
                    setOriginal(content);
                    setDirty(false);
                    await reload();
                  });
              }}
            >
              {t("另存为", "Save as")}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
export function SettingsPage() {
  const { t, action } = useUI();
  const {
    data: providers,
    reload: reloadProviders,
    error,
  } = useLoad<Json[]>("/providers", []);
  const { data: hosts, reload: reloadHosts } = useLoad<Json[]>("/hosts", []);
  const { data: agents, reload: reloadAgents } = useLoad<Json[]>("/agents", []);
  const { data: system, reload: reloadSystem } = useLoad<Json>("/system", {});
  const { data: settings, reload: reloadSettings } = useLoad<Json>(
    "/settings",
    {},
  );
  const [tab, setTab] = useState("providers");
  const [modal, setModal] = useState<{ resource: string; item?: Json } | null>(
    null,
  );
  const [test, setTest] = useState<Json | null>(null);
  const [settingsText, setSettingsText] = useState("{}");
  useEffect(
    () => setSettingsText(JSON.stringify(settings, null, 2)),
    [settings],
  );
  const reload = () => {
    void reloadProviders();
    void reloadHosts();
    void reloadAgents();
    void reloadSystem();
  };
  return (
    <>
      <header className="topbar">
        <div className="topbar-project">
          <Settings size={16} />
          {t("设置与连接", "Settings & connections")}
        </div>
        <span className="local-label">FOREST / LOCAL</span>
      </header>
      <div className="page settings-page">
        <PageHeading title={t("设置", "Settings")} />
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { id: "providers", label: t("模型 Provider", "Model providers") },
            { id: "hosts", label: t("计算主机", "Compute hosts") },
            { id: "agents", label: t("Agent 角色", "Agent roles") },
            { id: "defaults", label: t("预算与默认参数", "Budget & defaults") },
            { id: "system", label: t("系统资源", "System resources") },
          ]}
        />
        <ErrorBox error={error} retry={reload} />
        {["providers", "hosts", "agents"].includes(tab) && (
          <>
            <div className="section-toolbar">
              <h2>
                {tab === "providers"
                  ? t("模型连接", "Model connections")
                  : tab === "hosts"
                    ? t("计算主机", "Compute hosts")
                    : t("研究角色", "Research agents")}
              </h2>
              <Button
                className="primary"
                onClick={() => setModal({ resource: tab })}
              >
                <Plus size={15} />
                {t("添加", "Add")}
              </Button>
            </div>
            <div className="settings-cards">
              {(tab === "providers"
                ? providers
                : tab === "hosts"
                  ? hosts
                  : agents
              ).map((item) => (
                <section className="surface connection-card" key={item.id}>
                  <div className="connection-card-icon">
                    {tab === "providers" ? (
                      <PlugZap size={23} />
                    ) : tab === "hosts" ? (
                      <Server size={23} />
                    ) : (
                      <Lightbulb size={23} />
                    )}
                  </div>
                  <div>
                    <h3>{item.name || item.role}</h3>
                    <p>
                      {item.model ||
                        item.hostname ||
                        item.description ||
                        item.kind}
                    </p>
                    <code>{item.base_url || item.address || item.role}</code>
                  </div>
                  <Badge status={item.status || "idle"} />
                  {tab === "providers" && item.kind !== "ollama" && (
                    <ProviderUsage providerId={item.id} />
                  )}
                  <IconButton
                    label={t("编辑连接", "Edit connection")}
                    onClick={() => setModal({ resource: tab, item })}
                  >
                    <Pencil size={15} />
                  </IconButton>
                  {tab !== "agents" && (
                    <Button
                      onClick={() =>
                        action(async () => {
                          setTest(
                            await api(`/${tab}/${item.id}/test`, "POST", {}),
                          );
                          reload();
                        })
                      }
                    >
                      <PlugZap size={14} />
                      {t("测试连接", "Test connection")}
                    </Button>
                  )}
                  {tab === "providers" && (
                    <Button
                      onClick={() =>
                        action(
                          () =>
                            api("/settings", "PATCH", {
                              default_provider_id: item.id,
                            }),
                          "Default model updated",
                        )
                      }
                    >
                      Use for new projects
                    </Button>
                  )}
                </section>
              ))}
            </div>
            {!(
              tab === "providers" ? providers : tab === "hosts" ? hosts : agents
            ).length && (
              <Empty
                icon={
                  tab === "providers" ? (
                    <PlugZap size={28} />
                  ) : (
                    <Server size={28} />
                  )
                }
                title={
                  tab === "providers"
                    ? t("暂无模型提供方", "No model providers")
                    : tab === "hosts"
                      ? t("暂无计算主机", "No compute hosts")
                      : t("暂无 Agent 角色", "No agent roles")
                }
                action={
                  <Button onClick={() => setModal({ resource: tab })}>
                    <Plus size={15} />
                    {t("添加配置", "Add configuration")}
                  </Button>
                }
              />
            )}
          </>
        )}
        {tab === "defaults" && (
          <section className="surface settings-defaults">
            <h3>{t("全局运行设置", "Global execution settings")}</h3>
            <p className="muted">
              {t(
                "项目设置会覆盖这些默认值。",
                "Project settings override these defaults.",
              )}
            </p>
            <textarea
              className="code-input"
              rows={20}
              value={settingsText}
              onChange={(e) => setSettingsText(e.target.value)}
            />
            <Button
              className="primary"
              onClick={() =>
                action(
                  async () => {
                    await api("/settings", "PATCH", parseJson(settingsText));
                    await reloadSettings();
                  },
                  t("设置已保存", "Settings saved"),
                )
              }
            >
              <Save size={15} />
              {t("保存设置", "Save settings")}
            </Button>
            <StorageCleanup />
          </section>
        )}
        {tab === "system" && (
          <section className="surface">
            <div className="section-toolbar">
              <h3>{t("实际运行环境", "Runtime environment")}</h3>
              <Button onClick={reloadSystem}>
                <RefreshCw size={14} />
                {t("刷新检测", "Refresh")}
              </Button>
            </div>
            <JsonView value={system} />
          </section>
        )}
      </div>
      {modal && (
        <ConnectionEditor
          resource={modal.resource}
          item={modal.item}
          onClose={() => setModal(null)}
          onSaved={reload}
        />
      )}{" "}
      {test && (
        <Modal
          title={t("实际连接测试结果", "Connection test result")}
          onClose={() => setTest(null)}
        >
          <JsonView value={test} />
        </Modal>
      )}
    </>
  );
}
function ConnectionEditor({
  resource,
  item,
  onClose,
  onSaved,
}: {
  resource: string;
  item?: Json;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t, action } = useUI();
  const [name, setName] = useState(item?.name || "");
  const [kind, setKind] = useState(
    item?.kind || (resource === "providers" ? "ollama" : "local"),
  );
  const [url, setUrl] = useState(item?.base_url || "http://127.0.0.1:11434");
  const [model, setModel] = useState(item?.model || "");
  const [key, setKey] = useState("");
  const [paid, setPaid] = useState(item?.allow_paid || false);
  const [apiMode, setApiMode] = useState(item?.config?.api || "responses");
  const [limitUsd, setLimitUsd] = useState(
    String(item?.config?.budget_usd ?? ""),
  );
  const [inputRate, setInputRate] = useState(
    String(item?.config?.pricing?.input_per_million ?? ""),
  );
  const [cachedRate, setCachedRate] = useState(
    String(item?.config?.pricing?.cached_input_per_million ?? ""),
  );
  const [outputRate, setOutputRate] = useState(
    String(item?.config?.pricing?.output_per_million ?? ""),
  );
  const [imageModel, setImageModel] = useState(
    item?.config?.image_generation?.model || "",
  );
  const [imageCeiling, setImageCeiling] = useState(
    String(item?.config?.image_generation?.max_request_usd ?? ""),
  );
  const [config, setConfig] = useState(
    JSON.stringify(
      item && resource === "providers"
        ? item.config || {}
        : item
          ? Object.fromEntries(
              Object.entries(item).filter(
                ([k]) =>
                  !["id", "name", "created_at", "updated_at"].includes(k),
              ),
            )
          : resource === "hosts"
            ? {
                kind: "local",
                hostname: "localhost",
                workspace: "",
                allow_shell: true,
              }
            : resource === "agents"
              ? {
                  role: "researcher",
                  model: "",
                  instructions: "",
                  tools: [],
                  budget: {},
                }
              : {},
      null,
      2,
    ),
  );
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={t("连接与角色配置", "Connection & role configuration")}
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          const body =
            resource === "providers"
              ? {
                  name,
                  kind,
                  base_url: url,
                  model,
                  allow_paid: paid,
                  ...(key ? { api_key: key } : {}),
                  config: {
                    ...parseJson(config),
                    ...(kind !== "ollama" && kind !== "codex_cli"
                      ? {
                          api: apiMode,
                          budget_usd: limitUsd.trim() ? Number(limitUsd) : null,
                          ...(imageModel.trim()
                            ? {
                                image_generation: {
                                  ...(parseJson(config).image_generation || {}),
                                  model: imageModel.trim(),
                                  max_request_usd: Number(imageCeiling),
                                },
                              }
                            : {}),
                          pricing: {
                            input_per_million: inputRate.trim()
                              ? Number(inputRate)
                              : null,
                            cached_input_per_million: cachedRate.trim()
                              ? Number(cachedRate)
                              : inputRate.trim()
                                ? Number(inputRate)
                                : null,
                            output_per_million: outputRate.trim()
                              ? Number(outputRate)
                              : null,
                            currency: "USD",
                          },
                        }
                      : {}),
                  },
                }
              : { name, ...parseJson(config) };
          const r = await action(() =>
            api(
              item ? `/${resource}/${item.id}` : `/${resource}`,
              item ? "PATCH" : "POST",
              body,
            ),
          );
          setBusy(false);
          if (r) {
            onSaved();
            onClose();
          }
        }}
      >
        <Field label={t("名称", "Name")}>
          <input
            required
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        {resource === "providers" && (
          <>
            <Field label="Provider">
              <select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  if (e.target.value === "codex_cli") {
                    setModel("gpt-6-luna");
                    setConfig(
                      JSON.stringify(
                        { reasoning_effort: "xhigh", timeout: 120 },
                        null,
                        2,
                      ),
                    );
                    setPaid(false);
                  }
                  setUrl(
                    e.target.value === "codex_cli"
                      ? "http://127.0.0.1"
                      : e.target.value === "ollama"
                        ? "http://127.0.0.1:11434"
                        : "https://api.openai.com/v1",
                  );
                }}
              >
                <option value="ollama">Ollama (local)</option>
                <option value="codex_cli">Codex CLI (local login)</option>
                <option value="openai">OpenAI-compatible API</option>
              </select>
            </Field>
            {kind === "codex_cli" && (
              <p>
                Uses the server account’s existing Codex CLI login. FOREST owns
                tool execution; the inference subprocess has shell, plugins and
                browser tools disabled. Subscription USD cost and a hard token
                ceiling are unavailable; use a reporting request cap.
              </p>
            )}
            <Field label="Base URL">
              <input
                required
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </Field>
            {kind !== "ollama" && kind !== "codex_cli" && (
              <>
                <Field label="API format">
                  <select
                    value={apiMode}
                    onChange={(e) => setApiMode(e.target.value)}
                  >
                    <option value="responses">OpenAI Responses</option>
                    <option value="chat_completions">
                      Chat Completions compatible
                    </option>
                  </select>
                </Field>
                <Field label="Shared API spending limit (USD)">
                  <input
                    type="number"
                    min="0"
                    step="any"
                    required={paid}
                    value={limitUsd}
                    onChange={(e) => setLimitUsd(e.target.value)}
                  />
                </Field>
                <div className="form-row">
                  <Field label="Input price / 1M tokens (USD)">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      required={paid}
                      value={inputRate}
                      onChange={(e) => setInputRate(e.target.value)}
                    />
                  </Field>
                  <Field label="Output price / 1M tokens (USD)">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      required={paid}
                      value={outputRate}
                      onChange={(e) => setOutputRate(e.target.value)}
                    />
                  </Field>
                </div>
                <Field label="Cached input price / 1M tokens (USD, optional)">
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={cachedRate}
                    onChange={(e) => setCachedRate(e.target.value)}
                  />
                </Field>
                <Field
                  label={t("图片模型 ID（可选）", "Image model ID (optional)")}
                >
                  <input
                    value={imageModel}
                    onChange={(e) => setImageModel(e.target.value)}
                  />
                </Field>
                {imageModel.trim() && (
                  <Field
                    label={t(
                      "每次图片请求费用上界（美元）",
                      "Per image request cost ceiling (USD)",
                    )}
                    hint={t(
                      "图片请求与文字请求共用总限额；无法核算费用时保留预留金额。",
                      "Image and text requests share the total limit; unknown image charges retain their reservation.",
                    )}
                  >
                    <input
                      required
                      type="number"
                      min="0.001"
                      step="any"
                      value={imageCeiling}
                      onChange={(e) => setImageCeiling(e.target.value)}
                    />
                  </Field>
                )}
              </>
            )}
            <Field label={t("实际模型 ID", "Model ID")}>
              <input
                required
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={t(
                  "由 Provider 提供的模型名称",
                  "Model ID provided by your provider",
                )}
              />
            </Field>
            <Field label="API key">
              <input
                type="password"
                autoComplete="new-password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder={
                  item
                    ? t("留空保留现有密钥", "Leave blank to keep existing key")
                    : ""
                }
              />
            </Field>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={paid}
                onChange={(e) => setPaid(e.target.checked)}
              />
              {t("允许使用此已配置的付费 API", "Allow paid API calls")}
            </label>
          </>
        )}
        <Field label={t("配置", "Configuration")}>
          <textarea
            className="code-input"
            rows={resource === "providers" ? 3 : 12}
            value={config}
            onChange={(e) => setConfig(e.target.value)}
          />
        </Field>
        <div className="modal-actions">
          <Button type="button" onClick={onClose}>
            {t("取消", "Cancel")}
          </Button>
          <Button className="primary" type="submit" busy={busy}>
            <Save size={14} />
            {t("保存配置", "Save configuration")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function compactMetrics(metrics: Json) {
  return (
    Object.entries(metrics)
      .filter(
        ([key, value]) =>
          typeof value === "number" ||
          typeof value === "boolean" ||
          (typeof value === "string" && key === "evidence_label"),
      )
      .slice(0, 5)
      .map(
        ([key, value]) =>
          `${key}: ${typeof value === "number" ? Number(value.toPrecision(5)) : value}`,
      )
      .join(" · ") || Object.keys(metrics).join(", ")
  );
}
function MeasuredResults({ runs }: { runs: Run[] }) {
  const { t } = useUI();
  const available = runs.filter(
    (r) => Array.isArray(r.metrics?.summary) && r.metrics.summary.length,
  );
  const [runId, setRunId] = useState("");
  const [dataset, setDataset] = useState("all");
  const [metric, setMetric] = useState("brier");
  const [mode, setMode] = useState("summary");
  const run = available.find((r) => r.id === runId) || available[0];
  if (!run) return null;
  const rows: Json[] =
    mode === "summary" ? run.metrics.summary : run.metrics.per_seed || [];
  const datasets = [...new Set(rows.map((r) => r.dataset))];
  const visible = rows.filter(
    (r) => dataset === "all" || r.dataset === dataset,
  );
  const metricOptions = ["brier", "log_loss", "auc", "accuracy", "ece"].filter(
    (k) => rows.some((r) => typeof r[k] === "number"),
  );
  const max = Math.max(...visible.map((r) => Number(r[metric] || 0)), 0.001);
  return (
    <section className="surface measured-results">
      <div className="section-toolbar">
        <div>
          <span className="muted">MEASURED</span>
          <h2>{t("方法与数据集比较", "Methods across datasets")}</h2>
        </div>
        <div className="toolbar-actions">
          <select
            aria-label="Measured run"
            value={run.id}
            onChange={(e) => setRunId(e.target.value)}
          >
            {available.map((r) => (
              <option key={r.id} value={r.id}>
                {r.kind} · {r.id.slice(0, 8)}
              </option>
            ))}
          </select>
          <select
            aria-label="Dataset"
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
          >
            <option value="all">{t("全部数据集", "All datasets")}</option>
            {datasets.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
          <select
            aria-label="Measured metric"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          >
            {metricOptions.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </div>
      </div>
      <Tabs
        value={mode}
        onChange={setMode}
        items={[
          {
            id: "summary",
            label: t("均值与标准差", "Mean & standard deviation"),
          },
          { id: "seeds", label: t("每次重复", "Per repetition") },
        ]}
      />
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t("数据集", "Dataset")}</th>
              <th>{t("方法", "Method")}</th>
              <th>
                {mode === "summary" ? t("重复数", "Repetitions") : "Seed"}
              </th>
              <th>{metric}</th>
              <th>{t("数值比较", "Value comparison")}</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr key={`${row.dataset}-${row.method}-${index}`}>
                <td>{row.dataset}</td>
                <td>
                  <strong>{row.method}</strong>
                </td>
                <td>{mode === "summary" ? row.seeds : row.seed}</td>
                <td className="numeric">
                  {typeof row[metric] === "number"
                    ? row[metric].toFixed(5)
                    : "—"}
                  {mode === "summary" &&
                    typeof row[`${metric}_std`] === "number" && (
                      <small>± {row[`${metric}_std`].toFixed(5)}</small>
                    )}
                </td>
                <td>
                  <div className="value-track">
                    <div
                      style={{
                        width: `${(Number(row[metric] || 0) / max) * 100}%`,
                      }}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="statistical-scope">
        {run.metrics.statistical_scope ||
          t(
            "保存的重复实验结果：均值 ± 标准差。",
            "Mean ± SD across saved repetitions.",
          )}
      </p>
    </section>
  );
}
function ProposalCommands({
  record,
  onApplied,
}: {
  record: RecordItem;
  onApplied: () => Promise<void>;
}) {
  const { t, action } = useUI();
  const [selected, setSelected] = useState<number[]>([]);
  const [commands, setCommands] = useState<string[]>(
    record.data.commands.map((c: Json) => JSON.stringify(c, null, 2)),
  );
  const [edit, setEdit] = useState<number | null>(null);
  const [reviewedRevision, setReviewedRevision] = useState<number | null>(null);
  const [rejectionReason, setRejectionReason] = useState("");
  useEffect(() => {
    let active = true;
    api(`/projects/${record.project_id}/graph`).then((graph) => {
      if (active) setReviewedRevision(graph.revision);
    });
    return () => {
      active = false;
    };
  }, [record.id, record.project_id]);
  return (
    <div className="proposal-commands">
      <strong>{t("待采用的路线建议", "Unapplied suggestions")}</strong>
      {commands.map((text, index) => {
        let command: Json;
        try {
          command = JSON.parse(text);
        } catch {
          command = { operation: "Invalid JSON" };
        }
        return (
          <div className="proposal-command" key={index}>
            <input
              type="checkbox"
              aria-label={`Select command ${index + 1}`}
              checked={selected.includes(index)}
              disabled={(record.data.accepted_indices || []).includes(index)}
              onChange={(e) =>
                setSelected((s) =>
                  e.target.checked
                    ? [...s, index]
                    : s.filter((i) => i !== index),
                )
              }
            />
            <div>
              <code>{command.operation}</code>
              <p>
                {command.params?.title ||
                  command.params?.name ||
                  command.targets?.join(", ")}
              </p>
              {edit === index && (
                <textarea
                  rows={8}
                  className="code-input"
                  value={text}
                  onChange={(e) =>
                    setCommands((s) =>
                      s.map((x, i) => (i === index ? e.target.value : x)),
                    )
                  }
                />
              )}
            </div>
            <IconButton
              label={t("修改建议", "Edit suggestion")}
              onClick={() => setEdit(edit === index ? null : index)}
            >
              <Pencil size={13} />
            </IconButton>
          </div>
        );
      })}
      <Button
        className="primary"
        disabled={!selected.length}
        onClick={() =>
          action(
            async () => {
              await api(`/research/proposals/${record.id}/apply`, "POST", {
                request_id: uid(),
                expected_revision: reviewedRevision,
                indices: selected,
                commands: commands.map(parseJson),
              });
              setSelected([]);
              await onApplied();
            },
            t("已采用选中路线变更", "Selected path changes applied"),
          )
        }
      >
        <Check size={14} />
        {t("采用选中建议", "Apply selected suggestions")} ({selected.length})
      </Button>
      <Field label={t("拒绝理由", "Proposal rejection reason")}>
        <textarea
          value={rejectionReason}
          onChange={(event) => setRejectionReason(event.target.value)}
        />
      </Field>
      <Button
        disabled={!rejectionReason.trim() || reviewedRevision === null}
        onClick={() =>
          action(async () => {
            await api(`/research/proposals/${record.id}/reject`, "POST", {
              expected_revision: reviewedRevision,
              reason: rejectionReason,
            });
            await onApplied();
          })
        }
      >
        {t("拒绝建议", "Reject proposal")}
      </Button>
    </div>
  );
}
function PaperRevisions({
  projectId,
  revision,
  reload,
}: {
  projectId: string;
  revision?: number;
  reload: () => Promise<void>;
}) {
  const { t } = useUI();
  const { data: reviews, reload: reloadReviews } = useLoad<RecordItem[]>(
    `/reviews?project_id=${projectId}`,
    [],
  );
  useEffect(() => {
    const refresh = () => void reloadReviews();
    window.addEventListener("forest-refresh", refresh);
    return () => window.removeEventListener("forest-refresh", refresh);
  }, [reloadReviews]);
  const proposals = reviews.filter(
    (r) =>
      Array.isArray(r.data.edits) &&
      r.data.edits.length &&
      r.status !== "applied",
  );
  if (!proposals.length) return null;
  return (
    <details className="paper-revision-list" open>
      <summary>
        {t("逐处检查论文修订建议", "Suggested revisions")} ({proposals.length})
      </summary>
      {proposals.map((r) => (
        <RevisionProposal
          key={r.id}
          record={r}
          revision={revision}
          onApplied={async () => {
            await reload();
            await reloadReviews();
          }}
        />
      ))}
    </details>
  );
}
function RevisionProposal({
  record,
  revision,
  onApplied,
}: {
  record: RecordItem;
  revision?: number;
  onApplied: () => Promise<void>;
}) {
  const { t, action } = useUI();
  const [selected, setSelected] = useState<number[]>([]);
  const edits: Json[] = record.data.edits;
  return (
    <section className="revision-proposal">
      <h3>{record.title}</h3>
      {edits.map((edit, index) => (
        <div key={index}>
          <label>
            <input
              type="checkbox"
              checked={selected.includes(index)}
              onChange={(e) =>
                setSelected((s) =>
                  e.target.checked
                    ? [...s, index]
                    : s.filter((i) => i !== index),
                )
              }
            />
            {edit.reason || edit.issue || t("措辞修订", "Wording revision")}
          </label>
          <p className="revision-original">{edit.original}</p>
          <p className="revision-replacement">{edit.replacement}</p>
        </div>
      ))}
      <div className="inline-actions">
        <Badge status={record.status} />
        <span className="muted">
          {t("来源论文版本", "Source manuscript revision")}:{" "}
          {record.data.paper_revision}
        </span>
        <Button
          className="primary"
          disabled={!selected.length || record.data.paper_revision !== revision}
          onClick={() =>
            action(async () => {
              await api(`/reviews/${record.id}/apply`, "POST", {
                expected_revision: revision,
                indices: selected,
              });
              await onApplied();
            })
          }
        >
          <Check size={14} />
          {t("应用选中的最小修改", "Apply selected edits")}
        </Button>
      </div>
    </section>
  );
}

function StorageCleanup() {
  const { t, action } = useUI();
  const [days, setDays] = useState(30);
  const [history, setHistory] = useState(false);
  const [projectId, setProjectId] = useState("");
  const [result, setResult] = useState<Json | null>(null);
  const { data: projects } = useLoad<Json[]>("/projects", []);
  return (
    <details className="storage-cleanup">
      <summary>{t("存储与历史清理", "Storage & history cleanup")}</summary>
      <p className="muted">
        {t(
          "删除选定保留期之前的已完成运行与对应输出。引用这些材料的位置会显示需要更新。",
          "Delete completed runs and outputs older than the retention period. References to removed material will require an update.",
        )}
      </p>
      <div className="form-row">
        <Field label={t("项目范围", "Project scope")}>
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
          >
            <option value="">{t("全部项目", "All projects")}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("保留最近天数", "Days to retain")}>
          <input
            type="number"
            min="0"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </Field>
      </div>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={history}
          onChange={(e) => setHistory(e.target.checked)}
        />
        {t(
          "同时清理所有项目的撤销历史",
          "Also remove undo history from all projects",
        )}
      </label>
      <Button
        onClick={() => {
          if (
            confirm(
              t(
                `删除 ${days} 天前的已完成运行输出${history ? "，并清空所有项目撤销历史" : ""}？`,
                `Delete completed run outputs older than ${days} days${history ? " and all project undo history" : ""}?`,
              ),
            )
          )
            void action(async () =>
              setResult(
                await api("/settings/cleanup", "POST", {
                  days,
                  clear_edit_history: history,
                  ...(projectId ? { project_id: projectId } : {}),
                }),
              ),
            );
        }}
      >
        <Trash2 size={14} />
        {t("执行清理", "Clean up")}
      </Button>
      {result && <JsonView value={result} />}
    </details>
  );
}
function RawDataBrowser({ runs }: { runs: Run[] }) {
  const { t, action } = useUI();
  const available = runs.filter(
    (r) => r.kind === "experiment" && r.status === "completed",
  );
  const [runId, setRunId] = useState("");
  const [path, setPath] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("");
  const [descending, setDescending] = useState(false);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Json | null>(null);
  const [busy, setBusy] = useState(false);
  const selected = runId || available[0]?.id;
  const load = async (next = 0) => {
    if (!selected) return;
    setBusy(true);
    await action(async () => {
      const query = new URLSearchParams({
        offset: String(next),
        limit: "50",
        filter,
        sort,
        descending: String(descending),
      });
      if (path.trim()) query.set("path", path.trim());
      setData(await api(`/data/${selected}/rows?${query}`));
      setOffset(next);
    });
    setBusy(false);
  };
  if (!available.length) return null;
  return (
    <details className="surface raw-data-browser">
      <summary>
        <Table2 size={16} />
        {t("查看原始预测与数据表", "Raw predictions & tables")}
      </summary>
      <div className="raw-data-controls">
        <Field label={t("来源运行", "Source run")}>
          <select
            value={selected}
            onChange={(e) => {
              setRunId(e.target.value);
              setData(null);
            }}
          >
            {available.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id.slice(0, 8)} · {r.kind}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={t(
            "文件路径（留空读取预测）",
            "File path (blank for predictions)",
          )}
        >
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="predictions.csv / dataset.parquet"
          />
        </Field>
        <Field label={t("过滤表格内容", "Filter table values")}>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} />
        </Field>
        <Button className="primary" busy={busy} onClick={() => load(0)}>
          <Table2 size={14} />
          {t("读取实际数据", "Load data")}
        </Button>
      </div>
      {data && (
        <>
          <div className="section-toolbar">
            <span className="muted">
              {data.total.toLocaleString()} {t("行", "rows")} · {data.origin}
            </span>
            <div className="inline-actions">
              <select
                aria-label="Sort raw table"
                value={sort}
                onChange={(e) => setSort(e.target.value)}
              >
                <option value="">{t("原始顺序", "Original order")}</option>
                {data.columns.map((column: string) => (
                  <option key={column}>{column}</option>
                ))}
              </select>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={descending}
                  onChange={(e) => setDescending(e.target.checked)}
                />
                {t("降序", "Descending")}
              </label>
              <Button onClick={() => load(0)}>
                {t("应用排序", "Apply sort")}
              </Button>
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {data.columns.map((column: string) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row: Json, index: number) => (
                  <tr key={index}>
                    {data.columns.map((column: string) => (
                      <td key={column}>
                        {row[column] === null ? (
                          <span className="missing">null</span>
                        ) : typeof row[column] === "object" ? (
                          JSON.stringify(row[column])
                        ) : (
                          String(row[column] ?? "")
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="raw-pagination">
            <Button
              disabled={offset === 0 || busy}
              onClick={() => load(Math.max(0, offset - 50))}
            >
              {t("上一页", "Previous")}
            </Button>
            <span>
              {offset + 1}–{Math.min(offset + 50, data.total)} / {data.total}
            </span>
            <Button
              disabled={offset + 50 >= data.total || busy}
              onClick={() => load(offset + 50)}
            >
              {t("下一页", "Next")}
            </Button>
            <Button
              onClick={() =>
                action(() =>
                  download(
                    `/projects/${runs.find((r) => r.id === selected)?.project_id}/download?path=${encodeURIComponent(path || `${runs.find((r) => r.id === selected)?.output_path}/predictions.csv`)}`,
                  ),
                )
              }
            >
              <Download size={14} />
              {t("下载原始数据", "Download source")}
            </Button>
          </div>
        </>
      )}
    </details>
  );
}

function FileTablePreview({
  projectId,
  path,
}: {
  projectId: string;
  path: string;
}) {
  const { t } = useUI();
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useLoad<Json>(
    `/projects/${projectId}/file/preview?path=${encodeURIComponent(path)}&offset=${offset}&limit=50`,
    null!,
  );
  return (
    <div className="file-table-preview">
      <div className="file-table-heading">
        <span>{t("已保存文件中的实际数据", "Saved file preview")}</span>
        <Button onClick={reload}>
          <RefreshCw size={13} />
          {t("重新读取", "Reload")}
        </Button>
      </div>
      <ErrorBox error={error} retry={reload} />
      {loading ? (
        <Loading />
      ) : (
        data && (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    {data.columns.map((column: string) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row: Json, index: number) => (
                    <tr key={index}>
                      {data.columns.map((column: string) => (
                        <td key={column}>
                          {row[column] === null ? (
                            <span className="missing">null</span>
                          ) : typeof row[column] === "object" ? (
                            JSON.stringify(row[column])
                          ) : (
                            String(row[column] ?? "")
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="raw-pagination">
              <Button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 50))}
              >
                {t("上一页", "Previous")}
              </Button>
              <span>
                {Math.min(offset + 1, data.total)}–
                {Math.min(offset + 50, data.total)} / {data.total}
              </span>
              <Button
                disabled={offset + 50 >= data.total}
                onClick={() => setOffset(offset + 50)}
              >
                {t("下一页", "Next")}
              </Button>
            </div>
          </>
        )
      )}
    </div>
  );
}
