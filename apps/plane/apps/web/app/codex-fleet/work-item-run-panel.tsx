import type { MouseEvent, SyntheticEvent } from "react";
import { useEffect, useMemo, useState } from "react";

import { CodexFleetLocalApi, CodexFleetLocalApiError, ensureCodexFleetLocalConnection } from "./local-api";
import type { CodexFleetRun, CodexFleetTaskMetadata } from "./local-api";

type Props = {
  workItemId: string;
  planeProjectId: string;
  identifier: string;
  labels?: string[];
};

type LocalSettings = {
  apiUrl: string;
  token: string;
};

const readLocalSettings = (): LocalSettings => {
  if (typeof window === "undefined") return { apiUrl: "http://127.0.0.1:18790", token: "" };
  return {
    apiUrl: window.localStorage.getItem("codexFleetLocalApiUrl") || "http://127.0.0.1:18790",
    token: window.localStorage.getItem("codexFleetLocalToken") || "",
  };
};

const reconnectUrl = (apiUrl: string) => {
  if (typeof window === "undefined") return "";
  const path = `${window.location.pathname}${window.location.search}`.replace(/^\/+/, "");
  return new CodexFleetLocalApi({ baseUrl: apiUrl, token: null }).connectUrl(path);
};

const AGENT_LABELS: Record<string, string> = {
  "agent-orchestrator": "Orchestrator",
  "agent-planner": "Planner",
  "agent-code-scout": "Code Scout",
  "agent-code_scout": "Code Scout",
  "agent-implementer": "Implementer",
  "agent-worker": "Implementer",
  "agent-harness-reviewer": "Harness Reviewer",
  "agent-harness_reviewer": "Harness Reviewer",
  "agent-security-reviewer": "Security Reviewer",
  "agent-security_reviewer": "Security Reviewer",
  "agent-token-reviewer": "Token Reviewer",
  "agent-token_reviewer": "Token Reviewer",
  "agent-delivery-manager": "Delivery Manager",
  "agent-delivery_manager": "Delivery Manager",
};

