# WhatsApp Routing Map -- Owner Mapping Review

**Status: RECEIVE-ONLY SHADOW MODE. Nothing below is active configuration.**
Every mapping in this document is a tentative, name-derived suggestion built by cross-
referencing the 372-group live WhatsApp inventory (`docs/WHATSAPP_GROUP_INVENTORY.md`)
against the real (read-only) `technicians` table. Nothing here has been written to
`data/dispatcher.db`, the WhatsApp allowlist, or any runtime config. No group has been
enabled. No message has been sent. Every row is marked for explicit owner confirmation
before it can become real routing logic.

## 0. Owner-confirmed facts (2026-08-11)

These are **facts**, not name-derived suggestions -- provided directly by the owner and not to be
contradicted by group-name inference elsewhere in this document. Everything else in this document
remains a tentative suggestion pending confirmation; only the items below are settled.

1. **`Adam - Greenville`** (`120363424464824773@g.us`) and **`Adam - Greenville Closings`**
   (`120363409883999573@g.us`) belong to **Mike Jobs**, a company/job-source -- not a technician,
   not an alias, and not related to GMI's own "Adams" contact (see Section 2 for that
   disambiguation).
2. **`Augusta - Adam`** (`120363400431562993@g.us`) and **`Augusta - Adam - clossings`**
   (`120363399822651490@g.us`) also belong to **Mike Jobs**.
3. **Mike Jobs + Augusta -> Sebai.** Confirmed to route to the already-matched Sebai Augusta
   groups: `Sebai Augusta Dispatch` (`120363424227833077@g.us`) and `Sebai augusta closing`
   (`120363426584048614@g.us`).
4. **Mike Jobs + Greenville splits by service type**, not a single area->technician mapping:
   - Lockouts and house/residential lock work -> **Toto** -> `Toto Greenville Dispatch`
     (`120363407762864599@g.us`) / `Toto Greenville Closings` (`120363429123808793@g.us`).
   - Car keys and harder/more complex jobs -> **Joe** -> `Joe Greenville Dispatch/Saturday`
     (`120363425260967940@g.us`) / `Joe Greenville Closings` (`120363423478202519@g.us`).
   - Any Greenville service type outside these two confirmed categories (e.g. "lock installation",
     which the DB already lists as a skill for technician Kimo) is **not** decided by this document
     -- it is `HUMAN_REVIEW_NEEDED` until the owner supplies more rules.
5. **GMI is a company/source**, full stop -- not a technician, not a technician alias, and no
   `Adam - <Area>` group is assumed to belong to GMI just because both mention an "Adam"/"Adams".

**Explicitly still unconfirmed** (not to be inferred from the above): `Adam - Charlotte NC`,
`Adam - Charlotte NC Closings`, `ADAM - ASHEVILLE NC`, `Adam - Asheville NC CLOSINGS`,
`ADAM SC/GA`, `ADAM SUB 50% NC/SC/GA/VA/WA` -- these were NOT confirmed as Mike Jobs and are left
exactly as ambiguous as before. Whether `Adam - Greenville Closings` and `Augusta - Adam -
clossings` are truly Mike Jobs' company-closing destinations (as opposed to some other group) is
also an inference from naming pattern, not something the owner stated explicitly -- flagged in
Section 6, not upgraded to fact status here.

## 1. Headline counts

- Groups discovered: **372**
- Confidently classified as work-related (dispatch/closing/company, non-ambiguous keyword match): **83**
  - Company job-intake groups: **5**
  - Company closing groups: **2**
  - Technician dispatch groups: **39**
  - Technician closing groups: **37**
- Confidently classified as non-work (legacy-format group ID, no business keyword -- almost
  certainly personal/social groups on this linked account): **206**
- Unknown -- new-format group ID, no keyword matched, genuinely unclear from the name alone: **83**
- ALL 372 groups require owner confirmation before any code treats them as real, except the
  206 NON_WORK/IGNORE ones, which need no action.

Two groups (`Jobs Update 132`, `Jobs Update 133`) matched the word "jobs" but are almost
certainly personal, not company job-intake, and were manually excluded from that count as a
known false-positive pattern -- flagged here rather than silently miscounted.

## 2. Companies

Three company/job-source identities are now established: two by name in the group inventory
(GMI, AGN), and one owner-confirmed despite not saying its own name in the group (Mike Jobs).

### Mike Jobs (owner-confirmed -- see Section 0)

| group_id | exact_group_name | role | area | confirmed technician |
|---|---|---|---|---|
| `120363424464824773@g.us` | Adam - Greenville | COMPANY_JOB_INTAKE | Greenville | splits by service type -- see Section 0 |
| `120363409883999573@g.us` | Adam - Greenville Closings | COMPANY_CLOSINGS (inferred destination, not owner-stated) | Greenville | - |
| `120363400431562993@g.us` | Augusta - Adam | COMPANY_JOB_INTAKE | Augusta | Sebai |
| `120363399822651490@g.us` | Augusta - Adam - clossings | COMPANY_CLOSINGS (inferred destination, not owner-stated) | Augusta | Sebai |

**Important disambiguation:** Mike Jobs' contact is referred to as "Adam" in these group names.
This is a **different person/entity than GMI's own contact "Adams"** (see below) -- the two
happen to share a similar name but are confirmed-separate companies. Do not merge them.

`Adam - Charlotte NC`, `Adam - Charlotte NC Closings`, `ADAM - ASHEVILLE NC`, `Adam - Asheville
NC CLOSINGS`, `ADAM SC/GA`, `ADAM SUB 50% NC/SC/GA/VA/WA` are **not confirmed** as Mike Jobs --
left as UNKNOWN pending owner input, not assumed.

### GMI (contact: "Adams" -- NOT the same "Adam" as Mike Jobs above)

| group_id | exact_group_name | role guess | area |
|---|---|---|---|
| `120363429428302880@g.us` | GMI Adams job receiving | COMPANY_JOB_INTAKE | (none in name) |
| `120363427915008647@g.us` | GMI Adams Savannah Jobs | COMPANY_JOB_INTAKE | Savannah |
| `120363412686198496@g.us` | Adams closings GMI All Areas | COMPANY_CLOSINGS | (none in name) |

This matches the exact example pattern you described (GMI's contact "Adams" -> job-receiving
group -> per-area job groups -> a single "All Areas" closing group). `GMI Adams job receiving`
has no area in its name -- likely the general/default intake channel, with `GMI Adams Savannah
Jobs` as an area-specific one. **Open question for you:** are there more `GMI Adams <Area> Jobs`
groups for other areas, or is Savannah the only area-specific GMI channel and everything else
routes through the general intake group?

### AGN

| group_id | exact_group_name | role guess | area |
|---|---|---|---|
| `120363417036944991@g.us` | AGN Delaware CLOSINGS | COMPANY_CLOSINGS | Delaware |

Only a **closing** group was found for AGN (`AGN Delaware CLOSINGS`) -- no group whose name
clearly reads as AGN's job-intake channel was found among the 372. Either AGN's intake channel
has a name that doesn't say "AGN" (needs your identification), or AGN jobs currently arrive by
a channel outside WhatsApp group shadowing (e.g. a source this system already parses some other
way -- `src/dispatcher/parser.py` already recognizes AGN by message header, independent of
which group it arrives in).

### Main Leads / Lucky

`src/dispatcher/parser.py` recognizes these as known job sources, but **no discovered group
name contains either term**. Same open question as AGN: if these arrive via WhatsApp groups at
all, their group names don't say so -- needs your identification.

### Other job-intake-shaped groups (no company name detected)

| group_id | exact_group_name | area | note |
|---|---|---|---|
| `120363426349991534@g.us` | Khries Lead Management | (none) | Looks operational (lead/job/schedule language) but no company brand in the name -- could be an internal admin group rather than a company channel. |
| `120363407514149856@g.us` | Mike//Adam Local Leads - Raleigh | Raleigh | Looks operational (lead/job/schedule language) but no company brand in the name -- could be an internal admin group rather than a company channel. |
| `120363407868760676@g.us` | Schedule jobs Augusta | Augusta | Looks operational (lead/job/schedule language) but no company brand in the name -- could be an internal admin group rather than a company channel. |

## 3. Technicians

### 3a. Cross-referenced against the real `technicians` table

Exact-string comparison of each technician's configured `dispatch_group`/`closing_group`
(already owner-entered, not name-guessed) against the 372 live group names. This is the only
part of this document backed by data you already confirmed, not name inference.

| technician | DB area(s) | dispatch_group (DB) | live match? | closing_group (DB) | live match? |
|---|---|---|---|---|---|
| Kabaka | Columbia | Ismail columbia Dispatch | **NO EXACT MATCH in live inventory** | Ismail Columbia Closings | YES -> `120363392480509639@g.us` |
| Mohamed Osama | NJ | (not configured) | - | (not configured) | - |
| Sebai | Augusta | Sebai Augusta Dispatch | YES -> `120363424227833077@g.us` | Sebai augusta closing | YES -> `120363426584048614@g.us` |
| Ziyadah | Raleigh | (not configured) | - | (not configured) | - |
| Ashraf | Charlotte | Ashraf Charlotte Dispatch/ Saturday | **NO EXACT MATCH in live inventory** | Ashraf Charlotte Closings | YES -> `120363428143815156@g.us` |
| Falcon | Vancouver | (not configured) | - | (not configured) | - |
| Mohamed Shabaan | Greenville | (not configured) | - | (not configured) | - |
| Kimo | Greenville | (not configured) | - | (not configured) | - |
| Youssef Sweillam | Greenville | (not configured) | - | (not configured) | - |
| Abozeid | Arlington | (not configured) | - | (not configured) | - |
| Fares | Virginia Beach | (not configured) | - | (not configured) | - |
| Kheris | Chicago | (not configured) | - | (not configured) | - |
| Luxor | Savannah | (not configured) | - | (not configured) | - |
| Tarek | Savannah | (not configured) | - | (not configured) | - |
| Omar Shata | Savannah | (not configured) | - | (not configured) | - |

