# End-to-End Workflow Audit

Written against HEAD `35e7ad5`, as the first step of the "autonomous
three-hour finalization sprint." Based on direct code reading (file:line
citations below), not documentation claims alone — every doc citation
was cross-checked against the code that implements or contradicts it.

**Architecture note:** the repo has multiple parallel UI candidates from
earlier phases (`app.py`, `luxor_demo*.py`, `luxor_ops.py`,
`luxor_console.py`, `luxor_desk.py`, the unconnected `operator_ui_v2`).
`operator_api.py` + `operator_ui_v2_connected/` is the newest,
authenticated, DB-backed, currently-deployed candidate. This audit
treats it (plus the shared `src/dispatcher/*` service layer it wraps) as
the canonical backend, and cites the older Streamlit apps only where
they add corroborating evidence. `docs/FINAL_MVP_PLAN.md`'s M1-M8
milestones describe the *older* `luxor_desk.py` build and are
superseded by this audit where they disagree.

## Classification per lifecycle step

| # | Step | Status | Evidence |
|---|---|---|---|
| 1 | Receive new job via Quo | **IMPLEMENTED** (read-only, structurally incapable of write) | `src/dispatcher/quo_client.py` — `QuoClient` exposes only `_get()`; zero `.post/.put/.patch/.delete` in the file. `quo_ingest.py::poll_for_new_messages()` filters `direction == "incoming"`. `operator_api.py::route_sync` is the manual "Sync" button — no timer, no auto-poll. |
| 2 | Validate genuine actionable job | **IMPLEMENTED** | `src/dispatcher/pipeline.py::process_raw_message()` — extract → classify → profile → decide → format. `policy.py`'s `_POLICY_TABLE` falls back to `SAFE_DEFAULT_POLICY` (`human_review_mandatory=True`) for anything unconfigured. |
| 3 | Send/prepare company "K" | **PREPARE-ONLY IMPLEMENTED**; live send BLOCKED BY DESIGN | `policy.py` sets `acknowledgment_required=True, acknowledgment_text="K"` for AGN/Lucky. `application.py::acknowledge_job_manually()` (line 144) is the real, working, human-confirmed "Mark K sent manually" path, wired via `operator_api.py::route_acknowledge` (line 577) and `app.js`'s "Copy K" / "Mark K sent manually" buttons (lines 567-616). `outbound.py::_send_live()` unconditionally raises `NotImplementedError`; `QUO_OUTBOUND_MODE` defaults to disabled. This is a genuine, working, non-stub manual flow — not a gap. |
| 4 | Select technician deterministically | **IMPLEMENTED** when unambiguous; defers to human otherwise | `storage.list_eligible_technicians_for()` / `assign_technician()` filter by area/skill/vehicle/active. `operator_api.py::_new_job_dto()` auto-suggests only when exactly one technician is eligible; never auto-picks among several. Real technicians' `dispatch_group` was blank per `docs/EXTERNAL_BLOCKERS.md` #1 as of that doc's writing — unverified this session (out of scope, real-data question). |
| 5 | Send/prepare job to technician WhatsApp | **PREPARE-ONLY IMPLEMENTED**; no send capability exists | `formatter.py::format_technician_message()` builds the canonical template. `application.py::prepare_technician_dispatch()` / `confirm_whatsapp_dispatch()` only record milestones (`copied_at`, `whatsapp_opened_at`, `dispatched_manually_at`) — never transmit. `operator_api.py` module docstring: "this server never opens a socket to web.whatsapp.com... 'Open WhatsApp' is a purely client-side action." |
| 6 | Wait for technician "K"/acceptance | **DEMO-ONLY / NOT TRACKED** | `whatsapp_bridge_adapter.py::add_observation()` captures inbound group messages; a bare "K"/"ok"/"👍" is explicitly flagged `human_review_needed=True` rather than interpreted as acceptance (`_ACKNOWLEDGEMENT_ONLY_TEXTS`, line 48). There is **no `technician_accepted_at` column anywhere** — `dispatch_tracking` has no concept of technician-side acceptance. `route_whatsapp_attach()` only updates the observation row, never `jobs`/`events`. |
| 7 | Receive technician status updates | **PARTIALLY IMPLEMENTED — built, tested, unreachable** | `updates.py::map_status_to_reply()` and `application.py::record_technician_update()` (line 245) are fully implemented, idempotent, and tested (`docs/IDEMPOTENCY_MATRIX.md`). **Gap: zero route in `operator_api.py`'s `ROUTES` table calls `record_technician_update`.** A *different*, already-wired "commercial" (price-quote) flow occupies the same UI slot instead (`route_commercial_preview/save/confirm_reply_sent` → `record_commercial_update()` → Shadow Mode tables) — it answers "what did the technician quote," not the Going/ETA/No-answer/Completed vocabulary from `docs/workflow.md` §8. |
| 8 | Send/prepare company updates | **PARTIALLY IMPLEMENTED — same gap as step 7** | `application.py::prepare_company_update()` / `confirm_company_update_sent()` (lines 279, 307) exist and are tested but have **no route**. `operator_api.py::_active_next_action_label()` (line 489) already computes and displays a `"Prepare company reply"` label on the Active screen from `latest_technician_update` — but clicking into that job provides no action to actually do it; only the commercial/quote panel is wired. |
| 9 | Receive technician's closing | **IMPLEMENTED** (manual paste, structured capture) | `application.py::record_closing()` / `operator_api.py::route_closing_record`. Consistent with `docs/workflow.md` §9 ("repeated exactly as the technician reports it"). No automated WhatsApp→closing-form population exists (expected — the bridge is receive-only/observation-only). |
| 10 | Validate closing info/amount | **IMPLEMENTED** (verbatim-only, never recalculates) | `closing.py` docstring: "Never invents or recalculates an amount." `prepare_company_closing()` returns `human_review_required` with a `HumanReviewReason` when amount/payment method is missing or the company has no closing template, rather than fabricating. |
| 11 | Send/prepare closing to company | **PREPARE-ONLY IMPLEMENTED**; live send BLOCKED (same as step 3) | `closing.py::render_closing_from_persisted_fields()` + `route_closing_prepare` / `route_closing_confirm_sent` (recomputes text server-side rather than trusting client-supplied text). |
| 12 | Mark complete + audit trail | **IMPLEMENTED** | `application.py::complete_job()` requires `status == "Closed"`, idempotent no-op on replay. `storage.save_audit_entry()` is the single append-only writer for `events`; `jobs.status` is always derived via `derive_job_status()`, never stored out-of-sync. |

