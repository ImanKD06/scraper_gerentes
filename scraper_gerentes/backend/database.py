"""
Capa de base de datos — SQLite por defecto, compatible con MySQL.
"""

import sqlite3, os, logging
from datetime import datetime
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

DB_PATH = "/tmp/gerentes.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    # Si la BD existe pero le falta la columna fuente, borrarla y recrear
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(empresas)").fetchall()]
            conn.close()
            if cols and "fuente" not in cols:
                os.remove(DB_PATH)
                logger.info("BD antigua sin columna fuente eliminada, recreando...")
        except Exception:
            if os.path.exists(DB_PATH):
                os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS empresas (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre           TEXT NOT NULL,
            provincia        TEXT,
            email            TEXT,
            telefono         TEXT,
            web              TEXT,
            direccion        TEXT,
            facturacion_eur  REAL,
            cnae_cpv         TEXT,
            url_datoscif     TEXT,
            fuente           TEXT,
            gerente          TEXT,
            estado           TEXT DEFAULT 'pendiente',
            error_msg        TEXT,
            fecha_scraping   TIMESTAMP,
            fecha_carga      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nombre ON empresas(nombre)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_estado ON empresas(estado)")
    conn.commit()
    conn.close()
    logger.info("Base de datos inicializada.")
