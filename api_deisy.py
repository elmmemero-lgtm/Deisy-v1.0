"""Servidor central de Deisy 4.1.

Arquitectura: esta API decide y registra; la Omen ejecuta un conjunto pequeño
de acciones físicas; Groq solo clasifica o conversa detrás de filtros locales.
Todos los endpoints se registran antes de arrancar Waitress.
"""

from __future__ import annotations

import base64
import hmac
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any
from contexto_deisy import estado_actual as leer_estado_operativo
from personalidad import construir_contexto_identidad, identidad_activa
from reflexion import reflexion_activa, proponer_reflexion, formatear_propuesta

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from waitress import serve

from iniciativa import (
    iniciativa_activa,
    decidir_iniciativa,
    contexto_iniciativa_para_modelo,
    registrar_turno_conversacional,
    ofertas_basicas_activas,
)

from estado_cognitivo import (
    registrar_entrada,
    registrar_resultado,
    resumen_seguro_para_modelo,
)
from base_datos import (
    encolar_iphone,
    guardar_historial,
    listar_dispositivos,
    bautizar_dispositivo,
    recuperar_memoria_para_charla,
    registrar_solicitud_pendiente,
    responder_memoria_literal,
    resumen_solicitudes_pendientes,
)
from capacidades_deisy import (
    detectar_capacidad_pendiente,
    parece_peticion_de_mejora,
    texto_capacidades,
)
from cerebro import (
    VisionSinDescripcion,
    analizar_imagen_b64,
    dar_opinion_codigo,
    enviar_apagado_confirmado_a_omen,
    enviar_orden_a_omen,
    generar_respuesta_charla,
    procesar_instruccion,
    recordar_vision,
)
from contexto_deisy import (
    ENFOQUES_VALIDOS,
    actualizar_contexto,
    contexto_para_modelo,
    respuesta_estado_contexto,
    estado_actual,
    sugerencia_sueno_si_toca,
    normalizar_contexto,
)
from contexto_ordenes import (
    olvidar_referencia_repetible,
    registrar_orden_confirmada,
    resolver_alias_apertura,
    resolver_contexto_corto,
)
from politica_entrada import (
    clasificar_entrada,
    comando_autorizado,
    contexto_chat_reciente,
    registrar_turno_chat,
    familia_de_comando,
    cancelar_oferta_basica,
    registrar_oferta_basica,
    resolver_oferta_basica,
)
from filtros_intencion import es_continuacion_de_charla, filtrar_comandos_router, pide_hora
from intenciones_deterministas import es_accion_implementada, resolver_intencion_local
from ubicaciones import identificar_lugar

# Módulos existentes del proyecto. Se mantienen: esta versión no borra radar,
# recordatorios, notificaciones ni el navegador verificado.
import navegante
import notificador
import onda_red
import proactivo
from notificador import notificar


load_dotenv()
app = Flask(__name__)

MAX_MENSAJE = 4000
MAX_IMAGEN_B64 = 5_600_000
MAX_IMAGEN_BYTES = 4 * 1024 * 1024
_ULTIMA_UBICACION: dict[str, dict[str, Any]] = {}
_ESTADOS_IPHONE: dict[str, dict[str, Any]] = {}
_CONTEXTO_MOVIL_BLOQUEO = threading.RLock()
_EVENTOS_MOVIL_RECIENTES: dict[tuple[str, ...], float] = {}
RADAR_DEDUPE_SEGUNDOS = 60
EVENTO_IPHONE_DEDUPE_SEGUNDOS = 30
RADAR_FRESCO_SEGUNDOS = 15 * 60
RADAR_ANTIGUO_SEGUNDOS = 24 * 60 * 60


def _env_verdadero(nombre: str, por_defecto=False) -> bool:
    valor = os.getenv(nombre)
    if valor is None:
        return por_defecto
    return valor.strip().casefold() in {"1", "true", "si", "sí", "yes", "on"}


def _datos_json() -> dict[str, Any] | None:
    """Lee JSON sin provocar una página HTML de Flask si el cliente se equivoca."""
    datos = request.get_json(silent=True)
    return datos if isinstance(datos, dict) else None


def _token_valido() -> bool:
    esperado = os.getenv("DEISY_TOKEN", "").strip()
    recibido = request.headers.get("X-Deisy-Token", "")
    # En la versión estable un token ausente es un error de configuración, no un
    # permiso abierto. Así los iPhone/Omen nunca envían datos a una API expuesta.
    return bool(esperado and recibido and hmac.compare_digest(recibido, esperado))


def _requiere_token():
    if not os.getenv("DEISY_TOKEN", "").strip():
        return jsonify({"error": "DEISY_TOKEN no está configurado en la HP."}), 503
    if not _token_valido():
        return jsonify({"error": "No autorizado"}), 401
    return None


@app.route("/salud", methods=["GET"])
def salud():
    """Comprobación de arranque sin revelar secretos ni datos personales."""
    return jsonify({
        "ok": True,
        "servicio": "api_deisy",
        "token_configurado": bool(os.getenv("DEISY_TOKEN", "").strip()),
        "subconsciente_activo": _env_verdadero("SUBCONSCIENTE_ACTIVO"),
    })


def _registrar_reflexion_no_bloqueante(pregunta, respuesta, modulo, exito):
    """Muestra una propuesta sin modificar la respuesta ni el sistema.

    La reflexión es deliberadamente posterior a la decisión y a la ejecución.
    Si este módulo falla, la conversación normal continúa.
    """
    if not reflexion_activa():
        return

    try:
        propuesta = proponer_reflexion(
            pregunta,
            respuesta=respuesta,
            modulo=modulo,
            exito=exito,
        )
        if propuesta:
            print(formatear_propuesta(propuesta))
    except Exception as error:
        # No imprimimos la petición: podría contener datos privados.
        print(f"⚠️ Reflexión omitida sin afectar a Deisy: {type(error).__name__}")




def _devolver_local(pregunta: str, respuesta: str, modulo: str, exito=True):
    """Devuelve y registra una respuesta que no necesitó Groq."""
    guardar_historial(pregunta, respuesta, modulo_usado=modulo, exito=exito)
    _registrar_reflexion_no_bloqueante(pregunta, respuesta, modulo, exito)
    return jsonify({"respuesta": respuesta})


def _hora_local() -> str:
    return f"Son las {datetime.now().strftime('%H:%M')}."


def _normalizar(texto: object) -> str:
    texto = str(texto or "").casefold()
    tabla = str.maketrans("áéíóúü", "aeiouu")
    return re.sub(r"\s+", " ", texto.translate(tabla)).strip()


def _clave_dispositivo(valor: object) -> str:
        """Clave interna estable; no se muestra directamente al usuario."""
        return _normalizar(valor)[:80] or "dispositivo"


def _edad_segundos(fecha: datetime) -> int:
    return max(0, int((datetime.now() - fecha).total_seconds()))


def _texto_antiguedad(fecha: datetime) -> str:
    segundos = _edad_segundos(fecha)
    if segundos < 60:
        return "hace un momento"
    minutos = segundos // 60
    if minutos < 60:
        return f"hace {minutos} min"
    return f"hace {minutos // 60} h"


def _evento_repetido(clave: tuple[str, ...], ventana: int) -> bool:
    """Devuelve True solo para el mismo evento dentro de una ventana corta."""
    ahora = time.monotonic()
    with _CONTEXTO_MOVIL_BLOQUEO:
        expirados = [
            clave_antigua
            for clave_antigua, cuando in _EVENTOS_MOVIL_RECIENTES.items()
            if ahora - cuando > max(ventana * 2, 120)
        ]
        for clave_antigua in expirados:
            _EVENTOS_MOVIL_RECIENTES.pop(clave_antigua, None)

        anterior = _EVENTOS_MOVIL_RECIENTES.get(clave)
        _EVENTOS_MOVIL_RECIENTES[clave] = ahora
        return anterior is not None and ahora - anterior < ventana


