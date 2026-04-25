// DOM element references
const meta = document.getElementById("meta");                   // status bar at the top
const houseTapGridEl = document.getElementById("houseTapGrid"); // grid of house tap cells
const guestTapGridEl = document.getElementById("guestTapGrid"); // grid of guest tap cells
const beersEl = document.getElementById("beers");               // list of beer rows
const beerFormEl = document.getElementById("beerForm");         // create/edit beer form container
const newBeerBtn = document.getElementById("newBeerBtn");       // "New Beer" button
const pinOverlay = document.getElementById("pinOverlay");       // full-screen PIN lock overlay
const pinInput = document.getElementById("pinInput");           // PIN input field
const pinError = document.getElementById("pinError");           // error message shown on wrong PIN
const pinSubmit = document.getElementById("pinSubmit");         // "Unlock" button
const slidesListEl = document.getElementById("slidesList");     // list of slide rows
const slideFormEl = document.getElementById("slideForm");       // create/edit slide form container
const newSlideBtn = document.getElementById("newSlideBtn");     // "New Slide" button

// ── Auth helpers ──────────────────────────────────────────
// Token is stored in sessionStorage so it's cleared when the tab is closed
function getToken() { return sessionStorage.getItem("adminToken"); }
function setToken(t) { sessionStorage.setItem("adminToken", t); }

// ── Inactivity timeout (3 minutes) ───────────────────────
// Automatically shows the PIN lock if the admin hasn't interacted for 3 minutes
const TIMEOUT_MS = 3 * 60 * 1000;
let inactivityTimer = null;

function resetInactivityTimer() {
    clearTimeout(inactivityTimer);
    inactivityTimer = setTimeout(() => {
        sessionStorage.removeItem("adminToken");
        showPin(); // re-show lock screen after timeout
    }, TIMEOUT_MS);
}

// Any user interaction resets the inactivity countdown
["click", "keydown", "mousemove", "touchstart"].forEach(evt =>
    document.addEventListener(evt, resetInactivityTimer, { passive: true })
);

function showPin() {
    pinOverlay.classList.remove("hidden");
    pinInput.value = "";
    pinError.classList.add("hidden");
    pinInput.focus();
}

function hidePin() {
    pinOverlay.classList.add("hidden");
}

// Submit the PIN to the server; on success, store the token and load the admin UI
async function submitPin() {
    const pin = pinInput.value.trim();
    if (pin.length !== 4) return; // require exactly 4 digits before submitting
    try {
        const res = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pin })
        });
        if (!res.ok) {
            pinError.classList.remove("hidden");
            pinInput.value = "";
            pinInput.focus();
            return;
        }
        const { token } = await res.json();
        setToken(token);
        hidePin();
        resetInactivityTimer();
        loadAll(); // load the admin data now that we're authenticated
    } catch {
        pinError.classList.remove("hidden");
    }
}

pinSubmit.addEventListener("click", submitPin);
pinInput.addEventListener("keydown", e => { if (e.key === "Enter") submitPin(); });

// Module-level state: current beer list, full menu, and slides
let beers = [];
let menu = null;
let slides = [];

// Escape special HTML characters to prevent XSS when injecting data into innerHTML
function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;"
    }[c]));
}

// Authenticated fetch wrapper. Automatically adds the Bearer token header,
// shows the PIN screen on 401 (expired session), and throws on other errors.
async function api(path, opts = {}) {
    const token = getToken();
    const res = await fetch(path, {
        headers: {
            "Content-Type": "application/json",
            ...(token ? { "Authorization": `Bearer ${token}` } : {}),
            ...(opts.headers || {})
        },
        ...opts
    });

    if (res.status === 401) {
        // Token expired or invalid — force re-authentication
        sessionStorage.removeItem("adminToken");
        showPin();
        throw new Error("Session expired — please re-enter PIN");
    }

    if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`${opts.method || "GET"} ${path} failed: ${res.status} ${txt}`);
    }

    // Return parsed JSON if the response is JSON, otherwise return raw text
    const ct = res.headers.get("content-type") || "";
    return ct.includes("application/json") ? res.json() : res.text();
}

