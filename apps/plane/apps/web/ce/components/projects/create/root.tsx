/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useState } from "react";
import { observer } from "mobx-react";
import { FormProvider, useForm } from "react-hook-form";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EFileAssetType } from "@plane/types";
// components
import ProjectCommonAttributes from "@/components/project/create/common-attributes";
import ProjectCreateHeader from "@/components/project/create/header";
import ProjectCreateButtons from "@/components/project/create/project-create-buttons";
// hooks
import { getCoverImageType, uploadCoverImage } from "@/helpers/cover-image.helper";
import { useProject } from "@/hooks/store/use-project";
import { usePlatformOS } from "@/hooks/use-platform-os";
import {
  CodexFleetLocalApi,
  CodexFleetLocalApiError,
  ensureCodexFleetLocalConnection,
  readCodexFleetLocalConnection,
} from "@/app/codex-fleet/local-api";
// plane web types
import type { TProject } from "@/plane-web/types/projects";
import { ProjectAttributes } from "./attributes";
import { getProjectFormValues } from "./utils";

type CodexFleetProjectMode = "link" | "create";
type CodexFleetProjectType = "blank" | "simple-web" | "node-next" | "python";
type CodexFleetWorkflowMode = "execute_only" | "plan_only" | "plan_execute" | "full_auto";
type CodexFleetConnectionState = "checking" | "connected" | "missing_token" | "unreachable";

type CodexFleetAgentProfile = {
  label: string;
  purpose: string;
  model: string;
  reasoning_effort: string;
  sandbox_mode: string;
  enabled: boolean;
};

const DEFAULT_AGENT_PROFILES: Record<string, CodexFleetAgentProfile> = {
  planner: { label: "Planner", purpose: "Splits the parent request into durable child tasks.", model: "gpt-5.5", reasoning_effort: "medium", sandbox_mode: "workspace-write", enabled: true },
  code_scout: { label: "Code Scout", purpose: "Maps unfamiliar code before implementation when needed.", model: "gpt-5.4-mini", reasoning_effort: "high", sandbox_mode: "workspace-write", enabled: false },
  implementer: { label: "Implementer", purpose: "Edits code for assigned child tasks.", model: "gpt-5.5", reasoning_effort: "low", sandbox_mode: "workspace-write", enabled: true },
  quality_reviewer: { label: "Quality Reviewer", purpose: "Checks build quality, harness fit, and token/context efficiency.", model: "gpt-5.4-mini", reasoning_effort: "high", sandbox_mode: "workspace-write", enabled: true },
  security_reviewer: { label: "Security Reviewer", purpose: "Runs only for auth, secrets, deployment, shell, or filesystem risk.", model: "gpt-5.5", reasoning_effort: "medium", sandbox_mode: "workspace-write", enabled: false },
  test_reviewer: { label: "Test Agent", purpose: "Runs the product and records proof artifacts when available.", model: "gpt-5.4-mini", reasoning_effort: "high", sandbox_mode: "workspace-write", enabled: true },
  delivery_manager: { label: "Delivery Manager", purpose: "Prepares PR/local merge delivery and cleanup.", model: "gpt-5.4-mini", reasoning_effort: "medium", sandbox_mode: "workspace-write", enabled: true },
};

export type TCreateProjectFormProps = {
  setToFavorite?: boolean;
  workspaceSlug: string;
  onClose: () => void;
  handleNextStep: (projectId: string) => void;
  data?: Partial<TProject>;
  templateId?: string;
  updateCoverImageStatus: (projectId: string, coverImage: string) => Promise<void>;
};

