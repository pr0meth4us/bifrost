# Bifrost Privacy Policy

**Effective:** [EFFECTIVE DATE]
**Last updated:** 2026-07-29

> Draft pending legal review, and not a binding agreement. Internal notes on
> the placeholders and open questions live in `docs/legal/README.md`.

This policy explains what personal data [LEGAL ENTITY NAME] ("Bifrost", "we")
handles, why, and what rights people have over it.

---

## 1. Read this first: two different roles

Bifrost handles personal data in two distinct capacities, and which one applies
changes who you should contact.

**As a processor — most of the data.** When someone signs in to a customer's
application through Bifrost, we hold their data *on that customer's
instruction*. The customer decides what is collected and why. We do not use it
for our own purposes.

> **If you are an end user of an application that uses Bifrost, this policy is
> not the one that governs your data.** Read the privacy notice of the app you
> signed up to, and send access or deletion requests there. They can act on your
> data directly; we can only act on their instruction.

**As a controller — a smaller set.** For our own customers — the businesses that
register applications — we decide the purposes ourselves: account
administration, billing, support, security.

## 2. Data we handle as a processor

Held on behalf of a customer, for the users of their application:

| Category | Fields | Why |
|---|---|---|
| Identifiers | Email, username, phone number, Telegram ID, social provider IDs | To identify a returning user |
| Credentials | Password hash (never the password), one-time codes | To authenticate |
| Profile | Display name, avatar URL where supplied | To personalise the customer's app |
| Access | Role and entitlements per application, expiry dates | To tell the app what a user may do |
| Session | OpenID Connect authorization codes, refresh tokens, sign-in session | To keep a user signed in |
| Transactions | Amount, currency, reference, status, timestamps | To record a purchase |
| Technical | IP address and timestamps in security logs | To detect abuse |

**Passwords are never stored.** Only a salted hash, from which the password
cannot be recovered.

Where a customer connects their own database, its contents stay in *their*
database. We do not copy it.

## 3. Data we handle as a controller

For our customers' own staff:

- **Account** — name, work email, password hash, MFA status, trusted-device tokens
- **Billing** — company details, plan, invoices and payment status
- **Usage** — API call volume, feature usage, error rates
- **Support** — what you send us when you ask for help
- **Audit** — which console user changed what, and when

## 4. Why we process it, and on what basis

**As a processor:** on the customer's documented instruction, under the
[Data Processing Agreement](data-processing-agreement.md). Their lawful basis
covers it; we do not establish our own.

**As a controller:**

| Purpose | Basis |
|---|---|
| Providing the platform | Performance of a contract |
| Billing and collections | Performance of a contract |
| Security, fraud and abuse prevention | Legitimate interests |
| Product improvement from aggregate usage | Legitimate interests |
| Legal and accounting obligations | Legal obligation |
| Marketing about our own services | Consent, withdrawable at any time |

**We do not sell personal data. We do not use it to train machine-learning
models. We do not build advertising profiles.**

## 5. Cookies and similar technology

Bifrost sets only what it needs to function:

| Cookie | Purpose | Lifetime |
|---|---|---|
| Session cookie | Keeps you signed in; carries the single sign-on session | Session, max 12 hours for SSO |
| `bo_trusted_device` | Remembers a console device so MFA is not re-prompted every session | 30 days |
| CSRF token | Blocks cross-site request forgery | Session |

All are strictly necessary. **We set no analytics, advertising or third-party
tracking cookies**, which is why there is no cookie banner: there is nothing
optional to consent to.

Console sessions are signed, `HttpOnly`, `SameSite=Lax` and — outside local
development — `Secure`.

## 6. Who we share it with

**Subprocessors.** Named individually in [subprocessors.md](subprocessors.md),
with what each one receives. Each is bound by contract to protect the data and
use it only to provide their service.

**The customer.** As processor, we make data available to the customer whose
users it belongs to. That is the point of the arrangement.

**Legal.** Where required by law, court order or a valid authority request. We
will tell the affected customer unless legally forbidden.

**Corporate transactions.** In a merger or sale, data may transfer. Notice will
be given, and this policy continues to apply until replaced.

**We do not share personal data for anyone else's marketing.**

## 7. How long we keep it

These are the periods the system actually enforces, not aspirations:

| Data | Retained |
|---|---|
| One-time verification codes | 10 minutes, then deleted automatically |
| OIDC authorization codes | 1 hour (kept past expiry so replay is detectable) |
| OIDC refresh tokens | 30 days, or until revoked or rotated |
| Single sign-on session | 12 hours |
| Trusted-device cookie | 30 days |
| End-user accounts | Until the customer deletes them, or [NOTICE PERIOD] days after their account closes |
| Transaction records | As required for accounting in [JURISDICTION] |
| Console audit log | Retained for security and dispute resolution |
| Support correspondence | 24 months from resolution |

Deleting an account also revokes its refresh tokens and authorization codes.

## 8. International transfers

Our infrastructure providers operate in multiple countries, so data may be
processed outside [JURISDICTION]. Where it is transferred out of a region whose
law restricts that, we rely on the safeguards described in the DPA, including
standard contractual clauses where they apply.

## 9. Your rights

Depending on where you live, you may have the right to access your data, correct
it, delete it, restrict or object to processing, receive it in a portable
format, and withdraw consent.

**How to exercise them:**

- **End user of an application** — contact that application. They control your
  data. If you contact us, we will point you to them; we cannot act without
  their instruction.
- **Our direct customer** — email [PRIVACY CONTACT EMAIL].

We respond within 30 days. We may need to verify identity before acting.

You may also complain to the data protection authority in your country.

## 10. Security

- TLS in transit
- Passwords salted and hashed; plaintext never stored
- Stored secrets and tenant database credentials encrypted at rest
- Mandatory MFA for every console account, every role
- Role-based access control enforced server-side on every route
- Console sessions: 30-minute idle timeout, 8-hour maximum
- Per-tenant isolation of user directories and databases
- Audit logging of console changes, with before-and-after values
- Rate limiting on authentication endpoints

**Access by our staff:** for customers classified as external tenants, our
platform administrators are limited to configuration, aggregate metrics and the
audit log. They cannot read stored secrets, end-user records, content or payment
details. Where deeper access is genuinely needed, the customer grants it
themselves, and can revoke it.

## 11. Children

Bifrost is not directed at children and we do not knowingly collect their data
as a controller. As a processor, whether an application serves children is the
customer's responsibility, along with any consent their law requires. Tell us at
[PRIVACY CONTACT EMAIL] if you believe a child's data has reached us in error.

## 12. Breach notification

If a breach affects personal data we process, we notify the affected customer
without undue delay and no later than 72 hours after becoming aware, with what
we know: what happened, which data, likely consequences, and what we are doing.

Where we are the controller, we notify affected individuals and regulators as
the law requires.

## 13. Changes

We may update this policy. Material changes are notified by email or in the
console at least [NOTICE PERIOD] days before taking effect. The date at the top
always reflects the current version.

## 14. Contact

**Privacy:** [PRIVACY CONTACT EMAIL]
**Security:** [SECURITY CONTACT EMAIL]
**Post:** [LEGAL ENTITY NAME], [REGISTERED ADDRESS]