## Sub-question answers (condensed — see the full agent trace this session for exact line numbers)

- **Quo GET-only:** confirmed structurally — `quo_client.py` has exactly one HTTP verb call site (`.get(`), enforced by `tests/test_quo_client.py::test_module_source_contains_no_write_http_verbs`.
- **WhatsApp bridge receive-only:** confirmed structurally — `whatsapp_bridge_adapter.py` implements only `do_GET`/`do_POST /observation`; `whatsapp_shadow_bridge/src/client.js` subscribes only to `"message"`, never `"message_create"`; zero `sendMessage`/`.send(` calls anywhere in the bridge. Two structural tests enforce this.
- **Existing "K" flow:** yes, real and working (see step 3).
- **Deterministic technician assignment:** yes, `list_eligible_technicians_for()` + `assign_technician()`, driven by `rules/service_areas.yaml`.
- **Outbound-delivery abstraction ("prepare/deliver/status", "manual copy", "mark as sent"):** **this already exists, pervasively**, under different names:
  - `dispatch_tracking` table: `first_seen_at → luxor_prepared_at → acknowledged_at → copied_at → whatsapp_opened_at → dispatched_manually_at` — an explicit, additive-only, "set once" prepare→confirm-sent milestone pipeline.
  - The identical prepare/confirm-sent pair repeated for company updates (`mark_company_update_prepared` / `mark_company_update_sent_manually`) and closings (`mark_company_closing_prepared` / `mark_company_closing_sent_manually`).
  - `adapters.py::ManualWhatsAppHandoffAdapter` / `DisabledQuoOutboundAdapter` — named exactly "manual handoff" / "disabled" adapters implementing a confirm-sent contract without ever sending.
  - **Sprint decision:** Step 12 asked for a new `DeliveryConnector.prepare()/deliver()/status()` interface. Building one from scratch would duplicate this existing, tested, safe infrastructure for no new capability — the user's own instructions say not to introduce unnecessary infrastructure. This sprint reuses the existing prepare/confirm-sent pattern rather than wrapping it in a parallel class hierarchy. See the final report for the one-paragraph justification.
- **"Updates owed" queue:** no dedicated screen exists. `_active_next_action_label()` already computes the right value (`"Prepare company reply"`, `"Add technician update"`, etc.) as a column on the Active screen — a read-only indicator with no action behind it, because steps 7-8's routes don't exist yet.
- **Closing-workflow code:** complete, see step 9-11.
- **Audit-trail mechanism:** `storage.save_audit_entry()` is the single append-only writer for `events`; idempotency keys prevent duplicate rows on retry/double-click. A separate, additive Shadow Mode trail (`shadow_episodes`/`shadow_events`) records the commercial/quote flow and does not replace the primary trail.
- **Action Center vs. Command Center:** no "Action Center" exists anywhere in `operator_ui_v2_connected`. Command Center (`route_command_center`) is the closest thing — a read-only composed dashboard with an `attention_now` list — but it is a landing/overview screen, not an actionable per-item queue.

## The one real, high-value gap this audit surfaces

Steps 6 and 7-8 are the weak points, and they are exactly the ones the
sprint's non-negotiable workflow calls out:

1. **Technician "K"/acceptance (step 6)** has no tracking concept at all
   in the schema. Building this correctly (matching evidence: allowlisted
   group, quoted-message ID, ticket/PDL, timestamp proximity, confidence
   category) is a real, multi-hour feature — scoped for a later phase,
   not attempted this sprint (see checkpoint doc).
2. **Technician status updates → company updates owed (steps 7-8)** are
   fully implemented and tested one layer down (`application.py`,
   `storage.py`) but have zero HTTP route and zero UI. This is
   well-scoped, safe (pure wrapper over already-tested code, no schema
   change), and directly closes a non-negotiable requirement — **this
   sprint's primary implementation target.**
