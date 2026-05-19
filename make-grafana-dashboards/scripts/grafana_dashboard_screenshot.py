#!/usr/bin/env python3
"""Capture authenticated Grafana dashboard screenshots with Playwright.

This helper is intentionally generic and has no hard-coded Grafana host.
It supports agent-friendly and human workflows:

  profiles     - list saved browser profiles
  login-start  - open a headed browser in the background for manual SSO login
  login-finish - close the browser opened by login-start after the user logged in
  login        - blocking human-oriented login flow
  capture      - reuse a profile to capture a dashboard screenshot
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

CACHE_ROOT = Path.home() / ".cache" / "grafana-dashboard-screenshot"
FINISH_FILE = ".login-finish"
SESSION_FILE = ".login-session.json"
SESSION_LOG = ".login-session.log"


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - human-facing error path
        print(
            "Python Playwright is not installed.\n\n"
            "Recommended isolated setup:\n"
            "  python3 -m venv ~/.cache/grafana-dashboard-screenshot/venv\n"
            "  ~/.cache/grafana-dashboard-screenshot/venv/bin/python -m pip install playwright\n"
            "  ~/.cache/grafana-dashboard-screenshot/venv/bin/python -m playwright install chromium\n",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return sync_playwright, PlaywrightTimeoutError


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0]
    value = re.sub(r"[^a-z0-9_.-]+", "-", value)
    value = value.strip("-._")
    return value or "grafana"


def profile_dir(url: str, profile_name: str | None, profile_dir_arg: str | None = None) -> Path:
    if profile_dir_arg:
        return Path(profile_dir_arg).expanduser().resolve()
    name = profile_name or slugify(urlparse(url).netloc or url)
    return CACHE_ROOT / name


def profile_name_for_url(url: str) -> str:
    return slugify(urlparse(url).netloc or url)


def parse_viewport(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", value.strip())
    if not match:
        raise argparse.ArgumentTypeError("viewport must be WIDTHxHEIGHT, e.g. 1800x1400")
    return int(match.group(1)), int(match.group(2))


def common_browser_args(args: argparse.Namespace) -> dict:
    width, height = args.viewport
    return {
        "headless": not getattr(args, "headed", False),
        "viewport": {"width": width, "height": height},
        "device_scale_factor": args.device_scale_factor,
    }


def list_profile_dirs() -> list[Path]:
    if not CACHE_ROOT.exists():
        return []
    ignored = {"venv", "_tmp", "tmp"}
    return sorted(
        p
        for p in CACHE_ROOT.iterdir()
        if p.is_dir() and p.name not in ignored and not p.name.startswith(".")
    )


def find_profile_for_url(url: str) -> Path | None:
    host_slug = profile_name_for_url(url)
    candidates = list_profile_dirs()
    exact = [p for p in candidates if p.name == host_slug]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [p for p in candidates if host_slug in p.name or p.name in host_slug]
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def print_profiles(_: argparse.Namespace) -> int:
    profiles = list_profile_dirs()
    if not profiles:
        print(f"No profiles found under {CACHE_ROOT}")
        return 0
    for p in profiles:
        session = p / SESSION_FILE
        marker = ""
        if session.exists():
            marker = " active-login-session"
        print(f"{p.name}\t{p}{marker}")
    return 0


def session_paths(user_data_dir: Path) -> tuple[Path, Path, Path]:
    return user_data_dir / FINISH_FILE, user_data_dir / SESSION_FILE, user_data_dir / SESSION_LOG


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def login(args: argparse.Namespace) -> int:
    """Human-oriented blocking login flow."""
    sync_playwright, _ = import_playwright()
    user_data_dir = profile_dir(args.url, args.profile_name, args.profile_dir)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,
            viewport={"width": args.viewport[0], "height": args.viewport[1]},
            device_scale_factor=args.device_scale_factor,
        )
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)

        print(f"\nProfile directory: {user_data_dir}")
        print("Log in manually in the opened browser.")
        print("When Grafana has loaded, return here and press Enter to save/close the profile.\n")
        try:
            input()
        finally:
            context.close()

    return 0


def login_hold(args: argparse.Namespace) -> int:
    """Background process used by login-start. It exits when FINISH_FILE appears."""
    sync_playwright, _ = import_playwright()
    user_data_dir = profile_dir(args.url, args.profile_name, args.profile_dir)
    user_data_dir.mkdir(parents=True, exist_ok=True)
    finish_file, _, _ = session_paths(user_data_dir)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=False,
            viewport={"width": args.viewport[0], "height": args.viewport[1]},
            device_scale_factor=args.device_scale_factor,
        )
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        try:
            while not finish_file.exists():
                time.sleep(1)
        finally:
            context.close()
            try:
                finish_file.unlink()
            except FileNotFoundError:
                pass
    return 0


def login_start(args: argparse.Namespace) -> int:
    user_data_dir = profile_dir(args.url, args.profile_name, args.profile_dir)
    user_data_dir.mkdir(parents=True, exist_ok=True)
    finish_file, session_file, log_file = session_paths(user_data_dir)
    finish_file.unlink(missing_ok=True)

    if session_file.exists():
        try:
            session = json.loads(session_file.read_text())
            pid = int(session.get("pid"))
            if is_pid_alive(pid):
                print(json.dumps({
                    "status": "already-running",
                    "pid": pid,
                    "profile_dir": str(user_data_dir),
                    "message": "Login browser is already open. Ask the user to log in, then say continue.",
                }))
                return 0
        except Exception:
            pass

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "login-hold",
        "--url",
        args.url,
        "--profile-dir",
        str(user_data_dir),
        "--viewport",
        f"{args.viewport[0]}x{args.viewport[1]}",
        "--device-scale-factor",
        str(args.device_scale_factor),
        "--timeout-ms",
        str(args.timeout_ms),
    ]
    log = log_file.open("ab")
    proc = subprocess.Popen(
        cmd,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    session = {
        "pid": proc.pid,
        "url": args.url,
        "profile_dir": str(user_data_dir),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "log_file": str(log_file),
    }
    session_file.write_text(json.dumps(session, indent=2))
    print(json.dumps({
        "status": "started",
        "pid": proc.pid,
        "profile_dir": str(user_data_dir),
        "log_file": str(log_file),
        "message": "Login browser opened. Tell the user: Log in, then say continue.",
    }))
    return 0


def login_finish(args: argparse.Namespace) -> int:
    user_data_dir = profile_dir(args.url, args.profile_name, args.profile_dir)
    finish_file, session_file, log_file = session_paths(user_data_dir)
    if not session_file.exists():
        print(json.dumps({
            "status": "no-active-session",
            "profile_dir": str(user_data_dir),
            "message": "No login-start session found. If the user already logged in and closed the browser, try capture.",
        }))
        return 0

    try:
        session = json.loads(session_file.read_text())
        pid = int(session["pid"])
    except Exception as exc:
        print(f"Could not read session file {session_file}: {exc}", file=sys.stderr)
        return 2

    finish_file.touch()
    deadline = time.time() + args.wait_seconds
    while time.time() < deadline:
        if not is_pid_alive(pid):
            session_file.unlink(missing_ok=True)
            print(json.dumps({
                "status": "finished",
                "pid": pid,
                "profile_dir": str(user_data_dir),
            }))
            return 0
        time.sleep(0.5)

    if args.terminate_on_timeout and is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    session_file.unlink(missing_ok=True)
    print(json.dumps({
        "status": "timeout",
        "pid": pid,
        "profile_dir": str(user_data_dir),
        "log_file": str(log_file),
        "message": "Timed out waiting for login browser to close. Capture may still work if login completed.",
    }))
    return 0


def looks_like_login_page(text: str) -> bool:
    markers = [
        "welcome to grafana cloud",
        "sign in with saml",
        "sign in with okta",
        "sign in to grafana",
    ]
    lower = text.lower()
    return any(marker in lower for marker in markers)


def resolve_capture_profile(args: argparse.Namespace) -> Path | None:
    user_data_dir = profile_dir(args.url, args.profile_name, args.profile_dir)
    if user_data_dir.exists():
        return user_data_dir

    # Agent-friendly discovery: if no explicit profile was requested, try host match.
    if not args.profile_name and not args.profile_dir:
        match = find_profile_for_url(args.url)
        if match:
            print(f"Using discovered profile for host: {match}", file=sys.stderr)
            return match
    return None


def capture(args: argparse.Namespace) -> int:
    sync_playwright, PlaywrightTimeoutError = import_playwright()
    user_data_dir = resolve_capture_profile(args)
    if user_data_dir is None:
        expected = profile_dir(args.url, args.profile_name, args.profile_dir)
        profiles = list_profile_dirs()
        profile_lines = "\n".join(f"  - {p.name}: {p}" for p in profiles) or "  (none)"
        print(
            f"Profile directory does not exist: {expected}\n"
            f"Existing profiles under {CACHE_ROOT}:\n{profile_lines}\n"
            "Start browser login with login-start, ask the user to log in and say continue, then run login-finish and capture.",
            file=sys.stderr,
        )
        return 2

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(user_data_dir), **common_browser_args(args))
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)

        if args.wait_for_selector:
            page.wait_for_selector(args.wait_for_selector, timeout=args.timeout_ms)

        if args.wait_for_text:
            page.get_by_text(args.wait_for_text).first.wait_for(timeout=args.timeout_ms)

        if args.wait_ms > 0:
            page.wait_for_timeout(args.wait_ms)

        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=5_000)
        except PlaywrightTimeoutError:
            pass

        if not args.allow_login_page and looks_like_login_page(body_text):
            context.close()
            print(
                "Screenshot appears to be a Grafana login page.\n"
                "Agent flow: run login-start, tell the user 'Log in, then say continue', then run login-finish and capture again.",
                file=sys.stderr,
            )
            return 3

        if args.selector:
            locator = page.locator(args.selector).first
            locator.screenshot(path=str(output))
        else:
            page.screenshot(path=str(output), full_page=args.full_page)

        context.close()

    print(str(output))
    return 0


def add_login_args(parser: argparse.ArgumentParser, *, include_headed: bool = False) -> None:
    parser.add_argument("--url", required=True, help="Grafana base URL or dashboard URL.")
    parser.add_argument("--profile-name", help="Stable profile name. Defaults to the URL host.")
    parser.add_argument("--profile-dir", help="Explicit Playwright persistent profile directory. Overrides --profile-name.")
    parser.add_argument("--viewport", type=parse_viewport, default=(1600, 1200), help="Viewport WIDTHxHEIGHT. Default: 1600x1200.")
    parser.add_argument("--device-scale-factor", type=float, default=1.0)
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    if include_headed:
        parser.add_argument("--headed", action="store_true", help="Accepted for symmetry; login is always headed.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture Grafana dashboard screenshots with a persistent Playwright profile.")
    sub = parser.add_subparsers(dest="command", required=True)

    profiles_p = sub.add_parser("profiles", help="List saved Playwright browser profiles.")
    profiles_p.set_defaults(func=print_profiles)

    login_p = sub.add_parser("login", help="Blocking human login flow. Agents should prefer login-start/login-finish.")
    add_login_args(login_p)
    login_p.set_defaults(func=login)

    login_start_p = sub.add_parser("login-start", help="Open a headed login browser in the background and return immediately.")
    add_login_args(login_start_p)
    login_start_p.set_defaults(func=login_start)

    login_finish_p = sub.add_parser("login-finish", help="Close a browser opened by login-start after the user says continue.")
    login_finish_p.add_argument("--url", required=True, help="Same URL/host used for login-start.")
    login_finish_p.add_argument("--profile-name", help="Profile name used for login-start. Defaults to URL host.")
    login_finish_p.add_argument("--profile-dir", help="Explicit profile directory used for login-start.")
    login_finish_p.add_argument("--wait-seconds", type=float, default=10.0, help="How long to wait for the login browser to close. Default: 10.")
    login_finish_p.add_argument("--terminate-on-timeout", action=argparse.BooleanOptionalAction, default=True, help="Terminate browser process on timeout. Default: true.")
    login_finish_p.set_defaults(func=login_finish)

    login_hold_p = sub.add_parser("login-hold", help="Internal process used by login-start; normally do not run directly.")
    add_login_args(login_hold_p)
    login_hold_p.set_defaults(func=login_hold)

    cap_p = sub.add_parser("capture", help="Capture a screenshot using a saved profile.")
    cap_p.add_argument("--url", required=True, help="Full Grafana dashboard/panel URL.")
    cap_p.add_argument("--output", required=True, help="Output PNG path.")
    cap_p.add_argument("--profile-name", help="Profile name used during login. Defaults to the URL host.")
    cap_p.add_argument("--profile-dir", help="Explicit Playwright persistent profile directory. Overrides --profile-name.")
    cap_p.add_argument("--viewport", type=parse_viewport, default=(1800, 1400), help="Viewport WIDTHxHEIGHT. Default: 1800x1400.")
    cap_p.add_argument("--device-scale-factor", type=float, default=1.0)
    cap_p.add_argument("--timeout-ms", type=int, default=60_000)
    cap_p.add_argument("--wait-ms", type=int, default=8_000, help="Extra wait after navigation. Default: 8000.")
    cap_p.add_argument("--wait-for-selector", help="Optional selector to wait for before capture.")
    cap_p.add_argument("--wait-for-text", help="Optional text to wait for before capture.")
    cap_p.add_argument("--selector", help="Screenshot only this selector instead of the full page.")
    cap_p.add_argument("--full-page", action=argparse.BooleanOptionalAction, default=True, help="Capture full page. Default: true.")
    cap_p.add_argument("--headed", action="store_true", help="Run browser headed for debugging.")
    cap_p.add_argument("--allow-login-page", action="store_true", help="Allow screenshots that look like login pages.")
    cap_p.set_defaults(func=capture)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
