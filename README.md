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

## 5. Daily auto-sync (later)

Add a Cloud Scheduler job that calls `POST /api/sync` daily. Easiest path: add a small
`/api/cron-sync` endpoint protected by Cloud Scheduler's OIDC token.

## Roadmap

- [ ] Category overrides + custom rules
- [ ] Venmo ↔ Chase dedupe (Venmo cash-outs show up in both)
- [ ] Budgets and alerts
- [ ] Recurring charge / subscription detection
- [ ] Net worth over time (cash + investments)
- [ ] "Ask my finances" LLM chat
