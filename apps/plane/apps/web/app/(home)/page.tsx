"use client";

import React, { useEffect, useMemo, useState } from "react";

import { CodexFleetLocalApi, ensureCodexFleetLocalConnection } from "@/app/codex-fleet/local-api";

type LocalSettings = {
  apiUrl: string;
  token: string;
};

const DEFAULT_API_URL = "http://127.0.0.1:18790";
const DEFAULT_DASHBOARD_PATH = "/codex-fleet/dashboard";

function readLocalSettings(): LocalSettings {
  if (typeof window === "undefined") {
    return { apiUrl: DEFAULT_API_URL, token: "" };
  }

  const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const apiUrl =
    hashParams.get("apiUrl") || window.localStorage.getItem("codexFleetLocalApiUrl") || DEFAULT_API_URL;
  const token = hashParams.get("token") || window.localStorage.getItem("codexFleetLocalToken") || "";

  if (hashParams.get("apiUrl")) {
    window.localStorage.setItem("codexFleetLocalApiUrl", apiUrl);
  }
  if (hashParams.get("token")) {
    window.localStorage.setItem("codexFleetLocalToken", token);
  }

  return { apiUrl, token };
}

function withLocalSettings(path: string, settings: LocalSettings): string {
  const params = new URLSearchParams({ apiUrl: settings.apiUrl });
  if (settings.token) {
    params.set("token", settings.token);
  }
  return `${path}#${params.toString()}`;
}

function readNextPath(): string {
  if (typeof window === "undefined") return DEFAULT_DASHBOARD_PATH.replace(/^\/+/, "");
  const raw = new URLSearchParams(window.location.search).get("next_path") || DEFAULT_DASHBOARD_PATH;
  const trimmed = raw.trim().replace(/^\/+/, "");
  return trimmed || DEFAULT_DASHBOARD_PATH.replace(/^\/+/, "");
}

