# LifeTracker

Personal finance dashboard: Chase (bank + credit cards) and UBS (investments) via Plaid, plus Venmo CSV imports.

- **Frontend:** static HTML/JS on GitHub Pages (`/frontend`)
- **Backend:** Flask on Google Cloud Run (`/backend`), Firestore for storage
- **Auth:** Firebase Google Sign-In; the API only answers to `ALLOWED_EMAIL`

> ⚠️ Keep this repo **private**. Never commit `.env`, CSVs, or keys — `.gitignore` blocks them.

---

## 1. Local dev (sandbox data)

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in Plaid sandbox keys, set DEV_SKIP_AUTH=1
gcloud auth application-default login   # lets Firestore work locally
python main.py                # http://localhost:8080/health
```

Frontend: set `API_BASE` in `frontend/config.js` to `http://localhost:8080`, then serve the folder
(e.g. VS Code Live Server on port 5500). Plaid sandbox login: `user_good` / `pass_good`.

## 2. One-time Google Cloud setup

```bash
PROJECT=lifetracker-XXXX        # your new project id
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com firestore.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com cloudscheduler.googleapis.com
gcloud firestore databases create --location=nam5

# Secrets
printf "YOUR_CLIENT_ID" | gcloud secrets create plaid-client-id --data-file=-
printf "YOUR_SECRET"    | gcloud secrets create plaid-secret    --data-file=-
printf "you@georgetown.edu" | gcloud secrets create allowed-email --data-file=-
```

Then in the Firebase console, add Firebase to the same project, enable **Authentication → Google**,
add `georgegervasio1.github.io` under **Authorized domains**, and paste the web config into `frontend/config.js`.

## 3. GitHub → Cloud Run (keyless)

Create a Workload Identity pool + provider for this repo and a deploy service account
(roles: Cloud Run Admin, Cloud Build Editor, Service Account User, Artifact Registry Writer,
Secret Manager Secret Accessor). Add two repo secrets:

| Secret | Value |
|---|---|
| `GCP_WIF_PROVIDER` | `projects/<NUMBER>/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_DEPLOY_SA` | `github-deployer@<PROJECT>.iam.gserviceaccount.com` |

Push to `main` → `deploy-backend.yml` deploys the API. Copy the Cloud Run URL into `frontend/config.js`.

## 4. GitHub Pages

Settings → Pages → Source: **GitHub Actions**. Push to `main` → `pages.yml` publishes `/frontend`.
(Pages on a private repo needs GitHub Pro — free with the GitHub Student Developer Pack.)

## 5. AI assistant (Claude)

The **🤖 AI Assistant** tab sends your question to `POST /api/chat`. The backend calls the Claude API with
three read-only tools (`get_spending_summary`, `search_transactions`, `get_investments`) scoped to your own
data, so Claude looks up real numbers before answering. It cannot change data or move money.

```bash
read -s -p "Anthropic API key: " V; echo; printf %s "$V" | gcloud secrets create anthropic-api-key --data-file=-; unset V
```
Then redeploy with `ANTHROPIC_API_KEY=anthropic-api-key:latest` added to `--set-secrets`.
Model defaults to `claude-sonnet-5-5`; override with the `CLAUDE_MODEL` env var.

## 6. Health: workouts + WHOOP

Workouts are logged in **Health → Workouts** and shown on **Health → Calendar**. WHOOP adds daily recovery,
HRV, resting heart rate, strain, sleep, and WHOOP-tracked workouts (API v2, OAuth with refresh tokens).

1. Create an app at the WHOOP Developer Dashboard with redirect URL
   `https://<your-cloud-run-url>/api/whoop/callback` and scopes `read:recovery read:cycles read:sleep read:workout read:profile` (+ `offline`).
2. Store the credentials:
   ```bash
   read -s -p "WHOOP client ID: " V; echo; printf %s "$V" | gcloud secrets create whoop-client-id --data-file=-; unset V
   read -s -p "WHOOP client secret: " V; echo; printf %s "$V" | gcloud secrets create whoop-client-secret --data-file=-; unset V
   ```
3. Redeploy with `WHOOP_REDIRECT_URI` and `FRONTEND_URL` env vars and the two secrets, then **Health → Connect → Connect WHOOP**.

The first sync pulls ~6 months; later syncs pull the last 3 days (scores can change after the fact).

## 7. Education

**Education** tracks courses and grades, computes GPA with Georgetown Law's grade points (A+ 4.33 … F 0;
P/H/CR excluded), separates actual from projected grades, solves for the average needed to hit a target GPA,
audits J.D. requirements (85 credits, 54 at the Law Center, 6 experiential, PR, upperclass writing,
critical-perspectives course, 2.00 minimum), and plans upcoming terms with time-conflict detection.
Load data via **Education → Import file** (`{profile, courses}` JSON) or add courses in the Planner.

## 8. Daily auto-sync (later)

Add a Cloud Scheduler job that calls `POST /api/sync` daily. Easiest path: add a small
`/api/cron-sync` endpoint protected by Cloud Scheduler's OIDC token.

## Roadmap

- [ ] Category overrides + custom rules
- [ ] Venmo ↔ Chase dedupe (Venmo cash-outs show up in both)
- [ ] Budgets and alerts
- [ ] Recurring charge / subscription detection
- [ ] Net worth over time (cash + investments)
- [x] "Ask my finances" AI chat (Claude)
- [x] Health: workout log, calendar, WHOOP sync
- [x] Education: GPA engine, degree audit, course planner
- [ ] Education: weekly schedule, finals planner, Canvas feed
- [ ] Assistant actions: recategorize, set budgets
- [ ] "Change the app" requests → GitHub issue → Claude Code PR
