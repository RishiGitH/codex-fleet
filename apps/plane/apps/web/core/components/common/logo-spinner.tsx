/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export function LogoSpinner() {
  return (
    <div className="flex min-h-24 items-center justify-center">
      <style>{`
        @keyframes codexFleetOrbit {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes codexFleetPulse {
          0%, 100% { opacity: 0.55; transform: scale(0.96); }
          50% { opacity: 1; transform: scale(1); }
        }
      `}</style>
      <div className="relative flex size-16 items-center justify-center" aria-label="Loading codex-fleet">
        <div className="absolute inset-0 rounded-2xl border border-custom-border-200 bg-custom-background-90" />
        <div
          className="absolute inset-1 rounded-2xl border border-transparent border-t-custom-primary-100"
          style={{ animation: "codexFleetOrbit 1.1s linear infinite" }}
        />
        <div
          className="absolute inset-3 rounded-xl bg-custom-primary-100/10"
          style={{ animation: "codexFleetPulse 1.6s ease-out infinite" }}
        />
        <img src="/codex-fleet-logo.svg" alt="" className="relative size-8" />
      </div>
    </div>
  );
}
