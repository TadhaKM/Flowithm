"""One-shot Gmail OAuth bootstrap.

Run once per Gmail account you want to ingest:

    python -m ingest.gmail_auth

Opens a browser window for Google OAuth consent. After authorising,
saves credentials to gmail_token.json. Paste the contents of that file
into the "Credentials JSON" field on the dashboard's Connect → Gmail flow.

Requires GOOGLE_CLIENT_SECRET_PATH to point at a client_secret.json
downloaded from Google Cloud Console (OAuth 2.0 Client ID, Desktop app).
"""
from __future__ import annotations

import json
import os
import sys

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_TOKEN_PATH = "gmail_token.json"


def main() -> None:
    # Lazy import — keeps `python -m brain.X` style discovery working
    # even when google-auth-oauthlib isn't installed in the environment.
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]
    except ImportError:
        sys.stderr.write(
            "google-auth-oauthlib is not installed. Install with:\n"
            "  pip install google-auth google-auth-oauthlib google-api-python-client\n"
        )
        sys.exit(1)

    client_secret_path = os.getenv("GOOGLE_CLIENT_SECRET_PATH", "client_secret.json")
    if not os.path.isfile(client_secret_path):
        sys.stderr.write(
            f"client_secret.json not found at {client_secret_path!r}. "
            "Set GOOGLE_CLIENT_SECRET_PATH or place the file in the project root.\n"
        )
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    out_path = os.getenv("GMAIL_TOKEN_OUTPUT", DEFAULT_TOKEN_PATH)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    print(f"Credentials saved to {out_path}")
    print("Paste the contents into the Credentials JSON field when connecting Gmail in the dashboard.")


if __name__ == "__main__":
    main()
