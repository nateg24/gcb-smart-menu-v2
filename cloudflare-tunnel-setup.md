# Cloudflare Tunnel Setup — GCB Smart Menu
## Goal
Expose the menu admin panel at `menu.gnarlycedar.com` so staff can update taps remotely.

---

## Step 1 — Add your domain to Cloudflare
1. Go to **cloudflare.com** and create a free account (or log in)
2. Click **Add a domain** → type `gnarlycedar.com` → choose the **Free** plan
3. Cloudflare will scan and import your existing DNS records — verify your WordPress records appear before continuing
4. Cloudflare will give you two nameservers (e.g. `xxx.ns.cloudflare.com`) — save these for Step 2

---

## Step 2 — Point your domain to Cloudflare at WordPress.com
1. Log into **WordPress.com** → go to **Upgrades → Domains**
2. Click `gnarlycedar.com` → **Name Servers**
3. Switch from "WordPress.com nameservers" to **Custom nameservers**
4. Enter the two nameservers from Step 1
5. Save — propagation takes a few minutes to a few hours. Your WordPress site stays up the whole time.

---

## Step 3 — Install cloudflared on the server machine
1. Search for `cloudflared-windows-amd64.msi` on Cloudflare's GitHub releases page and run the installer
2. Open a terminal and verify it installed:
```
cloudflared --version
```

---

## Step 4 — Authenticate cloudflared with your Cloudflare account
Run:
```
cloudflared tunnel login
```
A browser window will open — log in and select `gnarlycedar.com`. This saves a certificate to your machine.

---

## Step 5 — Create the tunnel
Run:
```
cloudflared tunnel create gcb-menu
```
Note the **tunnel ID** it prints — you'll need it in Step 6.

---

## Step 6 — Create the config file
Create the file `C:\Users\Nate\.cloudflared\config.yml` with the following contents.
Replace `<your-tunnel-id>` with the ID from Step 5.

```yaml
tunnel: gcb-menu
credentials-file: C:\Users\Nate\.cloudflared\<your-tunnel-id>.json

ingress:
  - hostname: menu.gnarlycedar.com
    service: http://localhost:8000
  - service: http_status:404
```

---

## Step 7 — Add the DNS record in Cloudflare
Run:
```
cloudflared tunnel route dns gcb-menu menu.gnarlycedar.com
```
This automatically creates a `menu.gnarlycedar.com` CNAME in Cloudflare pointing to your tunnel.

---

## Step 8 — Run the tunnel
Make sure uvicorn is running first, then:
```
cloudflared tunnel run gcb-menu
```
The menu is now live at:
- **Admin panel:** `https://menu.gnarlycedar.com/admin`
- **TV display:** `https://menu.gnarlycedar.com/menu`

---

## Step 9 (optional) — Run as a Windows service
So the tunnel starts automatically on boot without manual intervention:
```
cloudflared service install
```

---

## Notes
- The tunnel only works while the server machine is on and connected to the internet
- uvicorn still needs to be running — consider setting it up as a startup task as well
- The PIN on the admin panel is your only access control — make sure it's set to something strong before going live remotely
