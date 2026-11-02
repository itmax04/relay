# relay — ImageGen prompts for UI mockups

Exploratory mockups only. Final colors, type, spacing and radii come from the DESIGN.md the owner
chooses (owner's global rule); then replace the STYLE block below with values from it.

Use: paste STYLE + one screen prompt per generation. Keep STYLE identical across screens.

## STYLE (prepend to every prompt)

High-fidelity UI screenshot of a native macOS app named "relay", flat front view, no device frame,
no perspective, no hands, no desk. macOS Sequoia look: SF Pro typography, system sidebar with
translucent vibrancy material, standard traffic-light window buttons, SF Symbols-style line icons,
8-point spacing grid, generous whitespace, subtle separators, rounded cards. Calm, professional
developer-tool aesthetic, information-dense but readable. Neutral grays with one accent color;
status colors: green = fine, amber = attention, red = act now. All text crisp, legible and spelled
exactly as given in quotes. No company logos; agent names appear as plain text labels only.
Light mode.

## 1. Menu bar popover

Compact popover hanging from a macOS menu bar icon (a small relay icon with an amber dot), 380×560 px.
Header: "relay" and a status pill "1 suggestion". Section "Active sessions" with two rows:
row 1 "Claude · telegram-bots" with chips "Opus 5.5" "high", a horizontal context bar filled to 486K
of 1M with two thin markers labelled "150K" and "300K", bar colored red past 300K, caption
"cache expires in 12 min"; row 2 "Codex · lingo" with chips "Sol 6.1" "high", context bar at 92K, green.
Below, one highlighted suggestion card with amber edge: title "Start a new session",
text "Context 486K. The step is finished — write a handoff and continue fresh.",
buttons "Copy handoff prompt" (primary) and "Dismiss". Footer: two small ring gauges
"Claude week 78%" and "Codex week 69%", and a text button "Open relay".

## 2. Main window — "Now"

Main window 1440×900. Left sidebar items with icons: "Now" (selected), "Suggestions" (badge 3),
"Quotas", "Report", "Settings". Content: title "Now" and a session card grid.
Large card "Claude · telegram-bots": big context gauge "486K / 1M" with soft and hard limit markers,
chips "Opus 5.5" "effort high", stats row "Turns 1,454 · Avg context 431K · Output 2.0M",
"Cache: warm, expires in 12 min" with a small countdown ring, and a sparkline titled
"Context per turn" rising steadily with a dashed line at 150K. Smaller card "Codex · lingo":
gauge "92K / 258K", chips "Sol 6.1" "high", green status "Healthy".
Right column "Latest advice" with two compact entries and timestamps.

## 3. Suggestions feed

Main window 1440×900, sidebar item "Suggestions" selected. Vertical feed of advice cards, newest first,
each with a colored severity edge, timestamp, session label, source tag ("Rule" or "Advisor · Groq"):
card 1 red "Start a new session" — "Context 486K, above the 300K hard limit. Handoff, then continue fresh.";
card 2 amber "Switch to Sonnet 5.5 · medium" — "Last 15 turns were mechanical edits against a finished plan.";
card 3 amber "Same test failure 3 times" — "Change approach or raise effort instead of retrying.";
card 4 gray "Read narrowly" — "A 40K-token file was read in full."
Each card has an action button (e.g. "Copy handoff prompt", "Copy /model command") and two small
feedback buttons "Useful" and "Wrong". Top bar: filter chips "All" "Claude" "Codex" "Rules" "Advisor".

## 4. Quotas

Main window 1440×900, sidebar item "Quotas" selected. Two side-by-side panels "Claude Pro" and "ChatGPT Plus · Codex".
Each shows two large ring gauges: "5-hour 12%" and "Week 78%" (Claude), "5-hour 91%" and "Week 69%" (Codex),
with reset times "resets in 1h 29m", "resets Fri 07:00". Below, a full-width line chart "Weekly usage forecast"
with actual usage solid and projected dashed line crossing 100% on "Thu", an annotation
"At the current pace Claude runs out Thursday". A small table "Spend by session this week" with three rows.

## 5. Report

Main window 1440×900, sidebar item "Report" selected. Period selector "Last 30 days".
Top row of four stat tiles: "Model calls 7,779", "Re-sent context 3.0B tokens",
"Calls above 200K 70%", "Saving with 150K cap 66%". Middle: horizontal bar chart
"Context size per call" with buckets "<100K 2%", "100–200K 5%", "200–400K 19%", "400–700K 38%", "≥700K 36%".
Bottom: table "Top sessions" with columns Share, Dates, Agent, Project, Calls, Max context, Avg context, Model;
rows like "21.6% · Oct 22–25 · Claude · telegram-bots · 1,454 · 968K · 486K · Opus 5.5".

## 6. Settings

Main window 1440×900, sidebar item "Settings" selected. Grouped form sections in macOS settings style:
"Limits" with two sliders "Soft limit 150K" and "Hard limit 300K" and a stepper "Auto-compact window 350K";
"Advisor" with an ordered provider chain shown as connected pills "Groq → Cloudflare Workers AI → Rules only",
each with a status dot and "Daily budget 200K tokens, used 18K", a masked key field "gsk_••••••••" with
"Stored in Keychain" caption; "Data sent to advisor" with toggles "Metrics" (on), "Prompt text" (on),
"Error excerpts" (on), "File names" (on); "Agents" with rows "Claude Code — hooks installed ✓" and
"Codex — hooks need trust in /hooks" with an amber badge.

## 7. New session sheet + notification

macOS window 1440×900 with a modal sheet titled "Continue in a new session". Text "Handoff updated
2 min ago · ~/Developer/lingo/docs/changes/LINGO-05/handoff.md". A monospaced read-only box with the prompt
"Continue from the handoff at docs/changes/LINGO-05/handoff.md". Suggested configuration chips
"Sonnet 5.5" "medium" with caption "Suggested by advisor: implementation step with exact checks".
Buttons: "Copy prompt" (primary), "Open in Claude CLI", "Open in Codex", "Cancel".
In the top-right corner of the screen, a standard macOS notification banner from "relay":
"Context 310K in telegram-bots — time for a new session".

## Variants

- Dark mode: replace "Light mode." in STYLE with "Dark mode, true macOS dark appearance."
- Russian UI: add to STYLE "All interface text in Russian" and translate the quoted labels.
