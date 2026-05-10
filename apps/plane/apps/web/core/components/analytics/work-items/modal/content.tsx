/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useMemo, useState } from "react";
import { observer } from "mobx-react";
// plane package imports
import type { ICycle, IModule, IProject } from "@plane/types";
import { Spinner } from "@plane/ui";
// codex-fleet
import {
  CodexFleetLocalApi,
  DEFAULT_CODEX_FLEET_API_URL,
  ensureCodexFleetLocalConnection,
  readCodexFleetLocalConnection,
  type CodexFleetLocalConnection,
  type CodexFleetLogs,
} from "@/app/codex-fleet/local-api";

type Props = {
  fullScreen: boolean;
  projectDetails: IProject | undefined;
  cycleDetails: ICycle | undefined;
  moduleDetails: IModule | undefined;
  isEpic?: boolean;
};

export const WorkItemsModalMainContent = observer(function WorkItemsModalMainContent(props: Props) {
  const { projectDetails } = props;
  const [settings, setSettings] = useState<CodexFleetLocalConnection>(() => readCodexFleetLocalConnection());
  const [logs, setLogs] = useState<CodexFleetLogs | null>(null);
  const [message, setMessage] = useState("Loading codex-fleet logs...");
  const [loading, setLoading] = useState(false);
  const api = useMemo(
    () => new CodexFleetLocalApi({ baseUrl: settings.apiUrl, token: settings.token || null }),
    [settings.apiUrl, settings.token]
  );

  const load = async () => {
    if (!projectDetails?.id) {
      setMessage("Open Fleet Logs from a project board.");
      return;
    }
    if (!settings.token) {
      setMessage("Local launcher token missing. Start codex-fleet with make up and use the opened Plane URL.");
      return;
    }
    setLoading(true);
    setMessage("Loading codex-fleet logs...");
    try {
      const result = await api.fleetLogs(projectDetails.id);
      setLogs(result.fleet_logs);
      setMessage(result.fleet_logs.message || "Fleet Logs loaded.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet logs unavailable.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    ensureCodexFleetLocalConnection()
      .then((connection) => {
        if (mounted) setSettings(connection);
      })
      .catch((error) => {
        if (mounted) {
          setSettings({ apiUrl: DEFAULT_CODEX_FLEET_API_URL, token: "" });
          setMessage(error instanceof Error ? error.message : "Local launcher session expired. Reopen Plane from make up.");
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectDetails?.id, settings.token, settings.apiUrl]);

  if (loading && !logs) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="grid gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-subtle bg-surface-2 p-4">
        <div>
          <h3 className="text-base font-semibold text-primary">Fleet Logs</h3>
          <p className="mt-1 text-sm text-secondary">{message}</p>
          {logs?.repo ? <p className="mt-2 break-all text-xs text-tertiary">{logs.repo}</p> : null}
          {logs?.linked === false ? (
            <p className="mt-2 break-all text-xs text-tertiary">Plane project {logs.plane_project_id}</p>
          ) : null}
        </div>
        <button
          type="button"
          className="h-8 rounded-md border border-subtle px-3 text-xs font-semibold text-primary hover:bg-surface-1"
          onClick={() => void load()}
        >
          Refresh
        </button>
      </div>

      {logs ? (
        <>
          <div className="grid gap-3 md:grid-cols-4">
            <Metric label="Runs" value={String(logs.analytics.runs_total)} />
            <Metric label="Active" value={String(logs.analytics.active_runs)} />
            <Metric label="Tokens" value={String(logs.analytics.total_tokens || 0)} />
            <Metric label="Tasks" value={String(logs.tasks.length)} />
          </div>

          <section className="grid gap-3 rounded-lg border border-subtle bg-surface-2 p-4">
            <h4 className="text-sm font-semibold text-primary">Virtual agents</h4>
            <div className="grid gap-2 md:grid-cols-2">
              {logs.analytics.by_role.length ? (
                logs.analytics.by_role.map((agent) => (
                  <div key={agent.role} className="rounded-md border border-subtle bg-surface-1 p-3 text-xs">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-semibold text-primary">{agent.role}</span>
                      <span className="text-tertiary">{agent.runs} runs</span>
                    </div>
                    <p className="mt-2 text-secondary">
                      success {agent.success} - failed {agent.failed} - active {agent.active} - cancelled {agent.cancelled}
                    </p>
                    <p className="mt-1 text-tertiary">tokens {agent.total_tokens || 0}</p>
                  </div>
                ))
              ) : (
                <p className="text-sm text-secondary">No agent runs recorded yet.</p>
              )}
            </div>
          </section>

          <section className="grid gap-3 rounded-lg border border-subtle bg-surface-2 p-4">
            <h4 className="text-sm font-semibold text-primary">Task tree</h4>
            {logs.tasks.length ? (
              logs.tasks.map((task) => (
                <div key={task.item_id} className="rounded-md border border-subtle bg-surface-1 p-3 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-primary">{task.source}</span>
                    <span className="text-tertiary">depth {task.depth}</span>
                  </div>
                  <p className="mt-2 break-all text-secondary">
                    item {task.item_id}
                    {task.parent_identifier ? ` - parent ${task.parent_identifier}` : ""}
                  </p>
                  {task.latest_run ? (
                    <p className="mt-1 text-tertiary">
                      latest {task.latest_run.status} - {task.latest_run.agent_name || task.latest_run.agent_role || "agent"}
                    </p>
                  ) : null}
                </div>
              ))
            ) : (
              <p className="text-sm text-secondary">No child or delivery tasks recorded yet.</p>
            )}
          </section>

          <section className="grid gap-3 rounded-lg border border-subtle bg-surface-2 p-4">
            <h4 className="text-sm font-semibold text-primary">Recent events</h4>
            {logs.recent_events.length ? (
              <ol className="grid gap-2">
                {logs.recent_events.slice(0, 30).map((event) => (
                  <li key={`${event.run_id || "run"}-${event.id}`} className="border-l border-subtle pl-3 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium text-primary">{event.kind.replaceAll("_", " ")}</span>
                      <span className="text-tertiary">{event.created_at}</span>
                    </div>
                    <p className="mt-1 break-all text-secondary">run {event.run_id || "unknown"}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-secondary">No events recorded yet.</p>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
});

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-subtle bg-surface-2 p-4">
      <p className="text-xs font-medium text-tertiary">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-primary">{value}</p>
    </div>
  );
}
