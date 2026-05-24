"""
Scraper multi-fuente para obtener el gerente/administrador de empresas españolas.
Fuentes en orden de fallback:
  1. datoscif.es          (principal)
  2. einforma.com         (fallback 1)
  3. empresite.eleconomista.es (fallback 2)
"""

import requests
from bs4 import BeautifulSoup
import time, random, logging, re, unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

CARGOS_PRIORIDAD = [
    "administrador unico","administrador único","gerente","consejero delegado",
    "director general","administrador solidario","administrador mancomunado",
    "presidente","administrador","apoderado",
]

ES_EMPRESA = re.compile(
    r'\b(SL|SA|SAU|SLU|SLL|CB|SC|SCP|AIE|SLNE|PLC|LTD|GMBH|SAS|BV|NV|AG|INC|LLC)\b\.?$',
    re.IGNORECASE
)

def normalizar(t):
    nfkd = unicodedata.normalize("NFKD", t)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()

def nombre_parece_persona(n):
    n = n.strip()
    if not n or len(n) < 4: return False
    if ES_EMPRESA.search(n): return False
    if any(c in normalizar(n) for c in CARGOS_PRIORIDAD): return False
    return True

def get_headers(referer="https://www.google.es/"):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": referer,
        "Upgrade-Insecure-Requests": "1",
    }

def random_delay(a=1.5, b=4.0):
    time.sleep(random.uniform(a, b))

def nombre_a_slug(nombre):
    n = normalizar(nombre)
    n = re.sub(r'[^a-z0-9\s]', '', n)
    return re.sub(r'\s+', '-', n.strip())

def limpiar_variantes(nombre):
    nombre = nombre.strip()
    sin = re.sub(
        r'\s+(S\.A\.U\.?|S\.L\.U\.?|S\.A\.?|S\.L\.?|SAU|SLU|SA|SL|CB|SC)\s*$',
        '', nombre, flags=re.IGNORECASE
    ).strip()
    v = []
    if sin and sin != nombre: v.append(sin)
    v.append(nombre)
    palabras = sin.split()
    if len(palabras) > 3: v.append(" ".join(palabras[:3]))
    return v

def extraer_por_texto(soup):
    """Método genérico: busca cargo en texto plano y toma la línea siguiente."""
    lineas = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
    for i, linea in enumerate(lineas):
        norm = normalizar(linea)
        for cargo in CARGOS_PRIORIDAD:
            if cargo in norm and len(linea) < 60:
                for j in range(1, 4):
                    if i+j < len(lineas) and nombre_parece_persona(lineas[i+j]) and len(lineas[i+j]) > 5:
                        return lineas[i+j]
                break
    return None

def extraer_por_tabla(soup):
    """Método genérico: busca tabla con columnas nombre/cargo."""
    for table in soup.find_all("table"):
        ths = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not ths:
            fila = table.find("tr")
            if fila: ths = [td.get_text(strip=True).lower() for td in fila.find_all(["td","th"])]
        if not any("cargo" in h or "nombre" in h for h in ths): continue
        idx_n = next((i for i,h in enumerate(ths) if "nombre" in h), 0)
        idx_c = next((i for i,h in enumerate(ths) if "cargo"  in h), 1)
        idx_h = next((i for i,h in enumerate(ths) if "hasta"  in h), -1)
        candidatos = []
        for tr in table.find_all("tr"):
            celdas = tr.find_all("td")
            if len(celdas) < 2: continue
            nc = celdas[idx_n].get_text(strip=True) if idx_n < len(celdas) else ""
            cc = celdas[idx_c].get_text(strip=True) if idx_c < len(celdas) else ""
            hc = celdas[idx_h].get_text(strip=True) if (idx_h>=0 and idx_h<len(celdas)) else ""
            if hc and re.search(r'\d{2}/\d{2}/\d{4}', hc): continue
            cn = normalizar(cc)
            for p, co in enumerate(CARGOS_PRIORIDAD):
                if co in cn:
                    if nombre_parece_persona(nc): candidatos.append((p, nc))
                    break
        if candidatos:
            candidatos.sort(key=lambda x: x[0])
            return candidatos[0][1]
    return None


