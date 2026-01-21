#!/usr/bin/env python3
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'urls.db')

def init_db():
    """Initialize database if it doesn't exist"""
    db = sqlite3.connect(DB_PATH)
    db.execute('''CREATE TABLE IF NOT EXISTS urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        short_code TEXT UNIQUE NOT NULL,
        long_url TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        hits INTEGER DEFAULT 0
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_short_code ON urls(short_code)')
    db.commit()
    db.close()
    print(f"Database initialized at {DB_PATH}")

if __name__ == '__main__':
    init_db()
