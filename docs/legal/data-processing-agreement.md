# Data Processing Agreement

**Effective:** [EFFECTIVE DATE]
**Last updated:** 2026-07-29

> Draft pending legal review, and not a binding agreement. Internal notes on
> the placeholders and open questions live in `docs/legal/README.md`.

This Agreement ("DPA") forms part of the
[Terms of Service](terms-of-service.md) between [LEGAL ENTITY NAME]
("Processor", "we") and the customer ("Controller", "you"). Where the two
conflict on the processing of personal data, this DPA wins.

---

## 1. Roles

You are the **Controller**. Your users' personal data is collected for your
purposes, under your lawful basis, subject to your privacy notice.

We are the **Processor**. We process it only on your documented instructions.

Using the platform as configured is a documented instruction. So is anything you
do through the console or the API.

Where we process your own staff's account and billing data, we act as Controller
and the [Privacy Policy](privacy-policy.md) governs instead.

## 2. Subject matter and duration

**Subject matter.** Providing identity, authentication, role management, payment
orchestration and administrative tooling.

**Duration.** The term of the Terms of Service, plus the deletion window in
clause 10.

## 3. Categories of data subject

- End users of your application
- Your staff who use the administrative console

## 4. Categories of personal data

| Category | Detail |
|---|---|
| Identifiers | Email, username, phone number, Telegram ID, social provider IDs |
| Authentication | Password hashes, one-time codes, session and refresh tokens, MFA state |
| Profile | Display name, avatar URL |
| Authorisation | Roles, entitlements, expiry dates |
| Transactional | Payment amounts, currency, references, status, receipt URLs |
| Technical | IP addresses, timestamps, user agents in security logs |

**Special categories.** The platform is not designed for health, biometric,
genetic, racial, political, religious or sexual-orientation data. Do not route
such data through it without a written agreement first — the safeguards those
categories require are not built in.

## 5. Our obligations

We will:

1. Process personal data only on your documented instructions, including for
   transfers, unless required otherwise by law — in which case we tell you
   first, where that is lawful.
2. Tell you if an instruction appears to breach applicable data protection law.
3. Bind everyone with access to confidentiality.
4. Implement the measures in clause 6.
5. Engage subprocessors only under clause 7.
6. Assist you, as far as is reasonable, in responding to data subject requests
   (clause 8).
7. Assist you with security, breach notification, impact assessments and prior
   consultation, taking account of what we know and can do.
8. Delete or return personal data at the end of the term (clause 10).
9. Make available the information needed to demonstrate compliance, and allow
   audits under clause 11.

## 6. Security measures

Implemented, not aspirational:

**Access control**
- Multi-factor authentication mandatory for every console account and role
- Role-based access control enforced server-side on every route, not by hiding UI
- Console sessions: 30-minute idle timeout, 8-hour absolute maximum
- Rate limiting on authentication endpoints
- Platform staff access to external tenants limited to configuration, aggregate
  metrics and the audit log — not secrets, end-user records, content or payments

**Encryption**
- TLS in transit
- Passwords salted and hashed; plaintext never stored, never recoverable
- Stored secrets and tenant database credentials encrypted at rest
- OpenID Connect ID tokens signed RS256 with a persisted key; refresh tokens
  stored only as hashes

**Isolation**
- Each tenant has its own account directory; a lookup in one cannot return
  another's user
- Database-level uniqueness constraints are scoped per tenant
- Tenants using our managed database receive their own schema
- Connection pools are keyed per tenant, so one tenant's connection cannot be
  handed to another
- A single sign-on session is valid only within the directory it was created in

**Integrity and accountability**
- Console changes are audit-logged with actor, target and before/after values
- Authorization codes are single-use, with replay detected and derived tokens
  revoked
- Tables a tenant must not edit can be locked platform-side, enforced on write

**Resilience**
- Managed database infrastructure with provider-level backups and replication

We may change these measures, but not in a way that materially reduces security.

## 7. Subprocessors

You give general authorisation for the subprocessors listed in
[subprocessors.md](subprocessors.md).

We will give at least [NOTICE PERIOD] days' notice before adding or replacing
one. You may object on reasonable data protection grounds within that period; if
we cannot resolve it, you may terminate the affected service without penalty.

Each subprocessor is bound by written terms no less protective than this DPA. We
remain fully liable to you for their performance.

## 8. Data subject requests

Your users should contact **you**. You hold the relationship and the console
tools to act — you can view, correct, export and delete your users' records
directly.

If a request reaches us, we will not respond substantively. We will tell you
without undue delay and, where you need it, assist you technically.

## 9. Personal data breach

We will notify you **without undue delay, and no later than 72 hours** after
becoming aware of a breach affecting your personal data, giving:

- The nature of the breach, and the categories and approximate number of records
- Likely consequences
- Measures taken or proposed
- A contact point for more information

Where we do not have everything at once, we will send it in phases rather than
delay the first notification.

Notifying your regulator and your users is your responsibility as Controller.

## 10. Deletion and return

On termination, and at your choice:

- **Export.** Personal data remains available for export for [NOTICE PERIOD]
  days after termination.
- **Deletion.** After that window we delete it, including from backups on the
  normal backup rotation.

We may keep what law requires us to keep, for as long as it requires, subject to
continued confidentiality.

## 11. Audit

On reasonable notice and no more than once a year — or after a breach affecting
your data — we will provide the information reasonably needed to demonstrate
compliance.

Where that is not enough, you may audit, or appoint an independent auditor
(not a competitor of ours), subject to reasonable confidentiality terms, during
business hours, without disrupting our operations or exposing another customer's
data.

## 12. International transfers

Subprocessors operate in multiple countries. Where personal data leaves a region
whose law restricts transfers, we rely on an adequacy decision where one exists,
or standard contractual clauses, which are incorporated here by reference and
prevail over this DPA on conflict.

## 13. Liability

Liability under this DPA is subject to the limits in the Terms of Service,
except where applicable law does not permit that.

## 14. Governing law

The law of [JURISDICTION], subject to any mandatory provision of the data
protection law applying to a given data subject.

---

## Annex — processing at a glance

| | |
|---|---|
| **Subject matter** | Identity, authentication, role management, payment orchestration |
| **Duration** | Term of the Terms of Service plus the deletion window |
| **Nature and purpose** | Authenticating end users; storing identifiers, credentials and roles; recording transactions; providing administrative tooling |
| **Data types** | See clause 4 |
| **Data subjects** | End users of the Controller's application; the Controller's console staff |
| **Obligations** | See clauses 5 and 6 |