# ══════════════════════════════════════════════════════════
# CLASE BASE
# ══════════════════════════════════════════════════════════
class BaseScraper:
    nombre_fuente = "base"

    def __init__(self):
        self.session = requests.Session()

    def _get(self, url, referer=None, retries=3):
        for attempt in range(retries):
            try:
                r = self.session.get(url, headers=get_headers(referer or url), timeout=20)
                if r.status_code == 200: return r
                if r.status_code in (428, 429):
                    wait = (attempt+1) * random.uniform(12, 22)
                    logger.warning(f"[{self.nombre_fuente}] Rate limit — esperando {wait:.0f}s")
                    time.sleep(wait)
                    self.session = requests.Session()
                    continue
                if r.status_code == 403:
                    wait = (attempt+1) * random.uniform(6, 12)
                    logger.warning(f"[{self.nombre_fuente}] 403 — esperando {wait:.0f}s")
                    time.sleep(wait)
                    self.session = requests.Session()
                    continue
                if r.status_code == 404: return None
                return None
            except requests.exceptions.Timeout:
                time.sleep(random.uniform(3, 6))
            except Exception as e:
                logger.error(f"[{self.nombre_fuente}] Error peticion: {e}")
                time.sleep(random.uniform(2, 5))
        return None

    def get_gerente(self, nombre_empresa):
        raise NotImplementedError


# ══════════════════════════════════════════════════════════
# FUENTE 1: datoscif.es
# ══════════════════════════════════════════════════════════
class DatosCifScraper(BaseScraper):
    nombre_fuente = "datoscif"
    BASE = "https://www.datoscif.es"

    def __init__(self):
        super().__init__()
        try:
            self.session.get(self.BASE, headers=get_headers(), timeout=15)
            random_delay(0.5, 1.5)
        except: pass

    def buscar_empresa(self, nombre):
        # Intento 1: URL directa por slug
        slug = nombre_a_slug(nombre)
        url = f"{self.BASE}/empresa/{slug}"
        r = self._get(url, referer=self.BASE+"/")
        if r and ("Administrador" in r.text or "CARGOS" in r.text):
            return url
        random_delay(1, 2)

        # Intento 2: buscador
        for q in limpiar_variantes(nombre):
            url_b = f"{self.BASE}/busca?q={requests.utils.quote(q)}"
            r = self._get(url_b, referer=self.BASE+"/")
            if not r: random_delay(1, 2); continue
            soup = BeautifulSoup(r.text, "lxml")
            links = soup.find_all("a", href=re.compile(r"/empresa/"))
            if links:
                href = links[0]["href"]
                return href if href.startswith("http") else self.BASE+href
            random_delay(1.5, 3)
        return None

    def extraer_gerente(self, url):
        r = self._get(url, referer=self.BASE+"/")
        if not r: return None
        soup = BeautifulSoup(r.text, "lxml")

        # Tabla CARGOS (estructura principal de datoscif)
        resultado = extraer_por_tabla(soup)
        if resultado: return resultado

        # Texto plano
        resultado = extraer_por_texto(soup)
        if resultado: return resultado

        # BORME: "representante ... es DON NOMBRE APELLIDO"
        patron = re.compile(
            r'representante[^.]{0,120}(?:es|:|,)\s*(?:DON|DOÑA|D\.|Dña\.)\s+([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ\s]{5,50})',
            re.IGNORECASE)
        m = patron.search(r.text)
        if m:
            nombre = m.group(1).strip().rstrip('.,')
            if nombre_parece_persona(nombre): return nombre

        return None

    def get_gerente(self, nombre_empresa):
        url = self.buscar_empresa(nombre_empresa)
        if not url: return None
        random_delay(1.5, 3)
        gerente = self.extraer_gerente(url)
        return {"gerente": gerente, "url": url, "fuente": "datoscif.es"}


# ══════════════════════════════════════════════════════════
# FUENTE 2: einforma.com
# ══════════════════════════════════════════════════════════
class EInformaScraper(BaseScraper):
    nombre_fuente = "einforma"
    BASE = "https://www.einforma.com"

    def buscar_empresa(self, nombre):
        for q in limpiar_variantes(nombre):
            url = f"{self.BASE}/servlet/app/portal/ENTP/screen/SResultados?TIPO_BUSQUEDA=empresa&NOMBRE={requests.utils.quote(q)}&action=buscar"
            r = self._get(url, referer=self.BASE+"/")
            if not r: random_delay(1, 2); continue
            soup = BeautifulSoup(r.text, "lxml")
            links = soup.find_all("a", href=re.compile(r"/datos-de-empresa/|/empresa/"))
            if links:
                href = links[0]["href"]
                return href if href.startswith("http") else self.BASE+href
            # selectores alternativos
            links2 = soup.select("a.empresa-nombre, .resultado-empresa a, h3 a, h2 a")
            if links2:
                href = links2[0]["href"]
                return href if href.startswith("http") else self.BASE+href
            random_delay(1.5, 3)
        return None

    def extraer_gerente(self, url):
        r = self._get(url, referer=self.BASE+"/")
        if not r: return None
        soup = BeautifulSoup(r.text, "lxml")

        # Bloques conocidos de einforma
        for selector in ["#administradores", ".administradores", "#directivos", ".directivos", "#cargos", ".cargos"]:
            bloque = soup.select_one(selector)
            if bloque:
                lineas = [l.strip() for l in bloque.get_text("\n").split("\n") if l.strip()]
                for i, l in enumerate(lineas):
                    if any(cargo in normalizar(l) for cargo in CARGOS_PRIORIDAD) and len(l) < 60:
                        for j in range(1, 3):
                            if i+j < len(lineas) and nombre_parece_persona(lineas[i+j]):
                                return lineas[i+j]

        resultado = extraer_por_tabla(soup)
        if resultado: return resultado
        return extraer_por_texto(soup)

    def get_gerente(self, nombre_empresa):
        random_delay(1, 2)
        url = self.buscar_empresa(nombre_empresa)
        if not url: return None
        random_delay(1.5, 3)
        gerente = self.extraer_gerente(url)
        return {"gerente": gerente, "url": url, "fuente": "einforma.com"}


