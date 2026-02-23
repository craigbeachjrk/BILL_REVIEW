"""
Bitwarden CLI Lookup Lambda
Looks up utility portal credentials from a Bitwarden vault.
Returns portal URL and username only — NEVER returns passwords.

Invoked by the Bill Review app's Directed Workflow feature to help
users know where to log in to collect utility bills.

Environment:
  BW_SECRET_NAME: Secrets Manager secret name (default: bitwarden/api-key)
  Expected secret JSON: {"client_id": "...", "client_secret": "...", "master_password": "..."}

The Bitwarden CLI binary must be available at /opt/bin/bw (via Lambda Layer).
"""

import os
import json
import subprocess
import boto3

sm = boto3.client("secretsmanager")
BW_SECRET_NAME = os.getenv("BW_SECRET_NAME", "bitwarden/api-key")

# Cache session key across warm invocations
_session_key = None


def _get_bw_env():
    """Build environment dict for bw CLI subprocess calls."""
    return {
        "PATH": "/opt/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "BITWARDENCLI_APPDATA_DIR": "/tmp/bw-data",
    }


def _unlock_vault():
    """Authenticate and unlock the Bitwarden vault, returning a session key."""
    global _session_key
    if _session_key:
        # Verify session is still valid
        env = {**_get_bw_env(), "BW_SESSION": _session_key}
        result = subprocess.run(
            ["/opt/bin/bw", "status"],
            env=env, capture_output=True, text=True, timeout=15
        )
        if '"status":"unlocked"' in result.stdout:
            return _session_key
        _session_key = None

    # Load credentials from Secrets Manager
    secret = sm.get_secret_value(SecretId=BW_SECRET_NAME)
    creds = json.loads(secret["SecretString"])

    env = {
        **_get_bw_env(),
        "BW_CLIENTID": creds["client_id"],
        "BW_CLIENTSECRET": creds["client_secret"],
    }

    # Login with API key
    subprocess.run(
        ["/opt/bin/bw", "login", "--apikey"],
        env=env, capture_output=True, text=True, timeout=30
    )

    # Unlock with master password
    env["BW_PASSWORD"] = creds["master_password"]
    result = subprocess.run(
        ["/opt/bin/bw", "unlock", "--passwordenv", "BW_PASSWORD"],
        env=env, capture_output=True, text=True, timeout=30
    )

    # Parse session key from output
    for line in result.stdout.splitlines():
        if "BW_SESSION=" in line:
            # Format: export BW_SESSION="..."  or  $ env:BW_SESSION="..."
            part = line.split("BW_SESSION=")[1].strip().strip('"').strip("'")
            _session_key = part
            return _session_key

    raise RuntimeError(f"Failed to unlock Bitwarden vault. stdout={result.stdout[:200]}, stderr={result.stderr[:200]}")


def _search_vault(session: str, query: str) -> list:
    """Search the vault for items matching the query."""
    env = {**_get_bw_env(), "BW_SESSION": session}
    result = subprocess.run(
        ["/opt/bin/bw", "list", "items", "--search", query],
        env=env, capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        print(f"[BW] Search failed: {result.stderr[:200]}")
        return []
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []


def handler(event, context):
    """
    Lambda handler for Bitwarden lookups.

    Query params:
      provider: Provider name to search for (e.g., "sce", "pge", "ladwp")
      account:  Optional account number for more specific matching

    Returns:
      {exists: bool, portal_url: str, username: str, item_name: str}
      NEVER returns passwords.
    """
    params = event.get("queryStringParameters") or {}
    provider = (params.get("provider") or "").strip().lower()

    if not provider:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "provider parameter is required"})
        }

    try:
        session = _unlock_vault()
    except Exception as e:
        print(f"[BW] Vault unlock failed: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to access credential vault"})
        }

    # Search vault
    items = _search_vault(session, provider)

    # Find best match
    account = (params.get("account") or "").strip().lower()
    best_match = None

    for item in items:
        name = (item.get("name") or "").lower()
        notes = (item.get("notes") or "").lower()

        # Check if provider name appears in item name or notes
        if provider not in name and provider not in notes:
            continue

        # If account specified, prefer items that mention it
        if account and (account in name or account in notes):
            best_match = item
            break

        if best_match is None:
            best_match = item

    if not best_match:
        return {
            "statusCode": 200,
            "body": json.dumps({
                "exists": False,
                "portal_url": "",
                "username": "",
                "item_name": ""
            })
        }

    login = best_match.get("login") or {}
    uris = login.get("uris") or []
    portal_url = uris[0].get("uri", "") if uris else ""

    return {
        "statusCode": 200,
        "body": json.dumps({
            "exists": True,
            "portal_url": portal_url,
            "username": login.get("username", ""),
            "item_name": best_match.get("name", ""),
        })
    }
