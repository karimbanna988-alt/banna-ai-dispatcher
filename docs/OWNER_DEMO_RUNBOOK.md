# Owner Demo Runbook

Everything needed to run the two-chat AI dispatcher demo. Verified
end-to-end before you arrived — the exact message sequence below is
pinned by tests (`tests/test_demo_dispatcher.py`).

**Production is untouched by all of this.** Receive-only, 0 WhatsApp
sends, 0 Quo writes, production registry and jobs unchanged.

---

## Step 0 — see it before creating anything

```bash
python scripts/demo_dispatcher_console.py --dry-run
```

Replays the whole lifecycle with **no WhatsApp and no database**.
Nothing to set up.

---

## Step 1 — create the two groups (you do this in WhatsApp)

Create two WhatsApp **groups** (not 1:1 chats — the bridge structurally
refuses those, which is why groups keep the safety boundary intact):

1. **DEMO Company**  → you post fabricated jobs here
2. **DEMO Technician** → you reply as the technician here

A group with just you in it is fine.

---

## Step 2 — find their exact IDs

```bash
python scripts/register_demo_chats.py --list --filter DEMO
```

Prints `id  name` for every group whose name contains "DEMO". Copy the
two ids (they end in `@g.us`).

---

## Step 3 — register them

```bash
python scripts/register_demo_chats.py --company <COMPANY_ID> --technician <TECHNICIAN_ID>
```

This writes `.runtime/demo_chats.json` and adds **only those two exact
ids** to the bridge allowlist. No bridge restart needed — the allowlist
is read per message.

It deliberately does **not** add them to `whatsapp_group_registry`, so
production continues to treat them as unmapped and refuses to interpret
them.

---

## Step 4 — open the demo console

```bash
python scripts/demo_dispatcher_console.py
```

Leave it open. It watches only the two demo chats, by exact id.

---

## Step 5 — run the demo

### In **DEMO Company**, paste a fabricated job:

```
Power By Ucallz App

Demo Company
Ticket: DEMO-001
Name: Demo Customer
Phone: (555) 010-0000
Address: 1 Demo Street, Demo City
Desc: House lockout
```

**The console will show:**
- `DETECTED` — New job DEMO-001 -- House lockout
- `DECISION` — Dispatch to Demo Technician
- `WHY` — company, area, service, and that the routing rule matched exactly
- `CONFIDENCE` — EXACT
- `ACTION` — SEND_TO_TECHNICIAN
- `PROPOSED MESSAGE` — the exact technician message, ticket/service/address/phone
- `STATE` — NEW

### In **DEMO Technician**, send:

```
K
```
→ *Technician accepted the job*, CONFIDENCE EXACT, proposes the company
update, state **NEW → ASSIGNED**

```
20 min
```
→ *Technician is on the way (20 min)*, proposes the ETA update to the
company, state **ASSIGNED → EN_ROUTE**
(`on my way` works identically.)

```
Done 180 cash
```
→ *Closing report -- $180 cash*, proposes the company closing, state
**EN_ROUTE → COMPLETED**

---

## Step 6 — the negative case (the important one)

Start a second job (post another job in DEMO Company with a new ticket,
e.g. `DEMO-002`), send `K`, then in **DEMO Technician** send:

```
Done
```

**The system must refuse to close it:**
- `DETECTED` — Technician says the job is done, but stated no amount
- `ACTION` — **HUMAN_REVIEW_NEEDED**
- `MISSING` — amount, payment_method *(never invented)*
- state does **not** become COMPLETED

That is the whole point: it will not fabricate a price.

Another worth trying — a figure mentioned in passing is not a closing:
```
customer is arguing about the $150
```
→ no amount recorded, job not completed.

---

## The CRM

`http://localhost:8512` (also `http://127.0.0.1:8512`) — verified
reachable. Login password is in `.env` as `LUXOR_DEMO_PASSWORD`.

The CRM shows the **live production shadow feed** (Seif's real shift).
The demo chats appear there as unmapped groups with no interpretation —
which is exactly the isolation working.

---

## What is real vs demo

**Real (reused, not reimplemented):** field extraction is
`parser.extract_job`, the same function production uses. The
acknowledgement/decline vocabulary is `technician_ack`'s. The closing
rule is the same adjacency-anchored shape production uses.

**Fabricated on purpose:** the company, area and technician identities,
and the routing rule between them — so the demo can never reference a
real technician or imply a real customer.

## What stays true throughout

- Nothing is sent. The console prints the message a dispatcher *would*
  send, for you to copy.
- The demo reasoning engine is pure — no database connection, no SQL, no
  file access — so it cannot reach production jobs/events at all.
- It never calls the production status machine, so it cannot manufacture
  a real job status change.
- Ambiguity always resolves to `HUMAN_REVIEW_NEEDED`, never a guess.
