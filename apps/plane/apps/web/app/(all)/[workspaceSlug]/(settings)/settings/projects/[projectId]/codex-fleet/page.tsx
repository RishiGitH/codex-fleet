"use client";

/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { Bot } from "lucide-react";
import { Breadcrumbs } from "@plane/ui";
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { SettingsContentWrapper } from "@/components/settings/content-wrapper";
import { SettingsPageHeader } from "@/components/settings/page-header";
import {
  CodexFleetLocalApi,
  DEFAULT_CODEX_FLEET_API_URL,
  ensureCodexFleetLocalConnection,
  type CodexFleetProject,
  type CodexFleetProjectSettings,
} from "@/app/codex-fleet/local-api";

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
  subagents: {
    code_scout: { model: "gpt-5.4-mini", reasoning_effort: "medium", sandbox_mode: "read-only" },
    implementer: { model: "gpt-5.5", reasoning_effort: "low", sandbox_mode: "workspace-write" },
    harness_reviewer: { model: "gpt-5.4-mini", reasoning_effort: "high", sandbox_mode: "read-only" },
    security_reviewer: { model: "gpt-5.5", reasoning_effort: "medium", sandbox_mode: "read-only" },
    token_reviewer: { model: "gpt-5.4-mini", reasoning_effort: "high", sandbox_mode: "read-only" },
  },
};

type Props = {
  params: {
    projectId: string;
  };
};

