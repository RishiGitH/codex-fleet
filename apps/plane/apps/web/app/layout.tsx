/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import Script from "next/script";

// styles
import "@/styles/globals.css";

// helpers
import { cn } from "@plane/utils";

// local
import { AppProvider } from "./provider";

const APP_TITLE = "codex-fleet | Local Codex agent control center";
const APP_DESCRIPTION = "Local Codex agent control center for project work.";
const CODEX_FLEET_ICON = "/codex-fleet-logo.svg";

export const meta = () => [
  { title: APP_TITLE },
  { name: "description", content: APP_DESCRIPTION },
  {
    name: "keywords",
    content: "codex, agents, local development, work item tracking, kanban, orchestration, git worktrees",
  },
  {
    name: "viewport",
    content:
      "width=device-width, initial-scale=1, minimum-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover",
  },
  { property: "og:title", content: APP_TITLE },
  {
    property: "og:description",
    content: APP_DESCRIPTION,
  },
  { property: "og:url", content: "http://127.0.0.1:17880/" },
  { property: "og:image", content: "/og-image.png" },
  { property: "og:image:width", content: "1200" },
  { property: "og:image:height", content: "630" },
  { property: "og:image:alt", content: "codex-fleet local Codex agent control center" },
  { name: "twitter:site", content: "@codex-fleet" },
  { name: "twitter:card", content: "summary_large_image" },
  { name: "twitter:image", content: "/og-image.png" },
  { name: "twitter:image:width", content: "1200" },
  { name: "twitter:image:height", content: "630" },
  { name: "twitter:image:alt", content: "codex-fleet local Codex agent control center" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const isSessionRecorderEnabled = parseInt(process.env.VITE_ENABLE_SESSION_RECORDER || "0");

  return (
    <html lang="en">
      <head>
        <meta name="theme-color" content="#0B1020" />
        <link rel="icon" type="image/svg+xml" href={CODEX_FLEET_ICON} />
        <link rel="manifest" href="/site.webmanifest.json" />
        <link rel="shortcut icon" href={CODEX_FLEET_ICON} />
        {/* Meta info for PWA */}
        <meta name="application-name" content="codex-fleet" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="default" />
        <meta name="apple-mobile-web-app-title" content="codex-fleet" />
        <meta name="format-detection" content="telephone=no" />
        <meta name="mobile-web-app-capable" content="yes" />
        <link rel="apple-touch-icon" href={CODEX_FLEET_ICON} />
        <link rel="apple-touch-icon" sizes="180x180" href={CODEX_FLEET_ICON} />
        <link rel="apple-touch-icon" sizes="512x512" href={CODEX_FLEET_ICON} />
        <link rel="manifest" href="/manifest.json" />
      </head>
      <body>
        <div id="context-menu-portal" />
        <div id="editor-portal" />
        <AppProvider>
          <div className={cn("relative flex h-screen w-full flex-col overflow-hidden", "app-container")}>
            <main className="relative h-full w-full overflow-hidden">{children}</main>
          </div>
        </AppProvider>
      </body>
      {!!isSessionRecorderEnabled && process.env.VITE_SESSION_RECORDER_KEY && (
        <Script id="clarity-tracking">
          {`(function(c,l,a,r,i,t,y){
              c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};
              t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;
              y=l.getElementsByTagName(r)[0];if(y){y.parentNode.insertBefore(t,y);}
          })(window, document, "clarity", "script", "${process.env.VITE_SESSION_RECORDER_KEY}");`}
        </Script>
      )}
    </html>
  );
}