def _respuesta_radar() -> tuple[str, bool]:
    """Muestra enlaces de radar solo en una consulta explícita."""
    with _CONTEXTO_MOVIL_BLOQUEO:
        ubicaciones = [dict(info) for info in _ULTIMA_UBICACION.values()]

    if not ubicaciones:
        return (
            "Aún no he recibido ninguna ubicación de tus dispositivos. "
            "El iPhone tiene que enviarla con su Atajo.",
            True,
        )

    partes = []
    for info in sorted(ubicaciones, key=lambda dato: dato["cuando"], reverse=True):
        edad = _edad_segundos(info["cuando"])
        cuando = _texto_antiguedad(info["cuando"])
        if edad > RADAR_ANTIGUO_SEGUNDOS:
            partes.append(
                f"{info['dispositivo']} (última señal {cuando}; "
                "ya no la trato como actual): "
                f"{info['link']}"
            )
        else:
            partes.append(
                f"{info['dispositivo']} (última señal recibida {cuando}): "
                f"{info['link']}"
            )
    return "Radar: " + " | ".join(partes), True


def _es_consulta_radar(texto_normalizado: str) -> bool:
    menciona_dispositivo = any(palabra in texto_normalizado for palabra in (
        "iphone", "telefono", "movil", "celular", "dispositivo",
    ))
    pide_ubicacion = any(palabra in texto_normalizado for palabra in (
        "ubicacion", "localiza", "donde esta", "mandame la ubicacion",
    ))
    return "radar" in texto_normalizado or (
        menciona_dispositivo and pide_ubicacion
    )


def _contexto_ubicacion_chat(
    dispositivo: object,
    ubicacion_bruta: object,
) -> str:
    """Indica solo que hay una señal, nunca sus coordenadas ni enlace.

    El texto devuelto solo activa la regla de privacidad del prompt de
    charla. El detalle de Maps se reserva exclusivamente para radar.
    """
    recibida = str(ubicacion_bruta or "").strip()
    normal = _normalizar(recibida)
    if normal and normal not in {
        "ubicacion no indicada", "sin ubicacion", "desconocida",
    }:
        return "Ubicación declarada por este canal."

    clave = _clave_dispositivo(dispositivo)
    with _CONTEXTO_MOVIL_BLOQUEO:
        info = _ULTIMA_UBICACION.get(clave)
        info = dict(info) if info else None

    if info and _edad_segundos(info["cuando"]) <= RADAR_FRESCO_SEGUNDOS:
        return "Señal de ubicación reciente del mismo dispositivo."
    return ""


def _ciudad_contextual_segura(valor: object) -> str:
    """Acepta ciudad declarada; rechaza direcciones y coordenadas."""
    ciudad = str(valor or "").strip(" []{}<>")
    if not ciudad or len(ciudad) > 80:
        return ""
    normal = _normalizar(ciudad)
    if normal in {"ubicacion no indicada", "sin ubicacion", "desconocida"}:
        return ""
    if re.search(r"\d", ciudad) or any(marca in normal for marca in (
        "calle", "avenida", "plaza", "portal", "piso", "bloque",
        "numero", "nº",
    )):
        return ""
    if not re.fullmatch(
        r"[A-Za-zÀ-ÿ .,'-]+(?:,\s*[A-Za-zÀ-ÿ .,'-]+){0,2}",
        ciudad,
    ):
        return ""
    return ciudad


def _es_consulta_memoria(texto: object) -> bool:
    """Detecta preguntas de memoria, no simples correcciones de una orden.

    En particular, ``te dije que abras X`` no debe sacar a Deisy del flujo de
    acciones y convertirlo en una búsqueda de historial.
    """
    bajo = _normalizar(texto)
    return any(marca in bajo for marca in (
        "recuerd", "memoria", "que te dije", "me dijiste", "hablamos",
        "conversacion anterior", "dijimos", "algo que te conte",
    ))


def _es_consulta_capacidades(texto: object) -> bool:
    bajo = _normalizar(texto)
    return any(marca in bajo for marca in (
        "que puedes hacer", "que sabes hacer", "que puedes decirme",
        "en que me puedes ayudar", "en que puedes ayudarme", "que haces",
        "tus funciones", "funciones tienes", "capacidades tienes",
    ))


def _es_consulta_pendientes(texto: object) -> bool:
    bajo = _normalizar(texto)
    return "funciones pendientes" in bajo or "que tienes pendiente" in bajo


def _peticion_clima(texto: object) -> bool:
        bajo = _normalizar(texto)
        return any(marca in bajo for marca in (
            "clima", "temperatura", "que tiempo hace", "como esta el clima",
            "el clima como esta", "como esta el tiempo", "el tiempo como esta",
            "dime el clima", "dime la temperatura", "clima actual", "llueve",
            "hace frio", "hace calor", "llover", "pronostico", "prevision",
            "tiempo hara", "clima manana",
        ))


def _es_cadena_acciones_clara(texto_normalizado: str) -> bool:
    """Detecta dos acciones separadas para no ejecutar solo la primera.

    La cadena se entrega completa al router y a su filtro estricto. No basta
    con que aparezca la palabra «y»: cada lado debe contener un verbo de acción
    inequívoco, con lo que una charla normal no se convierte en una cadena.
    """
    partes = re.split(r"\b(?:y|luego|despues)\b", texto_normalizado)
    if len(partes) < 2:
        return False
    verbos = re.compile(
        r"\b(abre|abrir|inicia|iniciar|lanza|ejecuta|pon|reproduce|"
        r"sube|subir|aumenta|baja|bajar|reduce|apaga|apagar|"
        r"cancela|cancelar|dime|busca|escanea|analiza|recuerda)\b"
    )
    return sum(bool(verbos.search(parte)) for parte in partes) >= 2


# ---------------------------------------------------------------------------
# Fuentes locales verificables: clima y noticias
# ---------------------------------------------------------------------------
_WMO = {
    0: "cielo despejado", 1: "principalmente despejado", 2: "parcialmente nuboso",
    3: "cubierto", 45: "niebla", 48: "niebla con escarcha", 51: "llovizna débil",
    53: "llovizna moderada", 55: "llovizna intensa", 61: "lluvia débil",
    63: "lluvia moderada", 65: "lluvia intensa", 71: "nieve débil", 73: "nieve moderada",
    75: "nieve intensa", 80: "chubascos débiles", 81: "chubascos moderados",
    82: "chubascos fuertes", 95: "tormenta", 96: "tormenta con granizo débil",
    99: "tormenta con granizo fuerte",
}
_CLIMA_CACHE: dict[str, tuple[float, Any]] = {}
_CLIMA_BLOQUEO = threading.RLock()


def _cache_clima(clave: str, productor):
    with _CLIMA_BLOQUEO:
        entrada = _CLIMA_CACHE.get(clave)
        if entrada and time.monotonic() - entrada[0] < 600:
            return entrada[1]
    valor = productor()
    with _CLIMA_BLOQUEO:
        _CLIMA_CACHE[clave] = (time.monotonic(), valor)
    return valor


