// carwatch front-end glue: theme, compare tray (Alpine store), toasts, chart defaults.
(function () {
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  };

  // Theme: "auto" | "light" | "dark"
  const applyTheme = (t) => {
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
  };
  applyTheme(store.get("cw-theme", "auto"));
  window.cwCycleTheme = () => {
    const order = ["auto", "light", "dark"];
    const next = order[(order.indexOf(store.get("cw-theme", "auto")) + 1) % 3];
    store.set("cw-theme", next);
    applyTheme(next);
    window.dispatchEvent(new CustomEvent("cw-theme", { detail: next }));
  };

  document.addEventListener("alpine:init", () => {
    Alpine.store("cmp", {
      ids: store.get("cw-compare", []),
      max: 6,
      has(id) { return this.ids.includes(id); },
      toggle(id) {
        if (this.has(id)) this.ids = this.ids.filter((x) => x !== id);
        else if (this.ids.length < this.max) this.ids = [...this.ids, id];
        else window.cwToast(`Máximo de ${this.max} veículos no comparativo`, "err");
        store.set("cw-compare", this.ids);
      },
      clear() { this.ids = []; store.set("cw-compare", []); },
      url() { return "/compare?ids=" + this.ids.join(","); },
    });
    Alpine.store("toasts", { items: [] });
  });

  window.cwToast = (msg, kind = "ok") => {
    const t = Alpine.store("toasts");
    const item = { id: Date.now() + Math.random(), msg, kind };
    t.items.push(item);
    setTimeout(() => { t.items = t.items.filter((x) => x.id !== item.id); }, 3200);
  };
  document.body?.addEventListener?.("toast", (e) => window.cwToast(e.detail.msg, e.detail.kind));
  document.addEventListener("DOMContentLoaded", () => {
    document.body.addEventListener("toast", (e) => window.cwToast(e.detail.msg, e.detail.kind));
    const ok = new URLSearchParams(location.search).get("ok");
    if (ok) {
      setTimeout(() => window.cwToast(ok), 50);
      const u = new URL(location.href); u.searchParams.delete("ok");
      history.replaceState(null, "", u.pathname + u.search + u.hash);
    }
  });

  // Chart.js defaults pulled from CSS tokens so charts follow the theme.
  window.cwCss = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  window.cwPalette = ["#4f46e5", "#0891b2", "#d97706", "#db2777", "#059669", "#7c3aed", "#dc2626", "#2563eb", "#65a30d"];
  window.cwChartDefaults = () => {
    if (!window.Chart) return;
    Chart.defaults.font.family = cwCss("--font");
    Chart.defaults.font.size = 12;
    Chart.defaults.color = cwCss("--muted");
    Chart.defaults.borderColor = cwCss("--border");
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
    Chart.defaults.plugins.legend.labels.boxHeight = 10;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.maintainAspectRatio = false;
  };
  window.cwBRL = (v) => v == null ? "—" : "R$ " + Math.round(v).toLocaleString("pt-BR");
})();
