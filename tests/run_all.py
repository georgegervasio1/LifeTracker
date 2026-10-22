"""Run every offline test: python tests/run_all.py  (needs backend/requirements.txt and pytest installed).
Tests that need personal fixtures look in $LT_PRIVATE (default ../lifetracker-private, then the Claude project's
shared folder) and skip if absent. Also checks frontend JS syntax (node) and, when the private kit has
privacy-terms.txt (one term per line), that no tracked file contains a personal term."""
import os, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.environ.get("LT_PRIVATE") or next((p for p in (os.path.join(HERE, "..", "..", "lifetracker-private"),
    "/mnt/project-files/lifetracker-private") if os.path.isdir(p)), os.path.join(HERE, "..", "..", "lifetracker-private"))
dirs = [HERE] + ([os.path.join(PRIVATE, "tests")] if os.path.isdir(os.path.join(PRIVATE, "tests")) else [])
names = [(d, f) for d in dirs for f in sorted(os.listdir(d)) if f.startswith("test_") and f.endswith(".py")]
env = {**os.environ, "LT_TESTS": HERE, "LT_PRIVATE": os.path.abspath(PRIVATE)}
print(f"private tests: {'found' if len(dirs) > 1 else 'not found (set LT_PRIVATE to run them)'}")
failed = []
for d, n in names:
    r = subprocess.run([sys.executable, os.path.join(d, n)], capture_output=True, text=True, env=env)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {n:28s} {last}")
    if r.returncode:
        failed.append(n); print(r.stdout[-1500:], r.stderr[-1500:])
# backend/tests: pytest suite (ledger, finance, taxes). UBS PDF tests need LIFETRACKER_FIXTURES, else skip.
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=os.path.join(HERE, "..", "backend"), capture_output=True, text=True)
print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {'backend/tests (pytest)':28s} {(r.stdout.strip().splitlines() or [''])[-1]}")
if r.returncode:
    failed.append("backend/tests"); print(r.stdout[-3000:], r.stderr[-1500:])
# frontend: syntax check every script (catches a broken app.js before Pages serves it)
ROOT = os.path.join(HERE, "..")
if shutil.which("node"):
    for n in sorted(f for f in os.listdir(os.path.join(ROOT, "frontend")) if f.endswith(".js")):
        r = subprocess.run(["node", "--check", os.path.join(ROOT, "frontend", n)], capture_output=True, text=True)
        print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {'node --check ' + n:28s}")
        if r.returncode:
            failed.append(n); print(r.stderr[-1500:])
# privacy: the repo is public, so personal terms listed in the private kit must not appear in tracked files
terms_file = os.path.join(PRIVATE, "privacy-terms.txt")
if os.path.exists(terms_file):
    terms = [t.strip().lower() for t in open(terms_file) if t.strip() and not t.startswith("#")]
    hits = []
    for f in subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines():
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8").read().lower()
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{f}: {t}" for t in terms if t in text]
    print(f"{'FAIL' if hits else 'ok  '} {'privacy scan':28s} {len(terms)} terms")
    if hits:
        failed.append("privacy scan"); print("\n".join(hits))
sys.exit(1 if failed else 0)
