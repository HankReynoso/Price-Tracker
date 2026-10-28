const $ = (sel, root = document) => root.querySelector(sel);
const money = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" });
const shortDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const longDate = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" });
const UNDO_MS = 5000;
const ERROR_MS = 8000;

const state = {
  products: [],
  // One activity per product; a missing entry means idle.
  // {kind: "busy", text} | {kind: "error", text} | {kind: "removing"}
  activity: new Map(),
  openId: null,
  removal: null,
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function describe(product) {
  const best = product.lowest;
  const target = product.target_price ?? null;
  const history = product.history || [];
  const change = best && history.length > 1 ? Math.round((best.price - history[0].price) * 100) / 100 : 0;
  const targetMet = best != null && target != null && best.price <= target;
  let cue = null;
  if (!best) cue = { kind: "none", icon: "", text: "No new listings", more: "" };
  else if (targetMet) cue = { kind: "target", icon: "✓", text: best.price < target ? "Under target" : "At target", more: ` of ${money.format(target)}` };
  else if (change < 0) cue = { kind: "down", icon: "↓", text: money.format(-change), more: " since added" };
  else if (change > 0) cue = { kind: "up", icon: "↑", text: money.format(change), more: " since added" };
  return { best, target, change, targetMet, cue };
}

function ago(iso) {
  const seconds = Math.min(0, (new Date(iso) - Date.now()) / 1000);
  for (const [unit, size] of [["day", 86400], ["hour", 3600], ["minute", 60]]) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function link(href, text) {
  const a = el("a", null, text);
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener";
  return a;
}

function renderArt(slot, product) {
  const initial = () => (slot.textContent = product.name.trim().charAt(0).toUpperCase());
  const src = product.image || product.offers?.find((o) => o.thumbnail)?.thumbnail;
  if (!src) return initial();
  const img = el("img");
  img.src = src;
  img.alt = "";
  img.onerror = () => {
    img.remove();
    initial();
  };
  slot.append(img);
}

function renderMeta(meta, product, view, activity) {
  if (activity?.kind === "busy") return meta.append(activity.text);
  if (activity?.kind === "error") {
    meta.classList.add("error");
    return meta.append(`! ${activity.text}`);
  }
  if (view.best) meta.append(el("span", null, view.best.store), " · ");
  const time = el("time");
  time.dataset.ago = product.checked_at;
  time.dateTime = product.checked_at;
  time.title = longDate.format(new Date(product.checked_at));
  time.textContent = `checked ${ago(product.checked_at)}`;
  meta.append(time);
}

function renderOffers(row, product) {
  const offers = product.offers || [];
  $(".offers", row).hidden = !offers.length;
  $(".no-offers", row).hidden = offers.length > 0;
  const tbody = $("tbody", row);
  offers.forEach((offer, i) => {
    const tr = tbody.insertRow();
    const store = tr.insertCell();
    store.textContent = offer.store;
    if (i === 0) {
      tr.className = "lowest";
      store.append(el("span", "tag", "Lowest"));
    }
    const delivery = tr.insertCell();
    delivery.className = "delivery";
    delivery.textContent = offer.delivery || "";
    const price = tr.insertCell();
    price.className = "num";
    price.textContent = money.format(offer.price);
    const go = tr.insertCell();
    go.className = "go";
    if (offer.url) go.append(link(offer.url, "View"));
  });
}

function sparkline(history) {
  if (history.length < 2) return el("p", "hint", "Price history appears after the next check.");
  const prices = history.map((h) => h.price);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  const last = history.at(-1);
  const x = (i) => (i / (history.length - 1)) * 100;
  const y = (price) => (high === low ? 50 : 10 + (1 - (price - low) / (high - low)) * 80);
  const at = (i) => `left:${x(i)}%;top:${y(prices[i])}%`;

  const figure = el("figure", "spark");
  figure.setAttribute("role", "img");
  figure.setAttribute(
    "aria-label",
    `Lowest price over ${history.length} checks: low ${money.format(low)}, high ${money.format(high)}, now ${money.format(last.price)} at ${last.store}.`,
  );
  const points = prices.map((p, i) => `${x(i)},${y(p)}`).join(" ");
  figure.innerHTML = `
    <div class="plot" aria-hidden="true">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="${points}" /></svg>
      <span class="dot current" style="${at(history.length - 1)}"></span>
      <span class="cross" hidden></span>
      <span class="dot hover" hidden></span>
      <span class="tip" hidden></span>
    </div>
    <figcaption aria-hidden="true">
      <span>${shortDate.format(new Date(history[0].at))}</span>
      <span>Low ${money.format(low)} · High ${money.format(high)}</span>
      <span>${shortDate.format(new Date(last.at))}</span>
    </figcaption>`;

  const plot = $(".plot", figure);
  const hoverParts = [".cross", ".dot.hover", ".tip"].map((sel) => $(sel, plot));
  const [cross, dot, tip] = hoverParts;
  plot.addEventListener("pointermove", (event) => {
    const box = plot.getBoundingClientRect();
    const fraction = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    const i = Math.round(fraction * (history.length - 1));
    cross.style.left = `${x(i)}%`;
    dot.style.cssText = at(i);
    tip.style.left = `${x(i)}%`;
    tip.style.transform = `translateX(${-x(i)}%)`;
    tip.textContent = `${shortDate.format(new Date(history[i].at))} · ${money.format(prices[i])} · ${history[i].store}`;
    hoverParts.forEach((part) => (part.hidden = false));
  });
  plot.addEventListener("pointerleave", () => hoverParts.forEach((part) => (part.hidden = true)));
  return figure;
}

function renderTile(product) {
  const tile = $("#tile-tpl").content.firstElementChild.cloneNode(true);
  const activity = state.activity.get(product.id);
  tile.dataset.id = product.id;
  tile.hidden = activity?.kind === "removing";
  tile.classList.toggle("busy", activity?.kind === "busy");
  renderArt($(".art", tile), product);
  $(".label", tile).textContent = product.name;
  tile.title = product.name;
  const status = $(".tile-status", tile);
  if (activity?.kind === "busy") status.textContent = activity.text;
  if (activity?.kind === "error") {
    status.textContent = `! ${activity.text}`;
    status.classList.add("error");
  }
  tile.addEventListener("click", () => openDetail(product.id));
  return tile;
}

function renderDetail(product) {
  const body = $("#detail-tpl").content.firstElementChild.cloneNode(true);
  const activity = state.activity.get(product.id);
  const view = describe(product);
  body.classList.toggle("busy", activity?.kind === "busy");

  renderArt($(".art", body), product);
  $(".name", body).textContent = product.name;
  renderMeta($(".meta", body), product, view, activity);
  $(".price", body).textContent = view.best ? money.format(view.best.price) : "";
  if (view.cue) {
    const cue = $(".cue", body);
    cue.dataset.kind = view.cue.kind;
    cue.append(el("span", "icon", view.cue.icon), `${view.cue.icon ? " " : ""}${view.cue.text}${view.cue.more}`);
    $(".icon", cue).setAttribute("aria-hidden", "true");
  }

  renderOffers(body, product);
  $(".history", body).append(sparkline(product.history || []));

  const targetForm = $(".target-form", body);
  const targetInput = $("input", targetForm);
  targetInput.value = view.target ?? "";
  $(".clear-target", body).hidden = view.target == null;
  targetForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = targetInput.value;
    update(product.id, { target_price: value === "" ? null : Number(value) }, "Saving…");
  });
  $(".clear-target", body).addEventListener("click", () => update(product.id, { target_price: null }, "Saving…"));

  const nameForm = $(".name-form", body);
  const nameInput = $("input", nameForm);
  nameInput.value = product.name;
  nameForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const name = nameInput.value.trim();
    if (name && name !== product.name) update(product.id, { name }, `Searching for “${name}”…`);
  });

  const notes = [];
  if (product.hidden?.used) notes.push(`${product.hidden.used} used`);
  if (product.hidden?.unrelated) notes.push(`${product.hidden.unrelated} unrelated`);
  const note = $(".note", body);
  if (notes.length) note.append(`Hidden: ${notes.join(", ")}.`);
  if (product.source_url) note.append(notes.length ? " " : "", link(product.source_url, "Original page"));

  $(".close", body).addEventListener("click", closeDetail);
  $(".refresh", body).addEventListener("click", () => refresh(product.id));
  $(".remove", body).addEventListener("click", () => remove(product.id));
  return body;
}

