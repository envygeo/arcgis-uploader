"""Build the server release and the small iframe integration package.

Outputs:
  dist/arcgis-uploader-server-YYYYMMDD.zip
  dist/arcgis-uploader-embed-YYYYMMDD.zip

The server archive contains the complete runtime application, but no tests,
developer scripts, repository metadata, or credentials. The embed archive is
for the team that owns the parent web application. It contains only integration
instructions and examples; it does not contain the uploader server.

Usage:  python scripts/make_release.py        (stdlib only, no deps)
"""
from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SERVER_INCLUDE = [
    "app",
    "static",
    "pyproject.toml",
    "uv.lock",
    "LICENSE",
]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
FORBIDDEN_NAMES = {".env"}

SERVER_DEPLOY_MD = """# ArcGIS uploader server release

This archive contains the files needed to run the uploader service. It does
not contain tests, developer scripts, repository metadata, or credentials.

## Install

Requirements:

- Python 3.10 or newer
- uv

From the unpacked release directory:

```powershell
Copy-Item config.env.example .env
# Edit .env before continuing. Keep DRY_RUN=true for the first test.
uv sync --frozen --no-dev
uv run --no-dev uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Put the service behind the web host's HTTPS reverse proxy and authentication.
Do not expose the uvicorn development process directly to the internet.

## Replace the frontend

The server serves `static/index.html` at `/` and maps nested files beneath
`static/` to the same URL paths. Replace `static/index.html` and add or replace
assets in `static/` without editing `app/main.py`. Keep browser links and API
requests relative, such as `src="assets/app.js"` and `fetch("api/info")`, so
the frontend also works when the reverse proxy publishes this service beneath
a subpath.

Server routes take priority over the final static-file mount. Files beneath
`static/api/` cannot replace API, authentication, debug, or OpenAPI routes.
The iframe page is `/example3`. Its required application endpoints are:

- `GET /examples.css`
- `GET /query-prefill.js`
- `GET /api/info`
- `POST /api/preview`
- `POST /api/upload`

Example 3 also loads Leaflet 1.9.4 from `unpkg.com`. If the server or browsers
cannot reach that CDN, vendor Leaflet locally before deployment.

## Identity

For an audited deployment, have the trusted reverse proxy set the header named
by `USERNAME_HEADER`, and keep `ALLOW_CLIENT_USERNAME=false`. A username sent
by the browser or included in the iframe URL is user-controlled data.

## Embedding headers

For a cross-origin iframe, configure the reverse proxy's Content-Security-Policy
`frame-ancestors` directive with the exact parent application origin. Do not
send `X-Frame-Options: DENY` or `X-Frame-Options: SAMEORIGIN` for that page.
Authentication inside a cross-site iframe may also be affected by browser
third-party cookie restrictions.

## Go live

1. Test `/example3` while `DRY_RUN=true`.
2. Confirm the target and comparison layer URLs.
3. Confirm that the reverse proxy supplies the expected authenticated user.
4. Set `DRY_RUN=false` and restart the service.
"""

EMBED_README_MD = """# Example 3 iframe integration

The uploader remains a separate hosted service. The parent application embeds
its `/example3` URL and does not need the uploader's Python source code.

## Basic embed

Replace the sample host and project ID in `iframe.html`, or construct the same
URL in the parent application's normal view/component code:

```html
<iframe
  src="https://uploader.example.gov/example3?project_id=EA-74"
  title="Project geometry uploader"
  style="width:100%; min-height:900px; border:0">
</iframe>
```

Use a URL builder when `project_id` is dynamic. The case-sensitive query
parameter name is `project_id`.

The optional `username` query parameter only prefills an editable form field.
It must not be treated as authenticated identity. For auditing, the uploader
should authenticate the iframe request itself and receive the username from a
trusted reverse-proxy header.

## Host requirements

- The uploader host must permit the parent application's exact origin in its
  Content-Security-Policy `frame-ancestors` directive.
- The uploader response must not use `X-Frame-Options: DENY` or `SAMEORIGIN`
  for a cross-origin parent.
- Authentication inside the iframe must work under current browser cookie
  restrictions.
- The parent page's query string is not inherited by the iframe. Add
  `project_id` to the iframe `src` explicitly.

`Example3Uploader.razor` is a Blazor example that constructs the URL with
`QueryHelpers.AddQueryString` so the project ID is encoded correctly.
"""

