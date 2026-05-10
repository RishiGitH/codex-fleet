"use client";

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import type React from "react";
import {
  Activity,
  AlertCircle,
  Archive,
  Bot,
  CheckCircle2,
  Clock3,
  Copy,
  FileText,
  MessageSquare,
  Network,
  PlayCircle,
  RefreshCw,
  Settings,
  Shield,
  Sparkles,
  X,
} from "lucide-react";
import "./project-surfaces.css";
import {
  CodexFleetLocalApi,
  CodexFleetLocalApiError,
  DEFAULT_CODEX_FLEET_API_URL,
  ensureCodexFleetLocalConnection,
  type CodexFleetProject,
  type CodexFleetProjectSettings,
  type CodexFleetRun,
  type CodexFleetRunArtifact,
  type CodexFleetRunEvent,
  type CodexFleetRunMessage,
  type CodexFleetSession,
  type CodexFleetWorktree,
} from "@/app/codex-fleet/local-api";

const UI_BUILD_MARKER = "apps/plane-codex-fleet-mission-control-v3";
const EXPECTED_API_BUILD = "codex-fleet-api-board-simplified-v1";

const emptySettings: CodexFleetProjectSettings = {
  runner_mode: "app-server",
  default_model: "gpt-5.5",
  reasoning_effort: "low",
  approval_policy: "never",
  sandbox_mode: "workspace-write",
  max_parallel_agents: 3,
  max_depth: 2,
  max_child_tasks_per_run: 8,
  job_timeout_seconds: 1200,
  workflow_mode: "plan_execute",
  skill_policy: "minimal",
  subagents_enabled: false,
  enabled_agent_roles: [],
  agent_profiles: {},
  subagents: {},
};

type SurfaceProps = {
  projectId: string;
};

type LoadState = {
  apiUrl: string;
  token: string;
  message: string;
  busy: boolean;
  session?: CodexFleetSession | null;
};

type SurfaceData = {
  dashboard: Record<string, unknown>;
  logs: Record<string, unknown>;
  runs: CodexFleetRun[];
  unconfigured: boolean;
};

type GateState = LoadState & {
  checked: boolean;
  unconfigured: boolean;
};

type ProjectDashboardView = {
  projectName: string;
  repoPath: string;
  workflowMode: string;
  model: string;
  reasoning: string;
  activeAgents: number;
  latestRunState: string;
  blocker: string;
  tokenUsage: string;
  runCount: number;
  artifactCount: number;
};

type TaskGraphNodeView = {
  id: string;
  key: string;
  role: string;
  state: string;
  workflow: string;
  parent: string;
  depth: string;
  runStatus: string;
  legacy: boolean;
};

type AgentSessionView = {
  id: string;
  role: string;
  name: string;
  task: string;
  status: string;
  model: string;
  reasoning: string;
  branch: string;
  worktree: string;
  lastEvent: string;
  legacy: boolean;
};

type RunRowView = {
  run: CodexFleetRun;
  id: string;
  task: string;
  role: string;
  status: string;
  model: string;
  reasoning: string;
  started: string;
  duration: string;
  branch: string;
  worktree: string;
  artifacts: string;
  tokens: string;
  latestEvent: string;
  blocker: string;
  legacy: boolean;
};

type TranscriptMessageView = {
  id: string;
  role: string;
  kind: string;
  content: string;
  time: string;
  artifactPath: string;
};

type ArtifactGroupView = {
  run: RunRowView;
  artifacts: CodexFleetRunArtifact[];
  changedFiles: string[];
};

const initialState: LoadState = {
  apiUrl: DEFAULT_CODEX_FLEET_API_URL,
  token: "",
  message: "Connect to codex-fleet.",
  busy: false,
};

const roleOrder = [
  "orchestrator",
  "planner",
  "code_scout",
  "implementer",
  "quality_reviewer",
  "test_reviewer",
  "security_reviewer",
  "delivery_manager",
];

function reconnectCurrentCodexSurface(apiUrl: string) {
  if (typeof window === "undefined") return;
  const target = `${window.location.pathname}${window.location.search}`;
  window.location.assign(new CodexFleetLocalApi({ baseUrl: apiUrl, token: null }).connectUrl(target));
}

