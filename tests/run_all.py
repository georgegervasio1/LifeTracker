"""Run every offline test: python tests/run_all.py  (needs backend/requirements.txt installed).
Tests that need personal fixtures look in $LT_PRIVATE (default ../lifetracker-private) and skip if absent."""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.environ.get("LT_PRIVATE", os.path.join(HERE, "..", "..", "lifetracker-private"))
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
sys.exit(1 if failed else 0)