// Format a beer as "Brewery — Name" for use in dropdowns and list labels
function beerLabel(b) {
    const bits = [b.brewery, b.name].filter(Boolean).join(" — ");
    return bits || `Beer #${b.id}`;
}

// Returns true if the tap's beer is a GUEST or CIDER (shown in the guest grid)
function isGuestTap(t) {
    return ["GUEST", "CIDER"].includes(String(t?.beer?.category || "").toUpperCase());
}

// Build the HTML for a single tap cell, including a dropdown to reassign the beer.
// assignedIds is the set of beer IDs already on other taps (shown as disabled/grayed in dropdowns).
function buildTapCells(taps, assignedIds) {
    return taps.map(t => {
        const currentBeerId = String(t.beer_id ?? (t.beer ? t.beer.id : ""));
        const abv = t.beer?.abv ? `${Number(t.beer.abv).toFixed(1)}%` : null;
        const tapMeta = [t.beer?.style, abv].filter(Boolean).join(" • ");

        return `
        <div class="tapCell" data-id="${t.id}">
            <div class="tapCellDrag dragHandle tapHandle" title="Drag to reorder">☰</div>
            <div class="tapCellName">${escapeHtml(t.beer ? t.beer.name : "Empty")}</div>
            ${t.beer?.brewery ? `<div class="tapCellBrewery">${escapeHtml(t.beer.brewery)}</div>` : ""}
            ${tapMeta ? `<div class="small">${escapeHtml(tapMeta)}</div>` : ""}
            <select class="select tapCellSelect" data-tap-assign="${t.id}">
                <option value="">-- Unassigned --</option>
                ${beers.map(b => {
                    const bid = String(b.id);
                    const selected = bid === currentBeerId;
                    const taken = !selected && assignedIds.has(bid); // disable beers already on another tap
                    return `<option value="${b.id}" ${selected ? "selected" : ""} ${taken ? "disabled" : ""}>
                        ${escapeHtml(beerLabel(b))}${taken ? " (on tap)" : ""}
                    </option>`;
                }).join("")}
            </select>
            ${t.beer ? `<button class="btn btnSmall danger tapRemoveBtn" data-tap-remove="${t.id}">Remove</button>` : ""}
        </div>`;
    }).join("");
}

// Wire up the change event on every beer-assignment dropdown in a given container.
// On change, POSTs the new assignment and then re-renders the tap grid.
function bindTapAssign(container) {
    container.querySelectorAll("[data-tap-assign]").forEach(sel => {
        sel.addEventListener("change", async (e) => {
            const tapId = e.target.getAttribute("data-tap-assign");
            const beerId = e.target.value ? Number(e.target.value) : null;
            try {
                await api(`/api/taps/${tapId}/assign`, {
                    method: "POST",
                    body: JSON.stringify({ beer_id: beerId })
                });
                menu = await api("/api/menu");
                renderTapGrid();
                initGridSorting(); // re-init Sortable after DOM is rebuilt
            } catch (err) {
                alert("Error: " + err.message);
            }
        });
    });

    container.querySelectorAll("[data-tap-remove]").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            const tapId = e.target.getAttribute("data-tap-remove");
            try {
                await api(`/api/taps/${tapId}/assign`, {
                    method: "POST",
                    body: JSON.stringify({ beer_id: null })
                });
                menu = await api("/api/menu");
                renderTapGrid();
                initGridSorting();
            } catch (err) {
                alert("Error: " + err.message);
            }
        });
    });
}

// Rebuild both tap grids (house and guest) from the current menu state.
function renderTapGrid() {
    if (!menu || !Array.isArray(menu.taps)) return;

    // Collect all beer IDs currently assigned to any tap
    const assignedIds = new Set(
        menu.taps
            .map(t => t.beer_id ?? (t.beer ? t.beer.id : null))
            .filter(Boolean)
            .map(String)
    );

    const house = menu.taps.filter(t => !isGuestTap(t));
    const guest = menu.taps.filter(t => isGuestTap(t));

    houseTapGridEl.innerHTML = buildTapCells(house, assignedIds);
    guestTapGridEl.innerHTML = buildTapCells(guest, assignedIds);

    // Bind assignment dropdowns after injecting new HTML
    bindTapAssign(houseTapGridEl);
    bindTapAssign(guestTapGridEl);
}

