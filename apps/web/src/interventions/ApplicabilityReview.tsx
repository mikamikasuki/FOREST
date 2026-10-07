import { useState } from "react";
import { api, uid } from "../api";
import type { Graph } from "../api";
import type { components } from "../../../../packages/contracts/api.generated";
import { Badge, Button, ErrorBox, Field, Modal, useLoad, useUI } from "../ui";

type Applicability = components["schemas"]["ApplicabilityView"];

export function ApplicabilityReview({
  runId,
  completed,
}: {
  runId: string;
  completed: boolean;
}) {
  const { t, action } = useUI();
  const value = useLoad<Applicability>(
    `/runs/${runId}/applicability`,
    null as unknown as Applicability,
  );
  const [revision, setRevision] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [choice, setChoice] = useState<"reuse" | "exclude">("reuse");
  const [requestId, setRequestId] = useState("");
  return (
    <section className="run-evidence-attempts">
      <strong>{t("当前目标适用性", "Applicability to current goal")}</strong>
      <ErrorBox error={value.error} />
      {value.data && (
        <>
          <Badge status={value.data.status} />
          <p>{value.data.reason}</p>
          <p className="muted">
            {t(
              "适用性与计算验证独立；目标改变不会删除原始计算或验证回执。",
              "Applicability and computational verification are separate. Goal changes preserve original computations and verification receipts.",
            )}
          </p>
          {completed && (
            <Button
              onClick={() =>
                action(async () => {
                  const lineage = await api<{ project_id: string }>(
                    `/runs/${runId}/lineage`,
                  );
                  const graph = await api<Graph>(
                    `/projects/${lineage.project_id}/graph`,
                  );
                  setRevision(graph.revision);
                  setRequestId(uid());
                  setReason("");
                  setChoice("reuse");
                })
              }
            >
              {t("审阅证据用途", "Review evidence use")}
            </Button>
          )}
        </>
      )}
      {revision !== null && (
        <Modal
          title={t(
            "审阅证据与当前目标",
            "Review evidence against current goal",
          )}
          onClose={() => setRevision(null)}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void action(async () => {
                await api(`/runs/${runId}/applicability/decisions`, "POST", {
                  request_id: requestId,
                  expected_revision: revision,
                  choice,
                  reason,
                });
                setRevision(null);
                await value.reload();
              });
            }}
          >
            <Field label={t("当前目标", "Current goal")}>
              <p>{value.data?.current_goal}</p>
            </Field>
            <Field
              label={t("执行时采用的目标", "Goals recorded during execution")}
            >
              <pre>{JSON.stringify(value.data?.execution_goals, null, 2)}</pre>
            </Field>
            <Field label={t("证据用途", "Evidence use")}>
              <select
                value={choice}
                onChange={(event) =>
                  setChoice(event.target.value as typeof choice)
                }
              >
                <option value="reuse">
                  {t("允许用于当前目标", "Allow use for current goal")}
                </option>
                <option value="exclude">
                  {t(
                    "保留历史，排除当前用途",
                    "Preserve history and exclude current use",
                  )}
                </option>
              </select>
            </Field>
            <Field label={t("科学理由", "Scientific reason")}>
              <textarea
                required
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
            <Button type="submit" className="primary" disabled={!reason.trim()}>
              {t("保存证据用途决定", "Save evidence-use decision")}
            </Button>
          </form>
        </Modal>
      )}
    </section>
  );
}
