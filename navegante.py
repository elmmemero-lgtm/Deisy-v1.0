import ipaddress
import os
import re
import socket
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from ddgs import DDGS
from dotenv import load_dotenv

load_dotenv()

_CACHE = {}
_BLOQUEO_CACHE = __import__("threading").RLock()
MAX_CACHE_ENTRADAS = 64


def _entero_env(nombre, defecto, minimo, maximo):
    """Lee .env sin impedir el arranque si alguien escribe un valor inválido."""
    try:
        valor = int(os.getenv(nombre, str(defecto)))
    except ValueError:
        print(f"⚠️ {nombre} inválido; uso {defecto}.")
        valor = defecto
    return max(minimo, min(valor, maximo))


MAX_CANDIDATOS = _entero_env("DEISY_WEB_MAX_CANDIDATOS", 6, 3, 10)
TTL_GENERAL = _entero_env("DEISY_WEB_TTL_GENERAL", 900, 60, 86400)
TTL_ACTUALIDAD = _entero_env("DEISY_WEB_TTL_ACTUALIDAD", 120, 30, 3600)
USER_AGENT = "Deisy/1.0 (enlaces auditados; contacto local)"


# Solo son pistas de categoría; nunca convierten un dominio en una verdad.
DOMINIOS_OFICIALES = (
    "112.es", "aemet.es", "dgt.es", "boe.es", "incibe.es",
    "sanidad.gob.es", "interior.gob.es", "who.int", "un.org",
    "europa.eu",
)
DOMINIOS_MEDIOS = (
    "rtve.es", "reuters.com", "apnews.com", "elpais.com",
    "bbc.com", "efe.com",
)


def _normalizar(texto):
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )
    return re.sub(r"\s+", " ", texto).strip()


def limpiar_consulta(peticion):
    """Quita envoltorios de conversación sin usar IA para reescribir la búsqueda."""
    texto = re.sub(r"\s+", " ", str(peticion or "")).strip()
    if not texto:
        return ""

    texto = re.sub(
        r"^\s*(?:(?:oye\s+)?deisy\s*,?\s*)?"
        r"(?:(?:puedes|podrias|me puedes|quiero que)\s+)?"
        r"(?:buscar|busca|investiga|investigar|verifica|verificar|"
        r"contrasta|comprobar|comprueba|encuentra|mira)\s+",
        "",
        texto,
        flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"\s+(?:en|con)\s+(?:el\s+)?(?:navegador|brave|opera)\b",
        "",
        texto,
        flags=re.IGNORECASE,
    )
    texto = texto.strip(" ¿?¡!.,:;")
    return texto


def enlace_busqueda_brave(consulta):
    """Enlace clicable; no abre Brave ni controla ningún navegador."""
    return "https://search.brave.com/search?q=" + quote_plus(consulta)


def es_peticion_busqueda(texto):
    bajo = _normalizar(texto)
    tiene_verbo = any(
        palabra in bajo
        for palabra in ("busca", "buscar", "encuentra", "mira en internet")
    )
    tiene_web = any(
        palabra in bajo
        for palabra in ("internet", "web", "navegador", "brave", "opera")
    )
    return tiene_verbo and tiene_web


def es_peticion_investigacion(texto):
    """Detecta cuándo hacen falta fuentes, no solo un enlace de búsqueda."""
    bajo = _normalizar(texto)
    marcas = (
        "investiga", "verifica", "contrasta", "fuentes", "es verdad",
        "es cierto", "puedes confirmar", "puedes comprobar",
        "quien lo confirmo", "quién lo confirmó", "que causo", "qué causó",
        "fue provocado", "fueron provocados",
    )
    return any(marca in bajo for marca in marcas)


def _es_alto_riesgo(consulta):
    """Actualidad, salud y seguridad necesitan una auditoría más estricta."""
    bajo = _normalizar(consulta)
    marcas = (
        "emergencia", "incendio", "evacuacion", "evacuación", "medico",
        "médico", "medicina", "medicamento", "salud", "ley", "legal",
        "dinero", "inversion", "inversión", "noticia", "noticias",
        "actualidad", "ultima hora", "última hora",
    )
    return any(marca in bajo for marca in marcas)


