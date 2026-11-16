# Gold Price Drop Alert Agent

An autonomous AI agent that watches India's official gold benchmark rate (IBJA) twice every business day and sends a Telegram or WhatsApp alert to my family when the price drops meaningfully. A 9:30 AM early hint warns when global gold fell overnight. It runs in the cloud on GitHub Actions at ₹0 cost.

```
*Gold Price Dropped* (IBJA rate)
16 Nov 2026, AM rate (about 12:05 PM)

22K: Rs 12,309/g  (previous Rs 12,440)
     Down Rs 131 (-1.1%)
24K: Rs 13,438/g  (previous Rs 13,581)
     Down Rs 143 (-1.1%)

IBJA benchmark, before GST and making charges.
Info only, not buying advice. Confirm the rate at your jeweller.
```

## How it works

| Time (IST, Mon-Fri) | What runs |
|---|---|
| 9:30 AM | Early hint: global gold fell 1%+ in its latest session (in rupees). Not official, clearly labelled |
| 12:20 PM | Check the IBJA AM rate (published about 12:05 PM) |
| 5:20 PM | Check the IBJA PM rate (published about 5:05 PM) |

```
GitHub Actions (12:20 PM & 5:20 PM IST, Mon-Fri)
  -> Fetch official IBJA rate (ibjarates.com) + global gold price (cross-check only)
  -> Save to SQLite (price history, alert log)
  -> Drop rules in plain Python (exact maths)
       no drop -> stop, send nothing
  -> AI agent (Groq, Gemini as backup) with 3 read-only tools:
       get_rate_freshness, get_price_history, get_global_cross_check
       verifies the drop and writes the message as strict JSON
  -> Validator: every number must match the database; no advice, no links
       fails or LLM down -> fixed template message instead
  -> WhatsApp (CallMeBot or Whatabot) or Telegram, chosen per person: drop alert to admin + family
Any error at any step -> admin note to Lohith only
```

**Design choices:**
- **Hybrid design.** Plain code does all the maths. The LLM does the judgement and the wording, and it is never trusted with numbers.
- **One agent on purpose.** The task is small and linear; more agents would add cost and failure points with no benefit.
- **Least privilege.** The agent's tools are read-only. It cannot send messages, and it never sees phone numbers or keys.
- **Graceful degradation.** If Groq fails, Gemini is tried, then a template. A real drop is never lost.
- **Trusted data only.** IBJA is India's benchmark rate; RBI uses it for sovereign gold bonds. The global price is only a cross-check and is never shown.

### Alert rules (`config.yaml`)

| Trigger | Rule |
|---|---|
| Daily drop | 22K falls 1% or more against the previous IBJA rate |
| 30-day low | 22K is the lowest rate of the last 30 days |
| Below monthly high | 22K is 3% or more below the 30-day high |

There are also rules to prevent spam. Each rate is alerted at most once, and there are at most 2 alerts a day; the second needs a further 1% fall. The 30-day triggers only repeat on a new low. Nothing is sent on a price rise. A move of more than 5% is treated as bad data. If IBJA and the global price disagree by more than 2 percentage points, the alert is held.

### Languages

Each contact can choose `"language": "en"` (English, default) or `"te"` (Telugu). The AI writes the English alert, and the validator checks it. Telugu readers get a fixed, reviewed Telugu template filled with the same database numbers, including the reason line and a note that IBJA is the official Indian bullion market price. That way they never see an unchecked AI translation. Admin notes are always in English.

### Who gets what

| Message | Admin (Lohith) | Family |
|---|---|---|
| Gold price drop alert | Yes | Yes |
| Errors, held alerts, LLM fallbacks, weekly health note | Yes | Never |

This rule is enforced in code. `Router.send_admin()` can only reach the admin, and tests check that no error path reaches the family.

## Project structure

```
src/goldbot/
  fetchers/ibja.py          official IBJA page parser
  fetchers/global_price.py  global gold price (cross-check)
  storage.py                SQLite: rates, quotes, alerts, run log
  rules.py                  drop detection + cross-check maths
  agent/                    agent loop, prompts, read-only tools
  validator.py              checks the agent's message
  messages.py               alert template + admin notes
  notifier/                 CallMeBot, Whatabot, Telegram senders + admin/family router
  main.py                   one full check
tests/                      65 unit + end-to-end tests (fake LLM), live eval script
.github/workflows/          scheduled check, tests + secret scan
```

## Setup

### Phase 0: free accounts (about 20 minutes)

