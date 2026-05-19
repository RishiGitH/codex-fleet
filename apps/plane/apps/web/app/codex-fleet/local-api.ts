export type CodexFleetProject = {
  id: number;
  name: string;
  slug: string;
  repo_path: string;
  git_root: string | null;
  plane_workspace_slug: string | null;
  plane_project_id: string | null;
  harness_status: string;
  runner_mode: string;
  path_status: "ok" | "missing_folder" | "not_git";
  plane_status: "linked" | "relinked" | "created" | "stale" | "error" | "skipped";
  status_message: string;
  can_run: boolean;
  codex_settings: CodexFleetProjectSettings;
};

export type CodexFleetWorkflowMode = "execute_only" | "plan_only" | "plan_execute" | "full_auto";

export type CodexFleetSubagentSettings = {
  model: string;
  reasoning_effort: string;
  sandbox_mode: string;
  enabled?: boolean;
};

export type CodexFleetProjectSettings = {
  runner_mode: string;
  default_model: string;
  reasoning_effort: string;
  approval_policy: string;
  sandbox_mode: string;
  max_parallel_agents: number;
  max_depth: number;
  max_child_tasks_per_run: number;
  job_timeout_seconds: number;
  workflow_mode: CodexFleetWorkflowMode;
  skill_policy: string;
  subagents_enabled?: boolean;
  enabled_agent_roles?: string[];
  agent_profiles?: Record<string, CodexFleetSubagentSettings>;
  subagents: Record<string, CodexFleetSubagentSettings>;
  delivery_policy?: Record<string, unknown>;
  test_policy?: Record<string, unknown>;
};

export type CodexFleetRun = {
  id: string;
  item_id: string;
  identifier: string;
  status: string;
  branch_name: string | null;
  worktree_path: string | null;
  runner_name?: string | null;
  agent_role?: string | null;
  agent_name?: string | null;
  agent_avatar?: string | null;
  model?: string | null;
  reasoning_effort?: string | null;
  codex_thread_id?: string | null;
  codex_turn_id?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
  workflow_mode?: string | null;
  parent_workflow_mode?: string | null;
  effective_settings?: Record<string, unknown>;
  settings_source?: string | null;
  prompt_role?: string | null;
  is_legacy_runner?: boolean;
  artifact_count?: number | null;
  changed_files?: string[];
  latest_event_text?: string | null;
  blocker_text?: string | null;
  settings?: Record<string, unknown>;
  token_usage?: Record<string, number>;
  error: string | null;
  events?: CodexFleetRunEvent[];
  artifacts?: CodexFleetRunArtifact[];
  transcript_preview?: CodexFleetRunMessage[];
};

export type CodexFleetRunMessage = {
  id: number;
  run_id: string;
  sequence: number;
  kind: "user" | "assistant" | "tool_call" | "tool_result" | "system_event" | "error" | string;
  agent_role?: string | null;
  agent_name?: string | null;
  content: string;
  artifact_path?: string | null;
  payload?: Record<string, unknown>;
  created_at: string;
};