// Render the beer list with drag handles, info, and Edit/Delete buttons
function renderBeers() {
    beersEl.innerHTML = beers.map(b => {
        const isHouse = b.brewery?.toLowerCase().includes("gnarly cedar");
        const isGuest = ["GUEST", "CIDER"].includes((b.category || "").toUpperCase());
        const rowMod = isHouse ? " beerRow--house" : isGuest ? " beerRow--guest" : "";
        return `
    <div class="beerRow${rowMod}" data-id="${b.id}">
      <div class="dragHandle" title="Drag to reorder">☰</div>

      <div class="beerMain">
        <div class="beerName">${escapeHtml(beerLabel(b))}</div>
        <div class="beerMeta">
          ${escapeHtml([b.style, b.abv ? `${Number(b.abv).toFixed(1)}%` : null, b.price]
        .filter(Boolean)
        .join(" • "))}
        </div>
      </div>

      <div class="beerActions">
        <button type="button" class="btnSmall" data-edit="${b.id}">Edit</button>
        <button type="button" class="btnSmall danger" data-del="${b.id}">Delete</button>
      </div>
    </div>
  `;
    }).join("");
}

// Use event delegation on the beer list so we don't rebind listeners after every re-render.
// Handles both Delete (soft-delete via API) and Edit (opens form).
beersEl.addEventListener("click", async (e) => {
    const deleteBtn = e.target.closest("[data-del]");
    if (deleteBtn) {
        const id = deleteBtn.getAttribute("data-del");
        if (!confirm("Delete this beer permanently? This cannot be undone.")) return;

        try {
            await api(`/api/beers/${id}`, { method: "DELETE" });
            await loadAll();
        } catch (err) {
            alert("Error: " + err.message);
        }
        return;
    }

    const editBtn = e.target.closest("[data-edit]");
    if (editBtn) {
        openBeerForm(Number(editBtn.getAttribute("data-edit")));
    }
});

// Show the create/edit beer form, pre-populated with the existing beer if editing.
// Dynamically builds the form HTML and wires up Save/Cancel handlers.
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
    <textarea class="textarea" id="f_notes" placeholder="Staff notes (internal only — never shown on TV or menu)" style="margin-top:8px;">${escapeHtml(b?.notes || "")}</textarea>
    <div style="display:flex;gap:20px;margin-top:12px;font-size:13px;color:var(--muted);">
      <label style="display:flex;align-items:center;gap:6px;">
        <input type="checkbox" id="f_is_new" ${b?.is_new ? "checked" : ""} /> New
      </label>
      <label style="display:flex;align-items:center;gap:6px;">
        <input type="checkbox" id="f_is_featured" ${b?.is_featured ? "checked" : ""} /> Featured
      </label>
    </div>

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
            notes: document.getElementById("f_notes").value.trim() || null,
            category: document.getElementById("f_category").value,
            is_new: document.getElementById("f_is_new").checked,
            is_featured: document.getElementById("f_is_featured").checked,
            is_active: true,
        };

        if (!payload.name) { alert("Name is required"); return; }

        // PUT to update an existing beer, POST to create a new one
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

document.getElementById("clearTapsBtn").addEventListener("click", async () => {
    if (!confirm("Remove all beers from all taps? This cannot be undone.")) return;
    try {
        await api("/api/taps/clear", { method: "POST" });
        await loadAll();
    } catch (err) {
        alert("Error: " + err.message);
    }
});

document.getElementById("exportPdfBtn").addEventListener("click", () => {
    window.open("/menu?print=1", "_blank");
});

// Sortable.js instances — stored so they can be destroyed and recreated after DOM rebuilds
let beerSortable = null;
let houseGridSortable = null;
let guestGridSortable = null;