def respuesta_urgencia_inmediata(texto):
    """No espera búsquedas ni modelos cuando el texto parece una urgencia real."""
    bajo = _normalizar(texto)
    patrones = (
        r"\b(?:no puedo respirar|no respiro|dolor de pecho|"
        r"sangrado abundante|me estoy ahogando)\b",
        r"\b(?:llama|llamen|necesito)\s+(?:al\s+)?"
        r"(?:112|ambulancia|policia|bomberos)\b",
        r"\b(?:fuego|humo|incendio)\b.*"
        r"\b(?:aqui|aqui cerca|casa|edificio|ahora)\b",
    )
    if not any(re.search(patron, bajo) for patron in patrones):
        return ""

    return (
        "Si hay peligro inmediato, llama al 112 ahora. No esperes a que yo "
        "busque información. Fuente oficial: https://www.112.es/"
    )


def respuesta_enlace_busqueda(peticion):
    consulta = limpiar_consulta(peticion)
    if not consulta:
        return "¿Qué quieres que busque?"
    return (
        f"Te paso los resultados de Brave para «{consulta}»:\n"
        f"{enlace_busqueda_brave(consulta)}\n\n"
        "No he abierto Brave ni ningún navegador."
    )


def _dominio(url):
    return urlparse(str(url or "")).hostname.lower().removeprefix("www.") if url else ""


def _tipo_dominio(dominio):
    if not dominio:
        return "web"
    if (
        dominio.endswith(".gob.es")
        or dominio.endswith(".europa.eu")
        or any(dominio == base or dominio.endswith("." + base) for base in DOMINIOS_OFICIALES)
    ):
        return "institucional"
    if any(dominio == base or dominio.endswith("." + base) for base in DOMINIOS_MEDIOS):
        return "medio"
    return "web"


def _url_publica_https(url):
    """Filtro defensivo básico antes de descargar un enlace externo.

    No sustituye un cortafuegos de salida ni una lista blanca corporativa: el
    DNS podría cambiar entre esta comprobación y la conexión. Reduce riesgos
    obvios (localhost, IP privadas, credenciales en URL y puertos raros).
    """
    try:
        parsed = urlparse(str(url or ""))
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443):
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False

        direcciones = socket.getaddrinfo(host, None)
        if not direcciones:
            return False
        for direccion in direcciones:
            ip = ipaddress.ip_address(direccion[4][0])
            if not ip.is_global:
                return False
        return True
    except (OSError, ValueError):
        return False


def _extraer_texto_html(html):
    """Extrae solo título y meta-descripción; no interpreta la página como hecho."""
    def limpiar(valor):
        return re.sub(r"\s+", " ", unescape(valor or "")).strip()

    titulo = ""
    descripcion = ""
    coincidencia = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if coincidencia:
        titulo = limpiar(re.sub(r"(?is)<[^>]+>", " ", coincidencia.group(1)))

    meta = re.search(
        r"""(?is)<meta[^>]+(?:name|property)\s*=\s*["'](?:description|og:description)["'][^>]+content\s*=\s*["'](.*?)["']""",
        html,
    )
    if not meta:
        # Algunas páginas ponen content antes de name; es un segundo intento.
        meta = re.search(
            r"""(?is)<meta[^>]+content\s*=\s*["'](.*?)["'][^>]+(?:name|property)\s*=\s*["'](?:description|og:description)["'][^>]*>""",
            html,
        )
    if meta:
        descripcion = limpiar(re.sub(r"(?is)<[^>]+>", " ", meta.group(1)))

    return titulo[:240], descripcion[:600]