function tileFor(id) {
  return $(`#products .tile[data-id="${id}"]`);
}

function paintDetail(product) {
  const host = $("#detail");
  const active = host.contains(document.activeElement) ? document.activeElement : null;
  const draft = active?.tagName === "INPUT" ? active.value : null;
  host.replaceChildren(renderDetail(product));
  const again = active?.dataset.key && $(`[data-key="${active.dataset.key}"]`, host);
  if (!again) return;
  if (draft != null) again.value = draft;
  again.focus();
}

function paint(id) {
  const product = state.products.find((p) => p.id === id);
  const old = tileFor(id);
  if (!product) {
    old?.remove();
    if (state.openId === id) closeDetail();
    return;
  }
  const next = renderTile(product);
  const hadFocus = old != null && old === document.activeElement;
  if (old) old.replaceWith(next);
  else $("#products").prepend(next);
  if (hadFocus) next.focus();
  if (state.openId === id) paintDetail(product);
}

function openDetail(id) {
  const product = state.products.find((p) => p.id === id);
  if (!product || state.activity.get(id)?.kind === "removing") return;
  state.openId = id;
  paintDetail(product);
  const dialog = $("#detail-dialog");
  if (!dialog.open) dialog.showModal();
  history.replaceState(null, "", `#open=${id}`);
}

