# Company Job Templates (masked examples)

These examples use fabricated placeholder data (fake names, fake numbers, fake addresses) to illustrate the *structure* each source uses. They are not real customer records. Use these as the reference shape for the parser's detection and extraction logic.

## Main Leads

```
Power By Ucallz App

<Business Name> Locksmith Pro
Ticket: XXXX-YYYY
Name: <First Name>
Phone: (555) 010-2000
2nd Phone #:
Address: <City>, <ST> <ZIP>
Desc: <Service Type>
<Vehicle Year Make Model>

<Freeform note about the customer's situation>
```

Notes:
- Detect via: "Power By Ucallz App" header line and/or a `Ticket:` field.
- `Name:` is a first name only in observed samples.
- Vehicle info appears as its own line after `Desc:`, not inside a labeled field.
- Real customer phone number — no extension involved.
- No price field.

## AGN

```
Ref: AGN Locksmith Services
PDL: XXXXX
Ph: 1-555-010-3000 (Job Ext: 123)
Addr: <Street>, <City>, <ST>, <ZIP>
Desc: <Service Type> [<internal code>]
Occu: Locksmith

Notes:
```

Notes:
- Detect via: `Ref: AGN Locksmith Services` and/or a `PDL:` field.
- No customer name field.
- Phone is often a company routing number plus `(Job Ext: NNN)` — pass both through unchanged, never invent an extension if one isn't given.
- Vehicle info, when present, is embedded inside `Desc:` or `Notes:` (e.g. "Car Lockout - Honda Civic 2020"), not a separate field.
- No price field.

## Lucky Locksmith LLC

```
Confirm: <tracking-link>

Co: LUCKY LOCKSMITH LLC
PDL: XXXXX
Ph: 5550104000
Addr: <Street>, <City>, <ST>, <ZIP>
Desc: <Service Type>
Occu: Locksmith

Notes:
```

Notes:
- Detect via: `Co: LUCKY LOCKSMITH LLC` and/or a `Confirm:` tracking link plus `PDL:` field.
- Same shape as AGN. Extension is not always present — when absent, do not invent one.
- No price field.

## Lucky closing example (structure)

```
Confirm: <tracking-link>

Co: LUCKY LOCKSMITH LLC
PDL: XXXXX
Ph: 5550104000
Addr: <Street>, <City>, <ST>, <ZIP>
Desc: <Service Type>

Notes:

Notes: <Vehicle Year Make Model>

Closed
$<amount> CC
$<amount> parts
```

- "CC" = credit card. Closing amounts must be copied exactly as the technician reports them — never recalculated or invented.
- AGN's closing template has not been confirmed with a real example yet — treat as a separate, unconfirmed template (see open items in `docs/phase1_scope.md`).

## Detection priority

When multiple signals are present, use this priority order (most specific first) to avoid misclassifying a job:
1. Exact source header/company line (`Ref: AGN Locksmith Services`, `Co: LUCKY LOCKSMITH LLC`, `Power By Ucallz App`).
2. Field-name fingerprint (`Ticket:` → Main Leads; `PDL:` + `Ref:` → AGN; `PDL:` + `Co:` → Lucky).
3. If no known fingerprint matches confidently, classify as **Unknown** and route to Handle Manually rather than guessing.
