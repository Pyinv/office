---
# gstack: design-md-format=spec
name: The Office
description: A quiet office floor. People at desks in rooms, ink on paper, and one amber note for whoever needs you.
colors:
  floor: "#f7f6f3"
  floor-dark: "#121211"
  desk: "#ffffff"
  desk-dark: "#1c1c1b"
  field: "#f1efeb"
  field-dark: "#242422"
  line: "#e7e4df"
  line-dark: "#2c2b29"
  text: "#1b1b1a"
  text-dark: "#edebe6"
  text-muted: "#6e6b66"
  text-muted-dark: "#a9a59d"
  primary: "#1b1b1a"
  on-primary: "#f7f6f3"
  needs-you: "#b86e00"
  needs-you-dark: "#f0a020"
  needs-you-soft: "#fcefd9"
  working: "#3d6fe0"
  working-dark: "#7c9cff"
  success: "#2f8a52"
  success-dark: "#5cc488"
  draft: "#8a6a1f"
  terminal: "#111312"
typography:
  display:
    fontFamily: Bricolage Grotesque
    fontWeight: 650
    fontSize: 1.3rem
    letterSpacing: -0.01em
  body:
    fontFamily: General Sans
    fontSize: 15px
    lineHeight: 1.5
  label:
    fontFamily: General Sans
    fontSize: 0.8rem
    letterSpacing: 0
  mono:
    fontFamily: JetBrains Mono
    fontFeature: tnum
rounded:
  sm: 6px
  md: 10px
  lg: 14px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
  desk:
    backgroundColor: "{colors.desk}"
    rounded: "{rounded.md}"
  input:
    backgroundColor: "{colors.field}"
    rounded: "{rounded.md}"
---

# The Office

## Overview

**Creative North Star:** a real office floor. The agents are people at desks, grouped in rooms, and the page should feel like walking into your own office in the morning: you know where everyone sits and you see at once who is holding up a note for you.
**Product context:** a one-person control room for many Claude Code agents running in tmux, used all day on a phone and a laptop.
**Mode per surface:** Operate everywhere. The terminal is the one Experience surface and keeps its own dark palette.
**Key characteristics:**
- Calm by default; only "needs you" is loud.
- Faces and names outrank operational metadata.
- Rooms go Ventures, Clients, Personal; inside a room the most recently active desk comes first (pinned before all).
- Ink on paper in light mode, warm charcoal in dark mode, with no purple, gradients or glows.

## Colors

**Strategy:** Restrained. Neutrals carry the page; the accent is ink itself (buttons, focus, selection). Amber is reserved for "needs you" and nothing else. Working is a small blue mark, finished is a quiet green.
**Light or dark:** both, following the system by default. Desks sit one step above the floor in each theme; dark mode keeps the same hierarchy rather than inverting it.
Each person's face sits on a soft tint of a hue derived from their name (`--h`), light in light mode and deep in dark mode, so faces stay recognisable without coloured rings.

## Typography

Bricolage Grotesque gives names, rooms and the "need you" count a slightly hand-painted feel, as if lettered on a desk. General Sans carries replies, buttons and menus. JetBrains Mono is for project names, paths, code and the terminal. All three load from Google Fonts or Fontshare with `display=swap`, with system fallbacks.

## Layout

The floor is grouped into rooms (Ventures, Clients, Personal), most recently active first in each. The target is a compact desk (about 74px) showing face, name, project and one line (only what Claude Code's own files say it waits for, or that it isn't running: never a guess from the reply's wording); the full reply lives in the person's panel (side panel on a laptop, sheet on a phone). A "Your move" row is a shortcut to whoever needs you; it never moves the desks themselves. The page max width is 1180px with a 16px phone gutter.

## Elevation & Depth

Depth comes from the floor/desk/field steps and 1px lines. Shadows have an offset and a soft blur (`0 8px 24px -12px`). No zero-offset coloured halos, no gradient washes and no coloured side stripes on cards: a state shows as a border tint, a label or the amber note.

## Shapes

Radii: 6px for small controls and code, 10px for desks, inputs and buttons, 14px for dialogs and sheets, full for avatars in chips. A nested radius is the outer radius minus the gap.

## Components

- **Desk:** a 1px line border; it turns amber-tinted when the agent needs you, blue-tinted while working, and it is dashed and dimmed when away for more than a week. Hover darkens the border; the selected desk gets an ink ring.
- **Unread dot:** a small green dot after the name: Claude finished a reply after you last looked at that desk (panel, terminal or a message). Opening the panel clears it.
- **Needs-you note:** an amber tab breaking the top edge of the desk with a verb: "Answer" (an open question), "Allow" (a permission prompt) or "Waiting" (another dialog), all read from Claude Code's status file and transcript. This is the one authored moment.
- **Buttons:** the primary is ink on paper; secondary buttons are line-bordered; destructive actions are in red text inside menus.
- **Focus:** a 3px ring in ink at 18% opacity.

## Do's and Don'ts

- Do keep amber for "needs you" only.
- Do use the display face for people's names and room titles, never for body text.
- Do keep the terminal dark in every theme.
- Don't add gradients, glows, coloured left borders or a second accent colour.
- Don't put a row of action buttons on every desk; actions belong in the person's panel or a single overflow menu.
- Don't reorder desks by status: activity orders them, the amber note carries urgency.

## Motion

- **Approach:** minimal-functional.
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro(50-100ms) short(150-250ms) medium(250-400ms) long(400-700ms)
- **The one authored moment:** the needs-you note on a desk. Working agents have a slow 2s breathing dot; it is turned off under `prefers-reduced-motion`.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-29 | Initial design system created | Created by /design-consultation from the product context, with independent proposals from Codex and a Claude subagent |
| 2026-10-29 | Stage 1 shipped: palette, fonts, header count | Purple, gradients, glows and card stripes removed |
| 2026-10-30 | Desks sorted by activity, Ventures first | The user's call: activity order over fixed seats |
| 2026-10-30 | State only from Claude Code's files | The user's call: no guessing from text. Busy / idle / waiting comes from ~/.claude/sessions, the question and its choices from the transcript's open AskUserQuestion; the reply preview line and the Draft note are gone |
| 2026-10-29 | Floor of desks, the person's panel, face rail, front desk | Rooms with fixed seats; a desk opens a panel (side panel / phone sheet) with the reply, answer buttons and one ⋯ menu; Nadia sits at the bottom on a laptop but stays at the top on the phone, where a bottom bar fights the keyboard |