def _ciudad_y_momento(
        peticion: str,
        ciudad_contextual="",
    ) -> tuple[str, str]:
        bajo = _normalizar(peticion)
        momento = (
            "manana" if "manana" in bajo else
            "hoy" if "hoy" in bajo else
            "ahora" if "ahora" in bajo else
            "general"
        )
        posible = re.sub(
            r"\b(que|como|dime|puedes|por favor|clima|tiempo|temperatura|"
            r"hace|hara|manana|hoy|ahora|actual|esta|tarde|noche|en|de|para|"
            r"el|la|va a|llover|prevision|pronostico)\b",
            " ",
            bajo,
        ).strip(" ,?.!")

        ciudad_explicita = _ciudad_contextual_segura(posible)
        if ciudad_explicita:
            return momento, ciudad_explicita

        ciudad_declarada = _ciudad_contextual_segura(ciudad_contextual)
        if ciudad_declarada:
            return momento, ciudad_declarada

        return momento, os.getenv(
            "DEISY_CIUDAD_POR_DEFECTO",
            "Parla, Madrid, España",
        ).strip()


def _numero(valor: object) -> str:
    numero = float(valor)
    return str(int(round(numero))) if abs(numero - round(numero)) < 0.05 else f"{numero:.1f}"


def _clima_local(peticion: str, ciudad_contextual="") -> tuple[str, bool]:
    momento, ciudad = _ciudad_y_momento(peticion, ciudad_contextual)
    if not ciudad:
        return "Para el clima usa solo una ciudad, por ejemplo «Madrid»; no enviaré una dirección completa a un servicio externo.", False
    try:
        def geocodificar():
            respuesta = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": ciudad.split(",", 1)[0], "count": 1, "language": "es", "countryCode": "ES"},
                timeout=(3.05, 10),
            )
            respuesta.raise_for_status()
            resultados = respuesta.json().get("results") or []
            if not resultados:
                return None
            r = resultados[0]
            return {"lat": float(r["latitude"]), "lon": float(r["longitude"]), "nombre": str(r.get("name") or ciudad), "provincia": str(r.get("admin1") or "")}

        lugar = _cache_clima("geo:" + ciudad.casefold(), geocodificar)
        if not lugar:
            return f"No encontré una ciudad española verificable llamada «{ciudad}».", False
        etiqueta = f"{lugar['nombre']}, {lugar['provincia']}".strip(", ")

        def prevision():
            respuesta = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lugar["lat"], "longitude": lugar["lon"],
                    "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": 2, "timezone": "auto", "temperature_unit": "celsius", "wind_speed_unit": "kmh",
                }, timeout=(3.05, 10),
            )
            respuesta.raise_for_status()
            return respuesta.json()

        datos = _cache_clima(f"meteo:{lugar['lat']:.4f},{lugar['lon']:.4f}", prevision)
        actual = datos["current"]
        diario = datos["daily"]
        descripcion = _WMO.get(int(actual["weather_code"]), "condición meteorológica no especificada")
        ahora = (
            f"Según Open-Meteo, a las {str(actual['time']).replace('T', ' ')} en {etiqueta}: "
            f"{_numero(actual['temperature_2m'])} °C, sensación de {_numero(actual['apparent_temperature'])} °C, "
            f"{descripcion} y viento de {_numero(actual['wind_speed_10m'])} km/h."
        )
        indice = 1 if momento == "manana" else 0
        dia = "mañana" if indice == 1 else "hoy"
        prevision_texto = (
            f"Previsión para {dia}: mínima de {_numero(diario['temperature_2m_min'][indice])} °C, "
            f"máxima de {_numero(diario['temperature_2m_max'][indice])} °C, "
            f"{_WMO.get(int(diario['weather_code'][indice]), 'condición no especificada')} "
            f"y probabilidad máxima de precipitación de {_numero(diario['precipitation_probability_max'][indice])}%."
        )
        return (prevision_texto if momento in {"hoy", "manana"} else ahora if momento == "ahora" else ahora + " " + prevision_texto), True
    except (requests.RequestException, KeyError, TypeError, ValueError, IndexError) as error:
        print(f"⚠️ Open-Meteo no disponible: {type(error).__name__}")
        return "No tengo un dato meteorológico verificable ahora mismo. No voy a inventar una temperatura.", False


def _noticias_local(tema: str) -> tuple[str, bool]:
    try:
        url = (
            f"https://news.google.com/rss/search?q={requests.utils.quote(tema)}&hl=es&gl=ES&ceid=ES:es"
            if tema else "https://news.google.com/rss?hl=es&gl=ES&ceid=ES:es"
        )
        respuesta = requests.get(url, timeout=(3.05, 10), headers={"User-Agent": "Deisy/4.1"})
        respuesta.raise_for_status()
        raiz = ET.fromstring(respuesta.content)
        titulares = [str(item.findtext("title") or "").strip() for item in raiz.iter("item")][:3]
        titulares = [t for t in titulares if t]
        return ("Titulares: " + " | ".join(titulares) + ".", True) if titulares else ("No encontré titulares verificables ahora mismo.", False)
    except Exception as error:
        print(f"⚠️ Noticias no disponibles: {type(error).__name__}")
        return "No pude leer titulares verificables ahora mismo.", False


def _respuesta_funcion_pendiente(solicitud: str, canal: str) -> str:
    capacidad = detectar_capacidad_pendiente(solicitud)
    if not _es_solicitud_pendiente_explicita(solicitud):
        return "No he identificado una mejora concreta. Puedes decir, por ejemplo: «quiero que puedas leer notificaciones»."
    guardado = registrar_solicitud_pendiente(solicitud, canal=canal)
    if not guardado.get("ok"):
        return f"Esa función no está disponible y {guardado.get('motivo', 'no pude guardarla')}."
    if capacidad:
        texto = f"{capacidad['nombre'].capitalize()} todavía no está programada."
        if capacidad.get("alternativa"):
            texto += " " + capacidad["alternativa"]
    else:
        texto = "Esa función todavía no está programada."
    veces = int(guardado.get("veces", 1))
    texto += " La he guardado como mejora pendiente." if veces == 1 else f" Sigue guardada como mejora pendiente; ya la has pedido {veces} veces."
    return texto + " No diré que está hecha hasta que haya código y una prueba real."


def _es_solicitud_pendiente_explicita(texto: str) -> bool:
    """Acepta deseos claros, no una mera mención de una función pendiente."""
    if parece_peticion_de_mejora(texto):
        return True
    capacidad = detectar_capacidad_pendiente(texto)
    if not capacidad:
        return False
    bajo = _normalizar(texto)
    return bool(re.search(r"\b(lee|leer|controla|controlar|enciende|apaga|consulta|consultar|maneja|manejar|activa|activar)\b", bajo))


def _respuesta_lista_pendientes() -> str:
    filas = resumen_solicitudes_pendientes()
    if not filas:
        return "No tengo funciones pendientes guardadas todavía."
    return "Mejoras pendientes guardadas: " + "; ".join(
        f"{fila['solicitud_original']} ({fila['veces_solicitada']} petición/es)" for fila in filas
    ) + "."


def _resolver_internet_local(pregunta: str):
    """Internet ocurre solo por petición explícita y a través de navegante.py."""
    aviso = navegante.respuesta_urgencia_inmediata(pregunta)
    if aviso:
        return aviso, True, "urgencia_local"
    if navegante.es_peticion_investigacion(pregunta):
        informe = navegante.investigar(pregunta)
        return navegante.formatear_investigacion(informe), bool(informe.get("exito")), "investigacion_web"
    if navegante.es_peticion_busqueda(pregunta):
        return navegante.respuesta_enlace_busqueda(pregunta), True, "enlace_busqueda"
    return None


