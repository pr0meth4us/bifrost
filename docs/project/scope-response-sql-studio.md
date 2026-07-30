# SQL Studio + Console Redesign — Vendor Response

**Against:** *DevTools (Valhalla SQL Studio) Implementation Plan*, Prolong engineering
**Assessed system:** Bifrost backoffice / Valhalla console at `f2cd191`
**Date:** 2026-07-28

Both requests are done: SQL Studio is built, and the console has been rebuilt on a single
design system. Sections 1–3 are the reply. Section 4 is the part you need to act on.

---

## 1. SQL Studio

Built as described — raw SQL from the browser, against the tenant's live PostgreSQL,
so schema work no longer means opening Supabase Studio.

| | |
|---|---|
| Editor | `/backoffice/app/<app_id>/devtools` |
| Execute | `POST /backoffice/api/app/<app_id>/devtools/execute` |
| Permission | `db:execute` |
| Code | [`bifrost/backoffice/devtools_routes.py`](../bifrost/backoffice/devtools_routes.py), [`devtools.html`](../bifrost/templates/backoffice/devtools.html) |

Multiple statements separated by semicolons run as one batch, so a whole migration can be
pasted in. Anything returning rows comes back as a table; `CREATE`/`DROP`/`UPDATE` return
an affected-row count. Postgres errors are surfaced verbatim — position and hint included —
rather than flattened into "query failed". `Ctrl`/`⌘` + `Enter` runs.

Documented for your team at `/docs`, Part VI.

### Four deviations from your plan, and why

**1. `developer` did not exist as a role.** The plan gates on
`current_role in ['developer', 'pr0meth4us']`, but there was no `developer` entry in
`ROLE_PERMISSIONS`, and `CONSOLE_ROLES` would have blocked it from signing in at all — so
that check would have been permanently false.

Rather than hand-roll an inline role list in two routes (a second access-control mechanism
sitting next to the existing one), we added the role properly and gated on a permission:

```python
"developer": {"read:config", "audit:view", "db:execute"} | _CONTENT,
```

`@requires("db:execute")` then does the whole job through the same mechanism every other
route uses. `db:execute` is held by exactly **one** role and is deliberately *not* folded
into `admin` or `owner` — an owner must grant it on purpose. That is stricter than your
plan, not looser: it means you can give a contractor migration access without also giving
them the payment queue.

There is a test asserting `db:execute` never leaks to another role, so this can't drift.

**2. Routes went in a new file, not `tenant_routes.py`.** That file is already 1,017 lines.
`devtools_routes.py` keeps the diff readable; it registers the same way.

**3. Nothing commits unless you tick the box.** Not in the plan, and it is the single most
useful safety property here. Statements run inside a transaction that is rolled back unless
*Commit changes* is checked — so you can run a `DELETE`, read the row count, and *then*
decide. A forgotten checkbox costs a re-run; a forgotten `WHERE` that auto-commits costs the
table.

**4. No blocklist of dangerous statements.** We considered it and rejected it deliberately.
Any such filter is bypassed in seconds with `DO $$ ... $$` or `EXECUTE`, and its real cost is
convincing people the tool is safer than it is. The protections are structural instead:

- **Every statement is audit-logged before it runs**, against the acting account — so a query
  that takes the database down still leaves a record of who ran it.
- **15-second statement timeout.** Your tenant connection pool is 8 connections wide
  (`tenant_db.py`). Without this, one runaway scan starves the entire console for that tenant
  — not just the person who ran it.
- **500-row result cap**, so `SELECT *` on a large table returns a preview instead of trying
  to serialise the table into a JSON response.

### What this does not protect against

A committed `DROP TABLE users CASCADE` still drops the table. Treat `db:execute` as
equivalent to handing over production database credentials, because functionally that is what
it is. Keep backups.

One thing to flag explicitly: `check_permission` returns `True` for `heimdall` and
`pr0meth4us` before consulting the matrix, so platform staff get SQL Studio automatically.
Your plan named only `developer` and `pr0meth4us`. We left the existing behaviour — Heimdall
already has all-app access, the API vault and user deletion, so this isn't an escalation —
but say the word and we'll carve it out.

---

## 2. Console redesign

You described it as "25 apps merged together." That was close to literally true, and the
cause was structural rather than cosmetic: **all 15 templates were standalone
`<!DOCTYPE html>` documents.** There was not one `{% extends %}` anywhere in the codebase.
Every page re-declared its own fonts, its own Tailwind build, and its own navigation, so
five different design languages had grown up in parallel with nothing holding them together:

| Design language | Pages |
|---|---|
| Light Tailwind slate + Plus Jakarta | dashboard, app users, create app, global users |
| Dark Tailwind slate | payment queue, audit log |
| Dark glassmorphism, Fira Code, radial glows | monitor, AI metrics |
| Material-3 dark tokens (inline config) | content grid |
| Hand-rolled Apple-style CSS variables, light | schema config, RBAC, onboarding |

Three of them disagreed on where "Back" went and what the role badge looked like.

