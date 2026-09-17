# Telemeric Uzum Telegram collector

The collector signs in as a regular Telegram account, reads only group
`-1002707306458`, imports recent history, and forwards new messages to the
private Telemeric Uzum dashboard.

Required service secrets:

- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from `my.telegram.org/apps`.
- `TELEGRAM_COLLECTOR_SECRET` shared with the Sites ingestion endpoint.
- `SITES_AUTH_TOKEN` for identity-less access to the private Site.
- `SETUP_TOKEN` protecting the one-time `/setup` page and analytics admin endpoints.
- `TELEGRAM_BOT_TOKEN` authenticating mirrored updates from the existing
  `telegram-usedesk-bridge` service. The bridge keeps ownership of the Telegram
  webhook; the collector never replaces it.

When `TELEGRAM_BOT_TOKEN` is configured, new messages arrive through the
existing bridge and reports are sent with that service bot. A Telegram user
session is then optional and is needed only for an automatic history backfill.
The Telegram StringSession, when used, is encrypted with a key derived from
`TELEGRAM_COLLECTOR_SECRET` before it is stored in the Site database.

After deploy, open `/setup?token=<SETUP_TOKEN>`, enter the Telegram phone
number, and complete the one-time Telegram verification in the page. Do not
commit or paste any real secret into this repository.

## Daily SLA analytics

The analytics layer is intentionally isolated from the original collector. `main.py`
is unchanged. `bootstrap.py` wraps the existing delivery function, mirrors normalized
messages into a disposable local SQLite cache, and starts the report scheduler only
when `DAILY_REPORT_ENABLED=true`.

The support team uses one shared Telegram account: `@uzum_franchise`. Two operators
may work through it on different days, but reports intentionally do not split metrics
by employee. Any message sent by `@uzum_franchise` is treated as a support response
for SLA calculations.

The report uses the configured workday window and sends three PNG images:

1. Executive summary: tickets, SLA, median response, unanswered and top categories.
2. Load & response: hourly traffic, response-time metrics and shift-quality indicators.
3. Details: top questions, top solutions and the longest SLA breaches.

Important settings:

- `REPORT_CHAT_ID` — destination Telegram group. Defaults to the source group.
- `REPORT_TIMEZONE=Asia/Tashkent`.
- `WORKDAY_START=10:00`, `WORKDAY_END=19:00`.
- `REPORT_TIME=19:01`.
- `SLA_TARGET_MINUTES=15`, `SLA_TARGET_PERCENT=90`.
- `ANALYTICS_AGENT_USERNAMES=uzum_franchise` — shared support account without employee-level breakdown.
- `TICKET_GAP_MINUTES=30` — customer messages inside this gap stay in one open ticket until the first support response.

Keep `DAILY_REPORT_ENABLED=false` until the destination group has been verified and
a manual report has been checked. The original Telegram -> Site delivery continues
even if analytics cache writes or report rendering fail.

Protected operator endpoints:

- `GET /analytics/status` with `Authorization: Bearer <SETUP_TOKEN>` — preview counters/config.
- `POST /analytics/report-now` with the same header — force a test report immediately.
- `GET /analytics/check-recipient` with the same header — verify that the report bot can reach the configured recipient without sending a message.

The legacy `?token=` form remains available for the browser setup page, but
operator API calls should use the authorization header so secrets do not enter
request logs.

## Free Northflank deployment

1. Create a private GitHub repository and upload all files from this package.
2. In Northflank, create a free Sandbox project and a **Combined service**.
3. Select the repository and branch, then choose **Dockerfile**.
4. Use port `8080` with the HTTP protocol and one instance.
5. Add every value from `.env.example` as a runtime variable. Never commit the
   real values to GitHub.
6. Deploy, open `/health`, and then open `/setup?token=<SETUP_TOKEN>` to sign in.
7. Verify `/analytics/status` before enabling scheduled reports.

The Docker image runs the collector continuously and keeps analytics optional and
isolated from the existing collector flow.
