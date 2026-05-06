"""Flowithm Slack bot — entry point.

Run from the repo root:
    python slack/app.py
or:
    python -m slack.app

Uses Socket Mode for local development (no public URL needed). For production,
swap SocketModeHandler for the HTTP adapter and configure a Request URL in your
Slack app settings — see slack/README.md for the full setup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python slack/app.py` to find the `slack` package even when the script
# is launched directly (no -m). Add the repo root to sys.path before any
# project imports.
if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "slack"

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from slack import handlers

load_dotenv()


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"{name} missing — see slack/README.md for setup.")
    return val


def main() -> None:
    bot_token = _require("SLACK_BOT_TOKEN")
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET")  # not required for Socket Mode
    app_token = _require("SLACK_APP_TOKEN")

    app = App(token=bot_token, signing_secret=signing_secret)
    handlers.register(app)

    # Quick auth check so we fail fast on a bad token rather than hanging
    # the socket connection.
    try:
        identity = app.client.auth_test()
        bot_user = identity.get("user") or "unknown"
        team = identity.get("team") or "unknown"
        print(f"[Flowithm Slack] connected as {bot_user} in {team}", flush=True)
    except Exception as exc:
        sys.exit(f"Slack auth_test failed — check SLACK_BOT_TOKEN. Detail: {exc}")

    print("[Flowithm Slack] starting in Socket Mode…", flush=True)
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()
