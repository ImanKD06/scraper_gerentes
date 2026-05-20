
"""
Scraper para datoscif.es - Extrae el administrador/gerente de empresas.
Basado en la estructura real de la web:
  - Búsqueda: /busca?q=nombre  →  links /empresa/slug
  - Ficha empresa: tabla "CARGOS" con columnas Nombre | Cargo | Desde | Hasta
  - También lee el bloque "Administrador Único / Solidario / Consejero"
"""

import requests
from bs4 import BeautifulSoup
import time
import random
import logging
import re
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

BASE_URL = "https://www.datoscif.es"

# Cargos objetivo en orden de prioridad
CARGOS_PRIORIDAD = [
    "administrador unico",
    "administrador único",
    "gerente",
    "consejero delegado",
    "director general",
    "administrador solidario",
    "administrador mancomunado",
    "presidente",
    "administrador",
    "apoderado",
]

ES_EMPRESA = re.compile(
    r'\b(SL|SA|SAU|SLU|SLL|CB|SC|SCP|AIE|SLNE|PLC|LTD|GMBH|SAS|BV|NV|AG|INC|LLC)\b\.?$',
    re.IGNORECASE
)


def normalizar(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def nombre_parece_persona(nombre: str) -> bool:
    n = nombre.strip()
    if not n or len(n) < 4:
        return False
    if ES_EMPRESA.search(n):
        return False
    norm = normalizar(n)
    if any(cargo in norm for cargo in CARGOS_PRIORIDAD):
        return False
    return True


def get_headers(referer: str = BASE_URL + "/") -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,ca;q=0.7,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }


def random_delay(min_s=1.5, max_s=4.5):
    time.sleep(random.uniform(min_s, max_s))


def nombre_a_slug(nombre: str) -> str:
    """'EUROPEAN ENERGY WORLD SL' -> 'european-energy-world-sl'"""
    n = normalizar(nombre)
    n = re.sub(r'[^a-z0-9\s]', '', n)
    n = re.sub(r'\s+', '-', n.strip())
    return n


