import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";

import { CodexFleetLocalApi, ensureCodexFleetLocalConnection } from "./local-api";
import type {
  CodexFleetAgentAnalytics,
  CodexFleetHarness,
  CodexFleetProject,
  CodexFleetRun,
  CodexFleetWorkflowMode,
  CodexFleetWorkItem,
} from "./local-api";

type BootstrapParams = {
  apiUrl: string;
  token: string;
};

type ProjectMode = "link" | "create";
type ProjectType = "blank" | "simple-web" | "node-next" | "python";
type ReasoningEffort = "low" | "medium" | "high";
type AgentProfile = {
  label: string;
  purpose: string;
  model: string;
  reasoning_effort: ReasoningEffort;
  sandbox_mode: string;
  enabled: boolean;
};

const DEFAULT_AGENT_PROFILES: Record<string, AgentProfile> = {
  planner: {
    label: "Planner",
    purpose: "Splits the parent request into useful child tasks.",
    model: "gpt-5.5",
    reasoning_effort: "medium",
    sandbox_mode: "workspace-write",
    enabled: true,
  },
  code_scout: {
    label: "Code Scout",
    purpose: "Maps unfamiliar code before implementation when needed.",
    model: "gpt-5.4-mini",
    reasoning_effort: "high",
    sandbox_mode: "workspace-write",
    enabled: false,
  },
  implementer: {
    label: "Implementer",
    purpose: "Edits code in an isolated worktree.",
    model: "gpt-5.5",
    reasoning_effort: "low",
    sandbox_mode: "workspace-write",
    enabled: true,
  },
  quality_reviewer: {
    label: "Quality Reviewer",
    purpose: "Checks build quality, harness fit, and context efficiency.",
    model: "gpt-5.4-mini",
    reasoning_effort: "high",
    sandbox_mode: "workspace-write",
    enabled: true,
  },
  security_reviewer: {
    label: "Security Reviewer",
    purpose: "Runs only for security-sensitive changes.",
    model: "gpt-5.5",
    reasoning_effort: "medium",
    sandbox_mode: "workspace-write",
    enabled: false,
  },
  test_reviewer: {
    label: "Test Agent",
    purpose: "Runs the app and records browser proof when available.",
    model: "gpt-5.4-mini",
    reasoning_effort: "high",
    sandbox_mode: "workspace-write",
    enabled: true,
  },
  delivery_manager: {
    label: "Delivery Manager",
    purpose: "Prepares PR/local merge delivery and cleanup.",
    model: "gpt-5.4-mini",
    reasoning_effort: "medium",
    sandbox_mode: "workspace-write",
    enabled: true,
  },
};

const WORKFLOW_OPTIONS: { value: CodexFleetWorkflowMode; label: string }[] = [
  { value: "execute_only", label: "Execute only" },
  { value: "plan_only", label: "Plan only" },
  { value: "plan_execute", label: "Plan and execute" },
  { value: "full_auto", label: "Full auto" },
];

const readBootstrapParams = (): BootstrapParams => {
  if (typeof window === "undefined") return { apiUrl: "", token: "" };
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return {
    apiUrl: params.get("apiUrl") ?? window.localStorage.getItem("codexFleetLocalApiUrl") ?? "",
    token: params.get("token") ?? window.localStorage.getItem("codexFleetLocalToken") ?? "",
  };
};