IFRAME_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Uploader iframe example</title>
</head>
<body>
  <!-- Replace the host and project ID. Use your framework's URL builder for dynamic values. -->
  <iframe
    src="https://uploader.example.gov/example3?project_id=EA-74"
    title="Project geometry uploader"
    style="width:100%; min-height:900px; border:0">
  </iframe>
</body>
</html>
"""

RAZOR_COMPONENT = """@using Microsoft.AspNetCore.WebUtilities

<iframe src="@UploaderUrl"
        title="Project geometry uploader"
        style="width:100%; min-height:900px; border:0">
</iframe>

@code {
    [Parameter, EditorRequired]
    public string UploaderBaseUrl { get; set; } = "";

    [Parameter, EditorRequired]
    public string ProjectId { get; set; } = "";

    private string UploaderUrl => QueryHelpers.AddQueryString(
        $"{UploaderBaseUrl.TrimEnd('/')}/example3",
        "project_id",
        ProjectId);
}
"""


def gather_server_files() -> list[Path]:
    """Return the allowlisted runtime files in stable archive order."""
    files: list[Path] = []
    for name in SERVER_INCLUDE:
        path = ROOT / name
        if not path.exists():
            sys.exit(f"required server release path not found: {name}")
        if path.is_file():
            files.append(path)
            continue
        for child in sorted(path.rglob("*")):
            if not child.is_file():
                continue
            parts = child.relative_to(ROOT).parts
            if SKIP_DIRS.intersection(parts) or child.suffix == ".pyc":
                continue
            files.append(child)

    for file in files:
        relative = file.relative_to(ROOT)
        if FORBIDDEN_NAMES.intersection(relative.parts):
            sys.exit(f"refusing to package {file}")
    return files


def release_config() -> str:
    """Use the documented template with the safer iframe identity default."""
    source = ROOT / ".env.example"
    if not source.exists():
        sys.exit("required server release path not found: .env.example")
    text = source.read_text(encoding="utf-8")
    old = "ALLOW_CLIENT_USERNAME=true"
    if text.count(old) != 1:
        sys.exit(f"expected exactly one {old} setting in .env.example")
    return text.replace(old, "ALLOW_CLIENT_USERNAME=false")


def write_server_release(out: Path) -> None:
    files = gather_server_files()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files:
            arcname = file.relative_to(ROOT).as_posix()
            zf.write(file, arcname)
            print(f"  + server: {arcname}")
        zf.writestr("config.env.example", release_config())
        zf.writestr("DEPLOY.md", SERVER_DEPLOY_MD)
    print(f"\n{out}  ({out.stat().st_size / 1024:.0f} KB, {len(files) + 2} files)")


def write_embed_package(out: Path) -> None:
    license_path = ROOT / "LICENSE"
    if not license_path.exists():
        sys.exit("required embed package path not found: LICENSE")
    generated = {
        "README.md": EMBED_README_MD,
        "iframe.html": IFRAME_HTML,
        "Example3Uploader.razor": RAZOR_COMPONENT,
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in generated.items():
            zf.writestr(arcname, content)
            print(f"  + embed: {arcname}")
        zf.write(license_path, "LICENSE")
        print("  + embed: LICENSE")
    print(f"\n{out}  ({out.stat().st_size / 1024:.0f} KB, {len(generated) + 1} files)")


def build(out_dir: Path | None = None) -> tuple[Path, Path]:
    """Build both release archives and return their paths."""
    out_dir = out_dir or ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{date.today():%Y%m%d}"
    server_out = out_dir / f"arcgis-uploader-server-{stamp}.zip"
    embed_out = out_dir / f"arcgis-uploader-embed-{stamp}.zip"
    write_server_release(server_out)
    print()
    write_embed_package(embed_out)
    return server_out, embed_out


def main() -> None:
    build()


if __name__ == "__main__":
    main()