export const CreateProjectForm = observer(function CreateProjectForm(props: TCreateProjectFormProps) {
  const { setToFavorite, workspaceSlug, data, onClose, handleNextStep, updateCoverImageStatus } = props;
  // store
  const { t } = useTranslation();
  const { addProjectToFavorites, createProject, updateProject } = useProject();
  // states
  const [shouldAutoSyncIdentifier, setShouldAutoSyncIdentifier] = useState(true);
  const [codexProjectMode, setCodexProjectMode] = useState<CodexFleetProjectMode>("create");
  const [localProjectPath, setLocalProjectPath] = useState("");
  const [localProjectParentPath, setLocalProjectParentPath] = useState("");
  const [localProjectType, setLocalProjectType] = useState<CodexFleetProjectType>("blank");
  const [showManualPath, setShowManualPath] = useState(true);
  const [applyHarness, setApplyHarness] = useState(true);
  const [initialGoal, setInitialGoal] = useState("");
  const [startInitialGoal, setStartInitialGoal] = useState(true);
  const [workflowMode, setWorkflowMode] = useState<CodexFleetWorkflowMode>("plan_execute");
  const runnerMode = "app-server";
  const [defaultModel, setDefaultModel] = useState("gpt-5.5");
  const [reasoningEffort, setReasoningEffort] = useState("low");
  const [approvalPolicy, setApprovalPolicy] = useState("never");
  const [sandboxMode, setSandboxMode] = useState("workspace-write");
  const [maxParallelAgents, setMaxParallelAgents] = useState(3);
  const [maxTaskDepth, setMaxTaskDepth] = useState(2);
  const [jobTimeoutSeconds, setJobTimeoutSeconds] = useState(1200);
  const [subagentsEnabled, setSubagentsEnabled] = useState(false);
  const [agentProfiles, setAgentProfiles] = useState<Record<string, CodexFleetAgentProfile>>(DEFAULT_AGENT_PROFILES);
  const [localProjectNotice, setLocalProjectNotice] = useState<{ tone: "info" | "error"; message: string } | null>(null);
  const [localConnectionState, setLocalConnectionState] = useState<CodexFleetConnectionState>("checking");
  const [localConnectionMessage, setLocalConnectionMessage] = useState("Checking codex-fleet local launcher...");
  // form info
  const methods = useForm<TProject>({
    defaultValues: { ...getProjectFormValues(), ...data },
    reValidateMode: "onChange",
  });
  const {
    handleSubmit,
    reset,
    setValue,
    formState: { isSubmitting },
  } = methods;
  const { isMobile } = usePlatformOS();
  const handleAddToFavorites = (projectId: string) => {
    if (!workspaceSlug) return;

    addProjectToFavorites(workspaceSlug.toString(), projectId).catch(() => {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: t("failed_to_remove_project_from_favorites"),
      });
    });
  };

  const localSettings = () => {
    return readCodexFleetLocalConnection();
  };

  const localApi = () =>
    new CodexFleetLocalApi({
      baseUrl: localSettings().apiUrl,
      token: localSettings().token || null,
    });

  const currentReconnectPath = () => {
    if (typeof window === "undefined") return "codex-fleet/projects/";
    return `${window.location.pathname}${window.location.search}`.replace(/^\/+/, "") || "codex-fleet/projects/";
  };

  const reconnectCodexFleet = () => {
    const { apiUrl } = localSettings();
    const api = new CodexFleetLocalApi({ baseUrl: apiUrl, token: null });
    window.location.assign(api.connectUrl(currentReconnectPath()));
  };

  const refreshLocalConnection = useCallback(async () => {
    const connection = await ensureCodexFleetLocalConnection();
    if (connection.status === "connected" && connection.token) {
      setLocalConnectionState("connected");
      setLocalConnectionMessage("Local launcher connected");
      return connection;
    }
    if (connection.status === "unreachable") {
      setLocalConnectionState("unreachable");
      setLocalConnectionMessage(connection.message || "Codex Fleet is not reachable. Start it with make up, then reconnect.");
      return connection;
    }
    setLocalConnectionState("missing_token");
    setLocalConnectionMessage(connection.message || "Reconnect Codex Fleet to allow local folder access.");
    return connection;
  }, []);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const connection = await refreshLocalConnection();
        if (cancelled) return;
        if (connection.status === "connected" && connection.token) {
          setLocalConnectionState("connected");
          setLocalConnectionMessage("Local launcher connected");
          return;
        }
      } catch {
        if (!cancelled) {
          setLocalConnectionState("missing_token");
          setLocalConnectionMessage("Start codex-fleet from your terminal: make up. Use the browser window opened by codex-fleet.");
        }
        return;
      }
    };
    void check();
    const onSessionMayHaveChanged = () => void check();
    window.addEventListener("hashchange", onSessionMayHaveChanged);
    window.addEventListener("focus", onSessionMayHaveChanged);
    window.addEventListener("storage", onSessionMayHaveChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("hashchange", onSessionMayHaveChanged);
      window.removeEventListener("focus", onSessionMayHaveChanged);
      window.removeEventListener("storage", onSessionMayHaveChanged);
    };
  }, [refreshLocalConnection]);

  const codexFleetFragment = () => {
    const { apiUrl } = localSettings();
    const params = new URLSearchParams({ apiUrl });
    return params.toString();
  };

  const setProjectMode = (mode: CodexFleetProjectMode) => {
    setCodexProjectMode(mode);
    setLocalProjectNotice(null);
    if (mode === "create" && localProjectPath && !localProjectParentPath) {
      setLocalProjectParentPath(localProjectPath);
      setLocalProjectPath("");
    }
    if (mode === "link" && localProjectParentPath && !localProjectPath) {
      setLocalProjectPath(localProjectParentPath);
      setLocalProjectParentPath("");
    }
  };

  const chooseFolder = async () => {
    let connection;
    try {
      connection = await refreshLocalConnection();
    } catch {
      connection = localSettings();
    }
    if (connection.status !== "connected" || !connection.token) {
      const message = connection.message || localConnectionMessage;
      setLocalProjectNotice({ tone: "error", message });
      return;
    }
    setLocalProjectNotice({
      tone: "info",
      message: codexProjectMode === "create" ? "Choose the parent folder for the new project." : "Choose the project folder to link.",
    });
    try {
      const folder = await new CodexFleetLocalApi({ baseUrl: connection.apiUrl, token: connection.token }).pickFolder();
      if (codexProjectMode === "create") {
        setLocalProjectParentPath(folder.path);
      } else {
        setLocalProjectPath(folder.path);
      }
      if (codexProjectMode === "link" && !methods.getValues("name")) {
        setValue("name", folder.name);
      }
      setLocalProjectNotice({
        tone: "info",
        message: codexProjectMode === "create" ? `New project will be created inside ${folder.path}.` : `Linked folder: ${folder.path}.`,
      });
    } catch (error) {
      const code = error instanceof CodexFleetLocalApiError ? error.code : "picker_unavailable";
      if (code === "picker_cancelled") {
        setLocalProjectNotice(null);
        return;
      }
      setShowManualPath(true);
      const message =
        code === "auth_missing"
          ? "Start codex-fleet from your terminal: make up. Use the browser window opened by codex-fleet."
          : code === "api_unreachable"
            ? "codex-fleet local API is not reachable. Keep make up running; Brave or Safari may need localhost/private-network access enabled."
            : "The folder picker is unavailable. Paste the folder path below and keep codex-fleet up running.";
      setLocalProjectNotice({
        tone: "error",
        message,
      });
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Folder picker unavailable",
        message,
      });
    }
  };

  const onSubmit = async (formData: Partial<TProject>) => {
    // Upper case identifier
    formData.identifier = formData.identifier?.toUpperCase();
    const linkedPath = localProjectPath.trim();
    const parentPath = localProjectParentPath.trim();
    const connection = await refreshLocalConnection().catch(() => localSettings());
    const localApiUrl = connection.apiUrl;
    const localToken = connection.token;
    const requiredPath = codexProjectMode === "create" ? parentPath : linkedPath;
    if (workflowMode === "full_auto" && !subagentsEnabled) {
      const message = "Full auto needs subagents so Codex Fleet can plan, implement, test, review, and deliver safely.";
      setLocalProjectNotice({ tone: "error", message });
      setToast({ type: TOAST_TYPE.ERROR, title: "Enable subagents", message });
      return Promise.reject(new Error(message));
    }
    if (!requiredPath) {
      const message =
        codexProjectMode === "create"
          ? "Choose a parent folder or paste its path before creating this codex-fleet project."
          : "Choose a project folder or paste its path before creating this codex-fleet project.";
      setShowManualPath(true);
      setLocalProjectNotice({ tone: "error", message });
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Folder required",
        message,
      });
      return Promise.reject(new Error(message));
    }
    if (linkedPath || (codexProjectMode === "create" && parentPath)) {
      if (!localToken || connection.status !== "connected") {
        setLocalProjectNotice({
          tone: "error",
          message: connection.message || localConnectionMessage,
        });
        setToast({
          type: TOAST_TYPE.ERROR,
          title: "codex-fleet is not connected",
          message: connection.message || localConnectionMessage,
        });
        return Promise.reject(new Error(connection.message || localConnectionMessage));
      }
      try {
        setLocalProjectNotice({
          tone: "info",
          message:
            codexProjectMode === "create"
              ? "Creating the starter project, applying the harness, and linking it to this board."
              : "Applying the harness and linking this folder to the board.",
        });
        const result = await new CodexFleetLocalApi({ baseUrl: localApiUrl, token: localToken }).createProject({
          ...(codexProjectMode === "create"
            ? {
                create_new: true,
                location: parentPath,
                folder_slug: formData.identifier?.trim() || undefined,
                project_type: localProjectType,
              }
            : { path: linkedPath }),
          name: formData.name?.trim() || undefined,
          apply_harness: applyHarness,
          initial_goal: initialGoal.trim() || undefined,
          start_initial_goal: startInitialGoal,
          workflow_mode: workflowMode,
          require_plane_mapping: true,
          codex_settings: {
            runner_mode: runnerMode,
            default_model: defaultModel,
            reasoning_effort: reasoningEffort,
            approval_policy: approvalPolicy,
            sandbox_mode: sandboxMode,
            max_parallel_agents: maxParallelAgents,
            max_depth: maxTaskDepth,
            job_timeout_seconds: jobTimeoutSeconds,
            workflow_mode: workflowMode,
            subagents_enabled: subagentsEnabled,
            enabled_agent_roles: Object.entries(agentProfiles).filter(([, profile]) => profile.enabled).map(([role]) => role),
            agent_profiles: Object.fromEntries(Object.entries(agentProfiles).map(([role, profile]) => [
              role,
              {
                model: profile.model,
                reasoning_effort: profile.reasoning_effort,
                sandbox_mode: profile.sandbox_mode,
                enabled: profile.enabled,
              },
            ])),
            subagents: Object.fromEntries(Object.entries(agentProfiles).map(([role, profile]) => [
              role,
              {
                model: profile.model,
                reasoning_effort: profile.reasoning_effort,
                sandbox_mode: profile.sandbox_mode,
              },
            ])),
          },
        });
        const planeProjectId = result.plane?.project_id || result.project.plane_project_id;
        if (!planeProjectId) throw new Error(result.plane?.reason || "codex-fleet did not return a linked project id.");
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("success"),
          message: result.initial_item
            ? `Project linked. ${result.initial_item.identifier} is Ready.`
            : "Project created and linked to codex-fleet.",
        });
        setLocalProjectNotice({
          tone: "info",
          message: (result.setup_log || []).join(" "),
        });
        onClose();
        window.location.assign(
          `/${workspaceSlug.toString()}/projects/${planeProjectId}/issues/#${codexFleetFragment()}`
        );
        return;
      } catch (error) {
        setLocalProjectNotice({
          tone: "error",
          message: error instanceof Error ? error.message : "The local folder was not mapped. Check codex-fleet up and try again.",
        });
        setToast({
          type: TOAST_TYPE.ERROR,
          title: "codex-fleet project setup failed",
          message: error instanceof Error ? error.message : "The local folder was not mapped.",
        });
        return Promise.reject(error);
      }
    }
    const coverImage = formData.cover_image_url;
    let uploadedAssetUrl: string | null = null;

    if (coverImage) {
      const imageType = getCoverImageType(coverImage);

      if (imageType === "local_static") {
        try {
          uploadedAssetUrl = await uploadCoverImage(coverImage, {
            workspaceSlug: workspaceSlug.toString(),
            entityIdentifier: "",
            entityType: EFileAssetType.PROJECT_COVER,
            isUserAsset: false,
          });
        } catch (error) {
          console.error("Error uploading cover image:", error);
          setToast({
            type: TOAST_TYPE.ERROR,
            title: t("toast.error"),
            message: error instanceof Error ? error.message : "Failed to upload cover image",
          });
          return Promise.reject(error);
        }
      } else {
        formData.cover_image = coverImage;
        formData.cover_image_asset = null;
      }
    }

    return createProject(workspaceSlug.toString(), formData)
      .then(async (res) => {
        if (uploadedAssetUrl) {
          await updateCoverImageStatus(res.id, uploadedAssetUrl);
          await updateProject(workspaceSlug.toString(), res.id, { cover_image_url: uploadedAssetUrl });
        } else if (coverImage && coverImage.startsWith("http")) {
          await updateCoverImageStatus(res.id, coverImage);
          await updateProject(workspaceSlug.toString(), res.id, { cover_image_url: coverImage });
        }
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("success"),
          message: t("project_created_successfully"),
        });

        if (setToFavorite) {
          handleAddToFavorites(res.id);
        }
        handleNextStep(res.id);
      })
      .catch((err) => {
        try {
          // Handle the new error format where codes are nested in arrays under field names
          const errorData = err?.data ?? {};

          const nameError = errorData.name?.includes("PROJECT_NAME_ALREADY_EXIST");
          const identifierError = errorData?.identifier?.includes("PROJECT_IDENTIFIER_ALREADY_EXIST");

          if (nameError || identifierError) {
            if (nameError) {
              setToast({
                type: TOAST_TYPE.ERROR,
                title: t("toast.error"),
                message: t("project_name_already_taken"),
              });
            }

            if (identifierError) {
              setToast({
                type: TOAST_TYPE.ERROR,
                title: t("toast.error"),
                message: t("project_identifier_already_taken"),
              });
            }
          } else {
            setToast({
              type: TOAST_TYPE.ERROR,
              title: t("toast.error"),
              message: t("something_went_wrong"),
            });
          }
        } catch (error) {
          // Fallback error handling if the error processing fails
          console.error("Error processing API error:", error);
          setToast({
            type: TOAST_TYPE.ERROR,
            title: t("toast.error"),
            message: t("something_went_wrong"),
          });
        }
      });
  };

  const handleClose = () => {
    onClose();
    setShouldAutoSyncIdentifier(true);
    setTimeout(() => {
      reset();
    }, 300);
  };

  return (
    <FormProvider {...methods}>
      <ProjectCreateHeader handleClose={handleClose} isMobile={isMobile} />

      <form onSubmit={handleSubmit(onSubmit)} className="px-3">
        <div className="mt-9 space-y-6 pb-5">
          <ProjectCommonAttributes
            setValue={setValue}
            isMobile={isMobile}
            shouldAutoSyncIdentifier={shouldAutoSyncIdentifier}
            setShouldAutoSyncIdentifier={setShouldAutoSyncIdentifier}
          />
          <ProjectAttributes isMobile={isMobile} />
          {isSubmitting ? (
            <div className="rounded-lg border border-custom-primary-100/30 bg-custom-primary-100/10 px-4 py-3 text-sm text-custom-text-200">
              <p className="font-semibold text-custom-text-100">Setting up your local Codex workspace...</p>
              <p className="mt-1 text-xs leading-5 text-custom-text-300">
                Creating the board, preparing the repo, applying the harness, and adding the first Ready work item. The work-items page can take a moment to hydrate after this finishes.
              </p>
            </div>
          ) : null}
          <div className="overflow-hidden rounded-lg border border-custom-border-200 bg-custom-background-90 shadow-sm">
            <div className="border-b border-custom-border-200 bg-custom-background-100 px-5 py-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-base font-semibold text-custom-text-100">Codex workspace setup</h3>
                  <p className="mt-1 max-w-2xl text-sm leading-6 text-custom-text-300">
                    Connect this Plane project to a local repo, choose Codex defaults, and start with a Ready task.
                  </p>
                </div>
                <span className="shrink-0 rounded-md border border-custom-border-200 bg-custom-background-100 px-2.5 py-1 text-[11px] font-medium text-custom-text-300">
                  local only
                </span>
              </div>
            </div>

            <div className="grid gap-5 px-5 py-5">
              <div
                className={`flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs leading-5 ${
                  localConnectionState === "connected"
                    ? "border-green-500/30 bg-green-500/10 text-green-200"
                    : "border-amber-500/30 bg-amber-500/10 text-amber-100"
                }`}
              >
                <span>
                  <span className="font-semibold">
                    {localConnectionState === "connected" ? "Local launcher connected" : "Local launcher not connected"}
                  </span>
                  <span className="ml-2">{localConnectionMessage}</span>
                </span>
                {localConnectionState === "missing_token" ? (
                  <button
                    type="button"
                    className="h-8 rounded-md bg-custom-primary-100 px-3 text-xs font-semibold text-white hover:bg-custom-primary-200"
                    onClick={reconnectCodexFleet}
                  >
                    Reconnect Codex Fleet
                  </button>
                ) : null}
              </div>
              <section className="grid gap-3">
                <div className="flex items-center gap-3">
                  <span className="flex size-7 items-center justify-center rounded-md border border-custom-border-200 bg-custom-background-80 text-xs font-semibold text-custom-text-200">1</span>
                  <div>
                    <p className="text-sm font-semibold text-custom-text-100">Project source</p>
                    <p className="text-xs text-custom-text-400">Choose the code folder Codex will work inside.</p>
                  </div>
                </div>

                <div className="grid gap-2 sm:grid-cols-2">
                {[
                  ["create", "Create new project", "Pick a parent folder; codex-fleet creates the repo and initializes git."],
                  ["link", "Use existing repo", "Pick a folder that already has a .git repository."],
                ].map(([mode, label, description]) => (
                  <button
                    key={mode}
                    type="button"
                    aria-pressed={codexProjectMode === mode}
                    className={`rounded-md border px-3 py-3 text-left transition ${
                      codexProjectMode === mode
                        ? "border-custom-primary-100 bg-custom-primary-100/15 ring-1 ring-custom-primary-100"
                        : "border-custom-border-200 bg-custom-background-100 hover:bg-custom-background-80"
                    }`}
                    onClick={() => setProjectMode(mode as CodexFleetProjectMode)}
                  >
                    <span className="flex items-center justify-between gap-3 text-sm font-semibold text-custom-text-100">
                      {label}
                      {codexProjectMode === mode ? (
                        <span className="rounded bg-custom-primary-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-white">
                          selected
                        </span>
                      ) : null}
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-custom-text-400">{description}</span>
                  </button>
                ))}
              </div>
              {codexProjectMode === "create" ? (
                <select
                  className="h-10 rounded-md border border-custom-border-200 bg-custom-background-100 px-3 text-sm font-normal text-custom-text-100 outline-none focus:border-custom-primary-100"
                  value={localProjectType}
                  onChange={(event) => setLocalProjectType(event.target.value as CodexFleetProjectType)}
                >
                  <option value="blank">Blank repo</option>
                  <option value="simple-web">Simple web app</option>
                  <option value="node-next">Node / Next.js app</option>
                  <option value="python">Python package</option>
                </select>
              ) : null}
              <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                <button
                  type="button"
                  className="h-10 rounded-md bg-custom-primary-100 px-3 text-sm font-semibold text-white shadow-sm hover:bg-custom-primary-200 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={chooseFolder}
                  disabled={localConnectionState !== "connected"}
                >
                  {codexProjectMode === "create" ? "Choose parent folder" : "Choose folder"}
                </button>
                <button
                  type="button"
                  className="h-10 rounded-md border border-custom-border-200 px-3 text-xs font-semibold text-custom-text-200 hover:bg-custom-background-80 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => setShowManualPath((value) => !value)}
                >
                  {showManualPath ? "Hide path field" : "Paste path"}
                </button>
              </div>
              {localConnectionState !== "connected" ? (
                <p className="text-xs leading-5 text-custom-text-400">
                  {localConnectionState === "unreachable"
                    ? "Folder picking and project creation need the local Codex Fleet API. Start it with make up, then return here."
                    : "Folder picking and project creation need a connected local Codex Fleet session. Use Reconnect Codex Fleet to continue."}
                </p>
              ) : null}
              {codexProjectMode === "create" && localProjectParentPath ? (
                <div className="rounded border border-custom-border-200 bg-custom-background-100 px-3 py-2">
                  <p className="text-xs font-medium text-custom-text-100">Create inside</p>
                  <p className="mt-1 break-all text-xs text-custom-text-400">{localProjectParentPath}</p>
                </div>
              ) : null}
              {codexProjectMode === "link" && localProjectPath ? (
                <div className="rounded border border-custom-border-200 bg-custom-background-100 px-3 py-2">
                  <p className="text-xs font-medium text-custom-text-100">{localProjectPath.split("/").filter(Boolean).pop() ?? localProjectPath}</p>
                  <p className="mt-1 break-all text-xs text-custom-text-400">{localProjectPath}</p>
                </div>
              ) : null}
              {showManualPath ? (
                <input
                  className="h-9 rounded border border-custom-border-200 bg-custom-background-100 px-3 text-sm font-normal text-custom-text-100 outline-none focus:border-custom-primary-100"
                  placeholder={codexProjectMode === "create" ? "/path/to/parent/folder" : "/path/to/project"}
                  value={codexProjectMode === "create" ? localProjectParentPath : localProjectPath}
                  onChange={(event) =>
                    codexProjectMode === "create" ? setLocalProjectParentPath(event.target.value) : setLocalProjectPath(event.target.value)
                  }
                />
              ) : null}
              {localProjectNotice ? (
                <div
                  className={`rounded-md border px-3 py-2 text-xs leading-5 ${
                    localProjectNotice.tone === "error"
                      ? "border-red-500/30 bg-red-500/10 text-red-200"
                      : "border-custom-border-200 bg-custom-background-100 text-custom-text-300"
                  }`}
                >
                  {localProjectNotice.message}
                </div>
              ) : null}
              </section>

              <section className="grid gap-3 border-t border-custom-border-200 pt-5">
                <div className="flex items-center gap-3">
                  <span className="flex size-7 items-center justify-center rounded-md border border-custom-border-200 bg-custom-background-80 text-xs font-semibold text-custom-text-200">2</span>
                  <div>
                    <p className="text-sm font-semibold text-custom-text-100">First goal</p>
                    <p className="text-xs text-custom-text-400">Turn a plain request into a Ready work item that Codex can claim.</p>
                  </div>
                </div>
                <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                  What should Codex build first?
                  <textarea
                    className="min-h-24 rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-2 text-sm text-custom-text-100 outline-none placeholder:text-custom-text-400 focus:border-custom-primary-100"
                    placeholder="Example: Build a landing page with pricing and a contact form"
                    value={initialGoal}
                    onChange={(event) => setInitialGoal(event.target.value)}
                  />
                </label>
                <label className="flex items-start gap-2 text-xs text-custom-text-300">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={startInitialGoal}
                    onChange={(event) => setStartInitialGoal(event.target.checked)}
                  />
                  <span>Create this as a Ready task so codex-fleet starts automatically.</span>
                </label>
              </section>

              <section className="grid gap-3 border-t border-custom-border-200 pt-5">
                <div className="flex items-center gap-3">
                  <span className="flex size-7 items-center justify-center rounded-md border border-custom-border-200 bg-custom-background-80 text-xs font-semibold text-custom-text-200">3</span>
                  <div>
                    <p className="text-sm font-semibold text-custom-text-100">Codex settings</p>
                    <p className="text-xs text-custom-text-400">These defaults apply to every task unless the work item overrides them.</p>
                  </div>
                </div>

                <div className="grid gap-2 md:grid-cols-3">
                  <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                    Model
                    <input className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100" value={defaultModel} onChange={(event) => setDefaultModel(event.target.value)} />
                  </label>
                  <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                    Reasoning
                    <select className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100" value={reasoningEffort} onChange={(event) => setReasoningEffort(event.target.value)}>
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                      <option value="xhigh">xhigh</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                    Approval
                    <select className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100" value={approvalPolicy} onChange={(event) => setApprovalPolicy(event.target.value)}>
                      <option value="on-request">on-request</option>
                      <option value="never">never</option>
                      <option value="untrusted">untrusted</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                    Sandbox
                    <select className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100" value={sandboxMode} onChange={(event) => setSandboxMode(event.target.value)}>
                      <option value="workspace-write">workspace-write</option>
                      <option value="read-only">read-only</option>
                      <option value="danger-full-access">danger-full-access</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                    Automation mode
                    <select className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100" value={workflowMode} onChange={(event) => setWorkflowMode(event.target.value as CodexFleetWorkflowMode)}>
                      <option value="execute_only">Execute only</option>
                      <option value="plan_only">Plan only</option>
                      <option value="plan_execute">Plan and execute</option>
                      <option value="full_auto">Full auto</option>
                    </select>
                  </label>
                </div>

                <details className="rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-2 text-xs text-custom-text-300">
                  <summary className="cursor-pointer font-semibold text-custom-text-200">Execution limits</summary>
                  <div className="mt-3 grid gap-2 md:grid-cols-3">
                    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                      Max agents
                      <input type="number" min={1} className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100" value={maxParallelAgents} onChange={(event) => setMaxParallelAgents(Number(event.target.value) || 1)} />
                    </label>
                    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                    Max depth
                      <input type="number" min={1} className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100" value={maxTaskDepth} onChange={(event) => setMaxTaskDepth(Number(event.target.value) || 1)} />
                    </label>
                    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
                      Timeout seconds
                      <input type="number" min={60} className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100" value={jobTimeoutSeconds} onChange={(event) => setJobTimeoutSeconds(Number(event.target.value) || 1200)} />
                    </label>
                  </div>
                </details>

                <section className="grid gap-3 rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-3">
                  <label className="flex items-start gap-2 text-xs text-custom-text-300">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={subagentsEnabled}
                      onChange={(event) => setSubagentsEnabled(event.target.checked)}
                    />
                    <span>
                      Enable Codex subagents
                      <span className="block text-custom-text-400">
                        Required for Full auto. Codex Fleet will use only the roles needed for each task.
                      </span>
                    </span>
                  </label>
                  {workflowMode === "full_auto" && !subagentsEnabled ? (
                    <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                      Full auto needs subagents so Codex Fleet can plan, implement, test, review, and deliver safely.
                    </div>
                  ) : null}
                  <div className="grid gap-2">
                    {Object.entries(agentProfiles).map(([role, profile]) => (
                      <div key={role} className="grid gap-2 rounded border border-custom-border-200 px-2 py-2 md:grid-cols-[minmax(150px,1fr)_minmax(120px,160px)_minmax(120px,160px)_minmax(140px,180px)]">
                        <label className="flex items-start gap-2">
                          <input
                            type="checkbox"
                            className="mt-1"
                            disabled={!subagentsEnabled || role === "planner" || role === "implementer" || role === "delivery_manager"}
                            checked={Boolean(profile.enabled)}
                            onChange={(event) => setAgentProfiles((current) => ({ ...current, [role]: { ...current[role], enabled: event.target.checked } }))}
                          />
                          <span>
                            <span className="block font-semibold text-custom-text-100">{profile.label}</span>
                            <span className="block text-custom-text-400">{profile.purpose}</span>
                          </span>
                        </label>
                        <input
                          className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100"
                          value={profile.model}
                          onChange={(event) => setAgentProfiles((current) => ({ ...current, [role]: { ...current[role], model: event.target.value } }))}
                        />
                        <select
                          className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100"
                          value={profile.reasoning_effort}
                          onChange={(event) => setAgentProfiles((current) => ({ ...current, [role]: { ...current[role], reasoning_effort: event.target.value } }))}
                        >
                          <option value="low">low</option>
                          <option value="medium">medium</option>
                          <option value="high">high</option>
                          <option value="xhigh">xhigh</option>
                        </select>
                        <select
                          className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100"
                          value={profile.sandbox_mode}
                          onChange={(event) => setAgentProfiles((current) => ({ ...current, [role]: { ...current[role], sandbox_mode: event.target.value } }))}
                        >
                          <option value="workspace-write">workspace-write</option>
                          <option value="read-only">read-only</option>
                        </select>
                      </div>
                    ))}
                  </div>
                </section>

                <label className="flex items-start gap-2 rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-2 text-xs text-custom-text-300">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={applyHarness}
                    onChange={(event) => setApplyHarness(event.target.checked)}
                  />
                  <span>
                    Add agent harness
                    <span className="block text-custom-text-400">
                      AGENTS.md, workflow guidance, Codex config, and starter subagent guidance.
                    </span>
                  </span>
                </label>
              </section>
            </div>
          </div>
        </div>
        <ProjectCreateButtons handleClose={handleClose} />
      </form>
    </FormProvider>
  );
});
