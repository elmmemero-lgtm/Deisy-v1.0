"""Contexto corto y seguro de órdenes de la Omen.

Permite continuaciones breves únicamente después de una confirmación real de
la Omen. El estado vive solo en RAM y caduca: nunca se convierte en un permiso
permanente ni sobrevive al reinicio de api_deisy.py.
"""

from __future__ import annotations

import re
import time
import unicodedata
from threading import RLock
from politica_entrada import es_peticion_apertura


TTL_SEGUNDOS = 120
MAX_SESIONES = 50
_bloqueo = RLock()
_ultimas_acciones: dict[str, dict[str, object]] = {}


def _normalizar(texto: object) -> str:
    """Normaliza texto para comparar sin depender de mayúsculas ni tildes."""
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


def _clave_sesion(sesion: object) -> str:
    """Evita que el contexto de Telegram active una orden de otro canal."""
    return re.sub(r"[^a-z0-9_.:-]", "_", _normalizar(sesion))[:100] or "principal"


def _categoria_repetible(comando: object) -> str:
    """Clasifica solo acciones inocuas que admiten contexto corto."""
    comando = _normalizar(comando)
    if comando in {"sistema: subir_volumen", "sistema: bajar_volumen"}:
        return "volumen"
    if comando in {"tecla: siguiente", "tecla: anterior", "tecla: pausa", "tecla: play"}:
        return "musica"
    return ""


def _purgar_expiradas() -> None:
    """Elimina contexto viejo antes de tomar una decisión."""
    ahora = time.monotonic()
    for clave in list(_ultimas_acciones):
        if ahora - float(_ultimas_acciones[clave]["cuando"]) > TTL_SEGUNDOS:
            _ultimas_acciones.pop(clave, None)


def registrar_orden_confirmada(comando: object, sesion: object) -> bool:
    """Guarda solo una acción inocua que la Omen confirmó de verdad.

    Además de la categoría se conserva el comando canónico exacto. Eso evita
    interpretar "más" como subir cuando la última acción fue bajar, o "otra
    vez" como saltar canción cuando lo último fue una pausa.
    """
    comando_normalizado = _normalizar(comando)
    categoria = _categoria_repetible(comando_normalizado)
    clave = _clave_sesion(sesion)

    with _bloqueo:
        _purgar_expiradas()

        # Si acaba de confirmarse otra acción, la referencia anterior deja de
        # ser el contexto correcto para una frase corta posterior.
        if not categoria:
            _ultimas_acciones.pop(clave, None)
            return False

        _ultimas_acciones[clave] = {
            "categoria": categoria,
            "comando": comando_normalizado,
            "cuando": time.monotonic(),
        }
        while len(_ultimas_acciones) > MAX_SESIONES:
            _ultimas_acciones.pop(next(iter(_ultimas_acciones)))
    return True


def olvidar_referencia_repetible(comando: object, sesion: object) -> None:
    """Olvida contexto tras un fallo de la Omen.

    Da igual qué comando falló: es más seguro no reutilizar una acción anterior
    tras una petición distinta que no llegó a confirmarse.
    """
    with _bloqueo:
        _ultimas_acciones.pop(_clave_sesion(sesion), None)


def _ultima_accion_repetible(sesion: object) -> dict[str, object]:
    """Devuelve una copia del último contexto válido de ESTA sesión."""
    with _bloqueo:
        _purgar_expiradas()
        dato = _ultimas_acciones.get(_clave_sesion(sesion))
        return dict(dato) if dato else {}


