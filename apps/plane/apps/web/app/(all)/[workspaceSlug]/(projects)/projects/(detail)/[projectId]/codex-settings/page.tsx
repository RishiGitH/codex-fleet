/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { CodexSettingsSurface } from "@/app/codex-fleet/project-surfaces";
import type { Route } from "./+types/page";

export default function CodexSettingsPage({ params }: Route.ComponentProps) {
  return <CodexSettingsSurface projectId={params.projectId} />;
}
