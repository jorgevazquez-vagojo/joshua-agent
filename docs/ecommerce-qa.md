# E-commerce QA Skills

Joshua-agent includes four built-in skills for auditing e-commerce platforms. These skills are designed for read-only sprints (`git_strategy: none`) that test live sites and codebases without deploying changes.

## Skills

### `researcher`

Tests the **live website** using HTTP. No codebase access required — the project directory is used only to write reports.

**What it does:**
- Probes URLs with `curl` — HTTP status codes, response times, redirects, SSL
- Tests key user flows: homepage, search, product page, cart, checkout, login, register
- Sends requests with iPhone mobile user-agent
- Checks EU compliance: cookie consent, GDPR notices, privacy policy
- Checks locale: language, currency format, number formats

**Required context:**
- `{site_url}` — the live site URL (e.g., `https://primor.eu`)
- `{project_dir}` — where to write reports (`reports/cycle-N.md`)

**Typical tasks:** Checkout funnel audit, category page scan, asset audit (broken images, JS/CSS), login/register flow, SSL + HSTS check.

---

### `magento-hunter`

Audits a **Magento 2 PHP codebase** for bugs and performance issues.

**What it does:**
- Scans for N+1 queries (`collection->load()` in loops, unfiltered `getItems()`)
- Audits custom modules in `app/code/` for DI errors, wrong area codes, broken plugins
- Checks layout XML for duplicate handles, missing templates, broken references
- Audits observers/plugins for heavy synchronous operations
- Flags deprecated APIs: direct `ObjectManager` use, non-injected dependencies
- Security: unescaped output in `.phtml`, raw SQL
- Audits cron jobs: table locks, missing cleanup, overlapping schedules

**Required context:**
- `{project_dir}` — Magento root directory

**Typical tasks:** Module audit, N+1 scan, template security review, cron audit, checkout plugin audit.

---

### `mobile-tester`

Tests **Magento REST/GraphQL API endpoints** for the mobile app.

**What it does:**
- Auth: `POST /rest/V1/integration/customer/token` — valid/invalid creds, JWT structure
- Catalog: `GET /rest/V1/products` — pagination, filters, required fields (name, price, sku, images)
- Cart: create, add items, update qty, apply coupon, stock validation
- Checkout: shipping-information, payment-information, order creation
- Search: relevance, empty results, response time
- Schema validation: required fields present, no nulls in critical fields
- Error responses: 401/404/422 have useful messages for mobile clients
- Flags endpoints >1s (mobile 4G threshold)
- Tests authenticated and unauthenticated access

**Required context:**
- `{site_url}` — API base URL (e.g., `https://primor.eu`)
- `{project_dir}` — where to write mobile reports

**Typical tasks:** Auth audit, catalog endpoint audit, cart/checkout flow test, search audit, account/orders audit.

---

### `ecommerce-qa`

Gate skill that reviews all agent findings and issues a **business-impact-aware verdict**.

**Severity mapping:**

| Severity | Verdict | Examples |
|---|---|---|
| CRITICAL | REVERT | Checkout broken, payment errors, login down, 5xx on main pages |
| HIGH | CAUTION | Pages >5s, broken search, cart issues, missing PDP images |
| MEDIUM | GO (with notes) | Broken links, copy errors, minor layout issues, API <5s |
| LOW | GO | Cosmetic issues, minor a11y, non-blocking warnings |

**Rules:**
- Reads all reports in `{project_dir}/reports/` before deciding
- Multiple CRITICAL issues → REVERT with each URL + symptom
- Output ranked by business impact + recommended next action

**Auto-detected:** Phase `gate`, `verdict_format: true` — no manual config needed.

---

## Configuration

These skills are activated by setting `skill:` in your agent YAML:

```yaml
agents:
  researcher:
    skill: researcher
    name: probe
    tasks:
      - "Audit the checkout funnel..."

  magento:
    skill: magento-hunter
    name: vulcan-m2
    run_when_blocked: true   # continues auditing even when gate blocks

  qa:
    skill: ecommerce-qa
    name: sentinel
    # phase: gate and verdict_format: true are auto-detected
```

### `site_url` field

Set `project.site_url` to pass the live URL to `researcher` and `mobile-tester` agents:

```yaml
project:
  name: my-shop
  path: ~/my-shop
  site_url: https://example.com
```

The value is available in prompts as `{site_url}`.

---

## Sprint settings for QA-only sprints

QA sprints that only test (never deploy) should use:

```yaml
sprint:
  git_strategy: none        # No git operations — reports only
  gate_blocking: true       # Block work agents on REVERT verdict
  cross_agent_context: true # Hunter gets researcher's findings from previous cycle
```

---

## Reports

All agents write findings to `{project_dir}/reports/`. The ecommerce-qa gate reads these before issuing its verdict. Reports persist across cycles, building an audit trail.

Report format (per finding):
```
| severity | URL/file | issue | recommendation |
```

Example: `examples/primor-magento.yaml`, `examples/primor-mobile.yaml`