// Initialize drag-to-reorder on both tap grids.
// After a drag ends, sends the new order to the API and re-renders.
// Must be called again after the DOM is rebuilt (after renderTapGrid).
function initGridSorting() {
    const onReorder = async () => {
        // Collect all tap IDs in visual order, house grid first, then guest grid
        const allIds = [
            ...houseTapGridEl.querySelectorAll(".tapCell"),
            ...guestTapGridEl.querySelectorAll(".tapCell")
        ].map(el => Number(el.dataset.id));

        await api("/api/taps/reorder", {
            method: "POST",
            body: JSON.stringify({ order: allIds })
        });
        menu = await api("/api/menu");
        renderTapGrid();
        initGridSorting(); // re-initialize after DOM is rebuilt
    };

    // Destroy existing instances before creating new ones to prevent duplicate listeners
    if (houseGridSortable) houseGridSortable.destroy();
    if (guestGridSortable) guestGridSortable.destroy();

    const opts = { animation: 150, handle: ".tapHandle", swap: true, swapClass: "tapCellSwap", touchStartThreshold: 5, onEnd: onReorder };
    houseGridSortable = new Sortable(houseTapGridEl, opts);
    guestGridSortable = new Sortable(guestTapGridEl, opts);
}

// Initialize drag-to-reorder on the beer list.
// Only initialized once (guard check at top); doesn't need rebuilding since
// beersEl itself isn't replaced, only its innerHTML.
function initBeerSorting() {
    if (beerSortable) return; // already initialized

    beerSortable = new Sortable(beersEl, {
        animation: 150,
        handle: ".dragHandle",
        onEnd: async () => {
            // Read the new order from the DOM after the drag completes
            const ids = [...beersEl.querySelectorAll(".beerRow")].map(el => Number(el.dataset.id));
            await api("/api/beers/reorder", {
                method: "POST",
                body: JSON.stringify({ order: ids })
            });
            await loadAll(); // refresh everything to reflect saved order
        }
    });
}

// ── Slides ────────────────────────────────────────────────

function renderSlides() {
    if (!slides.length) {
        slidesListEl.innerHTML = `<div class="small" style="padding:10px;">No slides yet — click "+ New Slide" to add one.</div>`;
        return;
    }
    slidesListEl.innerHTML = slides.map(s => `
        <div class="slideRow${s.is_active ? "" : " slideRow--inactive"}" data-id="${s.id}">
            <div class="dragHandle" title="Drag to reorder">☰</div>
            <div class="slideMain">
                <div class="slideName">${escapeHtml(s.title || "(No title)")}</div>
                <div class="slideMeta">${s.duration}s display${s.is_active ? "" : " • inactive"}</div>
            </div>
            <div class="slideActions">
                <button type="button" class="btnSmall" data-slide-edit="${s.id}">Edit</button>
                <button type="button" class="btnSmall danger" data-slide-del="${s.id}">Delete</button>
            </div>
        </div>
    `).join("");
}