function CodexFleetProjectSettingsPage({ params }: Props) {
  const [apiUrl, setApiUrl] = useState(DEFAULT_CODEX_FLEET_API_URL);
  const [token, setToken] = useState("");
  const [project, setProject] = useState<CodexFleetProject | null>(null);
  const [settings, setSettings] = useState<CodexFleetProjectSettings>(emptySettings);
  const [message, setMessage] = useState("Connect to codex-fleet.");
  const [busy, setBusy] = useState(false);

  const api = useMemo(() => new CodexFleetLocalApi({ baseUrl: apiUrl, token: token || null }), [apiUrl, token]);

  useEffect(() => {
    ensureCodexFleetLocalConnection()
      .then((local) => {
        setApiUrl(local.apiUrl);
        setToken(local.token);
      })
      .catch((error) => {
        setMessage(error instanceof Error ? error.message : "Local launcher session expired. Reopen Plane from make up.");
      });
  }, []);

  const load = async () => {
    setBusy(true);
    setMessage("Loading codex-fleet project settings...");
    try {
      const result = await api.projectSettings(params.projectId);
      setProject(result.project);
      setSettings(result.settings);
      setMessage("codex-fleet settings loaded.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet settings unavailable.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (token) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, params.projectId]);

  const save = async () => {
    setBusy(true);
    setMessage("Saving codex-fleet settings...");
    try {
      const result = await api.updateProjectSettings(params.projectId, settings);
      setProject(result.project);
      setSettings(result.settings);
      setMessage("codex-fleet settings saved.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet settings were not saved.");
    } finally {
      setBusy(false);
    }
  };

  const update = (patch: Partial<CodexFleetProjectSettings>) => setSettings((current) => ({ ...current, ...patch }));

  return (
    <SettingsContentWrapper
      header={
        <SettingsPageHeader
          leftItem={
            <Breadcrumbs>
              <Breadcrumbs.Item component={<BreadcrumbLink label="codex-fleet" icon={<Bot className="size-4 text-tertiary" />} />} />
            </Breadcrumbs>
          }
        />
      }
    >
      <div className="grid gap-6">
        <section className="rounded-xl border border-custom-border-200 bg-custom-background-90 p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold text-custom-text-100">codex-fleet project settings</h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-custom-text-300">
                Configure how this Plane project maps to a local repo, how Codex runs, and how agent-created tasks enter the board.
              </p>
            </div>
            <span className="rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-1.5 text-xs font-semibold text-custom-text-300">
              {token ? "Local API connected" : "Launcher token needed"}
            </span>
          </div>
          <div className="mt-5 grid gap-3 text-sm md:grid-cols-3">
            <Info label="Repo folder" value={project?.repo_path || "Not linked"} />
            <Info label="Harness" value={project?.harness_status || "Unknown"} />
            <Info label="Runner" value="Codex App Server" />
          </div>
          <p className="mt-4 rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-2 text-xs text-custom-text-300">
            {busy ? "Working..." : message}
          </p>
        </section>

        <section className="grid gap-4">
          <h3 className="text-base font-semibold text-custom-text-100">Codex run defaults</h3>
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Default model" value={settings.default_model} onChange={(value) => update({ default_model: value })} />
            <Select label="Reasoning" value={settings.reasoning_effort} onChange={(value) => update({ reasoning_effort: value })} options={["low", "medium", "high", "xhigh"]} />
            <Select
              label="Local edit approval"
              value={settings.approval_policy}
              onChange={(value) => update({ approval_policy: value })}
              options={[
                ["never", "Auto local edits"],
                ["on-request", "Ask before risky actions"],
                ["untrusted", "Untrusted sandbox"],
              ]}
            />
            <Select label="Sandbox" value={settings.sandbox_mode} onChange={(value) => update({ sandbox_mode: value })} options={["workspace-write", "read-only", "danger-full-access"]} />
            <Select
              label="Automation mode"
              value={settings.workflow_mode}
              onChange={(value) => update({ workflow_mode: value as CodexFleetProjectSettings["workflow_mode"] })}
              options={[
                ["execute_only", "Execute only"],
                ["plan_only", "Plan only"],
                ["plan_execute", "Plan and execute"],
                ["full_auto", "Full auto"],
              ]}
            />
            <NumberField label="Max parallel agents" value={settings.max_parallel_agents} onChange={(value) => update({ max_parallel_agents: value })} />
            <NumberField label="Max depth" value={settings.max_depth} onChange={(value) => update({ max_depth: value })} />
            <NumberField label="Max child tasks per run" value={settings.max_child_tasks_per_run} onChange={(value) => update({ max_child_tasks_per_run: value })} />
            <NumberField label="Timeout seconds" value={settings.job_timeout_seconds} onChange={(value) => update({ job_timeout_seconds: value })} />
          </div>
          <p className="rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-2 text-xs leading-5 text-custom-text-300">
            Runs happen in isolated git worktrees. codex-fleet never merges or deploys automatically.
          </p>
        </section>

        <section className="grid gap-4">
          <h3 className="text-base font-semibold text-custom-text-100">Subagent defaults</h3>
          <div className="grid gap-3">
            {Object.entries(settings.subagents).map(([name, agent]) => (
              <div key={name} className="grid gap-3 rounded-lg border border-custom-border-200 bg-custom-background-90 p-3 md:grid-cols-[1fr_1fr_1fr_1fr]">
                <div className="text-sm font-semibold text-custom-text-100">{name}</div>
                <Field
                  label="Model"
                  value={agent.model}
                  onChange={(value) =>
                    update({ subagents: { ...settings.subagents, [name]: { ...agent, model: value } } })
                  }
                />
                <Select
                  label="Reasoning"
                  value={agent.reasoning_effort}
                  onChange={(value) =>
                    update({ subagents: { ...settings.subagents, [name]: { ...agent, reasoning_effort: value } } })
                  }
                  options={["low", "medium", "high", "xhigh"]}
                />
                <Select
                  label="Sandbox"
                  value={agent.sandbox_mode}
                  onChange={(value) =>
                    update({ subagents: { ...settings.subagents, [name]: { ...agent, sandbox_mode: value } } })
                  }
                  options={["read-only", "workspace-write", "danger-full-access"]}
                />
              </div>
            ))}
          </div>
        </section>

        <div className="flex justify-end">
          <button
            type="button"
            className="h-10 rounded-md bg-custom-primary-100 px-4 text-sm font-semibold text-white disabled:opacity-50"
            disabled={busy || !token}
            onClick={save}
          >
            Save codex-fleet settings
          </button>
        </div>
      </div>
    </SettingsContentWrapper>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-custom-border-200 bg-custom-background-100 px-3 py-2">
      <p className="text-xs font-medium text-custom-text-400">{label}</p>
      <p className="mt-1 break-all text-sm font-semibold text-custom-text-100">{value}</p>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
      {label}
      <input className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100 outline-none focus:border-custom-primary-100" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
      {label}
      <input type="number" min={1} className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100 outline-none focus:border-custom-primary-100" value={value} onChange={(event) => onChange(Number(event.target.value) || 1)} />
    </label>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: (string | [string, string])[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid gap-1 text-xs font-medium text-custom-text-300">
      {label}
      <select className="h-9 rounded-md border border-custom-border-200 bg-custom-background-100 px-2 text-sm text-custom-text-100 outline-none focus:border-custom-primary-100" value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => {
          const [optionValue, optionLabel] = Array.isArray(option) ? option : [option, option];
          return (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
          );
        })}
      </select>
    </label>
  );
}

export default CodexFleetProjectSettingsPage;
