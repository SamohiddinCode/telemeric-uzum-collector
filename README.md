# Telemeric Uzum Telegram collector

The collector signs in as a regular Telegram account, reads only group
`-1002707306458`, imports recent history, and forwards new messages to the
private Telemeric Uzum dashboard.

Required service secrets:

- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from `my.telegram.org/apps`.
- `TELEGRAM_COLLECTOR_SECRET` shared with the Sites ingestion endpoint.
- `SITES_AUTH_TOKEN` for identity-less access to the private Site.
- `SETUP_TOKEN` protecting the one-time `/setup` page.

The Telegram StringSession is encrypted with a key derived from
`TELEGRAM_COLLECTOR_SECRET` before it is stored in the Site database. The Site
stores only encrypted ciphertext.

After deploy, open `/setup?token=<SETUP_TOKEN>`, enter the Telegram phone
number, and complete the one-time Telegram verification in the page. Do not
commit or paste any real secret into this repository.

## Free Northflank deployment

1. Create a private GitHub repository and upload all files from this package.
2. In Northflank, create a free Sandbox project and a **Combined service**.
3. Select the repository and `main` branch, then choose **Dockerfile**.
4. Use port `8080` with the HTTP protocol and one instance.
5. Add every value from `.env.example` as a runtime variable. Never commit the
   real values to GitHub.
6. Deploy, open `/health`, and then open `/setup?token=<SETUP_TOKEN>` to sign in.

The Docker image runs the collector continuously and exposes only its health
and one-time protected setup pages.
