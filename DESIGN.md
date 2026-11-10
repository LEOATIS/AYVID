# AYVID Design System — Research Workspace

Single source of truth for AYVID UI. Tokens live in `frontend/src/app/globals.css`;
this file describes how to use them. Synthesised from AYVID's existing warm-paper /
warm-graphite register, Linear's workspace density conventions, and Mercury /
Mastercard's editorial financial authority — none of their colours, type or assets
are copied.

## 1. Register

**Ink on paper.** A research workspace is a reading instrument, not a dashboard.
Every surface should feel like a printed analyst note: warm cream paper (light) or
warm graphite (dark), hairline rules, one restrained gold accent.

Never: neon, gradients, glowing orbs, glassmorphism, violet/lavender, centred
three-card SaaS grids, decorative illustration, badges on every sentence, fade-in on
scroll, invented customers/quotes/figures.

## 2. Colour tokens (HSL channels, use `hsl(var(--x))`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #F4EFE6 cream | #0F0D0A graphite | page |
| `--panel` / `--panel-2` | lifted paper | lifted graphite | cards, drawer |
| `--bg-sunken` | darker paper | darker graphite | inputs, table heads, code |
| `--fg` / `--fg-muted` / `--fg-subtle` | ink #261F18 → muted | ivory #F0E9DD → muted | text tiers |
| `--border` / `--border-strong` | hairlines | hairlines | 1px rules only |
| `--brand` | gold #C9A84C | gold | focus ring, primary action, citation pill hover. ≤ 5% of pixels |
| `--pos` | forest #5B8B5B | | healthy / not-triggered / positive change |
| `--neg` | terracotta #A8413A | | triggered / refusal / negative change |
| `--warn` | amber #B8862B | | STALE, DEGRADED, UNKNOWN |

Semantic colour is always paired with a text label (never colour alone).

## 3. Type

- `--font-display` **Fraunces** — page titles and the executive synthesis heading only.
- `--font-sans` **Inter** — all UI and body prose. 14px workspace body, 15–16px answer prose.
- `--font-mono` **JetBrains Mono** — IDs (`US:NASDAQ:NVDA`), accession numbers, hashes,
  citation pills, and every number column. Numbers always use `.num`
  (`tabular-nums lining-nums`).
- Eyebrows: `.label-mono` 11px uppercase, +0.12em tracking.
- Headings use `text-wrap: balance`. Ellipsis is `…`.

## 4. Space & density

Fibonacci scale `--s-1..--s-8` (4, 8, 13, 21, 34, 55, 89, 144). Workspace uses
`--s-2..--s-5`; marketing may use the upper steps. Table rows 32px. Radius 6px on
cards, 4px on pills/inputs, never fully rounded except status dots.

## 5. Workspace layout

```
┌ Left rail 248px ┬──────── Centre (max 860px measure) ────────┬ Evidence drawer 400px ┐
│ + New research  │ Query bar + instrument resolver              │ Source │ Calc │ Prov │ │
│ nav             │ Pipeline stepper                             │ Edge                  │
│ Saved theses ●  │ Answer blocks (synthesis, data, exposures,   │                       │
│ Recent          │ bull/bear, uncertainty, monitor)             │                       │
│ Data mode · LLM │ Action bar: Save as thesis                   │                       │
└─────────────────┴──────────────────────────────────────────────┴───────────────────────┘
```

- < 1024px: rail and drawer become slide-over sheets (`role="dialog"`, Esc closes,
  `overscroll-behavior: contain`).
- Drawer opens when any `[S#]`, `[CALC-#]`, metric cell or edge row is activated.

## 6. Epistemic status

Five labels, rendered as small mono text tags at the *start of a block*, not per sentence:

| Label | Mark | Meaning |
|---|---|---|
| REPORTED | `RPT` ink | figure from a filing / market data |
| CALCULATED | `CALC` gold | deterministic Python output, auditable |
| MANAGEMENT | `MGMT` muted | statement by the company |
| INTERPRETATION | `INTERP` muted italic | model/engine reasoning |
| HYPOTHESIS | `HYP` amber | unverified; needs evidence |

## 7. States (every data view must handle all five)

`loading` (skeleton + "Loading…"), `empty` (what to do next), `stale`
(amber `STALE DATA (as of YYYY-MM-DD)`), `degraded` (amber banner naming the failed
provider and the sources used), `error` (terracotta, request ID shown).
UNKNOWN is never rendered as 0 or "Not triggered".

## 8. Interaction

- Keyboard first: `⌘K` / `/` focuses the query bar, `Esc` closes drawer/sheets,
  every control reachable by Tab with a visible gold `:focus-visible` ring.
- `<button>` for actions, `<Link>` for navigation. Icon-only buttons get `aria-label`.
- State that matters is in the URL (`/research/[id]`, `?tab=`, `?period=`).
- Motion: 200ms `--ease-monument` on colour/transform only; `prefers-reduced-motion`
  disables it. No `transition: all` in new code.
- Async results announce via `aria-live="polite"`.
