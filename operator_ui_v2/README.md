# LUXOR Operator UI V2 — Visual Prototype

**Status: prototype only, not approved, not connected to any real system.**

This is a from-scratch visual redesign of the operator-facing interface,
built to replace the current Streamlit-based `luxor_desk.py` UI after
repeated real-phone reviews rejected its look and feel. It exists
entirely outside the real application: it is plain HTML/CSS/vanilla JS,
served as static files, with zero framework, zero build step, and zero
connection to `data/dispatcher.db`, the real Quo API, or any real
customer data.

## What this is

- `index.html` — single-page shell; five screens (Jobs, Active, Closings,
  Reports, Technicians) implemented as sibling `<section>` elements
  toggled by JS, not separate pages.
- `css/styles.css` — the entire new visual system: light background, navy
  header, royal-blue primary actions, pale-yellow/green/gray status
  chips, red reserved for actual errors. No dark theme, no gradients, no
  oversized cards.
- `js/data.js` — the only "backend" this prototype has: ~14 fabricated
  jobs, 4 fabricated technicians, 8 fabricated report rows. No real
  names, phone numbers, addresses, or tickets.
- `js/app.js` — all rendering and interaction (DOM string templates +
  manual event listeners). Mutates only the in-memory arrays from
  `data.js`; nothing here calls a network API or writes to disk.
- `screenshots/` — real browser-rendered captures (headless Chrome, not
  CSS inspection) proving the responsive behavior at the four required
  viewports plus a few supplementary screens.

## Running it locally

```
python -m http.server 8511 --directory operator_ui_v2
```

Then open `http://localhost:8511`. No dependencies beyond Python's
standard library; no `npm install`, no build step.

## What this is not

- Not wired to `DispatcherService`, `storage.py`, or any real database.
- Not wired to Quo or WhatsApp — no message of any kind can be sent from
  this prototype.
- Not approved. See the design-review questions in the delivery report
  for what still needs a yes/no before any integration work starts.

## Design source

Structural inspiration came from two reference screenshots supplied
during design review: a compact date/job/client/location list (for the
Jobs/Active/Closings screens) and a yellow spreadsheet-style technician
report (for the Reports screen). No logos, customer information, or
proprietary branding from those references were copied — only structural
ideas (dense rows, clear columns, section headers, thin dividers,
highlighted selection, compact controls).
