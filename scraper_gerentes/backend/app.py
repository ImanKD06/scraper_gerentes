"""
API Flask — Backend scraper de gerentes (multi-fuente)
"""

import os, threading, logging
from datetime import datetime
from flask import Flask, jsonify, request, send_file
from database import (init_db, cargar_empresas_desde_excel, get_empresas,
                      get_stats, actualizar_empresa, get_empresas_pendientes,
                      exportar_a_excel, get_connection)
from scraper import MultiScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="../frontend", static_url_path="")

scraping_state = {
    "running": False, "stop_requested": False,
    "current": 0, "total": 0,
    "current_empresa": "", "log": [],
}
scraping_thread = None

DATA_DIR     = os.path.join(os.path.dirname(__file__), "..", "data")
EXCEL_INPUT  = os.path.join(DATA_DIR, "empresas_input.xlsx")
EXCEL_OUTPUT = os.path.join(DATA_DIR, "empresas_con_gerentes.xlsx")
os.makedirs(DATA_DIR, exist_ok=True)


def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    scraping_state["log"].append(entry)
    if len(scraping_state["log"]) > 500:
        scraping_state["log"] = scraping_state["log"][-500:]
    logger.info(msg)


def run_scraping():
    scraper = MultiScraper()
    empresas = get_empresas_pendientes()
    scraping_state["total"]   = len(empresas)
    scraping_state["current"] = 0
    add_log(f"Iniciando scraping de {len(empresas)} empresas — 3 fuentes activas (datoscif → einforma → empresite)")

    for empresa in empresas:
        if scraping_state["stop_requested"]:
            add_log("Scraping detenido por el usuario.")
            break

        scraping_state["current_empresa"] = empresa["nombre"]
        add_log(f"Procesando: {empresa['nombre']}")

        result = scraper.get_gerente(empresa["nombre"])

        actualizar_empresa(
            empresa_id=empresa["id"],
            gerente=result.get("gerente"),
            url=result.get("url"),
            fuente=result.get("fuente"),
            estado=result["estado"],
            error=result.get("error"),
        )

        icono = {"ok":"✅","sin_gerente":"⚠️","no_encontrada":"❌","error":"💥"}.get(result["estado"],"·")
        fuente_txt = f" [{result.get('fuente','')}]" if result.get("fuente") else ""
        if result["estado"] == "ok":
            add_log(f"  → {icono} Gerente: {result['gerente']}{fuente_txt}")
        elif result["estado"] == "error":
            add_log(f"  → {icono} Error: {result.get('error','')}")
        else:
            add_log(f"  → {icono} {result['estado']}{fuente_txt}")

        scraping_state["current"] += 1

    scraping_state["running"] = False
    scraping_state["stop_requested"] = False
    scraping_state["current_empresa"] = ""
    add_log("✅ Scraping completado.")


# ─── RUTAS ────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file("../frontend/index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No se recibió archivo"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".xlsx"):
        return jsonify({"error": "Solo se admiten archivos .xlsx"}), 400
    f.save(EXCEL_INPUT)
    init_db()
    n = cargar_empresas_desde_excel(EXCEL_INPUT)
    return jsonify({"ok": True, "nuevas": n, "stats": get_stats()})


@app.route("/api/stats")
def stats():
    try:
        return jsonify(get_stats())
    except Exception:
        return jsonify({"total":0,"pendientes":0,"con_gerente":0,
                        "sin_gerente":0,"no_encontradas":0,"errores":0})


@app.route("/api/empresas")
def empresas():
    estado   = request.args.get("estado")
    page     = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    data = get_empresas(estado=estado, limit=per_page, offset=(page-1)*per_page)
    return jsonify(data)


@app.route("/api/empresas/search")
def search_empresas():
    q = request.args.get("q", "").strip()
    if not q: return jsonify([])
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM empresas WHERE nombre LIKE ? ORDER BY nombre LIMIT 20",
            (f"%{q}%",)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/scraping/start", methods=["POST"])
def start_scraping():
    global scraping_thread
    if scraping_state["running"]:
        return jsonify({"error": "Ya hay un scraping en curso"}), 400
    scraping_state["running"] = True
    scraping_state["stop_requested"] = False
    scraping_state["log"] = []
    scraping_thread = threading.Thread(target=run_scraping, daemon=True)
    scraping_thread.start()
    return jsonify({"ok": True})


@app.route("/api/scraping/stop", methods=["POST"])
def stop_scraping():
    scraping_state["stop_requested"] = True
    return jsonify({"ok": True})


@app.route("/api/scraping/status")
def scraping_status():
    pct = round(scraping_state["current"] / scraping_state["total"] * 100, 1) if scraping_state["total"] else 0
    return jsonify({
        "running":         scraping_state["running"],
        "current":         scraping_state["current"],
        "total":           scraping_state["total"],
        "percent":         pct,
        "current_empresa": scraping_state["current_empresa"],
        "log":             scraping_state["log"][-50:],
    })


@app.route("/api/scraping/single/<int:empresa_id>", methods=["POST"])
def scrape_single(empresa_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM empresas WHERE id=?", (empresa_id,)).fetchone()
    if not row:
        return jsonify({"error": "Empresa no encontrada"}), 404
    scraper = MultiScraper()
    result  = scraper.get_gerente(dict(row)["nombre"])
    actualizar_empresa(
        empresa_id=empresa_id,
        gerente=result.get("gerente"),
        url=result.get("url"),
        fuente=result.get("fuente"),
        estado=result["estado"],
        error=result.get("error"),
    )
    return jsonify(result)


@app.route("/api/reset/pendientes", methods=["POST"])
def reset_pendientes():
    if scraping_state["running"]:
        return jsonify({"error": "No se puede resetear con scraping en curso"}), 400
    with get_connection() as conn:
        conn.execute("UPDATE empresas SET estado='pendiente', gerente=NULL, url_datoscif=NULL, fuente=NULL, error_msg=NULL, fecha_scraping=NULL")
    add_log("🔄 Todas las empresas reseteadas a pendiente.")
    return jsonify({"ok": True, "stats": get_stats()})


@app.route("/api/reset/todo", methods=["POST"])
def reset_todo():
    if scraping_state["running"]:
        return jsonify({"error": "No se puede eliminar con scraping en curso"}), 400
    with get_connection() as conn:
        conn.execute("DELETE FROM empresas")
    for f in [EXCEL_INPUT, EXCEL_OUTPUT]:
        if os.path.exists(f): os.remove(f)
    scraping_state["log"] = []
    scraping_state["current"] = scraping_state["total"] = 0
    add_log("🗑️ Datos eliminados. Carga un nuevo Excel para comenzar.")
    return jsonify({"ok": True})


@app.route("/api/export")
def export():
    try:
        exportar_a_excel(EXCEL_OUTPUT)
        return send_file(
            os.path.abspath(EXCEL_OUTPUT),
            as_attachment=True,
            download_name="empresas_con_gerentes.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_db()
    print("\n🚀 Servidor en http://localhost:5000")
    print("📡 Fuentes activas: datoscif.es → einforma.com → empresite.eleconomista.es\n")
    app.run(debug=True, port=5000, threaded=True)
