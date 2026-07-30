# Legal & compliance documents

> **These are drafts, not executed agreements.** They are written against what
> Bifrost actually does — checked line by line against the code, not copied from
> a generic SaaS template — but they have not been reviewed by a lawyer. Every
> `[BRACKETED]` value is a placeholder that must be filled in, and the whole set
> needs review by counsel qualified in the jurisdictions you operate in before
> you put them in front of a customer.

## What's here

| Document | Who it binds | Purpose |
|---|---|---|
| [terms-of-service.md](terms-of-service.md) | You ↔ tenant | The commercial contract for using Bifrost |
| [privacy-policy.md](privacy-policy.md) | You ↔ everyone | What personal data you handle and why |
| [data-processing-agreement.md](data-processing-agreement.md) | You ↔ tenant | Your obligations when processing *their* users' data |
| [acceptable-use-policy.md](acceptable-use-policy.md) | You ↔ tenant | What tenants may not do with the platform |
| [subprocessors.md](subprocessors.md) | Disclosure | Third parties that touch tenant data |

## Why a DPA matters most here

Bifrost is a **processor**, not a controller, for the great majority of the
personal data it holds. A tenant's end users gave their email to *the tenant* —
Savvify, edcore, Ministry Exam Prep — and Bifrost holds it on that tenant's
instruction.

That distinction decides who answers a deletion request, who notifies a
regulator after a breach, and who is liable when something goes wrong. The
Terms of Service alone does not establish it. The DPA does, and any customer
with a compliance function will ask for one before signing.

## Placeholders to fill before use

| Placeholder | What it needs |
|---|---|
| `[LEGAL ENTITY NAME]` | The registered company, exactly as incorporated |
| `[COMPANY REGISTRATION NUMBER]` | Registration / incorporation number |
| `[REGISTERED ADDRESS]` | Registered office address |
| `[JURISDICTION]` | Governing law (e.g. Kingdom of Cambodia) |
| `[COURTS]` | Where disputes are heard |
| `[PRIVACY CONTACT EMAIL]` | A monitored mailbox for data requests |
| `[SECURITY CONTACT EMAIL]` | A monitored mailbox for vulnerability reports |
| `[SUPPORT CONTACT EMAIL]` | General support |
| `[NOTICE PERIOD]` | Days of notice for term changes — 30 is typical |
| `[EFFECTIVE DATE]` | The date you publish these |

## Questions for counsel

Written down so they are not forgotten rather than because the answers are
obvious:

1. **Where do your customers' users live?** Bifrost handles Cambodian payment
   rails, but an end user in the EU or UK brings GDPR with them regardless of
   where you are incorporated. The DPA is drafted assuming that is possible.
2. **Are you a payment intermediary?** You do not hold funds — PayWay and
   Gumroad credentials belong to each tenant, and money moves between the end
   user and the tenant's own merchant account. That is deliberate, and it is the
   single most important fact for whether payment regulation attaches to you.
   Confirm it holds for every payment method before relying on it.
3. **Does Cambodia's personal data regime apply to you as drafted?** Cite the
   current statute in the privacy policy once counsel confirms which one governs.
4. **Retention.** The periods stated in the privacy policy are the ones the code
   actually enforces. Confirm they satisfy any local minimum for financial
   records — a tenant's transaction history may be subject to a longer statutory
   floor than Bifrost's own defaults.