**Two configured values don't exist in the live inventory as typed:**
- Kabaka's `dispatch_group` is `"Ismail columbia Dispatch"` -- the live inventory has
  `Ismail agaza Dispatch` (different area word) and `Ismail Columbia Closings` (a closing group,
  not dispatch), but no group literally named `Ismail columbia Dispatch`. Possible rename, typo,
  or the group no longer exists under that name.
- Ashraf's `dispatch_group` is `"Ashraf Charlotte Dispatch/ Saturday"` -- the live inventory
  only has `Ashraf Charlotte Dispatch` (no `/Saturday` suffix). Same possibilities.

### 3b. Owner-confirmed technicians not yet in the `technicians` table (Toto, Joe)

Per Section 0, **Toto** and **Joe** are confirmed real technicians handling Mike Jobs/Greenville
work, split by service type. Neither appears as a row in the `technicians` table today.

| technician | confirmed role | dispatch group | closing group |
|---|---|---|---|
| Toto | Mike Jobs / Greenville / lockouts + house-residential lock work | `Toto Greenville Dispatch` (`120363407762864599@g.us`) | `Toto Greenville Closings` (`120363429123808793@g.us`) |
| Joe | Mike Jobs / Greenville / car keys + harder/complex jobs | `Joe Greenville Dispatch/Saturday` (`120363425260967940@g.us`) | `Joe Greenville Closings` (`120363423478202519@g.us`) |

**This creates a direct conflict with the DB, worth flagging plainly:** the `technicians` table
already has **three** Greenville-area technicians with skills configured -- Mohamed Shabaan
(`car lockout`, `house lockout`), Kimo (`car keys`, `house keys`, `lock installation`), and
Youssef Sweillam (all of the above). None of those three names match "Toto" or "Joe". Possible
explanations, none assumed here:
- Toto/Joe are WhatsApp aliases/nicknames for two of those three DB technicians, or
- Toto/Joe are technicians who genuinely aren't in the DB yet and need to be added, or
- the three DB Greenville rows are stale and no longer reflect who actually works Greenville today.

This needs your reconciliation before Greenville routing can be built -- listed in Section 6.

### 3c. Name-matched candidates for technicians with NO dispatch/closing group configured

These technicians have `NULL` in both DB fields. The groups below matched by first-name
substring against the technician's name -- **suggestion only, several have area mismatches or
ambiguous name collisions flagged explicitly.**

| group_id | exact_group_name | category | area | candidate technician | flag |
|---|---|---|---|---|---|
| `120363406134197775@g.us` | Fares Virginia Beach Dispatch | TECH_DISPATCH | Virginia Beach | Fares |  |
| `120363403321651687@g.us` | Abozeid Arlington Dispatch | TECH_DISPATCH | Arlington | Abozeid |  |
| `120363425255488739@g.us` | Tarek columbia dispatch | TECH_DISPATCH | Columbia | Tarek | Tarek's DB area is Savannah only -- this group implies Tarek may also cover Columbia, or there's a second person named Tarek. Not in DB's `areas_json`. |
| `120363424227833077@g.us` | Sebai Augusta Dispatch | TECH_DISPATCH | Augusta | Sebai |  |
| `120363411941241893@g.us` | Kimo Alabama Dispatch | TECH_DISPATCH | Alabama | Kimo | DB says Kimo's area is Greenville; this group says Alabama. Area mismatch -- either DB is stale, Kimo also covers Alabama, or a different Kimo. |
| `120363404172800287@g.us` | Tarek Savannah Dispatch | TECH_DISPATCH | Savannah | Tarek |  |
| `120363408179176581@g.us` | Fares Virginia Beach Closings | TECH_CLOSINGS | Virginia Beach | Fares |  |
| `120363408595340686@g.us` | Kimo Alabama Closings | TECH_CLOSINGS | Alabama | Kimo | Same mismatch (Alabama closing counterpart). |
| `120363425277781798@g.us` | Tarek columbia closings | TECH_CLOSINGS | Columbia | Tarek | Same as above (Columbia closing counterpart). |
| `120363428199635701@g.us` | Ashraf Charlotte Dispatch | TECH_DISPATCH | Charlotte | Ashraf |  |
| `120363428143815156@g.us` | Ashraf Charlotte Closings | TECH_CLOSINGS | Charlotte | Ashraf |  |
| `120363426584048614@g.us` | Sebai augusta closing | TECH_CLOSINGS | Augusta | Sebai |  |
| `120363420900690444@g.us` | Ziyadah closings | TECH_CLOSINGS | - | Ziyadah |  |
| `120363421773052415@g.us` | Ziyadah Dispatch/Sunday | TECH_DISPATCH | - | Ziyadah |  |
| `120363401104609706@g.us` | Khries Chicago Closings | TECH_CLOSINGS | Chicago | Kheris (alias candidate -- spelling variant, NOT confirmed) | Name spelled "Khries" vs DB's "Kheris" -- spelling variant, area (Chicago) matches DB exactly, so this is a reasonably strong candidate but still unconfirmed. |
| `120363408345627351@g.us` | Luxor Savannah dispatch | TECH_DISPATCH | Savannah | Luxor |  |
| `120363422531151840@g.us` | Abozeid Arlington Closings | TECH_CLOSINGS | Arlington | Abozeid |  |
| `120363414378981497@g.us` | Luxor savannah closings | TECH_CLOSINGS | Savannah | Luxor |  |
| `120363423142361260@g.us` | Tarek Savannah Closings | TECH_CLOSINGS | Savannah | Tarek |  |
| `120363403179158034@g.us` | Mohamed Delaware Dispatch | TECH_DISPATCH | Delaware | AMBIGUOUS -- could be Mohamed Osama OR Mohamed Shabaan (multiple DB technicians share this first name) | AMBIGUOUS by design: two DB technicians share the first name "Mohamed" (Mohamed Osama, area NJ; Mohamed Shabaan, area Greenville). This group says "Delaware" -- matches neither technician's DB area. Do not assume either without your confirmation. |
| `120363402764308679@g.us` | Mohamed Delaware Closings | TECH_CLOSINGS | Delaware | AMBIGUOUS -- could be Mohamed Osama OR Mohamed Shabaan (multiple DB technicians share this first name) | Same ambiguity as above (Delaware closing counterpart). |
| `120363424544764208@g.us` | Omar Closings DE for Joe | TECH_CLOSINGS | DE | Omar Shata | Group name is "Omar Closings DE for Joe" -- reads as Omar closing on behalf of a technician named Joe, for Delaware. DB's Omar Shata is Savannah-only. Could be a coincidental name match, not the same Omar. |
| `120363418011867566@g.us` | Luxor Charleston Dispatch | TECH_DISPATCH | Charleston | Luxor |  |
| `120363422611738767@g.us` | Luxor Charleston Closings | TECH_CLOSINGS | Charleston | Luxor |  |
| `120363421850567182@g.us` | Abozeid Charleston Dispatch | TECH_DISPATCH | Charleston | Abozeid | DB lists Abozeid's only area as Arlington; this group says Charleston -- DB's `areas_json` may be incomplete rather than this being a different person (Arlington groups DID match cleanly). |
| `120363404902885686@g.us` | Abozeid Charleston Closings | TECH_CLOSINGS | Charleston | Abozeid | Same (Charleston closing counterpart). |

### 3d. Recurring identities that appear in MULTIPLE groups but match NO technician in the DB

These names show up consistently (dispatch + closing pair, sometimes across several areas) but
do not correspond to any of the 15 rows in `technicians`. This is the single most important
*remaining* open finding in this review: **the live WhatsApp footprint has more working
relationships than the `technicians` table currently represents.** Each of these still needs an
owner decision: is this an existing technician's WhatsApp alias, a technician not yet entered in
the system, or a company/broker relationship rather than an individual technician?

`Adam` (Greenville and Augusta occurrences) has been **removed from this table** -- Section 0
confirms those specifically are the Mike Jobs company, not a technician identity. The Charlotte
NC / Asheville NC `Adam` occurrences remain genuinely unresolved and are listed separately below,
not folded back into this table, since they were not part of what the owner confirmed. `Toto` and
`Joe` have also been removed -- both are now confirmed technicians per Section 3b, no longer
ambiguous, even though neither is in the DB yet.

