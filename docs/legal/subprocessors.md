# Subprocessors

**Last updated:** 2026-07-29

> Draft pending legal review, and not a binding agreement. Internal notes on
> the placeholders and open questions live in `docs/legal/README.md`. Verify each entity name,
> location and role against your actual contracts before publishing — this list
> is derived from the code, which shows what the platform *calls*, not which
> corporate entity you contracted with.

Third parties that may process personal data on our behalf, under the
[Data Processing Agreement](data-processing-agreement.md). We give at least
[NOTICE PERIOD] days' notice before adding or replacing one.

---

## Core infrastructure

Used for every customer.

| Subprocessor | Purpose | Data it receives | Location |
|---|---|---|---|
| **MongoDB Atlas** (MongoDB, Inc.) | Primary database | All stored identity, role and transaction data | [VERIFY REGION] |
| **Koyeb** | Application hosting | All data in transit through the application | [VERIFY REGION] |
| **Redis** (managed) | Caching and rate limiting | Transient session and rate-limit keys | [VERIFY REGION] |

## Communication

Used where the corresponding feature is enabled for a customer's application.

| Subprocessor | Purpose | Data it receives | Location |
|---|---|---|---|
| **Google (Gmail SMTP)** | Transactional email — one-time codes, invitations, password resets | Recipient email address, message content | Global |
| **Twilio** | SMS one-time codes | Recipient phone number, message content | Global |
| **Telegram** (Telegram FZ-LLC) | Telegram sign-in and bot messaging | Telegram user ID, message content | Global |

## Payments

Merchant credentials belong to each customer. Funds move between the end user
and the customer's own merchant account; we do not hold them.

| Subprocessor | Purpose | Data it receives | Location |
|---|---|---|---|
| **ABA Bank (PayWay)** | Payment processing, Cambodia | Transaction amount, reference, payer details supplied at checkout | Cambodia |
| **Gumroad** | Payment processing, international | Purchaser email, transaction details | United States |

## Identity providers

Engaged only when an end user chooses that sign-in method. Each receives only
what the user's own authentication requires.

| Subprocessor | Purpose | Location |
|---|---|---|
| **Google** | Google sign-in | Global |
| **GitHub** (Microsoft) | GitHub sign-in | Global |
| **Microsoft** | Microsoft sign-in | Global |
| **Apple** | Apple sign-in | Global |
| **Meta** | Facebook sign-in | Global |

> These act as identity providers rather than as our subprocessors in the strict
> sense — the user authenticates directly with them and they return an identity
> assertion to us. Listed for transparency. Confirm the correct characterisation
> with counsel.

## Optional services

Engaged only for customers using the relevant feature.

| Subprocessor | Purpose | Data it receives | Location |
|---|---|---|---|
| **Google Cloud (Vertex AI, Vision API)** | AI and OCR features | Content submitted for processing | [VERIFY REGION] |
| **Supabase** or customer-supplied PostgreSQL | Tenant database hosting | Whatever the customer stores | Customer's choice |

## Customer-supplied infrastructure

Where a customer connects their own database, that database is **not** our
subprocessor. It is the customer's own infrastructure, under their control and
their agreements. We connect to it on their instruction and store nothing from
it.

---

## Changes

Material changes to this list are notified by email or in the console at least
[NOTICE PERIOD] days before taking effect. Customers may object on reasonable
data protection grounds — see clause 7 of the DPA.

**Contact:** [PRIVACY CONTACT EMAIL]