def _sondear_fuente(resultado):
    """Comprueba técnicamente que un enlace parezca público y accesible ahora.

    Sigue como máximo dos redirecciones y vuelve a validar la URL en cada una.
    Descarga como máximo 96 KB. Es una protección de profundidad, no una
    garantía absoluta contra todas las formas de redirección o DNS malicioso.
    No decide si el contenido es verdadero ni relevante para la consulta.
    """
    original = str(resultado.get("href") or resultado.get("url") or "")
    actual = original

    for _ in range(3):
        if not _url_publica_https(actual):
            return None

        respuesta = None
        try:
            respuesta = requests.get(
                actual,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=(3.05, 8),
                stream=True,
                allow_redirects=False,
            )
            if 300 <= respuesta.status_code < 400:
                destino = respuesta.headers.get("Location", "")
                respuesta.close()
                if not destino:
                    return None
                actual = urljoin(actual, destino)
                continue

            if respuesta.status_code != 200:
                respuesta.close()
                return None

            tipo = respuesta.headers.get("Content-Type", "").lower()
            if "html" not in tipo:
                respuesta.close()
                return None

            trozos = []
            total = 0
            for trozo in respuesta.iter_content(chunk_size=16384):
                if not trozo:
                    continue
                trozos.append(trozo)
                total += len(trozo)
                if total >= 98304:
                    break
            codificacion = respuesta.encoding or "utf-8"
            respuesta.close()

            html = b"".join(trozos).decode(codificacion, errors="replace")
            titulo_html, descripcion_html = _extraer_texto_html(html)
            dominio = _dominio(actual)
            return {
                "titulo": titulo_html or str(resultado.get("title") or dominio),
                "descripcion": descripcion_html,
                "url": actual,
                "dominio": dominio,
                "tipo": _tipo_dominio(dominio),
                # age viene del buscador, no de la página: se etiqueta como tal.
                "fecha_indicada_por_buscador": str(resultado.get("age") or "").strip(),
            }
        except requests.RequestException:
            if respuesta is not None:
                respuesta.close()
            return None

    return None


def _buscar_ddgs(consulta):
    """Obtiene candidatos. DDGS no se considera fuente final por sí solo."""
    try:
        return list(DDGS().text(consulta, max_results=MAX_CANDIDATOS)) or []
    except Exception as error:
        print(f"⚠️ DDGS no respondió: {type(error).__name__}")
        return []


def _cache_leer(clave, ttl):
    with _BLOQUEO_CACHE:
        entrada = _CACHE.get(clave)
    if not entrada:
        return None
    guardado, informe = entrada
    return informe if time.monotonic() - guardado <= ttl else None


def _cache_guardar(clave, informe):
    with _BLOQUEO_CACHE:
        # La caché no debe crecer sin límite si la API queda encendida días.
        while len(_CACHE) >= MAX_CACHE_ENTRADAS and clave not in _CACHE:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[clave] = (time.monotonic(), informe)


def _estado_fuentes(fuentes, alto_riesgo):
    """Describe enlaces disponibles, sin confundirlos con una verificación."""
    dominios = {fuente["dominio"] for fuente in fuentes if fuente["dominio"]}
    institucionales = [
        fuente for fuente in fuentes if fuente["tipo"] == "institucional"
    ]

    if institucionales:
        return "dominio_institucional_accesible"
    if len(dominios) >= 2:
        return "dos_dominios_accesibles"
    if alto_riesgo:
        return "enlaces_insuficientes_alto_riesgo"
    return "un_enlace_accesible"


