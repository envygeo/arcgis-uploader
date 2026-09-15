// _codex-unknown-model- on behalf of Matt Wilkie_
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("static/assets/preview-context.js", "utf8");
const defaults = JSON.parse(fs.readFileSync("app/preview-map.json", "utf8"));

function setup(info = {}, missingPlugin = false) {
  info.preview_map ||= structuredClone(defaults);
  if (info.basemap_url) {
    Object.assign(info.preview_map.basemap, {
      url: info.basemap_url, type: "xyz_tiles", attribution: info.basemap_attribution || "",
    });
  }
  const elements = [];
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.style = {}; elements.push(this); }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    after(child) { this.sibling = child; }
    setAttribute(key, value) { this[key] = value; }
    addEventListener(name, callback) { this.events[name] = callback; }
    fire(name) { this.events[name]?.(); }
  }
  class Events {
    constructor() { this.events = {}; }
    on(names, callback) {
      for (const name of names.split(" ")) (this.events[name] ||= new Set()).add(callback);
      return this;
    }
    off(names, callback) {
      for (const name of names.split(" ")) {
        if (callback) this.events[name]?.delete(callback);
        else this.events[name]?.clear();
      }
      return this;
    }
    fire(name) { for (const callback of this.events[name] || []) callback(); }
  }
  const map = new Events();
  const container = new Element("map");
  map.getContainer = () => container;
  map.panes = {};
  map.createPane = name => (map.panes[name] = new Element("pane"));
  map.layers = new Set();
  map.eachLayer = callback => { for (const layer of [...map.layers]) callback(layer); };
  const layers = [];
  class Layer extends Events {
    constructor(options, tiled) { super(); this.options = options; this.tiled = tiled; layers.push(this); }
    addTo(target) { assert.equal(target, map); map.layers.add(this); this.fire("loading"); return this; }
    remove() { map.layers.delete(this); }
    redraw() { this.redraws = (this.redraws || 0) + 1; this.fire("loading"); }
    setOpacity(value) { this.options.opacity = value; }
  }
  class ImageOverlay extends Layer {
    getPane() { return map.panes[this.options.pane]; }
  }
  const timers = new Map();
  let timerId = 0;
  const env = {
    window: {},
    document: { createElement: tag => new Element(tag), createTextNode: text => ({ text }) },
    L: { ImageOverlay,
      map: (id, options) => {
        map.options = options;
        map.setView = (center, zoom) => { map.center = center; map.zoom = zoom; return map; };
        return map;
      },
      esri: missingPlugin ? undefined : { dynamicMapLayer: options => new Layer(options, false) },
      tileLayer: (url, options) => new Layer({ ...options, url }, true) },
    setTimeout: (fn, ms) => { assert.equal(ms, info.preview_map.request_timeout_ms); timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
  };
  vm.runInNewContext(source, env);
  env.window.createPreviewMap("map", info);
  return {
    map, layers, timers, ImageOverlay,
    inputs: elements.filter(e => e.type === "checkbox"),
    opacity: elements.find(e => e.type === "range"),
    output: elements.find(e => e.tag === "output"),
    status: elements.find(e => e.role === "status"),
    retry: elements.find(e => e.tag === "button"),
  };
}

test("four logical controls use paired scale-dependent claims and only requested sublayers", () => {
  const s = setup();
  assert.equal(s.inputs.length, 4);
  assert.equal(s.map.zoom, defaults.view.zoom);
  assert.deepEqual(s.map.center, defaults.view.center);
  assert.equal(s.map.options.minZoom, defaults.view.min_zoom);
  assert.equal(s.map.options.maxZoom, defaults.view.max_zoom);
  assert.ok(s.inputs.every(input => input.checked));
  assert.equal(s.layers.length, 2);
  const [basemap, context] = s.layers;
  assert.match(basemap.options.url, /Yukon_Basemap_Cache\/MapServer$/);
  assert.equal(basemap.options.transparent, false);
  assert.match(context.options.url, /GeoYukon\/GY_Mining\/MapServer$/);
  assert.deepEqual(Array.from(context.options.layers), [35, 36, 10, 11, 39, 16]);
  assert.equal(context.options.opacity, 0.65);
  assert.equal(context.options.transparent, true);
  assert.equal(s.map.panes.previewContext.style.zIndex, "300");
  assert.equal(s.map.panes.previewBasemap.style.zIndex, "200");
  assert.equal(s.map.panes.previewContext.style.pointerEvents, "none");
  for (const layer of s.layers) {
    assert.equal(layer.options.token, undefined);
    assert.equal(layer.options.timeout, 15000);
    layer.fire("load");
  }
  assert.equal(s.status.textContent, "");
  assert.equal(s.timers.size, 0);
});

