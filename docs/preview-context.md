# Preview background maps

Examples 2, 3 and 4 share `static/assets/preview-context.js`. The upload and
duplicate-check pipelines are unchanged. No portal login or editing token is
used for background maps.

## Default layers

The basemap is the public
[Yukon topographic basemap](https://mapservices.gov.yk.ca/arcgis/rest/services/Yukon_Basemap_Cache/MapServer).
Its cache uses Yukon Albers (EPSG:3578), not Web Mercator XYZ tiles. Esri Leaflet
requests server-rendered exports in the map's EPSG:3857 instead of attempting
to use those cached tiles directly.

Four checked-by-default controls select sublayers from the public
[GeoYukon mining service](https://mapservices.gov.yk.ca/arcgis/rest/services/GeoYukon/GY_Mining/MapServer):

| Control | Sublayer IDs | Published scale ranges |
| --- | --- | --- |
| Quartz claims | 35 (1M), 36 (50k) | 1:4,000,001 to 1:250,001; then 1:250,000 to 1:1,000 |
| Placer claims | 10 (1M), 11 (50k) | 1:20,000,000 to 1:400,001; then 1:400,000 to 1:1,000 |
| Quartz land use permits | 39 | 1:2,000,000 to 1:1,000 |
| Placer land use permits | 16 | 1:2,000,000 to 1:1,000 |

Both claims resolutions are included in the `layers=show:` export request.
ArcGIS applies its published scale visibility, so there is no client-side
latitude/zoom approximation or custom renderer to maintain. No annotation or
other group sublayers are included. Unchecking a claims control removes both
resolutions. Turning all four off removes the mining overlay entirely, rather
than sending an empty layer list that could display service defaults.

Backgrounds occupy separate non-interactive panes beneath uploaded geometry.
The opacity slider affects mining context only. Changing or resetting an upload
does not reset the background controls. Published scale ranges still apply:
at regional scales, zoom in to see permits. The map is constrained to zooms
5-18, keeping the default view regional and single-point previews within a
useful inspection scale.

These layers are visual context only. Their absence on screen does not prove
that no claims or permits exist, and they do not add overlap/eligibility checks
to the upload pipeline.

## Deployment and availability

- Leaflet 1.9.4 and Esri Leaflet 3.0.19 are bundled under `static/assets/`, with
  their licenses. No mapping-library CDN is needed by the preview pages.
- Browsers need HTTPS access to `mapservices.gov.yk.ca`, including its REST
  export images. If using a Content-Security-Policy, allow that origin for
  `connect-src` and `img-src` (plus your configured tile provider).
- The browser sends map extents and selected public layer IDs directly to the
  map service. Uploaded features, attributes, project IDs and edit tokens are
  not sent with these requests, but the extent can reveal the area being viewed.
- Attribution is taken from service metadata. A custom Web Mercator XYZ
  basemap may be supplied with `BASEMAP_URL`; supply its provider attribution
  in `BASEMAP_ATTRIBUTION` (trusted operator HTML). Blank `BASEMAP_URL` selects
  the Yukon basemap.
- Loading, service errors, image failures and a 15-second loading timeout are
  reported beside the controls, independently of preview/upload status.
  Failed imagery is hidden. Use **Retry background maps** to reload; previewing
  and appending remain available during a background outage.
- Public services can change layer IDs or availability. If they change, update
  the fixed layer mapping in `preview-context.js` and its tests.

Implementation reference: [Esri Leaflet dynamic map layer](https://developers.arcgis.com/esri-leaflet/api-reference/esri-leaflet/dynamic-map-layer/).