const friendlyAgentRole = (role?: string | null) => {
  if (!role) return null;
  const normalized = role.toLowerCase().replaceAll("_", "-");
  return (
    AGENT_LABELS[`agent-${normalized}`] ??
    normalized
      .split("-")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
};

const agentOwnerFromLabels = (labels: string[] = []) => {
  const normalized = labels.map((label) => label.toLowerCase());
  for (const label of normalized) {
    if (AGENT_LABELS[label]) return AGENT_LABELS[label];
  }
  return null;
};

const taskSourceLabel = (labels: string[] = []) => {
  const normalized = labels.map((label) => label.toLowerCase());
  if (normalized.includes("agent-followup")) return "Agent follow-up";
  if (normalized.includes("agent-proposed")) return "Agent proposed";
  if (normalized.includes("human-requested")) return "Human";
  return "Task";
};

const taskOwnerLabel = (labels: string[] = [], run?: CodexFleetRun | null) =>
  run?.agent_name || friendlyAgentRole(run?.agent_role) || agentOwnerFromLabels(labels) || taskSourceLabel(labels);

const friendlyValue = (value: unknown) =>
  ({
    never: "Auto local edits",
    "on-request": "Ask before risky actions",
    untrusted: "Untrusted sandbox",
    execute_only: "Execute only",
    plan_only: "Plan only",
    plan_execute: "Plan and execute",
    full_auto: "Full auto",
  })[String(value)] ?? String(value);

const friendlyEvent = (kind: string) =>
  ({
    claimed: "Claimed",
    workspace_prepared: "Workspace ready",
    runner_started: "Codex started",
    runner_finished: "Codex completed",
    needs_input: "Needs input",
    proposed_task_created: "Child task created",
    parent_blocked: "Parent blocked",
    parent_children_completed: "Ready for human review",
    retry_requested: "Retry requested",
    cancel_requested: "Cancel requested",
    cancelled: "Cancelled",
  })[kind] ?? kind.replaceAll("_", " ");

export function CodexFleetWorkItemRunPanel({ workItemId, planeProjectId, identifier, labels }: Props) {
  const [settings, setSettings] = useState(readLocalSettings);
  const [run, setRun] = useState<CodexFleetRun | null>(null);
  const [children, setChildren] = useState<CodexFleetTaskMetadata[]>([]);
  const [parent, setParent] = useState<CodexFleetTaskMetadata | null>(null);
  const [answer, setAnswer] = useState("");
  const [message, setMessage] = useState("Ready for codex-fleet.");
  const [busy, setBusy] = useState(false);
  const [needsConfiguration, setNeedsConfiguration] = useState(false);

  const api = useMemo(
    () => new CodexFleetLocalApi({ baseUrl: settings.apiUrl, token: settings.token || null }),
    [settings.apiUrl, settings.token]
  );
  const projectRef = { plane_project_id: planeProjectId };
  const canConnect = Boolean(settings.token);
  const owner = taskOwnerLabel(labels, run);
  const modelSummary = [run?.model, run?.reasoning_effort].filter(Boolean).join(" / ");
  const refreshAfterMutation = async () => {
    await refreshStatus().catch(() => undefined);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("codex-fleet-refresh", { detail: { planeProjectId, workItemId } }));
      window.setTimeout(() => window.location.reload(), 900);
    }
  };

  const refreshConnection = async () => {
    const connection = await ensureCodexFleetLocalConnection();
    setSettings({ apiUrl: connection.apiUrl, token: connection.token });
    return connection;
  };

  const refreshStatus = async () => {
    const connection = await refreshConnection().catch(() => ({ ...settings, token: "", status: "unreachable" }));
    if (!connection.token) {
      setMessage("Reconnect Codex Fleet to load run status.");
      return;
    }
    setBusy(true);
    try {
      const localApi = new CodexFleetLocalApi({ baseUrl: connection.apiUrl, token: connection.token });
      const result = await localApi.runStatus(workItemId, projectRef);
      const [childResult, parentResult] = await Promise.all([
        localApi.workItemChildren(workItemId, projectRef).catch(() => ({ children: [] })),
        localApi.workItemParent(workItemId, projectRef).catch(() => ({ parent: null })),
      ]);
      setRun(result.run);
      setChildren(childResult.children);
      setParent(parentResult.parent);
      setNeedsConfiguration(false);
      setMessage(result.run ? `Latest run: ${result.run.status}.` : "No codex-fleet run recorded yet.");
    } catch (error) {
      if (error instanceof CodexFleetLocalApiError && error.code === "codex_not_configured") {
        setNeedsConfiguration(true);
        setMessage("Codex is not configured for this project yet.");
        return;
      }
      setMessage(error instanceof Error ? error.message : "codex-fleet status unavailable.");
    } finally {
      setBusy(false);
    }
  };

  const retryWorkItem = async () => {
    if (!canConnect) return;
    setBusy(true);
    try {
      const result = await api.retryWorkItem(workItemId, projectRef);
      setRun(result.previous_run);
      setMessage(`Moved ${identifier} to ${result.state}.`);
      await refreshAfterMutation();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not retry this work item.");
    } finally {
      setBusy(false);
    }
  };

  const cancelWorkItem = async () => {
    if (!canConnect) return;
    setBusy(true);
    try {
      const result = await api.cancelWorkItem(workItemId, projectRef);
      setRun(result.run);
      setMessage(`Moved ${identifier} to ${result.state}.`);
      await refreshAfterMutation();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not cancel this work item.");
    } finally {
      setBusy(false);
    }
  };

  const answerInput = async () => {
    if (!canConnect || !answer.trim()) return;
    setBusy(true);
    try {
      const result = await api.answerInput(workItemId, answer.trim(), projectRef);
      setAnswer("");
      setRun(result.run ?? run);
      setMessage("Answer saved. Codex Fleet will resume automatically.");
      await refreshAfterMutation();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not save the answer.");
    } finally {
      setBusy(false);
    }
  };

  const runWorkItem = async () => {
    const connection = await refreshConnection().catch(() => ({ ...settings, token: "", status: "unreachable" }));
    if (!connection.token) {
      setMessage("Reconnect Codex Fleet before running this work item.");
      return;
    }
    setBusy(true);
    setMessage("Starting codex-fleet...");
    try {
      const localApi = new CodexFleetLocalApi({ baseUrl: connection.apiUrl, token: connection.token });
      const result = await localApi.runWorkItem(
        workItemId,
        {
          fake: false,
        },
        projectRef
      );
      setRun(result.run);
      setNeedsConfiguration(false);
      setMessage(result.dispatched ? `Started ${result.run?.identifier ?? identifier}.` : result.message);
      await refreshAfterMutation();
    } catch (error) {
      if (error instanceof CodexFleetLocalApiError && error.code === "codex_not_configured") {
        setNeedsConfiguration(true);
        setMessage("Codex is not configured for this project yet.");
        return;
      }
      setMessage(error instanceof Error ? error.message : "codex-fleet could not start this work item.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    setSettings(readLocalSettings());
    void refreshStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (needsConfiguration) {
    return (
      <section className="rounded-lg border border-custom-primary-100/40 bg-custom-primary-100/10 p-3 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-primary">Configure codex-fleet for this project</div>
            <div className="mt-1 text-xs text-secondary">This project is blocked until it is linked to a local repo.</div>
          </div>
          <a
            className="h-8 rounded-md bg-custom-primary-100 px-3 py-1.5 text-xs font-semibold text-white"
            href={`/${window.location.pathname.split("/")[1]}/projects/${planeProjectId}/codex-settings/`}
            onClick={(event) => event.stopPropagation()}
          >
            Configure
          </a>
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-subtle bg-surface-1 p-3 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-8 flex-shrink-0 items-center justify-center rounded-md border border-subtle bg-surface-2">
            <img src="/codex-fleet-logo.svg" alt="" className="size-5" />
          </span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-primary">codex-fleet run</div>
            <div className="truncate text-xs text-secondary">
              Agent: {owner} · {message}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!canConnect ? (
            <a
              className="h-8 rounded-md border border-subtle px-3 py-1.5 text-xs font-medium text-primary hover:bg-surface-2"
              href={reconnectUrl(settings.apiUrl)}
              onClick={(event) => event.stopPropagation()}
            >
              Reconnect Codex Fleet
            </a>
          ) : null}
          <button
            type="button"
            className="h-8 rounded-md border border-subtle px-3 text-xs font-medium text-primary hover:bg-surface-2 disabled:opacity-50"
            disabled={busy}
            onClick={() => void refreshStatus()}
          >
            Status
          </button>
          <button
            type="button"
            className="h-8 rounded-md bg-custom-primary-100 px-3 text-xs font-semibold text-white disabled:opacity-50"
            disabled={busy || needsConfiguration}
            onClick={() => void runWorkItem()}
          >
            Run with Codex
          </button>
          <button
            type="button"
            className="h-8 rounded-md border border-subtle px-3 text-xs font-medium text-primary hover:bg-surface-2 disabled:opacity-50"
            disabled={busy}
            onClick={() => void retryWorkItem()}
          >
            Retry
          </button>
          <button
            type="button"
            className="h-8 rounded-md border border-danger-primary/40 px-3 text-xs font-medium text-danger-primary hover:bg-danger-50 disabled:opacity-50"
            disabled={busy}
            onClick={() => void cancelWorkItem()}
          >
            Cancel
          </button>
        </div>
      </div>
      {needsConfiguration ? (
        <div className="mt-3 rounded-md border border-custom-primary-100/40 bg-custom-primary-100/10 p-3 text-xs text-secondary">
          <div className="font-semibold text-primary">Configure Codex for this project</div>
          <p className="mt-1">This Plane project is not linked to a local repo yet. Open Codex Settings or Fleet Logs, then choose Create new repo or Add existing repo.</p>
        </div>
      ) : null}
      {run && (
        <div className="mt-3 grid gap-3">
          <dl className="grid gap-2 rounded-md border border-subtle bg-surface-2 p-3 text-xs text-secondary sm:grid-cols-2">
            <div className="min-w-0 rounded-md border border-subtle bg-surface-1 p-2 sm:col-span-2">
              <dt className="text-tertiary">Agent owner</dt>
              <dd className="mt-1 flex flex-wrap items-center gap-2 text-primary">
                <span className="font-semibold">{owner}</span>
                <span className="rounded border border-subtle px-1.5 py-0.5 text-tertiary">{run.status}</span>
                {modelSummary ? <span className="rounded border border-subtle px-1.5 py-0.5 text-tertiary">{modelSummary}</span> : null}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-tertiary">Run</dt>
              <dd className="truncate text-primary">{run.identifier}</dd>
            </div>
            <div className="min-w-0">
              <dt className="text-tertiary">Status</dt>
              <dd className="truncate text-primary">{run.status}</dd>
            </div>
            {run.branch_name && (
              <div className="min-w-0">
                <dt className="text-tertiary">Branch</dt>
                <dd className="truncate text-primary">{run.branch_name}</dd>
              </div>
            )}
            <div className="min-w-0">
              <dt className="text-tertiary">Agent</dt>
              <dd className="truncate text-primary">{run.agent_name || run.agent_role || "Worker"}</dd>
            </div>
            {run.model && (
              <div className="min-w-0">
                <dt className="text-tertiary">Model</dt>
                <dd className="truncate text-primary">{run.model}</dd>
              </div>
            )}
            {Boolean(run.settings?.approval_policy) && (
              <div className="min-w-0">
                <dt className="text-tertiary">Local edit approval</dt>
                <dd className="truncate text-primary">{friendlyValue(String(run.settings?.approval_policy))}</dd>
              </div>
            )}
            {Boolean(run.settings?.workflow_mode) && (
              <div className="min-w-0">
                <dt className="text-tertiary">Automation</dt>
                <dd className="truncate text-primary">{friendlyValue(String(run.settings?.workflow_mode))}</dd>
              </div>
            )}
            {run.token_usage?.total_tokens && (
              <div className="min-w-0">
                <dt className="text-tertiary">Tokens</dt>
                <dd className="truncate text-primary">{run.token_usage.total_tokens.toLocaleString()}</dd>
              </div>
            )}
            {run.worktree_path && (
              <div className="min-w-0">
                <dt className="text-tertiary">Worktree</dt>
                <dd className="truncate text-primary">{run.worktree_path}</dd>
              </div>
            )}
            {run.error && (
              <div className="min-w-0 sm:col-span-2">
                <dt className="text-tertiary">Error</dt>
                <dd className="truncate text-danger-primary">{run.error}</dd>
              </div>
            )}
          </dl>

          {(parent || children.length > 0) && (
            <div className="rounded-md border border-subtle bg-surface-2 p-3 text-xs">
              <div className="mb-2 font-semibold text-primary">Task graph</div>
              {parent ? <p className="text-secondary">Parent: {parent.parent_identifier || parent.parent_item_id}</p> : null}
              {children.length ? (
                <div className="mt-2 grid gap-1">
                  {children.map((child) => (
                    <div key={child.item_id} className="rounded border border-subtle bg-surface-1 px-2 py-1 text-secondary">
                      {child.item_id} · {child.role || "implementer"} · depth {child.depth}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          )}

          {run.status === "needs_input" && (
            <div className="grid gap-2 rounded-md border border-subtle bg-surface-2 p-3 text-xs">
              <div className="font-semibold text-primary">Answer and resume</div>
              {run.error ? <p className="text-secondary">{run.error}</p> : null}
              <textarea
                className="min-h-20 rounded border border-subtle bg-surface-1 p-2 text-primary"
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
                placeholder="Reply here. Codex Fleet will save the answer, move this task to Ready, and resume on the next tick."
              />
              <button type="button" className="h-8 w-max rounded bg-custom-primary-100 px-3 font-semibold text-white" onClick={() => void answerInput()}>
                Answer and resume
              </button>
            </div>
          )}

          {run.events && run.events.length > 0 && (
            <div className="rounded-md border border-subtle bg-surface-2 p-3">
              <div className="mb-2 text-xs font-semibold text-primary">Timeline</div>
              <ol className="grid gap-2">
                {run.events.slice(-8).map((event) => (
                  <li key={event.id} className="grid gap-1 border-l border-subtle pl-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-primary">{friendlyEvent(event.kind)}</span>
                      <span className="shrink-0 text-tertiary">{event.created_at}</span>
                    </div>
                    {Object.keys(event.payload).length > 0 && (
                      <pre className="max-h-28 overflow-auto rounded border border-subtle bg-surface-1 p-2 text-[11px] leading-4 text-secondary">
                        {JSON.stringify(event.payload, null, 2)}
                      </pre>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {run.artifacts && run.artifacts.length > 0 && (
            <div className="rounded-md border border-subtle bg-surface-2 p-3 text-xs">
              <div className="mb-2 font-semibold text-primary">Artifacts</div>
              <div className="grid gap-1">
                {run.artifacts.map((artifact) => (
                  <div key={artifact.id} className="truncate text-secondary" title={artifact.path}>
                    {artifact.kind}: {artifact.path}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export function CodexFleetWorkItemRunCompact({ workItemId, planeProjectId, identifier, labels }: Props) {
  const [settings, setSettings] = useState(readLocalSettings);
  const [run, setRun] = useState<CodexFleetRun | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [needsConfiguration, setNeedsConfiguration] = useState(false);

  const api = useMemo(
    () => new CodexFleetLocalApi({ baseUrl: settings.apiUrl, token: settings.token || null }),
    [settings.apiUrl, settings.token]
  );
  const projectRef = { plane_project_id: planeProjectId };
  const canConnect = Boolean(settings.token);
  const status = run?.status || message || "No run";
  const owner = taskOwnerLabel(labels, run);
  const modelSummary = [run?.model, run?.reasoning_effort].filter(Boolean).join(" / ");
  const refreshBoardSoon = () => {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent("codex-fleet-refresh", { detail: { planeProjectId, workItemId } }));
    window.setTimeout(() => window.location.reload(), 900);
  };

  const refreshConnection = async () => {
    const connection = await ensureCodexFleetLocalConnection();
    setSettings({ apiUrl: connection.apiUrl, token: connection.token });
    return connection;
  };

  useEffect(() => {
    setSettings(readLocalSettings());
    void refreshStatusFromMount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshStatusFromMount = async () => {
    const connection = await refreshConnection().catch(() => ({ ...settings, token: "", status: "unreachable" }));
    if (!connection.token) return;
    try {
      const result = await new CodexFleetLocalApi({ baseUrl: connection.apiUrl, token: connection.token }).runStatus(workItemId, projectRef);
      setRun(result.run);
      setNeedsConfiguration(false);
      setMessage(result.run ? "" : "No run");
    } catch (error) {
      if (error instanceof CodexFleetLocalApiError && error.code === "codex_not_configured") {
        setNeedsConfiguration(true);
        setMessage("Configure codex-fleet");
      }
    }
  };

  const stopCardNavigation = (event: SyntheticEvent) => {
    event.preventDefault();
    event.stopPropagation();
  };

  const refreshStatus = async (event: MouseEvent<HTMLButtonElement>) => {
    stopCardNavigation(event);
    const connection = await refreshConnection().catch(() => ({ ...settings, token: "", status: "unreachable" }));
    if (!connection.token) {
      setMessage("Connect API");
      return;
    }
    setBusy(true);
    try {
      const result = await new CodexFleetLocalApi({ baseUrl: connection.apiUrl, token: connection.token }).runStatus(workItemId, projectRef);
      setRun(result.run);
      setNeedsConfiguration(false);
      setMessage(result.run ? "" : "No run");
    } catch (error) {
      if (error instanceof CodexFleetLocalApiError && error.code === "codex_not_configured") {
        setNeedsConfiguration(true);
        setMessage("Configure Codex");
        return;
      }
      setMessage(error instanceof Error ? error.message : "Status failed");
    } finally {
      setBusy(false);
    }
  };

  const runWorkItem = async (event: MouseEvent<HTMLButtonElement>) => {
    stopCardNavigation(event);
    const connection = await refreshConnection().catch(() => ({ ...settings, token: "", status: "unreachable" }));
    if (!connection.token) {
      setMessage("Connect API");
      return;
    }
    setBusy(true);
    setMessage("Starting");
    try {
      const result = await new CodexFleetLocalApi({ baseUrl: connection.apiUrl, token: connection.token }).runWorkItem(
        workItemId,
        {
          fake: false,
        },
        projectRef
      );
      setRun(result.run);
      setNeedsConfiguration(false);
      setMessage(result.dispatched ? "" : result.message);
      refreshBoardSoon();
    } catch (error) {
      if (error instanceof CodexFleetLocalApiError && error.code === "codex_not_configured") {
        setNeedsConfiguration(true);
        setMessage("Configure Codex");
        return;
      }
      setMessage(error instanceof Error ? error.message : "Run failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="mt-2 flex flex-wrap items-center gap-1.5 rounded-md border border-subtle bg-surface-1 px-2 py-1.5 text-xs shadow-sm"
      onClick={stopCardNavigation}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <span className="flex size-5 flex-shrink-0 items-center justify-center rounded border border-subtle bg-surface-2">
        <img src="/codex-fleet-logo.svg" alt="" className="size-3.5" />
      </span>
      <span className="max-w-[8rem] truncate font-semibold text-primary" title={`Agent: ${owner}`}>
        {owner}
      </span>
      <span className="rounded border border-subtle px-1.5 py-0.5 text-tertiary" title={status}>
        {status}
      </span>
      {modelSummary ? <span className="rounded border border-subtle px-1.5 py-0.5 text-tertiary">{modelSummary}</span> : null}
      <button
        type="button"
        className="rounded border border-subtle px-1.5 py-0.5 text-tertiary hover:bg-surface-2 disabled:opacity-50"
        disabled={busy}
        onClick={(event) => void refreshStatus(event)}
      >
        Status
      </button>
      <button
        type="button"
        className="rounded bg-custom-primary-100 px-1.5 py-0.5 font-medium text-white disabled:opacity-50"
        disabled={busy || needsConfiguration}
        onClick={(event) => void runWorkItem(event)}
      >
        {needsConfiguration ? "Configure Codex" : "Run with Codex"}
      </button>
      {["blocked", "needs_input", "rework", "failed", "cancelled"].includes(String(run?.status || "").toLowerCase()) ? (
        <button
          type="button"
          className="rounded border border-subtle px-1.5 py-0.5 font-medium text-primary hover:bg-surface-2 disabled:opacity-50"
          disabled={busy}
          onClick={(event) => {
            stopCardNavigation(event);
            void api.retryWorkItem(workItemId, projectRef).then((result) => {
              setRun(result.previous_run);
              setMessage(`Moved ${identifier} to ${result.state}`);
              refreshBoardSoon();
            });
          }}
        >
          Retry
        </button>
      ) : null}
      {run?.identifier && (
        <span className="max-w-[5rem] truncate text-tertiary" title={run.identifier}>
          {run.identifier || identifier}
        </span>
      )}
    </div>
  );
}