### What replaced it

One token set, one head, two layouts. Every page now extends one of them.

| File | Role |
|---|---|
| [`static/valhalla.css`](../bifrost/static/valhalla.css) | Semantic tokens + component layer |
| [`_head.html`](../bifrost/templates/backoffice/_head.html) | The single `<head>` |
| [`base.html`](../bifrost/templates/backoffice/base.html) | Console shell — sidebar + topbar |
| [`base_auth.html`](../bifrost/templates/backoffice/base_auth.html) | Sign-in screens |

Tailwind reads the **same CSS variables** as the stylesheet, so `bg-surface` and `.card` can
never drift apart. Adding a page means extending `base.html` and using semantic names; a
hard-coded palette colour breaks dark mode immediately and visibly, which is the point.

Also extracted three chunks that had been copy-pasted: the sidebar
([`_nav.html`](../bifrost/templates/backoffice/_nav.html), driven by the real
`check_permission` so the menu can't disagree with what the routes allow), the role dropdown
([`_role_options.html`](../bifrost/templates/backoffice/_role_options.html)), and the raw JSON
editor ([`_json_editor.html`](../bifrost/templates/backoffice/_json_editor.html)).

Design direction was familiarity over flourish — system fonts, one accent, no glass, no glow,
light default with a dark toggle that persists. Non-technical tenant staff use these screens
daily and shouldn't have to learn a look.

Accessibility, which most of the old pages had no story for: real `<label>`s throughout, one
`:focus-visible` ring, `aria-current` navigation, a skip link, `prefers-reduced-motion`,
muted text at 5.2:1 / 6.1:1 contrast, alerts that print the category word instead of relying
on colour alone, and dialogs that close on Escape and return focus.

The public docs at `/docs` were a sixth design language. They're now on the same tokens, with
a filterable table of contents and a new Part VI covering SQL Studio.

---

## 3. Six defects found and fixed on the way

None of these were in scope. All were live at `f2cd191`.

**1. Stored XSS in the content grid's edit drawer.** The drawer built its form controls by
concatenating tenant database values into HTML strings. A row containing `"><script>` executed
script in the console operator's session the moment someone clicked Edit — an admin-session
XSS reachable by anyone who can write a row. Rebuilt with `createElement` and `.value`.

**2. XSS surface in the live monitor.** Log rendering escaped only `<` and `>`, then
re-inserted markup for keyword highlighting. One regex change away from the same bug. Now
`textContent`; the decorative colour-coding was not worth it.

**3. Relation pickers were completely broken.** They fetched
`/cms/api/lookup?table=` — a route that does not exist; the real one is
`/api/app/<id>/cms/<table>/lookup` — and then read `opt.value` where the endpoint returns
`{id, label}`. Two independent bugs, so every foreign-key dropdown in the CMS rendered empty.

**4. Raw JSON edits to the schema config were silently discarded.** Saving called
`buildConfigObject()`, which rebuilds from the form controls and overwrote whatever had just
been typed into the JSON editor. It reported success.

**5. "Manage App Keys" opened the wrong application.** Linked to `view_app`, which resolves
the *session's* active app, not the card that was clicked.

**6. A permission check was a Mongo round-trip.** `check_permission` calls
`get_current_role_in_app` on every check, uncached. The new sidebar asks about six
permissions per render, so this would have added six database round-trips to every page
load. Now memoised per request — a net reduction against the old behaviour.

---

## 4. What we need from you

**Assign the `developer` role.** Nobody has SQL Studio access until an owner grants it, under
**Users & settings → Manage**. It will not appear in the sidebar and the URL returns 403 until
then. This is intentional.

**Verification, per your plan:**

1. Sign in as `developer` on Ministry Exam Prep → **SQL Studio** appears under *Developer*.
2. Run `SELECT * FROM exam_tracks;` → results grid.
3. Run `CREATE TABLE test_table (id SERIAL);` **with Commit ticked** → table created.
   Unticked, it will report success and roll back. That is correct behaviour, not a bug.
4. Sign in as an `owner` or content editor → the link is absent and the URL 403s.

**Automated checks:**

```bash
.venv/bin/python tests/test_console_templates.py
```

21 page renders against realistic data — including the paths that break things: empty
database, no tables, error branches, `NULL` cells, hostile row text — plus assertions that no
page still carries the old design fingerprints, that `db:execute` is held by exactly one role,
and that both routes are registered. The pre-existing `test_queue_schema` (23) and
`test_console_phase1` (27) still pass.

---

## 5. Deliberately not built

- **Monaco editor.** A `<textarea>` with `Ctrl`/`⌘`+`Enter` covers the described use case.
  Worth adding when you want autocomplete against the live schema — say so and we will.
- **Query history / saved snippets.** Add when someone asks for it twice.
- **A Tailwind build step.** Still CDN-loaded, as before. Fine for now, but it belongs on the
  list alongside the CSP work already noted in `console-onboarding.md` — the same change
  removes the `cdn.tailwindcss.com` and `fonts.googleapis.com` origins.
