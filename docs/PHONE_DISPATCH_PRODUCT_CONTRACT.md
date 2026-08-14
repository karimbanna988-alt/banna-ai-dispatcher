# Phone Dispatch Product Contract

What the operator needs from a phone during **one job**. This is an
operational contract, not an architecture document. If a screen cannot
answer these questions in seconds with one thumb, the screen is wrong.

The CRM is the memory. This is the product.

## The seven questions

Every phone screen exists to answer these, in this order:

| # | Question | Where it is answered |
|---|----------|----------------------|
| 1 | **What just happened?** | Stage badge + latest event line on the card |
| 2 | **Which job?** | Ticket (mono, first line) + service |
| 3 | **What does LUXOR think?** | `RECOMMENDED <technician>` + confidence chip |
| 4 | **What should I do?** | One primary button, bottom of the screen |
| 5 | **Why?** | `WHY` block — observable business-rule evidence only |
| 6 | **What message should I send?** | `MESSAGE` block, verbatim, copyable |
| 7 | **What am I waiting for next?** | `Waiting for …` line, always present |

Question 5 means **evidence**, never reasoning narration. Acceptable:
"Company = Mike Jobs", "Area = Greenville", "Service = Car lockout →
lockouts route to Toto", "Ticket matched exactly". Not acceptable:
anything that reads as the model's internal deliberation.

## The three stages

Stages are an **operator read-model**, derived from backend truth. They
are not database statuses and must never drive a change to
`storage.derive_job_status`.

### TO DISPATCH
New current work. Card shows ticket, service, company, area,
recommended technician, confidence, and any missing facts.
Primary action: **Dispatch & copy** (existing audited
acknowledge → prepare → confirm flow). Nothing is ever sent.

### IN PROGRESS
The screen that matters most during a shift. Every job carries an
explicit waiting state:

`WAITING FOR TECH K` · `TECH ACCEPTED` · `WAITING FOR ETA` ·
`ETA <n> MIN` · `EN ROUTE` · `PRICE QUOTED` · `CUSTOMER NO ANSWER` ·
`TECH DECLINED` · `POSSIBLE REASSIGNMENT` · `WAITING FOR CLOSING` ·
`HUMAN REVIEW`

Each shows ticket, technician, elapsed time, latest meaningful event,
and next expected event. When a company update is owed, the exact text
is prepared and copyable.

### TO CLOSE
Ticket, technician, closing report, amount **only if explicitly
stated**, payment method **only if explicitly stated**, and missing
fields named outright.

- `$180` + `CASH` → **READY TO CLOSE** → `COPY CLOSING`
- bare `Done` → **MISSING AMOUNT · MISSING PAYMENT METHOD** →
  `HUMAN_REVIEW_NEEDED`, and the copy action is not offered.

A closing never completes on an implied amount.

## Phone interaction rules

- One stage fills the viewport at a time; switching stages is one tap.
- Touch targets ≥ 44 px. No hover-only affordance anywhere.
- The primary action sits in the **lower** third, reachable one-handed.
- No horizontal scrolling at any width ≥ 320 px.
- Copy is one tap and needs no confirmation — it commits nothing.
- Anything that commits state (dispatch) stays a deliberate, labelled
  button. No swipe, no long-press, no gesture may commit.
- Detail opens as a full-height sheet with its own close control; the
  board does not re-render underneath it.

## Truthfulness rules

- If the backend is unreachable, the board is visibly **OFFLINE** and
  marked stale. Cached pixels must never read as live truth.
- Missing values render as `not stated`, never as a guess.
- Stale pre-commit material (> 24 h) stays out of the live board and
  is signposted to Admin › Inbox Review.
- Offline is read-only. No dispatch, closing, or company update is ever
  queued for later execution.

## What the phone deliberately does not show first

Charts, revenue, technician database, CRM history, performance tables.
Those live behind **Admin**. The first screen answers only:
*what do I need to do now?*
