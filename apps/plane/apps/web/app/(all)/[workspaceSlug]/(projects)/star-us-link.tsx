/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTheme } from "next-themes";
// assets
import githubBlackImage from "@/app/assets/logos/github-black.png?url";
import githubWhiteImage from "@/app/assets/logos/github-white.png?url";

export function StarUsOnGitHubLink() {
  // hooks
  const { resolvedTheme } = useTheme();
  const imageSrc = resolvedTheme === "dark" ? githubWhiteImage : githubBlackImage;

  return (
    <a
      aria-label="Star codex-fleet on GitHub"
      className="flex h-8 flex-shrink-0 items-center gap-1.5 rounded-md border border-custom-border-200 bg-custom-background-90 px-3 text-xs font-semibold text-custom-text-200 hover:border-custom-primary-100/40 hover:bg-custom-background-80 hover:text-custom-primary-100"
      href="https://github.com/RishiGitH/codex-fleet"
      target="_blank"
      rel="noopener noreferrer"
    >
      <img src={imageSrc} className="h-4 w-4 object-contain" alt="GitHub Logo" aria-hidden="true" />
      <span className="hidden sm:hidden md:block">Star on GitHub</span>
    </a>
  );
}
