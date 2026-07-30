# Bifrost documentation

Bifrost is multi-tenant identity, authentication and payment infrastructure. One
deployment serves many applications; each gets its own users, roles, branding,
payment credentials and database.

The API reference and integration guide are served live at **`/docs`**. This
directory holds everything that is better read as a file than as a web page.

---

## Start here

| If you want to… | Read |
|---|---|
| Understand how the system is built | [reference/WHITE_PAPER.md](reference/WHITE_PAPER.md) |
| Integrate an application with Bifrost | [guides/client_adoption.md](guides/client_adoption.md) |
| Run the admin console day to day | [guides/console-onboarding.md](guides/console-onboarding.md) |
| Work on Bifrost itself | [guides/dev_guide.md](guides/dev_guide.md) |
| Know what shipped and when | [../CHANGELOG.md](../CHANGELOG.md) |

## Layout

```
docs/
├── guides/      how to do things — integrators, operators, developers
├── reference/   how it works — architecture and design
├── legal/       terms, privacy, DPA, acceptable use, subprocessors
└── project/     scope responses and open work, tied to a specific engagement
```

The split is by *reason to read*, not by topic. A guide answers "how do I…", a
reference answers "how does it…", legal binds someone to something, and project
documents are dated artefacts of one engagement rather than living
documentation.

### guides/

| Document | For |
|---|---|
| [client_adoption.md](guides/client_adoption.md) | Integrating an app: registration, tokens, callbacks |
| [console-onboarding.md](guides/console-onboarding.md) | Operators: the console, payment queue, CMS |
| [dev_guide.md](guides/dev_guide.md) | Contributors: local setup, conventions |
| [testing_pipeline.md](guides/testing_pipeline.md) | How the test suite is structured and run |

### reference/

| Document | Covers |
|---|---|
| [WHITE_PAPER.md](reference/WHITE_PAPER.md) | System topology, data model, encryption, multi-tenant routing |

### legal/

Drafts, not executed agreements — see [legal/README.md](legal/README.md) for the
placeholders to fill and the questions for counsel.

| Document | Binds |
|---|---|
| [terms-of-service.md](legal/terms-of-service.md) | You ↔ tenant |
| [privacy-policy.md](legal/privacy-policy.md) | You ↔ everyone |
| [data-processing-agreement.md](legal/data-processing-agreement.md) | You ↔ tenant, for their users' data |
| [acceptable-use-policy.md](legal/acceptable-use-policy.md) | You ↔ tenant |
| [subprocessors.md](legal/subprocessors.md) | Disclosure |

### project/

Point-in-time documents. Useful as a record; not maintained as the system
changes.

| Document | Context |
|---|---|
| [scope-response-admin-console.md](project/scope-response-admin-console.md) | Gap analysis and vendor reply, admin console |
| [scope-response-sql-studio.md](project/scope-response-sql-studio.md) | Gap analysis and vendor reply, SQL studio |
| [todo-generic-payment-queue.md](project/todo-generic-payment-queue.md) | Open work on the payment queue |

---

## Operational scripts

Database migrations live in [`../scripts/`](../scripts/) and are numbered in the
order they must run. Every one is **dry-run by default** and takes `--apply` to
write.

| Script | Does |
|---|---|
| `002_pin_managed_schemas.py` | Pins managed tenants to their own Postgres schema |
| `003_backfill_account_directory.py` | Assigns a directory to accounts created before per-tenant scoping |
| `004_classify_tenants.py` | Marks each application internal or external |
| `005_drop_legacy_global_indexes.py` | Drops platform-wide unique indexes superseded by per-tenant ones |

## Conventions

**Changelog.** [Keep a Changelog](https://keepachangelog.com/), newest first,
`[Unreleased]` directly below the header note. The version banner on `/docs` is
parsed from the first `## [X.Y.Z] - YYYY-MM-DD` header, so an entry without that
exact shape is invisible to the product.

**Migrations.** Numbered, idempotent, dry-run by default. A migration that
cannot safely proceed reports why and changes nothing rather than guessing.

**Tests.** `.venv/bin/python -m pytest tests/ -q`. Tests that build the full
application connect to the configured database and take a few minutes; the
`mongomock`-backed suites (`test_oidc.py`, `test_platform_scope.py`,
`test_cms_mongo.py`) run in seconds and cover the security-critical paths.