def _respuesta_enfoque_iphone() -> str:
        with _CONTEXTO_MOVIL_BLOQUEO:
            estados = [dict(dato) for dato in _ESTADOS_IPHONE.values()]

        if estados:
            estados.sort(key=lambda dato: dato["cuando"], reverse=True)
            partes = [
                f"{dato['dispositivo']}: {dato['enfoque']} "
                f"(último evento {_texto_antiguedad(dato['cuando'])})"
                for dato in estados[:3]
            ]
            return "Últimos enfoques recibidos: " + "; ".join(partes) + "."

        estado = estado_actual()
        enfoque = estado.get("enfoque", {})
        cuando = enfoque.get("cuando")
        if cuando:
            return (
                f"El último enfoque persistido fue "
                f"{enfoque.get('valor', 'Personal')} desde "
                f"{enfoque.get('origen', 'un iPhone')} "
                f"({_texto_antiguedad(cuando)})."
            )
        return "Aún no he recibido un enfoque válido de ningún iPhone."



def _ejecutar_comando(
    comando: str,
    pregunta: str,
    ubicacion: str,
    dispositivo: str,
    sesion: str,
    permitir_apagado_confirmado=False,
    contexto_identidad="",
    contexto_iniciativa="",
) -> tuple[str | None, bool, str]:
    """Ejecuta un comando ya filtrado; devuelve texto, éxito y módulo."""
    bajo = comando.casefold()
    if bajo.startswith("hora:"):
        return _hora_local(), True, "hora"
    if bajo.startswith("clima:"):
        texto, ok = _clima_local(comando.split(":", 1)[1].strip() or pregunta)
        return texto, ok, "clima"
    if bajo.startswith("noticias:"):
        texto, ok = _noticias_local(comando.split(":", 1)[1].strip())
        return texto, ok, "noticias"
    if bajo.startswith("navegar:"):
        consulta = comando.split(":", 1)[1].strip() or pregunta
        if navegante.es_peticion_investigacion(consulta):
            informe = navegante.investigar(consulta)
            return navegante.formatear_investigacion(informe), bool(informe.get("exito")), "investigacion_web"
        return navegante.respuesta_enlace_busqueda(consulta), True, "enlace_busqueda"
    if bajo.startswith("red:"):
        sub = _normalizar(comando.split(":", 1)[1])
        try:
            if sub.startswith("nombrar") and " como " in sub:
                clave, alias = sub[len("nombrar"):].split(" como ", 1)
                return (f"Guardado como {alias.strip()}." if bautizar_dispositivo(clave.strip(), alias.strip()) else f"No encontré nada que coincida con {clave.strip()}."), True, "onda_red"
            if sub in {"lista", "listar", "dispositivos", "conocidos"}:
                dispositivos = listar_dispositivos()
                if not dispositivos:
                    return "Aún no tengo dispositivos guardados.", True, "onda_red"
                return "En tu red conozco: " + "; ".join(f"{d['alias'] or d['fabricante'] or 'desconocido'} en {d['ip']}" for d in dispositivos) + ".", True, "onda_red"
            info = onda_red.censar()
            nuevos = info.get("nuevos") or []
            total = info.get("total", 0)
            if nuevos:
                listado = ", ".join(f"{n.get('fabricante', 'desconocido')} en {n.get('ip', '?')}" for n in nuevos[:5])
                return f"{total} dispositivos en tu red. {len(nuevos)} nuevos: {listado}.", True, "onda_red"
            return f"{total} dispositivos en tu red y todos me suenan. Sin intrusos.", True, "onda_red"
        except Exception as error:
            return f"No pude escanear la red: {type(error).__name__}.", False, "onda_red"
    if bajo.startswith("iphone:"):
        cuerpo = comando.split(":", 1)[1].strip()
        accion, _, valor = cuerpo.partition(" ")
        if not encolar_iphone(accion.casefold(), valor):
            return "No pude dejar la orden en la cola del iPhone.", False, "iphone"
        return f"He dejado una orden para tu iPhone: {cuerpo}. Se ejecutará cuando su Atajo la recoja; aún no tengo confirmación de ejecución.", True, "iphone"
    if bajo.startswith("preguntar:"):
        return comando.split(":", 1)[1].strip(), True, "pregunta"
    if bajo.startswith("charla:"):
        ok, texto = generar_respuesta_charla(
            comando.split(":", 1)[1].strip() or pregunta,
            ubicacion=ubicacion,
            dispositivo=dispositivo,
            contexto_operativo=contexto_para_modelo(),
            memoria_recuperada=recuperar_memoria_para_charla(pregunta),
            sesion=sesion,
            contexto_identidad=contexto_identidad,
            contexto_iniciativa=contexto_iniciativa,
        )
        return texto, ok, "charla"
    if bajo.startswith("estado_contexto:"):
        return respuesta_estado_contexto(), True, "estado_contexto"
    if bajo.startswith("estado_enfoque:"):
        return _respuesta_enfoque_iphone(), True, "estado_iphone"
    if bajo.startswith("radar:"):
        texto, ok = _respuesta_radar()
        return texto, ok, "radar"    
    if bajo == "sistema: apagar_pc":
        if not permitir_apagado_confirmado:
            return "Bloqueé el apagado porque no hubo una confirmación local válida.", False, "seguridad"
        respuesta = enviar_apagado_confirmado_a_omen()
        ok = bool(respuesta.get("exito"))
        motivo = str(respuesta.get("motivo") or "sin detalle")
        if not ok:
            return f"La Omen no confirmó el apagado: {motivo}", False, "apagado_confirmado"
        return f"Apagado confirmado por la Omen: {motivo}", True, "apagado_confirmado"
    if bajo.startswith(("buscar:", "musica_app:", "combo_musica:", "tecla:", "sistema:", "ver:")):
        respuesta = enviar_orden_a_omen(comando)
        ok = bool(respuesta.get("exito"))
        motivo = str(respuesta.get("motivo") or "sin detalle")
        if not ok:
            olvidar_referencia_repetible(comando, sesion)
            return f"La Omen no confirmó la orden: {motivo}", False, "accion_omen"
        registrar_orden_confirmada(comando, sesion)
        return f"Orden confirmada por la Omen: {motivo}", True, "accion_omen"
    if bajo.startswith("pendiente:"):
        solicitud = comando.split(":", 1)[1].strip()
        if _es_solicitud_pendiente_explicita(solicitud):
            return _respuesta_funcion_pendiente(solicitud, dispositivo), True, "funcion_pendiente"
        return "No ejecutaré una capacidad que no esté confirmada.", False, "router_invalido"
    return None, True, ""


def _es_musica(pregunta_limpia: str) -> bool:
    return any(x in pregunta_limpia for x in ("musica", "cancion", "playlist", "spotify", "emisora"))


def _es_charla_final(comandos: object) -> bool:
    """Solo una salida charla puede recibir iniciativa.

    Las acciones, consultas locales, confirmaciones y cadenas quedan fuera.
    Este helper no decide intenciones; solo respeta la decisión ya tomada.
    """
    if not isinstance(comandos, (list, tuple)) or len(comandos) != 1:
        return False
    return str(comandos[0]).strip().casefold().startswith("charla:")


def _anexar_oferta_basica(texto_respuesta, exito, sesion, decision_iniciativa):
    """Añade una oferta local visible solo después de una charla exitosa.

    La oferta se registra a la vez que se muestra. Por eso un "sí" nunca puede
    referirse a una propuesta que Groq imaginó pero que el usuario no vio.
    """
    texto = str(texto_respuesta or "")
    if not exito or not ofertas_basicas_activas():
        return texto

    codigo = str(getattr(decision_iniciativa, "oferta_local", "") or "")
    if not codigo:
        return texto

    oferta_visible = registrar_oferta_basica(sesion, codigo)
    if not oferta_visible:
        return texto
    return f"{texto.rstrip()}\n\n{oferta_visible}"


