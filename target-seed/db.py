import os
import sqlite3

DB_PATH = os.environ.get("LEDGERLINE_DB", os.path.join(os.path.dirname(__file__), "data", "ledgerline.db"))

SEED = """
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS orders;
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, item TEXT, status TEXT);
INSERT INTO users (name, email) VALUES ('ada', 'ada@example.com'), ('grace', 'grace@example.com'), ('edsger', 'edsger@example.com');
INSERT INTO orders (customer, item, status) VALUES ('ada', 'posters', 'open'), ('ada', 'flyers', 'shipped'), ('grace', 'banners', 'open'), ('edsger', 'cards', 'open');
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect()
    conn.executescript(SEED)
    conn.commit()
    conn.close()
