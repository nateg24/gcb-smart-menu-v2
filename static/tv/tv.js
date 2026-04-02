// Scale the viewport so the full 1280px-wide menu design fits any screen width.
// This runs immediately (IIFE) before any rendering happens.
(function () {
    const DESIGN_WIDTH = 1280;
    const scale = Math.min(1, screen.width / DESIGN_WIDTH); // never zoom in, only zoom out
    document.querySelector('meta[name="viewport"]').content =
        `width=${DESIGN_WIDTH}, initial-scale=${scale.toFixed(3)}, minimum-scale=0.1, maximum-scale=3`;
})();

// DOM element references used throughout this file
const meta = document.getElementById("meta");           // bottom status bar (version, timestamp)
const houseGrid = document.getElementById("houseGrid"); // grid of house (CORE) tap cards
const guestGrid = document.getElementById("guestGrid"); // grid of guest/cider tap cards
const flightsRow = document.getElementById("flightsRow"); // static beer flights promo row

// ── QR Code ───────────────────────────────────────────────
// Generates a QR code pointing to the mobile menu URL.
// The IP is hardcoded to the local network address of the server.
const MENU_URL = "http://192.168.40.22:8000/menu";

new QRCode(document.getElementById("qrCode"), {
    text: MENU_URL,
    width: 110,
    height: 110,
    colorDark: "#0a0a0a",
    colorLight: "#f2f2f2",
    correctLevel: QRCode.CorrectLevel.M // medium error correction — balances size and redundancy
});

let ws = null;
let lastVersion = null; // tracks the last rendered menu version to skip unnecessary re-renders

// Escape special HTML characters to prevent XSS when injecting user data into innerHTML
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;"
  }[c]));
}

// Build the description line shown under a beer's name.
// Prefers the description field; falls back to style. Appends ABV if present.
function beerDesc(beer) {
  const bits = [];
  if (beer.description) bits.push(beer.description);
  else if (beer.style) bits.push(beer.style);
  if (beer.abv != null) bits.push(`${beer.abv}%`);
  return bits.join(" - ");
}

// Render an array of tap objects into HTML card strings.
// Empty taps (no beer assigned) get a placeholder div to maintain grid spacing.
function renderItems(items) {
  return items.map(t => {
    if (!t.beer) return `<div class="item item--empty"></div>`;
    return `
    <div class="item">
      <div class="itemTop">
        <div class="name">${escapeHtml(t.beer.name)}</div>
        <div class="price">${escapeHtml((t.beer.price || "").replace("$", ""))}</div>
      </div>
      <div class="desc">${escapeHtml(beerDesc(t.beer))}</div>
    </div>
  `;
  }).join("");
}

// Re-render the entire menu display with fresh data from the API.
function renderMenu(data) {
  // Update the status bar with the menu version and fetch time
  if (meta) meta.textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString()} • v${data.version}`;

  const allTaps = data.taps || [];

  // Classify taps as guest if their beer is GUEST or CIDER; everything else is house
  const isGuest = (t) => ["GUEST", "CIDER"].includes(String(t.beer?.category || "").toUpperCase());

  const house = allTaps.filter(t => !isGuest(t));
  const guest = allTaps.filter(t => isGuest(t));

  if (houseGrid) houseGrid.innerHTML = renderItems(house);

  // Beer flights row is always shown with a fixed $13 price
  if (flightsRow) {
    flightsRow.innerHTML = `
      <div class="left">
        <div class="label">BEER FLIGHTS</div>
        <div class="desc">SELECT 4 FROM THE TAP BEER LIST</div>
      </div>
      <div class="price">13</div>
    `;
  }

  // Only show the guest section and its divider if at least one guest tap has a beer
  const hasGuest = guest.some(t => t.beer);
  if (guestGrid) {
    guestGrid.innerHTML = renderItems(guest);
    guestGrid.style.display = hasGuest ? "" : "none";
  }

  const divider = document.querySelector(".divider");
  if (divider) divider.style.display = hasGuest ? "" : "none";
}

// Fetch the latest menu from the API and re-render if the version changed.
// Uses cache: "no-store" to always get fresh data even on repeat requests.
async function loadMenu() {
  const res = await fetch("/api/menu", { cache: "no-store" });
  if (!res.ok) throw new Error(`GET /api/menu failed: ${res.status}`);
  const data = await res.json();

  // Skip re-render if the server version matches what we already have displayed
  if (lastVersion === data.version) return;
  lastVersion = data.version;

  renderMenu(data);
}

// Open a WebSocket connection to the server's /ws/menu endpoint.
// When the server broadcasts "menu_updated", we fetch and re-render.
// On disconnect, waits 1.5s then reconnects automatically.
function connectWS() {
  // Build the WebSocket URL from the current page's host (works on any port/domain)
  const wsUrl = new URL("/ws/menu", window.location.href);
  wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";

  ws = new WebSocket(wsUrl.toString());

  ws.onopen = () => { if (meta) meta.textContent = "Connected"; };

  ws.onmessage = async (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "menu_updated") await loadMenu();
    } catch {}
  };

  ws.onerror = (e) => console.log("WS error", e);

  ws.onclose = () => {
    if (meta) meta.textContent = "Disconnected • retrying…";
    setTimeout(connectWS, 1500); // retry after 1.5 seconds
  };
}

// Boot sequence: load menu immediately, then open WebSocket for real-time updates.
// Also starts a 15-second polling interval as a fallback (e.g. if WS stays broken).
(async () => {
  try { await loadMenu(); }
  catch (e) { if (meta) meta.textContent = `Error: ${e.message}`; }

  connectWS();
  setInterval(() => loadMenu().catch(() => {}), 15000); // poll every 15s as safety net
})();
