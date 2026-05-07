# codex-fleet Design Context

## Register

Product UI. Design serves fast task completion, safe local execution, and trust.

## Visual Direction

Use a restrained, high-contrast local-workstation theme. Dark mode is appropriate because users often run codex-fleet beside terminals, editors, and local logs. It should feel precise and quiet, not gloomy or neon.

## Color

- Tinted near-black surfaces, never pure black.
- One primary accent for actions and active state.
- Semantic color for success, warning, error, and local-running state.
- Avoid decorative gradients and multicolor glow.

## Typography

Use system UI fonts. Prefer compact hierarchy, readable line lengths, and clear labels. Buttons should read as native product controls, not marketing CTAs.

## Layout

- Root page: one clear primary action, Open project dashboard.
- Project add/create happens inside Plane's Add Project flow.
- Setup/onboarding is a fallback for connection recovery.
- Forms should show errors inline near the control that needs action.
- Avoid nested cards. Use panels only when they frame a tool or repeated item.

## Components

- Primary action: solid accent button.
- Secondary action: quiet border or text.
- Inputs: consistent height, border, focus ring, and inline help/error text.
- Loading: product-branded codex-fleet mark with calm status copy, no generic Plane loader.

## Motion

Use minimal stateful motion only for loading and small feedback. Keep motion short and compositor-only.
