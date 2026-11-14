#!/usr/bin/env python3
import ipaddress
import json
import logging
import os
import secrets
import sqlite3
import string
from contextlib import closing
from urllib.parse import urlparse

DEFAULT_DB_PATH = '/var/lib/pptiny/urls.db'
MAX_URL_LENGTH = 2048
MAX_BODY_BYTES = 4096
SHORT_CODE_LENGTH = 8
MAX_ROWS = 1_000_000
REDIRECT_CACHE_SECONDS = 300

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('pptiny')


def _setting(environ, key):
    # mod_wsgi puts SetEnv values in the WSGI environ, not os.environ.
    return environ.get(key) or os.environ.get(key)


def _connect(environ):
    # Autocommit; closing mid-transaction rolls back.
    path = _setting(environ, 'PPTINY_DB') or DEFAULT_DB_PATH
    return closing(sqlite3.connect(path, timeout=1.0, isolation_level=None))


def _response(start_response, status, body, extra_headers=()):
    # dict -> JSON, bytes -> plain text.
    content_type = 'text/plain'
    if isinstance(body, dict):
        content_type = 'application/json'
        body = json.dumps(body).encode()
    start_response(status, [
        ('Content-Type', content_type),
        ('X-Content-Type-Options', 'nosniff'),
        *extra_headers,
    ])
    return [body]


def _error(start_response, status, message):
    return _response(start_response, status, {'error': message})


def _short_code():
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(SHORT_CODE_LENGTH))


def _host_is_blocked_literal(host):
    # IP literals only; hostnames are not resolved.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _valid_url(url, allow_private=False):
    if not isinstance(url, str) or len(url) > MAX_URL_LENGTH:
        return False
    # Printable ASCII only, so the URL is safe as a Location header.
    if any(not 0x21 <= ord(c) <= 0x7e for c in url):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    return allow_private or not _host_is_blocked_literal(parsed.hostname)


def _handle_post(environ, start_response):
    content_type = environ.get('CONTENT_TYPE', '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        return _error(start_response, '415 Unsupported Media Type',
                      'Content-Type must be application/json')

    try:
        length = int(environ.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = -1
    if length < 0:
        return _error(start_response, '400 Bad Request', 'Invalid Content-Length')
    if length > MAX_BODY_BYTES:
        return _error(start_response, '413 Payload Too Large', 'Request body too large')

    try:
        data = json.loads(environ['wsgi.input'].read(length))
    except ValueError:
        return _error(start_response, '400 Bad Request', 'Invalid JSON')
    if not isinstance(data, dict):
        return _error(start_response, '400 Bad Request', 'JSON body must be an object')

    long_url = data.get('url')
    allow_private = _setting(environ, 'PPTINY_ALLOW_PRIVATE') == '1'
    if not _valid_url(long_url, allow_private):
        return _error(start_response, '400 Bad Request', 'Invalid URL')

    short_code = _short_code()
    with _connect(environ) as db:
        # IMMEDIATE takes the write lock, so the row cap can't be raced.
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT COUNT(*) FROM urls').fetchone()[0] >= MAX_ROWS:
            return _error(start_response, '507 Insufficient Storage', 'URL store is full')
        db.execute('INSERT INTO urls (short_code, long_url) VALUES (?, ?)',
                   (short_code, long_url))
        db.execute('COMMIT')

    host = _setting(environ, 'PPTINY_HOST') or environ['SERVER_NAME']
    return _response(start_response, '201 Created', {
        'short_url': f"{environ['wsgi.url_scheme']}://{host}/{short_code}",
        'short_code': short_code,
        'long_url': long_url,
    }, [('Location', f'/{short_code}')])


def _handle_get(environ, start_response, path):
    if not path:
        return _response(start_response, '200 OK',
                         b'URL Shortener - POST {"url": "..."} to create, GET /<code> to redirect')

    with _connect(environ) as db:
        row = db.execute('SELECT long_url FROM urls WHERE short_code = ?', (path,)).fetchone()
    if not row:
        return _response(start_response, '404 Not Found', b'Short URL not found')
    return _response(start_response, '302 Found', b'', [
        ('Location', row[0]),
        ('Cache-Control', f'private, max-age={REDIRECT_CACHE_SECONDS}'),
        ('Referrer-Policy', 'no-referrer'),
    ])


def _handle_delete(environ, start_response, path):
    if not path:
        return _error(start_response, '400 Bad Request', 'short code required')

    with _connect(environ) as db:
        deleted = db.execute('DELETE FROM urls WHERE short_code = ?', (path,)).rowcount
    if not deleted:
        return _error(start_response, '404 Not Found', 'short code not found')
    return _response(start_response, '204 No Content', b'')


def application(environ, start_response):
    method = environ['REQUEST_METHOD']
    path = environ['PATH_INFO'].strip('/')

    try:
        if method == 'POST':
            return _handle_post(environ, start_response)
        if method in ('GET', 'HEAD'):
            return _handle_get(environ, start_response, path)
        if method == 'DELETE':
            return _handle_delete(environ, start_response, path)
        return _response(start_response, '405 Method Not Allowed', b'Method not allowed',
                         [('Allow', 'GET, HEAD, POST, DELETE')])
    except sqlite3.OperationalError:
        logger.exception('%s sqlite operational error', method)
        return _error(start_response, '503 Service Unavailable', 'Database busy')
    except Exception:
        logger.exception('%s unhandled error', method)
        return _error(start_response, '500 Internal Server Error', 'Internal server error')
