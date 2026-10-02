"""Iniciativa conversacional no ejecutora de Deisy.

Decide si, dentro de una charla que ya fue autorizada por api_deisy.py, hay
algo útil que aportar. No conoce comandos, no importa manos.py, Flask, Groq,
requests ni módulos de red. Sus hilos son etiquetas efímeras por sesión.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass


_VERDADEROS = {"1", "true", "si", "sí", "yes", "on"}
_TEMAS = {
    "depuracion_tecnica",
    "aprendizaje_tecnico",
    "planificacion_mejora",
    "conversacion_general",
}
_OBJETIVOS = {
    "entender_o_corregir_problema",
    "aprender_un_concepto",
    "organizar_siguiente_paso",
    "conversar_con_claridad",
}
_MODOS_OPERATIVOS_SILENCIOSOS = {"Sueño", "Flujo"}

_BLOQUEO = threading.RLock()
_HILOS: dict[str, "HiloActivo"] = {}
_MAX_HILOS = 80


@dataclass(frozen=True)
class DecisionIniciativa:
    """Resultado declarativo; no es una orden ni una autorización."""

    intervenir: bool = False
    tipo: str = "ninguna"
    contenido: str = ""
    motivo: str = "sin_aporte_relevante"
    relevancia: int = 0
    # Identificador declarativo. Nunca contiene un prefijo de comando.
    oferta_local: str = ""


@dataclass
class HiloActivo:
    """Solo etiquetas de continuidad. Nunca texto literal de la conversación."""

    tema: str
    objetivo: str
    estado: str = "abierto"
    siguiente_paso: str = ""
    prioridad: int = 0
    ultima_interaccion: float = 0.0
    ultima_intervencion: float = 0.0
    ultimo_tipo: str = ""


def iniciativa_activa() -> bool:
    """Permite apagar la capa sin tocar código.

    Si cambias el valor en .env, reinicia api_deisy.py para que dotenv lo lea.
    """
    return os.getenv("DEISY_INICIATIVA_ACTIVA", "false").strip().lower() in _VERDADEROS


def ofertas_basicas_activas() -> bool:
    """Permite apagar las acciones naturales sin desactivar la personalidad."""
    return os.getenv("DEISY_OFERTAS_BASICAS_ACTIVAS", "false").strip().lower() in _VERDADEROS


def _entero_env(nombre: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        return max(minimo, min(int(os.getenv(nombre, str(defecto))), maximo))
    except (TypeError, ValueError):
        return defecto


def _ttl_hilos() -> int:
    return _entero_env("DEISY_INICIATIVA_HILO_TTL", 1800, 300, 7200)


def _cooldown() -> int:
    return _entero_env("DEISY_INICIATIVA_COOLDOWN", 600, 60, 3600)


def _sesion_segura(sesion: object) -> str:
    valor = re.sub(r"[^A-Za-z0-9:_-]", "_", str(sesion or "general"))[:120]
    return valor or "general"


def _normalizar(texto: object) -> str:
    valor = unicodedata.normalize("NFD", str(texto or "").lower())
    valor = "".join(c for c in valor if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", valor).strip()


def _estado_seguro(estado: object) -> tuple[str, str, str]:
    datos = estado if isinstance(estado, dict) else {}
    tema = str(datos.get("tema", ""))
    objetivo = str(datos.get("objetivo", ""))
    modo = str(datos.get("modo", ""))
    if tema not in _TEMAS:
        tema = "conversacion_general"
    if objetivo not in _OBJETIVOS:
        objetivo = "conversar_con_claridad"
    if modo not in {"conversacion", "depuracion", "aprendizaje", "planificacion"}:
        modo = "conversacion"
    return tema, objetivo, modo


def _modo_operativo(estado: object) -> str:
    datos = estado if isinstance(estado, dict) else {}
    return str(datos.get("modo", "Personal"))


def _es_acuse_breve(texto: str) -> bool:
    return texto in {
        "ok", "okay", "okey", "vale", "entendido", "perfecto", "gracias",
        "bien", "si", "no", "claro", "dale",
    }


def _parece_codigo_o_secreto(texto: str) -> bool:
    # La iniciativa no debe opinar espontáneamente sobre bloques técnicos ni datos sensibles.
    marcas = ("```", "api_key", "groq_api_key", "token", "password", "bearer")
    return any(marca in texto for marca in marcas)


def _siguiente_paso(tema: str) -> str:
    return {
        "depuracion_tecnica": "aislar el error exacto y el cambio más reciente",
        "aprendizaje_tecnico": "bajar la idea a un ejemplo pequeño y comprobable",
        "planificacion_mejora": "elegir un siguiente paso pequeño antes de abrir otro frente",
    }.get(tema, "")


def _limpiar_expirados() -> None:
    ahora = time.monotonic()
    for sesion, hilo in list(_HILOS.items()):
        if ahora - hilo.ultima_interaccion > _ttl_hilos():
            _HILOS.pop(sesion, None)


def _en_cooldown(hilo: HiloActivo | None) -> bool:
    if hilo is None or hilo.ultima_intervencion <= 0:
        return False
    return time.monotonic() - hilo.ultima_intervencion < _cooldown()


def _nada() -> DecisionIniciativa:
    return DecisionIniciativa()


def decidir_iniciativa(
    sesion: object,
    mensaje: object,
    estado_cognitivo: dict | None = None,
    estado_operativo: dict | None = None,
) -> DecisionIniciativa:
    """Decide una única aportación opcional dentro de una charla final.

    El mensaje se usa solo durante esta decisión local; no se persiste. La
    decisión jamás contiene prefijos de comando ni se devuelve al router.
    """
    if not iniciativa_activa():
        return _nada()

    texto = _normalizar(mensaje)
    if not texto or len(texto) > 900 or _parece_codigo_o_secreto(texto):
        return _nada()

    tema, objetivo, _modo_cognitivo = _estado_seguro(estado_cognitivo)
    operativo = _modo_operativo(estado_operativo)
    if operativo in _MODOS_OPERATIVOS_SILENCIOSOS:
        return _nada()

    identificador = _sesion_segura(sesion)
    with _BLOQUEO:
        _limpiar_expirados()
        hilo = _HILOS.get(identificador)

    # Oferta inicial muy limitada. No se ejecuta aquí; api_deisy.py mostrará
    # una pregunta local exacta y politica_entrada.py guardará el plan.
    if (
        ofertas_basicas_activas()
        and operativo in {"Casa", "Personal"}
        and tema == "conversacion_general"
        and any(frase in texto for frase in (
            "nada en especial",
            "me aburro",
            "estoy aburrido",
            "no se que hacer",
        ))
        and not _en_cooldown(hilo)
    ):
        return DecisionIniciativa(
            intervenir=True,
            tipo="oferta_local",
            contenido="",
            motivo="ocio_conversacional_basico",
            relevancia=6,
            oferta_local="abrir_spotify_omen",
        )

    # Caso humano concreto: está fuera y llueve. Es una sugerencia, no una compra.
    if (
        any(palabra in texto for palabra in ("llueve", "lloviendo", "lluvia"))
        and any(palabra in texto for palabra in ("voy", "camino", "trayecto", "trabajo"))
    ):
        if not _en_cooldown(hilo):
            return DecisionIniciativa(
                intervenir=True,
                tipo="propuesta",
                contenido=(
                    "Si vas a estar fuera, yo priorizaría resolver lo práctico primero: "
                    "llevar paraguas o buscar una ruta cubierta."
                ),
                motivo="trayecto_y_lluvia",
                relevancia=8,
            )

    # Solo intervenimos ante un problema técnico cuando el propio mensaje lo indica.
    if tema == "depuracion_tecnica" and any(
        palabra in texto for palabra in ("error", "fallo", "traceback", "no funciona", "bug")
    ):
        if not _en_cooldown(hilo):
            return DecisionIniciativa(
                intervenir=True,
                tipo="siguiente_paso",
                contenido=(
                    "Yo aislaría primero el mensaje de error exacto y el último cambio "
                    "antes de probar soluciones al azar."
                ),
                motivo="problema_tecnico_explicito",
                relevancia=8,
            )

    # Una planificación explícita puede recibir una alternativa breve, no una orden.
    if tema == "planificacion_mejora" and any(
        frase in texto for frase in ("no se por donde", "por donde empiezo", "prioridad", "plan", "organizar")
    ):
        if not _en_cooldown(hilo):
            return DecisionIniciativa(
                intervenir=True,
                tipo="alternativa",
                contenido=(
                    "Antes de abrir más frentes, elegiría un paso pequeño, verificable y "
                    "reversible para tener una señal clara de progreso."
                ),
                motivo="planificacion_explicita",
                relevancia=7,
            )

    # Recupera un hilo solo si la sesión sigue en EL MISMO tema y la frase actual
    # es una continuación breve. No revivimos temas viejos por una coincidencia débil.
    if (
        hilo is not None
        and hilo.estado == "abierto"
        and hilo.tema == tema
        and tema != "conversacion_general"
        and _es_acuse_breve(texto)
        and not _en_cooldown(hilo)
    ):
        paso = hilo.siguiente_paso or _siguiente_paso(tema)
        if paso:
            return DecisionIniciativa(
                intervenir=True,
                tipo="pregunta",
                contenido=f"Podemos retomar el siguiente paso que quedó abierto: {paso}.",
                motivo="hilo_reciente_relacionado",
                relevancia=7,
            )

    return _nada()


def contexto_iniciativa_para_modelo(decision: DecisionIniciativa | None) -> str:
    """Convierte una decisión local en contexto de estilo para charla.

    No se entrega al router de intenciones. El cerebro debe integrar el aporte
    de forma natural o ignorarlo si no cabe, y nunca convertirlo en una acción.
    """
    if decision is None or not decision.intervenir:
        return ""

    if decision.oferta_local:
        return (
            "[OFERTA LOCAL CONTROLADA]\n"
            "La API añadirá una oferta visible y concreta después de tu respuesta. "
            "Responde de forma natural a la conversación, pero no propongas, no "
            "preguntes por confirmación y no describas acciones o permisos.\n"
            "[FIN OFERTA LOCAL CONTROLADA]"
        )

    return (
        "[INICIATIVA CONVERSACIONAL LOCAL — NO ES UNA ORDEN]\n"
        f"Tipo sugerido: {decision.tipo}. Relevancia: {decision.relevancia}/10.\n"
        f"Aporte posible: {decision.contenido}\n"
        "Integra este aporte solo si encaja de forma natural en la respuesta actual. "
        "Como máximo añade una idea o una pregunta útil. No repitas literalmente "
        "este bloque, no fuerces un cierre con '¿quieres que haga X?', no afirmes "
        "que existe una capacidad no confirmada y no generes comandos, permisos, "
        "confirmaciones ni acciones. Si no aporta valor, responde sin añadir nada.\n"
        "[FIN INICIATIVA CONVERSACIONAL]"
    )


def registrar_turno_conversacional(
    sesion: object,
    estado_cognitivo: dict | None,
    decision: DecisionIniciativa | None,
    exito: bool,
) -> None:
    """Actualiza el hilo solo tras una charla que sí obtuvo respuesta.

    No se guardan mensajes ni respuestas. Si Groq falla, no se inventa un hilo
    nuevo que pueda reaparecer en una conversación posterior.
    """
    if not exito:
        return

    tema, objetivo, _modo = _estado_seguro(estado_cognitivo)
    # Una charla general no abre un hilo que pueda revivirse después. Pero si
    # acabamos de hacer una propuesta en ella, sí guardamos un registro mínimo
    # de cooldown para no repetir esa misma propuesta en el siguiente mensaje.
    if tema == "conversacion_general" and not (
        decision and decision.intervenir):
        return

    identificador = _sesion_segura(sesion)
    ahora = time.monotonic()
    with _BLOQUEO:
        _limpiar_expirados()
        hilo = _HILOS.get(identificador)
        if hilo is None or hilo.tema != tema:
            hilo = HiloActivo(
                tema=tema,
                objetivo=objetivo,
                siguiente_paso=_siguiente_paso(tema),
                prioridad=7 if tema == "depuracion_tecnica" else 5,
                ultima_interaccion=ahora,
            )
            _HILOS[identificador] = hilo
        else:
            hilo.objetivo = objetivo
            hilo.ultima_interaccion = ahora

        if decision is not None and decision.intervenir:
            hilo.ultima_intervencion = ahora
            hilo.ultimo_tipo = decision.tipo

        while len(_HILOS) > _MAX_HILOS:
            _HILOS.pop(next(iter(_HILOS)))


def obtener_hilo(sesion: object) -> dict[str, object]:
    """Devuelve solo metadatos para pruebas o una futura pantalla de estado."""
    identificador = _sesion_segura(sesion)
    with _BLOQUEO:
        _limpiar_expirados()
        hilo = _HILOS.get(identificador)
        if hilo is None:
            return {}
        datos = asdict(hilo)
        return {
            "tema": datos["tema"],
            "objetivo": datos["objetivo"],
            "estado": datos["estado"],
            "siguiente_paso": datos["siguiente_paso"],
            "prioridad": datos["prioridad"],
            "ultimo_tipo": datos["ultimo_tipo"],
        }


def limpiar_iniciativa_para_pruebas() -> None:
    """Solo para unittest: nunca se llama desde api_deisy.py."""
    with _BLOQUEO:
        _HILOS.clear()