def resolver_contexto_corto(texto: object, sesion: object) -> tuple[str, str]:
    """Devuelve ``(comando, aclaración)``; si ambos son vacíos, no decide.

    Esta función nunca manda HTTP, no llama a Groq y no ejecuta nada. Solo
    reconstruye un comando ya conocido; api_deisy.py seguirá aplicando su
    puerta, filtros y ejecutor normales.
    """
    normal = _normalizar(texto).lstrip("¿¡ ")

    # Órdenes completas y claras: no dependen del contexto anterior.
    if re.match(
        r"^(?:por favor |oye(?: deisy)? )?(?:puedes |podrias )?"
        r"(?:sube|aumenta|incrementa|subelo|subele)\b.*\b"
        r"(volumen|audio|sonido)\b",
        normal,
    ):
        return "sistema: subir_volumen", ""
    if re.match(
        r"^(?:por favor |oye(?: deisy)? )?(?:puedes |podrias )?"
        r"(?:baja|disminuye|reduce|bajalo|bajale)\b.*\b"
        r"(volumen|audio|sonido)\b",
        normal,
    ):
        return "sistema: bajar_volumen", ""

    if re.match(r"^(?:pasa|cambia|pon)\b.*\b(siguiente|otra)\b", normal) and any(
        x in normal for x in ("cancion", "musica", "tema", "pista")
    ):
        return "tecla: siguiente", ""
    if re.match(r"^(?:vuelve|pon)\b.*\banterior\b", normal) and any(
        x in normal for x in ("cancion", "musica", "tema", "pista")
    ):
        return "tecla: anterior", ""
    if re.match(r"^(?:pausa|pausala)\b", normal) and any(
        x in normal for x in ("cancion", "musica", "tema", "pista")
    ):
        return "tecla: pausa", ""
    # Reproducción explícita: reversible y no depende de que Spotify sea una
    # aplicación instalada. La señal media se manda solo a la Omen.
    if re.match(
        r"^(?:por favor |oye(?: deisy)? )?(?:puedes |podrias )?"
        r"(?:pon|poner|reproduce|reproducir|play)\b",
        normal,
    ) and any(x in normal for x in ("musica", "spotify", "cancion", "tema", "pista")):
        return "tecla: play", ""

    ultima = _ultima_accion_repetible(sesion)
    categoria = str(ultima.get("categoria", ""))
    comando_anterior = str(ultima.get("comando", ""))

    # "Más" por sí solo conserva la DIRECCIÓN confirmada, no una dirección
    # inventada. Tras bajar, baja más; tras subir, sube más.
    if normal in {"mas", "un poco mas", "sube mas", "aumenta mas", "subelo mas", "subele mas", "subelo un poco mas", "subele un poco mas"}:
        if normal.startswith(("sube", "aumenta", "subelo", "subele")):
            if categoria == "volumen":
                return "sistema: subir_volumen", ""
            return "", "¿Te refieres a subir el volumen de la Omen? Si es así, di «sube el volumen»."
        if comando_anterior in {"sistema: subir_volumen", "sistema: bajar_volumen"}:
            return comando_anterior, ""
        return "", "¿Te refieres a ajustar el volumen de la Omen? Di «sube el volumen» o «baja el volumen»."

    # "Baja más" conserva su intención explícita de bajar, pero aun así
    # exige que la última acción confirmada de ESTA sesión fuera de volumen.
    # Sin ese contexto no sabemos qué debería bajar y pedimos aclaración.
    if normal in {"baja mas", "disminuye mas", "bajalo mas", "bajale mas", "bajalo un poco", "bajale un poco", "baja un poco"}:
        if categoria == "volumen":
            return "sistema: bajar_volumen", ""
        return "", "¿Te refieres a bajar el volumen de la Omen? Si es así, di «baja el volumen»."

    # "Otra vez" no significa siempre "siguiente". Solo repite el último
    # salto de canción confirmado. Una pausa o play exige que lo digas claro.
    if normal in {"otra vez", "otra", "repite eso"}:
        if comando_anterior in {"tecla: siguiente", "tecla: anterior"}:
            return comando_anterior, ""
        return "", "¿Quieres pasar a la siguiente canción en la Omen? Si es así, di «pasa a la siguiente canción»."

    if normal in {"pasa otra", "pasa otra cancion", "pasa la siguiente cancion", "otra cancion"}:
        if categoria == "musica":
            return "tecla: siguiente", ""
        return "", "¿Te refieres a pasar a la siguiente canción en la Omen?"

    if normal in {"pasa la cancion anterior", "vuelve a la cancion anterior"}:
        if categoria == "musica":
            return "tecla: anterior", ""
        return "", "¿Te refieres a volver a la canción anterior en la Omen?"

    return "", ""

_ALIAS_LOL = re.compile(r"\b(?:lol|league(?:\s+of)?\s+legends|riot(?:\s+games)?)\b", re.IGNORECASE)
_ALIAS_SPOTIFY = re.compile(r"\b(?:spotify|spoty)\b", re.IGNORECASE)


def resolver_alias_apertura(texto: object) -> tuple[str, str]:
    """Resuelve únicamente alias que han sido comprobados en la Omen."""
    normal = _normalizar(texto).lstrip("¿¡ ")
    if not es_peticion_apertura(texto) or re.search(r"\b(iphone|movil)\b", normal):
        return "", ""
    if _ALIAS_LOL.search(normal):
        return "buscar: League of Legends", "LOL/Riot Games → League of Legends en la Omen"
    if _ALIAS_SPOTIFY.search(normal):
        return "buscar: Spotify", "Spotify → Spotify en la Omen"
    return "", ""