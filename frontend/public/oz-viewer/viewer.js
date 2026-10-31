// Starts OneZoom's viewer (vendor/OZentry.js) on this app's /api/oz/<source>/ endpoints,
// and talks to the page that embeds it with postMessage:
//   in:  {type: "goto", conceptId}  fly to a concept
//        {type: "view", view}       "polytomy" | "spiral" | "balanced" | "fern" | "natural"
//   out: {type: "ready"}, {type: "select", conceptId}, {type: "error", message}
// Query string: ?source=<source key>&view=<view>.
(async function () {
  if (!window.OZentry) return;
  // OneZoom's UI text (the page that hosts the viewer has to define it)
  window.OZstrings = await fetch("vendor/js_strings.json").then((r) => r.json());
  window.OZstrings["No common name"] = ""; // the store has no vernacular names
  const params = new URLSearchParams(location.search);
  const base = "/api/oz/" + encodeURIComponent(params.get("source") || "apweb");
  const post = (msg) => window.parent.postMessage(Object.assign({ from: "onezoom" }, msg), location.origin);

  // OneZoom requests data through jQuery.ajax; this is the part of it that it uses.
  // Everything goes out as GET with a query string. A URL of "#" answers {} at once.
  window.jQuery = window.$ = {
    ajax: function (o) {
      const done = () => o.complete && o.complete();
      if (!o.url || o.url === "#") {
        if (o.success) o.success({});
        done();
        return;
      }
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(o.data || {})) if (v !== undefined && v !== null) q.set(k, v);
      const ctl = new AbortController();
      const timer = o.timeout ? setTimeout(() => ctl.abort(), o.timeout) : null;
      fetch(q.size ? o.url + "?" + q : o.url, { signal: ctl.signal })
        .then((r) => {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(
          (json) => o.success && o.success(json),
          (e) => o.error && o.error(null, e.name === "AbortError" ? "timeout" : "error", e.message),
        )
        .finally(() => {
          clearTimeout(timer);
          done();
        });
    },
  };

  let oz = null;

  // The concept id travels in OneZoom's "ott" column (see backend apw/onezoom.py).
  function conceptAt(ozid) {
    const dr = oz.data_repo;
    const meta = ozid < 0 ? dr.metadata.leaf_meta[-ozid] : dr.metadata.node_meta[ozid];
    return meta ? meta[(ozid < 0 ? dr.mc_key_l : dr.mc_key_n).OTTid] : null;
  }

  oz = window.OZentry.default(
    {
      node_details_api: base + "/node_details",
      data_path_pics: "",
      static_data_url_func: (name) => base + "/" + name,
      OZ_leaf_json_url_func: () => "#",
      OZ_node_json_url_func: () => "#",
    },
    {
      // Clicking a name: OneZoom passes an "ott" for tips and an OZid for interior nodes.
      openLinkouts: (id, kind) => {
        const conceptId = kind === "ott" ? id : conceptAt(id);
        if (conceptId) post({ type: "select", conceptId: Number(conceptId) });
      },
      populateLinkouts: () => {},
      loadingMessage: (on) => document.body.classList.toggle("loading", on),
      badOTT: () => {},
    },
    null,
    "oz-canvas",
    { cols: "natural" },
  );
  window.onezoom = oz; // as on OneZoom's own pages; handy in the console
  // OneZoom's licence does not cover sponsorable trees; keep the "sponsor this leaf" text off.
  oz.config.projection.draw_sponsors = false;
  // Start in the default (spiral) view and switch once loaded: starting in the polytomy
  // view directly leaves OneZoom's binary node classes in place and the first flight fails.
  oz.add_hook("on_tree_loaded", () => {
    const view = params.get("view");
    if (!view || view === "spiral") return post({ type: "ready" });
    // change_view_type tries to restore the previous position, and nothing is visible yet
    oz.controller.change_view_type(view)
      .catch(() => {})
      .then(() => oz.controller.reset())
      .then(() => oz.controller.trigger_refresh_loop())
      .finally(() => post({ type: "ready" }));
  });

  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data || e.source !== window.parent) return;
    const m = e.data;
    if (m.type === "goto") {
      fetch(base + "/locate/" + encodeURIComponent(m.conceptId))
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("not in this tree"))))
        .then((r) => oz.controller.fly_on_tree_to(null, r.ozid, false))
        .catch((err) => post({ type: "error", message: String(err.message || err) }));
    } else if (m.type === "view") {
      oz.controller.change_view_type(m.view);
    }
  });
})();
