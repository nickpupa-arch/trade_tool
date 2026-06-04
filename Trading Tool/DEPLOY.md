# Fly.io deploy runbook

End-to-end migration of the trade tool from GitHub Actions to a single
always-on Fly.io machine. GitHub Actions stay live as fallback until step 9
explicitly retires them.

**Why Fly:** sub-minute cadence without GitHub's lossy scheduler; lower
latency on intraday alerts; one process to reason about instead of three
workflows + an external cron-job; cost is roughly $3–5/month.

**Architecture:** one process (`loop.py`) running four threads — intraday
worker, daily worker, bot worker, HTTP server — with `state.enc` on a
mounted volume (`/data`). All preserved Phase 1 behavior; cadences are env-
tunable.

---

## 0 · Pre-flight

You need on your local machine:
- `flyctl` — install: `curl -L https://fly.io/install.sh | sh`
- A Fly.io account: `fly auth signup` (or `fly auth login` if you have one)
- Your **three secrets** ready to paste:
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_CHAT_ID`
  - `PORTFOLIO_ENCRYPTION_KEY` (the **exact same key** you used on GitHub Actions
    — without it, you cannot decrypt the existing `state.enc`)
- A copy of your current `state.enc` if you want to preserve portfolio +
  dedup history. You can pull the latest from the repo:
  `git pull && cp "Trading Tool/state.enc" ~/state.enc.backup`
  
**Estimated time:** 15 minutes if nothing goes sideways.

---

## 1 · Launch the app (creates app + selects region; no deploy yet)

```bash
cd "Trading Tool"
fly launch --no-deploy --copy-config --name trade-tool --region ord
```

- `--copy-config` keeps the committed `fly.toml`.
- `--no-deploy` lets us create the volume + secrets *before* the machine boots.
- `ord` is Chicago (close to NYSE). Pick a different region if you prefer:
  `fly platform regions` to list.
- If `trade-tool` is taken (likely), pick something unique like
  `trade-tool-<yourname>` and update `app =` in `fly.toml` to match.

---

## 2 · Create the volume for `state.enc`

```bash
fly volumes create trade_tool_data --size 1 --region ord --yes
```

1GB is overkill (`state.enc` is ~1KB), but it's the minimum and costs ~$0.15/mo.
Enable daily snapshots for durability:

```bash
fly volumes list
# copy the volume id (vol_...)
fly volumes snapshots schedule <vol_id> --daily-retention 7
```

---

## 3 · Set secrets

```bash
fly secrets set \
  TELEGRAM_BOT_TOKEN="paste_real_token" \
  TELEGRAM_CHAT_ID="paste_real_chat_id" \
  PORTFOLIO_ENCRYPTION_KEY="paste_exact_same_key_from_github_actions"
```

Fly will tell you it's deploying. That's fine — first deploy will fail the
health check until step 5 seeds the state, but the secrets are now set.

---

## 4 · First deploy (machine boots with an empty volume)

```bash
fly deploy
```

Watch the logs:
```bash
fly logs
```

You should see the orchestrator startup lines (`[loop] started`, `[http]
serving`, `[intraday] loop started`, etc.) and the bot worker firing
every 60s. The intraday worker will say `empty watchlist` until the daily
worker has run at least once during market hours — that's expected.

---

## 5 · Seed `state.enc` onto the volume (optional but recommended)

If you want to preserve your portfolio + bot state, copy your local backup
to the volume:

```bash
# Open an SFTP shell to the running machine
fly ssh sftp shell

# In the SFTP prompt:
put ~/state.enc.backup /data/state.enc
exit
```

Then restart so the loop reloads state:
```bash
fly apps restart trade-tool
```

Skipping this step starts you with an empty portfolio — the bot's `/add`
commands still work and the daily snapshot populates on the next refresh.

---

## 6 · Verify

Hit the health endpoint:
```bash
curl https://<your-app>.fly.dev/health | python3 -m json.tool
```

You should see something like:
```json
{
  "ok": true,
  "uptime_s": 123,
  "now_et": "2026-06-04T17:30:00-04:00",
  "windows": { "intraday": true, "regular": true },
  "workers": {
    "intraday": { "runs": 2, "errors": 0, "last_ok": "..." },
    "daily":    { "runs": 1, "errors": 0, "last_ok": "..." },
    "bot":      { "runs": 4, "errors": 0, "last_ok": "..." }
  }
}
```

Open the dashboard in a browser:
```
https://<your-app>.fly.dev/
```

Test the bot from Telegram (`/list`) — should respond within 60s.

If the next intraday alert that should have fired (RVOL ≥ 2 on a watched
name during market hours) lands in Telegram, you're done.

---

## 7 · Disable GitHub Actions schedules (but keep workflows around)

Don't delete the workflow files yet — they're our rollback path. Instead,
disable the schedules so they stop running:

In `.github/workflows/refresh.yml`, `intraday.yml`, `bot.yml`, comment out
the `schedule:` block (keep `workflow_dispatch` and `repository_dispatch`
so manual fallback still works). Push.

Also pause your cron-job.org job that fires `intraday-refresh` — log into
cron-job.org and toggle the job(s) to disabled.

The Pages deploy can stay on; it's harmless. Or kill it by disabling
`refresh.yml` entirely — but then the Pages URL goes stale and you should
update bookmarks to the new `https://<your-app>.fly.dev/`.

---

## 8 · Monitor for one trading session

Watch logs during a full session:
```bash
fly logs
```

Specifically check:
- Intraday worker fires every 60s during market hours; quiet outside.
- Daily worker fires every 5 min during regular hours.
- Bot worker fires every 60s always.
- No repeating exceptions.
- `/health` endpoint stays green (Fly's check graph should be all-green).

If everything held up for a session, proceed.

---

## 9 · Retire the GitHub workflows (separate PR — only when confident)

This is a separate commit so it's easy to revert. Delete:
- `.github/workflows/refresh.yml`
- `.github/workflows/intraday.yml`
- `.github/workflows/bot.yml`

Keep the `_site/` Pages config disabled (Settings → Pages → Source: None).
Delete the now-unused `state-update` concurrency group references — there
aren't any other consumers.

The repo still hosts code; just doesn't run schedules.

---

## Rollback

If Fly misbehaves at any point:

```bash
# Stop Fly so it stops firing
fly apps stop trade-tool

# Re-enable cron-job.org + the GitHub workflow schedules
# (uncomment the schedule blocks, push)
```

GitHub Actions will pick up the next scheduled tick within 5 min. `state.enc`
in the repo is the source of truth in this mode; the Fly volume copy
diverges until you cut back over.

To delete Fly entirely:
```bash
fly apps destroy trade-tool
fly volumes destroy <vol_id>
```

---

## Cost estimate

| Item | Cost |
|---|---|
| shared-cpu-1x, 512MB, always-on | ~$3/mo |
| 1GB volume | $0.15/mo |
| Daily volume snapshots (7 days × ~1KB) | <$0.01/mo |
| Egress (tiny) | $0 |
| **Total** | **~$3–4/mo** |

GitHub Actions on the free tier was $0, so this is the cost of moving off
the lossy scheduler and getting true sub-minute cadence.

---

## What you do NOT need to change

- `screener/` code is untouched by this migration.
- `state.enc` format is unchanged; just lives at `/data/state.enc` instead.
- Telegram bot commands behave identically — same `/add /remove /list`.
- The dashboard HTML is the same file the daily worker writes; just served
  by stdlib `http.server` from Fly instead of GitHub Pages.
