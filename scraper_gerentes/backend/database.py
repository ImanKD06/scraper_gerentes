"""
Capa de base de datos.
Por defecto usa SQLite (sin configuración). Para MySQL, cambiar DATABASE_URL en config.py.
"""

import sqlite3
import os
import logging
from datetime import datetime
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "gerentes.db")


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crea las tablas si no existen."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS empresas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                provincia TEXT,
                email TEXT,
                telefono TEXT,
                web TEXT,
                direccion TEXT,
                facturacion_eur REAL,
                cnae_cpv TEXT,
                url_datoscif TEXT,
                gerente TEXT,
                estado TEXT DEFAULT 'pendiente',
                error_msg TEXT,
                fecha_scraping TIMESTAMP,
                fecha_carga TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_nombre ON empresas(nombre);
            CREATE INDEX IF NOT EXISTS idx_estado ON empresas(estado);
        """)
    logger.info("Base de datos inicializada.")


def cargar_empresas_desde_excel(filepath: str) -> int:
    """Carga empresas del Excel en la BD. Evita duplicados por nombre."""
    import pandas as pd
    df = pd.read_excel(filepath)
    df = df.where(pd.notna(df), None)

    inserted = 0
    with get_connection() as conn:
        for _, row in df.iterrows():
            nombre = str(row.get("nombre", "")).strip()
            if not nombre:
                continue
            exists = conn.execute("SELECT id FROM empresas WHERE nombre = ?", (nombre,)).fetchone()
            if exists:
                continue
            conn.execute("""
                INSERT INTO empresas (nombre, provincia, email, telefono, web, direccion, facturacion_eur, cnae_cpv)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                nombre,
                row.get("provincia"),
                row.get("email"),
                row.get("telefono"),
                row.get("web"),
                row.get("direccion"),
                row.get("facturacion_eur"),
                str(row.get("cnae_cpv", "")) if row.get("cnae_cpv") else None,
            ))
            inserted += 1
    logger.info(f"Insertadas {inserted} empresas nuevas.")
    return inserted


def get_empresas(estado: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict]:
    with get_connection() as conn:
        if estado:
            rows = conn.execute(
                "SELECT * FROM empresas WHERE estado = ? ORDER BY id LIMIT ? OFFSET ?",
                (estado, limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM empresas ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]


def get_empresas_pendientes() -> List[Dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM empresas WHERE estado = 'pendiente' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def actualizar_empresa(empresa_id: int, gerente: Optional[str], url: Optional[str], estado: str, error: Optional[str] = None):
    with get_connection() as conn:
        conn.execute("""
            UPDATE empresas
            SET gerente = ?, url_datoscif = ?, estado = ?, error_msg = ?, fecha_scraping = ?
            WHERE id = ?
        """, (gerente, url, estado, error, datetime.now().isoformat(), empresa_id))


def get_stats() -> Dict:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN estado = 'pendiente' THEN 1 ELSE 0 END) as pendientes,
                SUM(CASE WHEN estado = 'ok' THEN 1 ELSE 0 END) as con_gerente,
                SUM(CASE WHEN estado = 'sin_gerente' THEN 1 ELSE 0 END) as sin_gerente,
                SUM(CASE WHEN estado = 'no_encontrada' THEN 1 ELSE 0 END) as no_encontradas,
                SUM(CASE WHEN estado = 'error' THEN 1 ELSE 0 END) as errores
            FROM empresas
        """).fetchone()
        return dict(row)


def exportar_a_excel(output_path: str) -> str:
    """Exporta todas las empresas con gerente a un Excel."""
    import pandas as pd
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl import load_workbook

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT nombre, provincia, gerente, email, telefono, web, direccion,
                   facturacion_eur, url_datoscif, estado, fecha_scraping
            FROM empresas
            ORDER BY nombre
        """).fetchall()

    data = [dict(r) for r in rows]
    df = pd.DataFrame(data)
    df.columns = ["Empresa", "Provincia", "Gerente", "Email", "Teléfono", "Web",
                  "Dirección", "Facturación (€)", "URL DatosCIF", "Estado", "Fecha Scraping"]

    df.to_excel(output_path, index=False)

    # Formatear
    wb = load_workbook(output_path)
    ws = wb.active

    header_fill = PatternFill("solid", fgColor="1B4F72")
    header_font = Font(bold=True, color="FFFFFF", name="Arial", size=11)
    ok_fill = PatternFill("solid", fgColor="D5F5E3")

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for row in ws.iter_rows(min_row=2):
        estado_val = row[9].value
        if estado_val == "ok":
            for cell in row:
                cell.fill = ok_fill

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

    wb.save(output_path)
    return output_path