def investigar(peticion):
    """Busca y audita enlaces. Nunca crea un resumen factual con IA."""
    consulta = limpiar_consulta(peticion)
    if not consulta:
        return {
            "exito": False,
            "mensaje": "¿Qué asunto concreto quieres que investigue?",
            "fuentes": [],
        }

    alto_riesgo = _es_alto_riesgo(consulta)
    clave = _normalizar(consulta)
    en_cache = _cache_leer(
        clave,
        TTL_ACTUALIDAD if alto_riesgo else TTL_GENERAL,
    )
    if en_cache is not None:
        return dict(en_cache, desde_cache=True)

    candidatos = _buscar_ddgs(consulta)
    if not candidatos:
        return {
            "exito": False,
            "consulta": consulta,
            "mensaje": (
                "No pude obtener fuentes ahora mismo. No voy a afirmar "
                "nada sin enlaces verificables."
            ),
            "fuentes": [],
        }

    fuentes = []
    with ThreadPoolExecutor(max_workers=min(4, len(candidatos))) as ejecutor:
        trabajos = {
            ejecutor.submit(_sondear_fuente, candidato): candidato
            for candidato in candidatos
        }
        for trabajo in as_completed(trabajos):
            # Una web mal formada no debe tumbar toda la investigación.
            try:
                fuente = trabajo.result()
            except Exception as error:
                print(f"⚠️ Fuente descartada durante auditoría: {type(error).__name__}")
                fuente = None
            if fuente:
                fuentes.append(fuente)

    prioridad = {"institucional": 0, "medio": 1, "web": 2}
    fuentes.sort(key=lambda fuente: (prioridad[fuente["tipo"]], fuente["dominio"]))
    fuentes = fuentes[:5]

    if not fuentes:
        return {
            "exito": False,
            "consulta": consulta,
            "mensaje": (
                "Encontré resultados de búsqueda, pero ninguno superó las "
                "comprobaciones técnicas de enlace público HTTPS accesible."
            ),
            "fuentes": [],
        }

    informe = {
        "exito": True,
        "consulta": consulta,
        "alto_riesgo": alto_riesgo,
        "estado": _estado_fuentes(fuentes, alto_riesgo),
        "consultado_en": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "fuentes": fuentes,
        "desde_cache": False,
    }
    _cache_guardar(clave, informe)
    return informe


def formatear_investigacion(informe):
    """Devuelve evidencia visible; no inventa una conclusión sobre el tema."""
    if not informe.get("exito"):
        return str(informe.get("mensaje") or "No encontré enlaces que pudiera comprobar técnicamente.")

    estado = informe.get("estado")
    if estado == "dominio_institucional_accesible":
        cabecera = (
            "He encontrado al menos un enlace accesible de un dominio "
            "institucional. Aún no he comprobado que esa página confirme "
            "la afirmación: ábrela y léela antes de decidir."
        )
    elif estado == "dos_dominios_accesibles":
        cabecera = (
            "He localizado enlaces accesibles de al menos dos dominios "
            "distintos. Compáralos; aún no prueban por sí solos una causa "
            "o un detalle concreto."
        )
    elif estado == "enlaces_insuficientes_alto_riesgo":
        cabecera = (
            "No puedo confirmar esta cuestión de alto riesgo con enlaces "
            "suficientes. Te los dejo para revisar, pero no sacaré una conclusión."
        )
    else:
        cabecera = (
            "Solo encontré un enlace accesible. Te lo paso como punto de "
            "partida, no como un hecho confirmado."
        )

    lineas = [
        f"Consulta: «{informe.get('consulta', '')}».",
        cabecera,
        f"Consultado: {informe.get('consultado_en', '')}.",
    ]
    if informe.get("desde_cache"):
        lineas.append("Uso un paquete de fuentes reciente guardado en memoria.")

    for indice, fuente in enumerate(informe.get("fuentes", []), start=1):
        etiqueta = fuente.get("tipo", "web").upper()
        linea = (
            f"[{indice}] [{etiqueta}] {fuente.get('titulo', fuente.get('dominio', 'Fuente'))}\n"
            f"Dominio: {fuente.get('dominio', '')}\n"
            f"{fuente.get('url', '')}"
        )
        fecha = fuente.get("fecha_indicada_por_buscador")
        if fecha:
            linea += f"\nFecha indicada por el buscador: {fecha}"
        lineas.append(linea)

    lineas.append(
        "No he abierto Brave ni ningún navegador. No he comprobado que estas "
        "páginas respalden una afirmación concreta; abre las fuentes antes de "
        "atribuir una causa, una cifra o una responsabilidad."
    )
    return "\n\n".join(lineas)