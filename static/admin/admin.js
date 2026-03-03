const meta = document.getElementById("meta");
const tapsEl = document.getElementById("taps");
const guestTapsEl = document.getElementById("guestTaps");
const beersEl = document.getElementById("beers");
const beerFormEl = document.getElementById("beerForm");
const newBeerBtn = document.getElementById("newBeerBtn");

let beers = [];
let menu = null;

function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;"
    }[c]));
}

async function api(path, opts = {}) {
    const res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
        ...opts
    });
    if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`${opts.method || "GET"} ${path} failed: ${res.status} ${txt}`);
    }
    // Return json if possible, otherwise text
    const ct = res.headers.get("content-type") || "";
    return ct.includes("application/json") ? res.json() : res.text();
}

async function loadAll() {
    meta.textContent = "Loading…";
    [beers, menu] = await Promise.all([api("/api/beers"), api("/api/menu")]);
    meta.textContent = `Loaded • ${new Date(menu.generated_at).toLocaleTimeString()}`;
    renderTaps();
    renderBeers();
}

function beerLabel(b) {
    const bits = [b.brewery, b.name].filter(Boolean).join(" — ");
    return bits || `Beer #${b.id}`;
}


function renderTaps() {
    const renderTapRow = (t) => {
        const currentBeerId = (t.beer_id ?? (t.beer ? t.beer.id : ""));
        return `
      <div class="row">
        <div>
          <div class="tapNum">Tap ${t.tap_number}</div>
          <div class="small">${escapeHtml(t.beer ? beerLabel(t.beer) : "No beer assigned")}</div>
        </div>

        <div>
          <select class="select" data-tap-assign="${t.id}">
            <option value="">-- Empty / Unassigned --</option>
            ${beers.map(b => `<option value="${b.id}" ${String(b.id) === String(currentBeerId) ? "selected" : ""}>${escapeHtml(beerLabel(b))}</option>`).join("")}
          </select>
          <div class="small">Assign beer to this tap</div>
        </div>

        <div class="controls">
          <button class="btn on" data-tap-status="${t.id}" data-status="ON">ON</button>
          <button class="btn out" data-tap-status="${t.id}" data-status="OUT">OUT</button>
          <button class="btn" data-tap-status="${t.id}" data-status="COMING_SOON">COMING SOON</button>
        </div>
      </div>
    `;
    };

    // If menu endpoint doesn't include beer.category (or beer is inactive), guest split may be empty.
    const isGuestTap = (t) => {
        const cat = String(t?.beer?.category || "").toUpperCase();
        return cat === "GUEST" || cat === "CIDER";
    };

    const houseTaps = menu.taps.filter(t => !isGuestTap(t));
    const guestTaps = menu.taps.filter(t => isGuestTap(t));

    tapsEl.innerHTML = houseTaps.map(renderTapRow).join("");
    if (guestTapsEl) guestTapsEl.innerHTML = guestTaps.map(renderTapRow).join("");

    // Assign listeners (attach to both house + guest containers)
    document.querySelectorAll("[data-tap-assign]").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            const tapId = e.target.getAttribute("data-tap-assign");
            const beerId = e.target.value ? Number(e.target.value) : null;
            await api(`/api/taps/${tapId}/assign`, { method: "POST", body: JSON.stringify({ beer_id: beerId }) });
            menu = await api("/api/menu");
            renderTaps();
        });
    });

    // Status listeners
    document.querySelectorAll("[data-tap-status]").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            const tapId = e.target.getAttribute("data-tap-status");
            const status = e.target.getAttribute("data-status");
            await api(`/api/taps/${tapId}/status`, { method: "POST", body: JSON.stringify({ status }) });
            menu = await api("/api/menu");
            renderTaps();
        });
    });
}


// Inside static/admin/admin.js

// Replace the renderBeers function in static/admin/admin.js
// static/admin/admin.js