@app.route("/preguntar", methods=["POST"])
def preguntar():
    bloqueo = _requiere_token()
    if bloqueo:
        return bloqueo
    datos = _datos_json()
    if datos is None:
        return jsonify({"error": "El cuerpo debe ser JSON."}), 400
    pregunta = str(datos.get("mensaje", "")).strip()
    if not pregunta:
        return jsonify({"respuesta": "No he escuchado nada, Eddie."})
    if len(pregunta) > MAX_MENSAJE:
        return jsonify({"error": "El mensaje es demasiado largo."}), 413
    
    dispositivo = str(datos.get("dispositivo", "cliente"))[:80]
    sesion = str(
        datos.get("sesion") or f"{dispositivo}:principal"
    ).strip()[:120]
    ubicacion_bruta = str(datos.get("ubicacion", "")).strip()
    ubicacion = _contexto_ubicacion_chat(dispositivo, ubicacion_bruta)
    ciudad_contextual = _ciudad_contextual_segura(ubicacion_bruta)
    pregunta_limpia = _normalizar(pregunta)
    cadena_acciones = _es_cadena_acciones_clara(pregunta_limpia)
    print(f"📩 Mensaje desde {dispositivo}: {pregunta[:250]} | contexto de ubicación: {ubicacion}")
    # Telegram, Omen y cada iPhone no deben compartir una aclaración ni el
    # hilo breve de conversación.

    # 1. Determinista: apagado/volumen. Es la única ruta que puede crear apagar_pc.
    decision = resolver_intencion_local(pregunta, sesion)
    # Una decisión determinista (incluido apagado) nunca comparte sesión con
    # una oferta básica anterior.
    if decision is not None:
        cancelar_oferta_basica(sesion)
    if decision and decision["tipo"] == "respuesta":
        return _devolver_local(pregunta, decision["texto"], decision["modulo"])
    comando_determinista = decision["comando"] if decision and decision["tipo"] == "comando" else ""

    # 2. Solo repeticiones ya confirmadas por contexto_ordenes.py.
    # Alias aún NO: deben pasar primero por la puerta nueva.
    if cadena_acciones:
        comando_corto, aclaracion_corta = "", ""
    else:
        comando_corto, aclaracion_corta = resolver_contexto_corto(pregunta, sesion)

    # Una orden corta válida o su aclaración cambian el tema y nunca deben
    # dejar viva una autorización de Spotify anterior.
    if comando_corto or aclaracion_corta:
        cancelar_oferta_basica(sesion)

    comando_directo = comando_determinista or comando_corto
    if aclaracion_corta:
        return _devolver_local(pregunta, aclaracion_corta, "aclaracion_contexto")

    # Una frase nueva consume/cancela una oferta antes de cualquier retorno
    # local. Un "sí" posterior nunca reactiva un permiso viejo.
    plan_basico = None
    if ofertas_basicas_activas() and not comando_determinista and not comando_corto:
        plan_basico = resolver_oferta_basica(sesion, pregunta)
        if plan_basico and plan_basico["estado"] in {"cancelada", "aclarar"}:
            return _devolver_local(
                pregunta,
                plan_basico["respuesta"],
                "oferta_conversacional",
            )

    # 3. Hechos y capacidades: no pasan por el modelo.
    if not cadena_acciones and ("recuerdame" in pregunta_limpia or "recordatorio" in pregunta_limpia):
        try:
            from recordatorios import gestionar_recordatorio
            return _devolver_local(pregunta, gestionar_recordatorio(pregunta), "recordatorio")
        except Exception as error:
            return _devolver_local(pregunta, f"No pude gestionar el recordatorio: {type(error).__name__}.", "recordatorio", False)
    if not cadena_acciones and _es_consulta_capacidades(pregunta):
        return _devolver_local(pregunta, texto_capacidades(), "capacidades")
    if not cadena_acciones and _es_consulta_pendientes(pregunta):
        return _devolver_local(pregunta, _respuesta_lista_pendientes(), "funcion_pendiente")
    if not cadena_acciones and _es_consulta_memoria(pregunta):
        return _devolver_local(pregunta, responder_memoria_literal(pregunta), "memoria_literal")
    if not cadena_acciones and not comando_directo and not es_accion_implementada(pregunta) and _es_solicitud_pendiente_explicita(pregunta):
        return _devolver_local(pregunta, _respuesta_funcion_pendiente(pregunta, dispositivo), "funcion_pendiente")
    if not cadena_acciones and pide_hora(pregunta):
        return _devolver_local(pregunta, _hora_local(), "hora")
    if not cadena_acciones and _peticion_clima(pregunta):
        texto, ok = _clima_local(pregunta, ciudad_contextual)
        return _devolver_local(pregunta, texto, "clima", ok)
    if not cadena_acciones and _es_consulta_radar(pregunta_limpia):
        texto, ok = _respuesta_radar()
        return _devolver_local(pregunta, texto, "radar", ok)
    
    internet = None if cadena_acciones else _resolver_internet_local(pregunta)
    if internet:
        texto, ok, modulo = internet
        return _devolver_local(pregunta, texto, modulo, ok)

    # 4. Puerta: una charla no llega a alias, bypasses ni router de acciones.
    entrada = clasificar_entrada(pregunta, sesion)

    comandos_plan_basico = []
    if plan_basico and plan_basico["estado"] == "aceptada":
        # Los comandos vienen del catálogo cerrado de politica_entrada.py.
        # Aun así, pasarán por familia_de_comando/comando_autorizado y por el
        # ejecutor normal más abajo.
        comandos_plan_basico = list(plan_basico["acciones"])
        entrada["clase"] = "accion_explicita"
        entrada["familias"].update(plan_basico["familias"])

    # Estos candidatos proceden exclusivamente de módulos locales estrictos:
    # intención determinista y una repetición reversible ya confirmada.
    for comando_confiable in (comando_determinista, comando_corto):
        if comando_confiable:
            familia = familia_de_comando(comando_confiable)
            if familia == "desconocida":
                return _devolver_local(
                    pregunta,
                    "No pude verificar de forma segura la orden pendiente.",
                    "aclaracion",
                    False,
                )
            entrada["clase"] = "accion_explicita"
            entrada["familias"].add(familia)

    if entrada["clase"] == "ambigua" and not comando_directo:
        # Usa tu helper 4.1 si existe (por ejemplo _devolver_local) para no
        # duplicar JSON, historial, notificación y log.
        texto_respuesta = entrada["respuesta"]
        print(f"🧭 Aclaración antes de actuar: {texto_respuesta}")
        guardar_historial(pregunta, texto_respuesta, "aclaracion", True)
        registrar_turno_chat(sesion, pregunta, texto_respuesta)
        return jsonify({"respuesta": texto_respuesta})

    if entrada["clase"] == "charla":
        # Conserva ANTES de este bloque solo consultas locales no mutables que
        # ya tengas: hora, clima, capacidades, radar y estado de contexto.
        # Ninguna acción física debe aparecer antes de la puerta.
        contexto_sesion = contexto_chat_reciente(sesion)
        estado_cognitivo = {}
        contexto_identidad = ""
        contexto_iniciativa = ""
        decision_iniciativa = None

        if identidad_activa() or iniciativa_activa():
            try:
                estado_cognitivo = registrar_entrada(sesion, pregunta)
            except Exception as error:
                print(f"⚠️ Estado cognitivo omitido: {type(error).__name__}")

        if identidad_activa():
            try:
                estilo = construir_contexto_identidad(
                    leer_estado_operativo(),
                    estado_cognitivo,
                )
                continuidad = resumen_seguro_para_modelo(estado_cognitivo)
                contexto_identidad = "\n\n".join(
                    fragmento for fragmento in (estilo, continuidad) if fragmento
                )
            except Exception as error:
                print(f"⚠️ Identidad cognitiva omitida: {type(error).__name__}")

        if iniciativa_activa():
            try:
                decision_iniciativa = decidir_iniciativa(
                    sesion,
                    pregunta,
                    estado_cognitivo=estado_cognitivo,
                    estado_operativo=leer_estado_operativo(),
                )
                contexto_iniciativa = contexto_iniciativa_para_modelo(
                    decision_iniciativa
                )
            except Exception as error:
                print(f"⚠️ Iniciativa omitida: {type(error).__name__}")

        exito, texto_respuesta = generar_respuesta_charla(
            pregunta,
            ubicacion,
            contexto_web="",
            dispositivo=dispositivo,
            contexto_operativo=contexto_para_modelo(),
            memoria_recuperada=recuperar_memoria_para_charla(pregunta),
            contexto_sesion=contexto_sesion,
            contexto_identidad=contexto_identidad,
            contexto_iniciativa=contexto_iniciativa,
            sesion=sesion,
        )

        if identidad_activa() and exito:
            try:
                registrar_resultado(sesion, "charla")
            except Exception as error:
                print(f"⚠️ Estado cognitivo no actualizado: {type(error).__name__}")

        if iniciativa_activa() and exito:
            try:
                registrar_turno_conversacional(
                    sesion,
                    estado_cognitivo,
                    decision_iniciativa,
                    exito,
                )
            except Exception as error:
                print(f"⚠️ Hilo conversacional no actualizado: {type(error).__name__}")

                
        texto_respuesta = _anexar_oferta_basica(
            texto_respuesta,
            exito,
            sesion,
            decision_iniciativa,
        )
        registrar_turno_chat(sesion, pregunta, texto_respuesta)
        guardar_historial(pregunta, texto_respuesta, "charla", exito)
        return jsonify({"respuesta": texto_respuesta})

    if entrada["clase"] == "ambigua":
        print(f"🧭 Aclaración antes de actuar: {entrada['respuesta']}")
        return _devolver_local(pregunta, entrada["respuesta"], "aclaracion")

    # Una charla deja comandos vacío: al final irá directamente a
    # generar_respuesta_charla(), sin pasar por el router de órdenes.
    es_charla = entrada["clase"] == "charla"

    conectores = (" y ", " tambien ", " además ", " ademas ", " luego ")
    es_cadena = cadena_acciones or (
        len(pregunta_limpia.split()) > 6 and any(c in pregunta_limpia for c in conectores)
    )
    menciona_iphone = any(x in pregunta_limpia for x in (
        "iphone", "telefono", "movil", "celular"
    ))
    menciona_omen = any(x in pregunta_limpia for x in (
        "omen", "ordenador", "computadora", "torre", " pc", "el pc"
    ))

    comandos: list[str] = []

    if not es_charla:
        # Los alias se consultan después de que la puerta autorizó actuar.
        comando_alias, detalle_alias = "", ""
        if not cadena_acciones:
            comando_alias, detalle_alias = resolver_alias_apertura(pregunta)
        if detalle_alias:
            print(f"🎮 Alias seguro: {detalle_alias}")

        # Una respuesta concreta a "¿Omen o iPhone?" trae este comando.
        comando_directo = (
            comando_determinista
            or comando_corto
            or entrada.get("comando_preparado", "")
            or comando_alias
        )

        if comandos_plan_basico:
            comandos = comandos_plan_basico
        elif comando_directo:
            comandos = [comando_directo]

        elif not es_cadena and _es_musica(pregunta_limpia) and menciona_iphone:
            comandos = ["iphone: musica"]

        elif not es_cadena and _es_musica(pregunta_limpia) and menciona_omen:
            spotify_url = os.getenv("SPOTIFY_URL", "").strip()
            comandos = [f"combo_musica: {spotify_url}"] if spotify_url else ["musica_app: spotify"]

        elif not es_cadena and _es_musica(pregunta_limpia) and any(
            x in pregunta_limpia for x in ("pon", "reproduce", "abre", "play")
        ):
            comandos = ["preguntar: ¿Dónde quieres la música: en el iPhone o en la Omen?"]

        elif not es_cadena and any(frase in pregunta_limpia for frase in (
            "donde estoy", "en que modo estoy", "cual es mi contexto",
            "estoy en casa o trabajando",
        )):
            comandos = ["estado_contexto:"]

        elif not es_cadena and any(frase in pregunta_limpia for frase in (
            "que enfoque tengo", "estado del iphone 11", "estado de mi iphone 11",
        )):
            comandos = ["estado_enfoque:"]

        elif not es_cadena and (
            "radar" in pregunta_limpia
            or "localiza" in pregunta_limpia
            or (
                "donde esta" in pregunta_limpia
                and any(x in pregunta_limpia for x in (
                    "iphone", "telefono", "movil", "dispositivo",
                ))
            )
        ):
            comandos = ["radar:"]

        elif not es_cadena and (
            ("red" in pregunta_limpia or "wifi" in pregunta_limpia)
            and any(x in pregunta_limpia for x in (
                "escane", "onda", "conectad", "intruso", "quien esta", "quien hay",
            ))
        ):
            comandos = ["red: escanear"]

        else:
            propuesta = procesar_instruccion(
        pregunta,
        familias_permitidas=entrada["familias"],
    )
            print(f"🧭 Router propuso: {propuesta!r}")
            comandos = filtrar_comandos_router(
        propuesta,
        pregunta,
        familias_permitidas=entrada["familias"],
    )

        # Protege aliases y bypasses locales igual que los comandos de Groq.
        autorizados: list[str] = []
        for comando in comandos:
            familia = familia_de_comando(comando)
            if familia == "charla" or comando_autorizado(comando, entrada):
                autorizados.append(comando)
            else:
                print(f"🚫 Bypass/router bloqueado por puerta: {comando!r}")
        comandos = autorizados

        if not comandos:
            return _devolver_local(
                pregunta,
                "Entendí que querías una acción, pero no identifiqué una orden "
                "suficientemente clara. ¿Puedes reformularla?",
                "aclaracion",
            )
        # IMPORTANTE: aquí intenciones, políticas, alias, bypasses y filtros ya
    # terminaron. Si `comandos` es una acción, es_charla_final será False.
    es_charla_final = _es_charla_final(comandos)
    estado_cognitivo = {}
    contexto_identidad = ""
    contexto_iniciativa = ""
    decision_iniciativa = None

    # Reutilizamos el estado cognitivo existente únicamente para una charla
    # final. No llames registrar_entrada() otra vez más abajo.
    if es_charla_final and (identidad_activa() or iniciativa_activa()):
        try:
            estado_cognitivo = registrar_entrada(sesion, pregunta)
        except Exception as error:
            print(f"⚠️ Estado cognitivo omitido: {type(error).__name__}")

    if es_charla_final and identidad_activa():
        try:
            contexto_identidad = construir_contexto_identidad(
                leer_estado_operativo(),
                estado_cognitivo,
            )
            continuidad = resumen_seguro_para_modelo(estado_cognitivo)
            contexto_identidad = "\n\n".join(
                fragmento for fragmento in (contexto_identidad, continuidad) if fragmento
            )
        except Exception as error:
            print(f"⚠️ Identidad cognitiva omitida: {type(error).__name__}")

    if es_charla_final and iniciativa_activa():
        try:
            decision_iniciativa = decidir_iniciativa(
                sesion,
                pregunta,
                estado_cognitivo=estado_cognitivo,
                estado_operativo=leer_estado_operativo(),
            )
            contexto_iniciativa = contexto_iniciativa_para_modelo(decision_iniciativa)
        except Exception as error:
            # La iniciativa es opcional: su fallo no puede impedir una charla.
            print(f"⚠️ Iniciativa omitida: {type(error).__name__}")
        
    respuestas: list[str] = []
    exito_global = True
    modulo = "charla"

    for comando in comandos:
        texto, ok, modulo_actual = _ejecutar_comando(
            comando,
            pregunta,
            ubicacion,
            dispositivo,
            sesion,
            
            permitir_apagado_confirmado=(
                comando == "sistema: apagar_pc"
                and comando_determinista == "sistema: apagar_pc"
            ),

        contexto_identidad=contexto_identidad,
        contexto_iniciativa=contexto_iniciativa,
        )

        if texto:
            respuestas.append(texto)
            exito_global = exito_global and ok
            modulo = modulo_actual if len(comandos) == 1 else "cadena"

    if respuestas:
        texto_respuesta, exito = " ".join(respuestas), exito_global
    else:
    # Solo se ejecuta cuando la seguridad ya dejó la petición en charla final.
    # No manda texto crudo del usuario: estado_cognitivo devuelve etiquetas.
            exito, texto_respuesta = generar_respuesta_charla(
                pregunta,
                ubicacion=ubicacion,
                dispositivo=dispositivo,
                contexto_operativo=contexto_para_modelo(),
                memoria_recuperada=recuperar_memoria_para_charla(pregunta),
                sesion=sesion,
                contexto_identidad=contexto_identidad,
                contexto_iniciativa=contexto_iniciativa,
            )
            modulo = "charla"
            if identidad_activa() and exito:
                try:
                    registrar_resultado(sesion, modulo)
                except Exception as error:
                    print(f"⚠️ Estado cognitivo no actualizado: {type(error).__name__}")

    aviso = sugerencia_sueno_si_toca()
    if aviso and exito:
        texto_respuesta += "\n\n" + aviso
    print(f"🤖 Deisy responde: {texto_respuesta}")

    # Las acciones, consultas locales y errores no actualizan hilos.
    if es_charla_final and iniciativa_activa() and exito:
        try:
            registrar_turno_conversacional(
                sesion,
                estado_cognitivo,
                decision_iniciativa,
                exito,
            )
        except Exception as error:
            print(f"⚠️ Hilo conversacional no actualizado: {type(error).__name__}")
    
    guardar_historial(
        pregunta,
        texto_respuesta,
        modulo_usado=modulo,
        exito=exito,
    )
    _registrar_reflexion_no_bloqueante(
        pregunta,
        texto_respuesta,
        modulo,
        exito,
    )
    return jsonify({"respuesta": texto_respuesta})

