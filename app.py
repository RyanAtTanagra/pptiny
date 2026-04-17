#!/usr/bin/env python3
import ipaddress
import json
import logging
import os
import secrets
import socket
import sqlite3
import string
from urllib.parse import urlparse

DB_PATH = os.environ.get('PPTINY_DB', '/var/lib/pptiny/urls.db')
CANONICAL_HOST = os.environ.get('PPTINY_HOST')
ALLOW_PRIVATE = os.environ.get('PPTINY_ALLOW_PRIVATE') == '1'
MAX_URL_LENGTH = 2048
SHORT_CODE_LENGTH = 8
MAX_ROWS = 1_000_000
REDIRECT_CACHE_SECONDS = 300
CONTROL_CHARS = set(range(0x00, 0x20)) | {0x7f}

logger = logging.getLogger('pptiny')
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def _short_code(length=SHORT_CODE_LENGTH):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def _json_response(start_response, status, payload):
    body = json.dumps(payload).encode()
    start_response(status, [
        ('Content-Type', 'application/json'),
        ('X-Content-Type-Options', 'nosniff'),
    ])
    return [body]


def _text_response(start_response, status, body):
    start_response(status, [
        ('Content-Type', 'text/plain'),
        ('X-Content-Type-Options', 'nosniff'),
    ])
    return [body]


def _host_is_private(host):
    # DNS resolution at insert time; this is advisory, not a security boundary
    # (TOCTOU: DNS can be flipped after insert). It's meant to make weaponizing
    # the shortener for internal-endpoint phishing noticeably harder.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split('%', 1)[0])
        except ValueError:
            continue
        if (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def _valid_url(url):
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        return False
    if any(ord(c) in CONTROL_CHARS for c in url):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    if not parsed.hostname:
        return False
    if not ALLOW_PRIVATE and _host_is_private(parsed.hostname):
        return False
    return True


def _canonical_host(environ):
    return CANONICAL_HOST or environ.get('SERVER_NAME') or environ.get('HTTP_HOST', 'localhost')


def _handle_post(environ, start_response):
    db = None
    try:
        content_type = environ.get('CONTENT_TYPE', '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            return _json_response(start_response, '415 Unsupported Media Type',
                                  {'error': 'Content-Type must be application/json'})

        content_length = int(environ.get('CONTENT_LENGTH', 0) or 0)
        body = environ['wsgi.input'].read(content_length)
        data = json.loads(body.decode('utf-8'))
        long_url = data.get('url', '')

        if not _valid_url(long_url):
            return _json_response(start_response, '400 Bad Request', {'error': 'Invalid URL'})

        db = sqlite3.connect(DB_PATH, timeout=1.0, isolation_level=None)
        db.execute('BEGIN IMMEDIATE')
        try:
            row_count = db.execute('SELECT COUNT(*) FROM urls').fetchone()[0]
            if row_count >= MAX_ROWS:
                db.execute('ROLLBACK')
                return _json_response(start_response, '507 Insufficient Storage',
                                      {'error': 'URL store is full'})

            short_code = None
            for _ in range(10):
                candidate = _short_code()
                try:
                    db.execute('INSERT INTO urls (short_code, long_url) VALUES (?, ?)',
                               (candidate, long_url))
                    short_code = candidate
                    break
                except sqlite3.IntegrityError:
                    continue

            if short_code is None:
                db.execute('ROLLBACK')
                return _json_response(start_response, '500 Internal Server Error',
                                      {'error': 'Failed to generate unique short code'})

            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK')
            raise

        scheme = environ.get('wsgi.url_scheme', 'http')
        short_url = f"{scheme}://{_canonical_host(environ)}/{short_code}"

        return _json_response(start_response, '200 OK', {
            'short_url': short_url,
            'short_code': short_code,
            'long_url': long_url,
        })

    except json.JSONDecodeError:
        return _json_response(start_response, '400 Bad Request', {'error': 'Invalid JSON'})
    except sqlite3.OperationalError:
        logger.exception('POST sqlite operational error')
        return _json_response(start_response, '503 Service Unavailable',
                              {'error': 'Database busy'})
    except Exception:
        logger.exception('POST unhandled error')
        return _json_response(start_response, '500 Internal Server Error',
                              {'error': 'Internal server error'})
    finally:
        if db:
            db.close()


def _handle_get(environ, start_response, path):
    if not path:
        return _text_response(start_response, '200 OK',
                              b'URL Shortener - POST {"url": "..."} to create, GET /<code> to redirect')

    db = None
    try:
        db = sqlite3.connect(DB_PATH, timeout=1.0)
        db.row_factory = sqlite3.Row
        result = db.execute(
            'SELECT long_url FROM urls WHERE short_code = ?', (path,)
        ).fetchone()

        if result:
            start_response('302 Found', [
                ('Location', result['long_url']),
                ('Cache-Control', f'private, max-age={REDIRECT_CACHE_SECONDS}'),
                ('Referrer-Policy', 'no-referrer'),
                ('X-Content-Type-Options', 'nosniff'),
            ])
            return [b'']
        return _text_response(start_response, '404 Not Found', b'Short URL not found')
    except sqlite3.OperationalError:
        logger.exception('GET sqlite operational error')
        return _text_response(start_response, '503 Service Unavailable', b'Database busy')
    except Exception:
        logger.exception('GET unhandled error')
        return _text_response(start_response, '500 Internal Server Error', b'Internal server error')
    finally:
        if db:
            db.close()


def _handle_delete(environ, start_response, path):
    if not path:
        return _json_response(start_response, '400 Bad Request',
                              {'error': 'short code required'})

    db = None
    try:
        db = sqlite3.connect(DB_PATH, timeout=1.0)
        cursor = db.execute('DELETE FROM urls WHERE short_code = ?', (path,))
        db.commit()
        if cursor.rowcount == 0:
            return _json_response(start_response, '404 Not Found',
                                  {'error': 'short code not found'})
        start_response('204 No Content', [('X-Content-Type-Options', 'nosniff')])
        return [b'']
    except sqlite3.OperationalError:
        logger.exception('DELETE sqlite operational error')
        return _json_response(start_response, '503 Service Unavailable',
                              {'error': 'Database busy'})
    except Exception:
        logger.exception('DELETE unhandled error')
        return _json_response(start_response, '500 Internal Server Error',
                              {'error': 'Internal server error'})
    finally:
        if db:
            db.close()


def application(environ, start_response):
    method = environ['REQUEST_METHOD']
    path = environ['PATH_INFO'].strip('/')

    if method == 'POST':
        return _handle_post(environ, start_response)
    if method in ('GET', 'HEAD'):
        return _handle_get(environ, start_response, path)
    if method == 'DELETE':
        return _handle_delete(environ, start_response, path)
    return _text_response(start_response, '405 Method Not Allowed', b'Method not allowed')
