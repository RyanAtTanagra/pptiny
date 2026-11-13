# URL Shortener

A minimal URL shortener built with pure Python (no dependencies).

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/` | public | Short plain-text info message |
| `GET` / `HEAD` | `/<code>` | public | `302 Found` redirect to the stored URL |
| `POST` | `/` | localhost | Create a short URL (JSON body) |
| `DELETE` | `/<code>` | localhost | Remove a short URL |

Localhost-only methods are enforced at the Apache layer via `<LimitExcept GET HEAD>`.

## File Structure

```
/var/www/pptiny/
├── app.py          # WSGI application
├── wsgi.py         # WSGI entry point
└── init_db.py      # One-time database init

/var/lib/pptiny/
└── urls.db         # SQLite database (outside DocumentRoot)
```

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `PPTINY_DB` | SQLite DB path | `/var/lib/pptiny/urls.db` |
| `PPTINY_HOST` | Canonical host for `short_url` in POST responses (not reflected from `Host`) | `SERVER_NAME`, then `HTTP_HOST` |
| `PPTINY_ALLOW_PRIVATE` | Set to `1` to allow loopback / private / link-local IP-literal destinations | unset |

Under Apache, set these with `SetEnv` in the vhost. mod_wsgi delivers `SetEnv` values in the per-request WSGI environ (not the process environment), so the app reads each setting from there first and falls back to the process environment. `init_db.py` is a CLI script and reads `PPTINY_DB` from the process environment only.

## Setup

### 1. Dependencies

None — Python standard library only.

### 2. Initialize the Database

```bash
sudo mkdir -p /var/lib/pptiny
sudo chown www-data:www-data /var/lib/pptiny
sudo -u www-data python3 init_db.py
```

Creates the DB dir, enables `journal_mode=WAL`, and creates the schema + index.

### 3. Configure Apache

```bash
sudo apt-get install libapache2-mod-wsgi-py3
sudo a2enmod wsgi ssl headers
```

See `apache-config.example`. Key points:

- `:80` redirects to `:443` — no plaintext service
- `:443` terminates TLS and runs the WSGI app
- `SetEnv PPTINY_HOST pptiny.example.com` pins the canonical hostname; `UseCanonicalName On` backs it up by making `SERVER_NAME` the vhost's `ServerName` rather than the client's `Host` header
- `Strict-Transport-Security` (HSTS, one year) is set on every HTTPS response via `mod_headers`
- `<LimitExcept GET HEAD>` restricts POST/DELETE (and any other method) to localhost
- `LimitRequestBody 65536` caps request bodies at the Apache layer

Obtain a cert (e.g. via certbot), then:

```bash
sudo systemctl restart apache2
```

### 4. File Permissions

The application directory should not be writable by the Apache user:

```bash
sudo chown -R root:root /var/www/pptiny/
sudo chmod 755 /var/www/pptiny/
```

Only `/var/lib/pptiny/` needs `www-data` write access (set in step 2).

## Usage

### Create (localhost only)

Run on the server itself. `--resolve` sends the request to loopback while keeping the real hostname, so the request hits the right vhost and the TLS certificate validates normally — no `-k` needed:

```bash
curl --resolve pptiny.example.com:443:127.0.0.1 \
  -X POST https://pptiny.example.com/ \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/very/long/url"}'
```

URLs must be printable ASCII with no spaces: percent-encode anything else (and use punycode for internationalized hostnames).

Response: `201 Created` with a `Location: /<code>` header and JSON body:

```json
{
  "short_url": "https://pptiny.example.com/abc12345",
  "short_code": "abc12345",
  "long_url": "https://example.com/very/long/url"
}
```

### Redirect (public)

```bash
curl -L https://pptiny.example.com/abc12345
```

Returns `302 Found` with `Cache-Control: private, max-age=300` and `Referrer-Policy: no-referrer`. The short TTL means abuse takedowns propagate to browser caches within ~5 minutes.

### Delete (localhost only)

```bash
curl --resolve pptiny.example.com:443:127.0.0.1 \
  -X DELETE https://pptiny.example.com/abc12345