@app.route("/iphone_cola", methods=["GET", "POST"])
def iphone_cola():
    bloqueo = _requiere_token()
    if bloqueo:
        return bloqueo
    from base_datos import siguiente_orden_iphone
    orden = siguiente_orden_iphone()
    return jsonify(orden or {"accion": "nada", "valor": ""})


@app.route("/ubicacion_espia", methods=["POST"])
def atrapar_ubicacion():
    bloqueo = _requiere_token()
    if bloqueo:
        return bloqueo
    datos = _datos_json()
    if datos is None:
        return jsonify({"error": "El cuerpo debe ser JSON."}), 400

    def campo(nombre: str, defecto=""):
        # Compatibilidad con el error habitual de Atajos: claves con ':' final.
        return datos.get(nombre) or datos.get(nombre + ":") or defecto

    dispositivo = str(campo("dispositivo", "iPhone_Desconocido"))[:80]
    try:
        lat = float(str(campo("latitud")).replace(",", "."))
        lon = float(str(campo("longitud")).replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"error": "Coordenadas inválidas"}), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify({"error": "Coordenadas fuera de rango"}), 400

    enlace = f"https://www.google.com/maps?q={lat},{lon}"
    ahora = datetime.now()
    clave = _clave_dispositivo(dispositivo)

    with _CONTEXTO_MOVIL_BLOQUEO:
        anterior = _ULTIMA_UBICACION.get(clave)
        mismo_punto = bool(
            anterior
            and abs(float(anterior["lat"]) - lat) < 0.0001
            and abs(float(anterior["lon"]) - lon) < 0.0001
        )
        _ULTIMA_UBICACION[clave] = {
            "dispositivo": dispositivo,
            "link": enlace,
            "lat": lat,
            "lon": lon,
            "cuando": ahora,
        }

    # Registramos también la primera señal de un punto nuevo. Si no lo
    # hiciéramos, la segunda señal idéntica no sabría que debe deduplicarse.
    evento_repetido = _evento_repetido(
        ("radar", clave),
        RADAR_DEDUPE_SEGUNDOS,
    )
    duplicado = mismo_punto and evento_repetido
    if duplicado:
        print(f"📍 Radar duplicado ignorado: {dispositivo}")
        return jsonify({"estado": "recibido", "duplicado": True, "link": enlace})

    print(f"📍 Radar actualizado desde {dispositivo}")
    try:
        notificador.notificar(
            f"{dispositivo}: {enlace}",
            titulo="Deisy - Radar",
            prioridad="high",
            tags=["round_pushpin"],
        )
    except Exception as error:
        print(f"⚠️ No pude enviar radar a ntfy: {type(error).__name__}")

    return jsonify({"estado": "recibido", "duplicado": False, "link": enlace})


