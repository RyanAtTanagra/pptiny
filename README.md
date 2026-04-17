# URL Shortener

A minimal URL shortener built with pure Python (no dependencies).

## Features

- **GET requests (redirects)**: Open to the world - `302 Found` with short-lived cache
- **POST requests (create short URLs)**: Restricted to localhost via Apache config
- **DELETE requests (remove short URLs)**: Restricted to localhost via Apache config
- **SQLite database**: Stores URL mappings (WAL mode)
- **Clean URLs**: `/<code>` redirects to the full URL
- **Pure API**: No web interface, JSON only

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
| `PPTINY_HOST` | Canonical host used when constructing `short_url` in POST responses (not reflected from `Host` header) | falls back to `SERVER_NAME`, then `HTTP_HOST` |
| `PPTINY_ALLOW_PRIVATE` | Set to `1` to allow short URLs that resolve to loopback/private/link-local IPs (useful for local dev) | unset (private destinations blocked) |

## Setup Instructions

### 1. Dependencies

**None!** Uses only Python standard library.

### 2. Initialize Database

```bash
sudo mkdir -p /var/lib/pptiny
sudo chown www-data:www-data /var/lib/pptiny
sudo -u www-data python3 init_db.py
```

This creates the DB, sets `PRAGMA journal_mode=WAL` (persistent), and sets up the schema + index.

### 3. Configure Apache

Install mod_wsgi and enable SSL:

```bash
sudo apt-get install libapache2-mod-wsgi-py3
sudo a2enmod wsgi ssl
```

See `apache-config.example` for the full VirtualHost. Key points:

- `:80` redirects to `:443` — no plaintext service
- `:443` terminates TLS and runs the WSGI app
- `SetEnv PPTINY_HOST pptiny.twistedslinky.org` pins the canonical hostname
- `<LimitExcept GET HEAD>` restricts POST/DELETE (and any other method) to localhost

Obtain a cert (e.g. via certbot) and restart Apache:

```bash
sudo systemctl restart apache2
```

### 4. Set Permissions

The application directory should not be writable by the Apache user:

```bash
sudo chown -R root:root /var/www/pptiny/
sudo chmod 755 /var/www/pptiny/
```

Only `/var/lib/pptiny/` needs `www-data` write access (set in step 2).

## Usage

### Creating Short URLs (localhost only)

```bash
curl -X POST https://localhost/ \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/very/long/url"}'
```

Response:
```json
{
  "short_url": "https://pptiny.twistedslinky.org/abc12345",
  "short_code": "abc12345",
  "long_url": "https://example.com/very/long/url"
}
```

### Using Short URLs (available to everyone)

```bash
curl -L https://pptiny.twistedslinky.org/abc12345
```

Returns `302 Found` with `Cache-Control: private, max-age=300` and `Referrer-Policy: no-referrer`. Short TTL means abuse takedowns propagate in ~5 minutes.

### Deleting a Short URL (localhost only)

```bash
curl -X DELETE https://localhost/abc12345
```

Returns `204 No Content` on success, `404` if the code was unknown.

### View Database Contents

```bash
sudo -u www-data python3 -c "import sqlite3; db=sqlite3.connect('/var/lib/pptiny/urls.db'); [print(f'{code}: {url}') for code, url in db.execute('SELECT short_code, long_url FROM urls')]"
```

## Security

- **TLS-only**: `:80` redirects to `:443`; no plaintext service
- **Non-GET restricted to localhost**: Apache-enforced for POST, DELETE, and any future method
- **CSPRNG codes**: 8-char alphanumeric via `secrets.choice`
- **URL validation**: `urllib.parse` check for `http`/`https` scheme, non-empty host, no control characters, length cap
- **Private-destination block**: URLs resolving to loopback / RFC1918 / link-local / reserved / multicast IPs are rejected at insert time (advisory — see caveat below). Override with `PPTINY_ALLOW_PRIVATE=1`
- **Canonical host**: Response `short_url` uses `PPTINY_HOST`, not the client-supplied `Host` header
- **Redirect caching**: `302 Found` + short `Cache-Control` lets removed codes expire from browser caches quickly
- **TOCTOU-safe row cap**: Count + insert wrapped in `BEGIN IMMEDIATE`
- **`X-Content-Type-Options: nosniff`** on all responses
- **DB outside DocumentRoot**: cannot be served by a misconfigured Apache

## Threat Model

The restriction model assumes only trusted processes on the host can reach the WSGI app over localhost. Anyone who can POST — a neighboring service with SSRF, a compromised local account, a careless cron job — can mint redirects on the shortener's domain pointing anywhere on the public internet. Treat localhost POST access as equivalent to the ability to create phishing links on your domain.

**Caveat on the private-destination block:** it runs at insert time and resolves DNS once. An attacker who controls DNS for a destination can point it at a public IP during insert and flip it to an internal IP later. The block raises the bar for casual abuse but is not a security boundary; it does not replace network-level egress controls on whatever clicks the link.

If this surface is unacceptable, add a destination-domain allowlist or an authentication layer in front of POST/DELETE.

## Architecture

- **app.py**: WSGI application, routing and business logic
- **wsgi.py**: Minimal glue between Apache mod_wsgi and the application
- **init_db.py**: One-time init: creates DB dir, enables WAL, creates schema + index
- **urls.db**: SQLite database stored outside the web root
