# arcgis-uploader

A small web form where users drag-and-drop spatial data — a zipped shapefile,
GeoPackage, GeoJSON, KML or FlatGeobuf — and enter a project ID. The service
strips **all** attributes from the features, tags each one with the project ID
and the uploading user, and appends them to an ArcGIS Enterprise hosted
feature service.

**This is a reference implementation.** The Python/FastAPI service and the
vanilla-HTML forms are examples; teams are expected to re-implement the parts
they need on their own platform (MudBlazor, Leaflet, plain Python, .NET, …).
Two example clients are included:

| | Page | Flow |
|---|---|---|
| **Example 1** | `/` ([static/index.html](static/index.html)) | one step: pick file → upload & append |
| **Example 2** | `/preview` ([static/preview.html](static/preview.html)) | pick file → **preview map + attribute table** → confirm → append |

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

## Quick start (no ArcGIS required)

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
| `ARCGIS_USERNAME` / `ARCGIS_PASSWORD` | Built-in account with edit rights on the target layers. Leave empty only if the layers allow anonymous editing. |
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
- Plain 2D layers are fine — Z/M are stripped before append. Layers with
  `hasZ=true` are also supported; 2D uploads receive a default Z of `0.0`.

## The API contract (porting the client)

An **example 1** style client needs exactly one request:

```
POST /api/upload
Content-Type: multipart/form-data

file        the spatial file (.zip .gpkg .geojson .json .kml .fgb .shp)
project_id  text, validated against PROJECT_ID_PATTERN
username    optional — the calling app's authenticated user
            (see "Who did the upload?" below)
```

Successful response (`200`):

```json
{
  "project_id": "2026-0042",
  "uploaded_by": "YG\\mwilkie",
  "layers_read": ["upload.zip:roads", "upload.zip:sites"],
  "features_appended": { "point": 12, "line": 340 },
  "features_skipped_no_target_layer": { "polygon": 2 },
  "features_skipped_invalid": 1,
  "dry_run": false
}
```

### Who did the upload?

Every appended feature carries a second attribute, `USERNAME_FIELD`
(default `uploaded_by`). Browser JavaScript cannot read the OS username, so
the value is resolved server-side, in order of preference:

1. the **`username` form field** — for third-party apps using this API that
   already authenticated their user (a MudBlazor port would send
   `User.Identity.Name`). Set `ALLOW_CLIENT_USERNAME=false` to ignore it —
   do that when browsers can reach the API directly, because anything a
   client sends can be spoofed from dev tools.
2. the **`USERNAME_HEADER` request header** (default `X-Forwarded-User`),
   set by the SSO/reverse proxy in front of the app (IIS Windows
   Authentication, oauth2-proxy, …). The bundled example forms rely on
   this. Only trust it when the app is reachable solely through that proxy.
3. the literal `"unknown"` — bare development runs.

Each upload is also written to the server log:
`appended: user=... project=... file=... appended=...`.

Errors return `{ "detail": "human-readable message" }` with:

| Status | Meaning |
|---|---|
| `413` | File exceeds `MAX_UPLOAD_MB` |
| `409` | Duplicate refused: same id field and Shape already exist in the destination |
| `415` | File extension not accepted |
| `422` | Bad project ID, unreadable/empty file, no spatial layers, or missing CRS |
| `502` | ArcGIS Enterprise rejected a request (message says why) |

`GET /api/info` returns what the form needs to configure itself — accepted
extensions, the project-ID regex, configured geometry families, the size
limit, the basemap URL, duplicate-detection settings, and whether dry-run is
on. Interactive OpenAPI docs are at `/docs`.

An **example 2** style client adds one more request before confirming:

```
POST /api/preview
Content-Type: multipart/form-data

file      the spatial file (no project_id needed yet)
username  optional — same resolution as /api/upload
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
  "geojson": { "type": "FeatureCollection", "features": ["… in EPSG:4326 …"] },
  "geojson_truncated": false,
  "uploaded_by": "YG\\mwilkie"
}
```

`rows` holds at most the first 5 attribute rows per layer — show them to the
user as *what will be removed*, visually distinct from what will be *added*
(the project ID plus `uploaded_by`, which the preview resolves the same way
as the upload so the page can display it up front). The bundled page renders
one combined table per layer — added columns left-most in green, existing
columns in red with struck-through headers — and shows the map and a
skeleton of that table, muted, before any file is chosen. `geojson` is ready for any web map and is
capped at 2 000 features (`geojson_truncated` tells you the map is partial;
the append still gets everything). Nothing is stored server-side between the
two calls: on confirm, the client simply re-sends the same file to
`/api/upload`. That keeps every port stateless; if your files are huge, cache
the upload under a token server-side instead.

The reference forms are dependency-free JavaScript (example 2 pulls Leaflet
from a CDN — vendor it into `static/` for intranet use), commented for
porting. A MudBlazor client would use `MudFileUpload` +
`HttpClient.PostAsync` with `MultipartFormDataContent` — the requests are
the same.

## The ArcGIS recipe (porting the whole pipeline)

The server side is five plain REST calls — no Esri SDK required in any
language. GDAL/OGR (which did the file reading here) has bindings or
equivalents on every platform (.NET: MaxRev.Gdal / NetTopologySuite;
JS: gdal-async).

1. **Get a token** — `POST {portal}/sharing/rest/generateToken` with
   `username`, `password`, `client=referer`, `referer={portal}`,
   `expiration=60`, `f=json` → `{ "token": "...", "expires": 1718... }`.
   Cache it; refresh before `expires`.

2. **Read the layer's schema** — `POST {layerUrl}?f=json&token=...` →
   use `geometryType` to validate the target, `capabilities` to confirm
   `Create`, `fields` to confirm the project-ID field exists, and
   `extent.spatialReference.latestWkid` (fall back to `wkid`) as the
   reprojection target.

3. **Transform the features** — read the upload with GDAL/pyogrio, throw away all
   attributes, normalize dimensions, reproject to the layer's spatial reference, convert
   to Esri JSON observing the geometry rules listed under
   [How it works](#how-it-works). Attributes of every output feature are just
   `{ "<project_id_field>": "<form value>", "<username_field>": "<user>" }`
   plus `DUPLICATE_ID_FIELD` when that is a separate field. For shapefile
   uploads, set GDAL's `SHAPE_RESTORE_SHX=YES` if you want to tolerate missing
   `.shx` index files.

4. **Reject duplicates** — before appending, `POST {layerUrl}/query` with
   `where=<duplicate_id_field> = '<form value>'`, `returnGeometry=true`,
   and `outSR=<target wkid>`. Compare those Shapes with the outgoing Shapes
   in a metre CRS; this implementation refuses the append when Hausdorff
   distance is ≤ `DUPLICATE_TOLERANCE_M` (default 1 metre).

5. **Append** — `POST {layerUrl}/addFeatures` with form fields
   `features=<JSON array>`, `rollbackOnFailure=true`, `f=json`, `token=...`,
   in chunks of ≤ 500 features. **Check errors twice**: ArcGIS returns most
   failures inside an HTTP 200 body, either as a top-level `error` object or
   as per-feature `{ "success": false }` entries in `addResults`.

A feature posted to `addFeatures` looks like:

```json
{
  "geometry": { "rings": [[[ -135.1, 60.7 ], ...]], "spatialReference": { "wkid": 4326 } },
  "attributes": { "project_id": "2026-0042", "uploaded_by": "YG\\mwilkie" }
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