@app.route("/situar", methods=["POST"])
def situar():
    bloqueo = _requiere_token()
    if bloqueo:
        return bloqueo
    datos = _datos_json()
    if datos is None:
        return jsonify({"error": "El cuerpo debe ser JSON."}), 400
    etiqueta = identificar_lugar(datos.get("ubicacion", ""))
    dispositivo = str(datos.get("dispositivo", "iPhone")).strip()[:80]
    clave = _clave_dispositivo(dispositivo)
    if _evento_repetido(
        ("situar", clave, _normalizar(etiqueta)),
        EVENTO_IPHONE_DEDUPE_SEGUNDOS,
    ):
        return jsonify({"aviso_enviado": False, "duplicado": True, "ubicacion": etiqueta})
    try:
        enviado = proactivo.avisar_por_ubicacion(etiqueta)
        return jsonify({"aviso_enviado": bool(enviado), "ubicacion": etiqueta})
    except Exception as error:
        print(f"⚠️ Error en situar: {error}")
        return jsonify({"error": "No pude procesar la ubicación."}), 500


@app.route("/estado_iphone", methods=["POST"])
def recibir_estado_iphone():
    bloqueo = _requiere_token()
    if bloqueo:
        return bloqueo

    datos = _datos_json()
    if datos is None:
        return jsonify({"error": "El cuerpo debe ser JSON."}), 400

    dispositivo = str(datos.get("dispositivo", "iPhone")).strip()[:80]
    enfoque_bruto = str(datos.get("enfoque", "")).strip()
    evento = _normalizar(datos.get("evento", "enfoque_activado"))[:80]
    if not enfoque_bruto:
        return jsonify({"error": "Falta el enfoque."}), 400

    try:
        _, enfoque = normalizar_contexto("enfoque", enfoque_bruto)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    clave = _clave_dispositivo(dispositivo)
    if _evento_repetido(
        ("estado_iphone", clave, enfoque, evento),
        EVENTO_IPHONE_DEDUPE_SEGUNDOS,
    ):
        return jsonify({"estado": "duplicado", "enfoque": enfoque})

    with _CONTEXTO_MOVIL_BLOQUEO:
        _ESTADOS_IPHONE[clave] = {
            "dispositivo": dispositivo,
            "enfoque": enfoque,
            "evento": evento,
            "cuando": datetime.now(),
        }

    actualizar_contexto("enfoque", enfoque, dispositivo)
    print(f"📱 Estado recibido: {dispositivo} → {enfoque} ({evento})")
    return jsonify({"estado": "recibido", "enfoque": enfoque})