| name-like token | # groups | areas seen | sample group |
|---|---|---|---|
| beeko | 3 | Delaware | BEEKO DELAWARE CLOSINGS |
| joy locksmith | 3 | Columbia, Delaware, Raleigh | Joy Locksmith Delaware Dispatch |
| osos | 2 | NJ | Osos nj Closings |
| hashem | 2 | Vancouver | Vancouver Closing Hashem |
| ahmed | 2 | Delaware | Ahmed Delaware Dispatch |
| bassam | 2 | Alabama | Bassam Alabama Closing |
| farouk | 2 | Charleston | Farouk Charleston Dispatch |
| bekoo | 2 | Augusta | Bekoo Augusta Dispatch |

**Unresolved `Adam` occurrences (not confirmed as Mike Jobs or anything else):**

| group_id | exact_group_name | area |
|---|---|---|
| `120363425775173739@g.us` | Adam - Charlotte NC | Charlotte NC |
| `120363429084866103@g.us` | Adam - Charlotte NC Closings | Charlotte NC |
| `120363409378615504@g.us` | ADAM - ASHEVILLE NC | Asheville NC |
| `120363427497698453@g.us` | Adam - Asheville NC CLOSINGS | Asheville NC |
| `120363426454925857@g.us` | ADAM SC/GA | - |
| `120363424476089511@g.us` | ADAM SUB 50% NC/SC/GA/VA/WA | - |

Also present but only 1 group each (so excluded from the "recurring" table above, still worth
your attention since a single dispatch-or-closing-only group can still be real): `Trio`, `Mark`,
`Daniel`, `Mike`, `Amir`, `Shataa`/`Shataaa` (Charleston), `San Antonio` (bare area name, no
person), `Dispatch Team`, `Dispatchers`, and a bare group literally named `Dispatch`. The last
three look like internal/admin coordination groups rather than a single technician's channel --
flagged as `UNKNOWN` in the appendix rather than guessed into a technician slot.

## 4. Proposed deterministic routing registry (schema only -- not built, not populated)

**Corrected per Section 0/owner feedback: routing cannot be a flat `(company, area) -> group`**
**table.** Greenville proves one company + one area can still split across multiple technicians
by job/service type. The registry needs two tables, not one: a group registry (what every group
*is*), and a separate routing-rule table (which technician a job actually goes to).

```
whatsapp_group_registry
  group_id            TEXT PRIMARY KEY   -- WhatsApp's own id, e.g. 120363...@g.us
  exact_name          TEXT               -- last-seen group name (names can change; id is truth)
  role                TEXT               -- COMPANY_JOB_INTAKE | COMPANY_CLOSINGS |
                                          -- TECH_DISPATCH | TECH_CLOSINGS | IGNORE
  company             TEXT NULL          -- e.g. 'GMI', 'AGN', 'Mike Jobs' -- NULL for technician-role rows
  service_area        TEXT NULL          -- matches technicians.areas_json vocabulary
  technician_id       INTEGER NULL       -- FK -> technicians.id, NULL for company-role rows
  is_active           BOOLEAN            -- owner can disable a mapping without deleting history
  confirmed_by_owner  BOOLEAN NOT NULL DEFAULT 0
  confirmed_at        TIMESTAMP NULL
  source              TEXT               -- 'name_heuristic' | 'owner_manual' -- always heuristic
                                          -- until confirmed_by_owner flips to 1

company_routing_rules
  id                  INTEGER PRIMARY KEY
  company             TEXT               -- e.g. 'Mike Jobs'
  service_area        TEXT               -- e.g. 'Greenville'
  service_type_match  TEXT               -- e.g. 'lockout,house_lock' or 'car_key,hard_job' --
                                          -- vocabulary should reuse extract_service_type()'s
                                          -- existing output (src/dispatcher/parser.py:214),
                                          -- not a second competing classifier
  technician_id       INTEGER            -- FK -> technicians.id
  confirmed_by_owner  BOOLEAN NOT NULL DEFAULT 0
  confirmed_at        TIMESTAMP NULL
```

`company_routing_rules` is what actually encodes "Mike Jobs + Greenville + lockout -> Toto" as
data instead of code. A job whose `(company, area, service_type)` doesn't match any confirmed
rule row is exactly the `HUMAN_REVIEW_NEEDED` case the owner described for Greenville services
outside lockout/house-lock and car-key/hard-job -- the absence of a match, not a guess, is what
drives the review queue.

The existing `technicians.skills_json` field (already populated for the DB's three Greenville
rows: `car lockout`, `house lockout`, `car keys`, `house keys`, `lock installation`) is
suggestive of exactly this kind of service-type routing already having been anticipated in the
schema -- but as Section 3b notes, those three DB names don't match the owner-confirmed Toto/Joe
identities, so this can't be wired up automatically; it needs the owner's reconciliation first.

Every row in both tables this analysis produced would seed with `confirmed_by_owner = 0` and
`is_active = 0` -- inert until reviewed, EXCEPT the Section 0 facts, which would seed already
`confirmed_by_owner = 1` since the owner stated them directly (still `is_active = 0` until you
explicitly say to turn ingestion on). Unregistered/unconfirmed groups can never create a job,
dispatch, or closing, mirroring the existing allowlist's fail-closed default.

## 5. Proposed continuous multi-group shadow architecture (design only)

Extends the existing single-group shadow pipeline (`whatsapp_shadow_bridge/`) rather than
replacing it -- same fail-closed allowlist model, just driven by the registry above instead of a
single hardcoded group.

1. **Every confirmed, active registry row is observed.** The bridge's existing allowlist check
   (`src/allowlist.js`) becomes registry-driven: a group is only ever processed if it has a
   `whatsapp_group_registry` row with `is_active = 1 AND confirmed_by_owner = 1`. Everything else
   is ignored exactly as today (`ignored: group X is not on the allowlist`).
2. **Incoming messages persisted.** Each accepted message stored with: message id, group id,
   registry role at time of receipt, direction (`in`), sender, timestamp, raw text, quoted-
   message id -- reusing the existing message-capture shape in `messageHandler.js`, just widened
   from 1 group to N.
3. **Outgoing/self messages: audit-only, never inbound work.** `message.fromMe` is already
   detected and discarded from the forwarding pipeline today (`client.js`). For multi-group mode,
   the discarded event would additionally be logged to an audit-only table (never the job-
   creating pipeline) so a human can later see what this account itself sent, without that record
   ever being able to trigger `REVIEW_NEW_JOB` or any other workflow transition.
4. **No outgoing message creates new inbound work.** Structural, not a runtime check: the
   ingestion function that creates review-queue entries would only ever be called from the
   `fromMe === false` branch. There is no code path from the audit log back into intake.
5. **Message-id dedup, per existing `dedupeStore.js` (5000-entry cap), unchanged in shape --**
   just needs to hold IDs across many groups instead of one; no design change, just volume.
6. **Ticket/company isolation.** A ticket number from one company's job-intake group must never
   auto-match a technician K/update from a different company's dispatch group. Correlation keys
   on `(company, ticket)` or `(technician_id, dispatch message id)` pairs, not on ticket alone --
   this prevents two companies that happen to reuse the same ticket numbering from colliding.
7. **Cross-group correlation**, reusing the existing `jobs` / `messages` / `events` /
   `technician_acceptance` tables (per your Priority 9 spec from the original brief) rather than
   inventing a parallel data model: company job-intake message -> job -> technician dispatch ->
   technician K/update -> technician closing -> company closing, each hop an existing event type,
   just sourced from more groups.
8. **`HUMAN_REVIEW_NEEDED` triggers** whenever: a message arrives in a `TECH_DISPATCH` or
   `TECH_CLOSINGS` group with no open job to correlate to; a closing arrives with a missing
   amount (existing rule, extended); a message arrives in a group whose registry role is
   ambiguous or was recently changed; or a group not yet in the registry sends a message that
   looks like job content (best-effort keyword hint only, never auto-created as a job).
9. **Routing lookup is `(company, service_area, service_type) -> technician`, never a bare**
   **`area -> technician` shortcut** -- corrected per Section 0/owner feedback. One company can
   split one area across multiple technicians by job type (Mike Jobs/Greenville is the proven
   case); collapsing to area-only would silently misroute. See Section 4's
   `company_routing_rules` table.

None of this is built. This is the proposal for you to approve, amend, or reject before any code
is written.

## 6. Exact confirmations needed from you

**Resolved by Section 0, no longer open:** Adam-Greenville/Augusta identity, Mike Jobs+Augusta
routing, Mike Jobs+Greenville service-type split, GMI's company status. Removed from this list.

1. **Greenville reconciliation (highest priority new item):** the DB's three Greenville
   technicians (Mohamed Shabaan, Kimo, Youssef Sweillam, each with skills already configured)
   don't match the owner-confirmed Toto/Joe identities. Are Toto/Joe aliases of two of those three,
   or separate technicians who need to be added to the DB? This blocks building Greenville routing
   at all until resolved.
2. **Mike Jobs' closing destinations**: is `Adam - Greenville Closings` really Mike Jobs'
   Greenville company-closing group, and `Augusta - Adam - clossings` really its Augusta one?
   These are inferred from naming pattern, not part of what you explicitly confirmed.
3. **Mike Jobs' other areas**: are `Adam - Charlotte NC`, `ADAM - ASHEVILLE NC`, `ADAM SC/GA`,
   `ADAM SUB 50% NC/SC/GA/VA/WA` also Mike Jobs, or unrelated? Not assumed either way.
