/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// components
import { ProjectNavigation } from "@/components/workspace/sidebar/project-navigation";
import type { TNavigationItem } from "@/components/workspace/sidebar/project-navigation";
import { EUserPermissions } from "@plane/constants";
import { Activity, Archive, Bot, PlayCircle, Settings } from "lucide-react";

type TProjectItemsRootProps = {
  workspaceSlug: string;
  projectId: string;
};

export function ProjectNavigationRoot(props: TProjectItemsRootProps) {
  const { workspaceSlug, projectId } = props;
  return (
    <ProjectNavigation
      workspaceSlug={workspaceSlug}
      projectId={projectId}
      additionalNavigationItems={codexFleetNavigationItems}
    />
  );
}

function codexFleetNavigationItems(workspaceSlug: string, projectId: string): TNavigationItem[] {
  const access = [EUserPermissions.ADMIN, EUserPermissions.MEMBER, EUserPermissions.GUEST];
  return [
    {
      i18n_key: "",
      key: "fleet_logs",
      name: "Fleet Logs",
      href: `/${workspaceSlug}/projects/${projectId}/fleet-logs/`,
      icon: Activity,
      access,
      shouldRender: true,
      sortOrder: 1.1,
    },
    {
      i18n_key: "",
      key: "agents",
      name: "Agents",
      href: `/${workspaceSlug}/projects/${projectId}/agents/`,
      icon: Bot,
      access,
      shouldRender: true,
      sortOrder: 1.2,
    },
    {
      i18n_key: "",
      key: "runs",
      name: "Runs",
      href: `/${workspaceSlug}/projects/${projectId}/runs/`,
      icon: PlayCircle,
      access,
      shouldRender: true,
      sortOrder: 1.3,
    },
    {
      i18n_key: "",
      key: "artifacts",
      name: "Artifacts",
      href: `/${workspaceSlug}/projects/${projectId}/artifacts/`,
      icon: Archive,
      access,
      shouldRender: true,
      sortOrder: 1.4,
    },
    {
      i18n_key: "",
      key: "codex_settings",
      name: "Codex Settings",
      href: `/${workspaceSlug}/projects/${projectId}/codex-settings/`,
      icon: Settings,
      access,
      shouldRender: true,
      sortOrder: 1.5,
    },
  ];
}
