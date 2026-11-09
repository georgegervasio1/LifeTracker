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
  **Plaid is the only source once linked:** `policy()` reads `fin_meta/plaid_links` (written on every sync) and the
  `items`; when a Plaid account is live in the server's `PLAID_ENV`, only Plaid records tagged with that env (and, for a
  bank linked twice, the newest connection's copy) count. Statement PDFs, CSVs, Venmo, manual balances and sandbox
  leftovers stay in Firestore but sit out of every total (`fin_settings/sources` `{mode: "all"}` brings imports back).
  `/api/fin/sources` lists connections + what's left out; `/api/fin/sources/purge` deletes it (typed confirm).
- `ubs_parser.py`: UBS statement and tax-form PDFs → dicts (pages assigned by footer account code; rotated pages).
- `portfolio.py`: investments and net worth from the ledger; modified-Dietz / time-weighted returns; S&P 500
  comparison from the index returns printed on UBS statements.
- `finance.py`: `/api/fin/*` (overview/net worth, cash & cards, investments, income, taxes, imports).
- `subscriptions.py`: Finances → Net Worth "Subscriptions & bills" widget (`/api/fin/subscriptions`). Pure `detect()` finds
  recurring charges in the ledger: same merchant key (refs/phones/states dropped), steady amount (exact, a price change, or a
  bill that varies within 2.5x), steady gap (weekly … yearly), ≥3 charges (2 identical for 3-monthly or longer). No merchant
  list in code. Next charge = last + usual gap; overdue past a grace period → "Possibly cancelled". User hides go in
  `fin_settings/subscriptions`.
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
- `sports.py`: Sports section. Starred teams (`sports/favorites`), team schedules (venue, local time, TV, results),
  records, standings, team search across NFL/NBA/MLB/NHL/WNBA/MLS/EPL/college, from ESPN's public site API (unofficial,
  no key; cached in memory). `/api/sports/calendar.ics?teams=nfl:13,...` is public (public schedules only) so it can be
  downloaded or subscribed to (webcal). Frontend in `frontend/teams.js`.