export default function CodexFleetDashboard() {
  const [apiUrl, setApiUrl] = useState(() => readBootstrapParams().apiUrl || "http://127.0.0.1:18790");
  const [token, setToken] = useState(() => readBootstrapParams().token);
  const [projects, setProjects] = useState<CodexFleetProject[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState(() =>
    typeof window === "undefined" ? "" : window.localStorage.getItem("codexFleetProjectId") ?? ""
  );
  const [items, setItems] = useState<CodexFleetWorkItem[]>([]);
  const [runs, setRuns] = useState<CodexFleetRun[]>([]);
  const [analytics, setAnalytics] = useState<CodexFleetAgentAnalytics | null>(null);
  const [selectedRun, setSelectedRun] = useState<CodexFleetRun | null>(null);
  const [projectHarness, setProjectHarness] = useState<Record<number, CodexFleetHarness>>({});
  const [itemRunStatus, setItemRunStatus] = useState<Record<string, CodexFleetRun | null>>({});
  const [activeItemId, setActiveItemId] = useState<string | null>(null);
  const [message, setMessage] = useState("Connect to codex-fleet.");
  const [busy, setBusy] = useState(false);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newTaskDescription, setNewTaskDescription] = useState("");
  const [newProjectPath, setNewProjectPath] = useState("");
  const [newProjectParentPath, setNewProjectParentPath] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectMode, setNewProjectMode] = useState<ProjectMode>("link");
  const [newProjectType, setNewProjectType] = useState<ProjectType>("blank");
  const [showManualProjectPath, setShowManualProjectPath] = useState(false);
  const [applyHarnessOnCreate, setApplyHarnessOnCreate] = useState(true);
  const [initialGoal, setInitialGoal] = useState("");
  const [startInitialGoal, setStartInitialGoal] = useState(true);
  const [workflowMode, setWorkflowMode] = useState<CodexFleetWorkflowMode>("plan_execute");
  const [defaultModel, setDefaultModel] = useState("gpt-5.5");
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("low");
  const [approvalPolicy] = useState("never");
  const [sandboxMode] = useState("workspace-write");
  const [maxParallelAgents, setMaxParallelAgents] = useState(3);
  const [maxDepth, setMaxDepth] = useState(2);
  const [subagentsEnabled, setSubagentsEnabled] = useState(true);
  const [agentProfiles, setAgentProfiles] = useState<Record<string, AgentProfile>>(DEFAULT_AGENT_PROFILES);

  const api = () => new CodexFleetLocalApi({ baseUrl: apiUrl.trim() || undefined, token: token.trim() || null });
  const selectedProjectNumber = selectedProjectId ? Number(selectedProjectId) : undefined;
  const selectedProject = projects.find((project) => project.id === selectedProjectNumber);
  const fullAutoNeedsSubagents = workflowMode === "full_auto" && !subagentsEnabled;
  const requiredProjectPathMissing =
    (newProjectMode === "link" && !newProjectPath.trim()) ||
    (newProjectMode === "create" && (!newProjectName.trim() || !newProjectParentPath.trim()));
  const createProjectBlocked = busy || requiredProjectPathMissing || !initialGoal.trim() || fullAutoNeedsSubagents || !token.trim();

  const updateAgentProfile = (role: string, update: Partial<AgentProfile>) => {
    setAgentProfiles((current) => ({
      ...current,
      [role]: { ...current[role], ...update },
    }));
  };

  const codexSettingsPayload = () => {
    const enabledRoles = Object.entries(agentProfiles)
      .filter(([, profile]) => profile.enabled)
      .map(([role]) => role);
    const profileSettings = Object.fromEntries(
      Object.entries(agentProfiles).map(([role, profile]) => [
        role,
        {
          model: profile.model,
          reasoning_effort: profile.reasoning_effort,
          sandbox_mode: profile.sandbox_mode,
          enabled: profile.enabled,
        },
      ])
    );
    return {
      runner_mode: "app-server",
      default_model: defaultModel,
      reasoning_effort: reasoningEffort,
      approval_policy: approvalPolicy,
      sandbox_mode: sandboxMode,
      max_parallel_agents: maxParallelAgents,
      max_depth: maxDepth,
      workflow_mode: workflowMode,
      subagents_enabled: subagentsEnabled,
      enabled_agent_roles: enabledRoles,
      agent_profiles: profileSettings,
      subagents: Object.fromEntries(
        Object.entries(agentProfiles).map(([role, profile]) => [
          role,
          {
            model: profile.model,
            reasoning_effort: profile.reasoning_effort,
            sandbox_mode: profile.sandbox_mode,
          },
        ])
      ),
    };
  };

  const refresh = async (projectOverride?: number, connectionOverride?: BootstrapParams) => {
    setBusy(true);
    setMessage("Loading codex-fleet state...");
    try {
      const activeToken = connectionOverride?.token ?? token;
      const activeApiUrl = connectionOverride?.apiUrl ?? apiUrl;
      const client = new CodexFleetLocalApi({ baseUrl: activeApiUrl.trim() || undefined, token: activeToken.trim() || null });
      if (activeToken.trim()) window.localStorage.setItem("codexFleetLocalToken", activeToken.trim());
      if (activeApiUrl.trim()) window.localStorage.setItem("codexFleetLocalApiUrl", activeApiUrl.trim());
      if (selectedProjectId) window.localStorage.setItem("codexFleetProjectId", selectedProjectId);
      const projectResult = await client.projects();
      const savedProject = projectResult.projects.find((project) => project.id === selectedProjectNumber);
      const firstRunnableProject = projectResult.projects.find((project) => project.can_run);
      const projectId =
        projectOverride ??
        (savedProject ? savedProject.id : undefined) ??
        firstRunnableProject?.id ??
        (projectResult.projects.length ? projectResult.projects[0].id : undefined);
      if (projectId && !selectedProjectId) {
        setSelectedProjectId(String(projectId));
        window.localStorage.setItem("codexFleetProjectId", String(projectId));
      }
      const activeProject = projectResult.projects.find((project) => project.id === projectId);
      if (!activeProject?.can_run) {
        setProjects(projectResult.projects);
        setItems([]);
        setRuns([]);
        setAnalytics(null);
        setMessage(activeProject?.status_message ?? "Project needs attention before it can run.");
        return;
      }
      const [readyResult, runResult, analyticsResult, harnessResult] = await Promise.all([
        client.readyWorkItems(projectId),
        client.runs(projectId),
        projectId ? client.agentAnalytics(projectId).catch(() => null) : Promise.resolve(null),
        projectId ? client.planHarness(projectId).catch(() => null) : Promise.resolve(null),
      ]);
      setProjects(projectResult.projects);
      setItems(readyResult.items);
      setRuns(runResult.runs);
      setAnalytics(analyticsResult?.analytics ?? null);
      if (projectId && harnessResult?.harness) {
        setProjectHarness((current) => ({ ...current, [projectId]: harnessResult.harness }));
      }
      setMessage("codex-fleet is connected.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet is not reachable.");
    } finally {
      setBusy(false);
    }
  };

  const runNext = async () => {
    setBusy(true);
    setMessage("Running next Ready item...");
    try {
      const result = await api().runNextReady({
        fake: false,
      }, selectedProjectNumber);
      setMessage(result.dispatched ? `Started ${result.run?.identifier ?? "run"}.` : result.message);
      await refresh(selectedProjectNumber);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not start a run.");
      setBusy(false);
    }
  };

  const runWorkItem = async (itemId: string) => {
    setBusy(true);
    setActiveItemId(itemId);
    setMessage("Running selected Ready item...");
    try {
      const result = await api().runWorkItem(itemId, {
        fake: false,
      }, selectedProjectNumber);
      if (result.run) {
        setSelectedRun(result.run);
        setItemRunStatus((current) => ({ ...current, [itemId]: result.run }));
      }
      setMessage(result.dispatched ? `Started ${result.run?.identifier ?? "run"}.` : result.message);
      await refresh(selectedProjectNumber);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not start that item.");
      setBusy(false);
    } finally {
      setActiveItemId(null);
    }
  };

  const inspectItemStatus = async (itemId: string) => {
    setBusy(true);
    setActiveItemId(itemId);
    setMessage("Loading item run status...");
    try {
      const result = await api().runStatus(itemId, selectedProjectNumber);
      setItemRunStatus((current) => ({ ...current, [itemId]: result.run }));
      if (result.run) setSelectedRun(result.run);
      setMessage(result.run ? `Loaded ${result.run.identifier}.` : "No run recorded for that item.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not load item status.");
    } finally {
      setBusy(false);
      setActiveItemId(null);
    }
  };

  const createTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newTaskTitle.trim()) return;
    setBusy(true);
    setMessage("Creating Ready item...");
    try {
      await api().createWorkItem({
        title: newTaskTitle.trim(),
        description: newTaskDescription.trim() || undefined,
        ...(selectedProjectNumber ? { project_id: selectedProjectNumber } : {}),
      });
      setNewTaskTitle("");
      setNewTaskDescription("");
      await refresh(selectedProjectNumber);
      setMessage("Ready item created.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not create a work item.");
      setBusy(false);
    }
  };

  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (newProjectMode === "link" && !newProjectPath.trim()) return;
    if (newProjectMode === "create" && (!newProjectName.trim() || !newProjectParentPath.trim())) return;
    if (!initialGoal.trim()) {
      setMessage("Add an initial goal so Codex Fleet can create the first work item.");
      return;
    }
    if (fullAutoNeedsSubagents) {
      setMessage("Full auto needs subagents. Enable subagents or choose a lighter automation mode.");
      return;
    }
    if (!token.trim()) {
      setMessage("Reconnect Codex Fleet before creating a local project.");
      return;
    }
    setBusy(true);
    setMessage(newProjectMode === "create" ? "Creating project..." : "Adding project...");
    try {
      const result = await api().createProject({
        ...(newProjectMode === "create"
          ? {
              create_new: true,
              location: newProjectParentPath.trim(),
              folder_slug: newProjectName.trim(),
              project_type: newProjectType,
            }
          : { path: newProjectPath.trim() }),
        name: newProjectName.trim() || undefined,
        apply_harness: applyHarnessOnCreate,
        initial_goal: initialGoal.trim(),
        start_initial_goal: startInitialGoal,
        workflow_mode: workflowMode,
        require_plane_mapping: true,
        codex_settings: codexSettingsPayload(),
      });
      setSelectedProjectId(String(result.project.id));
      setProjectHarness((current) => ({ ...current, [result.project.id]: result.harness }));
      window.localStorage.setItem("codexFleetProjectId", String(result.project.id));
      setNewProjectPath("");
      setNewProjectParentPath("");
      setNewProjectName("");
      setInitialGoal("");
      await refresh(result.project.id);
      setMessage(
        result.initial_item
          ? `${result.project.name} is linked. ${result.initial_item.identifier} was created${startInitialGoal ? " in Ready" : ""}.`
          : result.harness.status === "ready"
            ? `${result.project.name} is ready. Move a codex-fleet item to Ready and codex-fleet will claim it.`
            : `${result.project.name} is mapped; harness is ${result.harness.status}.`
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not add that project.");
      setBusy(false);
    }
  };

  const chooseProjectFolder = async () => {
    setBusy(true);
    setMessage(newProjectMode === "create" ? "Choose where to create the project..." : "Opening folder picker...");
    try {
      const folder = await api().pickFolder();
      if (newProjectMode === "create") {
        setNewProjectParentPath(folder.path);
      } else {
        setNewProjectPath(folder.path);
        if (!newProjectName.trim()) setNewProjectName(folder.name);
      }
      setMessage(`${folder.name} selected.`);
    } catch (error) {
      setShowManualProjectPath(true);
      setMessage(error instanceof Error ? error.message : "Paste the folder path instead.");
    } finally {
      setBusy(false);
    }
  };

  const applyProjectHarness = async (project: CodexFleetProject) => {
    setBusy(true);
    setMessage(`Applying harness for ${project.name}...`);
    try {
      const result = await api().applyHarness(project.id);
      setProjectHarness((current) => ({ ...current, [project.id]: result.harness }));
      await refresh(project.id);
      setMessage(result.written.length ? `Harness wrote ${result.written.length} files.` : "Harness is already ready.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not apply harness.");
      setBusy(false);
    }
  };

  const scanProjectHarness = async (project: CodexFleetProject) => {
    setBusy(true);
    setMessage(`Scanning harness for ${project.name}...`);
    try {
      const result = await api().planHarness(project.id);
      setProjectHarness((current) => ({ ...current, [project.id]: result.harness }));
      setMessage(`${project.name} harness is ${result.harness.status}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not scan harness.");
    } finally {
      setBusy(false);
    }
  };

  const inspectRun = async (runId: string) => {
    setBusy(true);
    setMessage("Loading run evidence...");
    try {
      const result = await api().run(runId, selectedProjectNumber);
      setSelectedRun(result.run);
      setMessage(`Loaded evidence for ${result.run.identifier}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet could not load run evidence.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void (async () => {
      const connection = await ensureCodexFleetLocalConnection();
      setApiUrl(connection.apiUrl);
      setToken(connection.token);
      if (connection.status === "connected" && connection.token) {
        await refresh(undefined, { apiUrl: connection.apiUrl, token: connection.token });
      } else {
        setMessage(connection.message ?? "Reconnect Codex Fleet to create local projects.");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="min-h-dvh bg-[#0B1020] px-6 py-8 text-white">
      <section className="mx-auto flex max-w-6xl flex-col gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <img src="/codex-fleet-logo.svg" alt="codex-fleet" className="h-11 w-11" />
            <div>
              <h1 className="text-2xl font-semibold tracking-normal">codex-fleet</h1>
              <p className="text-sm text-slate-300">Local Codex agent control center</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {projects.length ? (
              <select
                className="h-10 rounded-md border border-white/10 bg-black/30 px-3 text-sm text-white outline-none focus:border-cyan-300"
                value={selectedProjectId}
                onChange={(event) => {
                  setSelectedProjectId(event.target.value);
                  window.localStorage.setItem("codexFleetProjectId", event.target.value);
                  void refresh(Number(event.target.value));
                }}
              >
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.can_run ? project.name : `${project.name} - needs attention`}
                  </option>
                ))}
              </select>
            ) : null}
            <button
              type="button"
              className="h-10 rounded-md border border-white/10 px-4 text-sm font-semibold text-white disabled:opacity-50"
              disabled={busy}
              onClick={() => void refresh()}
            >
              Refresh
            </button>
            <button
              type="button"
              className="h-10 rounded-md bg-cyan-300 px-4 text-sm font-semibold text-slate-950 disabled:opacity-50"
              disabled={busy || (projects.length > 0 && !selectedProject?.can_run)}
              onClick={runNext}
            >
              Run Ready
            </button>
          </div>
        </div>

        <div className="grid gap-3 rounded-lg border border-white/10 bg-white/[0.04] p-4 md:grid-cols-[1fr_2fr]">
          <label className="grid gap-2 text-sm text-slate-200">
            Local API
            <input
              className="h-10 rounded-md border border-white/10 bg-black/30 px-3 text-white outline-none focus:border-cyan-300"
              value={apiUrl}
              onChange={(event) => setApiUrl(event.target.value)}
            />
          </label>
          <label className="grid gap-2 text-sm text-slate-200">
            Local token
            <input
              className="h-10 rounded-md border border-white/10 bg-black/30 px-3 text-white outline-none focus:border-cyan-300"
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
          </label>
          <p className="text-sm text-slate-300 md:col-span-2">{busy ? "Working..." : message}</p>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Panel title="Fleet Logs">
            {analytics ? (
              <div className="grid gap-3">
                <div className="grid grid-cols-3 gap-2 text-center">
                  <Metric label="Runs" value={analytics.runs_total} />
                  <Metric label="Active" value={analytics.active_runs} />
                  <Metric label="Tokens" value={analytics.total_tokens} />
                </div>
                {analytics.by_role.length ? (
                  analytics.by_role.map((agent) => (
                    <div key={agent.role} className="rounded-md border border-white/10 bg-black/20 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-medium capitalize">{agent.role.replaceAll("_", " ")}</span>
                        <span className="text-xs text-cyan-200">{agent.active} active</span>
                      </div>
                      <p className="mt-2 text-xs text-slate-400">
                        {agent.success} review-ready · {agent.failed} needs attention · {agent.cancelled} cancelled
                      </p>
                      <p className="mt-1 text-xs text-slate-500">{agent.total_tokens.toLocaleString()} tokens</p>
                    </div>
                  ))
                ) : (
                  <EmptyText text="No agent runs yet." />
                )}
                {analytics.recent_events.slice(0, 5).map((event) => (
                  <div key={event.id} className="rounded-md border border-white/10 bg-black/20 p-2 text-xs">
                    <span className="font-medium text-slate-200">{friendlyEvent(event.kind)}</span>
                    <span className="ml-2 text-slate-500">{event.created_at}</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyText text="No analytics yet. Run a Ready task to populate Fleet Logs." />
            )}
          </Panel>

          <Panel title="Projects">
            <form className="grid gap-2" onSubmit={createProject}>
              <p className="text-sm text-slate-300">
                Create or link a repo, configure Codex, and create the first Plane work item in one flow.
              </p>
              {!token.trim() ? (
                <div className="grid gap-2 rounded-md border border-amber-300/30 bg-amber-500/10 p-3 text-sm text-amber-50">
                  <p>Codex Fleet is not connected, so folder picking and project creation are blocked.</p>
                  <a
                    className="w-max rounded-md bg-amber-200 px-3 py-2 text-xs font-semibold text-slate-950"
                    href={new CodexFleetLocalApi({ baseUrl: apiUrl.trim() || undefined, token: null }).connectUrl("codex-fleet/dashboard/")}
                  >
                    Reconnect Codex Fleet
                  </a>
                </div>
              ) : null}
              <div className="grid grid-cols-2 gap-2 rounded-md border border-white/10 bg-black/20 p-1">
                {[
                  ["link", "Link folder"],
                  ["create", "Create project"],
                ].map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    className={`h-9 rounded text-xs font-semibold ${
                      newProjectMode === mode ? "bg-cyan-300 text-slate-950" : "text-slate-300 hover:bg-white/[0.06]"
                    }`}
                    onClick={() => setNewProjectMode(mode as ProjectMode)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {newProjectMode === "create" ? (
                <select
                  className="h-10 rounded-md border border-white/10 bg-black/30 px-3 text-sm text-white outline-none focus:border-cyan-300"
                  value={newProjectType}
                  onChange={(event) => setNewProjectType(event.target.value as ProjectType)}
                >
                  <option value="blank">Blank repo</option>
                  <option value="simple-web">Simple web app</option>
                  <option value="node-next">Node / Next app</option>
                  <option value="python">Python package</option>
                </select>
              ) : null}
              <button
                type="button"
                className="h-10 rounded-md bg-cyan-300 px-3 text-sm font-semibold text-slate-950 disabled:opacity-50"
                disabled={busy}
                onClick={chooseProjectFolder}
              >
                {newProjectMode === "create" ? "Choose Parent Folder" : "Choose Folder"}
              </button>
              {newProjectMode === "create" && newProjectParentPath ? (
                <div className="rounded-md border border-white/10 bg-black/30 px-3 py-2">
                  <p className="text-sm font-medium text-white">Create inside</p>
                  <p className="mt-1 break-all text-xs text-slate-400">{newProjectParentPath}</p>
                </div>
              ) : null}
              {newProjectMode === "link" && newProjectPath ? (
                <div className="rounded-md border border-white/10 bg-black/30 px-3 py-2">
                  <p className="text-sm font-medium text-white">{newProjectPath.split("/").filter(Boolean).pop() ?? newProjectPath}</p>
                  <p className="mt-1 break-all text-xs text-slate-400">{newProjectPath}</p>
                </div>
              ) : null}
              <button
                type="button"
                className="w-max text-xs font-semibold text-cyan-200"
                onClick={() => setShowManualProjectPath((value) => !value)}
              >
                {showManualProjectPath ? "Hide pasted path" : "Paste path instead"}
              </button>
              {showManualProjectPath ? (
                <input
                  className="h-10 rounded-md border border-white/10 bg-black/30 px-3 text-sm text-white outline-none focus:border-cyan-300"
                  placeholder={newProjectMode === "create" ? "/path/to/parent/folder" : "/path/to/project"}
                  value={newProjectMode === "create" ? newProjectParentPath : newProjectPath}
                  onChange={(event) =>
                    newProjectMode === "create" ? setNewProjectParentPath(event.target.value) : setNewProjectPath(event.target.value)
                  }
                />
              ) : null}
              <input
                className="h-10 rounded-md border border-white/10 bg-black/30 px-3 text-sm text-white outline-none focus:border-cyan-300"
                placeholder={newProjectMode === "create" ? "New app name" : "Display name"}
                value={newProjectName}
                onChange={(event) => setNewProjectName(event.target.value)}
              />
              <textarea
                className="min-h-24 resize-y rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-cyan-300"
                placeholder="Initial goal, for example: Build a one-page landing page with a GitHub CTA and record browser proof."
                value={initialGoal}
                onChange={(event) => setInitialGoal(event.target.value)}
              />
              <div className="grid gap-2 rounded-md border border-white/10 bg-black/20 p-3">
                <div className="grid gap-2 md:grid-cols-3">
                  <label className="grid gap-1 text-xs text-slate-300">
                    Automation mode
                    <select
                      className="h-9 rounded-md border border-white/10 bg-black/30 px-2 text-sm text-white outline-none focus:border-cyan-300"
                      value={workflowMode}
                      onChange={(event) => setWorkflowMode(event.target.value as CodexFleetWorkflowMode)}
                    >
                      {WORKFLOW_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="grid gap-1 text-xs text-slate-300">
                    Default model
                    <input
                      className="h-9 rounded-md border border-white/10 bg-black/30 px-2 text-sm text-white outline-none focus:border-cyan-300"
                      value={defaultModel}
                      onChange={(event) => setDefaultModel(event.target.value)}
                    />
                  </label>
                  <label className="grid gap-1 text-xs text-slate-300">
                    Reasoning
                    <select
                      className="h-9 rounded-md border border-white/10 bg-black/30 px-2 text-sm text-white outline-none focus:border-cyan-300"
                      value={reasoningEffort}
                      onChange={(event) => setReasoningEffort(event.target.value as ReasoningEffort)}
                    >
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-xs text-slate-300">
                    Max agents
                    <input
                      className="h-9 rounded-md border border-white/10 bg-black/30 px-2 text-sm text-white outline-none focus:border-cyan-300"
                      min={1}
                      max={12}
                      type="number"
                      value={maxParallelAgents}
                      onChange={(event) => setMaxParallelAgents(Number(event.target.value) || 1)}
                    />
                  </label>
                  <label className="grid gap-1 text-xs text-slate-300">
                    Max depth
                    <input
                      className="h-9 rounded-md border border-white/10 bg-black/30 px-2 text-sm text-white outline-none focus:border-cyan-300"
                      min={0}
                      max={5}
                      type="number"
                      value={maxDepth}
                      onChange={(event) => setMaxDepth(Number(event.target.value) || 0)}
                    />
                  </label>
                  <label className="grid gap-1 text-xs text-slate-300">
                    Sandbox / approval
                    <span className="rounded-md border border-white/10 bg-black/30 px-2 py-2 text-sm text-slate-200">
                      {sandboxMode} / {approvalPolicy}
                    </span>
                  </label>
                </div>
                <label className="flex items-start gap-2 rounded-md border border-white/10 bg-black/20 p-3 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={startInitialGoal}
                    onChange={(event) => setStartInitialGoal(event.target.checked)}
                  />
                  <span>Create the first work item in Ready so Codex Fleet can start automatically.</span>
                </label>
                <label className="flex items-start gap-2 rounded-md border border-white/10 bg-black/20 p-3 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={subagentsEnabled}
                    onChange={(event) => setSubagentsEnabled(event.target.checked)}
                  />
                  <span>
                    Enable Codex subagents
                    <span className="block text-slate-500">Required for Full auto; Codex Fleet still runs only the roles needed for the task.</span>
                  </span>
                </label>
                {fullAutoNeedsSubagents ? (
                  <p className="rounded-md border border-amber-300/30 bg-amber-500/10 p-3 text-xs text-amber-50">
                    Full auto needs subagents so Codex Fleet can plan, implement, test, review, and deliver safely.
                  </p>
                ) : null}
                {subagentsEnabled ? (
                  <div className="grid gap-2">
                    {Object.entries(agentProfiles).map(([role, profile]) => {
                      const roleLocked = role === "planner" || role === "implementer" || role === "delivery_manager";
                      return (
                        <div key={role} className="grid gap-2 rounded-md border border-white/10 bg-black/20 p-3 md:grid-cols-[1fr_9rem_8rem]">
                          <label className="flex items-start gap-2 text-sm text-white">
                            <input
                              type="checkbox"
                              className="mt-1"
                              checked={profile.enabled}
                              disabled={roleLocked}
                              onChange={(event) => updateAgentProfile(role, { enabled: event.target.checked })}
                            />
                            <span>
                              <span className="font-semibold">{profile.label}</span>
                              <span className="block text-xs text-slate-500">{profile.purpose}</span>
                            </span>
                          </label>
                          <input
                            className="h-9 rounded-md border border-white/10 bg-black/30 px-2 text-sm text-white outline-none focus:border-cyan-300"
                            value={profile.model}
                            onChange={(event) => updateAgentProfile(role, { model: event.target.value })}
                          />
                          <select
                            className="h-9 rounded-md border border-white/10 bg-black/30 px-2 text-sm text-white outline-none focus:border-cyan-300"
                            value={profile.reasoning_effort}
                            onChange={(event) => updateAgentProfile(role, { reasoning_effort: event.target.value as ReasoningEffort })}
                          >
                            <option value="low">low</option>
                            <option value="medium">medium</option>
                            <option value="high">high</option>
                          </select>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </div>
              <label className="flex items-start gap-2 rounded-md border border-white/10 bg-black/20 p-3 text-xs text-slate-300">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={applyHarnessOnCreate}
                  onChange={(event) => setApplyHarnessOnCreate(event.target.checked)}
                />
                <span>
                  Add codex-fleet agent harness
                  <span className="block text-slate-500">AGENTS.md, workflow guidance, Codex config, and starter subagent guidance.</span>
                </span>
              </label>
              <button
                type="submit"
                className="h-9 rounded-md border border-white/10 px-3 text-xs font-semibold text-white disabled:opacity-50"
                disabled={createProjectBlocked}
              >
                {newProjectMode === "create" ? "Create Project" : "Add Project"}
              </button>
            </form>
            {projects.length ? (
              projects.map((project) => (
                <div key={project.id} className="rounded-md border border-white/10 bg-black/20 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium">{project.name}</span>
                    <ProjectStatusBadge project={project} />
                  </div>
                  <p className="mt-2 break-all text-xs text-slate-400">{project.repo_path}</p>
                  <p className={project.can_run ? "mt-2 text-xs text-emerald-100" : "mt-2 text-xs text-amber-100"}>
                    {project.status_message}
                  </p>
                  <HarnessSummary harness={projectHarness[project.id]} fallbackStatus={project.harness_status} />
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="h-8 rounded-md border border-white/10 px-3 text-xs font-semibold text-white disabled:opacity-50"
                      disabled={busy}
                      onClick={() => {
                        setSelectedProjectId(String(project.id));
                        window.localStorage.setItem("codexFleetProjectId", String(project.id));
                        void refresh(project.id);
                      }}
                    >
                      Select
                    </button>
                    <button
                      type="button"
                      className="h-8 rounded-md border border-white/10 px-3 text-xs font-semibold text-white disabled:opacity-50"
                      disabled={busy || !project.can_run}
                      onClick={() => scanProjectHarness(project)}
                    >
                      Scan
                    </button>
                    <button
                      type="button"
                      className="h-8 rounded-md border border-white/10 px-3 text-xs font-semibold text-white disabled:opacity-50"
                      disabled={busy || !project.can_run || projectHarness[project.id]?.status === "blocked"}
                      onClick={() => applyProjectHarness(project)}
                    >
                      Apply Harness
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <EmptyText text="No local projects yet." />
            )}
          </Panel>

          <Panel title="Ready Work">
            <form className="grid gap-2" onSubmit={createTask}>
              <input
                className="h-10 rounded-md border border-white/10 bg-black/30 px-3 text-sm text-white outline-none focus:border-cyan-300"
                placeholder="New Ready item"
                value={newTaskTitle}
                onChange={(event) => setNewTaskTitle(event.target.value)}
              />
              <textarea
                className="min-h-20 resize-y rounded-md border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none focus:border-cyan-300"
                placeholder="Task details"
                value={newTaskDescription}
                onChange={(event) => setNewTaskDescription(event.target.value)}
              />
              <button
                type="submit"
                className="h-9 rounded-md border border-white/10 px-3 text-xs font-semibold text-white disabled:opacity-50"
                disabled={busy || !newTaskTitle.trim()}
              >
                Add Ready
              </button>
            </form>
            {items.length ? (
              items.map((item) => (
                <div key={item.id} className="rounded-md border border-white/10 bg-black/20 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium">{item.identifier}</span>
                    <span className="text-xs text-cyan-200">{item.state}</span>
                  </div>
                  <p className="mt-2 text-sm text-slate-300">{item.title}</p>
                  {itemRunStatus[item.id] ? (
                    <p className="mt-2 break-all text-xs text-slate-400">
                      {itemRunStatus[item.id]?.status} · {itemRunStatus[item.id]?.worktree_path ?? "workspace pending"}
                    </p>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="h-8 rounded-md bg-cyan-300 px-3 text-xs font-semibold text-slate-950 disabled:opacity-50"
                      disabled={busy}
                      onClick={() => runWorkItem(item.id)}
                    >
                      {activeItemId === item.id && busy ? "Running" : "Run with Codex"}
                    </button>
                    <button
                      type="button"
                      className="h-8 rounded-md border border-white/10 px-3 text-xs font-semibold text-white disabled:opacity-50"
                      disabled={busy}
                      onClick={() => inspectItemStatus(item.id)}
                    >
                      Status
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <EmptyText text="No Ready items." />
            )}
          </Panel>

          <Panel title="Runs">
            {runs.length ? (
              runs.slice(0, 8).map((run) => (
                <div key={run.id} className="rounded-md border border-white/10 bg-black/20 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium">{run.identifier}</span>
                    <span className="text-xs text-slate-300">{run.status}</span>
                  </div>
                  {run.worktree_path ? <p className="mt-2 break-all text-xs text-slate-400">{run.worktree_path}</p> : null}
                  {run.error ? <p className="mt-2 text-xs text-red-200">{run.error}</p> : null}
                  <button
                    type="button"
                    className="mt-3 h-8 rounded-md border border-white/10 px-3 text-xs font-semibold text-white disabled:opacity-50"
                    disabled={busy}
                    onClick={() => inspectRun(run.id)}
                  >
                    Inspect
                  </button>
                </div>
              ))
            ) : (
              <EmptyText text="No runs yet." />
            )}
          </Panel>
        </div>

        {selectedRun ? (
          <Panel title={`Run Evidence ${selectedRun.identifier}`}>
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="grid content-start gap-2">
                <h3 className="text-sm font-semibold text-slate-200">Events</h3>
                {selectedRun.events?.length ? (
                  selectedRun.events.map((event) => (
                    <div key={event.id} className="rounded-md border border-white/10 bg-black/20 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium">{event.kind}</span>
                        <span className="text-xs text-slate-400">{event.created_at}</span>
                      </div>
                      <pre className="mt-2 overflow-auto text-xs text-slate-300">{JSON.stringify(event.payload, null, 2)}</pre>
                    </div>
                  ))
                ) : (
                  <EmptyText text="No events recorded." />
                )}
              </div>
              <div className="grid content-start gap-2">
                <h3 className="text-sm font-semibold text-slate-200">Artifacts</h3>
                {selectedRun.artifacts?.length ? (
                  selectedRun.artifacts.map((artifact) => (
                    <div key={artifact.id} className="rounded-md border border-white/10 bg-black/20 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium">{artifact.kind}</span>
                        <span className="text-xs text-slate-400">{artifact.created_at}</span>
                      </div>
                      <p className="mt-2 break-all text-xs text-slate-300">{artifact.path}</p>
                    </div>
                  ))
                ) : (
                  <EmptyText text="No artifacts recorded." />
                )}
              </div>
            </div>
          </Panel>
        ) : null}
      </section>
    </main>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="grid content-start gap-3 rounded-lg border border-white/10 bg-white/[0.04] p-4">
      <h2 className="text-sm font-semibold uppercase tracking-normal text-slate-300">{title}</h2>
      {children}
    </section>
  );
}

function EmptyText({ text }: { text: string }) {
  return <p className="text-sm text-slate-400">{text}</p>;
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-white/10 bg-black/20 p-2">
      <p className="text-lg font-semibold text-white">{value.toLocaleString()}</p>
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
    </div>
  );
}

function friendlyEvent(kind: string) {
  return (
    {
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
    }[kind] ?? kind.replaceAll("_", " ")
  );
}

function HarnessSummary({ harness, fallbackStatus }: { harness?: CodexFleetHarness; fallbackStatus: string }) {
  if (!harness) {
    return <p className="mt-2 text-xs text-slate-400">Harness: {fallbackStatus}</p>;
  }
  const commands = Object.entries(harness.scan.commands).filter((entry): entry is [string, string] => Boolean(entry[1]));
  return (
    <div className="mt-3 grid gap-2 rounded-md border border-white/10 bg-black/20 p-3 text-xs text-slate-300">
      <div className="flex items-center justify-between gap-3">
        <span>Harness</span>
        <span className={harness.status === "blocked" ? "text-red-200" : harness.status === "ready" ? "text-cyan-200" : "text-amber-100"}>
          {harness.status}
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {harness.scan.stack ? <Badge text={harness.scan.stack} /> : null}
        {harness.scan.package_manager ? <Badge text={harness.scan.package_manager} /> : null}
        {harness.scan.dirty ? <Badge text="dirty" tone="warn" /> : null}
      </div>
      {harness.scan.git_root ? <p className="break-all text-slate-400">{harness.scan.git_root}</p> : null}
      {commands.length ? (
        <div className="grid gap-1">
          {commands.map(([name, command]) => (
            <div key={name} className="grid grid-cols-[5rem_1fr] gap-2">
              <span className="text-slate-500">{name}</span>
              <code className="break-all text-slate-200">{command}</code>
            </div>
          ))}
        </div>
      ) : null}
      {harness.missing.length ? <p>{harness.missing.length} file(s) missing.</p> : null}
      {harness.scan.warnings.length ? (
        <ul className="grid gap-1 text-amber-100">
          {harness.scan.warnings.slice(0, 3).map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ProjectStatusBadge({ project }: { project: CodexFleetProject }) {
  const text = project.can_run
    ? project.plane_status === "created"
      ? "restored"
      : project.plane_status === "relinked"
        ? "relinked"
        : project.harness_status
    : project.path_status === "missing_folder"
      ? "folder missing"
      : project.path_status === "not_git"
        ? "not git"
        : "needs attention";
  const tone = project.can_run ? "default" : "warn";
  return <Badge text={text} tone={tone} />;
}

function Badge({ text, tone = "default" }: { text: string; tone?: "default" | "warn" }) {
  return (
    <span className={`rounded border px-2 py-0.5 ${tone === "warn" ? "border-amber-300/30 text-amber-100" : "border-cyan-300/30 text-cyan-100"}`}>
      {text}
    </span>
  );
}
