import { useState } from "react";

import { CodexFleetLocalApi } from "./local-api";
import type { CodexFleetHarness, CodexFleetProject } from "./local-api";

type BootstrapParams = {
  apiUrl: string;
  path: string;
  token: string;
};

const readBootstrapParams = (): BootstrapParams => {
  if (typeof window === "undefined") return { apiUrl: "", path: "", token: "" };
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return {
    apiUrl: params.get("apiUrl") ?? "",
    path: params.get("path") ?? "",
    token: params.get("token") ?? window.localStorage.getItem("codexFleetLocalToken") ?? "",
  };
};

export default function CodexFleetOnboarding() {
  const [apiUrl, setApiUrl] = useState(() => readBootstrapParams().apiUrl);
  const [path, setPath] = useState(() => readBootstrapParams().path);
  const [showManualPath, setShowManualPath] = useState(true);
  const [token, setToken] = useState(() => readBootstrapParams().token);
  const [message, setMessage] = useState("Choose a project folder.");
  const [project, setProject] = useState<CodexFleetProject | null>(null);
  const [harness, setHarness] = useState<CodexFleetHarness | null>(null);
  const [busy, setBusy] = useState(false);

  const api = () => new CodexFleetLocalApi({ baseUrl: apiUrl.trim() || undefined, token: token.trim() || null });

  const chooseFolder = async () => {
    setBusy(true);
    setMessage("Opening folder picker...");
    try {
      if (token.trim()) window.localStorage.setItem("codexFleetLocalToken", token.trim());
      if (apiUrl.trim()) window.localStorage.setItem("codexFleetLocalApiUrl", apiUrl.trim());
      const folder = await api().pickFolder();
      setPath(folder.path);
      setMessage(`${folder.name} selected.`);
    } catch (error) {
      setShowManualPath(true);
      setMessage(error instanceof Error ? error.message : "Paste the folder path instead.");
    } finally {
      setBusy(false);
    }
  };

  const bootstrap = async () => {
    setBusy(true);
    setMessage("Connecting to codex-fleet...");
    try {
      if (token.trim()) window.localStorage.setItem("codexFleetLocalToken", token.trim());
      if (apiUrl.trim()) window.localStorage.setItem("codexFleetLocalApiUrl", apiUrl.trim());
      const result = await api().bootstrap(path.trim() || undefined);
      setProject(result.project);
      setHarness(result.harness);
      setMessage(
        result.harness.status === "ready"
          ? `${result.project.name} is ready for Codex tasks.`
          : `${result.project.name} needs harness setup.`
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet is not reachable.");
    } finally {
      setBusy(false);
    }
  };

  const dashboardUrl = `/codex-fleet/projects/#${new URLSearchParams({
    apiUrl: apiUrl.trim() || "http://127.0.0.1:18790",
    token: token.trim(),
  }).toString()}`;

  const applyProjectHarness = async () => {
    if (!project) return;
    setBusy(true);
    setMessage("Writing harness files...");
    try {
      const result = await api().applyHarness(project.id);
      setHarness(result.harness);
      setMessage(
        result.harness.status === "ready"
          ? `${project.name} harness is ready.`
          : `${project.name} still needs harness setup.`
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "codex-fleet is not reachable.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-dvh bg-[#0B1020] text-white">
      <section className="mx-auto flex w-full max-w-3xl flex-col justify-center gap-8 px-6 py-12">
        <div className="flex items-center gap-4">
          <img src="/codex-fleet-logo.svg" alt="codex-fleet" className="h-14 w-14" />
          <div>
            <h1 className="text-3xl font-semibold tracking-normal">codex-fleet</h1>
            <p className="mt-1 text-sm text-slate-300">Connection setup fallback</p>
          </div>
        </div>

        <div className="grid gap-4 rounded-lg border border-white/10 bg-white/[0.04] p-5">
          <div className="grid gap-2">
            <a
              className="flex h-11 items-center justify-center rounded-md bg-cyan-300 px-4 text-sm font-semibold text-slate-950"
              href={dashboardUrl}
            >
              Open project dashboard
            </a>
            <p className="text-pretty text-xs leading-5 text-slate-400">
              Use this page only if the dashboard cannot reach your local codex-fleet API. Project creation now lives in Plane's Add Project flow.
            </p>
          </div>
          <label className="grid gap-2 text-sm text-slate-200">
            Local API
            <input
              className="h-11 rounded-md border border-white/10 bg-black/30 px-3 text-white outline-none focus:border-cyan-300"
              placeholder="http://127.0.0.1:18790"
              value={apiUrl}
              onChange={(event) => setApiUrl(event.target.value)}
            />
          </label>
          <div className="grid gap-2 text-sm text-slate-200">
            <button
              type="button"
              className="h-11 rounded-md bg-cyan-300 px-4 text-sm font-semibold text-slate-950 disabled:opacity-50"
              disabled={busy}
              onClick={chooseFolder}
            >
              Choose Folder
            </button>
            {path ? (
              <div className="rounded-md border border-white/10 bg-black/30 px-3 py-2">
                <p className="font-medium text-white">{path.split("/").filter(Boolean).pop() ?? path}</p>
                <p className="mt-1 break-all text-xs text-slate-400">{path}</p>
              </div>
            ) : null}
            <button type="button" className="w-max text-xs font-semibold text-cyan-200" onClick={() => setShowManualPath((value) => !value)}>
              {showManualPath ? "Hide pasted path" : "Paste path instead"}
            </button>
            {showManualPath ? (
              <input
                className="h-11 rounded-md border border-white/10 bg-black/30 px-3 text-white outline-none focus:border-cyan-300"
                placeholder="/path/to/project"
                value={path}
                onChange={(event) => setPath(event.target.value)}
              />
            ) : null}
          </div>
          <label className="grid gap-2 text-sm text-slate-200">
            Local token
            <input
              className="h-11 rounded-md border border-white/10 bg-black/30 px-3 text-white outline-none focus:border-cyan-300"
              placeholder="Generated by codex-fleet api"
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
          </label>
          <div className="flex items-center justify-between gap-4">
            <p className="min-h-5 text-sm text-slate-300">{message}</p>
            <div className="flex items-center gap-2">
              {project && harness?.status !== "ready" ? (
                <button
                  type="button"
                  className="h-10 rounded-md border border-white/10 px-4 text-sm font-semibold text-white disabled:opacity-50"
                  disabled={busy}
                  onClick={applyProjectHarness}
                >
                  Apply harness
                </button>
              ) : null}
              <button
                type="button"
                className="h-10 rounded-md bg-cyan-300 px-4 text-sm font-semibold text-slate-950 disabled:opacity-50"
                disabled={busy}
                onClick={bootstrap}
              >
                {busy ? "Working" : "Continue"}
              </button>
              {harness?.status === "ready" ? (
                <a
                  className="flex h-10 items-center rounded-md border border-white/10 px-4 text-sm font-semibold text-white"
                  href={dashboardUrl}
                >
                  Continue
                </a>
              ) : null}
            </div>
          </div>
          {harness ? (
            <div className="rounded-md border border-white/10 bg-black/20 p-3 text-sm text-slate-300">
              <div className="flex items-center justify-between gap-3">
                <span>Harness</span>
                <span className="font-medium text-white">{harness.status}</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                {harness.scan.stack ? <span className="rounded border border-cyan-300/30 px-2 py-0.5 text-cyan-100">{harness.scan.stack}</span> : null}
                {harness.scan.package_manager ? (
                  <span className="rounded border border-cyan-300/30 px-2 py-0.5 text-cyan-100">{harness.scan.package_manager}</span>
                ) : null}
                {harness.scan.dirty ? <span className="rounded border border-amber-300/30 px-2 py-0.5 text-amber-100">dirty</span> : null}
              </div>
              {harness.scan.git_root ? <p className="mt-2 break-all text-xs text-slate-400">{harness.scan.git_root}</p> : null}
              {Object.entries(harness.scan.commands).some(([, command]) => Boolean(command)) ? (
                <div className="mt-3 grid gap-1 text-xs">
                  {Object.entries(harness.scan.commands).map(([name, command]) =>
                    command ? (
                      <div key={name} className="grid grid-cols-[5rem_1fr] gap-2">
                        <span className="text-slate-500">{name}</span>
                        <code className="break-all text-slate-200">{command}</code>
                      </div>
                    ) : null
                  )}
                </div>
              ) : null}
              {harness.missing.length ? <p className="mt-2">{harness.missing.length} file(s) missing.</p> : null}
              {harness.scan.warnings.length ? (
                <ul className="mt-2 grid gap-1 text-xs text-amber-100">
                  {harness.scan.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>
    </main>
  );
}