function HomePage() {
  const [settings, setSettings] = useState<LocalSettings>(() => readLocalSettings());
  const [nextPath, setNextPath] = useState(DEFAULT_DASHBOARD_PATH.replace(/^\/+/, ""));
  const [isOpeningDashboard, setIsOpeningDashboard] = useState(false);
  const [connectionMessage, setConnectionMessage] = useState("");

  useEffect(() => {
    const onHashChange = () => setSettings(readLocalSettings());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    setNextPath(readNextPath());
    ensureCodexFleetLocalConnection()
      .then((connection) => setSettings(connection))
      .catch((error) => {
        setSettings(readLocalSettings());
        setConnectionMessage(error instanceof Error ? error.message : "Local launcher session could not be verified.");
      });
  }, []);

  const dashboardHref = useMemo(() => `/${nextPath.replace(/^\/+/, "")}`, [nextPath]);
  const onboardingHref = useMemo(() => withLocalSettings("/codex-fleet/onboarding", settings), [settings]);
  const hasToken = Boolean(settings.token);

  const openDashboard = async (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    if (!hasToken) {
      setConnectionMessage("Start codex-fleet with make up and use the browser window it opens.");
      return;
    }
    setIsOpeningDashboard(true);
    setConnectionMessage("");
    try {
      const result = await new CodexFleetLocalApi({ baseUrl: settings.apiUrl, token: settings.token }).planeLoginUrl(
        nextPath
      );
      window.location.assign(result.url);
    } catch (error) {
      setConnectionMessage(error instanceof Error ? error.message : "Could not open the connected project dashboard.");
      setIsOpeningDashboard(false);
    }
  };

  return (
    <main className="min-h-dvh bg-[oklch(13%_0.012_255)] text-[oklch(96%_0.006_255)]">
      <div className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col px-5 py-5 sm:px-8 lg:px-10">
        <header className="flex min-h-14 items-center justify-between border-b border-[oklch(100%_0_0/0.1)]">
          <a href="/" className="flex items-center gap-3">
            <img src="/codex-fleet-logo.svg" alt="codex-fleet" className="size-9" />
            <div className="leading-tight">
              <p className="text-sm font-semibold text-[oklch(98%_0.006_255)]">codex-fleet</p>
              <p className="text-xs text-[oklch(74%_0.02_255)]">Local Codex agent workspace</p>
            </div>
          </a>
          <div className="hidden items-center gap-4 text-xs text-[oklch(78%_0.02_255)] sm:flex">
            <a
              href="https://github.com/RishiGitH/codex-fleet"
              target="_blank"
              rel="noreferrer"
              className="rounded-md border border-[oklch(100%_0_0/0.12)] px-3 py-1.5 font-medium text-[oklch(89%_0.02_255)] hover:border-[oklch(72%_0.14_210/0.55)] hover:text-[oklch(82%_0.13_210)]"
            >
              Star us on GitHub
            </a>
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-[oklch(76%_0.17_158)]" />
              Runs on this machine
            </div>
          </div>
        </header>

        <section className="grid flex-1 items-center gap-10 py-10 lg:grid-cols-[minmax(0,1fr)_420px] lg:py-14">
          <div className="max-w-3xl">
            <p className="mb-5 w-max rounded-md border border-[oklch(72%_0.14_210/0.35)] bg-[oklch(72%_0.14_210/0.1)] px-3 py-1.5 text-xs font-medium text-[oklch(84%_0.12_210)]">
              Local board for Codex agents
            </p>

            <h1 className="max-w-3xl text-balance text-4xl font-semibold leading-tight text-[oklch(98%_0.006_255)] sm:text-5xl">
              Turn Ready work into local Codex runs.
            </h1>

            <p className="mt-5 max-w-2xl text-pretty text-base leading-7 text-[oklch(79%_0.025_255)]">
              Open the project dashboard, add tasks, move them to Ready, and review every run with branch, worktree,
              comments, and verification written back to the board.
            </p>

            <div className="mt-8 rounded-lg border border-[oklch(100%_0_0/0.11)] bg-[oklch(18%_0.012_255)] p-4 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-[oklch(96%_0.006_255)]">Run once per local project</p>
                <span className="rounded border border-[oklch(76%_0.17_158/0.28)] px-2 py-0.5 text-xs text-[oklch(82%_0.13_158)]">
                  loopback only
                </span>
              </div>
              <code className="mt-3 block rounded-md bg-[oklch(11%_0.009_255)] px-3 py-3 font-mono text-sm text-[oklch(92%_0.01_255)]">
                codex-fleet up --repo .
              </code>
            </div>

            <div className="mt-7 grid max-w-lg gap-3 sm:max-w-sm">
              <a
                href={dashboardHref}
                onClick={openDashboard}
                aria-disabled={!hasToken || isOpeningDashboard}
                className="inline-flex h-12 items-center justify-center rounded-md bg-[oklch(76%_0.16_210)] px-5 text-sm font-semibold text-[oklch(15%_0.016_255)] shadow-sm transition hover:bg-[oklch(82%_0.13_210)]"
              >
                {isOpeningDashboard ? "Opening project dashboard..." : "Open project dashboard"}
              </a>
              {connectionMessage ? (
                <p className="text-pretty text-xs leading-5 text-[oklch(83%_0.12_80)]">{connectionMessage}</p>
              ) : null}
              <p className="text-pretty text-xs leading-5 text-[oklch(64%_0.018_255)]">
                Add or create projects from the dashboard's Add Project button.
              </p>
            </div>
          </div>

          <aside className="rounded-lg border border-[oklch(100%_0_0/0.11)] bg-[oklch(18%_0.012_255)] p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-[oklch(96%_0.006_255)]">What happens next</h2>
            <ol className="mt-4 grid gap-3 text-sm text-[oklch(78%_0.025_255)]">
              {[
                ["1", "Open the project dashboard for your local workspace."],
                ["2", "Use Add Project to link a folder or create a starter with a harness."],
                ["3", "Create work items, move them to Ready, and review Codex results."],
              ].map(([step, text]) => (
                <li
                  key={step}
                  className="grid grid-cols-[2rem_1fr] gap-3 rounded-md border border-[oklch(100%_0_0/0.1)] bg-[oklch(14.5%_0.01_255)] p-3"
                >
                  <span className="flex size-8 items-center justify-center rounded-md bg-[oklch(72%_0.14_210/0.11)] text-xs font-semibold text-[oklch(84%_0.12_210)]">
                    {step}
                  </span>
                  <span className="text-pretty leading-6">{text}</span>
                </li>
              ))}
            </ol>

            <div className="mt-5 rounded-md border border-[oklch(100%_0_0/0.1)] bg-[oklch(14.5%_0.01_255)] p-4">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-semibold text-[oklch(96%_0.006_255)]">Local session</span>
                <span className={hasToken ? "text-xs text-[oklch(82%_0.13_158)]" : "text-xs text-[oklch(83%_0.12_80)]"}>
                  {hasToken ? "Ready" : "Launcher needed"}
                </span>
              </div>
              <p className="mt-2 text-pretty text-sm leading-6 text-[oklch(70%_0.022_255)]">
                {hasToken
                  ? "This browser has the local session token. Dashboard actions can reach the codex-fleet API."
                  : "If actions cannot connect, start codex-fleet from your terminal and use the browser URL it prints."}
              </p>
              <a href={onboardingHref} className="mt-3 inline-flex text-xs font-semibold text-[oklch(82%_0.13_210)]">
                Connection setup
              </a>
            </div>
          </aside>
        </section>
      </div>
    </main>
  );
}

export default HomePage;
