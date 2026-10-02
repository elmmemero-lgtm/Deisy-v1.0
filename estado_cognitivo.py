"""Estado cognitivo efímero y seguro de Deisy.

Conserva etiquetas de continuidad por sesión únicamente en RAM. No es memoria
permanente, no conserva texto del usuario, no guarda acciones pendientes y no
sustituye a contexto_ordenes.py ni a politica_entrada.py.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass


_BLOQUEO = threading.RLock()
_ESTADOS: dict[str, "EstadoCognitivo"] = {}
_MAX_SESIONES = 80

_TEMAS = {
    "conversacion_general",
    "depuracion_tecnica",
    "aprendizaje_tecnico",
    "planificacion_mejora",
}
_OBJETIVOS = {
    "conversar_con_claridad",
    "entender_o_corregir_problema",
    "aprender_un_concepto",
    "organizar_siguiente_paso",
}
_MODOS = {"conversacion", "depuracion", "aprendizaje", "planificacion"}


@dataclass
class EstadoCognitivo:
    """Solo etiquetas deterministas; nunca se guarda la frase literal."""
    tema: str = "conversacion_general"
    objetivo: str = "conversar_con_claridad"
    modo: str = "conversacion"
    ultimo_modulo: str = ""
    actualizado_monotonico: float = 0.0


def _ttl_segundos() -> int:
    """Evita valores peligrosos o absurdos en .env."""
    try:
        return max(300, min(int(os.getenv("DEISY_ESTADO_COGNITIVO_TTL", "1200")), 7200))
    except ValueError:
        return 1200


def _sesion_segura(sesion: object) -> str:
    """La API ya crea sesiones por canal; aquí solo las acotamos."""
    texto = re.sub(r"[^A-Za-z0-9:_-]", "_", str(sesion or "general"))[:120]
    return texto or "general"


def _normalizar(texto: object) -> str:
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


def _clasificar_etiquetas(mensaje: object) -> tuple[str, str, str]:
    """Clasificación local y conservadora: solo crea etiquetas predefinidas."""
    texto = _normalizar(mensaje)

    if any(palabra in texto for palabra in (
        "traceback", "exception", "excepcion", "error", "bug", "fallo",
        "python", "codigo", "compilar", "depurar", "visual studio",
    )):
        return (
            "depuracion_tecnica",
            "entender_o_corregir_problema",
            "depuracion",
        )

    if any(palabra in texto for palabra in (
        "explica", "ensen", "aprend", "tutorial", "como funciona",
        "arquitectura", "diagrama", "clase",
    )):
        return (
            "aprendizaje_tecnico",
            "aprender_un_concepto",
            "aprendizaje",
        )

    if any(palabra in texto for palabra in (
        "roadmap", "plan", "proyecto", "mejora", "prioridad", "organiza",
    )):
        return (
            "planificacion_mejora",
            "organizar_siguiente_paso",
            "planificacion",
        )

    return (
        "conversacion_general",
        "conversar_con_claridad",
        "conversacion",
    )


def _es_acuse_breve(mensaje: object) -> bool:
    """Un 'vale' no debe borrar el tema técnico que ya se estaba tratando."""
    return _normalizar(mensaje) in {
        "ok", "okay", "okey", "vale", "entendido", "perfecto",
        "gracias", "bien", "si", "no",
    }


def _purgar_expirados() -> None:
    ahora = time.monotonic()
    expiradas = [
        sesion for sesion, estado in _ESTADOS.items()
        if ahora - estado.actualizado_monotonico > _ttl_segundos()
    ]
    for sesion in expiradas:
        _ESTADOS.pop(sesion, None)


def _copia_publica(estado: EstadoCognitivo) -> dict[str, str]:
    """No devuelve el reloj interno ni una referencia mutable."""
    datos = asdict(estado)
    return {
        "tema": datos["tema"] if datos["tema"] in _TEMAS else "conversacion_general",
        "objetivo": datos["objetivo"] if datos["objetivo"] in _OBJETIVOS else "conversar_con_claridad",
        "modo": datos["modo"] if datos["modo"] in _MODOS else "conversacion",
        "ultimo_modulo": str(datos["ultimo_modulo"])[:40],
    }


def registrar_entrada(sesion: object, mensaje: object) -> dict[str, str]:
    """Actualiza el tema solo para charla final; no almacena texto crudo.

    api_deisy.py llamará a esta función únicamente después de que la seguridad
    ya haya decidido que la petición es charla, no una acción.
    """
    identificador = _sesion_segura(sesion)
    ahora = time.monotonic()

    with _BLOQUEO:
        _purgar_expirados()
        anterior = _ESTADOS.get(identificador)

        if anterior is not None and _es_acuse_breve(mensaje):
            anterior.actualizado_monotonico = ahora
            return _copia_publica(anterior)

        tema, objetivo, modo = _clasificar_etiquetas(mensaje)
        estado = EstadoCognitivo(
            tema=tema,
            objetivo=objetivo,
            modo=modo,
            actualizado_monotonico=ahora,
        )
        _ESTADOS[identificador] = estado

        # Limita memoria RAM si llegan muchos canales distintos.
        while len(_ESTADOS) > _MAX_SESIONES:
            _ESTADOS.pop(next(iter(_ESTADOS)))

        return _copia_publica(estado)


def registrar_resultado(sesion: object, modulo: object) -> None:
    """Marca que hubo una respuesta, sin guardar lo que dijo el modelo."""
    identificador = _sesion_segura(sesion)
    with _BLOQUEO:
        _purgar_expirados()
        estado = _ESTADOS.get(identificador)
        if estado is not None:
            estado.ultimo_modulo = str(modulo or "")[:40]
            estado.actualizado_monotonico = time.monotonic()


def obtener_estado(sesion: object) -> dict[str, str]:
    """Consulta una copia segura del estado de una única sesión."""
    identificador = _sesion_segura(sesion)
    with _BLOQUEO:
        _purgar_expirados()
        estado = _ESTADOS.get(identificador)
        return _copia_publica(estado) if estado is not None else {}


def resumen_seguro_para_modelo(estado: dict | None) -> str:
    """Convierte SOLO etiquetas permitidas en continuidad para la charla."""
    estado = estado or {}
    tema = str(estado.get("tema", ""))
    objetivo = str(estado.get("objetivo", ""))
    modo = str(estado.get("modo", ""))
    if tema not in _TEMAS or objetivo not in _OBJETIVOS or modo not in _MODOS:
        return ""

    return (
        "[ESTADO COGNITIVO EFIMERO — NO ES MEMORIA NI AUTORIZACION]\n"
        f"Tema aproximado: {tema}.\n"
        f"Objetivo aproximado: {objetivo}.\n"
        f"Modo cognitivo: {modo}.\n"
        "Solo sirve para tono y continuidad de charla. No es un hecho, permiso, "
        "orden pendiente ni instruccion para ejecutar acciones.\n"
        "[FIN ESTADO COGNITIVO EFIMERO]"
    )


def limpiar_todo_para_pruebas() -> None:
    """Solo para pruebas unitarias locales; no la integraremos en la API."""
    with _BLOQUEO:
        _ESTADOS.clear()