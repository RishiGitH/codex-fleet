/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

"use client";

import { useMemo, useState } from "react";
import type { MouseEvent } from "react";
import { useSearchParams } from "next/navigation";

import {
  CodexFleetLocalApi,
  DEFAULT_CODEX_FLEET_API_URL,
  ensureCodexFleetLocalConnection,
} from "@/app/codex-fleet/local-api";
import DefaultLayout from "@/layouts/default-layout";
import { Button } from "@plane/propel/button";

export function InstanceNotReady() {
  const searchParams = useSearchParams();
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectionMessage, setConnectionMessage] = useState("");
  const nextPath = useMemo(() => {
    const raw = searchParams.get("next_path") || "/codex-fleet/dashboard";
    const normalized = raw.trim().replace(/^\/+/, "");
    if (!normalized || /^https?:\/\//i.test(normalized) || normalized.startsWith("//")) return "codex-fleet/dashboard";
    return normalized;
  }, [searchParams]);
  const dashboardHref = useMemo(() => `/${nextPath}`, [nextPath]);

  const openCodexFleet = async (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    setIsConnecting(true);
    setConnectionMessage("");
    try {
      const connection = await ensureCodexFleetLocalConnection();
      const apiUrl = connection.apiUrl || DEFAULT_CODEX_FLEET_API_URL;
      if (connection.token) {
        const result = await new CodexFleetLocalApi({ baseUrl: apiUrl, token: connection.token }).planeLoginUrl(nextPath);
        window.location.assign(result.url);
        return;
      }
      window.location.assign(new CodexFleetLocalApi({ baseUrl: apiUrl, token: null }).connectUrl(nextPath));
    } catch (error) {
      setConnectionMessage(error instanceof Error ? error.message : "Could not connect to Codex Fleet.");
      setIsConnecting(false);
    }
  };

  return (
    <DefaultLayout>
      <main className="relative z-10 min-h-dvh overflow-hidden bg-[oklch(13%_0.012_255)] text-[oklch(96%_0.006_255)]">
        <div className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col px-5 py-5 sm:px-8 lg:px-10">
          <header className="flex min-h-14 items-center justify-between border-b border-[oklch(100%_0_0/0.1)]">
            <a href="/" className="flex items-center gap-3">
              <img src="/codex-fleet-logo.svg" alt="codex-fleet" className="size-9" />
              <div className="leading-tight">
                <p className="text-sm font-semibold text-[oklch(98%_0.006_255)]">codex-fleet</p>
                <p className="text-xs text-[oklch(74%_0.02_255)]">Local Codex agent workspace</p>
              </div>
            </a>
            <div className="hidden items-center gap-2 text-xs text-[oklch(78%_0.02_255)] sm:flex">
              <span className="size-2 rounded-full bg-[oklch(76%_0.17_158)]" />
              Runs on this machine
            </div>
          </header>

          <section className="grid flex-1 items-center gap-10 py-10 lg:grid-cols-[minmax(0,1fr)_420px] lg:py-14">
            <div className="max-w-3xl">
              <p className="mb-5 w-max rounded-md border border-[oklch(72%_0.14_210/0.35)] bg-[oklch(72%_0.14_210/0.1)] px-3 py-1.5 text-xs font-medium text-[oklch(84%_0.12_210)]">
                First-run setup
              </p>

              <h1 className="max-w-3xl text-balance text-4xl font-semibold leading-tight text-[oklch(98%_0.006_255)] sm:text-5xl">
                Open Codex Fleet to connect your local workspace.
              </h1>

              <p className="mt-5 max-w-2xl text-pretty text-base leading-7 text-[oklch(79%_0.025_255)]">
                Start from the Codex Fleet dashboard, link or create a project, then move work to Ready when you want a
                local Codex run.
              </p>

              <div className="mt-8 grid max-w-sm gap-3">
                <a href={dashboardHref} className="block" onClick={openCodexFleet}>
                  <Button
                    variant="primary"
                    className="h-12 w-full bg-[oklch(76%_0.16_210)] text-[oklch(15%_0.016_255)] hover:bg-[oklch(82%_0.13_210)]"
                    size="xl"
                  >
                    {isConnecting ? "Connecting..." : "Open Codex Fleet"}
                  </Button>
                </a>
                {connectionMessage ? (
                  <p className="text-pretty rounded-md border border-[oklch(75%_0.14_50/0.35)] bg-[oklch(75%_0.14_50/0.1)] px-3 py-2 text-sm leading-6 text-[oklch(86%_0.12_65)]">
                    {connectionMessage}
                  </p>
                ) : null}
                <a
                  href="/codex-fleet/onboarding"
                  className="inline-flex h-11 items-center justify-center rounded-md border border-[oklch(100%_0_0/0.12)] px-4 text-sm font-semibold text-[oklch(89%_0.02_255)] hover:border-[oklch(72%_0.14_210/0.55)] hover:text-[oklch(82%_0.13_210)]"
                >
                  Connection setup
                </a>
              </div>
            </div>

            <aside className="rounded-lg border border-[oklch(100%_0_0/0.11)] bg-[oklch(18%_0.012_255)] p-5 shadow-sm">
              <h2 className="text-sm font-semibold text-[oklch(96%_0.006_255)]">What happens next</h2>
              <ol className="mt-4 grid gap-3 text-sm text-[oklch(78%_0.025_255)]">
                {[
                  ["1", "Connect the local launcher session."],
                  ["2", "Add a project folder or create a starter project."],
                  ["3", "Create tasks, move them to Ready, and review Codex results."],
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
                <p className="text-sm font-semibold text-[oklch(96%_0.006_255)]">Local session</p>
                <p className="mt-2 text-pretty text-sm leading-6 text-[oklch(70%_0.022_255)]">
                  If dashboard actions cannot connect yet, start codex-fleet from your terminal and use the browser URL
                  it opens.
                </p>
              </div>
            </aside>
          </section>
        </div>
      </main>
    </DefaultLayout>
  );
}