@app.route("/contexto_deisy", methods=["POST"])
def recibir_contexto_deisy():
    bloqueo = _requiere_token()
    if bloqueo:
        return bloqueo
    datos = _datos_json()
    if datos is None:
        return jsonify({"error": "El cuerpo debe ser JSON."}), 400
    tipo_bruto = str(datos.get("tipo", ""))
    valor_bruto = str(datos.get("valor", ""))
    origen = str(datos.get("origen", "iPhone")).strip()[:80]
    evento = _normalizar(datos.get("evento", ""))[:80]

    try:
        tipo, valor = normalizar_contexto(tipo_bruto, valor_bruto)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    if _evento_repetido(
        ("contexto_deisy", _clave_dispositivo(origen), tipo, valor, evento),
        EVENTO_IPHONE_DEDUPE_SEGUNDOS,
    ):
        return jsonify({"estado": "duplicado", "modo": estado_actual()["modo"]})

    estado = actualizar_contexto(tipo, valor, origen)
    print(
        f"🧭 Contexto recibido: {tipo}={valor} desde {origen} "
        f"-> modo {estado['modo']}"
    )
    return jsonify({"estado": "recibido", "modo": estado["modo"]})


def _leer_imagen(datos: dict[str, Any]) -> tuple[str, str, str] | tuple[None, None, None]:
    imagen_b64 = str(datos.get("imagen_b64", ""))
    pregunta = str(datos.get("pregunta", "Describe la imagen."))[:1200]
    mime = str(datos.get("mime", "image/png"))
    if mime not in {"image/png", "image/jpeg", "image/webp"} or not imagen_b64 or len(imagen_b64) > MAX_IMAGEN_B64:
        return None, None, None
    try:
        if len(base64.b64decode(imagen_b64, validate=True)) > MAX_IMAGEN_BYTES:
            return None, None, None
    except Exception:
        return None, None, None
    return imagen_b64, pregunta, mime


@app.route("/analizar_pantalla", methods=["POST"])
def analizar_pantalla():
    bloqueo = _requiere_token()
    if bloqueo:
        return bloqueo
    datos = _datos_json()
    if datos is None:
        return jsonify({"error": "El cuerpo debe ser JSON."}), 400
    imagen, pregunta, _ = _leer_imagen(datos)
    if imagen is None:
        return jsonify({"error": "Imagen ausente, inválida o demasiado grande."}), 400
    try:
        respuesta = analizar_imagen_b64(imagen, pregunta, mime="image/png")
        recordar_vision(pregunta, respuesta, sesion="omen:vision")
        return jsonify({"respuesta": respuesta})
    except VisionSinDescripcion:
        # 422: la imagen era válida, pero no existe una descripción final que
        # podamos mostrar con honestidad. No se conserva su Base64.
        return jsonify({
            "error": (
                "No recibí una descripción utilizable de la pantalla. "
                "No voy a inventar qué muestra; puedes intentarlo de nuevo."
            ),
            "codigo": "VISION_SIN_TEXTO",
        }), 422
    except Exception as error:
        print(f"⚠️ Error de visión desde Omen: {error}")
        return jsonify({"error": "No pude analizar la pantalla."}), 500


@app.route("/analizar_foto", methods=["POST"])
def analizar_foto():
    bloqueo = _requiere_token()
    if bloqueo:
        return bloqueo
    datos = _datos_json()
    if datos is None:
        return jsonify({"error": "El cuerpo debe ser JSON."}), 400
    imagen, pregunta, mime = _leer_imagen(datos)
    if imagen is None:
        return jsonify({"error": "Imagen ausente, inválida o demasiado grande."}), 400
    try:
        respuesta = analizar_imagen_b64(imagen, pregunta, mime=mime)
        recordar_vision(pregunta, respuesta, sesion="telegram:vision")
        return jsonify({"respuesta": respuesta})
    except VisionSinDescripcion:
        # Es un fallo controlado del proveedor, no una excepción interna de
        # Flask. Telegram puede explicarlo sin el ambiguo mensaje "error 500".
        return jsonify({
            "error": (
                "No recibí una descripción utilizable de la foto. "
                "No voy a inventar qué muestra; prueba a enviarla otra vez "
                "o añade una pregunta más concreta."
            ),
            "codigo": "VISION_SIN_TEXTO",
        }), 422
    except Exception as error:
        print(f"⚠️ Error analizando foto: {error}")
        return jsonify({"error": "No pude analizar la foto."}), 500


def auditor_subconsciente():
    """Opcional y apagado en .env por defecto: nunca bloquea la API."""
    print("🧠 Subconsciente de Deisy activado. Esperando en las sombras…")
    time.sleep(20)
    while True:
        try:
            ruta = os.path.join(os.path.dirname(__file__), "cerebro.py")
            with open(ruta, encoding="utf-8") as archivo:
                opinion = dar_opinion_codigo(archivo.read()[:3500])
            notificar(f"Mi opinión sobre el código:\n\n{opinion}", titulo="Deisy analizó su código", prioridad="default", tags=["bulb", "robot"])
            print("💡 Reporte del subconsciente enviado.")
        except Exception as error:
            print(f"⚠️ Error en subconsciente: {error}")
        time.sleep(12 * 60 * 60)


def checador_recordatorios_bg():
    """Conserva el guardián existente, aislando errores para no matar la API."""
    try:
        from recordatorios import _cargar, _guardar
    except Exception as error:
        print(f"⚠️ Guardián de recordatorios no disponible: {error}")
        return
    print("⏰ Guardián de recordatorios activo.")
    while True:
        try:
            ahora = datetime.now()
            fecha_hoy, hora_actual = ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M")
            pendientes, modificado = [], False
            for recordatorio in _cargar() or []:
                disparar = False
                if recordatorio.get("repetir") == "unica":
                    if recordatorio.get("fecha") == fecha_hoy and recordatorio.get("hora") == hora_actual:
                        disparar, modificado = True, True
                    else:
                        pendientes.append(recordatorio)
                elif recordatorio.get("repetir") == "toda_la_semana":
                    creado = datetime.strptime(recordatorio["creado"], "%Y-%m-%d %H:%M")
                    if ahora <= creado + timedelta(days=7):
                        fechas = recordatorio.get("fechas_disparadas", [])
                        if recordatorio.get("hora") == hora_actual and fecha_hoy not in fechas:
                            fechas.append(fecha_hoy)
                            recordatorio["fechas_disparadas"] = fechas
                            disparar, modificado = True, True
                        pendientes.append(recordatorio)
                    else:
                        modificado = True
                if disparar:
                    notificar(f"Eddie, no te olvides de: {recordatorio['texto']}", titulo="Deisy - Recordatorio", prioridad="high", tags=["alarm_clock"])
            if modificado:
                _guardar(pendientes)
        except Exception as error:
            print(f"⚠️ Error en guardián de recordatorios: {error}")
        time.sleep(30)


if __name__ == "__main__":
    print("🚀 Servidor de Deisy activo con Waitress.")
    if _env_verdadero("SUBCONSCIENTE_ACTIVO", False):
        threading.Thread(target=auditor_subconsciente, daemon=True).start()
    else:
        print("🧠 Subconsciente desactivado para priorizar respuestas estables.")
    threading.Thread(target=checador_recordatorios_bg, daemon=True).start()
    serve(app, host=os.getenv("DEISY_HOST", "0.0.0.0"), port=int(os.getenv("DEISY_PORT", "5000")))
