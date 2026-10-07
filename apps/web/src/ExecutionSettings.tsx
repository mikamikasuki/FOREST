import type { Json } from "./api";
import { Button, Field } from "./ui";

const DEFAULT = "__forest_execution_default__";

/** Edits execution fields without replacing other task or container settings. */
export function ExecutionSettings({
  config,
  onChange,
}: {
  config: Json;
  onChange: (config: Json) => void;
}) {
  const backend =
    config.execution_backend === undefined
      ? DEFAULT
      : String(config.execution_backend);
  const container =
    config.container &&
    typeof config.container === "object" &&
    !Array.isArray(config.container)
      ? config.container
      : {};
  const network =
    container.network === undefined ? DEFAULT : String(container.network);
  const invalidContainer =
    config.container != null &&
    (typeof config.container !== "object" || Array.isArray(config.container));
  const setBackend = (value: string) => {
    const next = { ...config };
    if (value === DEFAULT) delete next.execution_backend;
    else next.execution_backend = value;
    onChange(next);
  };
  const setContainer = (key: string, value: string | number | undefined) => {
    const next = { ...container };
    if (value === undefined) delete next[key];
    else next[key] = value;
    onChange({ ...config, container: next });
  };

  return (
    <div className="execution-settings">
      <Field label="Evidence use">
        <select
          value={config.acceptance_contract?.purpose ?? DEFAULT}
          onChange={(event) => {
            const next = { ...config };
            if (event.target.value === DEFAULT) delete next.acceptance_contract;
            else
              next.acceptance_contract = {
                ...(config.acceptance_contract || {}),
                purpose: event.target.value,
              };
            onChange(next);
          }}
        >
          <option value={DEFAULT}>Task verification policy</option>
          <option value="exploratory">Exploratory notes</option>
          <option value="raw_data">Raw data handoff</option>
          <option value="comparison">Measured comparison</option>
          <option value="major_claim">Major claim with confirmation</option>
        </select>
      </Field>
      {config.acceptance_contract?.purpose === "raw_data" && (
        <Field
          label="Required data artifacts"
          hint="Producer workspace paths, one per line. Configure independent data_contract checks for their schema, units and sample split."
        >
          <textarea
            value={(config.acceptance_contract.artifact_paths || []).join("\n")}
            onChange={(event) =>
              onChange({
                ...config,
                acceptance_contract: {
                  ...config.acceptance_contract,
                  artifact_paths: event.target.value
                    .split("\n")
                    .map((path) => path.trim())
                    .filter(Boolean),
                },
              })
            }
          />
        </Field>
      )}
      {config.acceptance_contract?.purpose === "major_claim" && (
        <Field
          label="Confirmation runs"
          hint="Distinct completed confirmation-phase run IDs, one per line. Their numerical evidence must be independently checked."
        >
          <textarea
            value={(config.acceptance_contract.confirmation_run_ids || []).join(
              "\n",
            )}
            onChange={(event) =>
              onChange({
                ...config,
                acceptance_contract: {
                  ...config.acceptance_contract,
                  confirmation_run_ids: event.target.value
                    .split("\n")
                    .map((id) => id.trim())
                    .filter(Boolean),
                },
              })
            }
          />
        </Field>
      )}
      <Field label="Human action review">
        <select
          value={
            config.human_review_tools === undefined
              ? DEFAULT
              : Array.isArray(config.human_review_tools) &&
                  config.human_review_tools.includes("*")
                ? "all"
                : Array.isArray(config.human_review_tools) &&
                    !config.human_review_tools.length
                  ? "none"
                  : "selected"
          }
          onChange={(event) => {
            const next = { ...config };
            if (event.target.value === DEFAULT) delete next.human_review_tools;
            else
              next.human_review_tools =
                event.target.value === "all" ? ["*"] : [];
            onChange(next);
          }}
        >
          <option value={DEFAULT}>Inherit project review policy</option>
          <option value="all">Review every agent action</option>
          <option value="none">
            Execute enabled tools without action review
          </option>
          {Array.isArray(config.human_review_tools) &&
            config.human_review_tools.length > 0 &&
            !config.human_review_tools.includes("*") && (
              <option value="selected">
                Selected tools (editable in configuration)
              </option>
            )}
        </select>
      </Field>
      <Field label="Execution backend">
        <select value={backend} onChange={(e) => setBackend(e.target.value)}>
          <option value={DEFAULT}>Local (default)</option>
          <option value="local">Local</option>
          <option value="container">Container</option>
          {![DEFAULT, "local", "container"].includes(backend) && (
            <option value={backend}>
              {backend || "Empty value"} (unsupported)
            </option>
          )}
        </select>
      </Field>
      <p className="muted">
        Local runs trusted commands on this machine; container execution
        requires Docker.
      </p>
      {backend === "container" && invalidContainer && (
        <div className="instruction-note">
          <p>Container settings must be a JSON object.</p>
          <Button
            type="button"
            onClick={() => onChange({ ...config, container: {} })}
          >
            Reset container settings
          </Button>
        </div>
      )}
      {backend === "container" && !invalidContainer && (
        <>
          <Field label="Container image" hint="Default: forest-task:local">
            <input
              value={container.image ?? ""}
              placeholder="forest-task:local"
              onChange={(e) =>
                setContainer("image", e.target.value || undefined)
              }
            />
          </Field>
          <div className="form-row">
            <Field label="CPU limit" hint="Default: 1 CPU">
              <input
                type="number"
                min="0.001"
                step="any"
                value={container.cpus ?? ""}
                placeholder="1"
                onChange={(e) =>
                  setContainer(
                    "cpus",
                    e.target.value === "" ? undefined : Number(e.target.value),
                  )
                }
              />
            </Field>
            <Field label="Memory limit" hint="Default: 2g">
              <input
                value={container.memory ?? ""}
                placeholder="2g"
                pattern="[1-9][0-9]*[bkmgBKMG]?"
                title="A positive size such as 512m or 2g"
                onChange={(e) =>
                  setContainer("memory", e.target.value || undefined)
                }
              />
            </Field>
          </div>
          <Field label="Container network">
            <select
              value={network}
              onChange={(e) =>
                setContainer(
                  "network",
                  e.target.value === DEFAULT ? undefined : e.target.value,
                )
              }
            >
              <option value={DEFAULT}>None (default)</option>
              <option value="none">None</option>
              <option value="bridge">Bridge</option>
              {![DEFAULT, "none", "bridge"].includes(network) && (
                <option value={network}>
                  {network || "Empty value"} (unsupported)
                </option>
              )}
            </select>
          </Field>
        </>
      )}
    </div>
  );
}