# ══════════════════════════════════════════════════════════
# FUENTE 3: empresite.eleconomista.es
# ══════════════════════════════════════════════════════════
class EmpresiteScraper(BaseScraper):
    nombre_fuente = "empresite"
    BASE = "https://empresite.eleconomista.es"

    def buscar_empresa(self, nombre):
        for q in limpiar_variantes(nombre):
            url = f"{self.BASE}/busca/?q={requests.utils.quote(q)}"
            r = self._get(url, referer=self.BASE+"/")
            if not r: random_delay(1, 2); continue
            soup = BeautifulSoup(r.text, "lxml")
            links = soup.find_all("a", href=re.compile(r"empresite\.eleconomista\.es/[A-Z]"))
            if links:
                href = links[0]["href"]
                return href if href.startswith("http") else self.BASE+href
            links2 = soup.select("h2.name a, .company-name a, article h2 a, article h3 a")
            if links2:
                href = links2[0]["href"]
                return href if href.startswith("http") else self.BASE+href
            random_delay(1.5, 2.5)
        return None

    def extraer_gerente(self, url):
        r = self._get(url, referer=self.BASE+"/")
        if not r: return None
        soup = BeautifulSoup(r.text, "lxml")

        for selector in [".directivos", "#directivos", ".administradores", ".management", ".equipo"]:
            bloque = soup.select_one(selector)
            if bloque:
                for l in [l.strip() for l in bloque.get_text("\n").split("\n") if l.strip()]:
                    if nombre_parece_persona(l) and len(l) > 5:
                        return l

        resultado = extraer_por_tabla(soup)
        if resultado: return resultado
        return extraer_por_texto(soup)

    def get_gerente(self, nombre_empresa):
        random_delay(1, 2)
        url = self.buscar_empresa(nombre_empresa)
        if not url: return None
        random_delay(1.5, 3)
        gerente = self.extraer_gerente(url)
        return {"gerente": gerente, "url": url, "fuente": "empresite.eleconomista.es"}


# ══════════════════════════════════════════════════════════
# ORQUESTADOR MULTI-FUENTE (punto de entrada principal)
# ══════════════════════════════════════════════════════════
class MultiScraper:
    """
    Prueba las 3 fuentes en orden.
    - Si una devuelve gerente → para y devuelve resultado.
    - Si encuentra ficha pero sin gerente → guarda URL y prueba la siguiente fuente.
    - Si ninguna encuentra nada → no_encontrada.
    """
    def __init__(self):
        self.fuentes = [
            DatosCifScraper(),
            EInformaScraper(),
            EmpresiteScraper(),
        ]

    def get_gerente(self, nombre_empresa: str) -> dict:
        result = {
            "empresa":  nombre_empresa,
            "url":      None,
            "gerente":  None,
            "fuente":   None,
            "estado":   "pendiente",
        }
        try:
            for fuente in self.fuentes:
                logger.info(f"[{fuente.nombre_fuente}] Buscando: {nombre_empresa}")
                random_delay(2, 4)
                res = fuente.get_gerente(nombre_empresa)

                if res is None:
                    logger.info(f"[{fuente.nombre_fuente}] No encontrada, probando siguiente...")
                    continue

                # Guardar primera URL encontrada aunque no tenga gerente
                if res.get("url") and not result["url"]:
                    result["url"]    = res["url"]
                    result["fuente"] = res["fuente"]

                if res.get("gerente"):
                    result["gerente"] = res["gerente"]
                    result["url"]     = res["url"]
                    result["fuente"]  = res["fuente"]
                    result["estado"]  = "ok"
                    logger.info(f"[{fuente.nombre_fuente}] ✅ Gerente encontrado: {res['gerente']}")
                    return result

                logger.info(f"[{fuente.nombre_fuente}] Ficha sin gerente, probando siguiente fuente...")

            result["estado"] = "sin_gerente" if result["url"] else "no_encontrada"

        except Exception as e:
            logger.error(f"Error MultiScraper '{nombre_empresa}': {e}", exc_info=True)
            result["estado"] = "error"
            result["error"]  = str(e)

        return result