class DatosCifScraper:
    def __init__(self):
        self.session = requests.Session()
        self._init_session()

    def _init_session(self):
        try:
            self.session.get(BASE_URL, headers=get_headers(), timeout=15)
            random_delay(0.8, 1.5)
        except Exception as e:
            logger.warning(f"No se pudo inicializar sesion: {e}")

    def _get(self, url: str, retries: int = 3) -> Optional[requests.Response]:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, headers=get_headers(), timeout=20)
                if resp.status_code == 200:
                    return resp
                if resp.status_code in (428, 429):
                    wait = (attempt + 1) * random.uniform(12, 22)
                    logger.warning(f"Rate limit {resp.status_code} — esperando {wait:.0f}s")
                    time.sleep(wait)
                    self.session = requests.Session()
                    self._init_session()
                    continue
                if resp.status_code == 403:
                    wait = (attempt + 1) * random.uniform(6, 12)
                    logger.warning(f"403 en {url} — esperando {wait:.0f}s")
                    time.sleep(wait)
                    self.session = requests.Session()
                    self._init_session()
                    continue
                if resp.status_code == 404:
                    return None
                logger.warning(f"HTTP {resp.status_code} para {url}")
                return None
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout intento {attempt+1}")
                time.sleep(random.uniform(3, 6))
            except requests.exceptions.RequestException as e:
                logger.error(f"Error peticion: {e}")
                time.sleep(random.uniform(2, 5))
        return None

    def _limpiar_nombre(self, nombre: str) -> list:
        nombre = nombre.strip()
        sin_forma = re.sub(
            r'\s+(S\.A\.U\.?|S\.L\.U\.?|S\.A\.?|S\.L\.?|SAU|SLU|SLL|SA|SL|CB|SC)\s*$',
            '', nombre, flags=re.IGNORECASE
        ).strip()
        variantes = []
        if sin_forma and sin_forma != nombre:
            variantes.append(sin_forma)
        variantes.append(nombre)
        palabras = sin_forma.split()
        if len(palabras) > 3:
            variantes.append(" ".join(palabras[:3]))
        return variantes

    def buscar_empresa(self, nombre_empresa: str) -> Optional[str]:
        # Estrategia 1: URL directa por slug
        slug = nombre_a_slug(nombre_empresa)
        url_directa = f"{BASE_URL}/empresa/{slug}"
        resp = self._get(url_directa)
        if resp:
            if "Administrador" in resp.text or "CARGOS" in resp.text or "cif" in resp.text.lower():
                logger.info(f"URL directa OK: {url_directa}")
                return url_directa
        random_delay(1, 2)

        # Estrategia 2: buscador
        variantes = self._limpiar_nombre(nombre_empresa)
        for q in variantes:
            url_busca = f"{BASE_URL}/busca?q={requests.utils.quote(q)}"
            resp = self._get(url_busca)
            if not resp:
                random_delay(1, 2)
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            links = soup.find_all("a", href=re.compile(r"(/empresa/|datoscif\.es/empresa/)"))
            if links:
                href = links[0]["href"]
                url = href if href.startswith("http") else BASE_URL + href
                logger.info(f"Encontrada via busqueda '{q}': {url}")
                return url
            random_delay(1.5, 3)

        return None

    def extraer_gerente(self, url_empresa: str) -> Optional[str]:
        resp = self._get(url_empresa)
        if not resp:
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        texto_pagina = resp.text

        # ── MÉTODO 1: Tabla CARGOS con columnas Nombre / Cargo / Desde / Hasta ──
        for table in soup.find_all("table"):
            encabezados = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not encabezados:
                # Intentar con primera fila como cabecera
                primera = table.find("tr")
                if primera:
                    encabezados = [td.get_text(strip=True).lower() for td in primera.find_all(["td","th"])]

            tiene_cargo  = any("cargo"  in h for h in encabezados)
            tiene_nombre = any("nombre" in h for h in encabezados)
            if not (tiene_cargo or tiene_nombre):
                continue

            idx_nombre = next((i for i, h in enumerate(encabezados) if "nombre" in h), 0)
            idx_cargo  = next((i for i, h in enumerate(encabezados) if "cargo"  in h), 1)
            idx_hasta  = next((i for i, h in enumerate(encabezados) if "hasta"  in h), -1)

            candidatos = []
            for tr in table.find_all("tr"):
                celdas = tr.find_all("td")
                if len(celdas) < 2:
                    continue
                nombre_c = celdas[idx_nombre].get_text(strip=True) if idx_nombre < len(celdas) else ""
                cargo_c  = celdas[idx_cargo].get_text(strip=True)  if idx_cargo  < len(celdas) else ""
                hasta_c  = celdas[idx_hasta].get_text(strip=True)  if (idx_hasta >= 0 and idx_hasta < len(celdas)) else ""

                # Saltar cargos caducados
                if hasta_c and re.search(r'\d{2}/\d{2}/\d{4}', hasta_c):
                    continue

                cargo_norm = normalizar(cargo_c)
                for prio, cargo_obj in enumerate(CARGOS_PRIORIDAD):
                    if cargo_obj in cargo_norm:
                        if nombre_parece_persona(nombre_c):
                            candidatos.append((prio, nombre_c))
                        break

            if candidatos:
                candidatos.sort(key=lambda x: x[0])
                return candidatos[0][1]

        # ── MÉTODO 2: dt/dd o th/td con etiqueta de cargo ──
        for dt in soup.find_all(["dt", "th", "strong", "b", "label", "span"]):
            texto_dt = normalizar(dt.get_text(strip=True))
            if any(cargo in texto_dt for cargo in CARGOS_PRIORIDAD) and len(texto_dt) < 50:
                dd = dt.find_next_sibling(["dd", "td", "span", "div", "p"])
                if dd:
                    nombre = dd.get_text(strip=True)
                    if nombre_parece_persona(nombre):
                        return nombre

        # ── MÉTODO 3: Texto plano línea a línea ──
        lineas = [l.strip() for l in soup.get_text(separator="\n").split("\n") if l.strip()]
        for i, linea in enumerate(lineas):
            norm = normalizar(linea)
            for cargo in CARGOS_PRIORIDAD:
                if cargo in norm and len(linea) < 60:
                    for j in range(1, 4):
                        if i + j < len(lineas):
                            cand = lineas[i + j]
                            if nombre_parece_persona(cand) and len(cand) > 5:
                                return cand
                    break

        # ── MÉTODO 4: BORME — representante físico de sociedad administradora ──
        # Ej: "representante ... es DON CESAR LLEDO SILLA"
        patron = re.compile(
            r'representante[^.]{0,120}(?:es|:|,)\s*(?:DON|DOÑA|D\.|Dña\.)\s+([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ\s]{5,50})',
            re.IGNORECASE
        )
        m = patron.search(texto_pagina)
        if m:
            nombre = m.group(1).strip().rstrip('.,')
            if nombre_parece_persona(nombre):
                return nombre

        return None

    def get_gerente(self, nombre_empresa: str) -> dict:
        result = {"empresa": nombre_empresa, "url": None, "gerente": None, "estado": "pendiente"}
        try:
            random_delay(2, 5)
            url = self.buscar_empresa(nombre_empresa)
            if not url:
                result["estado"] = "no_encontrada"
                return result
            result["url"] = url
            random_delay(1.5, 3.5)
            gerente = self.extraer_gerente(url)
            if gerente:
                result["gerente"] = gerente
                result["estado"] = "ok"
            else:
                result["estado"] = "sin_gerente"
        except Exception as e:
            logger.error(f"Error procesando '{nombre_empresa}': {e}", exc_info=True)
            result["estado"] = "error"
            result["error"] = str(e)
        return result
