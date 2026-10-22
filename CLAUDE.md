# LifeTracker: working notes for Claude

Personal "life OS" web app for one owner today (multi-user later): **Finances, Health, Education, Goals**.
This file is the technical handoff. Personal context (the owner's data, open decisions) lives in the private
handoff doc in the Claude project, not here: **this repo is public**.

## Rules (read first)
- **Several sessions work in parallel.** `git pull --rebase` before starting and before pushing; keep commits
  focused; if a rebase conflicts, read the other session's change and merge both intents.
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

Libraries (cdnjs, pinned): Chart.js 4.4.1, marked, DOMPurify, Sortable 1.15.2, Leaflet 1.9.4 (CARTO basemap tiles); Plaid Link script.

### Backend modules (`backend/`)
Each feature module exposes `init(app, db, firestore, require_owner, user_ref)` and is registered in `main.py`.
- `main.py`: app setup, auth, Plaid link/sync, CSV/Venmo/PDF upload routes, `/health`. Money **in** is a
  **negative** amount in transactions.
- `ledger.py`: **the finance data model.** One canonical record per real account and transaction across Plaid,
  UBS statement PDFs, Chase CSV, Venmo CSV and manual entry (`src`, canonical `account`); deterministic ids and
  cross-source matching so nothing is double counted; transfer pairing; statement reconciliation.
- `ubs_parser.py`: UBS statement and tax-form PDFs → dicts (pages assigned by footer account code; rotated pages).
- `portfolio.py`: investments and net worth from the ledger; modified-Dietz / time-weighted returns; S&P 500
  comparison from the index returns printed on UBS statements.
- `finance.py`: `/api/fin/*` (overview/net worth, cash & cards, investments, income, taxes, imports).
- `fintax.py`: the whole-year tax picture (wages + trust distribution + investment income; federal, IL
  nonresident, DC resident with IL credit; estimated payments; settings for what documents can't tell us).
- `categorize.py`: generic merchant → category rules only.
- `assistant.py`: `/api/chat`, Claude tool loop over the finance data (model from `CLAUDE_MODEL`).
- `health.py`: WHOOP OAuth v2 (rotating refresh tokens, 8-char state), daily recovery/sleep/strain, workouts,
  calendar. WHOOP workouts merged into a training session are hidden (`merged_into`).
- `training.py`: workout planner (multi-week template, Mon–Sun board + tray, %-of-max targets), live sessions,
  history with PRs (Epley e1RM; PR = beats all *earlier* sessions on top weight or e1RM), WHOOP merge, import.
- `strava.py`: Strava OAuth (scope `read,activity:read_all`), sync of activities since Jan 1 last year (list
  200/page, then details for up to 30 runs per sync: splits, best efforts, full route), Running tab data
  (`/api/running?year=`), `/api/runs` for the calendar. Rate limit 100 reads / 15 min. **The Strava API needs a paid
  Strava subscription**, so the main path is `frontend/stravaexport.js`: the owner's account-export zip is parsed in the
  browser (activities.csv + GPX/TCX tracks → route, mile splits, best efforts; FIT not parsed) and posted in batches to
  `/api/strava/import` (`source: "strava_export"`). WHOOP runs (`workouts`, type Running, with `distance_m`) fill in
  runs Strava doesn't have; the same run on both (same day, starts within 30 min) shows once.
- `raceplan.py`: race training plans (5K, 10K, half, marathon). `build_plan` is pure: Monday-based weeks, base /
  build / peak / taper / race week, ~10%/week ramp with a cutback every 4th week, long run capped by share of the
  week, paces from Daniels' VDOT (current fitness from Strava best efforts or races in the last 10 weeks; goal time
  sets goal pace; neither = effort words, no paces). Planned vs done matched to Strava runs by date.
- `syllabus.py`: Education → Syllabi: upload a syllabus (Claude extracts sessions/deadlines) or import JSON;
  deadlines feed the calendar. Originals kept in `edu_syllabus_files`.
- `education.py`: GULC grade points, GPA engine (half-up `r2`), needed-average solver + grade plan, degree audit,
  planner conflicts, **P/F option budget** (7 credits, one course per semester, counted even if the letter grade
  is kept; a completed P course counts unless marked `pf_mandatory`; `pf_prior_credits` for untracked terms), **academic calendar** (GULC 2026–27 key dates) + **finals** (Registrar's Fall 2026 schedule matched to
  enrolled courses; ambiguous sections offer a picker; `exam_type: "Paper"` shows "Paper due"), merge import.
- `goals.py`: Goals. SMART goals (`measure.kind` number or steps; `metric` links a number goal to Finances, Health or
  Education, e.g. `fin.net_worth`, `health.lift_top:<Exercise>`, `edu.gpa`), step plans with dates, pace vs a straight
  line from start to deadline, manual value log, JSON import, and `/api/goals/coach` (Claude proposes a goal through the
  `propose_goal` tool; nothing is saved until the user presses Save). `goals_library.py`: the 100-goal starter library
  (generic, no personal data; targets only where the challenge defines one).
- `earnings.py`: paychecks (feeds Income/Taxes). Pub 15-T (2026) Worksheet 1A withholding, SS/Medicare, Illinois IL-700-T,
  year-end federal / IL nonresident / DC resident with credit for other-state tax. `SEED` holds the owner's past
  summer job (no employer name); payroll deposits are matched from the bank feed. `/api/taxes` builds the
  Taxes tab (where every dollar went, marginal rate via +$100 re-run, settle-up, bracket ladder, bank tax activity).
  Tax tables live in `TAX[2026]` with sources in comments; add a new year there.

### Firestore (`users/{uid}/…`)
`transactions` (ledger), `items` (Plaid), `inv_snapshots`, `inv_activity`, `holdings` (legacy), `health_days`, `workouts`, `integrations/whoop`, `oauth_states`,
`training` (state/template/sessions), `education/profile`, `edu_courses`, `edu_syllabi`, `edu_syllabus_files`,
`earnings/settings`, `paychecks`, `integrations/strava`, `strava_activities`, `race_plans`.

### Frontend map (`frontend/app.js`)
Hash routes `#/section/tab`, `SECTIONS` config at the top. Finances: Net Worth, Cash & Cards, Investments,
Income, Taxes, Accounts & Imports (chat on every tab). Health: Overview, Planner, History, Running, Race Training, Calendar, Connect.
Education: Overview, Syllabi, Grades & GPA, Degree Audit, Planner, Calendar. Goals: `goals.js` (loaded on first
visit; widgets, detail, editor, library, coach drawer).
Design tokens on `:root` in `styles.css` (`--accent`, `--pos`, `--neg`, `--warn`, `--exam`, `--done`, …).

### Environment & secrets (Cloud Run)
Secrets: `plaid-client-id`, `plaid-secret`, `allowed-email`, `anthropic-api-key`, `whoop-client-id`,
`whoop-client-secret`, `strava-client-id`, `strava-client-secret`. Env: `PLAID_ENV` (sandbox until Plaid production is approved), `CLAUDE_MODEL`,
`WHOOP_REDIRECT_URI`, `STRAVA_REDIRECT_URI`, `FRONTEND_URL`, `FRONTEND_ORIGIN`. Local dev: `DEV_SKIP_AUTH=1`.

## Testing
`pip install -r backend/requirements.txt pytest` then `python tests/run_all.py`. It runs `tests/` (scripts) and
`backend/tests/` (pytest: ledger, finance, taxes). All offline against in-memory Firestore fakes, no network.
UBS PDF tests need `LIFETRACKER_FIXTURES` pointing at the statement PDFs (never in the repo), else they skip.
Tests that need the owner's real data live in the private kit (`lifetracker-private/tests` + fixtures); point
`LT_PRIVATE` at it or place it next to the repo as `../lifetracker-private`. Without it they print SKIP.

For UI changes, render with Playwright (Chromium is preinstalled in cloud sessions) using stubbed Firebase and
API routes, and check light, dark and 390px phone width before pushing.

## Deploy checks
- Frontend: Pages updates ~1 min after push.
- Backend: Cloud Build → History shows Build → Push → Deploy (3–5 min). If a step fails: Build = Dockerfile/path,
  Push = Artifact Registry permission, Deploy = `run.admin` / `iam.serviceAccountUser` on the compute SA.
- The service keeps its env vars and secrets across deploys (cloudbuild only swaps the image).