```

Returns `204 No Content` on success, `404` if the code is unknown.

### Peek the Database

```bash
sudo -u www-data python3 -c "import sqlite3; db=sqlite3.connect('/var/lib/pptiny/urls.db'); [print(f'{code}: {url}') for code, url in db.execute('SELECT short_code, long_url FROM urls')]"
```

## HTTP Responses

| Code | When |
|---|---|
| `200 OK` | `GET /` info message |
| `201 Created` | Short URL created |
| `204 No Content` | Delete succeeded |
| `302 Found` | `GET /<code>` redirect |
| `400 Bad Request` | Malformed POST (bad `Content-Length`, invalid UTF-8 or JSON, body not a JSON object) or invalid URL |
| `404 Not Found` | Unknown short code |
| `405 Method Not Allowed` | Method outside `GET` / `HEAD` / `POST` / `DELETE` (sends an `Allow` header) |
| `413 Payload Too Large` | POST body exceeds 4 KiB |
| `415 Unsupported Media Type` | POST `Content-Type` is not `application/json` |
| `500 Internal Server Error` | Unhandled error, or no unique code found after 10 attempts |
| `503 Service Unavailable` | SQLite operational error (usually lock contention) |
| `507 Insufficient Storage` | DB row cap (`MAX_ROWS`) reached |

## Security

- **TLS-only**: `:80` redirects to `:443`; no plaintext service. HSTS (`max-age=31536000`) on every HTTPS response
- **Non-GET restricted to localhost**: Apache-enforced for POST, DELETE, and any other method
- **CSPRNG short codes**: 8-char alphanumeric via `secrets.choice`
- **URL validation**: `urllib.parse` — `http`/`https` scheme, non-empty host, printable ASCII only (no control characters, spaces, or non-ASCII — percent-encode instead), 2048-char length cap
- **Private IP-literal block**: URLs whose host is a loopback / RFC1918 / link-local / reserved / multicast IP literal are rejected. Hostnames are **not** resolved — no DNS lookups on the POST path. Override with `PPTINY_ALLOW_PRIVATE=1`
- **Body size cap**: 4 KiB at the app layer (`413` on overflow), 64 KiB at the Apache layer
- **Canonical host**: POST response `short_url` uses `PPTINY_HOST`, not the client-supplied `Host` header
- **No rate limiting**: not built in. Short codes are not practically guessable (62^8 space), but if request floods matter for your deployment, add it at the Apache or network layer
- **Redirect caching**: `302 Found` + short `Cache-Control` lets removed codes expire from browser caches quickly
- **TOCTOU-safe row cap**: count + insert wrapped in `BEGIN IMMEDIATE`
- **`X-Content-Type-Options: nosniff`** on every response
- **DB outside DocumentRoot**: cannot be served by a misconfigured Apache

## Threat Model

The restriction model assumes only trusted processes on the host can reach the WSGI app over localhost. Anyone who can POST — a neighboring service with SSRF, a compromised local account, a careless cron job — can mint redirects on the shortener's domain pointing anywhere on the public internet. Treat localhost POST access as equivalent to the ability to create phishing links on your domain.

**Caveat on the IP-literal block:** it only catches URLs where the host is literally an internal IP (e.g. `http://10.0.0.1/`). A URL whose hostname happens to resolve to an internal IP (`http://internal.corp/`) is not rejected — we don't do DNS lookups on the POST path. Internal-endpoint phishing via custom DNS is out of scope for this app; if it matters for your deployment, add network-level egress controls on clients or a destination-domain allowlist.

If localhost POST access itself is too broad, add an authentication layer in front of POST/DELETE.

## Architecture

- **app.py**: WSGI application — routing and business logic
- **wsgi.py**: Minimal glue between Apache mod_wsgi and the application
- **init_db.py**: One-time init — creates DB dir, enables WAL, creates schema + index
- **urls.db**: SQLite database, stored outside the web root
