# Sending email from your own domain

By default, Bifrost sends your users' verification codes, invites and alerts through
our mailbox. The email carries your app name and your logo, but the address it comes
from is ours.

Point Bifrost at your own mail server and that changes: every email to your users is
sent from your server, under your address. Nothing else about the emails changes.

You do not have to do this. If you skip it, everything keeps working.

---

## Do you need it?

| | Our mailbox (default) | Your mail server |
|---|---|---|
| From address | `Your App <bifrostbyhelm@gmail.com>` | `Your App <noreply@yourdomain.com>` |
| Deliverability depends on | Our sending reputation, shared with other tenants | Yours alone |
| Daily send limit | Shared across all tenants | Whatever your provider allows |
| Setup | None | This page, about 20 minutes |
| Replies from users go to | Nowhere useful | Your inbox, if you use a real address |

Worth doing if you send more than a trickle of email, if your users are business
users who will notice a gmail.com sender, or if your compliance people have opinions
about where your mail originates.

---

## What you need first

Four things, from whoever runs email for your domain:

1. **SMTP host** — e.g. `smtp.yourdomain.com`, `smtp.sendgrid.net`, `email-smtp.eu-west-1.amazonaws.com`
2. **Port** — almost always `587`. See [Ports](#ports) below.
3. **A from address** — e.g. `noreply@yourdomain.com`. This doubles as the SMTP username on most providers.
4. **A password or SMTP token** for that address.

If your provider offers an API key or "SMTP credentials" separate from the mailbox
password, use those. They are scoped to sending and you can revoke them without
locking anyone out of a real inbox.

### Common providers

| Provider | Host | Port | Username |
|---|---|---|---|
| Google Workspace | `smtp.gmail.com` | 587 | the full address; requires an [App Password](https://support.google.com/accounts/answer/185833), not the account password |
| Microsoft 365 | `smtp.office365.com` | 587 | the full address |
| SendGrid | `smtp.sendgrid.net` | 587 | the literal string `apikey`, with the API key as the password |
| Amazon SES | `email-smtp.<region>.amazonaws.com` | 587 | the SES SMTP username (not your AWS access key) |
| Mailgun | `smtp.mailgun.org` | 587 | `postmaster@<your-domain>` |
| Postmark | `smtp.postmarkapp.com` | 587 | your server API token, as both username and password |

---

## Setting it up

In the console, open your app → **Settings** → **Outbound email**.

1. **SMTP host** — from your provider.
2. **Port** — `587` unless your provider says otherwise.
3. **From address** — the address your users will see, also the SMTP username.
4. **From name** — optional. Blank uses your app name. Set it to something like
   `Acme Support` if you want the sender to read differently from the app itself.
5. **SMTP password** — typed once. It is encrypted before it is stored and the field
   never shows it back to you. Leaving it blank on a later save keeps the stored one.
6. **Save changes.**

All four of host, port, from address and password must be filled in. If any one of
them is missing, Bifrost keeps using our mailbox rather than trying a half-configured
server — a wrong-but-plausible combination fails silently at the recipient's spam
filter, which is much harder to notice than not being switched on yet.

To go back to our mailbox, clear the SMTP host and save.

---

## Before your first send: SPF and DKIM

This is the part that decides whether your mail lands in the inbox or the spam
folder, and it happens in your DNS, not in Bifrost.

Your provider publishes the exact records — ask them for "the SPF and DKIM records
for sending from this domain". Roughly:

- **SPF** — one TXT record on your domain listing who may send as you. If you already
  have an SPF record, *edit it*, do not add a second one. Two SPF records is the same
  as none.
- **DKIM** — one or more CNAME or TXT records your provider gives you, which lets
  receiving servers verify the signature on your mail.
- **DMARC** — optional but recommended once the first two are in place and verified.

Give DNS an hour, then send yourself a test invite from the console and check the
message headers. Gmail: **⋮ → Show original**. You want `SPF: PASS` and `DKIM: PASS`.

---

## Checking it works

Invite yourself as a user in the console (**Users** → invite, your own address). The
invite email should arrive from your address, not ours.

If it does not arrive:

| Symptom | Usual cause |
|---|---|
| Nothing arrives, and it worked before you changed the settings | Wrong password, or your provider requires an app-specific password rather than the account one |
| Nothing arrives, no email ever worked | Email OTP may be switched off — check **Settings → Enabled services → Email OTP** |
| It arrives from `bifrostbyhelm@gmail.com` | One of the four fields is blank, so Bifrost fell back. Re-check all four |
| It arrives in spam | SPF or DKIM is missing or not yet propagated |
| It arrives but the reply bounces | Your from address is send-only. Use a real mailbox, or set a forwarding rule on it |

We log the failure server-side when your server rejects a message. If you are stuck,
tell us roughly when you tried and we can tell you what your server said.

---

## Notes

- **Port 587 with STARTTLS is what Bifrost speaks.** Port 465 (implicit TLS) and port
  25 are not supported. Every provider in the table above offers 587.
- **The password lives in your app's own vault**, encrypted with a key derived from
  your app's secret. Another tenant's key cannot read it, and it is never rendered
  back into the settings form.
- **Console emails are not affected.** Password resets and login codes for the Bifrost
  console itself always come from us — those are our mail, not yours.
- **Rotating the password:** type the new one into the field and save. There is no
  window where both are valid, so rotate when a failed send is survivable.

## Ports

`587` is the submission port with STARTTLS: the connection opens in the clear and is
upgraded to TLS before the password is sent. This is the modern default and what
Bifrost uses.

If your provider insists on `465`, that is implicit TLS — a different handshake, and
Bifrost does not currently support it. Tell us and we will add it; in the meantime
almost every provider that offers 465 also offers 587.
