/* _codex-unknown-model- on behalf of Matt Wilkie_ */
(function () {
  "use strict";

  const ROOT = "https://mapservices.gov.yk.ca/arcgis/rest/services/";
  const MINING = ROOT + "GeoYukon/GY_Mining/MapServer";
  const BASEMAP = ROOT + "Yukon_Basemap_Cache/MapServer";
  const GROUPS = [
    { name: "Quartz claims", ids: [35, 36] },
    { name: "Placer claims", ids: [10, 11] },
    { name: "Quartz land use permits", ids: [39] },
    { name: "Placer land use permits", ids: [16] },
  ];

  window.addPreviewContext = function (map, info = {}) {
    const host = document.createElement("fieldset");
    host.className = "map-context";
    const legend = document.createElement("legend");
    legend.textContent = "Map context";
    host.appendChild(legend);
    map.getContainer().after(host);

    const choices = document.createElement("div");
    choices.className = "map-context-choices";
    host.appendChild(choices);
    const inputs = GROUPS.map(group => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = true;
      label.append(input, document.createTextNode(group.name));
      choices.appendChild(label);
      return input;
    });

    const opacityLabel = document.createElement("label");
    opacityLabel.className = "map-context-opacity";
    const opacity = document.createElement("input");
    opacity.type = "range";
    opacity.min = "0";
    opacity.max = "100";
    opacity.step = "5";
    opacity.value = "65";
    const percent = document.createElement("output");
    percent.textContent = "65%";
    opacityLabel.append(document.createTextNode("Context opacity"), opacity, percent);
    host.appendChild(opacityLabel);

    const note = document.createElement("p");
    note.className = "hint flush";
    note.textContent = "Claims switch from 1M to 50k detail as you zoom in. " +
      "Layers only draw at their published scales; zoom in for permits. " +
      "Reference only, not an overlap or eligibility check.";
    host.appendChild(note);
    const status = document.createElement("p");
    status.className = "map-context-status";
    status.setAttribute("role", "status");
    host.appendChild(status);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "secondary";
    retry.textContent = "Retry background maps";
    retry.hidden = true;
    host.appendChild(retry);

    const states = { Basemap: "", "Mining context": "" };
    function report(name, state) {
      states[name] = state;
      status.textContent = Object.entries(states)
        .filter(([, value]) => value)
        .map(([key, value]) => `${key}: ${value}.`).join(" ");
      retry.hidden = !Object.values(states).some(value => value.startsWith("unavailable"));
      if (!retry.hidden) status.textContent += " You can still preview and upload.";
    }

    function pane(name, zIndex) {
      const element = map.createPane(name);
      element.style.zIndex = String(zIndex);
      element.style.pointerEvents = "none";
      return element;
    }
    const basemapPane = pane("previewBasemap", 200);
    const contextPane = pane("previewContext", 300);
    const stops = new Set();

    function watch(layer, name, element, tiled = false) {
      let timer;
      let tileFailed = false;
      function failed() {
        clearTimeout(timer);
        element.style.visibility = "hidden";
        report(name, "unavailable (network or service error)");
      }
      function loading() {
        clearTimeout(timer);
        tileFailed = false;
        report(name, "loading");
        timer = setTimeout(failed, 15000);
      }
      function loaded(event) {
        if (event?.bounds && !event.bounds.equals(map.getBounds())) return;
        if (tileFailed) return;
        clearTimeout(timer);
        element.style.visibility = "";
        report(name, "");
      }
      function tileError() { tileFailed = true; failed(); }
      layer.on("loading", loading).on("load", loaded)
        .on("requesterror error", failed);
      if (tiled) layer.on("tileerror", tileError);
      function stop() {
        clearTimeout(timer);
        layer.off("loading", loading).off("load", loaded)
          .off("requesterror error", failed).off("tileerror", tileError);
        stops.delete(stop);
      }
      stops.add(stop);
      return stop;
    }

    const canExport = typeof L.esri?.dynamicMapLayer === "function";
    let basemap;
    if (info.basemap_url) {
      basemap = L.tileLayer(info.basemap_url, {
        pane: "previewBasemap", maxZoom: 18,
        attribution: info.basemap_attribution || "",
      });
      watch(basemap, "Basemap", basemapPane, true);
    } else if (canExport) {
      basemap = L.esri.dynamicMapLayer({
        url: BASEMAP, pane: "previewBasemap", opacity: 1,
        format: "png32", transparent: false, timeout: 15000,
      });
      watch(basemap, "Basemap", basemapPane);
    } else {
      report("Basemap", "unavailable (map library did not load)");
    }
    if (basemap) basemap.addTo(map);

    let context;
    let stopContext;
    function refreshContext() {
      if (stopContext) stopContext();
      // Retire pending Esri image callbacks before detaching their parent.
      // This pane belongs only to this context layer, never upload geometry.
      // _codex-unknown-model- on behalf of Matt Wilkie_
      map.eachLayer(layer => {
        if (layer instanceof L.ImageOverlay && layer.getPane() === contextPane) {
          layer.off("load error");
          layer.remove();
        }
      });
      if (context) { context.remove(); context = null; }
      const ids = GROUPS.flatMap((group, i) => inputs[i].checked ? group.ids : []);
      if (!ids.length) { report("Mining context", "off"); return; }
      if (!canExport) {
        report("Mining context", "unavailable (map library did not load)");
        return;
      }
      context = L.esri.dynamicMapLayer({
        url: MINING, layers: ids, pane: "previewContext",
        opacity: Number(opacity.value) / 100,
        format: "png32", transparent: true, timeout: 15000,
      });
      stopContext = watch(context, "Mining context", contextPane);
      context.addTo(map);
    }
    inputs.forEach(input => input.addEventListener("change", refreshContext));
    opacity.addEventListener("input", () => {
      percent.textContent = opacity.value + "%";
      if (context) context.setOpacity(Number(opacity.value) / 100);
    });
    retry.addEventListener("click", () => {
      if (basemap) basemap.redraw();
      refreshContext();
    });
    map.on("unload", () => { for (const stop of [...stops]) stop(); });
    refreshContext();
  };
})();
