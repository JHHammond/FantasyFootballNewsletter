# Setup

The app is FastAPI + Jinja templates. There are no user accounts.

## 1. Install

```bash
pip install -r requirements.txt
```

## 2. Supabase

New project → SQL Editor → run every file in `migrations/`, in order:

1. **`002_accountless.sql`** — tables, storage bucket, RLS posture
2. **`003_email.sql`** — subscribers, magic links, auto-send
3. **`004_lore.sql`** — renames `inside_jokes` to `lore`
4. **`005_setup.sql`** — league format, tone, stakes, punishment
5. **`006_edits.sql`** — keeps the original prose alongside your edits
6. **`007_themes.sql`** — which theme a paper is set in
7. **`008_views_and_ops.sql`** — read counts
8. **`009_rate_limits.sql`** — durable rate limits and the daily spend ceiling
9. **`010_accounts.sql`** — commissioner accounts; readers are unaffected

All verified against a real Postgres: the chain runs three times consecutively
without error, and re-running one over live data leaves the data intact.

Then Project Settings → API, and put these in `.env`:

```
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<the service_role key>
ANTHROPIC_API_KEY=<your key>
CURRENT_SEASON=2025

# Email — optional. Without RESEND_API_KEY, sends are printed to the
# terminal instead, which is fine for local development.
RESEND_API_KEY=re_...
EMAIL_FROM="The Commissioner's Desk <papers@yourdomain.com>"
MAILING_ADDRESS="Your Name, 123 Street, City ST 00000"
BASE_URL=https://yourdomain.com
TASK_KEY=<random string; protects the weekly send endpoint>
```

**This time you want the service_role key, not anon.** That's the opposite of
the usual advice, and it's a direct consequence of dropping accounts.

With logins, the browser held the anon key and row-level security decided what
each user could see. With no logins there's no `auth.uid()` for RLS to check,
so RLS can't protect anything. Instead: RLS is enabled with *zero* policies
(deny everything), the server holds the service_role key and is the only thing
that ever talks to the database, and authorization happens by unguessable token.

Which means: **the service key must never reach a browser.** It stays in the
server's environment. Don't put it in a template, a client-side script, or a
public repo.

## 3. Run

```bash
uvicorn web.app:app --reload --port 8000
```

Open `http://localhost:8000`.

## 4. Try it

1. Paste league ID `1252396303246176256`, season 2025, hit **Create my paper**.
2. You land on the manage page. **Bookmark it** — that URL is the only
   credential this league has, and there's no reset.
3. Add two or three inside jokes.
4. Generate week 1. Takes ~30 seconds and spends real Claude tokens.
5. Open the share link in a private window. It should render with no login.

## The two URLs

| URL | Who has it | What it does |
|---|---|---|
| `/l/<admin_token>` | just the commissioner | generate, edit jokes, settings |
| `/p/<public_slug>` | the whole league | read every edition |

The admin token is 192 bits of entropy, so guessing isn't a realistic attack.
Losing it is — there's no email on file to recover with. The manage page says
so in a yellow box on first visit.

## Ads

Classified blocks render at the foot of every paper (`ads.py`). They're house
ads today, styled as period-appropriate classifieds rather than banner units —
a classified reads as part of the paper, whereas people have spent twenty years
learning to ignore anything shaped like a leaderboard.

Each slot still carries `data-ad-slot` and `data-ad-size` with standard IAB
dimensions, so handing inventory to an ad network later means pointing a script
at `[data-ad-slot]`. No redesign needed.

To customize per league, pass an `ads=[Ad(...)]` list into `build_edition()`.

## Rate limits

Generation costs money and there's no login gating it, so `web/app.py` caps
each IP at 10 generations and 5 league creations per hour. In-memory, which is
fine for one process — move it to Redis if you run more than one.

## Deploying

Any host that runs a Python process: Railway, Render, Fly.io. The start command
is:

```
uvicorn web.app:app --host 0.0.0.0 --port $PORT
```

Set the same environment variables there. No build step, no Node, no static
asset pipeline.

## Tests

```bash
python -m pytest tests/ -q      # no network, no database
python verify_provider.py       # live Sleeper fetch
```

## Email

Three capture points, none of them a wall:

| Where | Ask | Type |
|---|---|---|
| Manage page | "We'll email you the link" | transactional |
| Foot of every paper | "Get this in your inbox" | marketing |
| Archive page | same | marketing |

Readers go through **double opt-in** — a subscription isn't active until the
address clicks a confirmation link. It costs some conversion and buys
deliverability, plus it stops anyone signing up an address they don't own.

Every marketing email carries a one-click unsubscribe link and `List-Unsubscribe`
headers, so Gmail and Apple Mail show their own unsubscribe button. Without
those, people unsubscribe by hitting "report spam", which damages the sending
domain for every league at once.

`MAILING_ADDRESS` appears in the marketing footer. CAN-SPAM requires a physical
address on marketing mail; the manage-link email is transactional and exempt.

**Without `RESEND_API_KEY` set, nothing is sent** — messages print to the
terminal. Good for local work, and it's why the tests need no credentials.

### Costs

Resend's free tier is 3,000 emails/month but capped at **100 per day**. A weekly
newsletter all firing on the same morning hits the daily cap first — roughly 16
leagues at 6 subscribers each. Pro at $20/mo removes the daily limit.

## Recovery instead of accounts

`/recover` takes an email and sends back the manage links for every paper
registered to it. Links work once and expire in 30 minutes. That's the whole of
authentication — no passwords, no signup form, no account settings.

The page gives the same response whether or not the address is known, so it
can't be used to check who has an account.

If a commissioner never gave us an email and loses their bookmark, they're
locked out. That's the honest cost of no accounts, which is why the manage page
asks for an address in a yellow box on first visit.

## Weekly auto-send

Turn on "Do it for me every week" in a league's settings. Then run the job
weekly, whichever way your host supports:

```bash
python -m web.tasks 3                    # real cron
```

```bash
curl -X POST https://yourdomain.com/tasks/weekly \
     -H "X-Task-Key: $TASK_KEY" -d "week=3"      # external scheduler
```

It generates the week if it doesn't exist, emails every confirmed subscriber,
and stamps `emailed_at`. **Safe to run twice** — the stamp is checked before
sending, so a retrying cron or a second process won't double-mail anyone.

A suggested crontab for Tuesday mornings, computing the NFL week from the
season start:

```
0 9 * * 2  cd /app && python -m web.tasks $(python -c "
from datetime import date
print(max(1, min(18, (date.today() - date(2025, 9, 2)).days // 7 + 1)))")
```
