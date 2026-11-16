# BeerShop P1 — mobile checkout, notifications, analytics, SEO, conversion

**Worktree:** existing staging Draft PR #17, branch
`staging/beershop-v9-sales-readiness-audit`. **DO NOT merge or deploy to
production.** No new paid services, real customer messages, external
analytics SDKs, payment attempts or live database migration.

## Mobile conversion UX

- A minimum 16px input font avoids iOS form zoom; touch controls are
  at least 44px tall.
- Full-screen scrollable cart on narrow devices (320–600px) uses
  `100dvh`, reduced horizontal overflow, a visible close control, safe
  area padding and a sticky confirmation action.
- The existing server-side `/api/quote` remains authoritative for
  stock, currency and total. If a price changes, the buyer sees an
  explicit state and must confirm **again** before `/api/orders`.
- Rapid repeat submit clicks are ignored while processing. On failure,
  the contact fields remain intact; no optimistic payment confirmation
  is displayed. The existing server idempotency key remains in place.
- After a successful synthetic order, the on-page confirmation clarifies
  that SMS/email are **not** automatically delivered, and bank payment
  has **not** been confirmed. This is not an SMS/email service.

## Test-only anonymous funnel metrics

The in-memory aggregate module `app/ux_p1.py` accepts only an exact
enumeration of stage names:
`catalog_view`, `product_view`, `cart_add`,
`checkout_open`, `checkout_attempt`, `checkout_success`,
`checkout_error` and `checkout_price_changed`.

It is **hard-disabled** unless ALL of the following hold:

```text
BEERSHOP_P1_UX_ANALYTICS=1
BEERSHOP_P1_UX_ACK=LOCAL_SYNTHETIC_BROWSER_ONLY
BEERSHOP_PERSISTENT_DATABASE_URL unset
DATABASE_URL points to an isolated /tmp/beershop-browser-*/test.db SQLite file
```

Only the existing local browser test fixture is enabled; production
Render/Neon, other SQLite paths and even generic localhost PostgreSQL
are rejected.

Endpoints (test fixture only):

- `GET /api/p1/ux-capabilities` — public, harmless enabled flag;
- `POST /api/p1/ux-event` — one finite event key, strict schema,
  local loopback only; rejects extra payload fields, such as email;
- `GET /api/admin/p1/ux-report` — owner/auditor-protected counts and
  attempt-success *event ratio*; no customer records or identifiers;
- `GET /api/admin/p1/notification-previews` — owner/auditor-protected
  email/SMS/on-site copy templates; `messages_sent=0`.

No cookies, unique visitor IDs, session IDs, IP analytics capture,
personal data, third-party SDKs or cross-site requests. No analytics
table is added to the model or database. Counts are **per process and
reset on restart**. The ratio is successes / submit attempts and NOT a
unique-user conversion rate or attribution measurement. Live traffic
and marketing analytics require consent/privacy review, persistence
design, retention policy, abuse controls and real rollout approval.

## Notification simulation

Three non-delivering message examples show order acceptance, manual
payment awaiting confirmation and dispatch status. They are **copy
previews only**. There is no live email/SMS sender, consent capture,
provider integration or real buyer messaging.

## SEO groundwork (not published indexing)

- Static storefront includes a Ukrainian meta description and social
  preview title/description.
- In the isolated local P1 browser fixture, `/products/{slug}`
  server-renders HTML-escaped product-specific `<title>`, description
  and Open Graph fields. The preview returns `X-Robots-Tag:
  noindex, nofollow`. Public product routes remain unchanged when
  the test feature is disabled.
- **No canonical domain is asserted** before the merchant's actual
  domain is verified. No price/stock Product Offer structured data is
  published based on unverified merchandise.
- Future launch SEO still needs approved canonical hostname, verified
  seller information, product availability, legal pages, crawlable
  sitemap, real performance measurement and indexing audit.

## Local automated verification

`tests/test_ux_p1.py` covers denial outside fixture, finite counters,
no-PII guards, notification previews and HTML injection.

`tests/p1_ux_browser.cjs` runs under
`tests/run_crm_browser.py` against an **isolated local synthetic DB**;
it exercises 320/360/390/600/768/1366px mobile cart layout, touch
targets, SEO preview, re-confirmation on simulated FX price changes,
one real *synthetic* checkout, onscreen confirmation, aggregate funnel
and SMS/email templates. It aborts all outbound non-local URLs.

The v9.7 release decision remains **NO-GO**. P1 usability work must
not override security and commerce-readiness blockers. The real website
is not deployed or changed by this PR.
