/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { route } from "@react-router/dev/routes";
import type { RouteConfigEntry } from "@react-router/dev/routes";

export const extendedRoutes: RouteConfigEntry[] = [
  route("codex-fleet/onboarding", "./codex-fleet/onboarding.tsx"),
  route("codex-fleet/dashboard", "./codex-fleet/dashboard.tsx"),
];
