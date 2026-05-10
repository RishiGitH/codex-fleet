/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { AgentsSurface } from "@/app/codex-fleet/project-surfaces";
import type { Route } from "./+types/page";

export default function AgentsPage({ params }: Route.ComponentProps) {
  return <AgentsSurface projectId={params.projectId} />;
}
