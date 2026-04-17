# URL Shortener

A minimal URL shortener built with pure Python (no dependencies).

## Features

- **GET requests (redirects)**: Open to the world - 301 permanent redirects
- **POST requests (create short URLs)**: Restricted to localhost via Apache config
- **SQLite database**: Stores URL mappings
- **Clean URLs**: `/<code>` redirects to the full URL
- **Pure API**: No web interface, JSON only

## File Structure

```
/var/www/pptiny/
├── app.py          # Main WSGI application (Apache only)
├── wsgi.py         # WSGI entry point for Apache
└── init_db.py      # Database initialization script

/var/lib/pptiny/
└── urls.db         # SQLite database (outside DocumentRoot)
```

The DB path can be overridden via the `PPTINY_DB` environment variable.

## Setup Instructions

### 1. Dependencies

**None!** Uses only Python standard library.

### 2. Initialize Database

Run once to create the database directory and schema:

```bash
sudo mkdir -p /var/lib/pptiny
sudo chown www-data:www-data /var/lib/pptiny
sudo -u www-data python3 init_db.py
```

### 3. Configure Apache

#### Install mod_wsgi

```bash
sudo apt-get install libapache2-mod-wsgi-py3
sudo a2enmod wsgi
```

#### Apache VirtualHost Configuration

See `apache-config.example` for the complete configuration. Key points:

- `WSGIScriptAlias` points to `wsgi.py`
- Any method other than `GET`/`HEAD` is restricted to localhost in Apache config
- `www-data` needs write access to `/var/lib/pptiny/`, not to the app directory

#### Restart Apache

```bash
sudo systemctl restart apache2
```

### 4. Set Permissions

The application directory should not be writable by the Apache user:

```bash
sudo chown -R root:root /var/www/pptiny/
sudo chmod 755 /var/www/pptiny/
```

Only the DB directory needs `www-data` write access (already set in step 2).

## Usage

### Creating Short URLs (localhost only)

POST to the root URL with a JSON payload:

```bash
curl -X POST http://localhost/ \
  -H "Content-Type: application/json" \
  -H "Host: pptiny.twistedslinky.org" \
  -d '{"url": "https://example.com/very/long/url"}'
```

Response:
```json
{
  "short_url": "http://pptiny.twistedslinky.org/abc12345",
  "short_code": "abc12345",
  "long_url": "https://example.com/very/long/url"
}
```

### Using Short URLs (available to everyone)

Visit the short URL:

```bash
curl -L http://pptiny.twistedslinky.org/abc12345
```

Returns a `301 Moved Permanently` redirect with `Referrer-Policy: no-referrer`.

### View Database Contents

```bash
sudo -u www-data python3 -c "import sqlite3; db = sqlite3.connect('/var/lib/pptiny/urls.db'); [print(f'{code}: {url}') for code, url in db.execute('SELECT short_code, long_url FROM urls')]"
```

## Security

- **POST (and any non-GET/HEAD) restricted to localhost**: Enforced by Apache configuration
- **URL validation**: `urllib.parse` check for `http`/`https` scheme, non-empty host, no control characters, length cap
- **CSPRNG short codes**: 8-character alphanumeric, generated with `secrets`
- **Content-Type required on POST**: must be `application/json`
- **Row cap**: prevents disk-exhaustion from a runaway local client
- **DB outside DocumentRoot**: `/var/lib/pptiny/urls.db` cannot be served by a misconfigured Apache

## Threat Model

The restriction model assumes only trusted processes on the host can reach the WSGI app over localhost. Anyone who can POST — a neighboring service with SSRF, a compromised local account, a careless cron job — can mint redirects on the shortener's domain pointing anywhere on the internet. 301 responses are cached aggressively by browsers, so takedown is slow. Treat localhost POST access as equivalent to the ability to create phishing links on your domain, and protect accordingly. If that surface is unacceptable, add a destination-domain allowlist or an authentication layer in front of POST.

## Architecture

- **app.py**: WSGI application, handles routing and business logic
- **wsgi.py**: Minimal glue between Apache mod_wsgi and the application
- **init_db.py**: Separate initialization script, run once at setup
- **urls.db**: SQLite database stored outside the web root
