#!/usr/bin/env python3
import sqlite3
import string
import random
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'urls.db')
MAX_URL_LENGTH = 2048

def generate_short_code(length=6):
    """Generate a random short code"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def application(environ, start_response):
    """WSGI application"""
    method = environ['REQUEST_METHOD']
    path = environ['PATH_INFO'].strip('/')

    # POST - create short URL
    if method == 'POST':
        db = None
        try:
            content_length = int(environ.get('CONTENT_LENGTH', 0))
            body = environ['wsgi.input'].read(content_length)
            data = json.loads(body.decode('utf-8'))
            long_url = data.get('url', '')

            # Validate URL length
            if len(long_url) > MAX_URL_LENGTH:
                start_response('400 Bad Request', [('Content-Type', 'application/json')])
                return [json.dumps({'error': 'URL exceeds maximum length'}).encode()]

            # Validate URL scheme
            if not long_url.startswith(('http://', 'https://')):
                start_response('400 Bad Request', [('Content-Type', 'application/json')])
                return [json.dumps({'error': 'URL must start with http:// or https://'}).encode()]

            # Generate unique short code
            db = sqlite3.connect(DB_PATH, timeout=1.0)
            for _ in range(10):
                short_code = generate_short_code()
                try:
                    db.execute('INSERT INTO urls (short_code, long_url) VALUES (?, ?)',
                              (short_code, long_url))
                    db.commit()
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                start_response('500 Internal Server Error', [('Content-Type', 'application/json')])
                return [json.dumps({'error': 'Failed to generate unique short code'}).encode()]

            # Build response
            scheme = environ.get('wsgi.url_scheme', 'http')
            host = environ.get('HTTP_HOST', 'localhost')
            short_url = f"{scheme}://{host}/{short_code}"

            response = json.dumps({
                'short_url': short_url,
                'short_code': short_code,
                'long_url': long_url
            })

            start_response('200 OK', [('Content-Type', 'application/json')])
            return [response.encode()]

        except json.JSONDecodeError:
            start_response('400 Bad Request', [('Content-Type', 'application/json')])
            return [json.dumps({'error': 'Invalid JSON'}).encode()]
        except Exception:
            start_response('500 Internal Server Error', [('Content-Type', 'application/json')])
            return [json.dumps({'error': 'Internal server error'}).encode()]
        finally:
            if db:
                db.close()

    # GET - redirect or info
    elif method == 'GET':
        # Root - just return service info
        if not path:
            start_response('200 OK', [('Content-Type', 'text/plain')])
            return [b'URL Shortener - POST {"url": "..."} to create, GET /<code> to redirect']

        # Short code lookup - redirect
        db = None
        try:
            db = sqlite3.connect(DB_PATH, timeout=1.0)
            db.row_factory = sqlite3.Row
            result = db.execute('SELECT long_url FROM urls WHERE short_code = ?', (path,)).fetchone()

            if result:
                # Increment hit counter
                db.execute('UPDATE urls SET hits = hits + 1 WHERE short_code = ?', (path,))
                db.commit()

                start_response('301 Moved Permanently', [('Location', result['long_url'])])
                return [b'']
            else:
                start_response('404 Not Found', [('Content-Type', 'text/plain')])
                return [b'Short URL not found']
        except Exception:
            start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])
            return [b'Internal server error']
        finally:
            if db:
                db.close()

    # Other methods not supported
    else:
        start_response('405 Method Not Allowed', [('Content-Type', 'text/plain')])
        return [b'Method not allowed']
