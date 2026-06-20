"""Run the main WeChatAI verification suite.

Expected precondition: backend and frontend dev servers are already running.
Defaults match the local development setup used by this project:
- backend:  http://127.0.0.1:8090
- frontend: http://127.0.0.1:5175
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def run_step(name: str, cmd: list[str], cwd: Path = ROOT) -> bool:
    print(f"\n==> {name}")
    print(" ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(cwd))
    ok = completed.returncode == 0
    print(f"{'PASS' if ok else 'FAIL'} {name}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WeChatAI smoke tests and build checks.")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter to use.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8090")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5175")
    parser.add_argument("--skip-build", action="store_true", help="Skip npm run build.")
    parser.add_argument(
        "--include-heavy",
        action="store_true",
        help="Also run heavier backend checks that write exports or trigger sync.",
    )
    args = parser.parse_args()

    backend_smoke_cmd = [args.python, "scripts/smoke_test.py", "--base-url", args.backend_url]
    if args.include_heavy:
        backend_smoke_cmd.append("--include-heavy")

    steps: list[tuple[str, list[str], Path]] = [
        (
            "source health",
            [args.python, "scripts/source_health.py"],
            ROOT,
        ),
        (
            "python compile",
            [
                args.python,
                "-m",
                "compileall",
                "-q",
                "backend/app",
                "scripts",
            ],
            ROOT,
        ),
        (
            "backend smoke",
            backend_smoke_cmd,
            ROOT,
        ),
        (
            "frontend smoke",
            [args.python, "scripts/frontend_smoke.py", "--frontend-url", args.frontend_url],
            ROOT,
        ),
    ]

    if not args.skip_build:
        steps.append(("frontend build", ["npm.cmd", "run", "build"], FRONTEND))

    failed = []
    for name, cmd, cwd in steps:
        if not run_step(name, cmd, cwd):
            failed.append(name)

    print("\n==> Summary")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1

    print(f"PASS: {len(steps)}/{len(steps)} verification steps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
