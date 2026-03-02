const meta = document.getElementById("meta");
const houseGrid = document.getElementById("houseGrid");
const guestGrid = document.getElementById("guestGrid");
const flightsRow = document.getElementById("flightsRow");

let ws = null;
let lastVersion = null;

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;"
  }[c]));
}

function beerDesc(beer) {
  const bits = [];
  if (beer.description) bits.push(beer.description);
  else if (beer.style) bits.push(beer.style);
  if (beer.abv != null) bits.push(`${beer.abv}%`);
  return bits.join(" - ");
}

function renderItems(items) {
  return items.map(t => {
    const beer = t.beer;
    return `
      <div class="item">
        <div class="itemTop">
          <div class="name">${escapeHtml(beer.name)}</div>
          <div class="price">${escapeHtml((beer.price || "").replace("$", ""))}</div>
        </div>
        <div class="desc">${escapeHtml(beerDesc(beer))}</div>
      </div>
    `;
  }).join("");
}

function split2(arr) {
  const mid = Math.ceil(arr.length / 2);
  return [arr.slice(0, mid), arr.slice(mid)];
}

function renderMenu(data) {
  if (meta) meta.textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString()} • v${data.version}`;

  const tapsWithBeer = (data.taps || []).filter(t => t.beer);

  const cat = (t) => String(t.beer?.category || "CORE").toUpperCase();
  const isGuest = (t) => ["GUEST", "CIDER"].includes(cat(t));

  const house = tapsWithBeer.filter(t => !isGuest(t));
  const guest = tapsWithBeer.filter(t => isGuest(t));

  const [houseL, houseR] = split2(house);
  const [guestL, guestR] = split2(guest);

  // HOUSE - Always renders
  if (houseGrid) {
    houseGrid.innerHTML = `
      <div class="col"><div class="section">${renderItems(houseL)}</div></div>
      <div class="col"><div class="section">${renderItems(houseR)}</div></div>
    `;
  }
  if (flightsRow) {
    flightsRow.style.display = ""; // Ensure it's visible
    flightsRow.innerHTML = `
      <div class="left">
        <div class="label">BEER FLIGHTS</div>
        <div class="desc">SELECT 4 FROM THE TAP BEER LIST</div>
      </div>
      <div class="price">13</div>
    `;
  }
  
  // GUEST - Only hides the guest list itself, not the flight row
  if (guestGrid) {
    guestGrid.innerHTML = `
      <div class="col"><div class="section">${renderItems(guestL)}</div></div>
      <div class="col"><div class="section">${renderItems(guestR)}</div></div>
    `;
    // Only toggle the visibility of the grid and the divider, leaving Flights alone
    guestGrid.style.display = guest.length ? "" : "none";
  }

  const divider = document.querySelector(".divider");
  if (divider) {
    divider.style.display = guest.length ? "" : "none";
  }
}

async function loadMenu() {
  const res = await fetch("/api/menu", { cache: "no-store" });
  if (!res.ok) throw new Error(`GET /api/menu failed: ${res.status}`);
  const data = await res.json();

  // skip re-render if version didn't change
  if (lastVersion === data.version) return;
  lastVersion = data.version;

  renderMenu(data);
}

function connectWS() {
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
    setTimeout(connectWS, 1500);
  };
}

(async () => {
  try { await loadMenu(); }
  catch (e) { if (meta) meta.textContent = `Error: ${e.message}`; }

  connectWS();
  setInterval(() => loadMenu().catch(() => {}), 15000);
})();