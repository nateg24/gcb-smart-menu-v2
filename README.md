# GCB Smart Menu v2

A real-time tap management system for Gnarly Cedar Brewery. Manage beers and tap assignments through an admin panel, and display a live menu on TV screens that updates automatically via WebSockets.

## Tech Stack

- **Backend:** FastAPI + SQLAlchemy + SQLite
- **Server:** Uvicorn (ASGI)
- **Frontend:** Vanilla JS, custom CSS
- **Real-time:** WebSockets
- **Deployment:** Cloudflare Tunnel (see [cloudflare-tunnel-setup.md](cloudflare-tunnel-setup.md))

---

## Setup

### Prerequisites

- Python 3.12+

### Install

```bash
# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

No `.env` file is required for development. The only configurable environment variable is:

| Variable    | Default | Description                              |
| ----------- | ------- | ---------------------------------------- |
| `ADMIN_PIN` | `1515`  | PIN required to log into the admin panel |

Set it for production:

```bash
export ADMIN_PIN="your-strong-pin"
```

The SQLite database (`menu.db`) is created automatically on first run. On first startup, the app seeds 20 sample beers and 24 tap slots.

---

## Running the App

```bash
# Development (with auto-reload)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Production
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Access Points

| URL                           | Description                         |
| ----------------------------- | ----------------------------------- |
| `http://localhost:8000/admin` | Admin panel (tap & beer management) |
| `http://localhost:8000/menu`  | TV display (real-time menu)         |
| `http://localhost:8000/docs`  | Swagger API docs                    |

---

## Admin Panel

Navigate to `/admin` and enter the `ADMIN_PIN` to log in. From there you can:

- Add, edit, or deactivate beers
- Assign beers to tap numbers
- Set tap status: **ON**, **OUT**, or **COMING SOON**
- Drag-and-drop to reorder beers and taps
- Bulk import beers via JSON

## TV Display

Navigate to `/menu` on any TV or browser. The display:

- Connects automatically via WebSocket
- Updates in real-time whenever any tap or beer change is made from the admin panel
- Is optimized for 1280px-wide screens

---

## Remote Access (Cloudflare Tunnel)

To expose the app publicly (e.g., at `menu.gnarlycedar.com`), follow the steps in [cloudflare-tunnel-setup.md](cloudflare-tunnel-setup.md).

---

## API Overview

| Method   |         Endpoint        | Auth | Description                     |
| -------- | ----------------------- | ---- | ------------------------------- |
| `POST`   | `/api/auth/login`       | No   | Exchange PIN for Bearer token   |
| `GET`    | `/api/menu`             | No   | Full menu with taps and beers   |
| `GET`    | `/api/beers`            | No   | List all beers                  |
| `POST`   | `/api/beers`            | Yes  | Create a beer                   |
| `PUT`    | `/api/beers/{id}`       | Yes  | Update a beer                   |
| `DELETE` | `/api/beers/{id}`       | Yes  | Deactivate a beer (soft delete) |
| `POST`   | `/api/taps/{id}/assign` | Yes  | Assign a beer to a tap          |
| `POST`   | `/api/taps/{id}/status` | Yes  | Set tap status                  |
| `POST`   | `/api/beers/reorder`    | Yes  | Reorder beers                   |
| `POST`   | `/api/taps/reorder`     | Yes  | Reorder taps                    |
| `POST`   | `/api/beers/bulk`       | Yes  | Bulk import/upsert beers        |
| `WS`     | `/ws/menu`              | No   | Real-time menu sync             |

All authenticated endpoints require a `Authorization: Bearer <token>` header. Obtain the token from `/api/auth/login`. Tokens are stored in memory and cleared on server restart.
