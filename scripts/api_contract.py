"""Check that frontend API calls match declared backend routes."""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "backend" / "app" / "api"
BACKEND_MAIN = ROOT / "backend" / "app" / "main.py"
FRONTEND_SRC = ROOT / "frontend" / "src"
SMOKE_TEST = ROOT / "scripts" / "smoke_test.py"

ROUTER_RE = re.compile(r"(\w+)\s*=\s*APIRouter\(\s*prefix=[\"']([^\"']*)[\"']")
ROUTE_RE = re.compile(r"@(\w+)\.(get|post|delete|put|patch)\(\s*[\"']([^\"']*)[\"']")
WEBSOCKET_ROUTE_RE = re.compile(r"@app\.websocket\(\s*([`'\"])(.+?)\1")
AXIOS_RE = re.compile(r"\bapi\.(get|post|delete|put|patch)\(\s*([`'\"])(.+?)\2", re.DOTALL)
FETCH_RE = re.compile(r"\bfetch\(\s*([`'\"])(/api/.+?)\1\s*(?:,\s*(\{.*?\})\s*)?\)", re.DOTALL)
FETCH_METHOD_RE = re.compile(r"\bmethod\s*:\s*([`'\"])(get|post|delete|put|patch)\1", re.IGNORECASE)
MEDIA_URL_RE = re.compile(r"=>\s*([`'\"])(/api/media/image/.+?)\1")
WEBSOCKET_RE = re.compile(r"\bnew\s+WebSocket\(\s*([`'\"])(.+?)\1", re.DOTALL)
SMOKE_REQUEST_RE = re.compile(
    r"\bclient\.request\(\s*([`'\"])(get|post|delete|put|patch)\1\s*,\s*f?([`'\"])(.+?)\3",
    re.IGNORECASE | re.DOTALL,
)
SMOKE_STREAM_RE = re.compile(r"\bclient\.stream_events\(\s*f?([`'\"])(.+?)\1", re.DOTALL)


def clean_path(path: str) -> str:
    path = path.strip()
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "{param}", path)
    path = re.sub(r"\{[^}:]+?\}", "{param}", path)
    path = re.sub(r"\s+", "", path)
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def route_key(method: str, path: str) -> tuple[str, str]:
    normalized = clean_path(path)
    normalized = re.sub(r"\{[^}/]+\}", "{param}", normalized)
    return method.upper(), normalized


def fetch_method(options: str | None) -> str:
    if not options:
        return "GET"
    match = FETCH_METHOD_RE.search(options)
    return match.group(2).upper() if match else "GET"


def collect_backend_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for path in sorted(API_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        router_prefixes = {name: prefix for name, prefix in ROUTER_RE.findall(text)}
        for router_name, method, suffix in ROUTE_RE.findall(text):
            prefix = router_prefixes.get(router_name)
            if prefix is None:
                continue
            base = "" if router_name == "open_router" else "/api"
            routes.add(route_key(method, f"{base}{prefix}{suffix}"))
    main_text = BACKEND_MAIN.read_text(encoding="utf-8")
    for _quote, path in WEBSOCKET_ROUTE_RE.findall(main_text):
        routes.add(route_key("WS", path))
    return routes


def collect_frontend_calls() -> set[tuple[str, str]]:
    calls: set[tuple[str, str]] = set()
    source_files = [
        *FRONTEND_SRC.rglob("*.ts"),
        *FRONTEND_SRC.rglob("*.vue"),
    ]

    for source_file in sorted(source_files):
        text = source_file.read_text(encoding="utf-8")

        for method, _quote, path in AXIOS_RE.findall(text):
            calls.add(route_key(method, f"/api{path}"))

        for _quote, path, options in FETCH_RE.findall(text):
            calls.add(route_key(fetch_method(options), path))

        for _quote, path in MEDIA_URL_RE.findall(text):
            calls.add(route_key("GET", path))

        for _quote, path in WEBSOCKET_RE.findall(text):
            if "/ws" in path:
                calls.add(route_key("WS", "/ws"))

    return calls


def collect_smoke_calls() -> set[tuple[str, str]]:
    text = SMOKE_TEST.read_text(encoding="utf-8")
    calls: set[tuple[str, str]] = set()

    for _quote, method, _path_quote, path in SMOKE_REQUEST_RE.findall(text):
        calls.add(route_key(method, path))

    for _quote, path in SMOKE_STREAM_RE.findall(text):
        calls.add(route_key("POST", path))

    if "websocket_url(base_url)" in text:
        calls.add(route_key("WS", "/ws"))

    return calls


def main() -> int:
    backend_routes = collect_backend_routes()
    frontend_calls = collect_frontend_calls()
    smoke_calls = collect_smoke_calls()

    missing = sorted(frontend_calls - backend_routes)
    if missing:
        print("API contract check failed. Frontend calls missing backend routes:")
        for method, path in missing:
            print(f"- {method} {path}")
        print("\nKnown backend routes:")
        for method, path in sorted(backend_routes):
            print(f"- {method} {path}")
        return 1

    unverified = sorted(backend_routes - frontend_calls - smoke_calls)
    if unverified:
        print("API contract check failed. Backend routes are neither used by frontend nor covered by smoke tests:")
        for method, path in unverified:
            print(f"- {method} {path}")
        return 1

    print(
        "API contract check passed: "
        f"{len(frontend_calls)} frontend calls matched, "
        f"{len(smoke_calls)} smoke calls covered"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
