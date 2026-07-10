# Bifrost CMS & Payment Bot — Handover Acceptance Checklist

**Author:** Product Consultant
**Date:** July 2026
**Status:** Draft for review
**Related docs:**
- Bifrost Content & Operations Console — PRD, v1.3
- Multi-Tenant Payment Bot Webhook Architecture (dev team)
- Bifrost — Data Rights & Tenant Lifecycle

---

## How to use this document

This is the checklist the delivered system is checked against at handover, not a progress tracker. Every item is written to be verifiable — pass or fail, ideally by someone other than the person who built it — rather than a subjective "feels done."

Three rules for using it honestly:

1. **An unchecked item is not silently dropped.** If something isn't met at handover, it goes in the waiver table at the end with an owner and a target date — it doesn't just quietly not happen.
2. **Verify behavior, not the presence of UI.** Several items below explicitly say to check via network response, direct DB inspection, or an actual forced error — not "does it look right in the browser," because permission and logging bugs hide behind UI that looks correct while leaking data underneath.
3. **Test with real tenant shapes, not synthetic data.** Several items call out Ministry Exam Prep or Finance Bot by name deliberately — this system's whole premise is working on data shapes nobody purpose-built it for, so testing only against clean sample data defeats the point.

---

## 1. Core Journeys — CMS PRD Section 5

- [ ] **Journey 1 (Orient):** opening a table neither party has configured shows a human-readable title, an accurate row count, and a sensible default column subset — verified on at least two structurally unrelated tables (e.g., `exam_questions` and an operations-style table), with zero config-layer entries for either.
- [ ] **Journey 2 (Edit):** editing a record via the drawer produces unambiguous save confirmation; cancelling mid-edit discards changes with no partial save reaching the database.
- [ ] **Journey 3 (Create):** creating a record enforces required fields with plain-language errors (no raw type/constraint text); every relationship field is filled via the picker end-to-end, never by typing a raw ID.
- [ ] **Journey 3 fallback:** the synthesized-label fallback (PRD Journey 3) is verified specifically against Ministry Exam Prep's `subjects` table — confirm what it actually renders when `subjects` has no clean name column, not just that the fallback code path exists.
- [ ] **Journey 4 (Find):** a target record is locatable via search/filter in a reasonable number of interactions on both a small table (loaded client-side) and a large, proxy-paged table.
- [ ] **Journey 5 (Triage):** both fast-paths work — the status-pill quick-transition menu, and checkbox-select + bottom action bar — and both are demonstrably driven by the same underlying valid-transition rules (change a transition rule once, confirm both paths reflect it).

## 2. Permissions — PRD Section 8