function closeDetail() {
  const dialog = $("#detail-dialog");
  if (dialog.open) dialog.close();
  detailClosed();
}

function detailClosed() {
  const id = state.openId;
  if (id == null) return;
  state.openId = null;
  $("#detail").replaceChildren();
  if (location.hash.startsWith("#open=")) history.replaceState(null, "", location.pathname);
  tileFor(id)?.focus();
}

function syncList() {
  const visible = state.products.filter((p) => state.activity.get(p.id)?.kind !== "removing").length;
  const adding = $("#products .placeholder") != null;
  $("#empty").hidden = visible > 0 || adding;
  $("#list-head").hidden = visible === 0;
  $("#count").textContent = `${visible} tracked`;
}

function setActivity(id, activity) {
  if (activity) state.activity.set(id, activity);
  else state.activity.delete(id);
  paint(id);
  syncList();
  if (activity?.kind === "error") {
    setTimeout(() => state.activity.get(id) === activity && setActivity(id, null), ERROR_MS);
  }
}

function upsert(product) {
  const i = state.products.findIndex((p) => p.id === product.id);
  if (i === -1) state.products.unshift(product);
  else state.products[i] = product;
}

async function mutate(id, request, busyText) {
  if (state.activity.has(id) && state.activity.get(id).kind !== "error") return;
  setActivity(id, { kind: "busy", text: busyText });
  try {
    upsert(await request());
    setActivity(id, null);
  } catch (err) {
    setActivity(id, { kind: "error", text: err.message });
  }
}

const refresh = (id) => mutate(id, () => api(`/api/products/${id}/refresh`, { method: "POST" }), "Checking prices…");
const update = (id, changes, busyText) =>
  mutate(id, () => api(`/api/products/${id}`, { method: "PATCH", body: JSON.stringify(changes) }), busyText);

