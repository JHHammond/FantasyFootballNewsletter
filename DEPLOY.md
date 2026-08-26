# Going live on Render

Ordered. Each step depends on the one before it.

## 1. Get the code into GitHub

Render deploys from a repo. Yours is local-only right now.

```bash
gh repo create commissioners-desk --private --source=. --push
```

Or make the repo on github.com and:

```bash
git remote add origin git@github.com:<you>/commissioners-desk.git
git push -u origin provider-layer
```

**Check `.env` is not in that push.** It's gitignored, but confirm:

```bash
git ls-files | grep -c '^\.env$'    # must print 0
```

If it ever did get committed, rotate every key in it — history is public
even after you delete the file.

## 2. Supabase

You've never run against a real database. In the SQL editor, in order:

```
migrations/002_accountless.sql
migrations/003_email.sql
migrations/004_lore.sql
migrations/005_setup.sql
migrations/006_edits.sql
migrations/007_themes.sql
```

All verified against a real Postgres and safe to re-run. Skip `000` and `001`.

Then Project Settings → API and keep the Project URL and the **service_role**
key handy. Not the anon key — see SETUP.md for why that's inverted here.

## 3. Resend

Only needed for email; the app runs fine without it (sends print to the log).

1. Add and verify your sending domain — DNS records, takes ~15 minutes.
2. Create an API key.
3. `EMAIL_FROM` must use the verified domain, e.g.
   `The Commissioner's Desk <papers@yourdomain.com>`.

Sending from an unverified domain lands in spam. Worth doing before you
market, not after.

## 4. Render

Dashboard → **New → Blueprint** → point at your repo. It reads `render.yaml`
and creates two services: the web app and the Tuesday cron.

Set these secrets on **both** services:

| Variable | Value |
|---|---|
| `BASE_URL` | `https://yourdomain.com` — no trailing slash |
| `SUPABASE_URL` | from step 2 |
| `SUPABASE_SERVICE_KEY` | the **service_role** key |
| `ANTHROPIC_API_KEY` | yours |
| `RESEND_API_KEY` | from step 3 |
| `EMAIL_FROM` | `Name <papers@yourdomain.com>` |
| `MAILING_ADDRESS` | a real postal address — CAN-SPAM requires it |

`TASK_KEY` is generated automatically.

**`BASE_URL` matters more than it looks.** Every share link, every link
preview, and every URL in an email is built from it. Get it wrong and the
links you hand people point somewhere that doesn't exist.

### Plan

`starter` ($7/mo) rather than free. The free tier spins down after 15 minutes
idle, so the first person to open a shared link waits ~30 seconds for a cold
start — on a product whose entire distribution is people tapping a link in a
group chat, that's the difference between reading it and closing the tab.

## 5. Domain

Render → Settings → Custom Domain, then add the CNAME it gives you. TLS is
automatic. Update `BASE_URL` to match and redeploy.

## 6. Check it works

```
https://yourdomain.com/healthz          -> {"ok": true}
```

Then, in order:

1. Create a league from the landing page.
2. Walk the setup questions, pick a theme.
3. Generate a week.
4. Open the share link **in a private window** — it must render with no login.
5. **Paste the share link into a group chat.** It should unfurl with the
   headline and, if you added a hero photo, the image. This is the one worth
   testing on a real device, because it's how every reader will arrive.
6. Open the paper on a phone.
7. Subscribe from the foot of the paper, confirm from the email.

## 7. Fire the weekly job once by hand

Don't wait until Tuesday to find out it's broken.

```bash
curl -X POST https://yourdomain.com/tasks/weekly \
     -H "X-Task-Key: $TASK_KEY" -d "week=1"
```

Returns a JSON summary of what it generated and mailed. Safe to run twice —
`emailed_at` stops a double send.

## Things that will bite you

**The cron's week calculation** assumes the 2025 season started 2 September.
Check `render.yaml` against the real Week 1 kickoff for the season you're in,
or it'll generate the wrong week every Tuesday.

**Rate limits are in-memory.** One instance only. If you ever scale to two,
each gets its own counters and the effective limit doubles. Move them to Redis
before scaling out.

**Storage is public-read by design.** Anyone with a paper's URL can read it —
that's the product. But it also means a URL, once shared, can't be unshared.

**No analytics yet.** You won't know how many people open a paper, which is
the first number an advertiser asks for. Worth adding before you sell anything.
