# arcgis-uploader

A small web form where users drag-and-drop spatial data — a zipped shapefile,
GeoPackage, GeoJSON, KML or FlatGeobuf — and enter a project ID. The service
strips **all** attributes from the features, tags each one with the project ID
and the uploading user, and appends them to an ArcGIS Enterprise hosted
feature service.

**This is a reference implementation.** The Python/FastAPI service and the
vanilla-HTML forms are examples; teams are expected to re-implement the parts
they need on their own platform (MudBlazor, Leaflet, plain Python, .NET, ...).
Four example clients are included:

| | Page | Flow |
|---|---|---|
| **Example 1** | `/` or `/example1` ([static/example1.html](static/example1.html)) | one step: pick file → upload & append |
| **Example 2** | `/example2` or `/preview` ([static/example2.html](static/example2.html)) | pick file → **preview map + attribute table** → confirm → append |
| **Example 3** | `/example3` ([static/example3.html](static/example3.html)) | example 2 flow, with duplicate checks against the polygon target **and** read-only compare layers |
| **Example 4** | `/example4` ([static/example4.html](static/example4.html)) | example 3 flow, but ArcGIS sees the browser SSO/OAuth user instead of `.env` username/password or the uvicorn process account |

And two porting paths are documented below:

- [Porting just the client](#the-api-contract-porting-the-client) — keep this
  service running, replace the form with your own UI. You only need to
  reproduce one HTTP request.
- [Porting the whole pipeline](#the-arcgis-recipe-porting-the-whole-pipeline) —
  re-implement the server too. The exact sequence of ArcGIS REST calls is
  documented; nothing here depends on Python.

## How it works

```
browser ──POST file + project_id──▶ this service
                                      │ 1. read the file with GDAL (any vector format)
                                      │ 2. discard every attribute; normalize Z/M values
                                      │ 3. bucket by geometry family (point / line / polygon)
                                      │ 4. reproject to the target layer's spatial reference
                                      │ 5. convert to Esri JSON, attributes = { project_id, uploaded_by }
                                      │ 6. reject duplicates: same id field + Shape within 1 m
                                      ▼
                          ArcGIS Enterprise:  generateToken ──▶ layer info ──▶ query ──▶ addFeatures
```

Geometry handling rules (these matter to any port):

- A feature layer stores one geometry family, so uploads are split across up
  to three target layers: point, polyline, polygon.
- MultiPoint geometries are **exploded** into individual point features
  (a point layer cannot store multipoints). Multi-line and multi-polygon map
  natively onto Esri paths/rings and stay as single features.
- GeometryCollections are flattened; empty/null geometries are skipped and
  counted in the response.
- Z and M values are dropped for ordinary 2D layers. If a target layer has
  `hasZ=true`, output vertices include a non-null Z value so ArcGIS accepts
  the append.
- Esri ring winding is the **opposite** of GeoJSON: exterior rings clockwise,
  holes counter-clockwise.

## The ArcGIS recipe (porting the whole pipeline)

The server side is five plain REST calls — no Esri SDK required in any
language. GDAL/OGR (which did the file reading here) has bindings or
equivalents on every platform (.NET: MaxRev.Gdal / NetTopologySuite;
JS: gdal-async).

1. **Get a token** — by default, `POST {portal}/sharing/rest/generateToken`
   with `username`, `password`, `client=referer`, `referer={portal}`,
   `expiration=60`, `f=json` -> `{ "token": "...", "expires": 1718... }`.
   Cache it; refresh before `expires`. For Windows Integrated Authentication,
   set `ARCGIS_AUTH_MODE=iwa`: the server uses SSPI/Negotiate as the Windows
   account running the app and does not read or send `ARCGIS_USERNAME` or
   `ARCGIS_PASSWORD`.

2. **Read the layer's schema** — `POST {layerUrl}?f=json&token=...` →
   use `geometryType` to validate the target, `capabilities` to confirm
   `Create`, `fields` to confirm the project-ID field exists, and
   `extent.spatialReference.latestWkid` (fall back to `wkid`) as the
   reprojection target.

3. **Transform the features** — read the upload with GDAL/pyogrio, throw away all
   attributes, normalize dimensions, reproject to the layer's spatial reference, convert
   to Esri JSON observing the geometry rules listed under
   [How it works](#how-it-works). Attributes of every output feature are just
   `{ "<project_id_field>": "<form value>", "<username_field>": "Uploaded by <user>." }`
   plus `DUPLICATE_ID_FIELD` when that is a separate field. For shapefile
   uploads, set GDAL's `SHAPE_RESTORE_SHX=YES` if you want to tolerate missing
   `.shx` index files.

4. **Reject duplicates** — before appending, `POST {layerUrl}/query` with
   `where=<duplicate_id_field> = '<form value>'`, `returnGeometry=true`,
   and `outSR=<target wkid>`. Compare those Shapes with the outgoing Shapes
   in a metre CRS; this implementation refuses the append when Hausdorff
   distance is ≤ `DUPLICATE_TOLERANCE_M` (default 1 metre). If
   `DUPLICATE_COMPARE_LAYERS` is configured, query those read-only layers too,
   using each layer's configured id field, before deciding whether to append.

5. **Append** — `POST {layerUrl}/addFeatures` with form fields
   `features=<JSON array>`, `rollbackOnFailure=true`, `f=json`, `token=...`,
   in chunks of ≤ 500 features. **Check errors twice**: ArcGIS returns most
   failures inside an HTTP 200 body, either as a top-level `error` object or
   as per-feature `{ "success": false }` entries in `addResults`.

A feature posted to `addFeatures` looks like:

```json
{
  "geometry": { "rings": [[[ -135.1, 60.7 ], ...]], "spatialReference": { "wkid": 4326 } },
  "attributes": { "project_id": "2026-0042", "uploaded_by": "Uploaded by YG\\mwilkie." }
}
```

## Production notes

- The ArcGIS credential lives **server-side only**. Never call
  `generateToken` from browser code — a client port still needs a backend
  (or a pre-authorized proxy) holding the secret.
- The example does no user authentication; put it behind your reverse proxy /
  SSO and serve it over HTTPS.
- `rollbackOnFailure=true` is per 500-feature chunk: a failure mid-upload can
  leave earlier chunks committed. If that matters, load into a staging layer
  and move features after full success.


## Example quick start with reference implementation

Requires Python ≥ 3.10. With [uv](https://docs.astral.sh/uv/):

```powershell
uv sync
copy .env.example .env     # set DRY_RUN=true for a first look
uv run uvicorn app.main:app --reload
```

or with plain pip:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e . --group dev
copy .env.example .env
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. With `DRY_RUN=true` the full pipeline runs —
reading, stripping, reprojecting, Esri JSON conversion — but nothing is sent
to ArcGIS, and the response includes a `sample_feature` showing exactly what
*would* be posted. Set the `TARGET_LAYER_*` variables and `DRY_RUN=false` to
go live.

Run the tests with `uv run pytest` (or `pytest` in the activated venv).

To share the project, `python scripts/make_package.py` builds
`dist/arcgis-uploader-<date>.zip` — an emailable package of just the source,
tests, docs and lockfile. It includes files by allowlist, so `.env`
(credentials), `.venv` and caches can never end up in it.

## Configuration

All settings come from environment variables or a `.env` file
(see [.env.example](.env.example)):

| Variable | Purpose |
|---|---|
| `PORTAL_URL` | ArcGIS Enterprise portal, e.g. `https://maps.example.gov/portal` |
| `ARCGIS_AUTH_MODE` | `password` (default) uses `ARCGIS_USERNAME`/`ARCGIS_PASSWORD`; `iwa` uses Windows Integrated Authentication as the account running the service; `anonymous` sends no token |
| `ARCGIS_USERNAME` / `ARCGIS_PASSWORD` | Built-in account with edit rights on the target layers. Leave empty only if the layers allow anonymous editing. |
| `ARCGIS_OAUTH_CLIENT_ID` | OAuth client id for Example 4 browser SSO approval-code flow (default `arcgispro`) |
| `GENERATE_TOKEN_URL` | Override for standalone ArcGIS Server (default `$PORTAL_URL/sharing/rest/generateToken`) |
| `TARGET_LAYER_POINT` / `_POLYLINE` / `_POLYGON` | Feature layer URLs (ending `/0`, `/1`, …). Leave one blank to skip that geometry family. |
| `PROJECT_ID_FIELD` | Field that receives the form value (default `project_id`) |
| `PROJECT_ID_PATTERN` | Regex a submitted project ID must match |
| `USERNAME_FIELD` | Field that records who uploaded (default `uploaded_by`) |
| `USERNAME_HEADER` | Header carrying the authenticated user, set by the SSO/reverse proxy (default `X-Forwarded-User`) |
| `ALLOW_CLIENT_USERNAME` | Accept a `username` form field from calling apps (default `true`) |
| `DUPLICATE_DETECTION` | `true` = refuse appends when the destination already has the same id field and Shape (default `true`) |
| `DUPLICATE_ID_FIELD` | Field used for duplicate lookup. Defaults to `PROJECT_ID_FIELD`; set to `yesab_id` when that is the destination id field. If it differs from `PROJECT_ID_FIELD`, the form value is also written to this field. |
| `DUPLICATE_TOLERANCE_M` | Shape comparison tolerance in metres (default `1.0`) |
| `DUPLICATE_COMPARE_LAYERS` | Optional JSON array of extra read-only layers to check for duplicates. Each object has `id_field` and `url`. The target layer is still checked using `DUPLICATE_ID_FIELD`. |
| `MAX_UPLOAD_MB` | Upload size cap (default 200) |
| `SHAPE_RESTORE_SHX` | GDAL/pyogrio option. Set to `YES` to read shapefiles whose `.shx` index is missing by rebuilding the index. Included in `.env.example`. |
| `DEFAULT_SOURCE_EPSG` | CRS assumed for uploads that have none (e.g. shapefile missing `.prj`). Unset = reject them. |
| `DRY_RUN` | `true` = never contact ArcGIS, report what would happen |
| `BASEMAP_URL` | Optional basemap for the example 2 preview map: an XYZ tile template in Web Mercator. A portal-hosted tiled MapServer works directly: `…/MapServer/tile/{z}/{y}/{x}` |

### Shapefile uploads

For zipped shapefiles, include the normal sidecar files when possible:
`.shp`, `.shx`, `.dbf`, and `.prj`. The service sets/uses GDAL's
`SHAPE_RESTORE_SHX=YES` option in `.env.example`, which lets GDAL/pyogrio
rebuild a missing `.shx` index and continue reading the shapefile. A missing
`.prj` is different: the upload is still rejected unless `DEFAULT_SOURCE_EPSG`
is configured, because the service needs a source CRS before reprojecting to
the target layer.

### Target layer requirements

- An editable hosted feature layer per geometry family (they can be three
  layers of one feature service). `Create` capability must be enabled.
- A text field matching `PROJECT_ID_FIELD`, length ≥ 64, and one matching
  `USERNAME_FIELD`, length ≥ 128.
- When duplicate detection is enabled, the target layer must also support
  `Query` and contain `DUPLICATE_ID_FIELD` (usually the same field as
  `PROJECT_ID_FIELD`, or `yesab_id`).
- Any `DUPLICATE_COMPARE_LAYERS` must support `Query`, return geometry, and
  contain their configured `id_field`. They do not need `Create` permission
  because they are read-only duplicate sources.
- Plain 2D layers are fine — Z/M are stripped before append. Layers with
  `hasZ=true` are also supported; 2D uploads receive a default Z of `0.0`.

## Example implementation pages and API contract

The examples build on each other in this order:

### Example 1: one-step upload and append

Use `/` or `/example1` when the client should choose a file, enter a project
ID, and append immediately. An Example 1 style client needs exactly one
request:

```
POST /api/upload
Content-Type: multipart/form-data

file        the spatial file (.zip .gpkg .geojson .json .kml .fgb .shp)
project_id  text, validated against PROJECT_ID_PATTERN
username    optional - the calling app's authenticated user
            (see "Who did the upload?" below)
```

Successful response (`200`):

```json
{
  "project_id": "2026-0042",
  "uploaded_by": "YG\\mwilkie",
  "username_attribute_value": "Uploaded by YG\\mwilkie.",
  "layers_read": ["upload.zip:roads", "upload.zip:sites"],
  "features_appended": { "point": 12, "line": 340 },
  "features_skipped_no_target_layer": { "polygon": 2 },
  "features_skipped_invalid": 1,
  "dry_run": false
}
```

### Example 2: preview, then confirm append

Use `/example2` or `/preview` when the client should show a map and attribute
preview before appending. Example 2 adds one request before the Example 1
`/api/upload` confirmation request:

```
POST /api/preview
Content-Type: multipart/form-data

file      the spatial file (no project_id needed yet)
username  optional - same resolution as /api/upload
```

```json
{
  "layers": [
    { "layer": "upload.zip:roads", "feature_count": 120,
      "columns": ["name", "type"],
      "rows": [["Main St", "hwy"], ["2nd Ave", null]] }
  ],
  "feature_counts": { "line": 120 },
  "features_skipped_invalid": 0,
  "geojson": { "type": "FeatureCollection", "features": ["... in EPSG:4326 ..."] },
  "geojson_truncated": false,
  "uploaded_by": "YG\\mwilkie",
  "username_attribute_value": "Uploaded by YG\\mwilkie."
}
```

`rows` holds at most the first 5 attribute rows per layer - show them to the
user as *what will be removed*, visually distinct from what will be *added*
(the project ID plus `username_attribute_value`, which the preview resolves
the same way as the upload so the page can display it up front). The bundled
page renders one combined table per layer - added columns left-most in green,
existing columns in red with struck-through headers - and shows the map and a
skeleton of that table, muted, before any file is chosen. `geojson` is ready
for any web map and is capped at 2 000 features (`geojson_truncated` tells you
the map is partial; the append still gets everything). Nothing is stored
server-side between the two calls: on confirm, the client simply re-sends the
same file to `/api/upload`. That keeps every port stateless; if your files are
huge, cache the upload under a token server-side instead.

### Example 3: preview plus YESAB cross-layer duplicate checks

Example 3 uses the `/example3` page and the same two-request flow as example 2.
The difference is server-side configuration: when the user confirms, polygon
appends are refused if the submitted project ID and Shape already exist in
`TARGET_LAYER_POLYGON` **or** in the read-only YESAB project layers below.

Use JSON for structured `.env` values rather than ad-hoc comma parsing:

```dotenv
TARGET_LAYER_POLYGON=https://maps.gov.yk.ca/server/rest/services/ENV_YESAB/EA_Project_Areas3/FeatureServer/0
PROJECT_ID_FIELD=YESAB_ID

DUPLICATE_DETECTION=true
  # Defaults to PROJECT_ID_FIELD, so the target layer check uses YESAB_ID.
#DUPLICATE_ID_FIELD=YESAB_ID
DUPLICATE_COMPARE_LAYERS=[{"id_field":"attr_yesab_proj","url":"https://maps.gov.yk.ca/server/rest/services/ENV_YESAB/YESAB_Projects/FeatureServer/3"},{"id_field":"attr_yesab_proj","url":"https://maps.gov.yk.ca/server/rest/services/ENV_YESAB/YESAB_Projects/FeatureServer/4"},{"id_field":"attr_yesab_proj","url":"https://maps.gov.yk.ca/server/rest/services/ENV_YESAB/YESAB_Projects/FeatureServer/5"}]
```

On append, the duplicate guard checks:

1. `TARGET_LAYER_POLYGON` where `YESAB_ID = <submitted project id>`
2. `YESAB_Projects/FeatureServer/3` where `attr_yesab_proj = <submitted project id>`
3. `YESAB_Projects/FeatureServer/4` where `attr_yesab_proj = <submitted project id>`
4. `YESAB_Projects/FeatureServer/5` where `attr_yesab_proj = <submitted project id>`

Any matching Shape within `DUPLICATE_TOLERANCE_M` metres returns `409` and no
features are appended.

### Example 4: preview plus duplicate checks, using browser SSO for ArcGIS

Example 4 uses `/example4` and the same preview/confirm client flow as
example 3, except it does not send a user-typed `username` form field. The
browser calls `/api/preview` for preview and `/api/upload-browser-sso` for
confirm. Before confirming, the user clicks **Open ArcGIS sign-in**, completes
Portal/Windows SSO in the browser, and pastes the ArcGIS approval code or full
approval URL back into the page. The backend exchanges that code for an
ArcGIS token and uses it for layer info, duplicate queries, and `addFeatures`.

This is intentionally the browser-user path. It avoids the server-process
identity and Kerberos double-hop problem: ArcGIS authorizes the user who
approved the OAuth code, not the account running `uvicorn`.

Configuration:

```dotenv
PORTAL_URL=https://maps.gov.yk.ca/portal
# The default matches scripts/arcgis_iwa_token_check.py's browser SSO path.
ARCGIS_OAUTH_CLIENT_ID=arcgispro
```

For troubleshooting, run the diagnostic script in browser SSO mode:

```powershell
uv run scripts/arcgis_iwa_token_check.py --auth-mode oauth
```

If `--auth-mode generate-token` reports that `username` and `password` are
required, that Portal endpoint is not accepting direct server-side
Negotiate/NTLM for token generation. Use Example 4's browser SSO/OAuth path,
or put the app behind IIS/SSO with proper Kerberos delegation if you need a
fully transparent enterprise deployment.

### Shared detail: who did the upload?

Every appended feature carries a second attribute, `USERNAME_FIELD`
(default `uploaded_by`). Its value is written as
`Uploaded by {username}.` Browser JavaScript cannot read the OS username, so
`{username}` is resolved server-side, in order of preference:

1. the **`username` form field** - for third-party apps using this API that
   already authenticated their user (a MudBlazor port would send
   `User.Identity.Name`). Set `ALLOW_CLIENT_USERNAME=false` to ignore it -
   do that when browsers can reach the API directly, because anything a
   client sends can be spoofed from dev tools.
2. the **`USERNAME_HEADER` request header** (default `X-Forwarded-User`),
   set by the SSO/reverse proxy in front of the app (IIS Windows
   Authentication, oauth2-proxy, ...). Only trust it when the app is reachable
   solely through that proxy.
3. the literal `"unknown"` - bare development runs.

The bundled example forms include a typable `username` field as a simple
intranet/development workaround. In a real host application, do not ask the
user to type it if you need reliable attribution: populate the field from the
already-authenticated app user (for example `User.Identity.Name`) or use the
trusted reverse-proxy header path above. A user-editable field is convenient,
but it is not an audit control.

Each upload is also written to the server log:
`appended: user=... project=... file=... appended=...`.

### Shared detail: errors, server info, and client implementation notes

Errors return `{ "detail": "human-readable message" }` with:

| Status | Meaning |
|---|---|
| `413` | File exceeds `MAX_UPLOAD_MB` |
| `409` | Duplicate refused: same id field and Shape already exist in the destination or configured compare layers |
| `415` | File extension not accepted |
| `422` | Bad project ID, unreadable/empty file, no spatial layers, or missing CRS |
| `502` | ArcGIS Enterprise rejected a request (message says why) |

`GET /api/info` returns what the forms need to configure themselves - accepted
extensions, the project-ID regex, configured geometry families, the size
limit, the basemap URL, duplicate-detection settings, the ArcGIS auth mode,
and whether dry-run is on. Interactive OpenAPI docs are at `/docs`.

The reference forms are dependency-free JavaScript (examples 2, 3 and 4 pull
Leaflet from a CDN - vendor it into `static/` for intranet use), commented for
porting. A MudBlazor client would use `MudFileUpload` +
`HttpClient.PostAsync` with `MultipartFormDataContent` - the requests are the
same.