- `fantasy.py`: Sports → Fantasy Football. Reads the user's ESPN league from the unofficial
  `lm-api-reads.fantasy.espn.com` endpoint (views mTeam/mRoster/mMatchup/mMatchupScore/mSettings/mStandings/mStatus).
  Private leagues need the `espn_s2` + `SWID` cookies the user pastes once (ESPN sign-in can't be automated); stored in
  `integrations/espn_fantasy` as `{leagues: [...], active}` (old one-league docs still read), never returned to the
  browser; a second league reuses the saved cookies (same ESPN account). "My team" is found from SWID. League-mate data
  is read live, never stored or committed. Connecting is blocked in the demo.
- `fantasy_trades.py`: trade ideas. Prices every roster with FantasyCalc's public redraft values
  (`api.fantasycalc.com/values/current`, params from the league: numQbs, numTeams, ppr; matched by `espnId`, then name +
  position; cached 6 h). Team strength = best legal starting lineup (league's own slots) + 15% of the top 4 bench.
  A suggestion is within 12% on value, raises your strength and doesn't lower theirs (1-2 players a side).
  `POST /api/fantasy/trades` grades a trade the user builds.
- `food.py`: Health → Food diary (MyFitnessPal-style): search USDA FoodData Central (`FDC_API_KEY`, else DEMO_KEY)
  + Open Food Facts, normalized per 100 g; entries store nutrients when logged; custom foods (per serving), quick
  add, copy a meal, recent foods, goals (blank until the user sets them), 30-day history.
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
- `demo.py` + `demo_seed.py` + `demo_store.py`: **demo profile** ("Try the demo" on the sign-in page, or `?demo` in the URL).
  The browser sends `Authorization: Demo <random id>`; the request runs as uid `demo` on a private in-memory copy of
  made-up data (`Router` stands in for the Firestore client, so demo requests never reach Firestore). Plaid, WHOOP/Strava/
  Gmail connections and every Claude call answer 403 in the demo. The owner can copy their course schedule (no grades,
  notes or P/F choices) into the demo from Education → Planner → "Demo profile…" (`/api/demo/schedule`, stored in
  Firestore `demo/education`). `DEMO_MODE=0` turns the demo off. Seed data is made up, never the owner's.
- `travel.py`: Travel → Commute. Capital Bikeshare: live bikes / e-bikes / docks at saved stations from the public GBFS
  feed (`BIKESHARE_GBFS`, cached), ride history parsed from the "Ride Receipt" emails in Gmail (stations, times, line items,
  "Saved this trip"), membership payoff (savings vs the price the user enters), miles = straight line between stations
  (receipts have no route), bank/card bikeshare charges from the ledger. Metro: WMATA has no API, so trips come from the
  SmarTrip usage-history CSV (`parse_smartrip_csv`, columns matched by keyword; re-imports only add new rows) plus the
  monthly transit benefit the user enters (`fin_settings` `transit_benefit`) and WMATA charges in the ledger.
  `/api/travel/commute` merges both by month.
- `apartments.py`: Travel → Apartments. Listings from the weekly digest (`/api/apartments/import`, owner sign-in or
  `X-Ingest-Key` = env `APARTMENTS_INGEST_KEY`), keyed by URL (query dropped) or address. User status / notes / rank /
  rating (1-5) survive later digests; `private` is sticky and private spots never go in the KML export; a rent change adds
  `price_history`. Photo URLs are hotlinked and copies are downloaded once into `apartment_photos` (base64, ≤700 KB; the
  frontend falls back to `/api/apartments/photo/<id>` when the hotlink fails). Digest docs keep subject + body.
  `/api/apartments/export.kml` for Google My Maps. Frontend `frontend/apartments.js` (Zillow-style cards, detail dialog).
- `trips.py`: Travel → Trips. Trips with dates, places, travelers, notes and bookings (flight / stay / car / other);
  prefilled Google Flights, Airbnb and Google Hotels searches and airline links (no airline/Flights booking API exists for
  personal apps). Home airport + airlines in `travel/trip_settings`. Frontend `frontend/trips.js`.
- `earnings.py`: paychecks (feeds Income/Taxes). Pub 15-T (2026) Worksheet 1A withholding, SS/Medicare, Illinois IL-700-T,
  year-end federal / IL nonresident / DC resident with credit for other-state tax. `SEED` holds the owner's past
  summer job (no employer name); payroll deposits are matched from the bank feed. `/api/taxes` builds the
  Taxes tab (where every dollar went, marginal rate via +$100 re-run, settle-up, bracket ladder, bank tax activity).
  Tax tables live in `TAX[2026]` with sources in comments; add a new year there.

### Firestore (`users/{uid}/…`)
`transactions` (ledger), `items` (Plaid), `inv_snapshots`, `inv_activity`, `holdings` (legacy), `health_days`, `workouts`, `integrations/whoop`, `oauth_states`,
`training` (state/template/sessions), `education/profile`, `edu_courses`, `edu_syllabi`, `edu_syllabus_files`,
`earnings/settings`, `paychecks`, `integrations/strava`, `strava_activities`, `race_plans`, `food_log/{date}`, `food_custom`, `food/goals`, `sports/favorites`,
`travel/settings`, `bike_rides`, `metro_trips`, `apartments`, `apartment_digests`, `apartment_photos`, `trips`, `travel/trip_settings`.

### Frontend map (`frontend/app.js`)
Hash routes `#/section/tab`, `SECTIONS` config at the top. Finances: Net Worth, Cash & Cards, Investments,
Income, Taxes, Accounts & Imports (chat on every tab). Health opens on a dashboard of five photo cards (`HEALTH` config: Weight Lifting, Running, Sleep & Recovery, Sports,
Food; Unsplash photos hotlinked with credit); `#/health/<area>/<tab>` maps to a panel; old `#/health/<tab>` links are aliased.
The card photo morphs into the area banner with a View Transition (`viewSwap` from the shared `motion.js`).
Calendar and Connections hang off the dashboard.
Sports: a dashboard of three photo cards like Travel (`sports.js`): Teams (`teams.js`: My Teams, Schedule calendar,
Standings, Find Teams), Fantasy Football (`fantasy.js`: Matchup, Roster, Trades, Standings, League; league switcher), Betting (in development, plan only).
Travel opens on a dashboard of three photo cards like Health (Apartments, Commute, Trips); `#/travel/<area>/<tab>`;
`travel.js` (loaded on first visit) draws its own heroes and tabs, with `apartments.js` and `trips.js`.
Education: Overview, Syllabi, Grades & GPA, Degree Audit, Planner, Calendar. Goals: `goals.js` (loaded on first
visit; widgets, detail, editor, library, coach drawer).
Design tokens on `:root` in `styles.css` (`--accent`, `--pos`, `--neg`, `--warn`, `--exam`, `--done`, …).

### Environment & secrets (Cloud Run)
Secrets: `plaid-client-id`, `plaid-secret`, `allowed-email`, `anthropic-api-key`, `whoop-client-id`,
`whoop-client-secret`, `strava-client-id`, `strava-client-secret`. Env: `PLAID_ENV` (sandbox until Plaid production is approved), `CLAUDE_MODEL`,
`WHOOP_REDIRECT_URI`, `STRAVA_REDIRECT_URI`, `FDC_API_KEY` (optional), `FRONTEND_URL`, `FRONTEND_ORIGIN`. Local dev: `DEV_SKIP_AUTH=1`.

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