export function CodexProjectGate({ projectId, children }: { projectId: string; children: React.ReactNode }) {
  const [state, setState] = useState<GateState>({
    ...initialState,
    checked: false,
    unconfigured: false,
    message: "Checking codex-fleet project configuration...",
  });

  const check = async () => {
    setState((current) => ({ ...current, busy: true, message: "Checking codex-fleet project configuration..." }));
    try {
      const local = await ensureCodexFleetLocalConnection();
      if (!local.token) {
        setState((current) => ({ ...current, apiUrl: local.apiUrl, token: "", busy: true, message: "Reconnecting Codex Fleet..." }));
        reconnectCurrentCodexSurface(local.apiUrl);
        return;
      }
      const api = new CodexFleetLocalApi({ baseUrl: local.apiUrl, token: local.token || null });
      const logsResult = await api.fleetLogs(projectId);
      const unconfigured = logsResult.fleet_logs?.linked === false;
      setState({
        apiUrl: local.apiUrl,
        token: local.token,
        busy: false,
        checked: true,
        unconfigured,
        message: unconfigured ? "Configure codex-fleet before work items can run." : "codex-fleet is ready for this project.",
      });
    } catch (error) {
      if (error instanceof CodexFleetLocalApiError && error.code === "codex_not_configured") {
        setState((current) => ({
          ...current,
          busy: false,
          checked: true,
          unconfigured: true,
          message: "Configure codex-fleet before work items can run.",
        }));
        return;
      }
      setState((current) => ({
        ...current,
        busy: false,
        checked: true,
        unconfigured: false,
        message: friendlyError(error, "Could not check codex-fleet configuration."),
      }));
    }
  };

  useEffect(() => {
    void check();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  if (!state.checked || state.unconfigured) {
    return (
      <SurfaceShell icon={<Settings className="size-4" />} title="Configure codex-fleet" state={state} onRefresh={check}>
        {state.unconfigured ? (
          <ConfigureCodexPanel projectId={projectId} state={state} onConfigured={check} />
        ) : (
          <EmptyState
            icon={<RefreshCw className="size-5" />}
            title="Checking project link"
            body="Codex Fleet is checking whether this Plane project is connected to a local repo."
          />
        )}
      </SurfaceShell>
    );
  }

  return <>{children}</>;
}

export function FleetLogsSurface({ projectId }: SurfaceProps) {
  const { state, data, reload } = useFleetData(projectId, "Fleet Logs");
  const [selectedRun, setSelectedRun] = useState<CodexFleetRun | null>(null);
  const dashboard = toDashboardView(data.dashboard, data.runs);
  const nodes = toTaskNodes(data.dashboard);
  const events = records(data.dashboard.events).length ? records(data.dashboard.events) : records(data.logs.recent_events);
  const runs = data.runs.map(toRunRow);
  const attention = runs.filter((run) => ["needs_input", "blocked", "rework", "failed", "stalled"].includes(run.status));
  const artifactGroups = toArtifactGroups(data.runs);

  return (
    <SurfaceShell icon={<Activity className="size-4" />} title="Fleet Logs" state={state} onRefresh={reload}>
      {data.unconfigured ? <ConfigureCodexPanel projectId={projectId} state={state} onConfigured={reload} /> : null}
      {data.unconfigured ? null : (
        <div className="grid gap-5">
          <CommandCenter dashboard={dashboard} />
          <AgentRunBoard runs={data.runs} onOpenChat={setSelectedRun} />
          <div className="grid gap-5">
            <TaskGraph nodes={nodes} />
            <AgentActivity events={events} runs={data.runs} />
          </div>
          <div className="grid gap-5">
            <NeedsAttention runs={attention} />
            <RecentRuns runs={runs.slice(0, 6)} />
          </div>
          <RecentArtifacts groups={artifactGroups.slice(0, 4)} runsExist={data.runs.length > 0} />
        </div>
      )}
      {selectedRun ? <RunDetailDrawer projectId={projectId} run={selectedRun} onClose={() => setSelectedRun(null)} /> : null}
    </SurfaceShell>
  );
}

export function AgentsSurface({ projectId }: SurfaceProps) {
  const { state, data, reload } = useFleetData(projectId, "Agents");
  const activeAgents = records(data.dashboard.active_agents).map(toAgentSession);
  const recentAgents = (records(data.dashboard.recent_agents).length ? records(data.dashboard.recent_agents) : data.runs).map(toAgentSession);

  return (
    <SurfaceShell icon={<Bot className="size-4" />} title="Agents" state={state} onRefresh={reload}>
      {data.unconfigured ? <ConfigureCodexPanel projectId={projectId} state={state} onConfigured={reload} /> : null}
      {data.unconfigured ? null : (
        <div className="grid gap-5">
          <ActiveAgentsStrip agents={activeAgents} />
          <AgentRoster agents={recentAgents} />
          <RecentAgentSessions agents={recentAgents.slice(0, 20)} />
        </div>
      )}
    </SurfaceShell>
  );
}

export function RunsSurface({ projectId }: SurfaceProps) {
  const { state, data, reload } = useFleetData(projectId, "Runs");
  const [selectedRun, setSelectedRun] = useState<CodexFleetRun | null>(null);
  const [filters, setFilters] = useState({ status: "all", role: "all", task: "", model: "", search: "" });
  const rows = data.runs.map(toRunRow);
  const filteredRows = rows.filter((row) => {
    const haystack = [row.task, row.role, row.status, row.model, row.reasoning, row.latestEvent].join(" ").toLowerCase();
    return (
      (filters.status === "all" || row.status === filters.status) &&
      (filters.role === "all" || normalizeRole(row.role) === filters.role) &&
      (!filters.task || row.task.toLowerCase().includes(filters.task.toLowerCase())) &&
      (!filters.model || row.model.toLowerCase().includes(filters.model.toLowerCase())) &&
      (!filters.search || haystack.includes(filters.search.toLowerCase()))
    );
  });

  return (
    <SurfaceShell icon={<PlayCircle className="size-4" />} title="Runs" state={state} onRefresh={reload}>
      {data.unconfigured ? <ConfigureCodexPanel projectId={projectId} state={state} onConfigured={reload} /> : null}
      {data.unconfigured ? null : (
        <Panel
          title="Run history"
          action={<span className="cf-mini">{filteredRows.length} of {rows.length} runs</span>}
        >
          <RunFilters rows={rows} filters={filters} onChange={setFilters} />
          <RunsTable rows={filteredRows} onSelect={(row) => setSelectedRun(row.run)} />
        </Panel>
      )}
      {selectedRun ? <RunDetailDrawer projectId={projectId} run={selectedRun} onClose={() => setSelectedRun(null)} /> : null}
    </SurfaceShell>
  );
}

export function ArtifactsSurface({ projectId }: SurfaceProps) {
  const { state, data, reload } = useFleetData(projectId, "Artifacts");
  const groups = toArtifactGroups(data.runs);

  return (
    <SurfaceShell icon={<Archive className="size-4" />} title="Artifacts" state={state} onRefresh={reload}>
      {data.unconfigured ? <ConfigureCodexPanel projectId={projectId} state={state} onConfigured={reload} /> : null}
      {data.unconfigured ? null : (
        <Panel title="Run artifacts" action={<span className="text-xs text-custom-text-400">{groups.length} runs with output</span>}>
          {groups.length === 0 ? (
            <EmptyState
              icon={<Archive className="size-5" />}
              title={data.runs.length ? "Runs exist, but no artifacts were attached" : "No artifacts yet"}
              body={data.runs.length ? "This likely means artifact capture is incomplete for these runs. Future App Server sessions should attach transcripts, summaries, logs, screenshots, or changed-file manifests." : "Artifacts appear after an agent writes transcripts, summaries, logs, screenshots, or changed-file manifests."}
            />
          ) : (
            <div className="grid gap-3">
              {groups.map((group) => <ArtifactGroup key={group.run.id} group={group} />)}
            </div>
          )}
        </Panel>
      )}
    </SurfaceShell>
  );
}

export function CodexSettingsSurface({ projectId }: SurfaceProps) {
  const [state, setState] = useState<LoadState>(initialState);
  const [project, setProject] = useState<CodexFleetProject | null>(null);
  const [unconfigured, setUnconfigured] = useState(false);
  const [settings, setSettings] = useState<CodexFleetProjectSettings>(emptySettings);
  const [loadedSettings, setLoadedSettings] = useState<CodexFleetProjectSettings>(emptySettings);
  const [worktrees, setWorktrees] = useState<CodexFleetWorktree[]>([]);
  const api = useMemo(() => new CodexFleetLocalApi({ baseUrl: state.apiUrl, token: state.token || null }), [state.apiUrl, state.token]);
  const dirty = JSON.stringify(settings) !== JSON.stringify(loadedSettings);

  const load = async () => {
    setState((current) => ({ ...current, busy: true, message: "Loading Codex settings..." }));
    try {
      const local = await ensureCodexFleetLocalConnection();
      if (!local.token) {
        setState((current) => ({ ...current, apiUrl: local.apiUrl, token: "", busy: true, message: "Reconnecting Codex Fleet..." }));
        reconnectCurrentCodexSurface(local.apiUrl);
        return;
      }
      const localApi = new CodexFleetLocalApi({ baseUrl: local.apiUrl, token: local.token || null });
      const [result, worktreeResult] = await Promise.all([
        localApi.projectSettings(projectId),
        localApi.worktrees(projectId).catch(() => ({ worktrees: [] as CodexFleetWorktree[] })),
      ]);
      setProject(result.project);
      setUnconfigured(false);
      setSettings(result.settings);
      setLoadedSettings(result.settings);
      setWorktrees(worktreeResult.worktrees || []);
      setState({ apiUrl: local.apiUrl, token: local.token, busy: false, message: "Codex settings loaded." });
    } catch (error) {
      if (error instanceof CodexFleetLocalApiError && error.code === "codex_not_configured") {
        setProject(null);
        setUnconfigured(true);
      }
      setState((current) => ({ ...current, busy: false, message: friendlyError(error, "Codex settings are unavailable.") }));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const save = async () => {
    setState((current) => ({ ...current, busy: true, message: "Saving Codex settings..." }));
    try {
      const result = await api.updateProjectSettings(projectId, settings);
      setProject(result.project);
      setSettings(result.settings);
      setLoadedSettings(result.settings);
      setState((current) => ({ ...current, busy: false, message: "Codex settings saved." }));
    } catch (error) {
      setState((current) => ({ ...current, busy: false, message: friendlyError(error, "Codex settings were not saved.") }));
    }
  };

  const update = (patch: Partial<CodexFleetProjectSettings>) => setSettings((current) => ({ ...current, ...patch }));
  const agentProfiles = settings.agent_profiles || settings.subagents || {};
  const updateAgentProfile = (role: string, patch: Record<string, unknown>) =>
    setSettings((current) => {
      const profiles = { ...(current.agent_profiles || current.subagents || {}) };
      profiles[role] = { ...(profiles[role] || { model: current.default_model, reasoning_effort: current.reasoning_effort, sandbox_mode: current.sandbox_mode }), ...patch };
      return {
        ...current,
        agent_profiles: profiles,
        subagents: Object.fromEntries(Object.entries(profiles).map(([key, profile]) => [key, { model: profile.model, reasoning_effort: profile.reasoning_effort, sandbox_mode: profile.sandbox_mode }])),
        enabled_agent_roles: Object.entries(profiles).filter(([, profile]) => profile.enabled !== false).map(([key]) => key),
      };
    });

  return (
    <SurfaceShell icon={<Settings className="size-4" />} title="Codex Settings" state={state} onRefresh={load}>
      {unconfigured ? <ConfigureCodexPanel projectId={projectId} state={state} onConfigured={load} /> : null}
      {unconfigured ? null : (
        <div className="grid gap-5">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <MetricTile label="Repo folder" value={project?.repo_path || "Not linked"} icon={<FileText className="size-4" />} />
            <MetricTile label="Harness" value={project?.harness_status || "Unknown"} icon={<WrenchIcon />} />
            <MetricTile label="Local API" value={state.token ? "Connected" : "Reconnect needed"} icon={<Network className="size-4" />} tone={state.token ? "good" : "warn"} />
            <MetricTile label="App Server" value="Production runner" icon={<Bot className="size-4" />} />
            <MetricTile label="Plane link" value={project?.plane_project_id ? "Linked" : "Unavailable"} icon={<CheckCircle2 className="size-4" />} tone={project?.plane_project_id ? "good" : "warn"} />
          </div>
          <InfoNotice>
            These settings apply to new or unclaimed work items. Running tasks keep their saved model, reasoning, sandbox, and approval settings.
          </InfoNotice>
          <div className="grid gap-5 xl:grid-cols-3">
            <SettingsSection title="Automation" icon={<Sparkles className="size-4" />}>
              <Select label="Automation mode" value={settings.workflow_mode} onChange={(value) => update({ workflow_mode: value as CodexFleetProjectSettings["workflow_mode"] })} options={[["execute_only", "Execute only"], ["plan_only", "Plan only"], ["plan_execute", "Plan and execute"], ["full_auto", "Full auto"]]} />
              <label className="flex items-start gap-2 rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-2 text-xs text-custom-text-300">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={Boolean(settings.subagents_enabled)}
                  onChange={(event) => update({ subagents_enabled: event.target.checked })}
                />
                <span>
                  Enable subagents
                  <span className="block text-custom-text-400">Required for Full auto; Codex Fleet still runs only roles needed for the task.</span>
                </span>
              </label>
              <NumberField label="Max parallel agents" value={settings.max_parallel_agents} onChange={(value) => update({ max_parallel_agents: value })} />
              <NumberField label="Max depth" value={settings.max_depth} onChange={(value) => update({ max_depth: value })} />
            </SettingsSection>
            <SettingsSection title="Model" icon={<Bot className="size-4" />}>
              <Field label="Default model" value={settings.default_model} onChange={(value) => update({ default_model: value })} />
              <Select label="Reasoning" value={settings.reasoning_effort} onChange={(value) => update({ reasoning_effort: value })} options={["low", "medium", "high", "xhigh"]} />
            </SettingsSection>
            <SettingsSection title="Safety" icon={<Shield className="size-4" />}>
              <Select label="Sandbox" value={settings.sandbox_mode} onChange={(value) => update({ sandbox_mode: value })} options={["workspace-write", "read-only", "danger-full-access"]} />
              <Select label="Approval" value={settings.approval_policy} onChange={(value) => update({ approval_policy: value })} options={["never", "on-request", "untrusted"]} />
            </SettingsSection>
          </div>
          <Panel title="Agent profiles" action={<span className="text-xs text-custom-text-400">{Object.keys(agentProfiles).length} roles</span>}>
            <div className="grid gap-2">
              {Object.entries(agentProfiles).map(([role, profile]) => (
                <div key={role} className="grid gap-2 rounded-md border border-custom-border-200 bg-custom-background-100 p-3 lg:grid-cols-[minmax(160px,1fr)_160px_140px_160px]">
                  <label className="flex items-start gap-2 text-xs text-custom-text-300">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={profile.enabled !== false}
                      onChange={(event) => updateAgentProfile(role, { enabled: event.target.checked })}
                    />
                    <span>
                      <span className="block font-semibold text-custom-text-100">{agentLabel(role)}</span>
                      <span className="block text-custom-text-400">{role === "quality_reviewer" ? "Harness, build, and token/context quality." : role === "test_reviewer" ? "Preview, screenshots, video, and test proof." : role === "security_reviewer" ? "Security-sensitive changes only." : "Role-specific Codex session."}</span>
                    </span>
                  </label>
                  <Field label="Model" value={String(profile.model || settings.default_model)} onChange={(value) => updateAgentProfile(role, { model: value })} />
                  <Select label="Reasoning" value={String(profile.reasoning_effort || settings.reasoning_effort)} onChange={(value) => updateAgentProfile(role, { reasoning_effort: value })} options={["low", "medium", "high", "xhigh"]} />
                  <Select label="Sandbox" value={String(profile.sandbox_mode || settings.sandbox_mode)} onChange={(value) => updateAgentProfile(role, { sandbox_mode: value })} options={["workspace-write", "read-only"]} />
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="Harness" action={<StatusPill status={project?.harness_status || "unknown"} />}>
            <div className="grid gap-2 text-sm text-custom-text-300">
              <CompactDataRow label="Repo" value={project?.repo_path || "Not linked"} />
              <CompactDataRow label="Status" value={project?.harness_status || "Unknown"} />
              <CompactDataRow label="Last doctor result" value="Run doctor from the local CLI for detailed harness diagnostics." />
            </div>
          </Panel>
          <Panel title="Worktrees" action={<span className="text-xs text-custom-text-400">{worktrees.length} tracked</span>}>
            {worktrees.length ? (
              <div className="grid gap-2">
                {worktrees.map((worktree) => (
                  <div key={`${worktree.task_id}-${worktree.path}`} className="grid gap-2 rounded-md border border-custom-border-200 bg-custom-background-100 p-3 md:grid-cols-[120px_160px_1fr]">
                    <div>
                      <p className="font-mono text-sm text-custom-text-100">{worktree.task_key}</p>
                      <AgentBadge role={worktree.role} />
                    </div>
                    <div>
                      <StatusPill status={worktree.status} />
                      <p className="mt-2 text-xs text-custom-text-400">{worktree.exists ? "Exists" : "Missing on disk"}</p>
                    </div>
                    <div className="min-w-0">
                      <CopyableCode label="Branch" value={worktree.branch || "Unavailable"} />
                      <CopyableCode label="Path" value={worktree.path} />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState icon={<Archive className="size-5" />} title="No active worktrees" body="Agent worktrees appear here while work is waiting for review or delivery. They are cleaned up after delivery completion." />
            )}
          </Panel>
          <div className="cf-settings-footer">
            {dirty ? <span className="text-xs text-amber-400">Unsaved changes</span> : <span className="text-xs text-custom-text-400">Settings are saved</span>}
            <button type="button" className="cf-button-secondary" disabled={state.busy || !dirty} onClick={() => setSettings(loadedSettings)}>
              Reset
            </button>
            <button type="button" className="cf-button" disabled={state.busy || !state.token || !dirty} onClick={save}>
              {state.busy ? "Saving..." : "Save settings"}
            </button>
          </div>
        </div>
      )}
    </SurfaceShell>
  );
}

function useFleetData(projectId: string, surface: string) {
  const [state, setState] = useState<LoadState>(initialState);
  const [data, setData] = useState<SurfaceData>({ dashboard: {}, logs: {}, runs: [], unconfigured: false });
  const [revision, setRevision] = useState<number | null>(null);

  const reload = async () => {
    setState((current) => ({ ...current, busy: true, message: `Loading ${surface}...` }));
    try {
      const local = await ensureCodexFleetLocalConnection();
      if (!local.token) {
        setState((current) => ({ ...current, apiUrl: local.apiUrl, token: "", busy: true, message: "Reconnecting Codex Fleet..." }));
        reconnectCurrentCodexSurface(local.apiUrl);
        return;
      }
      const api = new CodexFleetLocalApi({ baseUrl: local.apiUrl, token: local.token || null });
      const logsResult = await api.fleetLogs(projectId);
      if (logsResult.fleet_logs?.linked === false) {
        setData({ dashboard: {}, logs: logsResult.fleet_logs as unknown as Record<string, unknown>, runs: [], unconfigured: true });
        setState({ apiUrl: local.apiUrl, token: local.token, busy: false, message: String(logsResult.fleet_logs.message || "Codex is not configured for this project yet."), session: local.session });
        return;
      }
      const [dashboardResult, runsResult] = await Promise.all([api.fleetDashboard(projectId), api.runs({ plane_project_id: projectId })]);
      const dashboardRuns = records(dashboardResult.dashboard?.runs) as unknown as CodexFleetRun[];
      const runs = runsResult.runs?.length ? runsResult.runs : dashboardRuns;
      setData({
        dashboard: dashboardResult.dashboard || {},
        logs: (logsResult.fleet_logs || {}) as unknown as Record<string, unknown>,
        runs,
        unconfigured: false,
      });
      const nextRevision = dashboardResult.dashboard?.revision;
      if (typeof nextRevision === "number") setRevision(nextRevision);
      const staleMessage =
        !local.session?.build || local.session.build !== EXPECTED_API_BUILD
          ? "Codex Fleet daemon is stale. Restart with make stop && make up."
          : `${surface} loaded.`;
      setState({ apiUrl: local.apiUrl, token: local.token, busy: false, message: staleMessage, session: local.session });
    } catch (error) {
      if (error instanceof CodexFleetLocalApiError && error.code === "codex_not_configured") {
        setData({ dashboard: {}, logs: {}, runs: [], unconfigured: true });
      }
      setState((current) => ({ ...current, busy: false, message: friendlyError(error, `${surface} is unavailable.`) }));
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    const handleRefresh = () => {
      void reload();
    };
    window.addEventListener("codex-fleet-refresh", handleRefresh);
    return () => window.removeEventListener("codex-fleet-refresh", handleRefresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (!state.token) return undefined;
    const interval = window.setInterval(async () => {
      try {
        const api = new CodexFleetLocalApi({ baseUrl: state.apiUrl, token: state.token || null });
        const result = await api.projectRevision(projectId);
        if (typeof result.revision === "number" && revision !== null && result.revision !== revision) {
          setRevision(result.revision);
          await reload();
        }
      } catch {
        // Polling is best-effort; the main reload path surfaces connection errors.
      }
    }, 3000);
    return () => window.clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, state.apiUrl, state.token, revision]);

  return { state, data, reload };
}

function ConfigureCodexPanel({ projectId, state, onConfigured }: { projectId: string; state: LoadState; onConfigured: () => void }) {
  const [mode, setMode] = useState<"create_new" | "add_existing">("create_new");
  const [name, setName] = useState("Codex project");
  const [folder, setFolder] = useState("");
  const [slug, setSlug] = useState("codex-project");
  const [projectType, setProjectType] = useState<"blank" | "simple-web" | "node-next" | "python">("node-next");
  const [workflowMode, setWorkflowMode] = useState<"execute_only" | "plan_only" | "plan_execute" | "full_auto">("plan_execute");
  const [message, setMessage] = useState("Choose where Codex should work for this Plane project.");
  const [busy, setBusy] = useState(false);

  const api = useMemo(() => new CodexFleetLocalApi({ baseUrl: state.apiUrl, token: state.token || null }), [state.apiUrl, state.token]);

  const chooseFolder = async () => {
    if (!state.token) {
      setMessage("Reconnect Codex Fleet before choosing a folder.");
      return;
    }
    setBusy(true);
    try {
      const picked = await api.pickFolder();
      setFolder(picked.path);
      setMessage(mode === "create_new" ? "Parent folder selected." : "Repo folder selected.");
    } catch (error) {
      setMessage(friendlyError(error, "Folder picker failed."));
    } finally {
      setBusy(false);
    }
  };

  const configure = async () => {
    if (!state.token) {
      setMessage("Reconnect Codex Fleet before configuring this project.");
      return;
    }
    if (!folder.trim()) {
      setMessage(mode === "create_new" ? "Choose a parent folder first." : "Choose an existing repo folder first.");
      return;
    }
    setBusy(true);
    setMessage("Configuring Codex for this Plane project...");
    try {
      await api.configureCodexForPlaneProject({
        plane_project_id: projectId,
        mode,
        name,
        parent_path: mode === "create_new" ? folder : undefined,
        location: mode === "create_new" ? folder : undefined,
        folder_slug: mode === "create_new" ? slug : undefined,
        repo_path: mode === "add_existing" ? folder : undefined,
        path: mode === "add_existing" ? folder : undefined,
        project_type: projectType,
        apply_harness: true,
        workflow_mode: workflowMode,
        codex_settings: { workflow_mode: workflowMode },
      });
      setMessage("Codex is configured. Ready work items will be picked up on the next daemon tick.");
      onConfigured();
    } catch (error) {
      setMessage(friendlyError(error, "Could not configure Codex."));
    } finally {
      setBusy(false);
    }
  };

  const reconnectTarget = typeof window === "undefined" ? "codex-fleet/projects/" : window.location.pathname.replace(/^\/+/, "");

  return (
    <section className="rounded-lg border border-custom-primary-100/40 bg-custom-primary-100/10 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <div className="flex items-center gap-2">
            <span className="grid size-8 place-items-center rounded-md bg-custom-primary-100 text-white"><Settings className="size-4" /></span>
            <h2 className="text-lg font-semibold text-custom-text-100">Configure Codex Fleet for this project</h2>
          </div>
          <p className="mt-2 text-sm leading-6 text-custom-text-300">
            Connect this Plane project to a local repo. Until this is configured, Codex controls and agent pages stay locked so work cannot run in the wrong folder.
          </p>
        </div>
        {!state.token ? (
          <a className="rounded-md bg-custom-primary-100 px-3 py-2 text-sm font-semibold text-white" href={new CodexFleetLocalApi({ baseUrl: state.apiUrl, token: null }).connectUrl(reconnectTarget)}>
            Reconnect Codex Fleet
          </a>
        ) : <StatusPill status="Connected" />}
      </div>
      <div className="mt-5 grid gap-4 lg:grid-cols-3">
        <Select label="Setup" value={mode} onChange={(value) => setMode(value as "create_new" | "add_existing")} options={[["create_new", "Create new repo"], ["add_existing", "Add existing repo"]]} />
        <Field label="Project name" value={name} onChange={setName} />
        {mode === "create_new" ? <Field label="Repo folder name" value={slug} onChange={setSlug} /> : null}
        {mode === "create_new" ? <Select label="Starter type" value={projectType} onChange={(value) => setProjectType(value as typeof projectType)} options={["blank", "simple-web", "node-next", "python"]} /> : null}
        <Select label="Automation mode" value={workflowMode} onChange={(value) => setWorkflowMode(value as typeof workflowMode)} options={[["execute_only", "Execute only"], ["plan_only", "Plan only"], ["plan_execute", "Plan and execute"], ["full_auto", "Full auto"]]} />
      </div>
      <div className="mt-4 grid gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-3 text-sm font-medium text-custom-text-100 disabled:opacity-50" disabled={busy || !state.token} onClick={chooseFolder}>
            {mode === "create_new" ? "Choose parent folder" : "Choose repo folder"}
          </button>
          {!state.token ? <span className="text-xs text-amber-300">Reconnect Codex Fleet to enable folder picking.</span> : null}
        </div>
        <input className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100 outline-none focus:border-custom-primary-100" value={folder} onChange={(event) => setFolder(event.target.value)} placeholder={mode === "create_new" ? "/path/to/parent/folder" : "/path/to/existing/repo"} />
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-custom-text-300">{message}</p>
        <button type="button" className="h-9 rounded-md bg-custom-primary-100 px-4 text-sm font-semibold text-white disabled:opacity-50" disabled={busy || !state.token} onClick={configure}>
          {busy ? "Configuring..." : "Configure Codex"}
        </button>
      </div>
    </section>
  );
}

function SurfaceShell({ icon, title, state, children, onRefresh }: { icon: React.ReactNode; title: string; state: LoadState; children: React.ReactNode; onRefresh: () => void }) {
  return <CockpitShell icon={icon} title={title} state={state} onRefresh={onRefresh}>{children}</CockpitShell>;
}

function CockpitShell({ icon, title, state, children, onRefresh }: { icon: React.ReactNode; title: string; state: LoadState; children: React.ReactNode; onRefresh: () => void }) {
  const connectionStatus = state.token ? "Connected" : state.message.toLowerCase().includes("unavailable") ? "Local API down" : "Reconnect needed";
  const shouldShowBanner = state.busy || !state.token || /error|failed|unavailable|configure|reconnect|stale/i.test(state.message);
  return (
    <div className="cf-cockpit h-full overflow-auto">
      <div className="cf-shell">
        <header className="cf-topbar">
          <div className="cf-title-lockup">
            <span className="cf-surface-icon">{icon}</span>
            <div>
              <h1 className="cf-title">{title}</h1>
              <p className="cf-subtitle">Local Mission Control</p>
            </div>
          </div>
          <div className="cf-diagnostics">
            <span>Codex Fleet UI build: {UI_BUILD_MARKER}</span>
          </div>
          <div className="cf-actions">
            <StatusChip status={connectionStatus} />
            <IconButton label="Refresh" disabled={state.busy} onClick={onRefresh}>
              <RefreshCw className="size-3.5" />
            </IconButton>
          </div>
        </header>
        {shouldShowBanner ? <ConnectionBanner state={state} /> : null}
        {children}
      </div>
    </div>
  );
}

function ConnectionBanner({ state }: { state: LoadState }) {
  const message = state.message.toLowerCase();
  const tone = message.includes("error") || message.includes("failed") || message.includes("unavailable") ? "bad" : message.includes("stale") || !state.token ? "warn" : "good";
  const Icon = tone === "bad" ? AlertCircle : tone === "good" ? CheckCircle2 : Clock3;
  return (
    <div className={`cf-banner cf-banner-${tone}`}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <span>{state.busy ? "Working..." : state.message}</span>
    </div>
  );
}

function IconButton({ label, disabled, onClick, children }: { label: string; disabled?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" className="cf-icon-button" aria-label={label} title={label} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  );
}

function CommandCenter({ dashboard }: { dashboard: ProjectDashboardView }) {
  const hasBlocker = dashboard.blocker !== "No current blocker";
  const stateTone = hasBlocker ? "bad" : dashboard.latestRunState === "No runs yet" ? "warn" : ["Done", "Completed", "Human Review"].includes(dashboard.latestRunState) ? "good" : "info";
  return (
    <section className="cf-hero">
      <div>
        <p className="cf-project-kicker">Codex project</p>
        <h2 className="cf-project-name">{dashboard.projectName}</h2>
        <p className="cf-path" title={dashboard.repoPath}>{formatPath(dashboard.repoPath)}</p>
        <div className="cf-summary-grid">
          <SummaryItem label="Workflow" value={dashboard.workflowMode} icon={<Sparkles className="size-4" />} />
          <SummaryItem label="Model" value={dashboard.model} icon={<Bot className="size-4" />} />
          <SummaryItem label="Reasoning" value={dashboard.reasoning} icon={<Activity className="size-4" />} />
          <SummaryItem label="Runner" value="App Server" icon={<Network className="size-4" />} />
        </div>
      </div>
      <div className={`cf-state-card cf-state-${stateTone}`}>
        <div>
          <div className="cf-inline">
            {hasBlocker ? <AlertCircle className="size-4" /> : <PlayCircle className="size-4" />}
            <span className="cf-label">{hasBlocker ? "Needs attention" : "Current state"}</span>
          </div>
          <h3 className="cf-state-title">{hasBlocker ? "Blocked" : dashboard.latestRunState}</h3>
          <p className="cf-next-action">{hasBlocker ? nextActionForBlocker(dashboard.blocker) : "Move a configured work item to Ready to start or continue agent work."}</p>
        </div>
        <div className="cf-summary-grid">
          <SummaryItem label="Agents" value={String(dashboard.activeAgents)} />
          <SummaryItem label="Runs" value={String(dashboard.runCount)} />
          <SummaryItem label="Artifacts" value={String(dashboard.artifactCount)} />
          <SummaryItem label="Tokens" value={dashboard.tokenUsage} />
        </div>
        {hasBlocker ? <p className="mt-3 text-sm leading-6">{dashboard.blocker}</p> : null}
      </div>
    </section>
  );
}

function TaskGraph({ nodes }: { nodes: TaskGraphNodeView[] }) {
  const lanes = taskLanes(nodes);
  return (
    <Panel title="Task flow" action={<span className="cf-mini">{nodes.length} nodes</span>}>
      {nodes.length === 0 ? (
        <EmptyState icon={<Network className="size-5" />} title="No task graph yet" body="A configured Ready parent will create planner and child task nodes here." />
      ) : (
        <div className="cf-flow">
          {lanes.map((lane) => (
            <div key={lane.id} className="cf-lane">
              <div className="cf-lane-title">
                <span className={`cf-role-dot ${roleClass(lane.role)}`} />
                <span>{lane.title}</span>
              </div>
              {lane.nodes.length ? lane.nodes.map((node) => (
                <div key={node.id} className={`cf-node ${roleClass(node.role)}`}>
                  <div className="cf-inline justify-between">
                    <span className="cf-node-key" title={node.id}>{shortIdentifier(node.key)}</span>
                    <RunStatusPill status={node.runStatus || node.state} />
                  </div>
                  <div className="mt-2">
                    <RoleBadge role={node.role} />
                    {node.legacy ? <span className="ml-2"><StatusChip status="Legacy runner" /></span> : null}
                  </div>
                  <div className="cf-node-meta">
                    <CompactDataRow label="Workflow" value={node.workflow} />
                    <CompactDataRow label="Parent" value={shortIdentifier(node.parent)} />
                    <CompactDataRow label="Depth" value={node.depth} />
                  </div>
                  <ExpandableRawPayload label="Details" payload={{ id: node.id, key: node.key, parent: node.parent, workflow: node.workflow, state: node.state }} />
                </div>
              )) : (
                <div className={`cf-node ${roleClass(lane.role)}`}>
                  <RoleBadge role={lane.role} />
                  <p className="mt-3 text-xs text-custom-text-400">{lane.empty}</p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function AgentRunBoard({ runs, onOpenChat }: { runs: CodexFleetRun[]; onOpenChat: (run: CodexFleetRun) => void }) {
  const latestByRole = new Map<string, CodexFleetRun>();
  for (const run of runs) {
    const role = normalizeRole(value(run.agent_role, "orchestrator"));
    if (!latestByRole.has(role)) latestByRole.set(role, run);
  }
  const roles = roleOrder.filter((role) => latestByRole.has(role));
  return (
    <Panel title="Agent chats" action={<span className="cf-mini">{roles.length} agents with runs</span>}>
      {roles.length === 0 ? (
        <EmptyState icon={<MessageSquare className="size-5" />} title="No agent chats yet" body="When Planner, Implementer, Quality Reviewer, Test Agent, or Delivery Manager runs, their chat will be available here." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {roles.map((role) => {
            const run = latestByRole.get(role)!;
            const row = toRunRow(run);
            return (
              <div key={role} className={`cf-node ${roleClass(role)}`}>
                <div className="cf-inline justify-between">
                  <RoleBadge role={role} />
                  <RunStatusPill status={row.status} />
                </div>
                <div className="cf-node-meta">
                  <CompactDataRow label="Task" value={row.task} />
                  <CompactDataRow label="Model" value={`${row.model} / ${row.reasoning}`} />
                  <CompactDataRow label="Latest" value={row.latestEvent} />
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className="cf-button" onClick={() => onOpenChat(run)}>Open Chat</button>
                  <button type="button" className="cf-button-secondary" onClick={() => onOpenChat(run)}>Artifacts</button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

function AgentActivity({ events, runs }: { events: Record<string, unknown>[]; runs: CodexFleetRun[] }) {
  const timeline = toTimeline(events, runs).slice(0, 80);
  return (
    <Panel title="Agent timeline" action={<span className="cf-mini">{timeline.length} events</span>}>
      {timeline.length === 0 ? (
        <EmptyState icon={<MessageSquare className="size-5" />} title="No activity yet" body="Planner, implementer, reviewer, and delivery events will appear here as Codex Fleet works." />
      ) : (
        <div className="cf-timeline">
          {timeline.map((event) => (
            <div key={event.id} className="cf-event">
              <span className={`cf-role-dot ${roleClass(event.role)}`} />
              <div>
                <div className="cf-inline">
                  <RoleBadge role={event.role} />
                  <span className="cf-event-text">{event.text}</span>
                </div>
                <ExpandableRawPayload label="Details" payload={event.payload} />
              </div>
              <MiniTimestamp value={event.time} />
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function NeedsAttention({ runs }: { runs: RunRowView[] }) {
  return (
    <Panel title="Needs attention" action={<span className="text-xs text-custom-text-400">{runs.length} items</span>}>
      {runs.length === 0 ? (
        <EmptyState icon={<CheckCircle2 className="size-5" />} title="No current blocker" body="Blocked, failed, rework, and needs-input runs will appear here with the next action." />
      ) : (
        <div className="grid gap-2">
          {runs.map((run) => (
            <div key={run.id} className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <AgentBadge role={run.role} />
                <span className="font-mono text-sm text-custom-text-100">{run.task}</span>
                <RunStatusPill status={run.status} />
              </div>
              <p className="mt-2 text-sm text-amber-100">{run.blocker}</p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function RecentRuns({ runs }: { runs: RunRowView[] }) {
  return (
    <Panel title="Recent runs" action={<span className="text-xs text-custom-text-400">{runs.length} shown</span>}>
      {runs.length === 0 ? (
        <EmptyState icon={<PlayCircle className="size-5" />} title="No runs yet" body="Move a configured work item to Ready to create the first run." />
      ) : (
        <div className="grid gap-2">
          {runs.map((run) => (
            <CompactRunCard key={run.id} run={run} />
          ))}
        </div>
      )}
    </Panel>
  );
}

function RecentArtifacts({ groups, runsExist }: { groups: ArtifactGroupView[]; runsExist: boolean }) {
  return (
    <Panel title="Recent artifacts" action={<span className="text-xs text-custom-text-400">{groups.length} runs</span>}>
      {groups.length === 0 ? (
        <EmptyState
          icon={<Archive className="size-5" />}
          title={runsExist ? "Runs exist, but no artifacts were attached" : "No artifacts yet"}
          body={runsExist ? "Artifact capture should attach transcripts, summaries, logs, screenshots, or changed-file manifests for future runs." : "Artifacts will appear after agents produce transcripts, logs, summaries, or reports."}
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {groups.map((group) => <ArtifactGroup key={group.run.id} group={group} compact />)}
        </div>
      )}
    </Panel>
  );
}

function ActiveAgentsStrip({ agents }: { agents: AgentSessionView[] }) {
  return (
    <Panel title="Active now" action={<span className="cf-mini">{agents.length} running</span>}>
      {agents.length === 0 ? (
        <EmptyState icon={<Bot className="size-5" />} title="No active agents" body="Running App Server sessions will appear here with task, model, reasoning, branch, and current state." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {agents.map((agent) => (
            <div key={agent.id} className={`cf-node ${roleClass(agent.role)}`}>
              <div className="cf-inline justify-between">
                <RoleBadge role={agent.role} />
                <RunStatusPill status={agent.status} />
              </div>
              <div className="cf-node-meta">
                <CompactDataRow label="Task" value={agent.task} />
                <CompactDataRow label="Model" value={`${agent.model} / ${agent.reasoning}`} />
                <CompactDataRow label="Branch" value={agent.branch} />
                <CompactDataRow label="Last event" value={agent.lastEvent} />
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function AgentRoster({ agents }: { agents: AgentSessionView[] }) {
  const byRole = new Map<string, AgentSessionView>();
  for (const agent of agents) {
    if (!byRole.has(agent.role)) byRole.set(agent.role, agent);
  }
  return (
    <Panel title="Agent roster" action={<span className="cf-mini">role lanes</span>}>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {roleOrder.map((role) => {
          const agent = byRole.get(role);
          return (
            <div key={role} className={`cf-node ${roleClass(role)}`}>
              <div className="cf-inline justify-between">
                <RoleBadge role={role} />
                <RunStatusPill status={agent ? agent.status : "Idle"} />
              </div>
              <div className="cf-node-meta">
                <CompactDataRow label="Last task" value={agent ? shortIdentifier(agent.task) : "Idle"} />
                <CompactDataRow label="Model" value={agent ? `${agent.model} / ${agent.reasoning}` : "Waiting"} />
                <CompactDataRow label="Last event" value={agent ? agent.lastEvent : role === "planner" ? "Planner sessions appear after a Plan and execute task enters Ready." : "No recent session for this role."} />
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function RecentAgentSessions({ agents }: { agents: AgentSessionView[] }) {
  return (
    <Panel title="Recent sessions" action={<span className="cf-mini">{agents.length} shown</span>}>
      {agents.length === 0 ? (
        <EmptyState icon={<Bot className="size-5" />} title="No agent history yet" body="Move a configured work item to Ready and Codex Fleet will create planner and worker sessions here." />
      ) : (
        <div className="cf-table-wrap">
          <table className="cf-table">
            <thead>
              <tr>
                {["Task", "Role", "Status", "Model", "Reasoning", "Branch", "Worktree", "Last event"].map((header) => <th key={header}>{header}</th>)}
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.id}>
                  <td className="cf-code">{shortIdentifier(agent.task)}</td>
                  <td><RoleBadge role={agent.role} /></td>
                  <td><RunStatusPill status={agent.status} /></td>
                  <td>{agent.model}</td>
                  <td>{agent.reasoning}</td>
                  <td>{shortPath(agent.branch)}</td>
                  <td title={agent.worktree}>{formatPath(agent.worktree)}</td>
                  <td>{agent.lastEvent}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function RunFilters({ rows, filters, onChange }: { rows: RunRowView[]; filters: { status: string; role: string; task: string; model: string; search: string }; onChange: (next: { status: string; role: string; task: string; model: string; search: string }) => void }) {
  const statuses = unique(["all", ...rows.map((row) => row.status)]);
  const roles = unique(["all", ...rows.map((row) => normalizeRole(row.role))]);
  const models = unique(["", ...rows.map((row) => row.model).filter((model) => model !== "Unavailable")]);
  const patch = (next: Partial<typeof filters>) => onChange({ ...filters, ...next });
  return (
    <div className="cf-filter-bar">
      <select className="cf-select" value={filters.status} onChange={(event) => patch({ status: event.target.value })}>
        {statuses.map((status) => <option key={status} value={status}>{status === "all" ? "All statuses" : statusLabel(status)}</option>)}
      </select>
      <select className="cf-select" value={filters.role} onChange={(event) => patch({ role: event.target.value })}>
        {roles.map((role) => <option key={role} value={role}>{role === "all" ? "All roles" : agentLabel(role)}</option>)}
      </select>
      <input className="cf-input" value={filters.task} placeholder="Task" onChange={(event) => patch({ task: event.target.value })} />
      <select className="cf-select" value={filters.model} onChange={(event) => patch({ model: event.target.value })}>
        {models.map((model) => <option key={model || "all"} value={model}>{model ? model : "All models"}</option>)}
      </select>
      <input className="cf-input" value={filters.search} placeholder="Search runs" onChange={(event) => patch({ search: event.target.value })} />
    </div>
  );
}

function RunsTable({ rows, onSelect }: { rows: RunRowView[]; onSelect: (run: RunRowView) => void }) {
  if (!rows.length) return <EmptyState icon={<PlayCircle className="size-5" />} title="No runs recorded" body="Move a configured work item to Ready to start the first Codex run." />;
  return (
    <div className="cf-table-wrap">
      <table className="cf-table">
        <thead>
          <tr>
            {["Task", "Role", "Status", "Model", "Reasoning", "Started", "Duration", "Files", "Artifacts", "Open"].map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="cursor-pointer" onClick={() => onSelect(row)}>
              <td className="cf-code">{shortIdentifier(row.task)}</td>
              <td><RoleBadge role={row.role} /></td>
              <td><RunStatusPill status={row.status} /></td>
              <td>{row.model}</td>
              <td>{row.reasoning}</td>
              <td>{row.started}</td>
              <td>{row.duration}</td>
              <td>{row.run.changed_files?.length || 0}</td>
              <td>{row.artifacts}</td>
              <td>
                <button type="button" className="cf-button-secondary" onClick={(event) => { event.stopPropagation(); onSelect(row); }}>
                  Open
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunDetailDrawer({ projectId, run, onClose }: { projectId: string; run: CodexFleetRun; onClose: () => void }) {
  const [messages, setMessages] = useState<CodexFleetRunMessage[]>(run.transcript_preview || []);
  const [tab, setTab] = useState<"chat" | "timeline" | "files" | "artifacts" | "settings" | "raw">("chat");
  const row = toRunRow(run);

  useEffect(() => {
    let cancelled = false;
    ensureCodexFleetLocalConnection()
      .then((local) => new CodexFleetLocalApi({ baseUrl: local.apiUrl, token: local.token || null }).runTranscript(run.id, { plane_project_id: projectId }, tab === "raw" ? "raw" : tab === "timeline" ? "timeline" : "chat"))
      .then((result) => {
        if (!cancelled) setMessages(result.messages || []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [projectId, run.id, tab]);

  return (
    <div className="cf-cockpit cf-drawer-backdrop">
      <aside className="cf-drawer">
        <div className="cf-drawer-header">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="cf-title cf-code">{shortIdentifier(row.task)}</h2>
                <RoleBadge role={row.role} />
                <RunStatusPill status={row.status} />
                {row.legacy ? <StatusChip status="Legacy runner" /> : null}
              </div>
              <p className="cf-subtitle">{row.model} / {row.reasoning} / {row.latestEvent}</p>
            </div>
            <button type="button" className="cf-icon-button" aria-label="Close run details" onClick={onClose}>
              <X className="size-4" />
            </button>
          </div>
          <div className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
            <CopyableCode label="Branch" value={row.branch} />
            <CopyableCode label="Worktree" value={row.worktree} />
          </div>
        </div>
        <div className="cf-tabs">
          {(["chat", "timeline", "files", "artifacts", "settings", "raw"] as const).map((name) => (
            <button key={name} type="button" className={`cf-tab ${tab === name ? "cf-tab-active" : ""}`} onClick={() => setTab(name)}>
              {name[0].toUpperCase() + name.slice(1)}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {tab === "chat" ? <ChatTranscript messages={messages.map(toTranscriptMessage)} /> : null}
          {tab === "timeline" ? <AgentActivity events={records(run.events)} runs={[]} /> : null}
          {tab === "files" ? <RunFiles run={row} raw={run} /> : null}
          {tab === "artifacts" ? <RunArtifacts run={run} /> : null}
          {tab === "settings" ? <RunSettings run={run} /> : null}
          {tab === "raw" ? <RawRun run={run} /> : null}
        </div>
      </aside>
    </div>
  );
}

function ChatTranscript({ messages }: { messages: TranscriptMessageView[] }) {
  if (!messages.length) return <EmptyState icon={<MessageSquare className="size-5" />} title="No transcript recorded" body="Future App Server sessions will stream user prompts, assistant messages, tool calls, command results, and final answers here." />;
  return (
    <div className="cf-chat">
      {messages.map((message) => (
        <div key={message.id} className={`cf-message ${message.kind === "chat_user" || message.kind === "user" ? "cf-message-user" : message.kind === "error" || message.kind === "needs_input" ? "cf-message-error" : "cf-message-agent"} ${roleClass(message.role)}`}>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <RoleBadge role={message.role} />
            <span className="text-xs uppercase tracking-normal text-custom-text-400">{message.kind.replace("_", " ")}</span>
            <MiniTimestamp value={message.time} />
          </div>
          {message.kind === "tool_result" ? (
            <details>
              <summary className="cursor-pointer text-sm font-medium text-custom-text-100">Command output</summary>
              <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-custom-text-200">{message.content}</p>
            </details>
          ) : (
            <p className="whitespace-pre-wrap break-words text-sm leading-6 text-custom-text-100">{message.content}</p>
          )}
          {message.artifactPath !== "Unavailable" ? <p className="mt-2 break-all text-xs text-custom-text-300">Full artifact: {message.artifactPath}</p> : null}
        </div>
      ))}
    </div>
  );
}

function RunFiles({ run, raw }: { run: RunRowView; raw: CodexFleetRun }) {
  const changedFiles = raw.changed_files || [];
  return (
    <div className="grid gap-4">
      <Panel title="Workspace" compact>
        <div className="grid gap-2 text-sm text-custom-text-300">
          <CompactDataRow label="Branch" value={run.branch} />
          <CompactDataRow label="Worktree" value={run.worktree} />
          <CompactDataRow label="Thread" value={value(raw.codex_thread_id)} />
          <CompactDataRow label="Turn" value={value(raw.codex_turn_id)} />
        </div>
      </Panel>
      <Panel title="Changed files" compact>
        {changedFiles.length === 0 ? <EmptyState icon={<FileText className="size-5" />} title="No changed files recorded" body="Changed file manifests will appear here when the runner reports them." /> : (
          <div className="grid gap-2">
            {changedFiles.map((file) => <div key={file} className="break-all rounded-md border border-custom-border-200 px-3 py-2 text-xs text-custom-text-200">{file}</div>)}
          </div>
        )}
      </Panel>
    </div>
  );
}

function RunArtifacts({ run }: { run: CodexFleetRun }) {
  const artifacts = run.artifacts || [];
  if (!artifacts.length) return <EmptyState icon={<Archive className="size-5" />} title="No artifacts recorded" body="Transcripts, summaries, logs, screenshots, and reports will appear here when attached by the runner." />;
  return <div className="grid gap-2">{artifacts.map((artifact) => <ArtifactRow key={artifact.id} artifact={artifact} />)}</div>;
}

function RunSettings({ run }: { run: CodexFleetRun }) {
  const settings = run.settings || {};
  const rows = [
    ["Workflow mode", workflowLabel(value(run.workflow_mode || settings.workflow_mode))],
    ["Parent workflow", workflowLabel(value(run.parent_workflow_mode || settings.parent_workflow_mode))],
    ["Agent role", agentLabel(value(run.agent_role || settings.agent_role))],
    ["Prompt role", agentLabel(value(run.prompt_role || settings.agent_role))],
    ["Settings source", value(run.settings_source || settings.settings_source)],
    ["Model", value(run.model || settings.default_model || settings.model)],
    ["Reasoning", value(run.reasoning_effort || settings.reasoning_effort)],
    ["Sandbox", value(settings.sandbox_mode)],
    ["Approval", value(settings.approval_policy)],
    ["Max depth", value(settings.max_depth)],
    ["Max parallel agents", value(settings.max_parallel_agents)],
  ];
  return (
    <div className="grid gap-4">
      <Panel title="Effective settings" compact>
        <div className="grid gap-2 text-sm text-custom-text-300">
          {rows.map(([label, rowValue]) => <CompactDataRow key={label} label={label} value={rowValue} />)}
        </div>
      </Panel>
      <ExpandableRawPayload label="Raw settings" payload={settings} />
    </div>
  );
}

function ArtifactGroup({ group, compact = false }: { group: ArtifactGroupView; compact?: boolean }) {
  return (
    <div className="rounded-md border border-custom-border-200 bg-custom-background-100 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold text-custom-text-100">{group.run.task}</span>
          <AgentBadge role={group.run.role} />
          <RunStatusPill status={group.run.status} />
        </div>
        <span className="text-xs text-custom-text-400">{group.artifacts.length} artifacts / {group.changedFiles.length} files</span>
      </div>
      <div className={`mt-3 grid gap-2 ${compact ? "" : "md:grid-cols-2"}`}>
        {group.artifacts.map((artifact) => <ArtifactRow key={artifact.id} artifact={artifact} />)}
        {group.changedFiles.slice(0, compact ? 3 : 20).map((file) => (
          <div key={file} className="break-all rounded-md border border-custom-border-200 px-3 py-2 text-xs text-custom-text-300">
            <span className="font-medium text-custom-text-100">changed file</span> {file}
          </div>
        ))}
      </div>
    </div>
  );
}

function ArtifactRow({ artifact }: { artifact: CodexFleetRunArtifact }) {
  return (
    <div className="rounded-md border border-custom-border-200 bg-custom-background-90 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Archive className="size-3.5 text-custom-text-400" />
        <span className="text-xs font-semibold text-custom-text-100">{artifactKindLabel(artifact.kind)}</span>
        <span className="text-xs text-custom-text-400">{formatBytes(artifact.size_bytes)}</span>
      </div>
      <p className="mt-1 break-all text-xs text-custom-text-300">{artifact.path}</p>
      {artifact.sha256 ? <p className="mt-1 break-all text-[11px] text-custom-text-400">sha256 {artifact.sha256}</p> : null}
    </div>
  );
}

function CompactRunCard({ run }: { run: RunRowView }) {
  return (
    <div className="rounded-md border border-custom-border-200 bg-custom-background-100 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm text-custom-text-100">{run.task}</span>
          <AgentBadge role={run.role} />
        </div>
        <RunStatusPill status={run.status} />
      </div>
      <div className="mt-2 grid gap-1 text-xs text-custom-text-300 sm:grid-cols-2">
        <CompactDataRow label="Model" value={`${run.model} / ${run.reasoning}`} />
        <CompactDataRow label="Latest" value={run.latestEvent} />
      </div>
    </div>
  );
}

function Panel({ title, action, children, compact = false }: { title: string; action?: React.ReactNode; children: React.ReactNode; compact?: boolean }) {
  return (
    <section className={`cf-panel ${compact ? "p-3" : "cf-panel-pad"}`}>
      <SectionHeader title={title} action={action} />
      <div className="mt-3">{children}</div>
    </section>
  );
}

function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="cf-section-header">
      <h2 className="cf-section-title">{title}</h2>
      {action}
    </div>
  );
}

function MetricTile({ label, value: text, icon, tone = "neutral" }: { label: string; value: string; icon: React.ReactNode; tone?: "neutral" | "good" | "warn" | "bad" }) {
  return (
    <div className={`cf-health-tile ${toneClass(tone)}`}>
      <div className="flex items-center gap-2 text-xs font-medium text-custom-text-400">
        {icon}
        <span>{label}</span>
      </div>
      <p className="mt-2 break-words text-sm font-semibold text-custom-text-100">{text}</p>
    </div>
  );
}

function EmptyState({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div className="rounded-md border border-dashed border-custom-border-200 bg-custom-background-100 px-4 py-5 text-center">
      <div className="mx-auto grid size-9 place-items-center rounded-md border border-custom-border-200 text-custom-text-400">{icon}</div>
      <h3 className="mt-3 text-sm font-semibold text-custom-text-100">{title}</h3>
      <p className="mx-auto mt-1 max-w-xl text-sm leading-6 text-custom-text-300">{body}</p>
    </div>
  );
}

function InfoNotice({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-custom-border-200 bg-custom-background-90 px-3 py-2 text-sm text-custom-text-300">
      {children}
    </div>
  );
}

function SettingsSection({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-custom-border-200 bg-custom-background-90 p-4">
      <div className="flex items-center gap-2">
        <span className="text-custom-text-400">{icon}</span>
        <h2 className="text-sm font-semibold text-custom-text-100">{title}</h2>
      </div>
      <div className="mt-4 grid gap-3">{children}</div>
    </section>
  );
}

function CompactDataRow({ label, value: rowValue }: { label: string; value: unknown }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-normal text-custom-text-400">{label}</p>
      <p className="mt-0.5 break-words text-xs text-custom-text-100">{value(rowValue)}</p>
    </div>
  );
}

function ExpandableRawPayload({ payload, label = "Details" }: { payload: unknown; label?: string }) {
  return (
    <details className="cf-raw mt-2 text-xs text-custom-text-300">
      <summary className="cursor-pointer">{label}</summary>
      <pre>{JSON.stringify(payload || {}, null, 2)}</pre>
    </details>
  );
}

function StatusPill({ status }: { status: string }) {
  return <StatusChip status={status} />;
}

function RunStatusPill({ status }: { status: string }) {
  return <StatusChip status={status} />;
}

function AgentBadge({ role }: { role: string }) {
  return <RoleBadge role={role} />;
}

function RoleBadge({ role }: { role: string }) {
  const normalized = normalizeRole(role);
  return <span className={`cf-role-badge ${roleClass(normalized)}`}><span className={`cf-role-dot ${roleClass(normalized)}`} />{agentLabel(normalized)}</span>;
}

function StatusChip({ status }: { status: string }) {
  return <span className={`cf-status-chip ${statusClass(status)}`}>{statusLabel(status)}</span>;
}

function Field({ label, value: text, onChange }: { label: string; value: string; onChange: (next: string) => void }) {
  return (
    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
      {label}
      <input className="cf-input" value={text} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function NumberField({ label, value: numberValue, onChange }: { label: string; value: number; onChange: (next: number) => void }) {
  return (
    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
      {label}
      <input type="number" min={1} className="cf-input" value={numberValue} onChange={(event) => onChange(Number(event.target.value) || 1)} />
    </label>
  );
}

function Select({ label, value: selected, options, onChange }: { label: string; value: string; options: (string | [string, string])[]; onChange: (next: string) => void }) {
  return (
    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
      {label}
      <select className="cf-select" value={selected} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => {
          const [optionValue, optionLabel] = Array.isArray(option) ? option : [option, option];
          return <option key={optionValue} value={optionValue}>{optionLabel}</option>;
        })}
      </select>
    </label>
  );
}

function SummaryItem({ label, value: text, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <div className="cf-summary-item">
      <div className="cf-inline cf-label">{icon}{label}</div>
      <div className="cf-summary-value">{text}</div>
    </div>
  );
}

function MiniTimestamp({ value: timestamp }: { value: string }) {
  return <span className="cf-event-time">{timestamp}</span>;
}

function CopyableCode({ label, value: code }: { label: string; value: string }) {
  const copy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) void navigator.clipboard.writeText(code);
  };
  return (
    <div className="min-w-0">
      <p className="cf-label">{label}</p>
      <div className="cf-inline mt-1">
        <code className="cf-code min-w-0 truncate text-xs text-custom-text-100" title={code}>{formatPath(code)}</code>
        <button type="button" className="cf-icon-button h-7 w-7" aria-label={`Copy ${label}`} title={`Copy ${label}`} onClick={copy}>
          <Copy className="size-3.5" />
        </button>
      </div>
    </div>
  );
}

function RawRun({ run }: { run: CodexFleetRun }) {
  return (
    <div className="cf-raw">
      <pre>{JSON.stringify(run, null, 2)}</pre>
    </div>
  );
}

function taskLanes(nodes: TaskGraphNodeView[]) {
  const lanes = [
    { id: "parent", title: "Parent", role: "orchestrator", empty: "The human-requested task anchors the workflow.", nodes: [] as TaskGraphNodeView[] },
    { id: "planner", title: "Planner", role: "planner", empty: "Planner appears when planning starts.", nodes: [] as TaskGraphNodeView[] },
    { id: "workers", title: "Workers", role: "implementer", empty: "Implementer and scout tasks appear after planning.", nodes: [] as TaskGraphNodeView[] },
    { id: "reviewers", title: "Reviewers", role: "quality_reviewer", empty: "Quality, test, and security review tasks appear when validation is required.", nodes: [] as TaskGraphNodeView[] },
    { id: "delivery", title: "Delivery", role: "delivery_manager", empty: "Delivery task appears after full-auto completion.", nodes: [] as TaskGraphNodeView[] },
  ];
  for (const node of nodes) {
    const role = normalizeRole(node.role);
    if (role === "orchestrator") lanes[0].nodes.push(node);
    else if (role === "planner") lanes[1].nodes.push(node);
    else if (role === "code_scout" || role === "implementer") lanes[2].nodes.push(node);
    else if (role.includes("reviewer")) lanes[3].nodes.push(node);
    else if (role === "delivery_manager") lanes[4].nodes.push(node);
    else lanes[2].nodes.push(node);
  }
  return lanes;
}

function nextActionForBlocker(blocker: string): string {
  const normalized = blocker.toLowerCase();
  if (normalized.includes("planner output") || normalized.includes("needs input")) {
    return "Answer the planner question in the work item comments, then move the item to Ready.";
  }
  if (normalized.includes("configure")) return "Configure Codex Fleet for this project before running agents.";
  if (normalized.includes("token") || normalized.includes("reconnect")) return "Reconnect Codex Fleet to enable local actions.";
  return "Open the related work item, resolve the blocker, then move it back to Ready.";
}

function toDashboardView(dashboard: Record<string, unknown>, runs: CodexFleetRun[]): ProjectDashboardView {
  const project = object(dashboard.project);
  const settings = object(project.codex_settings);
  const latest = runs[0];
  const rootTasks = records(dashboard.root_tasks);
  const rootState = value(rootTasks[0]?.state || rootTasks[0]?.terminal_outcome, "");
  const artifacts = records(dashboard.artifacts);
  const activeAgents = records(dashboard.active_agents);
  const blocker = runs.find((run) => run.blocker_text || run.error)?.blocker_text || runs.find((run) => run.error)?.error || "No current blocker";
  return {
    projectName: value(project.name, "Codex Fleet project"),
    repoPath: value(project.repo_path || dashboard.repo, "No repo linked"),
    workflowMode: workflowLabel(value(settings.workflow_mode, "plan_execute")),
    model: value(settings.default_model || latest?.model, "Unavailable"),
    reasoning: value(settings.reasoning_effort || latest?.reasoning_effort, "Unavailable"),
    activeAgents: activeAgents.length,
    latestRunState: rootState ? statusLabel(rootState) : latest ? statusLabel(latest.status) : "No runs yet",
    blocker,
    tokenUsage: tokenUsage(object(dashboard.token_usage)),
    runCount: runs.length,
    artifactCount: artifacts.length || runs.reduce((total, run) => total + (run.artifact_count || run.artifacts?.length || 0), 0),
  };
}

function toTaskNodes(dashboard: Record<string, unknown>): TaskGraphNodeView[] {
  const rootTasks = records(dashboard.root_tasks);
  const childTasks = records(dashboard.child_tasks);
  return [...rootTasks, ...childTasks].map((task) => {
    const settings = object(task.settings);
    const latestRun = object(task.latest_run);
    return {
      id: value(task.item_id || task.identifier),
      key: value(task.identifier || task.parent_identifier || task.item_id),
      role: value(task.role || settings.agent_role, task.parent_item_id ? "implementer" : "orchestrator"),
      state: value(task.state || task.terminal_outcome || latestRun.status, "Waiting"),
      workflow: workflowLabel(value(settings.workflow_mode || task.approval_mode, "execute_only")),
      parent: value(task.parent_identifier || task.parent_item_id, "top-level"),
      depth: value(task.depth, "0"),
      runStatus: value(latestRun.status, "No run"),
      legacy: latestRun.runner_name === "CodexCliRunner",
    };
  });
}

function toAgentSession(input: Record<string, unknown> | CodexFleetRun): AgentSessionView {
  const run = input as CodexFleetRun & Record<string, unknown>;
  const lastEvent = object(run.last_event);
  return {
    id: value(run.run_id || run.id || run.item_id),
    role: value(run.role || run.agent_role, "orchestrator"),
    name: value(run.name || run.agent_name, agentLabel(value(run.role || run.agent_role, "orchestrator"))),
    task: value(run.identifier || run.item_id),
    status: value(run.status, "Idle"),
    model: value(run.model),
    reasoning: value(run.reasoning_effort),
    branch: value(run.branch_name),
    worktree: value(run.worktree_path),
    lastEvent: value(lastEvent.content || run.latest_event_text, "No event recorded"),
    legacy: run.runner_name === "CodexCliRunner" || run.is_legacy_runner === true,
  };
}

function toRunRow(run: CodexFleetRun): RunRowView {
  return {
    run,
    id: run.id,
    task: value(run.identifier || run.item_id || run.id),
    role: value(run.agent_role, "orchestrator"),
    status: value(run.status),
    model: value(run.model),
    reasoning: value(run.reasoning_effort),
    started: formatTime(run.started_at),
    duration: formatDuration(run.duration_seconds),
    branch: value(run.branch_name),
    worktree: value(run.worktree_path),
    artifacts: String(run.artifact_count ?? run.artifacts?.length ?? 0),
    tokens: tokenUsage(run.token_usage),
    latestEvent: value(run.latest_event_text, "No event recorded"),
    blocker: value(run.blocker_text || run.error, "No current blocker"),
    legacy: run.is_legacy_runner === true || run.runner_name === "CodexCliRunner",
  };
}

function toTranscriptMessage(message: CodexFleetRunMessage): TranscriptMessageView {
  return {
    id: String(message.id || `${message.run_id}-${message.sequence}`),
    role: value(message.agent_role, "orchestrator"),
    kind: value(message.kind, "system_event"),
    content: value(message.content),
    time: formatTime(message.created_at),
    artifactPath: value(message.artifact_path),
  };
}

function toArtifactGroups(runs: CodexFleetRun[]): ArtifactGroupView[] {
  return runs
    .map((run) => ({ run: toRunRow(run), artifacts: run.artifacts || [], changedFiles: run.changed_files || [] }))
    .filter((group) => group.artifacts.length > 0 || group.changedFiles.length > 0);
}

function toTimeline(events: Record<string, unknown>[], runs: CodexFleetRun[]) {
  const source = events.length ? events : runs.flatMap((run) => run.events || []);
  return source.map((event, index) => {
    const payload = object(event.payload);
    return {
      id: `${value(event.id, String(index))}-${index}`,
      role: value(payload.agent_role || payload.role, "orchestrator"),
      text: value(event.text || eventText(event)),
      time: formatTime(event.created_at),
      payload,
    };
  });
}

function records(valueToRead: unknown): Record<string, unknown>[] {
  if (!Array.isArray(valueToRead)) return [];
  return valueToRead.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item));
}

function object(valueToRead: unknown): Record<string, unknown> {
  if (!valueToRead || typeof valueToRead !== "object" || Array.isArray(valueToRead)) return {};
  return valueToRead as Record<string, unknown>;
}

function value(valueToRead: unknown, fallback = "Unavailable"): string {
  if (valueToRead === null || valueToRead === undefined || valueToRead === "") return fallback;
  if (typeof valueToRead === "string") return valueToRead;
  if (typeof valueToRead === "number" || typeof valueToRead === "boolean") return String(valueToRead);
  return fallback;
}

function workflowLabel(mode: string): string {
  return {
    execute_only: "Execute only",
    plan_only: "Plan only",
    plan_execute: "Plan and execute",
    full_auto: "Full auto",
  }[mode] || value(mode);
}

function statusLabel(status: string): string {
  return value(status).replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function agentLabel(role: string): string {
  return {
    orchestrator: "Orchestrator",
    planner: "Planner",
    code_scout: "Code Scout",
    implementer: "Implementer",
    harness_reviewer: "Quality Reviewer",
    quality_reviewer: "Quality Reviewer",
    security_reviewer: "Security Reviewer",
    token_reviewer: "Quality Reviewer",
    test_reviewer: "Test Agent",
    delivery_manager: "Delivery Manager",
  }[role] || statusLabel(role);
}

function normalizeRole(role: string): string {
  const normalized = value(role, "orchestrator").replace(/-/g, "_");
  return {
    harness_reviewer: "quality_reviewer",
    token_reviewer: "quality_reviewer",
    test_agent: "test_reviewer",
    qa_reviewer: "test_reviewer",
  }[normalized] || normalized;
}

function roleClass(role: string): string {
  return `cf-role-${normalizeRole(role).replace(/_/g, "-")}`;
}

function statusClass(status: string): string {
  const normalized = status.toLowerCase().replace(/\s+/g, "_");
  if (["connected", "done", "human_review", "completed", "success"].includes(normalized)) return "cf-status-good";
  if (["needs_attention", "needs_input", "blocked", "rework", "failed", "stalled", "local_api_down"].includes(normalized)) return "cf-status-bad";
  if (["running", "runner_started", "runner_streaming", "planning", "in_progress"].includes(normalized)) return "cf-status-running";
  if (["reconnect_needed", "legacy_runner", "queued", "ready", "waiting"].includes(normalized)) return "cf-status-warn";
  return "cf-status-neutral";
}

function toneClass(tone: "neutral" | "good" | "warn" | "bad"): string {
  if (tone === "good") return "border-green-500/25 bg-green-500/10";
  if (tone === "warn") return "border-amber-500/25 bg-amber-500/10";
  if (tone === "bad") return "border-red-500/25 bg-red-500/10";
  return "border-custom-border-200 bg-custom-background-100";
}

function tokenUsage(tokenUsageValue: Record<string, unknown> | undefined): string {
  if (!tokenUsageValue || tokenUsageValue.status === "Unavailable") return "Unavailable";
  const total = tokenUsageValue.total_tokens;
  return typeof total === "number" ? total.toLocaleString() : "Unavailable";
}

function formatTime(input: unknown): string {
  const raw = value(input, "");
  if (!raw) return "Unavailable";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleString();
}

function shortIdentifier(input: string): string {
  const text = value(input);
  if (text === "Unavailable" || text.length <= 18) return text;
  const taskMatch = text.match(/[A-Z][A-Z0-9]+-\d+/);
  if (taskMatch) return taskMatch[0];
  return `${text.slice(0, 8)}...${text.slice(-4)}`;
}

function shortPath(input: string): string {
  const text = value(input);
  if (text === "Unavailable") return text;
  const parts = text.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : text;
}

function formatPath(input: string): string {
  const text = value(input);
  if (text === "Unavailable" || text.length < 54) return text;
  const parts = text.split("/").filter(Boolean);
  if (parts.length < 3) return text;
  return `.../${parts.slice(-3).join("/")}`;
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}

function formatDuration(seconds: unknown): string {
  if (typeof seconds !== "number") return "Unavailable";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return remaining ? `${minutes}m ${remaining}s` : `${minutes}m`;
}

function formatBytes(bytes: unknown): string {
  if (typeof bytes !== "number") return "size unavailable";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function artifactKindLabel(kind: string): string {
  return value(kind).replace(/[-_]/g, " ");
}

function eventText(event: Record<string, unknown>): string {
  const kind = value(event.kind, "event");
  const payload = object(event.payload);
  if (kind === "claimed") return `Claimed ${value(payload.identifier || payload.item_id, "work item")}.`;
  if (kind === "runner_started" || kind === "agent_session_started") return "Agent session started.";
  if (kind === "runner_finished" || kind === "agent_session_finished") return "Agent session finished.";
  if (kind === "needs_input") return "Agent needs input.";
  if (kind === "parent_completed") return "Parent task completed.";
  if (kind === "delivery_task_created") return `Delivery task ${value(payload.identifier, "")} created.`;
  return statusLabel(kind) + ".";
}

function friendlyError(error: unknown, fallback: string): string {
  if (error instanceof Error) return error.message;
  return fallback;
}

function WrenchIcon() {
  return <Settings className="size-4" />;
}
