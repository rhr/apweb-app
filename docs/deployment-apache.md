# Deploying behind Apache at a sub-path

How the app runs on an Ubuntu server that already serves other sites with Apache, at
`https://oneil.fieldmuseum.org/apweb/`. The same steps work for any host and prefix;
change `/apweb` and the port everywhere below.

```
browser ──https──> Apache :443  /apweb/…  ──(strips /apweb)──> uvicorn 127.0.0.1:8010 ──> FastAPI
                                                                 serves /api/* and frontend/dist
```

- **One process.** uvicorn serves both the API and the built React app, as in the Docker
  image. systemd keeps it running (`deploy/apweb.service`).
- **Apache strips the prefix.** `ProxyPass /apweb/ → http://127.0.0.1:8010/`, so the
  backend sees `/api/…`, `/assets/…` and so on, exactly as when it runs on its own.
- **The frontend knows the prefix** because it is built with `--base=/apweb/`. Vite
  exposes the base as `import.meta.env.BASE_URL`, and `frontend/src/api.ts` exports it as
  `BASE`. API calls, the router's `basename`, the OneZoom iframe and links in Ask answers
  all use it. The OneZoom viewer (`public/oz-viewer/viewer.js`) calls `../api/oz/…`,
  relative to its own location. A plain `npm run build` still gives an app at `/`.
- **The backend knows the prefix** through `uvicorn --root-path /apweb`. Only the API docs
  (`/apweb/api/docs`) need it.
- **Port 8010.** On oneil, 8000 is taken by herman and 8080 by fieldnotes.

## Files

| file | installed as |
|---|---|
| `deploy/apweb.service` | `/etc/systemd/system/apweb.service` (runs as `rree` from `~/src/apweb-app/backend`) |
| `deploy/apweb.env.example` | `/etc/apweb/apweb.env`, mode 600: `APW_PASSWORD`, `APW_ASK_MODE=server`, `ANTHROPIC_API_KEY` |
| `deploy/apache-apweb.conf` | pasted into the host's `<VirtualHost *:443>` in `/etc/apache2/sites-enabled/000-default-le-ssl.conf` |

Apache modules used: `proxy`, `proxy_http`, `headers` (already enabled on oneil).

## First install

From a checkout at `~/src/apweb-app`:

```bash
# frontend, built for the prefix
cd frontend
npm ci
scripts/fetch-onezoom.sh                 # OneZoom viewer bundle (not committed; see licence note in README)
npm run build -- --base=/apweb/          # → frontend/dist, asset URLs start with /apweb/

# backend and data
cd ../backend
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/apw crawl apweb                # ~3 minutes
APW_CONTACT=<email> .venv/bin/apw crawl apg   # Wikimedia wants contact info in the User-Agent
.venv/bin/apw ingest all                 # → data/apw.sqlite

# service (needs sudo)
cd ..
sudo install -d -m 755 /etc/apweb
sudo install -m 600 deploy/apweb.env.example /etc/apweb/apweb.env
sudo nano /etc/apweb/apweb.env           # set APW_PASSWORD and ANTHROPIC_API_KEY
sudo cp deploy/apweb.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now apweb

# Apache: paste deploy/apache-apweb.conf into the oneil.fieldmuseum.org <VirtualHost *:443>
sudo apache2ctl configtest && sudo systemctl reload apache2
```

Open `https://oneil.fieldmuseum.org/apweb/`. The browser asks for a password; any
username works.

### Apache directives

```apache
RedirectMatch ^/apweb$ /apweb/

<Location "/apweb/">
    ProxyPass        http://127.0.0.1:8010/ flushpackets=on timeout=600
    ProxyPassReverse http://127.0.0.1:8010/
    RequestHeader set X-Forwarded-Proto "https"
</Location>
```

- `flushpackets=on` sends the Ask page's Server-Sent Events on as they arrive, instead
  of buffering them. mod_deflate leaves `text/event-stream` alone with Ubuntu's default
  `deflate.conf`.
- `timeout=600` gives a long Ask answer time to finish.
- Authentication is done by the app (`APW_PASSWORD`, HTTP Basic). Apache passes the
  `Authorization` header through unchanged.

## Updating

| change | do |
|---|---|
| backend code | `git pull`, then `sudo systemctl restart apweb` |
| frontend code | `git pull`, then `cd frontend && npm ci && npm run build -- --base=/apweb/` (no restart) |
| new Python dependencies | `backend/.venv/bin/pip install -e backend`, then restart |
| new APWeb snapshot | `apw crawl apweb && apw ingest all`, then restart |
| password or API key | edit `/etc/apweb/apweb.env`, then restart |

Restarting loses Ask conversations, which are kept in memory.

## Checking and troubleshooting

```bash
systemctl status apweb
journalctl -u apweb -f                                       # backend log
curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8010/     # 401 = running, password on
curl -s -u x:PASSWORD 127.0.0.1:8010/api/ask/status          # ask mode, server key present?
tail -f /var/log/apache2/error.log
```

- **503 from Apache:** the service is down or on another port.
- **Blank page, 404s for `/assets/…`:** the frontend was built without `--base=/apweb/`.
- **The OneZoom page says the viewer is not installed:** run `frontend/scripts/fetch-onezoom.sh`,
  then rebuild.
- **The Ask answer arrives all at once:** something between the browser and Apache is
  buffering (`flushpackets` missing, or compression applied to `text/event-stream`).
- **The service fails to start after a Python change:** the venv uses the interpreter in
  `~/miniconda3`. If that moves or is removed, recreate `backend/.venv`.