function openSlideForm(slideId = null) {
    const s = slideId ? slides.find(x => x.id === slideId) : null;

    slideFormEl.classList.remove("hidden");
    slideFormEl.innerHTML = `
        <div class="tapNum" style="margin-bottom:10px;">${slideId ? "Edit Slide" : "New Slide"}</div>
        <div class="formGrid" style="margin-top:10px;">
            <input class="input" id="sf_title" placeholder="Title (e.g. Upcoming Events)" value="${escapeHtml(s?.title || "")}" />
            <input class="input" id="sf_duration" type="number" min="5" max="300" placeholder="Display seconds" value="${s?.duration ?? 30}" />
        </div>
        <textarea class="textarea" id="sf_body" placeholder="Body text" style="margin-top:12px;">${escapeHtml(s?.body || "")}</textarea>
        <div style="display:flex;gap:8px;margin-top:12px;align-items:center;">
            <input class="input" id="sf_image" placeholder="Image URL (optional)" value="${escapeHtml(s?.image_url || "")}" style="flex:1;" />
            <label class="btn" style="cursor:pointer;white-space:nowrap;margin:0;">
                Upload Image
                <input type="file" id="sf_upload" accept="image/*" style="display:none;" />
            </label>
        </div>
        <label style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:13px;color:var(--muted);">
            <input type="checkbox" id="sf_active" ${s ? (s.is_active ? "checked" : "") : "checked"} />
            Active (show on TV)
        </label>
        <div class="formActions" style="margin-top:32px;">
            <button class="btn" id="cancelSlideForm">Cancel</button>
            <button class="btn primary" id="saveSlideForm">Save</button>
        </div>
    `;

    document.getElementById("cancelSlideForm").onclick = () => {
        slideFormEl.classList.add("hidden");
        slideFormEl.innerHTML = "";
    };

    document.getElementById("sf_upload").addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append("file", file);
        try {
            const res = await fetch("/api/uploads", {
                method: "POST",
                headers: { "Authorization": `Bearer ${getToken()}` },
                body: formData
            });
            if (!res.ok) throw new Error("Upload failed");
            const { url } = await res.json();
            document.getElementById("sf_image").value = url;
        } catch (err) {
            alert("Upload failed: " + err.message);
        }
    });

    document.getElementById("saveSlideForm").onclick = async () => {
        const payload = {
            title: document.getElementById("sf_title").value.trim() || null,
            body: document.getElementById("sf_body").value.trim() || null,
            image_url: document.getElementById("sf_image").value.trim() || null,
            duration: parseInt(document.getElementById("sf_duration").value) || 30,
            is_active: document.getElementById("sf_active").checked,
        };

        if (slideId) {
            await api(`/api/slides/${slideId}`, { method: "PUT", body: JSON.stringify(payload) });
        } else {
            await api("/api/slides", { method: "POST", body: JSON.stringify(payload) });
        }

        await loadAll();
        slideFormEl.classList.add("hidden");
        slideFormEl.innerHTML = "";
    };
}

slidesListEl.addEventListener("click", async (e) => {
    const delBtn = e.target.closest("[data-slide-del]");
    if (delBtn) {
        if (!confirm("Delete this slide? This cannot be undone.")) return;
        try {
            await api(`/api/slides/${delBtn.getAttribute("data-slide-del")}`, { method: "DELETE" });
            await loadAll();
        } catch (err) { alert("Error: " + err.message); }
        return;
    }
    const editBtn = e.target.closest("[data-slide-edit]");
    if (editBtn) openSlideForm(Number(editBtn.getAttribute("data-slide-edit")));
});

newSlideBtn.addEventListener("click", () => openSlideForm(null));

let slideSortable = null;

function initSlideSorting() {
    if (slideSortable) slideSortable.destroy();
    slideSortable = new Sortable(slidesListEl, {
        animation: 150,
        handle: ".dragHandle",
        onEnd: async () => {
            const ids = [...slidesListEl.querySelectorAll(".slideRow")].map(el => Number(el.dataset.id));
            await api("/api/slides/reorder", { method: "POST", body: JSON.stringify({ order: ids }) });
            await loadAll();
        }
    });
}

// Fetch both the beer list and the full menu in parallel, then render everything.
async function loadAll() {
    meta.textContent = "Loading…";

    try {
        const [beersRes, menuRes, slidesRes] = await Promise.all([
            api("/api/beers"),
            api("/api/menu"),
            fetch("/api/slides").then(r => r.json())
        ]);

        beers = Array.isArray(beersRes) ? beersRes : [];
        menu = menuRes;
        slides = Array.isArray(slidesRes) ? slidesRes : [];

        if (!menu || !Array.isArray(menu.taps)) {
            console.error("Bad /api/menu response:", menuRes);
            throw new Error("Bad /api/menu response (expected {taps:[...]})");
        }

        meta.textContent = `Loaded • ${new Date(menu.generated_at).toLocaleTimeString()}`;
        renderTapGrid();
        renderBeers();
        renderSlides();
        initGridSorting();
        initBeerSorting();
        initSlideSorting();
    } catch (e) {
        console.error(e);
        meta.textContent = `Error: ${e.message}`;
    }
}

// Boot — if a token is already in sessionStorage, skip the PIN screen and load immediately
if (getToken()) {
    hidePin();
    loadAll();
} else {
    showPin(); // no token: show the PIN lock screen
}