export type CodexFleetRunEvent = {
  id: number;
  run_id?: string;
  kind: string;
  text?: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type CodexFleetRunArtifact = {
  id: number;
  run_id?: string;
  path: string;
  kind: string;
  size_bytes?: number | null;
  sha256?: string | null;
  redaction?: string;
  created_at: string;
  download_path?: string;
};

export type CodexFleetWorktree = {
  task_id: string;
  task_key: string;
  role: string;
  branch: string | null;
  path: string;
  status: string;
  last_run_id: string;
  pr_url?: string | null;
  delivery_status?: string | null;
  exists: boolean;
};

export type CodexFleetTaskMetadata = {
  item_id: string;
  source: string;
  depth: number;
  parent_item_id: string | null;
  parent_identifier: string | null;
  parent_run_id: string | null;
  created_by_run_id: string | null;
  root_item_id?: string | null;
  role?: string | null;
  depends_on?: string[];
  generation?: number;
  approval_mode?: string | null;
  terminal_outcome?: string | null;
  settings: Record<string, unknown>;
  latest_run?: CodexFleetRun | null;
  created_at: string;
  updated_at: string;
};

export type CodexFleetAgentAnalytics = {
  runs_total: number;
  active_runs: number;
  total_tokens: number;
  by_role: { role: string; runs: number; success: number; failed: number; active: number; cancelled: number; total_tokens: number }[];
  recent_events: CodexFleetRunEvent[];
};

export type CodexFleetLogs = {
  linked?: boolean;
  message?: string;
  plane_project_id?: string;
  project: CodexFleetProject | null;
  repo: string;
  analytics: CodexFleetAgentAnalytics;
  runs: CodexFleetRun[];
  recent_events: CodexFleetRunEvent[];
  tasks: CodexFleetTaskMetadata[];
};

export type CodexFleetWorkItem = {
  id: string;
  identifier: string;
  title: string;
  description: string | null;
  state: string;
  priority: number;
  url: string | null;
  labels: string[];
};

export type CodexFleetHarness = {
  repo: string;
  status: string;
  scan: CodexFleetHarnessScan;
  files: { path: string; exists: boolean }[];
  missing: string[];
};

export type CodexFleetHarnessScan = {
  git_root: string | null;
  dirty: boolean | null;
  stack: string | null;
  package_manager: string | null;
  commands: {
    install: string | null;
    test: string | null;
    lint: string | null;
    typecheck: string | null;
    build: string | null;
    dev: string | null;
  };
  warnings: string[];
};

export type CodexFleetPickedFolder = {
  path: string;
  name: string;
};

export type CodexFleetSession = {
  ok: boolean;
  connected: boolean;
  service: string;
  build?: string | null;
  app_server_protocol?: string | null;
  repo: string;
  projects: number;
};

export class CodexFleetLocalApiError extends Error {
  readonly code: string;
  readonly status?: number;

  constructor(message: string, options: { code?: string; status?: number } = {}) {
    super(message);
    this.name = "CodexFleetLocalApiError";
    this.code = options.code ?? "unknown";
    this.status = options.status;
  }
}

export type CodexFleetRunOptions = {
  fake: boolean;
  fake_succeed?: boolean;
};

export type CodexFleetProjectRef = number | { plane_project_id: string };

export const DEFAULT_CODEX_FLEET_API_URL = "http://127.0.0.1:18790";

export type CodexFleetLocalConnection = {
  apiUrl: string;
  token: string;
  status?: "connected" | "missing_token" | "unreachable";
  message?: string;
  session?: CodexFleetSession;
};

const API_URL_STORAGE_KEY = "codexFleetLocalApiUrl";
const TOKEN_STORAGE_KEY = "codexFleetLocalToken";

let sessionExchangePromise: Promise<{ ok: boolean; apiUrl: string; token: string }> | null = null;

export function readCodexFleetLocalConnection(): CodexFleetLocalConnection {
  if (typeof window === "undefined") return { apiUrl: DEFAULT_CODEX_FLEET_API_URL, token: "" };
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const apiUrl = hash.get("apiUrl") || window.localStorage.getItem(API_URL_STORAGE_KEY) || DEFAULT_CODEX_FLEET_API_URL;
  const token = hash.get("token") || window.localStorage.getItem(TOKEN_STORAGE_KEY) || "";
  if (hash.get("apiUrl")) window.localStorage.setItem(API_URL_STORAGE_KEY, apiUrl);
  if (hash.get("token")) window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  return { apiUrl, token };
}

export async function ensureCodexFleetLocalConnection(): Promise<CodexFleetLocalConnection> {
  let connection = readCodexFleetLocalConnection();
  if (typeof window === "undefined") return connection;

  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const code = hash.get("code");
  if (code) {
    try {
      if (!sessionExchangePromise) {
        const api = new CodexFleetLocalApi({ baseUrl: connection.apiUrl, token: null });
        sessionExchangePromise = api.exchangeSessionCode(code).finally(() => {
          sessionExchangePromise = null;
        });
      }
      const session = await sessionExchangePromise;
      const apiUrl = session.apiUrl || connection.apiUrl || DEFAULT_CODEX_FLEET_API_URL;
      const token = session.token || "";
      window.localStorage.setItem(API_URL_STORAGE_KEY, apiUrl);
      if (token) window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
      hash.delete("code");
      hash.set("apiUrl", apiUrl);
      const fragment = hash.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${fragment ? `#${fragment}` : ""}`);
      connection = { apiUrl, token };
    } catch (error) {
      const storedApiUrl = window.localStorage.getItem(API_URL_STORAGE_KEY) || connection.apiUrl || DEFAULT_CODEX_FLEET_API_URL;
      const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY) || "";
      if (error instanceof CodexFleetLocalApiError && error.code === "auth_missing" && storedToken) {
        hash.delete("code");
        hash.set("apiUrl", storedApiUrl);
        const fragment = hash.toString();
        window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${fragment ? `#${fragment}` : ""}`);
        connection = { apiUrl: storedApiUrl, token: storedToken };
      } else if (error instanceof CodexFleetLocalApiError && error.code === "auth_missing") {
        hash.delete("code");
        hash.set("apiUrl", storedApiUrl);
        const fragment = hash.toString();
        window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${fragment ? `#${fragment}` : ""}`);
        connection = { apiUrl: storedApiUrl, token: "" };
      } else {
        return {
          ...connection,
          status: "unreachable",
          message: "Codex Fleet is not reachable. Start it with make up, then reconnect.",
        };
      }
    }
  }

  if (!connection.token) {
    return {
      ...connection,
      status: "missing_token",
      message: "Reconnect Codex Fleet to allow local folder access.",
    };
  }

  try {
    const api = new CodexFleetLocalApi({ baseUrl: connection.apiUrl, token: connection.token });
    const session = await api.session();
    return {
      ...connection,
      status: "connected",
      message: "Local launcher connected",
      session,
    };
  } catch (error) {
    if (error instanceof CodexFleetLocalApiError && error.code === "auth_missing") {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
      return {
        apiUrl: connection.apiUrl,
        token: "",
        status: "missing_token",
        message: "Reconnect Codex Fleet to allow local folder access.",
      };
    }
    return {
      ...connection,
      status: "unreachable",
      message: "Codex Fleet is not reachable. Start it with make up, then reconnect.",
    };
  }
}

export class CodexFleetLocalApi {
  private readonly baseUrl: string;
  private readonly token: string | null;

  constructor(options?: { baseUrl?: string; token?: string | null }) {
    this.baseUrl = options?.baseUrl ?? DEFAULT_CODEX_FLEET_API_URL;
    this.token = options?.token ?? null;
  }

  async status(): Promise<{ ok: boolean; service: string; repo: string; projects: number }> {
    return this.request("/api/status", { auth: false });
  }

  async session(): Promise<CodexFleetSession> {
    return this.request("/api/session");
  }

  async exchangeSessionCode(code: string): Promise<{ ok: boolean; apiUrl: string; token: string }> {
    return this.request(`/api/session/exchange?code=${encodeURIComponent(code)}`, { auth: false });
  }

  async planeLoginUrl(redirectPath: string): Promise<{ url: string }> {
    return this.request("/api/plane/login-url", {
      method: "POST",
      body: { redirect_path: redirectPath },
    });
  }

  connectUrl(redirectPath: string): string {
    return `${this.baseUrl}/api/plane/connect?redirect_path=${encodeURIComponent(redirectPath)}`;
  }

  async projects(): Promise<{ projects: CodexFleetProject[] }> {
    return this.request("/api/projects");
  }

  async createProject(body: {
    path?: string;
    name?: string;
    apply_harness?: boolean;
    create_new?: boolean;
    location?: string;
    parent_path?: string;
    folder_slug?: string;
    project_type?: "blank" | "simple-web" | "node-next" | "python";
    initial_goal?: string;
    start_initial_goal?: boolean;
    require_plane_mapping?: boolean;
    codex_settings?: Partial<CodexFleetProjectSettings>;
    workflow_mode?: CodexFleetWorkflowMode;
  }): Promise<{
    project: CodexFleetProject;
    plane?: {
      status: string;
      workspace_slug?: string;
      project_id?: string;
      config_path?: string;
      created_states?: string[];
      created_labels?: string[];
      reason?: string;
    };
    harness: CodexFleetHarness;
    written: string[];
    initial_item: CodexFleetWorkItem | null;
    setup_log: string[];
  }> {
    return this.request("/api/projects", {
      method: "POST",
      body,
    });
  }

  async configureCodexForPlaneProject(body: {
    plane_project_id: string;
    mode: "create_new" | "add_existing";
    name?: string;
    path?: string;
    repo_path?: string;
    location?: string;
    parent_path?: string;
    folder_slug?: string;
    project_type?: "blank" | "simple-web" | "node-next" | "python";
    apply_harness?: boolean;
    codex_settings?: Partial<CodexFleetProjectSettings>;
    workflow_mode?: CodexFleetWorkflowMode;
    default_model?: string;
    reasoning_effort?: string;
    max_parallel_agents?: number;
    max_depth?: number;
  }): Promise<{
    ok: boolean;
    project: CodexFleetProject;
    plane: { status: string; workspace_slug?: string; project_id?: string; config_path?: string; reason?: string };
    harness: CodexFleetHarness;
    written: string[];
    setup_log: string[];
  }> {
    return this.request("/api/projects/configure-codex", {
      method: "POST",
      body,
    });
  }

  async checkFolderPicker(): Promise<{ ok: boolean; available: boolean; picker: string }> {
    return this.request("/api/folders/check");
  }

  async pickFolder(): Promise<CodexFleetPickedFolder> {
    return this.request("/api/folders/pick", {
      method: "POST",
      body: {},
    });
  }

  async projectSettings(projectId: number | string): Promise<{ project: CodexFleetProject; settings: CodexFleetProjectSettings }> {
    return this.request(`/api/projects/${encodeURIComponent(String(projectId))}/fleet-settings`);
  }

  async fleetLogs(projectId: number | string): Promise<{ fleet_logs: CodexFleetLogs }> {
    return this.request(`/api/projects/${encodeURIComponent(String(projectId))}/fleet-logs`);
  }

  async fleetDashboard(projectId: number | string): Promise<{ dashboard: Record<string, unknown> }> {
    return this.request(`/api/projects/${encodeURIComponent(String(projectId))}/fleet-dashboard`);
  }

  async worktrees(projectId: number | string): Promise<{ worktrees: CodexFleetWorktree[] }> {
    return this.request(`/api/projects/${encodeURIComponent(String(projectId))}/worktrees`);
  }

  async projectRevision(projectId: number | string): Promise<{ revision: number }> {
    return this.request(`/api/projects/${encodeURIComponent(String(projectId))}/revision`);
  }

  async updateProjectSettings(
    projectId: number | string,
    settings: Partial<CodexFleetProjectSettings>
  ): Promise<{ project: CodexFleetProject; settings: CodexFleetProjectSettings }> {
    return this.request(`/api/projects/${encodeURIComponent(String(projectId))}/fleet-settings`, {
      method: "PATCH",
      body: { codex_settings: settings },
    });
  }

  async runs(project?: CodexFleetProjectRef): Promise<{ runs: CodexFleetRun[] }> {
    return this.request(withProjectRef("/api/runs", project));
  }

  async run(runId: string, project?: CodexFleetProjectRef): Promise<{ run: CodexFleetRun }> {
    return this.request(withProjectRef(`/api/runs/${encodeURIComponent(runId)}`, project));
  }

  async runTranscript(runId: string, project?: CodexFleetProjectRef, view: "chat" | "timeline" | "raw" = "chat"): Promise<{ messages: CodexFleetRunMessage[] }> {
    return this.request(withProjectRef(`/api/runs/${encodeURIComponent(runId)}/transcript?view=${encodeURIComponent(view)}`, project));
  }

  async completeDelivery(deliveryTaskId: string, project?: CodexFleetProjectRef): Promise<{ ok: boolean; state: string; error?: string }> {
    const body = typeof project === "number" ? { project_id: project } : project || {};
    return this.request(`/api/delivery/${encodeURIComponent(deliveryTaskId)}/complete`, {
      method: "POST",
      body,
    });
  }

  async agentAnalytics(projectId: number | string): Promise<{ analytics: CodexFleetAgentAnalytics }> {
    return this.request(`/api/projects/${encodeURIComponent(String(projectId))}/agent-analytics`);
  }

  async readyWorkItems(project?: CodexFleetProjectRef): Promise<{ items: CodexFleetWorkItem[] }> {
    return this.request(withProjectRef("/api/work-items/ready", project));
  }

  async createWorkItem(body: { title: string; description?: string; project_id?: number }): Promise<{ item: CodexFleetWorkItem }> {
    return this.request("/api/work-items", {
      method: "POST",
      body,
    });
  }

  async bootstrap(path?: string): Promise<{ ok: boolean; project: CodexFleetProject; harness: CodexFleetHarness }> {
    return this.request("/api/onboarding/local-bootstrap", {
      method: "POST",
      body: { path },
    });
  }

  async planHarness(projectId: number): Promise<{ harness: CodexFleetHarness }> {
    return this.request(`/api/projects/${projectId}/harness/plan`, {
      method: "POST",
      body: {},
    });
  }

  async applyHarness(projectId: number): Promise<{ written: string[]; harness: CodexFleetHarness }> {
    return this.request(`/api/projects/${projectId}/harness/apply`, {
      method: "POST",
      body: {},
    });
  }

  async runWorkItem(
    workItemId: string,
    options: CodexFleetRunOptions = { fake: false },
    project?: CodexFleetProjectRef
  ): Promise<{ dispatched: boolean; message: string; run: CodexFleetRun | null }> {
    return this.request("/api/runs", {
      method: "POST",
      body: { ...options, plane_work_item_id: workItemId, ...projectRefBody(project) },
    });
  }

  async runNextReady(
    options: CodexFleetRunOptions = { fake: false },
    project?: CodexFleetProjectRef
  ): Promise<{ dispatched: boolean; message: string; run: CodexFleetRun | null }> {
    return this.request("/api/runs/next-ready", {
      method: "POST",
      body: { ...options, ...projectRefBody(project) },
    });
  }

  async runStatus(workItemId: string, project?: CodexFleetProjectRef): Promise<{ run: CodexFleetRun | null }> {
    return this.request(withProjectRef(`/api/work-items/${encodeURIComponent(workItemId)}/run-status`, project));
  }

  async planWorkItem(workItemId: string, project?: CodexFleetProjectRef): Promise<{ dispatched: boolean; message: string; run: CodexFleetRun | null }> {
    return this.request(`/api/work-items/${encodeURIComponent(workItemId)}/plan`, {
      method: "POST",
      body: projectRefBody(project),
    });
  }

  async retryWorkItem(workItemId: string, project?: CodexFleetProjectRef): Promise<{ ok: boolean; item: CodexFleetWorkItem; previous_run: CodexFleetRun | null; state: string }> {
    return this.request(`/api/work-items/${encodeURIComponent(workItemId)}/retry`, {
      method: "POST",
      body: projectRefBody(project),
    });
  }

  async cancelWorkItem(workItemId: string, project?: CodexFleetProjectRef): Promise<{ ok: boolean; item: CodexFleetWorkItem; run: CodexFleetRun | null; state: string }> {
    return this.request(`/api/work-items/${encodeURIComponent(workItemId)}/cancel`, {
      method: "POST",
      body: projectRefBody(project),
    });
  }

  async workItemChildren(workItemId: string, project?: CodexFleetProjectRef): Promise<{ children: CodexFleetTaskMetadata[] }> {
    return this.request(withProjectRef(`/api/work-items/${encodeURIComponent(workItemId)}/children`, project));
  }

  async workItemParent(workItemId: string, project?: CodexFleetProjectRef): Promise<{ parent: CodexFleetTaskMetadata | null }> {
    return this.request(withProjectRef(`/api/work-items/${encodeURIComponent(workItemId)}/parent`, project));
  }

  async workItemGraph(workItemId: string, project?: CodexFleetProjectRef): Promise<{ graph: Record<string, unknown> }> {
    return this.request(withProjectRef(`/api/work-items/${encodeURIComponent(workItemId)}/graph`, project));
  }

  async createDeliveryTask(workItemId: string, project?: CodexFleetProjectRef): Promise<{ ok: boolean; item: CodexFleetWorkItem }> {
    return this.request(`/api/work-items/${encodeURIComponent(workItemId)}/delivery-task`, {
      method: "POST",
      body: projectRefBody(project),
    });
  }

  async answerInput(
    workItemId: string,
    answer: string,
    project?: CodexFleetProjectRef
  ): Promise<{ ok: boolean; state: string; resolved_run_id?: string; revision?: number; run?: CodexFleetRun | null }> {
    return this.request(`/api/work-items/${encodeURIComponent(workItemId)}/answer-input`, {
      method: "POST",
      body: { answer, ...projectRefBody(project) },
    });
  }

  async artifactBlob(
    runId: string,
    artifactId: number,
    project?: CodexFleetProjectRef
  ): Promise<Blob> {
    const headers = new Headers();
    if (this.token) headers.set("X-Codex-Fleet-Token", this.token);
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${withProjectRef(`/api/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(String(artifactId))}`, project)}`, {
        method: "GET",
        headers,
      });
    } catch {
      throw new CodexFleetLocalApiError(
        `Cannot reach codex-fleet local API at ${this.baseUrl}. Start codex-fleet from your terminal with make up and use the browser window it opens.`,
        { code: "api_unreachable" }
      );
    }
    if (!response.ok) {
      let message = response.statusText;
      let code = "request_failed";
      try {
        const payload = await response.json();
        message = payload?.error ?? message;
        code = payload?.code ?? code;
      } catch {
        // Non-JSON artifact errors are still surfaced with the HTTP status text.
      }
      throw new CodexFleetLocalApiError(message, { code, status: response.status });
    }
    return response.blob();
  }

  private async request<T>(
    path: string,
    options: { method?: "GET" | "POST" | "PATCH"; body?: Record<string, unknown>; auth?: boolean } = {}
  ): Promise<T> {
    const headers = new Headers();
    if (options.auth !== false && this.token) headers.set("X-Codex-Fleet-Token", this.token);
    if (options.body) headers.set("Content-Type", "application/json");

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: options.method ?? "GET",
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
      });
    } catch {
      throw new CodexFleetLocalApiError(
        `Cannot reach codex-fleet local API at ${this.baseUrl}. Start codex-fleet from your terminal with make up and use the browser window it opens. If you are using Brave or Safari, allow localhost/private-network requests for this site.`,
        { code: "api_unreachable" }
      );
    }
    const payload = await response.json();
    if (!response.ok) {
      throw new CodexFleetLocalApiError(payload?.error ?? response.statusText, {
        code: payload?.code ?? "request_failed",
        status: response.status,
      });
    }
    return payload as T;
  }
}

const withProjectRef = (path: string, project?: CodexFleetProjectRef): string => {
  const body = projectRefBody(project);
  const key = body.plane_project_id ? "plane_project_id" : body.project_id ? "project_id" : "";
  const value = key ? String(body[key]) : "";
  return key ? `${path}${path.includes("?") ? "&" : "?"}${key}=${encodeURIComponent(value)}` : path;
};

const projectRefBody = (project?: CodexFleetProjectRef): Record<string, unknown> => {
  if (!project) return {};
  if (typeof project === "number") return { project_id: project };
  if (project.plane_project_id) return { plane_project_id: project.plane_project_id };
  return {};
};