- [ ] Global Bifrost roles (`owner`/`admin`/`user`/`guest`) correctly gate console access itself, independent of anything configured inside the CMS.
- [ ] At least one tenant-defined custom CMS role hierarchy is created and functions end-to-end (not just Bifrost's four default roles) — confirms the mechanism is generic, not hardcoded to the illustrative example.
- [ ] Table-level and field-level permission enforcement is verified **at the API/network response level**, not just by confirming the UI hides a field — a role without column access must never receive that column's value in the payload at all.
- [ ] **Explicit decision recorded, not left ambiguous:** is row-level scoping (Section 8.3) in this release? If it's out, EDCORE's instructor-scoping gap is written down as a known, accepted limitation — not discovered by EDCORE after launch.
- [ ] Platform-level locks: at least one designated table (Finance Bot's `transactions`, or equivalent) is confirmed unconditionally unreachable through the CMS, including through an attempt by the tenant's own top-level role to grant itself access.
- [ ] A platform-locked table displays the "Protected by Bifrost data policy" badge in the tenant's config panel rather than disappearing without explanation.

## 3. Mutation Logging — PRD Section 8.5

- [ ] Every create, update, and soft-delete performed through the CMS produces a log entry containing actor, timestamp, and a before/after delta — confirmed by direct inspection of the log store, not by trusting the write path's code alone.
- [ ] Confirmed logging works with **no history/audit UI present** — the requirement is the data existing, independent of whether anyone can see it yet.

## 4. Scale — PRD Section 9

- [ ] The actual current proxy per-fetch cap is confirmed against the value assumed in the PRD (500 rows at time of writing) and the client-side/server-side UI threshold is verified to be driven by that real, current number — not a separately hardcoded guess that can silently drift out of sync with the backend.
- [ ] A large, paged table shows an accurate "Showing X of Y" count at all times, never a silently truncated list presented as complete.

## 5. Media & Internationalization — PRD Sections 6.5, 6.6

- [ ] Text/textarea fields render Khmer script correctly (Kantumruy Pro or Noto Sans Khmer fallback active, no clipped glyphs or diacritics) — tested against **real Ministry Exam Prep content**, not placeholder Latin text.
- [ ] Image-shaped URL/text columns render as inline thumbnails with click-to-expand — verified on both a question-diagram field and a Payment Bot receipt field, confirming the one shared implementation actually serves both cases.
- [ ] Image/attachment previews respect field-level permission rules — a role without access to a column gets no image preview from it either, confirmed at the network layer per Section 2 above.

## 6. Payment Bot Webhook Integration — PRD Section 14

- [ ] Webhook route is live and confirmed receiving real Telegram updates for at least one test tenant bot token.
- [ ] `client_id` resolution is confirmed to map to the exact same tenant identity the CMS's Postgres proxy uses — a payment inserted via the bot appears in that tenant's CMS queue immediately, with no manual sync or reconciliation step required.
- [ ] The dev team's own end-to-end manual verification plan (`/start` → photo upload → webhook hit → client_id resolved → row inserted into tenant Postgres) is completed and explicitly signed off, not just assumed passing because the code merged.
- [ ] Payment appears correctly in whichever surface is the reviewer's actual workflow at handover time — the CMS queue, Valhalla Portal, or both, per whatever the replace-vs-coexist decision (PRD Section 13) resolved to.
- [ ] The two engineering decisions the dev team flagged for review (webhook path structure; retaining `run_polling()` for local dev only) are confirmed resolved, one way or another, before handover — not left as an open question in a shipped system.

## 7. Data Rights & Tenant Lifecycle — companion doc

- [ ] Finance Bot's self-service deletion action ships and executes directly against Mongo from the bot's own backend — confirmed no staff-mediated deletion path exists or is needed.
- [ ] Deletion tiering verified against an actual test user record: immediate-purge fields are gone; pseudonymize-and-retain fields (`transactions`, etc.) have their identifying link severed while the record shell persists.
- [ ] Tenant offboarding path (deactivate → grace window → purge) is at minimum manually triggerable and produces the correct tiered result, even if a self-serve UI for it isn't part of this release.
- [ ] Bifrost-user (tenant staff) mutation-log `actor` pseudonymization on account deletion is verified against a real deleted test account, not just described in the plan.

## 8. Cross-Cutting / Non-Functional

- [ ] Mobile: the list/queue view and the record drawer (rendered as a full-screen sheet) are verified on an actual phone-width viewport — not a resized desktop browser window.
- [ ] Empty state verified on at least one genuinely zero-row table — confirmed it reads as an invitation with a clear create action, not a bare "no results" or blank screen.
- [ ] Error states verified by deliberately forcing three distinct failures — a validation error, a server-side constraint violation, and a network timeout — and confirming each produces a plain-language message, never a raw stack trace or database error string.
- [ ] Zero-config experience verified on a table with no Mongo config-layer entries at all, confirmed to render sensibly — treated as the default path being tested, not a fallback edge case being tolerated.

---

## Waivers (items not met at handover)

Any checklist item not satisfied at the handover date goes here — it does not simply remain unchecked with no owner.

| Item # / description | Why not met | Owner | Target resolution date |
|---|---|---|---|
| | | | |

---

## Sign-off

| Reviewer | Section(s) reviewed | Approved / Approved-with-waivers | Date |
|---|---|---|---|
| | | | |
| | | | |
| | | | |

Handover is considered complete only once every section above has either an approving signature or a corresponding entry in the waiver table — not both silence and an assumed pass.
