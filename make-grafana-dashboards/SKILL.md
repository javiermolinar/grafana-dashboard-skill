---
name: make-grafana-dashboards
compatibility: Requires gcx CLI for Grafana API/resource operations and Python Playwright for authenticated UI screenshots.
description: Create, modify, validate, publish, and visually review Grafana dashboards. Use when building dashboards, editing panels/variables/links, finding dashboard examples, using gcx, or taking Playwright screenshots of Grafana dashboards.
---

# Make Grafana Dashboards

Build Grafana dashboards as diagnostic tools, not metric inventories. Use `gcx` for API/resource work and Python Playwright for UI screenshots.

## Hard rules

- Project/AGENTS instructions override this skill.
- Do not create dashboards in arbitrary/default folders. If destination is ambiguous, ask one concise question.
- If the user gives a destination (for example, “create X dashboard in my folder”), proceed with stated assumptions instead of a long clarification round.
- Verify metrics/labels/datasources before adding panels.
- After creating or materially editing a dashboard, capture and inspect a screenshot unless auth/tooling blocks it.
- Report assumptions, target context/folder, dashboard URL/version, screenshot path, and remaining issues.

## Auth model

There are two independent auth layers:

- **gcx auth**: API access, dashboard read/write, datasource access, folder permissions.
- **Playwright/browser auth**: UI screenshot access via browser cookies in a persistent profile.

A `gcx` context can be online but still fail writes with `403`. A browser profile can be logged out even when `gcx` works.

Classify API failures:

- connectivity/auth: `gcx config check` fails, token expired, login required
- read permission: search/get returns 401/403
- write permission: save/update returns 403 while reads work
- folder permission: save fails only for a specific folder

Before saving/updating:

```bash
gcx config current-context
gcx config check
gcx config list-contexts
```

Try an explicitly appropriate context if available. Report the context used.

## Minimal decision flow

1. Confirm target only if needed: Grafana URL/context, folder, new vs existing UID, overwrite permission.
2. State assumptions for vague requests. Defaults: on-call/SRE audience, diagnostic triage dashboard, story `context → symptom → culprit/entity → subsystem path → resources/efficiency`.
3. Find examples: live Grafana first if local scans are restricted or irrelevant; local repo only if project rules permit.
4. Discover datasources/metrics/labels/log fields.
5. Build/update dashboard.
6. Push with `gcx`.
7. Fetch back and verify UID/folder/panels/variables/version.
8. Screenshot, inspect, iterate.

## Example discovery

Local repo examples are optional and only allowed when project instructions permit broad scans:

```bash
find . -iname '*dashboard*' -o -iname '*grafana*' | head -100
rg -n 'gridPos|templating|panels|dashboard|grafana-foundation-sdk' .
```

Live Grafana examples are often better for datasource variables and folder conventions:

```bash
gcx dashboards search '<keyword>' -o wide
gcx dashboards get <uid> -o json
```

Fallback when `gcx dashboards` resource API/RBAC fails:

```bash
gcx api '/api/search?query=<keyword>' -o json
gcx api /api/dashboards/uid/<uid> -o json
```

Extract useful patterns:

```bash
jq -r '.. | objects | select(.expr?) | .expr' dashboard.json | sort -u
jq '.dashboard.templating.list[]? | {name,type,query,current}' dashboard.json
jq '.dashboard.panels[]? | {id,title,type,gridPos}' dashboard.json
```

## Datasource and data discovery

If multiple Prometheus/Loki/Tempo datasources exist, infer from existing dashboards in the same folder/team. If still ambiguous, ask one concise datasource question.

Examples:

```bash
gcx metrics query 'up' -o json
gcx datasources prometheus query -d <prom_uid> 'count by (__name__) ({__name__=~".*query.*"})' --since 5m -o json
gcx datasources loki query -d <loki_uid> '{namespace="example"} | logfmt' --since 1h -o raw
gcx traces labels -d <tempo_uid> -o json
```

Check metric existence, labels, units, histogram buckets, status/route/op labels, and parsed log fields.

## Dashboard design guidance

Rows should tell a story. Keep only the triage row open unless the dashboard is explicitly a drilldown.

Common row flow:

```text
Triage + context
User outcome
Tenant/entity evidence
Frontend/gateway scheduling
Worker/backend execution
Storage/cache path
Resource pressure
Efficiency / before-after tuning
```

Each row should have a decision-gate description.

Row JSON example:

```json
{
  "id": 10,
  "type": "row",
  "title": "Query-frontend → queriers: scheduling",
  "description": "Move here when latency rises with queueing or throttles. Check batch weight, connected clients, and frontend cache health.",
  "collapsed": true,
  "gridPos": {"x": 0, "y": 12, "w": 24, "h": 1},
  "panels": []
}
```

