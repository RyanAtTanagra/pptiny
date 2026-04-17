#!/usr/bin/env python3
import sqlite3
import string
import secrets
import json
import logging
import os
from urllib.parse import urlparse

DB_PATH = os.environ.get('PPTINY_DB', '/var/lib/pptiny/urls.db')
MAX_URL_LENGTH = 2048
SHORT_CODE_LENGTH = 8
MAX_ROWS = 1_000_000
CONTROL_CHARS = set(range(0x00, 0x20)) | {0x7f}

logger = logging.getLogger('pptiny')
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def generate_short_code(length=SHORT_CODE_LENGTH):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def _json_response(start_response, status, payload):
    body = json.dumps(payload).encode()
    start_response(status, [('Content-Type', 'application/json')])
    return [body]


def _valid_url(url):
    if not url or len(url) > MAX_URL_LENGTH:
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
    return True


def application(environ, start_response):
    method = environ['REQUEST_METHOD']
    path = environ['PATH_INFO'].strip('/')

    if method == 'POST':
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

            if not isinstance(long_url, str) or not _valid_url(long_url):
                return _json_response(start_response, '400 Bad Request',
                                      {'error': 'Invalid URL'})

            db = sqlite3.connect(DB_PATH, timeout=1.0)

            row_count = db.execute('SELECT COUNT(*) FROM urls').fetchone()[0]
            if row_count >= MAX_ROWS:
                return _json_response(start_response, '507 Insufficient Storage',
                                      {'error': 'URL store is full'})

            short_code = None
            for _ in range(10):
                candidate = generate_short_code()
                try:
                    db.execute('INSERT INTO urls (short_code, long_url) VALUES (?, ?)',
                               (candidate, long_url))
                    db.commit()
                    short_code = candidate
                    break
                except sqlite3.IntegrityError:
                    continue

            if short_code is None:
                return _json_response(start_response, '500 Internal Server Error',
                                      {'error': 'Failed to generate unique short code'})

            scheme = environ.get('wsgi.url_scheme', 'http')
            host = environ.get('HTTP_HOST', 'localhost')
            short_url = f"{scheme}://{host}/{short_code}"

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

    elif method == 'GET':
        if not path:
            start_response('200 OK', [('Content-Type', 'text/plain')])
            return [b'URL Shortener - POST {"url": "..."} to create, GET /<code> to redirect']

        db = None
        try:
            db = sqlite3.connect(DB_PATH, timeout=1.0)
            db.row_factory = sqlite3.Row
            result = db.execute('SELECT long_url FROM urls WHERE short_code = ?', (path,)).fetchone()

            if result:
                start_response('301 Moved Permanently', [
                    ('Location', result['long_url']),
                    ('Referrer-Policy', 'no-referrer'),
                ])
                return [b'']
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b'Short URL not found']
        except sqlite3.OperationalError:
            logger.exception('GET sqlite operational error')
            start_response('503 Service Unavailable', [('Content-Type', 'text/plain')])
            return [b'Database busy']
        except Exception:
            logger.exception('GET unhandled error')
            start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])
            return [b'Internal server error']
        finally:
            if db:
                db.close()

    else:
        start_response('405 Method Not Allowed', [('Content-Type', 'text/plain')])
        return [b'Method not allowed']