// 1. Update the render loop to use the word "Delete"
function renderBeers() {
    beersEl.innerHTML = beers.map(b => `
    <div class="row" style="grid-template-columns: 1fr 220px;">
      <div>
        <div class="tapNum">${escapeHtml(beerLabel(b))}</div>
        <div class="small">${escapeHtml([b.style, b.abv ? `${Number(b.abv).toFixed(1)}%` : null, b.price].filter(Boolean).join(" • "))}</div>
      </div>
      <div class="controls">
        <button class="btn primary" data-edit="${b.id}">Edit</button>
        <button class="btn danger" data-del="${b.id}">Delete</button>
      </div>
    </div>
  `).join("");
}

// 2. Add this ONE listener OUTSIDE of any other functions
document.getElementById("beers").addEventListener("click", async (e) => {
    // Check if the clicked element (or its parent) is the delete button
    const deleteBtn = e.target.closest("[data-del]");

    if (deleteBtn) {
        const id = deleteBtn.getAttribute("data-del");

        // This confirmation MUST appear now
        if (!confirm("Permanently delete this beer from the database?")) return;

        try {
            console.log("Sending DELETE for ID:", id);
            const res = await api(`/api/beers/${id}`, { method: "DELETE" });

            if (res.ok || res) {
                console.log("Delete successful. Refreshing UI...");
                await loadAll(); // Re-fetches the list from the server
            }
        } catch (err) {
            console.error("Delete failed:", err);
            alert("Error: " + err.message);
        }
    }
});

function openBeerForm(beerId = null) {
    const b = beerId ? beers.find(x => x.id === beerId) : null;
    const cat = String(b?.category || "CORE").toUpperCase();

    beerFormEl.classList.remove("hidden");
    beerFormEl.innerHTML = `
    <div class="tapNum">${beerId ? "Edit Beer" : "New Beer"}</div>
    <div class="formGrid" style="margin-top:10px;">
      <input class="input" id="f_name" placeholder="Name" value="${escapeHtml(b?.name || "")}" />
      <input class="input" id="f_brewery" placeholder="Brewery" value="${escapeHtml(b?.brewery || "")}" />
      <input class="input" id="f_style" placeholder="Style" value="${escapeHtml(b?.style || "")}" />
      <input class="input" id="f_abv" placeholder="ABV (e.g. 6.5)" value="${escapeHtml(b?.abv ?? "")}" />
      <input class="input" id="f_price" placeholder="Price (e.g. $7)" value="${escapeHtml(b?.price || "")}" />
      <select class="select" id="f_category">
      <option value="CORE" ${cat === "CORE" ? "selected" : ""}>House</option>
      <option value="GUEST" ${cat === "GUEST" ? "selected" : ""}>Guest</option>
      <option value="CIDER" ${cat === "CIDER" ? "selected" : ""}>Cider</option>
      </select>
<div class="small">Category</div>
      <div></div>
    </div>
    <textarea class="textarea" id="f_desc" placeholder="Description (optional)">${escapeHtml(b?.description || "")}</textarea>

    <div class="formActions">
      <button class="btn" id="cancelForm">Cancel</button>
      <button class="btn primary" id="saveForm">Save</button>
    </div>
  `;

    document.getElementById("cancelForm").onclick = () => {
        beerFormEl.classList.add("hidden");
        beerFormEl.innerHTML = "";
    };

    document.getElementById("saveForm").onclick = async () => {
        const payload = {
            name: document.getElementById("f_name").value.trim(),
            brewery: document.getElementById("f_brewery").value.trim() || null,
            style: document.getElementById("f_style").value.trim() || null,
            abv: parseFloat(document.getElementById("f_abv").value) || null,
            price: document.getElementById("f_price").value.trim() || null,
            description: document.getElementById("f_desc").value.trim() || null,
            category: document.getElementById("f_category").value,
            is_active: true,
        };

        if (!payload.name) { alert("Name is required"); return; }

        if (beerId) {
            await api(`/api/beers/${beerId}`, { method: "PUT", body: JSON.stringify(payload) });
        } else {
            await api(`/api/beers`, { method: "POST", body: JSON.stringify(payload) });
        }

        await loadAll();
        beerFormEl.classList.add("hidden");
        beerFormEl.innerHTML = "";
    };
}

newBeerBtn.addEventListener("click", () => openBeerForm(null));

// Boot
loadAll().catch(e => {
    meta.textContent = `Error: ${e.message}`;
});