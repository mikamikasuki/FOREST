import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api, formatDate, uid } from "../api";
import type { Graph } from "../api";
import type { components } from "../../../../packages/contracts/api.generated";
import { Badge, Button, ErrorBox, Field, Modal, useLoad, useUI } from "../ui";

type Intervention = components["schemas"]["InterventionView"];
type Decision = components["schemas"]["DecisionView"];
type Instruction = components["schemas"]["InstructionRequest"];

export function InterventionPanel({ projectId }: { projectId: string }) {
  const { t, action } = useUI();
  const receipts = useLoad<Intervention[]>(
    `/projects/${projectId}/interventions?limit=20`,
    [],
  );
  const decisions = useLoad<Decision[]>(
    `/projects/${projectId}/decisions?limit=50`,
    [],
  );
  const [draft, setDraft] = useState<Instruction | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [review, setReview] = useState<Decision | null>(null);
  const [choice, setChoice] = useState<"accept" | "edit" | "reject">("accept");
  const [payload, setPayload] = useState("");
  const [reason, setReason] = useState("");
  const [resumeError, setResumeError] = useState("");
  useEffect(() => {
    const refresh = () => {
      void receipts.reload();
      void decisions.reload();
    };
    const timer = setInterval(refresh, 2000);
    window.addEventListener("forest-refresh", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener("forest-refresh", refresh);
    };
  }, [receipts.reload, decisions.reload]);
  return (
    <section
      className="surface progress-panel"
      aria-label="Human instructions and decisions"
    >
      <div className="section-toolbar">
        <h2>{t("人工指令与生效记录", "Human instructions and effects")}</h2>
        <Button
          onClick={() =>
            action(async () => {
              const current = await api<Graph>(`/projects/${projectId}/graph`);
              setGraph(current);
              setDraft({
                request_id: uid(),
                expected_revision: current.revision,
                text: "",
                scope: "project",
                target_id: null,
                boundary: "next_request",
              });
            })
          }
        >
          {t("作为指令发送", "Send as instruction")}
        </Button>
      </div>
      <p className="muted">
        {t(
          "指令在所选边界采用；已保存不代表模型已消费。普通评论保留为批注。",
          "Instructions apply at the selected boundary. Saved does not mean consumed. Ordinary comments remain annotations.",
        )}
      </p>
      <ErrorBox error={receipts.error || decisions.error || resumeError} />
      {decisions.data
        .filter(
          (item) =>
            item.status === "pending" ||
            (!item.consumed_at &&
              ["accepted", "rejected"].includes(item.status)),
        )
        .map((item) => (
          <div className="progress-fact" key={item.id}>
            <strong>
              {item.status === "pending"
                ? t("等待人工决定", "Waiting for human decision")
                : item.can_resume
                  ? t(
                      "决定已保存，等待继续",
                      "Decision saved; awaiting continuation",
                    )
                  : t(
                      `决定已保存；运行状态：${item.run_status || "未知"}`,
                      `Decision saved; run status: ${item.run_status || "unknown"}`,
                    )}
            </strong>
            <NavLink to={`/projects/${projectId}/runs?run=${item.run_id}`}>
              {item.run_id.slice(0, 8)}
            </NavLink>
            <span>
              {" "}
              · {String(item.proposed.tool)} · {formatDate(item.created_at)}
            </span>
            {item.status === "pending" ? (
              <Button
                onClick={() => {
                  setReview(item);
                  setPayload(JSON.stringify(item.proposed, null, 2));
                  setChoice("accept");
                  setReason("");
                }}
              >
                {t("审阅动作", "Review action")}
              </Button>
            ) : item.can_resume ? (
              <Button
                onClick={() =>
                  action(async () => {
                    await api(`/runs/${item.run_id}/resume`, "POST", {});
                    setResumeError("");
                    await decisions.reload();
                  })
                }
              >
                {t("继续已保存的决定", "Continue saved decision")}
              </Button>
            ) : null}
          </div>
        ))}
      {!receipts.data.length && (
        <p>{t("还没有正式干预记录。", "No formal intervention recorded.")}</p>
      )}
      <ul>
        {receipts.data.map((row) => (
          <li className="progress-fact" key={row.id}>
            <div>
              <strong>
                {row.kind === "instruction"
                  ? t("正式指令", "Owner instruction")
                  : row.kind === "configuration"
                    ? t("项目控制", "Project controls")
                    : row.kind === "run_control"
                      ? t("运行控制", "Run control")
                      : row.kind === "goal_applicability"
                        ? t("证据用途决定", "Evidence-use decision")
                        : t("图修改", "Graph edit")}
              </strong>{" "}
              <Badge status={row.status} /> · {formatDate(row.accepted_at)}
            </div>
            {row.kind === "instruction" && (
              <p>{String(row.intent.text || "")}</p>
            )}
            <small>
              {row.status === "accepted"
                ? t(
                    "已保存，等待对应请求或运行效果确认。",
                    "Saved; waiting for request delivery or execution confirmation.",
                  )
                : row.applied_at
                  ? `${t("已确认生效", "Confirmed applied")} · ${formatDate(row.applied_at)}`
                  : row.status}
            </small>
            {row.effects.map((effect) => (
              <div key={effect.id}>
                <NavLink
                  to={
                    effect.action === "materialize_workspace"
                      ? `/projects/${projectId}/workspace?branch=${effect.run_id}`
                      : `/projects/${projectId}/runs?run=${effect.run_id}`
                  }
                >
                  {effect.run_id.slice(0, 8)}
                </NavLink>{" "}
                · {effect.action} · {effect.status}
                {effect.error && <p role="status">{effect.error}</p>}
              </div>
            ))}
          </li>
        ))}
      </ul>
      {draft && graph && (
        <Modal
          title={t("发送正式指令", "Send owner instruction")}
          onClose={() => setDraft(null)}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void action(async () => {
                await api(`/projects/${projectId}/instructions`, "POST", draft);
                setDraft(null);
                await receipts.reload();
              });
            }}
          >
            <Field label={t("指令", "Instruction")}>
              <textarea
                aria-label="Instruction"
                required
                value={draft.text}
                onChange={(event) =>
                  setDraft({ ...draft, text: event.target.value })
                }
              />
            </Field>
            <Field label={t("作用范围", "Instruction scope")}>
              <select
                aria-label="Instruction scope"
                value={draft.scope}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    scope: event.target.value as Instruction["scope"],
                    target_id: null,
                  })
                }
              >
                <option value="project">{t("整个项目", "Project")}</option>
                <option value="branch">{t("指定分支", "Branch")}</option>
                <option value="node">{t("指定任务", "Node")}</option>
                <option value="run">{t("指定运行", "Run")}</option>
              </select>
            </Field>
            {draft.scope === "branch" || draft.scope === "node" ? (
              <Field label={t("对象", "Instruction target")}>
                <select
                  aria-label="Instruction target"
                  required
                  value={draft.target_id || ""}
                  onChange={(event) =>
                    setDraft({ ...draft, target_id: event.target.value })
                  }
                >
                  <option value="">{t("选择对象", "Choose target")}</option>
                  {(draft.scope === "branch"
                    ? graph.branches.map((b) => ({ id: b.id, title: b.name }))
                    : graph.nodes
                  ).map((n) => (
                    <option key={n.id} value={n.id}>
                      {n.title}
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              draft.scope === "run" && (
                <Field label={t("运行 ID", "Run ID")}>
                  <input
                    required
                    value={draft.target_id || ""}
                    onChange={(event) =>
                      setDraft({ ...draft, target_id: event.target.value })
                    }
                  />
                </Field>
              )
            )}
            <Field label={t("何时采用", "Instruction boundary")}>
              <select
                aria-label="Instruction boundary"
                value={draft.boundary}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    boundary: event.target.value as Instruction["boundary"],
                  })
                }
              >
                <option value="next_request">
                  {t("下一模型请求", "Next model request")}
                </option>
                <option value="next_run">
                  {t("之后新建的运行", "New runs created afterward")}
                </option>
              </select>
            </Field>
            <p>
              {t(
                "已发出的请求和实验保留原条件。若审阅期间项目改变，需要重新审阅。",
                "Requests and experiments already sent retain their original conditions. Project changes during review require another review.",
              )}
            </p>
            <Button type="submit" className="primary">
              {t("发送指令", "Send instruction")}
            </Button>
          </form>
        </Modal>
      )}
      {review && (
        <Modal
          wide
          title={t("审阅待执行动作", "Review pending action")}
          onClose={() => setReview(null)}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void action(async () => {
                const answered = await api<Decision>(
                  `/decisions/${review.id}/answer`,
                  "POST",
                  {
                    expected_revision: review.observed_revision,
                    choice,
                    reason,
                    action: choice === "edit" ? JSON.parse(payload) : null,
                    resume: true,
                  },
                );
                setResumeError(answered.resume_error || "");
                setReview(null);
                await decisions.reload();
              });
            }}
          >
            <pre>{JSON.stringify(review.proposed, null, 2)}</pre>
            <Field label={t("决定", "Action decision")}>
              <select
                value={choice}
                onChange={(event) =>
                  setChoice(event.target.value as typeof choice)
                }
              >
                <option value="accept">
                  {t("执行这一动作", "Execute this action")}
                </option>
                <option value="edit">
                  {t("修改后执行", "Edit and execute")}
                </option>
                <option value="reject">
                  {t("拒绝这一动作", "Reject this action")}
                </option>
              </select>
            </Field>
            {choice === "edit" && (
              <Field label={t("修改后的动作", "Edited action")}>
                <textarea
                  value={payload}
                  onChange={(event) => setPayload(event.target.value)}
                />
              </Field>
            )}
            <Field label={t("意见或理由", "Decision reason")}>
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
            <p>
              {t(
                "决定绑定这一个动作和审阅时的项目状态，跨重启保留。恢复仍检查剩余预算。",
                "The decision binds this action and reviewed project state and survives restart. Resume still checks remaining budget.",
              )}
            </p>
            <Button type="submit" className="primary">
              {t("保存决定并继续", "Save decision and continue")}
            </Button>
          </form>
        </Modal>
      )}
    </section>
  );
}
