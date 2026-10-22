# LifeTracker: working notes for Claude

Personal "life OS" web app for one owner today (multi-user later): **Finances, Health, Education, Goals**.
This file is the technical handoff. Personal context (the owner's data, open decisions) lives in the private
handoff doc in the Claude project, not here: **this repo is public**.

## Rules (read first)
- **Public repo: never commit personal data.** No merchant names, employer names, account numbers, statements,
  grades, pay amounts, or CSV/JSON exports. Personal facts are read from Firestore at runtime (e.g. the employer
  name on Finances → Summer 2026 comes from bank deposits). `.gitignore` blocks `*.csv`, `.env*`, keys.
- **Never commit secrets.** Secrets live in Google Secret Manager; config.js only holds public Firebase web config.
- **Never invent numbers.** Blank stays blank; label assumptions in the UI.
- **Run the tests before every push:** `python tests/run_all.py` (see Testing).
- **Pushing:** follow the push rule in the project instructions. Commit messages: imperative summary line,
  a few bullets, and the attribution trailer the session provides.
- Design: minimal Apple-style. No emoji or decorative icons. Light and dark (data-theme), phone-width checked.

## Architecture
| Piece | Where | Notes |
|---|---|---|
| Frontend | `frontend/` → GitHub Pages: https://georgegervasio1.github.io/LifeTracker/ | Static `index.html` + `app.js` (ES module) + `styles.css`. Deployed by `.github/workflows/pages.yml` on push to `main` (frontend changes). |
| Backend | `backend/` → Cloud Run `lifetracker-api`, region `us-east4`, project `lifetracker2001` | Flask. URL in `frontend/config.js` (`API_BASE`). |
| Deploy | `cloudbuild.yaml` + root `Dockerfile` | Cloud Build trigger on push to `main` builds, pushes to Artifact Registry and deploys. Check Cloud Build → History (us-east4). |
| Data | Firestore, `users/{uid}/...` | See collections below. |
| Auth | Firebase Google sign-in (modular SDK 10.12.0) | Backend `require_owner` checks the ID token + `ALLOWED_EMAIL`. |

Libraries (cdnjs, pinned): Chart.js 4.4.1, marked, DOMPurify, Sortable 1.15.2; Plaid Link script.

### Backend modules (`backend/`)
Each feature module exposes `init(app, db, firestore, require_owner, user_ref)` and is registered in `main.py`.
- `main.py`: Plaid link/sync, transactions, `/api/summary` (transfer categories excluded; Refund reduces spend;
  money **in** is a **negative** amount), bank/card CSV import (`/api/import/csv`), Venmo CSV, holdings, `/health`.
- `categorize.py`: generic merchant → category rules only.
- `assistant.py`: `/api/chat`, Claude tool loop over the finance data (model from `CLAUDE_MODEL`).
- `health.py`: WHOOP OAuth v2 (rotating refresh tokens, 8-char state), daily recovery/sleep/strain, workouts,
  calendar. WHOOP workouts merged into a training session are hidden (`merged_into`).
- `training.py`: workout planner (multi-week template, Mon–Sun board + tray, %-of-max targets), live sessions,
  history with PRs (Epley e1RM; PR = beats all *earlier* sessions on top weight or e1RM), WHOOP merge, import.
- `education.py`: GULC grade points, GPA engine (half-up `r2`), needed-average solver + grade plan, degree audit,
  planner conflicts, **P/F option budget** (7 credits, one course per semester, counted even if the letter grade
  is kept), **academic calendar** (GULC 2026–27 key dates) + **finals** (Registrar's Fall 2026 schedule matched to
  enrolled courses; ambiguous sections offer a picker; `exam_type: "Paper"` shows "Paper due"), merge import.
- `earnings.py`: paychecks + taxes. Pub 15-T (2026) Worksheet 1A withholding, SS/Medicare, Illinois IL-700-T,
  year-end federal / IL nonresident / DC resident with credit for other-state tax. `SEED` holds the owner's past
  summer job (no employer name); payroll deposits are matched from the bank feed. `/api/taxes` builds the
  Taxes tab (where every dollar went, marginal rate via +$100 re-run, settle-up, bracket ladder, bank tax activity).
  Tax tables live in `TAX[2026]` with sources in comments; add a new year there.

### Firestore (`users/{uid}/…`)
`transactions`, `items` (Plaid), `holdings`, `health_days`, `workouts`, `integrations/whoop`, `oauth_states`,
`training` (state/template/sessions), `education/profile`, `edu_courses`, `earnings/settings`, `paychecks`.

### Frontend map (`frontend/app.js`)
Hash routes `#/section/tab`, `SECTIONS` config at the top. Finances: Overview, Transactions, Investments,
Summer 2026 (`paychecks`), Taxes, Assistant, Accounts. Health: Overview, Planner, History, Calendar, Connect.
Education: Overview, Grades & GPA, Degree Audit, Planner, Calendar. Goals: placeholder.
Design tokens on `:root` in `styles.css` (`--accent`, `--pos`, `--neg`, `--warn`, `--exam`, `--done`, …).

### Environment & secrets (Cloud Run)
Secrets: `plaid-client-id`, `plaid-secret`, `allowed-email`, `anthropic-api-key`, `whoop-client-id`,
`whoop-client-secret`. Env: `PLAID_ENV` (sandbox until Plaid production is approved), `CLAUDE_MODEL`,
`WHOOP_REDIRECT_URI`, `FRONTEND_URL`, `FRONTEND_ORIGIN`. Local dev: `DEV_SKIP_AUTH=1`.

## Testing
`pip install -r backend/requirements.txt` then `python tests/run_all.py`.
Tests run offline against an in-memory Firestore (`tests/test_health.py` holds the shared fake) with no network.
Tests that need the owner's real data live in the private kit (`lifetracker-private/tests` + fixtures); point
`LT_PRIVATE` at it or place it next to the repo as `../lifetracker-private`. Without it they print SKIP.

For UI changes, render with Playwright (Chromium is preinstalled in cloud sessions) using stubbed Firebase and
API routes, and check light, dark and 390px phone width before pushing.

## Deploy checks
- Frontend: Pages updates ~1 min after push.
- Backend: Cloud Build → History shows Build → Push → Deploy (3–5 min). If a step fails: Build = Dockerfile/path,
  Push = Artifact Registry permission, Deploy = `run.admin` / `iam.serviceAccountUser` on the compute SA.
- The service keeps its env vars and secrets across deploys (cloudbuild only swaps the image).
