"use client";

import { useEffect } from "react";

import { ensureCodexFleetLocalConnection } from "./local-api";

const shouldRefreshConnection = () => {
  if (typeof window === "undefined") return false;
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return Boolean(hash.get("code") || hash.get("apiUrl"));
};

export function CodexFleetLocalSessionBootstrap() {
  useEffect(() => {
    const refresh = () => {
      if (!shouldRefreshConnection()) return;
      void ensureCodexFleetLocalConnection().catch(() => undefined);
    };

    refresh();
    window.addEventListener("hashchange", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("hashchange", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, []);

  return null;
}
