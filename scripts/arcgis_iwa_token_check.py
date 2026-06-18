# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "requests>=2.31",
#   "requests-negotiate-sspi>=0.5.2; sys_platform == 'win32'",
# ]
# ///
"""Generate and validate an ArcGIS Enterprise portal token without a password.

Run from the repository root on Windows:

    uv run scripts/arcgis_iwa_token_check.py

The script reads only URL settings (PORTAL_URL and optional GENERATE_TOKEN_URL)
from .env, environment variables, or .env.example. It intentionally does not
read or send ARCGIS_USERNAME or ARCGIS_PASSWORD.

Two no-password paths are supported:

* OAuth browser SSO, which opens the Portal OAuth page. Your normal browser /
  Windows SSO login completes the sign-in, then you paste the approval code.
* Direct SSPI/IWA generateToken, for portals whose REST endpoint itself issues
  a WWW-Authenticate: Negotiate/NTLM challenge.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests

DEFAULT_EXPIRATION_MINUTES = 60
DEFAULT_OAUTH_CLIENT_ID = "arcgispro"
OAUTH_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
USER_AGENT = "arcgis-uploader-iwa-token-check/1.0"


class ArcGISTokenCheckError(RuntimeError):
    """A user-facing failure while talking to ArcGIS Enterprise."""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate an ArcGIS Enterprise portal token with the current "
            "Windows/browser SSO session, then use that token to fetch "
            "authenticated portal/user information. No ArcGIS username or "
            "password is read or sent."
        )
    )
    parser.add_argument(
        "--auth-mode",
        choices=("auto", "oauth", "generate-token"),
        default="auto",
        help=(
            "Authentication path. auto tries direct SSPI generateToken first, "
            "then falls back to OAuth browser SSO. Default: auto."
        ),
    )
    parser.add_argument(
        "--oauth-client-id",
        default=DEFAULT_OAUTH_CLIENT_ID,
        help=(
            "OAuth client id for browser SSO. Defaults to ArcGIS Pro's "
            f"registered client id: {DEFAULT_OAUTH_CLIENT_ID}."
        ),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the OAuth URL instead of opening it in the default browser.",
    )
    parser.add_argument(
        "--portal-url",
        help="Portal URL, for example https://maps.gov.yk.ca/portal. Defaults to PORTAL_URL.",
    )
    parser.add_argument(
        "--token-url",
        help="generateToken URL. Defaults to GENERATE_TOKEN_URL or PORTAL_URL/sharing/rest/generateToken.",
    )
    parser.add_argument(
        "--expiration",
        type=int,
        default=DEFAULT_EXPIRATION_MINUTES,
        help=f"Requested token lifetime in minutes (default: {DEFAULT_EXPIRATION_MINUTES}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds (default: 60).",
    )
    parser.add_argument(
        "--show-token",
        action="store_true",
        help="Print the full token. By default only a masked preview is shown.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification. Use only for a temporary local test.",
    )
    args = parser.parse_args()

    if os.name != "nt" and args.auth_mode in ("auto", "generate-token"):
        raise ArcGISTokenCheckError(
            "Integrated Windows Authentication via SSPI requires Windows. "
            "Run this from the Windows account that should authenticate to ArcGIS, "
            "or use --auth-mode oauth."
        )

    env = load_url_env_values(Path.cwd())
    portal_url = clean_url(args.portal_url or env.get("PORTAL_URL", ""))
    if not portal_url:
        raise ArcGISTokenCheckError(
            "PORTAL_URL is not set. Put it in .env or pass --portal-url."
        )

    token_url = clean_url(
        args.token_url
        or env.get("GENERATE_TOKEN_URL", "")
        or f"{portal_url}/sharing/rest/generateToken"
    )
    referer = portal_url
    verify_tls = not args.insecure

    print("ArcGIS Enterprise IWA token check")
    print("=================================")
    print(f"Windows process identity : {windows_identity()}")
    print(f"Portal URL               : {portal_url}")
    print(f"Token URL                : {token_url}")
    print(f"Auth mode                : {args.auth_mode}")
    print("Username/password source : not used")
    print()

    iwa_session = None
    token_body, token_method = get_token_without_password(
        portal_url=portal_url,
        token_url=token_url,
        referer=referer,
        auth_mode=args.auth_mode,
        oauth_client_id=args.oauth_client_id,
        open_browser=not args.no_browser,
        verify_tls=verify_tls,
        expiration_minutes=args.expiration,
        timeout=args.timeout,
    )
    if args.auth_mode != "oauth":
        iwa_session = new_session(verify_tls=verify_tls)
        add_iwa_auth(iwa_session)
    token = str(token_body["token"])
    expires = parse_arcgis_epoch_ms(token_body.get("expires"))

    print(f"Token generated via      : {token_method}")
    if expires:
        print(f"Token expires            : {expires.isoformat()} local time")
    if "ssl" in token_body:
        print(f"Token requires SSL       : {token_body.get('ssl')}")
    if args.show_token:
        print(f"Token                    : {token}")
    else:
        print(f"Token                    : {mask_token(token)}")
    print()

    community_self_url = urljoin(portal_url + "/", "sharing/rest/community/self")
    portals_self_url = urljoin(portal_url + "/", "sharing/rest/portals/self")

    print("Anonymous visibility check")
    print("--------------------------")
    plain_session = new_session(verify_tls=verify_tls)
    anonymous = get_arcgis_json(
        plain_session,
        community_self_url,
        referer=referer,
        timeout=args.timeout,
        token=None,
        raise_on_arcgis_error=False,
    )
    if anonymous.ok and isinstance(anonymous.body, dict) and anonymous.body.get("username"):
        print(
            "WARNING: community/self returned a username without a token. "
            "This endpoint may also be protected by ambient web-tier auth."
        )
    else:
        print("community/self is not visible to a plain request without the token.")
        print(f"Anonymous response       : {anonymous.summary()}")
    print()

    print("Authenticated validation with generated token")
    print("---------------------------------------------")
    token_session = new_session(verify_tls=verify_tls)
    validation = get_arcgis_json(
        token_session,
        community_self_url,
        referer=referer,
        timeout=args.timeout,
        token=token,
        raise_on_arcgis_error=False,
    )
    validation_note = "fresh session, token only"

    if not response_has_authenticated_user(validation):
        # Some IWA web adaptors still challenge at the HTTP layer before ArcGIS
        # can inspect the token. Retry with IWA enabled, while still passing the
        # token, so the script remains useful in that topology.
        retry = get_arcgis_json(
            iwa_session or token_session,
            community_self_url,
            referer=referer,
            timeout=args.timeout,
            token=token,
            raise_on_arcgis_error=False,
        )
        if response_has_authenticated_user(retry):
            validation = retry
            validation_note = "IWA session plus generated token (web adaptor also required IWA)"

    if not response_has_authenticated_user(validation):
        raise ArcGISTokenCheckError(
            "Generated a token, but could not use it to fetch community/self.\n"
            f"Token validation response: {validation.summary()}"
        )

    user_info = validation.body
    assert isinstance(user_info, dict)
    print(f"Validated using          : {validation_note}")
    print_selected(
        "Authenticated user",
        user_info,
        [
            "username",
            "fullName",
            "email",
            "role",
            "userType",
            "userLicenseTypeId",
            "orgId",
            "id",
        ],
    )

    print()

    portal_response = get_arcgis_json(
        token_session,
        portals_self_url,
        referer=referer,
        timeout=args.timeout,
        token=token,
        raise_on_arcgis_error=False,
    )
    if not portal_response.ok or arcgis_error(portal_response.body):
        portal_response = get_arcgis_json(
            iwa_session or token_session,
            portals_self_url,
            referer=referer,
            timeout=args.timeout,
            token=token,
            raise_on_arcgis_error=False,
        )

    if portal_response.ok and isinstance(portal_response.body, dict):
        print_selected(
            "Portal info fetched with token",
            portal_response.body,
            [
                "name",
                "portalName",
                "id",
                "urlKey",
                "customBaseUrl",
                "portalHostname",
                "currentVersion",
                "allSSL",
                "isPortal",
            ],
        )
    else:
        print("Portal info fetched with token: unavailable")
        print(f"Portal response          : {portal_response.summary()}")

    print()
    print("Success: a token was generated without an ArcGIS username/password and used for an authenticated Portal REST request.")
    return 0


def get_token_without_password(
    *,
    portal_url: str,
    token_url: str,
    referer: str,
    auth_mode: str,
    oauth_client_id: str,
    open_browser: bool,
    verify_tls: bool,
    expiration_minutes: int,
    timeout: float,
) -> tuple[dict[str, Any], str]:
    """Return a Portal token without reading username/password settings."""

    if auth_mode in ("auto", "generate-token"):
        iwa_session = new_session(verify_tls=verify_tls)
        add_iwa_auth(iwa_session)
        try:
            body, method = generate_portal_token(
                iwa_session=iwa_session,
                token_url=token_url,
                referer=referer,
                expiration_minutes=expiration_minutes,
                timeout=timeout,
            )
            return body, f"direct SSPI/IWA generateToken ({method} {token_url})"
        except ArcGISTokenCheckError as exc:
            if auth_mode == "generate-token":
                raise
            print("Direct SSPI/IWA generateToken did not produce a token.")
            print(compact_text(str(exc), limit=500))
            print()
            print(
                "Falling back to OAuth browser SSO. This matches ArcGIS Pro/browser "
                "sign-in better when Portal delegates login to an identity provider."
            )
            print()

    return oauth_browser_token(
        portal_url=portal_url,
        client_id=oauth_client_id,
        open_browser=open_browser,
        verify_tls=verify_tls,
        expiration_minutes=expiration_minutes,
        timeout=timeout,
    )


def add_iwa_auth(session: requests.Session) -> None:
    try:
        from requests_negotiate_sspi import HttpNegotiateAuth
    except ImportError as exc:  # pragma: no cover - uv should install this from the header.
        raise ArcGISTokenCheckError(
            "Missing requests-negotiate-sspi. Run with `uv run scripts/arcgis_iwa_token_check.py` "
            "so uv installs the script dependencies."
        ) from exc

    session.auth = HttpNegotiateAuth()


def new_session(*, verify_tls: bool) -> requests.Session:
    session = requests.Session()
    session.verify = verify_tls
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def generate_portal_token(
    *,
    iwa_session: requests.Session,
    token_url: str,
    referer: str,
    expiration_minutes: int,
    timeout: float,
) -> tuple[dict[str, Any], str]:
    params = {
        "client": "referer",
        "referer": referer,
        "expiration": str(expiration_minutes),
        "f": "json",
    }
    attempts: list[str] = []

    # Esri's IWA support sample uses GET with SSPI. POST is included as a
    # fallback because the normal generateToken username/password flow is POST.
    for method in ("GET", "POST"):
        response = request_arcgis_json(
            iwa_session,
            method,
            token_url,
            referer=referer,
            timeout=timeout,
            params=params if method == "GET" else None,
            data=params if method == "POST" else None,
        )
        body = response.body
        if response.ok and isinstance(body, dict) and body.get("token"):
            return body, method
        attempts.append(f"{method}: {response.summary()}")

    raise ArcGISTokenCheckError(
        "Could not generate a portal token with Windows IWA credentials.\n"
        + "\n".join(f"  - {attempt}" for attempt in attempts)
    )


def oauth_browser_token(
    *,
    portal_url: str,
    client_id: str,
    open_browser: bool,
    verify_tls: bool,
    expiration_minutes: int,
    timeout: float,
) -> tuple[dict[str, Any], str]:
    authorize_url = urljoin(portal_url + "/", "sharing/rest/oauth2/authorize")
    token_url = urljoin(portal_url + "/", "sharing/rest/oauth2/token")
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI,
        "expiration": str(expiration_minutes),
    }
    url = f"{authorize_url}?{urlencode(params)}"

    print("OAuth browser SSO")
    print("-----------------")
    print(f"OAuth client id          : {client_id}")
    print(f"OAuth redirect URI       : {OAUTH_REDIRECT_URI}")
    print()
    print("A browser will open to the ArcGIS sign-in page.")
    print("If Windows/browser SSO is working, it should sign you in without an ArcGIS password.")
    print("After the ArcGIS approval page appears, copy the code or the full approval URL here.")
    print()
    print(f"Authorize URL            : {url}")
    if open_browser:
        webbrowser.open(url)
    print()

    pasted = input("Paste authorization code or approval URL: ").strip()
    code = extract_oauth_code(pasted)
    if not code:
        raise ArcGISTokenCheckError("No OAuth authorization code was pasted.")

    session = new_session(verify_tls=verify_tls)
    response = request_arcgis_json(
        session,
        "POST",
        token_url,
        referer=portal_url,
        timeout=timeout,
        params=None,
        data={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "f": "json",
        },
    )
    body = response.body
    if not response.ok or not isinstance(body, dict) or arcgis_error(body):
        raise ArcGISTokenCheckError(
            "OAuth code exchange failed.\n"
            f"Token endpoint response: {response.summary()}"
        )

    token = body.get("access_token") or body.get("token")
    if not token:
        raise ArcGISTokenCheckError(
            "OAuth code exchange succeeded, but no access token was returned.\n"
            f"Token endpoint response: {response.summary()}"
        )

    normalized = dict(body)
    normalized["token"] = str(token)
    if "expires" not in normalized and normalized.get("expires_in") is not None:
        try:
            normalized["expires"] = int((time.time() + int(normalized["expires_in"])) * 1000)
        except (TypeError, ValueError):
            pass
    return normalized, f"OAuth browser SSO ({client_id})"


def extract_oauth_code(pasted: str) -> str:
    pasted = pasted.strip()
    if not pasted:
        return ""

    parsed = urlparse(pasted)
    query_code = parse_qs(parsed.query).get("code")
    if query_code:
        return query_code[0].strip()
    fragment_code = parse_qs(parsed.fragment).get("code")
    if fragment_code:
        return fragment_code[0].strip()

    # The approval page title/body often contains "...?code=<value>"; accept
    # copied text as well as the bare code.
    match = re.search(r"(?:[?&]code=|code[:=]\s*)([A-Za-z0-9._~-]+)", pasted)
    if match:
        return match.group(1).strip()
    return pasted


def get_arcgis_json(
    session: requests.Session,
    url: str,
    *,
    referer: str,
    timeout: float,
    token: str | None,
    raise_on_arcgis_error: bool,
) -> "ArcGISResponse":
    params = {"f": "json"}
    if token:
        params["token"] = token
    response = request_arcgis_json(
        session,
        "GET",
        url,
        referer=referer,
        timeout=timeout,
        params=params,
        data=None,
    )
    if raise_on_arcgis_error and (not response.ok or arcgis_error(response.body)):
        raise ArcGISTokenCheckError(response.summary())
    return response


def request_arcgis_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    referer: str,
    timeout: float,
    params: dict[str, str] | None,
    data: dict[str, str] | None,
) -> "ArcGISResponse":
    try:
        http_response = session.request(
            method,
            url,
            params=params,
            data=data,
            timeout=timeout,
            headers={"Referer": referer},
        )
    except requests.RequestException as exc:
        return ArcGISResponse(status_code=0, body=None, text=str(exc), ok=False)

    text = http_response.text
    try:
        body: Any = http_response.json()
    except ValueError:
        body = None
    return ArcGISResponse(
        status_code=http_response.status_code,
        body=body,
        text=text,
        ok=http_response.ok,
    )


class ArcGISResponse:
    def __init__(self, *, status_code: int, body: Any, text: str, ok: bool) -> None:
        self.status_code = status_code
        self.body = body
        self.text = text
        self.ok = ok

    def summary(self) -> str:
        if isinstance(self.body, dict):
            error = arcgis_error(self.body)
            if error:
                return f"HTTP {self.status_code}, ArcGIS error: {error}"
            keys = ", ".join(sorted(str(key) for key in self.body.keys())[:8])
            return f"HTTP {self.status_code}, JSON object keys: {keys}"
        if isinstance(self.body, list):
            return f"HTTP {self.status_code}, JSON list length: {len(self.body)}"
        text = compact_text(self.text)
        return f"HTTP {self.status_code}, non-JSON response: {text}"


def response_has_authenticated_user(response: ArcGISResponse) -> bool:
    return (
        response.ok
        and isinstance(response.body, dict)
        and not arcgis_error(response.body)
        and bool(response.body.get("username"))
    )


def arcgis_error(body: Any) -> str:
    if not isinstance(body, dict) or "error" not in body:
        return ""
    error = body.get("error") or {}
    if isinstance(error, dict):
        message = str(error.get("message") or "ArcGIS error")
        details = " ".join(str(item) for item in (error.get("details") or []))
        code = error.get("code")
        prefix = f"{code}: " if code else ""
        return compact_text(f"{prefix}{message} {details}")
    return compact_text(str(error))


def print_selected(title: str, body: dict[str, Any], fields: list[str]) -> None:
    print(title)
    for field in fields:
        value = body.get(field)
        if value in (None, "", []):
            continue
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            value_text = str(value)
        print(f"  {field:<19}: {value_text}")


def parse_arcgis_epoch_ms(value: Any) -> dt.datetime | None:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    return dt.datetime.fromtimestamp(milliseconds / 1000).astimezone()


def mask_token(token: str) -> str:
    if len(token) <= 24:
        return "<redacted>"
    return f"{token[:12]}...{token[-8:]}"


def clean_url(value: str) -> str:
    return value.strip().rstrip("/")


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if "\\" in value:
        value = value.rsplit("\\", 1)[1]
    if "@" in value:
        value = value.split("@", 1)[0]
    return value


def compact_text(value: str, *, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        return value[: limit - 3] + "..."
    return value


def windows_identity() -> str:
    candidates: list[str] = []
    for command in (["whoami"], ["whoami", "/upn"]):
        try:
            output = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        output = output.strip()
        if output and output not in candidates:
            candidates.append(output)
    if candidates:
        return " / ".join(candidates)
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def load_url_env_values(root: Path) -> dict[str, str]:
    """Load only URL-related settings.

    This deliberately ignores ARCGIS_USERNAME, ARCGIS_PASSWORD, and every other
    credential-like application setting, even when they exist in .env.
    """

    allowed_keys = {"PORTAL_URL", "GENERATE_TOKEN_URL"}
    env = {
        key: value
        for key, value in os.environ.items()
        if key in allowed_keys
    }
    loaded_any = False
    for filename in (".env", ".env.example"):
        path = root / filename
        if not path.exists():
            continue
        loaded_any = True
        for key, value in parse_env_file(path).items():
            if key in allowed_keys:
                env.setdefault(key, value)

    # Expand simple $NAME / ${NAME} references after all files have been read.
    for key, value in list(env.items()):
        env[key] = expand_env_refs(value, env)

    if not loaded_any:
        return env
    return env


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def expand_env_refs(value: str, env: dict[str, str]) -> str:
    pattern = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")

    def replace(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("plain") or ""
        return env.get(name, match.group(0))

    previous = value
    for _ in range(3):
        expanded = pattern.sub(replace, previous)
        if expanded == previous:
            return expanded
        previous = expanded
    return previous


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ArcGISTokenCheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
