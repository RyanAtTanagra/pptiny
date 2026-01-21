# URL Shortener

A minimal URL shortener built with pure Python (no dependencies).

## Features

- **GET requests (redirects)**: Open to the world - 301 permanent redirects
- **POST requests (create short URLs)**: Restricted to localhost via Apache config
- **SQLite database**: Stores URL mappings and tracks hit counts
- **Clean URLs**: `/<code>` redirects to the full URL
- **Pure API**: No web interface, JSON only

## File Structure

```
/var/www/pptiny/
├── app.py          # Main WSGI application (Apache only)
├── wsgi.py         # WSGI entry point for Apache
├── init_db.py      # Database initialization script
└── urls.db         # SQLite database (created on first run)
```

## Setup Instructions

### 1. Dependencies

**None!** Uses only Python standard library (sqlite3, json, string, random, os).

### 2. Initialize Database

Run once to create the database:

```bash
python3 init_db.py
```

### 3. Configure Apache

#### Install mod_wsgi

```bash
sudo apt-get install libapache2-mod-wsgi-py3
sudo a2enmod wsgi
```

#### Apache VirtualHost Configuration

See `apache-config.example` for complete configuration. Key points:

- Use `WSGIScriptAlias` to point to `wsgi.py`
- Restrict POST to localhost in Apache config (not in Python)
- Ensure `www-data` has write access to the directory

#### Restart Apache

```bash
sudo systemctl restart apache2
```

### 4. Set Permissions

Apache needs write access to create/update the database:

```bash
sudo chown -R www-data:www-data /var/www/pptiny/
sudo chmod 775 /var/www/pptiny/
```

## Usage

### Creating Short URLs (localhost only)

POST to the root URL with JSON payload:

```bash
curl -X POST http://localhost/ \
  -H "Content-Type: application/json" \
  -H "Host: pptiny.twistedslinky.org" \
  -d '{"url": "https://example.com/very/long/url"}'
```

Response:
```json
{
  "short_url": "http://pptiny.twistedslinky.org/abc123",
  "short_code": "abc123",
  "long_url": "https://example.com/very/long/url"
}
```

### Using Short URLs (available to everyone)

Visit the short URL:

```bash
curl -L http://pptiny.twistedslinky.org/abc123
```

Returns a `301 Moved Permanently` redirect to the original URL.

### View Database Contents

```bash
# List all URLs
python3 -c "import sqlite3; db = sqlite3.connect('/var/www/pptiny/urls.db'); [print(f'{code}: {url} ({hits} hits)') for code, url, hits in db.execute('SELECT short_code, long_url, hits FROM urls')]"

# View schema
python3 -c "import sqlite3; print(sqlite3.connect('/var/www/pptiny/urls.db').execute('SELECT sql FROM sqlite_master WHERE type=\"table\"').fetchone()[0])"
```

## Security

- **POST restricted to localhost**: Enforced by Apache configuration
- **URL validation**: Only accepts URLs starting with `http://` or `https://`
- **Random short codes**: 6-character alphanumeric codes prevent enumeration
- **No authentication needed**: Since POST is localhost-only

## Architecture

- **app.py**: Pure WSGI application, handles routing and business logic (Apache only)
- **wsgi.py**: Minimal glue between Apache mod_wsgi and the application
- **init_db.py**: Separate initialization script, run once at setup
- **urls.db**: SQLite database with automatic hit tracking

The application uses 301 permanent redirects, proper connection cleanup with `finally` blocks, and maintains simplicity by avoiding frameworks.
