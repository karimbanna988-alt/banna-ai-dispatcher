# Pending Owner Questions

Accumulated during live shadowing. **None of these stop shadowing.** Each
is recorded here, left deliberately unresolved, and never guessed.

Nothing in this file has been applied to the database. Every item below
is still `UNMAPPED_WORK` / unlinked / unconfirmed in the live system.

---

## 1. `Ashraf Charlotte Dispatch` — group identity  *(OPEN)*

**Question:** Is the live WhatsApp group `Ashraf Charlotte Dispatch` the
same group represented by technician Ashraf's configured
`dispatch_group` value `'Ashraf Charlotte Dispatch/ Saturday'`, or is it
a separate operational group?

**Why it can't be auto-resolved:** the two strings differ, and
`/Saturday` `/Sunday` suffixes are a real naming convention in this
business — `Joe Greenville Dispatch/Saturday`, `Ziyadah Dispatch/Sunday`,
and `KhriesChicago Dispatch/Saturday` all exist as distinct groups. So
the suffix may denote a genuinely separate day-specific group rather
than being a typo.

**Evidence gathered (strong, but not deterministic):**
- Most active unmapped group: 24 incoming + 11 outgoing messages.
- Its paired closings group `Ashraf Charlotte Closings` is already
  owner-confirmed and now linked to technician Ashraf (id 10).
- Ticket `JOBW-ZZ3D` was observed flowing from this dispatch group into
  that confirmed closings group — a real cross-group job chain.
- Traffic content is classic technician-dispatch shape (K, ETA,
  no-answer, price negotiation).

**Current state:** `UNMAPPED_WORK`, not confirmed, not linked. Its
messages are captured and shown as `GROUP_MAPPING_REQUIRED`.

---

## 2. `Tarek Savannah Dispatch` — group identity  *(OPEN)*

**Question:** Does this group belong to technician Tarek's permanent
Savannah assignment?

**Why it can't be auto-resolved:** Tarek's technician record has no
configured `dispatch_group`, deliberately — the owner previously
confirmed the Columbia groups as *temporary coverage only* and
instructed that Tarek's permanent record stay untouched. So there is
nothing in the database to prove this group against.

**Evidence:** 3 incoming + 3 outgoing messages; content is
technician-dispatch shaped (including a no-answer report).

**Current state:** `UNMAPPED_WORK`.

---

## 3. Sender identity of the multi-group dispatcher account  *(OPEN, low urgency)*

**Question:** Is WhatsApp sender `149245868920864@lid` the human
dispatcher currently working the shift (referred to as "Safe")?

**Why it matters:** this sender posts in **11 distinct groups** and has
posted **22 full job blocks** — a shape no technician has (technicians
appear in one or two groups). Confirming it would let the system
distinguish "dispatcher forwarded a job" from "technician replied"
by sender rather than by message content alone.

**Why it is not applied:** sender-to-person identity is not
owner-confirmed, and the system deliberately does not assert who sent a
message. The acknowledgement interpretation shipped in `076d2d4`
describes the *message*, never the person, specifically to avoid
depending on this.

**Current state:** recorded as evidence only. No sender identity is
stored or asserted anywhere.

---

## 4. Two company-intake groups naming a technician  *(OPEN, low urgency)*

**Question:** `Augusta - Adam` (COMPANY_JOB_INTAKE) and
`Augusta - Adam - clossings` (COMPANY_CLOSINGS) both carry
`technician_name_unconfirmed = 'Sebai'`. Does that record a routing fact
("jobs from this company go to Sebai") rather than group ownership?

**Why it can't be auto-resolved:** a company intake group is not owned
by a technician, and no technician record points at these group names,
so the deterministic linking rule correctly refuses them.

**Current state:** left unlinked by `link_confirmed_groups_to_technician_records.py`
(covered by a test asserting exactly this refusal).

---

## 5. Freeform job postings without a ticket block  *(OPEN, evidence thin)*

**Question:** Are messages like `843-379-2370 car key 2005 ford f-150`
and `843-321-2187 house lockout 229 johnson...` genuine job postings
that should be parsed, or informal chatter?

**Why not acted on:** only 2 such messages observed so far — below the
repeated-evidence bar, and freeform parsing is materially riskier than
the labelled-field formats. Recorded, not implemented.

**Current state:** these correctly report their missing fields and go to
human review.
