# Polar API reference (saved 2026-11-17)

Source: Polar docs, "API Overview". Full docs index: https://polar.sh/docs/llms.txt
Our own Polar setup (product IDs, discounts, checkout links) is in [POLAR.md](POLAR.md).

## Base URLs

| Environment | Base URL | Purpose |
|---|---|---|
| Production | `https://api.polar.sh/v1` | Real customers and live payments |
| Sandbox | `https://sandbox-api.polar.sh/v1` | Safe testing and integration work |

The sandbox is fully isolated: data, users, tokens and organizations there do not affect production.
Create separate tokens in each environment. Docs: https://polar.sh/docs/integrate/sandbox.md

## Authentication

### Organization Access Token (OAT): for our backend

Acts on behalf of the organization (products, prices, checkouts, orders, subscriptions, benefits, refunds).

```http
Authorization: Bearer polar_oat_xxxxxxxxxxxxxxxxx
```

- Created in the organization settings. Docs: https://polar.sh/docs/integrate/oat.md
- Never put it in client-side code, public repos or logs. A leaked OAT is revoked automatically by Polar's
  secret scanning.
- For ClipHeist: stored as `POLAR_ACCESS_TOKEN` in `backend/.env` (git-ignored).

### Customer Access Token: for customer-facing pages

Do not use the OAT in the browser. Create a Customer Session on the server
(`POST /v1/customer-sessions/`), then use the returned customer access token with the Customer Portal API so a
signed-in customer can see only their own orders, subscriptions and benefits.
Docs: https://polar.sh/docs/api-reference/2026-10/customer-sessions/create-customer-session.md

## Core API vs Customer Portal API

| | Core API | Customer Portal API |
|---|---|---|
| Audience | Our server / backend | One customer |
| Auth | Organization Access Token | Customer Access Token |
| Scope | Everything in the organization | Only that customer's data |
| Typical use | Admin, automation, provisioning | Custom customer portal or gated app |
| Token creation | Dashboard (manual) | `/v1/customer-sessions/` (server-side) |
| Sensitive actions (create products, refunds) | Yes | No |

## Official SDKs

Python (our backend is Python):

```bash
uv add polar-sdk        # or: pip install polar-sdk
```

```python
import os
from polar.v2026_10 import Polar

polar = Polar(os.environ["POLAR_ACCESS_TOKEN"])
customer_state = polar.customers.get_state_external("customer_external_id")
print(customer_state)
```

TypeScript:

```bash
npm install @polar-sh/sdk
```

```typescript
import { createPolar } from "@polar-sh/sdk/2026-10";

const polar = createPolar({ accessToken: process.env.POLAR_ACCESS_TOKEN! });
const customerState = await polar.customers.getStateExternal("customer_external_id");
```

Both default to production. Use `environment="sandbox"` (Python) or `environment: "sandbox"` (TypeScript) for
the sandbox.

## Quick examples

```bash
# Production, Core API
curl https://api.polar.sh/v1/products/ \
  -H "Authorization: Bearer $POLAR_OAT" -H "Accept: application/json"

# Sandbox, Core API
curl https://sandbox-api.polar.sh/v1/products/ \
  -H "Authorization: Bearer $POLAR_OAT_SANDBOX" -H "Accept: application/json"

# Customer Portal API
curl https://api.polar.sh/v1/customer-portal/orders/ \
  -H "Authorization: Bearer $POLAR_CUSTOMER_TOKEN" -H "Accept: application/json"
```

## Pagination

List endpoints take `page` (default 1) and `limit` (default 10, max 100). `page` is a window offset:
`page=2&limit=10` skips the first 10 and returns the next 10.

Responses include:

| Field | Meaning |
|---|---|
| `pagination.total_count` | Total items across all pages |
| `pagination.max_page` | Number of pages for the current `limit` |

To fetch everything, loop `page` from 1 to `max_page`. The SDKs have built-in pagination helpers.

```bash
curl "https://api.polar.sh/v1/products/?page=1&limit=100" -H "Authorization: Bearer $POLAR_OAT"
```

## Rate limits

| Environment | Limit |
|---|---|
| Production | 500 requests per minute per organization / customer / OAuth2 client |
| Sandbox | 100 requests per minute |
| License key validate / activate / deactivate (unauthenticated) | 3 requests per second, both environments |

Going over returns `429 Too Many Requests` with a `Retry-After` header saying how long to wait.
Higher limits: contact Polar support.

## Related docs to read when building the integration

- Webhooks (for `order.paid` → add clip credits): see the docs index above
- Sandbox testing: https://polar.sh/docs/integrate/sandbox.md
- Local webhook testing with the CLI: `polar webhook listen`, `polar webhook trigger`
