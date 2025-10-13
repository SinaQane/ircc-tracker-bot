import os
import json
import time
import math
import html
from datetime import datetime, timedelta
from typing import Dict, Any

import requests
from dotenv import load_dotenv

load_dotenv()

UCI = os.getenv("UCI")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))

RELAY_URL = os.getenv("TG_RELAY_URL")
RELAY_SECRET = os.getenv("TG_RELAY_SECRET")

if not (UCI and AUTH_PASSWORD and RELAY_URL and RELAY_SECRET):
    raise RuntimeError("Missing one of UCI, AUTH_PASSWORD, TG_RELAY_URL, TG_RELAY_SECRET env vars.")

API_URL = "https://api.ircc-tracker-suivi.apps.cic.gc.ca/user"

HEADERS = {
    "Accept": "application/json",
    "Referer": "https://ircc-tracker-suivi.apps.cic.gc.ca/",
    "Content-Type": "application/json",
    # "Authorization": "Bearer <set after auth>"
}

REQUEST_BODY = {
    "method": "get-application-details",
    "uci": UCI,
    "isAgent": False,
}

PROFILE_REQUEST_BODY = {
    "method": "get-profile-summary",
    "uci": UCI,
    "isAgent": False,
}

AUTH_URL = "https://cognito-idp.ca-central-1.amazonaws.com/"
AUTH_HEADERS = {
    "Origin": "https://ircc-tracker-suivi.apps.cic.gc.ca",
    "Referer": "https://ircc-tracker-suivi.apps.cic.gc.ca",
    "x-amz-target": "AWSCognitoIdentityProviderService.InitiateAuth",
    "x-amz-user-agent": "aws-amplify/5.0.4 js",
    "Content-Type": "application/x-amz-json-1.1",
}
AUTH_BODY = {
    "AuthFlow": "USER_PASSWORD_AUTH",
    "ClientId": "3cfutv5ffd1i622g1tn6vton5r",
    "AuthParameters": {"USERNAME": UCI, "PASSWORD": AUTH_PASSWORD},
    "ClientMetadata": {},
}

# --- Globals ------------------------------------------------------------------
token_expiration_time = None

# Use a session with small retry/backoff
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

def _request_with_retries(method: str, url: str, **kwargs) -> requests.Response:
    backoff = 1.0
    for attempt in range(3):
        try:
            r = SESSION.request(method, url, timeout=kwargs.pop("timeout", 25), **kwargs)
            if r.status_code in (502, 503, 504):
                raise requests.HTTPError(f"Transient {r.status_code}", response=r)
            return r
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(backoff)
            backoff *= 2

def load_previous_data() -> Dict:
    try:
        with open("previous_state.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def save_current_data(data: Dict) -> None:
    tmp = "previous_state.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, "previous_state.json")

def log_change(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    print(line, end="")
    try:
        with open("changes.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def _format_changes_for_text(changes: Any) -> str:
    if isinstance(changes, str):
        return changes
    try:
        return "Updates:\n" + json.dumps(changes, indent=2, ensure_ascii=False)
    except Exception:
        return f"Updates: {changes!r}"

def send_via_relay(text: str) -> None:
    headers = {"x-hook-secret": RELAY_SECRET, "content-type": "application/json"}
    r = _request_with_retries("POST", RELAY_URL, headers=headers, json={"text": text})
    if r.status_code != 200:
        raise RuntimeError(f"Relay failed: {r.status_code} {r.text}")

def send_message(changes: Any) -> None:
    msg = _format_changes_for_text(changes)
    log_change(f"Sending via relay ({len(msg)} chars)")
    send_via_relay(msg)

def compare_data(old_data: Dict, new_data: Dict) -> str:
    if not old_data:
        return "Initialized data"

    changes = []

    try:
        old_last = old_data.get("app", {}).get("lastUpdated")
        new_last = new_data.get("app", {}).get("lastUpdated")
        if old_last != new_last:
            changes.append(f"Last updated time changed from {old_last} to {new_last}")
    except Exception as e:
        changes.append(f"(diff warning: lastUpdated check failed: {e})")

    try:
        old_rel = (old_data.get("relations") or [{}])[0]
        new_rel = (new_data.get("relations") or [{}])[0]
        old_acts = (old_rel.get("activities") or {}) or {}
        new_acts = (new_rel.get("activities") or {}) or {}
        keys = set(old_acts) | set(new_acts)
        for k in sorted(keys):
            if old_acts.get(k) != new_acts.get(k):
                changes.append(f"Activity {k} status changed from {old_acts.get(k)} to {new_acts.get(k)}")
    except Exception as e:
        changes.append(f"(diff warning: activities check failed: {e})")

    try:
        old_hist = {(h or {}).get("dateCreated") for h in ((old_data.get("relations") or [{}])[0].get("history") or [])}
        for entry in ((new_data.get("relations") or [{}])[0].get("history") or []):
            dc = entry.get("dateCreated")
            if dc and dc not in old_hist:
                changes.append(f"New history: {entry.get('key')} (Created at {dc})")
    except Exception as e:
        changes.append(f"(diff warning: history check failed: {e})")

    return "\n".join(changes) if changes else "No changes detected"

def refresh_token() -> None:
    global token_expiration_time
    try:
        resp = _request_with_retries("POST", AUTH_URL, headers=AUTH_HEADERS, json=AUTH_BODY)
        if resp.status_code != 200:
            try:
                log_change(f"Auth error body: {resp.json()}")
            except Exception:
                log_change(f"Auth error body: {resp.text}")
            resp.raise_for_status()

        data = resp.json()
        id_token = data["AuthenticationResult"]["IdToken"]
        HEADERS["Authorization"] = f"Bearer {id_token}"
        expires_in = data["AuthenticationResult"].get("ExpiresIn", 3600)
        token_expiration_time = datetime.now() + timedelta(seconds=max(60, expires_in - 120))
        log_change("Token refreshed successfully")
    except Exception as e:
        log_change(f"Token refresh failed: {e}")
        raise

def is_token_expired() -> bool:
    return token_expiration_time is None or datetime.now() >= token_expiration_time

def get_application_number() -> str:
    try:
        if "Authorization" not in HEADERS or is_token_expired():
            refresh_token()
        resp = _request_with_retries("POST", API_URL, headers=HEADERS, json=PROFILE_REQUEST_BODY)
        resp.raise_for_status()
        data = resp.json()
        if data.get("apps"):
            return data["apps"][0]["appNum"]
        raise RuntimeError("No application found in profile")
    except Exception as e:
        log_change(f"Failed to fetch application number: {e}")
        raise

def check_status() -> None:
    try:
        if "Authorization" not in HEADERS or is_token_expired():
            refresh_token()

        app_number = get_application_number()

        body = dict(REQUEST_BODY)
        body["applicationNumber"] = app_number

        resp = _request_with_retries("POST", API_URL, headers=HEADERS, json=body)
        if resp.status_code == 401:
            refresh_token()
            resp = _request_with_retries("POST", API_URL, headers=HEADERS, json=body)

        resp.raise_for_status()
        current = resp.json()

        previous = load_previous_data()
        changes = compare_data(previous, current)
        log_change(changes)
        save_current_data(current)

        if changes not in ("Initialized data", "No changes detected"):
            send_message(changes)
            
    except Exception as e:
        log_change(f"Error: {e}")

def main():
    print("Starting monitor...")
    while True:
        check_status()
        time.sleep(POLL_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    main()