async function refreshAll() {
  const button = $("#refresh-all");
  const ids = state.products.map((p) => p.id);
  button.disabled = true;
  for (const [i, id] of ids.entries()) {
    button.textContent = `Refreshing ${i + 1} of ${ids.length}…`;
    await refresh(id);
  }
  button.disabled = false;
  button.textContent = "Refresh all";
}

function showToast(text, onUndo) {
  const toast = $("#toast");
  $(".toast-text", toast).textContent = text;
  $(".toast-undo", toast).onclick = onUndo;
  toast.hidden = false;
  $(".toast-undo", toast).focus();
}

function commitRemoval(keepalive = false) {
  const removal = state.removal;
  if (!removal) return;
  state.removal = null;
  clearTimeout(removal.timer);
  $("#toast").hidden = true;
  api(`/api/products/${removal.id}`, { method: "DELETE", keepalive })
    .then(() => {
      state.products = state.products.filter((p) => p.id !== removal.id);
      state.activity.delete(removal.id);
      paint(removal.id);
    })
    .catch((err) => setActivity(removal.id, { kind: "error", text: `Couldn't remove: ${err.message}` }));
}

function remove(id) {
  commitRemoval();
  const product = state.products.find((p) => p.id === id);
  if (state.openId === id) closeDetail();
  setActivity(id, { kind: "removing" });
  state.removal = { id, timer: setTimeout(commitRemoval, UNDO_MS) };
  showToast(`Removed “${product.name}”.`, () => {
    clearTimeout(state.removal.timer);
    state.removal = null;
    $("#toast").hidden = true;
    setActivity(id, null);
    tileFor(id)?.focus();
  });
}

function setFormStatus(text, isError = false) {
  const status = $("#form-status");
  status.textContent = text;
  status.classList.toggle("error", isError);
}

async function add(query) {
  const placeholder = el("div", "tile placeholder");
  placeholder.innerHTML = `<span class="art"></span><span class="label"></span><span class="tile-status">Searching stores…</span>`;
  $(".label", placeholder).textContent = query;
  $("#products").prepend(placeholder);
  syncList();
  setFormStatus("");
  try {
    const product = await api("/api/products", { method: "POST", body: JSON.stringify({ query }) });
    const existed = state.products.some((p) => p.id === product.id);
    upsert(product);
    placeholder.remove();
    paint(product.id);
    openDetail(product.id);
    if (existed) setFormStatus(`Already tracking “${product.name}”. Prices updated.`);
  } catch (err) {
    placeholder.remove();
    $("#query").value ||= query;
    setFormStatus(err.message, true);
  }
  syncList();
}

function openFromHash() {
  const id = new URLSearchParams(location.hash.slice(1)).get("open");
  if (id && id !== state.openId) openDetail(id);
}

async function load() {
  const [status, products] = await Promise.all([api("/api/status"), api("/api/products")]);
  $("#demo-banner").hidden = !status.demo;
  state.products = products;
  $("#products").replaceChildren(...state.products.map(renderTile));
  openFromHash();
  syncList();
}

$("#add-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("#query");
  const query = input.value.trim();
  if (!query) return;
  input.value = "";
  add(query);
});
$("#query").addEventListener("input", () => setFormStatus(""));
$("#refresh-all").addEventListener("click", refreshAll);
document.querySelectorAll(".examples button").forEach((button) =>
  button.addEventListener("click", () => add(button.dataset.query)),
);

const dialog = $("#detail-dialog");
// Escape closes the dialog natively. The close event is queued, so it must not undo a dialog reopened since.
dialog.addEventListener("close", () => !dialog.open && detailClosed());
// A click on the dialog element itself (not its content) is a click on the backdrop.
dialog.addEventListener("click", (event) => event.target === dialog && closeDetail());
addEventListener("hashchange", openFromHash);
addEventListener("pagehide", () => commitRemoval(true));
setInterval(() => {
  document.querySelectorAll("time[data-ago]").forEach((t) => (t.textContent = `checked ${ago(t.dataset.ago)}`));
}, 60000);

load().catch((err) => setFormStatus(err.message, true));