| What | Where | Gives you |
|---|---|---|
| GitHub account with 2FA on | github.com | runs the bot for free |
| Gemini API key | aistudio.google.com, "Get API key" | `GEMINI_API_KEY` |
| Groq API key (backup) | console.groq.com, "API Keys" | `GROQ_API_KEY` |
| API Ninjas key | api-ninjas.com, free account | `API_NINJAS_KEY` |
| **Messaging: pick one or mix** | | |
| Telegram bot (most reliable) | Telegram app: message **@BotFather**, send `/newbot`, follow the steps | `TELEGRAM_BOT_TOKEN` |
| CallMeBot (WhatsApp) | callmebot.com WhatsApp page: save their number, send "I allow callmebot to send me messages" | your CallMeBot `apikey` |
| Whatabot (WhatsApp) | whatabot.io: save their number, send "I allow whatabot to send me messages" | your Whatabot `apikey` |

Never paste keys into a chat, an issue or a commit. They only go into `.env` (on your laptop) or GitHub Secrets.

### Phase 1: run it locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
python -m pytest                       # 65 tests, no keys needed
```

Dry run on the saved IBJA page. This prints messages instead of sending them:

```bash
# Windows PowerShell: $env:PYTHONPATH="src"      Mac/Linux: export PYTHONPATH=src
python -m goldbot --dry-run --db test.db --html-file tests/fixtures/ibja_2026-11-16_am.html --now 2026-11-16T12:20
```

Dry run on the live IBJA page:

```bash
python -m goldbot --dry-run --db test.db
```

### Phase 2: messaging test (to you only)

Copy `.env.example` to `.env` and fill in `ADMIN_CONTACT`. Keep `FAMILY_CONTACTS=[]`. Each contact picks its own `channel`: `telegram`, `callmebot` or `whatabot`. For example, you could use Telegram and your family WhatsApp.

**If you use Telegram:** put `TELEGRAM_BOT_TOKEN` in `.env`, open your new bot in Telegram, and tap **Start**. Then get your chat id:

```bash
python -m goldbot --telegram-setup
```

Put that number in `ADMIN_CONTACT` as `"chat_id"`. Then send the test note:

```bash
python -m goldbot --test-message
```

You should get "Test note: the gold bot can reach you."

### Phase 3: the agent

Add `GEMINI_API_KEY` and `GROQ_API_KEY` to `.env`, then run the live evaluation:

```bash
python tests/eval_live.py
```

It checks 4 scenarios against the real LLM: a real drop, a contradicting global price, cross-check unavailable, and a stale rate. All 4 should pass. If a provider rejects the model name, update its `model:` in `config.yaml`. A `429 Too Many Requests` from Gemini in this test is its free-tier per-minute limit; the bot itself makes only a few calls a day.

### Phase 4: run it on GitHub

1. Create a repository and push this folder. `.env` is git-ignored; check with `git status` that it is not listed.
2. In the repo, go to **Settings > Secrets and variables > Actions > New repository secret** and add: `ADMIN_CONTACT`, `FAMILY_CONTACTS` (`[]` for now), `TELEGRAM_BOT_TOKEN` (if used), `GEMINI_API_KEY`, `GROQ_API_KEY`, `API_NINJAS_KEY`.
3. In **Settings > Code security**, turn on **Secret scanning** and **Push protection**.
4. Go to **Actions > Check gold price > Run workflow**. Run `test-message` first, then `dry-run`.
5. Pin the actions in both workflow files to full commit SHAs (marked `TODO(Phase 4)`).

From then on it runs by itself at 9:30 AM (hint), 12:20 PM and 5:20 PM IST, Monday to Friday.

### Phase 5: add the family

Each family member activates CallMeBot from their own phone. Then update the `FAMILY_CONTACTS` secret:

```json
[{"name": "Amma", "phone": "+91XXXXXXXXXX", "apikey": "..."}, {"name": "Nanna", "phone": "+91XXXXXXXXXX", "apikey": "..."}]
```

Your own number must not be in this list, because you already get every alert as admin.

## Maintenance

- **Holidays.** Once a year, copy IBJA's holiday list (the PDF on ibjarates.com) into `holidays:` in `config.yaml`. On a holiday that is missing from the list, you get one admin note saying no new rate was found.
- **IBJA page change.** If IBJA redesigns its page, the parser stops and you get an admin note. The family never gets a wrong price.
- **Data use.** The public IBJA page is read twice a day for personal, non-commercial alerts, and the site has no robots.txt restrictions. IBJA asks anyone using its rates for valuation, pricing or financial products to subscribe to its official API, so this bot must stay personal.

## Security summary

- Secrets live only in GitHub Secrets or a local `.env`. Neither is ever committed: `.gitignore`, gitleaks in CI and pre-commit, and GitHub push protection all guard against it.
- Logs mask phone numbers and remove every registered key.
- The database stores public prices and alert times only. No names, numbers or keys.
- Dependencies are pinned with hashes (`--require-hashes`). Dependabot alerts are on.
- Workflows use minimal permissions. The only write permission is on the check job, to save price history.
- Prompts contain only public prices. Agent output must pass a JSON schema and the validator.
- The messaging services (CallMeBot, Whatabot, Telegram) see the message text and recipient numbers or chat ids, so messages contain only public price information.