Panel conventions:

- concise non-truncated titles
- descriptions for interpretation
- `topk()` for high-cardinality tenants/pods
- clear units and meaningful thresholds
- entity links for drilldown
- noisy/deep panels in collapsed rows
- no annotation spam

## Create/update with raw Grafana API

Fetch:

```bash
gcx api /api/dashboards/uid/<uid> -o json > /tmp/dashboard.json
```

Push payload shape:

```json
{"dashboard": {...}, "folderUid": "<folderUid>", "message": "Explain change", "overwrite": true}
```

Push:

```bash
gcx api /api/dashboards/db -d @/tmp/payload.json -o json
```

No dry-run exists for this endpoint. Fetch back and verify:

```bash
gcx api /api/dashboards/uid/<uid> -o json \
  | jq '{uid:.dashboard.uid,title:.dashboard.title,version:.dashboard.version,folder:.meta.folderUid,panels:(.dashboard.panels|length),vars:[.dashboard.templating.list[]?.name]}'
```

For resource-managed dashboards prefer:

```bash
gcx resources pull dashboards/<uid> -p ./resources
gcx resources validate -p ./resources
gcx resources push -p ./resources --dry-run
gcx resources push -p ./resources
```

## Links and drilldowns

Use absolute dashboard paths for internal links to avoid app-relative redirects:

```text
/d/<uid>/<slug>?${__url_time_range}&${varname:queryparam}&var-tenant=${__field.labels.tenant}
```

For trace links, provide both exact trace and log-correlation fallback:

```logql
{cluster=~"$cluster", namespace=~"$namespace"} |= "$traceID"
```

Long table-row URLs can be brittle. Provide a manual fallback in panel descriptions/prompts.

## Screenshot setup

Install once per machine:

```bash
python3 -m venv ~/.cache/grafana-dashboard-screenshot/venv
~/.cache/grafana-dashboard-screenshot/venv/bin/python -m pip install --upgrade pip
~/.cache/grafana-dashboard-screenshot/venv/bin/python -m pip install -r /path/to/skill/requirements.txt
~/.cache/grafana-dashboard-screenshot/venv/bin/python -m playwright install chromium
```

Helper:

```text
scripts/grafana_dashboard_screenshot.py
```

List profiles before declaring browser auth missing:

```bash
~/.cache/grafana-dashboard-screenshot/venv/bin/python /path/to/skill/scripts/grafana_dashboard_screenshot.py profiles
ls -1 ~/.cache/grafana-dashboard-screenshot/ 2>/dev/null || true
```

If a host-matching profile exists, retry capture with `--profile-name` or `--profile-dir`.

## Agent-friendly browser login

Start a non-blocking headed login browser:

```bash
~/.cache/grafana-dashboard-screenshot/venv/bin/python /path/to/skill/scripts/grafana_dashboard_screenshot.py login-start \
  --url 'https://grafana.example.com' \
  --profile-name grafana-example
```

Tell the user exactly:

```text
Log in in the opened browser, then say continue.
```

When the user says continue:

```bash
~/.cache/grafana-dashboard-screenshot/venv/bin/python /path/to/skill/scripts/grafana_dashboard_screenshot.py login-finish \
  --url 'https://grafana.example.com' \
  --profile-name grafana-example
```

Blocking human mode also exists: `login`.

## Capture and review screenshot

```bash
~/.cache/grafana-dashboard-screenshot/venv/bin/python /path/to/skill/scripts/grafana_dashboard_screenshot.py capture \
  --url 'https://grafana.example.com/d/<uid>/<slug>?from=now-1h&to=now' \
  --profile-name grafana-example \
  --output './screenshots/dashboard.png' \
  --viewport 1800x1400 \
  --wait-ms 8000
```

Useful options:

```bash
--profile-dir <path>
--selector 'body'
--full-page / --no-full-page
--headed
--wait-for-text '<dashboard title>'
--allow-login-page
```

For layout review, use a wide viewport. If the left nav wastes space, use a larger viewport, collapse nav in the profile, or use kiosk/embedded/solo-style URL options when appropriate.

Review checklist:

- not a login page
- expected dashboard/time range/variables
- row order and collapsed/open state
- title truncation
- overlap or layout gaps
- annotation spam
- no-data panels wasting prime space
- unreadable high-cardinality legends
- left nav reducing useful width
- top screen is actionable

## Final response

Include:

- assumptions made
- dashboard UID/title/URL
- Grafana context and folder
- version/result
- changes made
- screenshot path and visual validation summary
- if screenshot failed: blocker plus login/capture command sequence
- unresolved issues or next recommendation
