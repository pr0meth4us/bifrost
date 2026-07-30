# Bifrost Terms of Service

**Effective:** [EFFECTIVE DATE]
**Last updated:** 2026-07-29

> Draft pending legal review, and not a binding agreement. Internal notes on
> the placeholders and open questions live in `docs/legal/README.md`.

These Terms govern your use of Bifrost, an identity, authentication and payment
platform operated by [LEGAL ENTITY NAME], registered under
[COMPANY REGISTRATION NUMBER] at [REGISTERED ADDRESS] ("Bifrost", "we", "us").

By registering an application, or by using the platform, you ("Customer", "you")
accept these Terms. If you are accepting on behalf of an organisation, you
confirm you are authorised to bind it.

---

## 1. What Bifrost provides

Bifrost is business-to-business infrastructure. You integrate it into your own
application; your users interact with your product, not with us.

The platform provides:

- **Identity and authentication** — hosted sign-in, email and password, one-time
  codes by email and SMS, Telegram sign-in, social sign-in, and an OpenID
  Connect provider with single sign-on.
- **Role and entitlement management** — per-application roles for your users.
- **Payment orchestration** — checkout and verification flows against payment
  providers, using *your* merchant credentials.
- **Configuration and secrets storage** — encrypted storage your application
  reads at runtime.
- **An administrative console** — user management, a content editor for a
  connected database, a payment review queue, and an audit log.

Not every capability is enabled for every application. What is active for yours
is shown in your console.

## 2. Your account

You must give accurate registration details and keep them current.

You are responsible for your `client_secret`, `webhook_secret`, and every
console credential. Anything done with them is treated as done by you. Tell us
at [SECURITY CONTACT EMAIL] as soon as you suspect a credential is exposed; you
can rotate secrets yourself from the console at any time.

You must enable multi-factor authentication on every console account. It is
required for all console roles without exception.

## 3. Your users are yours

The people who sign in through Bifrost are **your** users. You decide what data
to collect, why, and for how long. We hold it on your instruction.

In data-protection terms you are the **controller** and we are the
**processor**. The [Data Processing Agreement](data-processing-agreement.md)
sets out what that means, and it forms part of these Terms.

Accordingly, you are responsible for:

- Having a lawful basis for collecting and processing your users' data
- Publishing your own privacy notice to your users
- Answering your users' access, correction and deletion requests
- Getting whatever consents your jurisdiction requires

You keep all rights in your data. We claim none.

## 4. Payments

**We never hold your money.** Payment credentials — your ABA PayWay merchant ID
and API key, your Gumroad product configuration — belong to you and are stored
against your application. Funds move directly between your user and your own
merchant account.

Consequently:

- Your agreement with each payment provider is yours, and you must comply with it
- Refunds, chargebacks and payment disputes are between you and your user
- We are not a party to any transaction and are not liable for one
- Where the console shows a payment queue, it is a review tool over *your*
  records; approving a payment in it does not move money

If a payment provider suspends or terminates you, the corresponding Bifrost
functionality stops working and that is not a failure of the platform.

## 5. Acceptable use

The [Acceptable Use Policy](acceptable-use-policy.md) forms part of these Terms.
Breaching it may lead to suspension under clause 11.

## 6. Fees

Fees, billing frequency and payment terms are as agreed in writing between us,
or as published for the plan you have selected.

Unless stated otherwise: fees exclude tax; invoices are due [NOTICE PERIOD] days
from issue; and we may change fees with at least [NOTICE PERIOD] days' notice,
effective at your next renewal.

## 7. Availability and support

We aim to keep Bifrost available and will give reasonable advance notice of
planned maintenance where we can.

**Unless a separate service level agreement has been signed, the platform is
provided without an uptime commitment.** Some parts depend on third parties —
payment providers, SMS and email delivery, Telegram, cloud hosting — and their
outages are outside our control.

Support is provided at [SUPPORT CONTACT EMAIL] on a commercially reasonable
basis.

## 8. Security

We maintain the measures described in the
[Data Processing Agreement](data-processing-agreement.md), including encryption
in transit, encryption at rest for stored secrets, password hashing, mandatory
multi-factor authentication for the console, role-based access control, and an
audit log of console changes.

No system is perfectly secure. If you find a vulnerability, report it to
[SECURITY CONTACT EMAIL] and give us reasonable time to fix it before disclosing
it publicly. We will not pursue good-faith security research that does not
access other customers' data, degrade the service, or use the finding for any
purpose beyond demonstrating it.

## 9. Confidentiality

Each party will protect the other's non-public information with at least the
care it applies to its own, and use it only to perform these Terms. This does
not cover information that is public through no fault of the receiver,
independently developed, or required to be disclosed by law — in which case the
disclosing party gets notice where that is lawful.

## 10. Intellectual property

We own Bifrost, including its software, design and documentation. You get a
non-exclusive, non-transferable right to use it for the term.

You own your data, your application and your content. You grant us only the
licence needed to run the service for you.

Feedback you send us may be used freely and without obligation.

## 11. Suspension and termination

**You** may terminate at any time by closing your account.

**We** may suspend or terminate if you materially breach these Terms or the
Acceptable Use Policy, fail to pay after written notice, or use the platform in
a way that endangers it or other customers.

Except where there is a live risk to the platform, to other customers, or to
someone's safety, we will give notice and a reasonable chance to fix the problem
before suspending.

**On termination:** your access ends, and we will keep your data available for
export for [NOTICE PERIOD] days, after which it is deleted per the
[Privacy Policy](privacy-policy.md). Export your data before that window closes.

## 12. Warranties and disclaimers

Each party warrants it has the authority to enter these Terms.

**Otherwise the platform is provided "as is".** To the fullest extent the law
allows, we disclaim all implied warranties, including merchantability, fitness
for a particular purpose, and non-infringement. We do not warrant that the
platform will be uninterrupted or error-free.

Nothing here excludes liability that cannot lawfully be excluded.

## 13. Limitation of liability

To the fullest extent the law allows:

- Neither party is liable for indirect, incidental, special or consequential
  loss, or for lost profits, revenue, goodwill or anticipated savings.
- Each party's total aggregate liability arising out of these Terms is limited
  to the fees you paid in the twelve months before the event giving rise to the
  claim.

These limits do not apply to your payment obligations, either party's breach of
confidentiality, or liability that cannot lawfully be limited.

## 14. Indemnity

You will indemnify us against third-party claims arising from your use of the
platform in breach of these Terms, your content, or your relationship with your
own users — including claims by your users about how their data was handled
under your instructions.

## 15. Changes

We may change these Terms. Material changes take effect [NOTICE PERIOD] days
after we notify you by email or in the console. Continuing to use the platform
after that is acceptance. If you do not accept, terminate before the change
takes effect.

## 16. General

**Governing law.** These Terms are governed by the laws of [JURISDICTION], and
disputes are subject to the exclusive jurisdiction of [COURTS].

**Assignment.** Neither party may assign without the other's written consent,
except to a successor in a merger or sale of substantially all assets.

**Entire agreement.** These Terms, the DPA, the Acceptable Use Policy and the
Privacy Policy are the whole agreement, replacing anything said before.

**Severability.** If a provision is unenforceable, the rest survives.

**No waiver.** Not enforcing a provision does not waive it.

**Notices.** To you at your registered email; to us at [SUPPORT CONTACT EMAIL].

---

**Contact:** [LEGAL ENTITY NAME], [REGISTERED ADDRESS] — [SUPPORT CONTACT EMAIL]