test("browser consumes supplied configuration rather than hidden map defaults", () => {
  const config = structuredClone(defaults);
  config.view = { center: [61, -140], zoom: 8, min_zoom: 3, max_zoom: 16 };
  config.context.opacity = 0.4;
  config.context.url = "https://context.test/MapServer";
  config.context.groups = [{ name: "Example", ids: [7, 8], enabled: true }];
  config.request_timeout_ms = 2000;
  const s = setup({ preview_map: config });
  assert.deepEqual(s.map.center, [61, -140]);
  assert.equal(s.map.zoom, 8);
  assert.equal(s.map.options.maxZoom, 16);
  assert.equal(s.inputs.length, 1);
  assert.equal(s.output.textContent, "40%");
  assert.equal(s.layers[1].options.url, "https://context.test/MapServer");
  assert.deepEqual(Array.from(s.layers[1].options.layers), [7, 8]);
  assert.equal(s.layers[1].options.opacity, 0.4);
  assert.equal(s.layers[1].options.timeout, 2000);
});

test("toggles remove both claims scales, all-off removes overlay, stale events are ignored", () => {
  const s = setup();
  const old = s.layers[1];
  const pendingImage = new s.ImageOverlay({ pane: "previewContext" });
  pendingImage.on("load", () => { throw new Error("retired image callback ran"); });
  pendingImage.on("error", () => { throw new Error("retired image error ran"); });
  pendingImage.addTo(s.map);
  s.inputs[0].checked = false;
  s.inputs[0].fire("change");
  assert.deepEqual(Array.from(s.layers.at(-1).options.layers), [10, 11, 39, 16]);
  assert.equal(s.map.layers.has(old), false);
  assert.equal(s.map.layers.has(pendingImage), false);
  pendingImage.fire("load");
  pendingImage.fire("error");
  for (const input of s.inputs) { input.checked = false; input.fire("change"); }
  assert.equal(s.map.layers.size, 1);
  assert.match(s.status.textContent, /Mining context: off/);
  const status = s.status.textContent;
  old.fire("error");
  old.fire("load");
  assert.equal(s.status.textContent, status);
  s.inputs[0].checked = true;
  s.inputs[0].fire("change");
  assert.deepEqual(Array.from(s.layers.at(-1).options.layers), [35, 36]);
});

test("opacity changes only mining context and survives toggles", () => {
  const s = setup();
  s.opacity.value = "30";
  s.opacity.fire("input");
  assert.equal(s.layers[1].options.opacity, 0.3);
  assert.equal(s.layers[0].options.opacity, 1);
  assert.equal(s.output.textContent, "30%");
  s.inputs[2].checked = false;
  s.inputs[2].fire("change");
  assert.equal(s.layers.at(-1).options.opacity, 0.3);
  assert.ok(!s.layers.at(-1).options.layers.includes(39));
});

test("service errors, failed images and timeouts hide context without removing upload geometry", () => {
  for (const event of ["requesterror", "error", "timeout"]) {
    const s = setup();
    const geometry = {};
    s.map.layers.add(geometry);
    s.layers[0].fire("load");
    if (event === "timeout") for (const fn of [...s.timers.values()]) fn();
    else s.layers[1].fire(event);
    assert.match(s.status.textContent, /unavailable.*You can still preview and upload/);
    assert.equal(s.map.panes.previewContext.style.visibility, "hidden");
    assert.equal(s.map.layers.has(geometry), true);
    assert.equal(s.retry.hidden, false);
    s.retry.fire("click");
    assert.equal(s.layers[0].redraws, 1);
    s.layers[0].fire("load");
    s.layers.at(-1).fire("load");
    assert.equal(s.map.panes.previewContext.style.visibility, "");
    assert.equal(s.status.textContent, "");
    assert.equal(s.retry.hidden, true);
    s.map.fire("unload");
    assert.equal(s.timers.size, 0);
  }
});

test("custom basemap URL and attribution are preserved and failed tiles remain reported", () => {
  const s = setup({ basemap_url: "https://tiles.test/{z}/{x}/{y}", basemap_attribution: "Tile provider" });
  const tiles = s.layers[0];
  assert.equal(tiles.tiled, true);
  assert.equal(tiles.options.url, "https://tiles.test/{z}/{x}/{y}");
  assert.equal(tiles.options.attribution, "Tile provider");
  tiles.fire("tileerror");
  tiles.fire("load");
  assert.match(s.status.textContent, /Basemap: unavailable/);
  tiles.redraw();
  tiles.fire("load");
  assert.doesNotMatch(s.status.textContent, /Basemap:/);
});

test("missing optional Esri library does not throw or prevent custom tiles", () => {
  const s = setup({}, true);
  assert.match(s.status.textContent, /map library did not load/);
  assert.equal(s.layers.length, 0);
  const custom = setup({ basemap_url: "https://tiles.test/{z}/{x}/{y}" }, true);
  assert.equal(custom.layers.length, 1);
  assert.match(custom.status.textContent, /Mining context: unavailable/);
});
