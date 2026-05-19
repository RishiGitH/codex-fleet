/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { FleetLogsSurface } from "@/app/codex-fleet/project-surfaces";
import type { Route } from "./+types/page";

export default function FleetLogsPage({ params }: Route.ComponentProps) {
  return <FleetLogsSurface projectId={params.projectId} />;
}