4. **Greenville service types beyond lockout/house-lock and car-key/hard-job** (e.g. "lock
   installation", already a DB skill for Kimo) -- which technician, if any, handles these under
   Mike Jobs? Per your instruction, this stays `HUMAN_REVIEW_NEEDED` until you say otherwise.
5. **GMI**: is `GMI Adams job receiving` the general/default intake, with per-area groups only
   for some areas (confirmed: Savannah)? Which other areas have their own GMI group, if any?
6. **AGN / Main Leads / Lucky**: none of their job-intake groups were identifiable by name. Do
   these arrive via WhatsApp groups at all? If so, which group(s)?
7. **Kabaka's dispatch_group** (`"Ismail columbia Dispatch"`) and **Ashraf's dispatch_group**
   (`"Ashraf Charlotte Dispatch/ Saturday"`) don't exactly match any live group name -- please
   confirm the current correct group for each, or correct the DB value.
8. **Tarek**: DB area is Savannah only, but `Tarek columbia dispatch`/`closings` exist live --
   does Tarek also cover Columbia, or is this a different person?
9. **Mohamed (Delaware)**: DB has two technicians whose first name is Mohamed (Osama/NJ,
   Shabaan/Greenville) -- neither area matches "Delaware". Which technician, if either, runs
   this group?
10. **Kimo**: DB area is Greenville, live groups say Alabama -- same technician covering both, or
    a different Kimo? (Note: this is a separate open question from item 1's Greenville/Toto/Joe
    question -- Kimo's own name appears verbatim in an Alabama-named group, unrelated to Mike Jobs.)
11. **Abozeid**: DB area is Arlington only; live groups also show a complete Charleston
    dispatch+closing pair under his name -- add Charleston to his `areas_json`, or different
    person?
12. **The remaining recurring, DB-absent identities** (Beeko/Bekoo, Joy Locksmith, Osos, Hashem,
    Ahmed, Bassam, Farouk -- section 3d): for each, is this an existing technician's WhatsApp
    alias, a technician not yet in the system, or a broker/company relationship?
13. **`Osos NJ dispatch`/`closings`**: possible phonetic/spelling variant of Mohamed **Osama**
    (whose DB area is NJ, with no dispatch/closing group configured) -- confirm or reject.
14. Whether to proceed with building the registry tables + multi-group ingestion (section 4/5) at
    all, and if so, starting with which confirmed subset (e.g. the technicians/relationships
    already clean: Sebai incl. Mike Jobs/Augusta, Fares, Luxor, Abozeid-Arlington, Ashraf-closing,
    Kabaka-closing -- Greenville/Toto/Joe excluded until item 1 above is reconciled).

## 7. Full 372-group appendix (this analysis's classification)

Supersedes the category column in `docs/WHATSAPP_GROUP_INVENTORY.md` (which used a 5-bucket,
less granular scheme) with the 6-bucket scheme used throughout this document. Sorted by
category, then name.

**Note:** this table is the mechanical, name-heuristic output and was not hand-edited row-by-row
for the Section 0 facts. For the 6 Mike Jobs / Toto / Joe rows specifically (`Adam - Greenville`,
`Adam - Greenville Closings`, `Augusta - Adam`, `Augusta - Adam - clossings`, the two `Toto`
groups, the two `Joe` groups), trust Sections 0/2/3 above, not the `company`/`candidate
technician` columns below.

| group_id | exact_group_name | category | company | area | candidate technician |
|---|---|---|---|---|---|
| `120363429428302880@g.us` | GMI Adams job receiving | COMPANY_JOB_INTAKE | GMI | - | - |
| `120363427915008647@g.us` | GMI Adams Savannah Jobs | COMPANY_JOB_INTAKE | GMI | Savannah | - |
| `120363426349991534@g.us` | Khries Lead Management | COMPANY_JOB_INTAKE | - | - | - |
| `120363407514149856@g.us` | Mike//Adam Local Leads - Raleigh | COMPANY_JOB_INTAKE | - | Raleigh | - |
| `120363407868760676@g.us` | Schedule jobs Augusta | COMPANY_JOB_INTAKE | - | Augusta | - |
| `120363412686198496@g.us` | Adams closings GMI All Areas | COMPANY_CLOSINGS | GMI | - | - |
| `120363417036944991@g.us` | AGN Delaware CLOSINGS | COMPANY_CLOSINGS | AGN | Delaware | - |
| `120363403321651687@g.us` | Abozeid Arlington Dispatch | TECH_DISPATCH | - | Arlington | Abozeid |
| `120363421850567182@g.us` | Abozeid Charleston Dispatch | TECH_DISPATCH | - | Charleston | Abozeid |
| `120363401109233236@g.us` | Ahmed Delaware Dispatch | TECH_DISPATCH | - | Delaware | - |
| `120363428199635701@g.us` | Ashraf Charlotte Dispatch | TECH_DISPATCH | - | Charlotte | Ashraf |
| `120363421410114618@g.us` | Bassam Alabama Dispatch | TECH_DISPATCH | - | Alabama | - |
| `120363401223938383@g.us` | Beeko Dispatch Delaware | TECH_DISPATCH | - | Delaware | - |
| `120363405707504909@g.us` | Bekoo Augusta Dispatch | TECH_DISPATCH | - | Augusta | - |
| `120363404586604674@g.us` | Delaware dispatch | TECH_DISPATCH | - | Delaware | - |
| `120363423140052802@g.us` | Dispatch | TECH_DISPATCH | - | - | - |
| `120363418907127697@g.us` | Dispatch delaware | TECH_DISPATCH | - | Delaware | - |
| `120363423121570395@g.us` | Dispatch Team | TECH_DISPATCH | - | - | - |
| `120363427329400350@g.us` | Dispatchers | TECH_DISPATCH | - | - | - |
| `120363406134197775@g.us` | Fares Virginia Beach Dispatch | TECH_DISPATCH | - | Virginia Beach | Fares |
| `120363420206936935@g.us` | Farouk Charleston Dispatch | TECH_DISPATCH | - | Charleston | - |
| `120363377646777301@g.us` | Ismail agaza Dispatch | TECH_DISPATCH | - | - | - |
| `120363346596208813@g.us` | Joe Delaware Dispatch | TECH_DISPATCH | - | Delaware | - |
| `120363425260967940@g.us` | Joe Greenville Dispatch/Saturday | TECH_DISPATCH | - | Greenville | - |
| `120363340443536301@g.us` | Joy Locksmith Columbia Dispatch | TECH_DISPATCH | - | Columbia | - |
| `120363345820489200@g.us` | Joy Locksmith Delaware Dispatch | TECH_DISPATCH | - | Delaware | - |
| `120363367002811086@g.us` | Joy locksmith Raleigh Dispatch | TECH_DISPATCH | - | Raleigh | - |
| `120363406092155275@g.us` | KhriesChicago Dispatch/Saturday | TECH_DISPATCH | - | - | - |
| `120363411941241893@g.us` | Kimo Alabama Dispatch | TECH_DISPATCH | - | Alabama | Kimo |
| `120363418011867566@g.us` | Luxor Charleston Dispatch | TECH_DISPATCH | - | Charleston | Luxor |
| `120363408345627351@g.us` | Luxor Savannah dispatch | TECH_DISPATCH | - | Savannah | Luxor |
| `120363427617650990@g.us` | Mark nj dispatch | TECH_DISPATCH | - | NJ | - |
| `120363403179158034@g.us` | Mohamed Delaware Dispatch | TECH_DISPATCH | - | Delaware | AMBIGUOUS -- could be Mohamed Osama OR Mohamed Shabaan (multiple DB technicians share this first name) |
| `120363403736659314@g.us` | Osos NJ dispatch | TECH_DISPATCH | - | NJ | - |
| `120363406291541239@g.us` | San Antonio Dispatch/Monday | TECH_DISPATCH | - | San Antonio | - |
| `120363424227833077@g.us` | Sebai Augusta Dispatch | TECH_DISPATCH | - | Augusta | Sebai |
| `120363422261595869@g.us` | Shataaa Charleston Dipatch | TECH_DISPATCH | - | Charleston | - |
| `120363418771294703@g.us` | St. Pete Dispatch | TECH_DISPATCH | - | St. Pete | - |
| `120363422119499970@g.us` | St. Pete Dispetch | TECH_DISPATCH | - | St. Pete | - |
| `120363425255488739@g.us` | Tarek columbia dispatch | TECH_DISPATCH | - | Columbia | Tarek |
| `120363404172800287@g.us` | Tarek Savannah Dispatch | TECH_DISPATCH | - | Savannah | Tarek |
| `120363407762864599@g.us` | Toto Greenville Dispatch | TECH_DISPATCH | - | Greenville | - |
| `120363410595686038@g.us` | Trio Dispatch | TECH_DISPATCH | - | - | - |
| `120363424128482715@g.us` | Vancouver Dispatch | TECH_DISPATCH | - | Vancouver | - |
| `120363409763747953@g.us` | Vancouver Dispatch Hashem | TECH_DISPATCH | - | Vancouver | - |
| `120363421773052415@g.us` | Ziyadah Dispatch/Sunday | TECH_DISPATCH | - | - | Ziyadah |
| `120363422531151840@g.us` | Abozeid Arlington Closings | TECH_CLOSINGS | - | Arlington | Abozeid |
| `120363404902885686@g.us` | Abozeid Charleston Closings | TECH_CLOSINGS | - | Charleston | Abozeid |
| `120363427497698453@g.us` | Adam - Asheville NC CLOSINGS | TECH_CLOSINGS | - | Asheville NC | - |
| `120363429084866103@g.us` | Adam - Charlotte NC Closings | TECH_CLOSINGS | - | Charlotte NC | - |
| `120363409883999573@g.us` | Adam - Greenville Closings | TECH_CLOSINGS | - | Greenville | - |
| `120363403604981447@g.us` | Ahmed Delaware Closings | TECH_CLOSINGS | - | Delaware | - |
| `120363363208638101@g.us` | Amir Columbia Closings | TECH_CLOSINGS | - | Columbia | - |
| `120363428143815156@g.us` | Ashraf Charlotte Closings | TECH_CLOSINGS | - | Charlotte | Ashraf |
| `120363399822651490@g.us` | Augusta - Adam - clossings | TECH_CLOSINGS | - | Augusta | - |
| `120363423141058670@g.us` | Bassam Alabama Closing | TECH_CLOSINGS | - | Alabama | - |
| `120363419496003209@g.us` | BEEKO DELAWARE CLOSINGS | TECH_CLOSINGS | - | Delaware | - |
| `120363403151465498@g.us` | Beeko Delaware Closings | TECH_CLOSINGS | - | Delaware | - |
| `120363424417483388@g.us` | Bekoo Augusta Closings | TECH_CLOSINGS | - | Augusta | - |
| `120363409119640702@g.us` | Cheap and fast Adam closing | TECH_CLOSINGS | - | - | - |
| `120363408179176581@g.us` | Fares Virginia Beach Closings | TECH_CLOSINGS | - | Virginia Beach | Fares |
| `120363403644040859@g.us` | Farouk Charleston Closings | TECH_CLOSINGS | - | Charleston | - |
| `120363392480509639@g.us` | Ismail Columbia Closings | TECH_CLOSINGS | - | Columbia | - |
| `120363334123868337@g.us` | Joe Delaware Closings Joy locksmith | TECH_CLOSINGS | - | Delaware | - |
| `120363423478202519@g.us` | Joe Greenville Closings | TECH_CLOSINGS | - | Greenville | - |
| `120363418587402372@g.us` | Joy Closings | TECH_CLOSINGS | - | - | - |
| `120363401104609706@g.us` | Khries Chicago Closings | TECH_CLOSINGS | - | Chicago | Kheris (alias candidate -- spelling variant, NOT confirmed) |
| `120363408595340686@g.us` | Kimo Alabama Closings | TECH_CLOSINGS | - | Alabama | Kimo |
| `120363422611738767@g.us` | Luxor Charleston Closings | TECH_CLOSINGS | - | Charleston | Luxor |
| `120363414378981497@g.us` | Luxor savannah closings | TECH_CLOSINGS | - | Savannah | Luxor |
| `120363402764308679@g.us` | Mohamed Delaware Closings | TECH_CLOSINGS | - | Delaware | AMBIGUOUS -- could be Mohamed Osama OR Mohamed Shabaan (multiple DB technicians share this first name) |
| `120363424544764208@g.us` | Omar Closings DE for Joe | TECH_CLOSINGS | - | DE | Omar Shata |
| `120363420633655712@g.us` | Osos nj Closings | TECH_CLOSINGS | - | NJ | - |
| `120363410086136159@g.us` | San Antonio Closings | TECH_CLOSINGS | - | San Antonio | - |
| `120363426584048614@g.us` | Sebai augusta closing | TECH_CLOSINGS | - | Augusta | Sebai |
| `120363423263506985@g.us` | Shataa Charleston Closings | TECH_CLOSINGS | - | Charleston | - |
| `120363419178859376@g.us` | St Pete. Closing | TECH_CLOSINGS | - | St Pete | - |
| `120363425277781798@g.us` | Tarek columbia closings | TECH_CLOSINGS | - | Columbia | Tarek |
| `120363423142361260@g.us` | Tarek Savannah Closings | TECH_CLOSINGS | - | Savannah | Tarek |
| `120363429123808793@g.us` | Toto Greenville Closings | TECH_CLOSINGS | - | Greenville | - |
| `120363428396358524@g.us` | Vancouver Closing Hashem | TECH_CLOSINGS | - | Vancouver | - |
| `120363406073039318@g.us` | Vancouver Closings | TECH_CLOSINGS | - | Vancouver | - |
| `120363420900690444@g.us` | Ziyadah closings | TECH_CLOSINGS | - | - | Ziyadah |
| `120363197377146431@g.us` | NEW YEAR’S EVE!!🕺😎 | UNKNOWN | - | - | - |
| `120363261107029866@g.us` | "طريق التعافي" | UNKNOWN | - | - | - |
| `120363241025228902@g.us` | "طريق التعافي" | UNKNOWN | - | - | - |
| `120363045164476641@g.us` | ... | UNKNOWN | - | - | - |
| `120363026055494751@g.us` | ... | UNKNOWN | - | - | - |
| `120363421199837215@g.us` | 28/3🤵 | UNKNOWN | - | - | - |
| `120363129796316363@g.us` | 8-May Class UK (Alex) | UNKNOWN | - | - | - |
| `120363151654854592@g.us` | Abu Dhabi job vacancy | UNKNOWN | - | - | - |
| `120363409378615504@g.us` | ADAM - ASHEVILLE NC | UNKNOWN | - | Asheville NC | - |
| `120363425775173739@g.us` | Adam - Charlotte NC | UNKNOWN | - | Charlotte NC | - |
| `120363424464824773@g.us` | Adam - Greenville | UNKNOWN | - | Greenville | - |
| `120363406823378030@g.us` | Adam -Savanna | UNKNOWN | - | - | - |
| `120363426454925857@g.us` | ADAM SC/GA | UNKNOWN | - | GA | - |
| `120363424476089511@g.us` | ADAM SUB 50% NC/SC/GA/VA/WA | UNKNOWN | - | GA | - |
| `120363397505184895@g.us` | Ahmed’s Bday - Feb 21 - Grand House Lounge | UNKNOWN | - | - | - |
| `120363427387023802@g.us` | AL VIGITINIA SUPPORT 40% | UNKNOWN | - | - | - |
| `120363403727119661@g.us` | Alex Trainings 2 | UNKNOWN | - | - | - |
| `120363141446528441@g.us` | Annuals | UNKNOWN | - | - | - |
| `120363039883223462@g.us` | AT&T -MSS- Wave 10 | UNKNOWN | - | - | - |
| `120363022116563063@g.us` | At&t MSS wave 10 (11-Jan) | UNKNOWN | - | - | - |
| `120363400431562993@g.us` | Augusta - Adam | UNKNOWN | - | Augusta | - |
| `120363427586099092@g.us` | Cheap & Fast Locksmkth Savannah | UNKNOWN | - | Savannah | - |
| `120363184060648393@g.us` | Dahaab علسحطوط | UNKNOWN | - | - | - |
| `120363419995467153@g.us` | Dahab Hangouts | UNKNOWN | - | - | - |
| `120363422103893720@g.us` | Daniel 24/7 / Adam Columbia | UNKNOWN | - | Columbia | - |
| `120363331858859575@g.us` | Events ⚡️ | UNKNOWN | - | - | - |
| `120363213125823187@g.us` | Family | UNKNOWN | - | - | - |
| `120363261526683862@g.us` | Family 😍 | UNKNOWN | - | - | - |
| `120363109231882161@g.us` | Fetar 2023 ✨ | UNKNOWN | - | - | - |
| `120363164712158957@g.us` | GYM | UNKNOWN | - | - | - |
| `120363406171412101@g.us` | Gym UFC Client | UNKNOWN | - | - | - |
| `120363076752367711@g.us` | Hi | UNKNOWN | - | - | - |
| `120363146485872137@g.us` | Jobs Update 132 ❤️ | UNKNOWN | - | - | - |
| `120363167806350264@g.us` | Jobs Update 133 | UNKNOWN | - | - | - |
| `120363409346625556@g.us` | Joy / Key Craft Vriginia | UNKNOWN | - | - | - |
| `120363404977680135@g.us` | Kabo Codes group | UNKNOWN | - | - | - |
| `120363406416094026@g.us` | Karim - medicine | UNKNOWN | - | - | - |
| `120363023386208160@g.us` | Kicking Assets 😎 | UNKNOWN | - | - | - |
| `120363145453149607@g.us` | Layla’s Engagement | UNKNOWN | - | - | - |
| `120363419970570185@g.us` | Locksmith 24 seven, Philadelphia Newcastle Delaware | UNKNOWN | - | Delaware | - |
| `120363044564439972@g.us` | marioma and alex bois🤠 | UNKNOWN | - | - | - |
| `120363160125566988@g.us` | Marketing Alfakhera 📉 | UNKNOWN | - | - | - |
| `120363166428740385@g.us` | Marketing prs | UNKNOWN | - | - | - |
| `120363333641927543@g.us` | MDLBEAST events | UNKNOWN | - | - | - |
| `120363361267888804@g.us` | Mike Boss Codes Group | UNKNOWN | - | - | - |
| `120363332333316437@g.us` | Miss Raver Door List ✨ | UNKNOWN | - | - | - |
| `120363314045322349@g.us` | Miss Raver Door List ✨ | UNKNOWN | - | - | - |
| `120363042033037136@g.us` | NASM | UNKNOWN | - | - | - |
| `120363024391575570@g.us` | NASM Alex 17 Jun | UNKNOWN | - | - | - |
| `120363401216276380@g.us` | NEW                 LOG | UNKNOWN | - | - | - |
| `120363418560048640@g.us` | Nonizzz b_dayyy💕 | UNKNOWN | - | - | - |
| `120363420096958542@g.us` | Outing 💃🏻 eros 3:8 sunday | UNKNOWN | - | - | - |
| `120363239877910116@g.us` | PLUR FAMILY | UNKNOWN | - | - | - |
| `120363332167951537@g.us` | Raves | UNKNOWN | - | - | - |
| `120363422956014836@g.us` | Team Alex Trainings | UNKNOWN | - | - | - |
| `120363405189361488@g.us` | Team Alex Trainings | UNKNOWN | - | - | - |
| `120363418872935383@g.us` | test combia | UNKNOWN | - | - | - |
| `120363397429602448@g.us` | The Happy Collective™ | UNKNOWN | - | - | - |
| `120363023450740261@g.us` | Weekends 💃🏻 | UNKNOWN | - | - | - |
| `120363134188322434@g.us` | Yehia’s SP @Mivida 26/5 | UNKNOWN | - | - | - |
| `120363431862873956@g.us` | اخبار الهيكل الخدمي لزمالة المدمنين المجهولين | UNKNOWN | - | - | - |
| `120363158777520453@g.us` | اصحاب الهمم | UNKNOWN | - | - | - |
| `120363405034348275@g.us` | الاصدقاء ❤️ | UNKNOWN | - | - | - |
| `120363039651895024@g.us` | الدراغمة👴🏿 | UNKNOWN | - | - | - |
| `120363411053460476@g.us` | الدعم الفني لعملاء نوفا iptv | UNKNOWN | - | - | - |
| `120363210323456854@g.us` | الشـلة💃🏿 | UNKNOWN | - | - | - |
| `120363316644327800@g.us` | اللوح الارشادية بالمصنع | UNKNOWN | - | - | - |
| `120363429430879845@g.us` | انتظار مجموعة طريق التعافي | UNKNOWN | - | - | - |
| `120363152759292918@g.us` | اهم حاجة الصحة 😒 | UNKNOWN | - | - | - |
| `120363321560882103@g.us` | تصوير المصنع اوقست | UNKNOWN | - | - | - |
| `120363311793513350@g.us` | تصوير المصنع جولاي 24 | UNKNOWN | - | - | - |
| `120363238893877387@g.us` | حفلات تيكنوا | UNKNOWN | - | - | - |
| `120363403969169550@g.us` | ختمه قرآنيه لشافعي في الشهر الكريم | UNKNOWN | - | - | - |
| `120363288970607194@g.us` | عمالقة الرياض الثلاثة | UNKNOWN | - | - | - |
| `120363408867622917@g.us` | عمالقة الهلع الثلاث | UNKNOWN | - | - | - |
| `120363042977823819@g.us` | عمالقة بودي جيم الاربعة | UNKNOWN | - | - | - |
| `120363046788401123@g.us` | عمالقة جولدز جيم الاربعة | UNKNOWN | - | - | - |
| `120363401583764152@g.us` | عمالقه سيدي جابر البلد | UNKNOWN | - | - | - |
| `120363400504684695@g.us` | فعاليات اتحاد طلاب تحيا مصر _ محافظة الإسكندرية | UNKNOWN | - | - | - |
| `120363166810702207@g.us` | قسم الفرح والمرح ANNUAL LEAVES 💃🏻💃🏻😂 | UNKNOWN | - | - | - |
| `120363237380399014@g.us` | ‎Open this link to join my WhatsApp Community: https://chat.whatsapp.com/DCoayKwRoSrERs9HeugH5d | UNKNOWN | - | - | - |
| `120363048883407122@g.us` | 🎉 SP 🎂 | UNKNOWN | - | - | - |
| `120363170375333363@g.us` | 😎 | UNKNOWN | - | - | - |
| `201115595770-1466805936@g.us` | .. | NON_WORK/IGNORE | - | - | - |
| `201015806940-1503164124@g.us` | 007 | NON_WORK/IGNORE | - | - | - |
| `201201979468-1542896991@g.us` | 12th 🎥 | NON_WORK/IGNORE | - | - | - |
| `201201979468-1562004171@g.us` | 25/8 Training | NON_WORK/IGNORE | - | - | - |
| `201014057570-1509960717@g.us` | 2eddeha 😎😎😎 | NON_WORK/IGNORE | - | - | - |
| `201002819281-1432982324@g.us` | 2s7b so2 | NON_WORK/IGNORE | - | - | - |
| `201062055597-1558479861@g.us` | 3id milad nona | NON_WORK/IGNORE | - | - | - |
| `201144117774-1426610298@g.us` | 5erougaaa | NON_WORK/IGNORE | - | - | - |
| `201222999906-1424114404@g.us` | 5erougat youm el sabat | NON_WORK/IGNORE | - | - | - |
| `201121230863-1507234929@g.us` | A7la Hezar Ba2a Wala Eh 💃 | NON_WORK/IGNORE | - | - | - |
| `201125210910-1434928361@g.us` | Ah ya donia | NON_WORK/IGNORE | - | - | - |
| `201015806940-1516795540@g.us` | Akl | NON_WORK/IGNORE | - | - | - |
| `201015806940-1487315109@g.us` | Al shabab | NON_WORK/IGNORE | - | - | - |
| `201211622197-1522910864@g.us` | Applied Art & PR Proj.🙆🏻‍♀ | NON_WORK/IGNORE | - | - | - |
| `201276673753-1518425464@g.us` | Applied arts group c | NON_WORK/IGNORE | - | - | - |
| `201015806940-1462656771@g.us` | Arabic | NON_WORK/IGNORE | - | - | - |
| `201276322934-1569195907@g.us` | Arabic Editing Skills (M) | NON_WORK/IGNORE | - | - | - |
| `201098198455-1541312576@g.us` | Arabic News Project 🎬🎤🎞🎥 | NON_WORK/IGNORE | - | - | - |
| `201116656144-1507380107@g.us` | Arabic Project 📚 | NON_WORK/IGNORE | - | - | - |
| `201062215336-1634838165@g.us` | AT&T MSS Consultants | NON_WORK/IGNORE | - | - | - |
| `201025719700-1451832147@g.us` | Aza marwan | NON_WORK/IGNORE | - | - | - |
| `201008154719-1425509857@g.us` | Balba3 bokra | NON_WORK/IGNORE | - | - | - |
| `201015806940-1619445144@g.us` | Bang bang | NON_WORK/IGNORE | - | - | - |
| `201068935198-1567162985@g.us` | Bashmohandes fares 🎬🎥 | NON_WORK/IGNORE | - | - | - |
| `201064479909-1457373280@g.us` | Biology | NON_WORK/IGNORE | - | - | - |
| `201020008088-1502572263@g.us` | Birthday | NON_WORK/IGNORE | - | - | - |
| `201015806940-1489696434@g.us` | Bokra 5atab | NON_WORK/IGNORE | - | - | - |
| `201068848080-1425474341@g.us` | brothers | NON_WORK/IGNORE | - | - | - |
| `201024257770-1583173538@g.us` | Cairo | NON_WORK/IGNORE | - | - | - |
| `201277714347-1507200534@g.us` | CAS Actors 2017 Sept 1A | NON_WORK/IGNORE | - | - | - |
| `201014057570-1508103210@g.us` | CAS club | NON_WORK/IGNORE | - | - | - |
| `201015806940-1463427160@g.us` | Chemistry | NON_WORK/IGNORE | - | - | - |
| `201015806940-1416167961@g.us` | Class 2016 ✌️ | NON_WORK/IGNORE | - | - | - |
| `201027179552-1537044629@g.us` | ClassB Term 5⃣ | NON_WORK/IGNORE | - | - | - |
| `201015806940-1557698034@g.us` | Clc almonyam | NON_WORK/IGNORE | - | - | - |
| `201211622197-1557070430@g.us` | CLC ALUMNI FINAL | NON_WORK/IGNORE | - | - | - |
| `201276322934-1556038144@g.us` | College of Maritime AAST | NON_WORK/IGNORE | - | - | - |
| `201145551527-1511642414@g.us` | Comm. theories🤦🏻‍♂ | NON_WORK/IGNORE | - | - | - |
| `201001606937-1555086771@g.us` | Communication group projc | NON_WORK/IGNORE | - | - | - |
| `201201979468-1537719254@g.us` | Communication Technology | NON_WORK/IGNORE | - | - | - |
| `201000976450-1506171008@g.us` | Communication Theories C | NON_WORK/IGNORE | - | - | - |
| `201015806940-1503351391@g.us` | Dahab | NON_WORK/IGNORE | - | - | - |
| `201227000234-1503266612@g.us` | Dahab 😍 | NON_WORK/IGNORE | - | - | - |
| `201096770448-1551436001@g.us` | Documentary | NON_WORK/IGNORE | - | - | - |
| `201201979468-1550236974@g.us` | Documentary | NON_WORK/IGNORE | - | - | - |
| `201207077499-1557942136@g.us` | Documentary interviews | NON_WORK/IGNORE | - | - | - |
| `201096770448-1556458428@g.us` | Documentary كيلاكيت ٣ مرة | NON_WORK/IGNORE | - | - | - |
| `201068935198-1559070991@g.us` | E7na el alumni tani | NON_WORK/IGNORE | - | - | - |
| `20115977335-1433535883@g.us` | El fadyeeen | NON_WORK/IGNORE | - | - | - |
| `201144117774-1425569924@g.us` | El maganeen | NON_WORK/IGNORE | - | - | - |
| `201210702011-1556541740@g.us` | El regala | NON_WORK/IGNORE | - | - | - |
| `201014057570-1530880398@g.us` | El warsha | NON_WORK/IGNORE | - | - | - |
| `201010533303-1487843380@g.us` | Elda7i7a | NON_WORK/IGNORE | - | - | - |
| `201002760348-1631747821@g.us` | Elsal7een💨👳 | NON_WORK/IGNORE | - | - | - |
| `201002819281-1416943480@g.us` | english private | NON_WORK/IGNORE | - | - | - |
| `201115416665-1505760609@g.us` | Engy's sp 6/10 | NON_WORK/IGNORE | - | - | - |
| `201200445226-1525953057@g.us` | Español  "C" | NON_WORK/IGNORE | - | - | - |
| `201015806940-1526419018@g.us` | Fam | NON_WORK/IGNORE | - | - | - |
| `201020008088-1490999096@g.us` | Family | NON_WORK/IGNORE | - | - | - |
| `201068848080-1469305093@g.us` | Family | NON_WORK/IGNORE | - | - | - |
| `201015806940-1621598284@g.us` | FARA7 a5o madyy !! | NON_WORK/IGNORE | - | - | - |
| `201003160441-1545404776@g.us` | Final🎬🎬 | NON_WORK/IGNORE | - | - | - |
| `201000053410-1635177346@g.us` | Fitness Department Maadi | NON_WORK/IGNORE | - | - | - |
| `201110077591-1465563991@g.us` | Ftar bokra | NON_WORK/IGNORE | - | - | - |
| `201092498794-1558493077@g.us` | ftar el 5amis | NON_WORK/IGNORE | - | - | - |
| `201201677955-1633621858@g.us` | Game over | NON_WORK/IGNORE | - | - | - |
| `201128855110-1503609183@g.us` | Gaming | NON_WORK/IGNORE | - | - | - |
| `201287764774-1518194103@g.us` | Gapped besties 😂👀 | NON_WORK/IGNORE | - | - | - |
| `201098657799-1520968370@g.us` | Gift 50, tom tamara 2:30 | NON_WORK/IGNORE | - | - | - |
| `201015806940-1431277294@g.us` | good luck | NON_WORK/IGNORE | - | - | - |
| `201011556266-1570717955@g.us` | Grad.Project في حب السيسي | NON_WORK/IGNORE | - | - | - |
| `201283326311-1586167275@g.us` | graduating class of 2020 | NON_WORK/IGNORE | - | - | - |
| `201223226436-1581942474@g.us` | Graduation meetings | NON_WORK/IGNORE | - | - | - |
| `201201979468-1570712961@g.us` | Graduation project 👨🏻‍🎓👩🏻‍🎓 | NON_WORK/IGNORE | - | - | - |
| `201110222140-1476996958@g.us` | Graduation 🎓 | NON_WORK/IGNORE | - | - | - |
| `97466843790-1506076947@g.us` | Group C 🎬 | NON_WORK/IGNORE | - | - | - |
| `201010533303-1506165094@g.us` | Group c.t | NON_WORK/IGNORE | - | - | - |
| `201015806940-1467733885@g.us` | Gym | NON_WORK/IGNORE | - | - | - |
| `201015806940-1497678517@g.us` | Gym | NON_WORK/IGNORE | - | - | - |
| `201015806940-1504647356@g.us` | Gym | NON_WORK/IGNORE | - | - | - |
| `201090151141-1502412612@g.us` | Hacienda | NON_WORK/IGNORE | - | - | - |
| `201090151141-1498776768@g.us` | Hacienda bokra | NON_WORK/IGNORE | - | - | - |
| `201141112259-1423240363@g.us` | Hat3mloo eh ya khwalat??? | NON_WORK/IGNORE | - | - | - |
| `201144117774-1418214891@g.us` | Iliad (Group A) | NON_WORK/IGNORE | - | - | - |
| `201211622197-1541704346@g.us` | intercultural comm. | NON_WORK/IGNORE | - | - | - |
| `201276322934-1520600263@g.us` | Intro to journalism 2 | NON_WORK/IGNORE | - | - | - |
| `201010533303-1526404072@g.us` | Jornalism project | NON_WORK/IGNORE | - | - | - |
| `201200203903-1633694390@g.us` | Kaumeya Reunion | NON_WORK/IGNORE | - | - | - |
| `201015806940-1469364267@g.us` | Ketf today | NON_WORK/IGNORE | - | - | - |
| `201210702011-1580827300@g.us` | King Mariouteya | NON_WORK/IGNORE | - | - | - |
| `201200203903-1632663323@g.us` | Kolotat | NON_WORK/IGNORE | - | - | - |
| `201010533303-1506525518@g.us` | Language for media | NON_WORK/IGNORE | - | - | - |
| `201276673753-1491472222@g.us` | Literary text 😇 | NON_WORK/IGNORE | - | - | - |
| `201015806940-1440013128@g.us` | Ma niggas | NON_WORK/IGNORE | - | - | - |
| `201119931187-1569466871@g.us` | Ma3rseeen♥️♥️♥️ | NON_WORK/IGNORE | - | - | - |
| `201201979468-1518020290@g.us` | Magazine Group C | NON_WORK/IGNORE | - | - | - |
| `201068848080-1443978284@g.us` | Mama | NON_WORK/IGNORE | - | - | - |
| `201129393455-1458839229@g.us` | Marwan's birthday | NON_WORK/IGNORE | - | - | - |
| `201010533303-1506968695@g.us` | Masra7 lkolia | NON_WORK/IGNORE | - | - | - |
| `201010533303-1507648870@g.us` | Mass com project | NON_WORK/IGNORE | - | - | - |
| `201201979468-1506195541@g.us` | MEDIA 2020🤷🏻‍♀ | NON_WORK/IGNORE | - | - | - |
| `201111946961-1568885393@g.us` | Media 7⃣B | NON_WORK/IGNORE | - | - | - |
| `201001606937-1585226071@g.us` | Media lit 🔥 | NON_WORK/IGNORE | - | - | - |
| `201001606937-1589905835@g.us` | Media literacy | NON_WORK/IGNORE | - | - | - |
| `201024111418-1587025022@g.us` | Media Management 12th | NON_WORK/IGNORE | - | - | - |
| `201228504878-1584359626@g.us` | Media management 7th | NON_WORK/IGNORE | - | - | - |
| `201066134418-1589761844@g.us` | media mangemnt final | NON_WORK/IGNORE | - | - | - |
| `201023018984-1500984778@g.us` | Media presentation | NON_WORK/IGNORE | - | - | - |
| `201111946961-1538042659@g.us` | Media production | NON_WORK/IGNORE | - | - | - |
| `201012835011-1560422856@g.us` | Media production city | NON_WORK/IGNORE | - | - | - |
| `201144117774-1451920820@g.us` | Meen hairou7 le marwan | NON_WORK/IGNORE | - | - | - |
| `201019290965-1506925094@g.us` | Mickey's birthday 🎉 🎊🎈💙 | NON_WORK/IGNORE | - | - | - |
| `201144117774-1459499402@g.us` | Miss Omneya revision | NON_WORK/IGNORE | - | - | - |
| `201006887871-1430855705@g.us` | Moustafa Koum | NON_WORK/IGNORE | - | - | - |
| `201155530451-1574782207@g.us` | Mr Sameh Selim's course | NON_WORK/IGNORE | - | - | - |
| `201015806940-1626616802@g.us` | Msh yala wala eh? | NON_WORK/IGNORE | - | - | - |
| `201141745546-1390647453@g.us` | MUN | NON_WORK/IGNORE | - | - | - |
| `201287764774-1488792841@g.us` | MyBD Alegria Thursday9/3 | NON_WORK/IGNORE | - | - | - |
| `201287764774-1491575067@g.us` | Nehal's sp | NON_WORK/IGNORE | - | - | - |
| `201111946961-1537188434@g.us` | New Media | NON_WORK/IGNORE | - | - | - |
| `201015806940-1480773639@g.us` | Night | NON_WORK/IGNORE | - | - | - |
| `201000053410-1635275475@g.us` | Night Shift | NON_WORK/IGNORE | - | - | - |
| `201062088196-1499071396@g.us` | Novel | NON_WORK/IGNORE | - | - | - |
| `201015806940-1478164596@g.us` | Oldies of 32 | NON_WORK/IGNORE | - | - | - |
| `201025719700-1451734670@g.us` | Omayier's suprise party | NON_WORK/IGNORE | - | - | - |
| `201096770448-1545118024@g.us` | Oral proficiency | NON_WORK/IGNORE | - | - | - |
| `201116241155-1422818350@g.us` | OverTrain-2-Grow | NON_WORK/IGNORE | - | - | - |
| `201010533303-1510574607@g.us` | Phonetics pvt | NON_WORK/IGNORE | - | - | - |
| `201207077499-1554894328@g.us` | Photography Project 📸 📸 | NON_WORK/IGNORE | - | - | - |
| `201001606937-1526982435@g.us` | Press Kit | NON_WORK/IGNORE | - | - | - |
| `201151784424-1539517950@g.us` | Projects | NON_WORK/IGNORE | - | - | - |
| `201116656144-1511188221@g.us` | Public Opinion Project 📚📕 | NON_WORK/IGNORE | - | - | - |
| `97466843790-1523958346@g.us` | Radio & Tv project | NON_WORK/IGNORE | - | - | - |
| `201096770448-1553454925@g.us` | Radio and tv | NON_WORK/IGNORE | - | - | - |
| `201003160441-1555433751@g.us` | Radio project🎙️🎙️ | NON_WORK/IGNORE | - | - | - |
| `201025719700-1536077239@g.us` | Regala | NON_WORK/IGNORE | - | - | - |
| `201015806940-1559161371@g.us` | Regala bass | NON_WORK/IGNORE | - | - | - |
| `201115233796-1585572658@g.us` | Research | NON_WORK/IGNORE | - | - | - |
| `201201979468-1542738941@g.us` | Research methods | NON_WORK/IGNORE | - | - | - |
| `201011556266-1582804850@g.us` | Research Team | NON_WORK/IGNORE | - | - | - |
| `201140100883-1608475667@g.us` | Reunion | NON_WORK/IGNORE | - | - | - |
| `16478249150-1511178528@g.us` | Reunion ya wlad el ws5a | NON_WORK/IGNORE | - | - | - |
| `201008154719-1432115752@g.us` | Sa7el | NON_WORK/IGNORE | - | - | - |
| `971562891451-1487168351@g.us` | Sahra | NON_WORK/IGNORE | - | - | - |
| `201124235214-1464498673@g.us` | Sat2 | NON_WORK/IGNORE | - | - | - |
| `201227000234-1502917991@g.us` | Saturday - King | NON_WORK/IGNORE | - | - | - |
| `201027397190-1585076503@g.us` | Script writing | NON_WORK/IGNORE | - | - | - |
| `201211669990-1603909564@g.us` | Secret tal3a | NON_WORK/IGNORE | - | - | - |
| `201015806940-1427021176@g.us` | secrets | NON_WORK/IGNORE | - | - | - |
| `201286559686-1442787443@g.us` | Seniors '16 | NON_WORK/IGNORE | - | - | - |
| `201015806940-1517437211@g.us` | Shabab | NON_WORK/IGNORE | - | - | - |
| `201201979468-1515325897@g.us` | Shabab el gam3a😊 | NON_WORK/IGNORE | - | - | - |
| `201015806940-1539903277@g.us` | Shababetos | NON_WORK/IGNORE | - | - | - |
| `201015806940-1452637651@g.us` | Sneak a little | NON_WORK/IGNORE | - | - | - |
| `201015806940-1474745133@g.us` | Sp | NON_WORK/IGNORE | - | - | - |
| `201113225582-1428346605@g.us` | Sp | NON_WORK/IGNORE | - | - | - |
| `201000868014-1427116321@g.us` | Sp .... | NON_WORK/IGNORE | - | - | - |
| `201111946961-1548846824@g.us` | Spanish magazine 🤷🏻‍♀ | NON_WORK/IGNORE | - | - | - |
| `201094175682-1525249472@g.us` | Spanish presentation | NON_WORK/IGNORE | - | - | - |
| `201150422809-1558524927@g.us` | Spanishh | NON_WORK/IGNORE | - | - | - |
| `201015806940-1476871078@g.us` | Squad | NON_WORK/IGNORE | - | - | - |
| `201015806940-1501245650@g.us` | Sro | NON_WORK/IGNORE | - | - | - |
| `201010533303-1499018951@g.us` | Summer | NON_WORK/IGNORE | - | - | - |
| `201015806940-1484946104@g.us` | Sunday | NON_WORK/IGNORE | - | - | - |
| `201003386857-1505908378@g.us` | Surprise engy💃💃💃💃💃 | NON_WORK/IGNORE | - | - | - |
| `201112463779-1614086257@g.us` | Ta3yenat 🥄🍽️ | NON_WORK/IGNORE | - | - | - |
| `201140246498-1568104759@g.us` | Techne summit | NON_WORK/IGNORE | - | - | - |
| `201116021666-1567591856@g.us` | Techno summit | NON_WORK/IGNORE | - | - | - |
| `201116021666-1567603493@g.us` | Techno summit event | NON_WORK/IGNORE | - | - | - |
| `201008155485-1487188520@g.us` | Term 1 Feb.2017 | NON_WORK/IGNORE | - | - | - |
| `201203238837-1490018639@g.us` | Term 2 | NON_WORK/IGNORE | - | - | - |
| `201015806940-1446290698@g.us` | The Squad 💪🏻 | NON_WORK/IGNORE | - | - | - |
| `201015806940-1417166844@g.us` | The squad 😎 | NON_WORK/IGNORE | - | - | - |
| `201115416665-1489612694@g.us` | Thursday | NON_WORK/IGNORE | - | - | - |
| `201203908882-1513688445@g.us` | Thursday | NON_WORK/IGNORE | - | - | - |
| `201203238837-1508745586@g.us` | Translation 1 | NON_WORK/IGNORE | - | - | - |
| `201067114429-1430737092@g.us` | Tuesdayy | NON_WORK/IGNORE | - | - | - |
| `201201979468-1556292771@g.us` | TV Finaaallll gamed 📺 | NON_WORK/IGNORE | - | - | - |
| `201223226436-1577276774@g.us` | Tv&radio | NON_WORK/IGNORE | - | - | - |
| `201141112259-1567446636@g.us` | Vandetta | NON_WORK/IGNORE | - | - | - |
| `201012510255-1586346102@g.us` | Visual reporting section | NON_WORK/IGNORE | - | - | - |
| `201207434752-1581351676@g.us` | Visual Reporting Tutor. | NON_WORK/IGNORE | - | - | - |
| `201201979468-1550304672@g.us` | Writing for media | NON_WORK/IGNORE | - | - | - |
| `201096770448-1556742358@g.us` | Writing for media 🎬 | NON_WORK/IGNORE | - | - | - |
| `201015806940-1467673926@g.us` | Z Team | NON_WORK/IGNORE | - | - | - |
| `201015806940-1428696458@g.us` | Zaa Gang 😎💣🔫 | NON_WORK/IGNORE | - | - | - |
| `201554967540-1614673111@g.us` | احلي دفعه ٢٠٢١ | NON_WORK/IGNORE | - | - | - |
| `201210702011-1541512154@g.us` | اشباح PubG | NON_WORK/IGNORE | - | - | - |
| `201128855110-1501683533@g.us` | الخنازير | NON_WORK/IGNORE | - | - | - |
| `201554967540-1614805019@g.us` | السرية التانيه | NON_WORK/IGNORE | - | - | - |
| `201129908889-1457775055@g.us` | الشلة الجاحدة 😂🖕🏻 | NON_WORK/IGNORE | - | - | - |
| `201003160441-1524504410@g.us` | المنياكوتش2.0 | NON_WORK/IGNORE | - | - | - |
| `201280409704-1486155070@g.us` | الورشة | NON_WORK/IGNORE | - | - | - |
| `201212910817-1619009897@g.us` | خير يارب دائما ❤️ | NON_WORK/IGNORE | - | - | - |
| `201200203903-1634466848@g.us` | ربعي | NON_WORK/IGNORE | - | - | - |
| `201200203903-1446067274@g.us` | سيكرت توبس | NON_WORK/IGNORE | - | - | - |
| `201096770448-1566685346@g.us` | شباب الجامعة 📽 | NON_WORK/IGNORE | - | - | - |
| `201001606937-1526980555@g.us` | شباب المستقبل | NON_WORK/IGNORE | - | - | - |
| `201150422809-1577736583@g.us` | شباب المستقبل 2 | NON_WORK/IGNORE | - | - | - |
| `201206894971-1628264825@g.us` | عمارة المعمارى برج هاء | NON_WORK/IGNORE | - | - | - |
| `201117736838-1598663758@g.us` | مستشفى الميرى التخصصى | NON_WORK/IGNORE | - | - | - |
| `201200003824-1492129525@g.us` | ☠☠ | NON_WORK/IGNORE | - | - | - |
| `201111956444-1604024317@g.us` | ✖️للكبار فقط✖️ | NON_WORK/IGNORE | - | - | - |
| `201110077591-1511517398@g.us` | 🍔🍟🍕🌭🍭🍻squad | NON_WORK/IGNORE | - | - | - |
| `201015806940-1488199166@g.us` | 👬المتوحشون ‏الأربعة👫 | NON_WORK/IGNORE | - | - | - |
| `201115416665-1435271253@g.us` | 🕺🏻عمالقة القومية الثلاثة 💃🏻 | NON_WORK/IGNORE | - | - | - |
