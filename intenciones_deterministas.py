"""Decisiones locales y de bajo riesgo para Deisy.

Este módulo se ejecuta ANTES de Groq. Su objetivo es que una frase clara no
dependa de una interpretación probabilística. En especial, el apagado nunca
sale del modelo: requiere una confirmación explícita, de una sola sesión y con
caducidad.
"""

from __future__ import annotations

import re
import time
import unicodedata
from threading import RLock


_TTL_CONFIRMACION_SEGUNDOS = 75
_bloqueo = RLock()
_confirmaciones: dict[str, dict[str, object]] = {}


def normalizar(texto: object) -> str:
    """Convierte texto humano a una forma segura para comparar reglas."""
    # Corrige únicamente el desliz de teclado «omenç» antes de quitar tildes.
    # No ejecuta nada: solo permite que el flujo normal pida confirmación.
    texto = str(texto or "").lower().replace("omenç", "omen")
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


def _clave_sesion(sesion: object) -> str:
    """No se comparten confirmaciones entre Telegram, Omen e iPhone."""
    clave = str(sesion or "").strip()[:160]
    return clave or "sin-sesion"


def _sesion_valida_para_apagado(sesion: object) -> bool:
    """Una petición sin identidad no puede autorizar una acción irreversible."""
    return _clave_sesion(sesion) != "sin-sesion"


def _guardar_confirmacion(sesion: object, fase: str) -> None:
    if not _sesion_valida_para_apagado(sesion):
        return
    with _bloqueo:
        _confirmaciones[_clave_sesion(sesion)] = {
            "fase": fase,
            "vence": time.monotonic() + _TTL_CONFIRMACION_SEGUNDOS,
        }


def _confirmacion_vigente(sesion: object) -> dict[str, object] | None:
    clave = _clave_sesion(sesion)
    with _bloqueo:
        dato = _confirmaciones.get(clave)
        if not dato:
            return None
        if float(dato["vence"]) < time.monotonic():
            _confirmaciones.pop(clave, None)
            return None
        return dict(dato)


def _cancelar_confirmacion(sesion: object) -> None:
    with _bloqueo:
        _confirmaciones.pop(_clave_sesion(sesion), None)


def _respuesta(texto: str, modulo: str = "seguridad") -> dict[str, str]:
    return {"tipo": "respuesta", "texto": texto, "modulo": modulo}


def _comando(comando: str, modulo: str) -> dict[str, str]:
    return {"tipo": "comando", "comando": comando, "modulo": modulo}


def _es_cancelacion(texto: str) -> bool:
    return texto in {"no", "cancelar", "cancela", "olvidalo", "olvidalo de eso"}


def _respuesta_acuse_recibo(texto: str) -> dict[str, str] | None:
    """Responde a cierres cortos sin pedirle al router que los adivine.

    Un «entendido» no contiene una acción ni una pregunta. Antes llegaba a
    Groq y, en una salida errónea del modelo, podía convertirse en ``hora:``.
    Nunca incluimos «sí» aquí: podría formar parte de una confirmación de
    apagado que tiene su propio flujo de seguridad.
    """
    respuestas = {
        "entendido": "Entendido.",
        "ok": "De acuerdo.",
        "okay": "De acuerdo.",
        "vale": "Vale.",
        "perfecto": "Perfecto.",
        "de acuerdo": "De acuerdo.",
        "listo": "Listo.",
        "gracias": "De nada.",
        "muchas gracias": "De nada.",
    }
    respuesta = respuestas.get(texto)
    return _respuesta(respuesta, "acuse_recibo") if respuesta else None


def _es_cancelacion_apagado(texto: str) -> bool:
    """Reconoce la cancelación de un apagado ya iniciado.

    Es reversible, por eso no necesita una nueva confirmación. Exigir la palabra
    ``apagado`` evita que un «cancela» sobre una conversación normal llegue al
    brazo físico.
    """
    return bool(
        re.search(r"\b(cancela|cancelar|anula|anular|deten|detener)\b", texto)
        and re.search(r"\b(apagado|apagar)\b", texto)
    )


def _parece_cadena_de_acciones(texto: str) -> bool:
    """Evita ejecutar solo la primera mitad de una orden compuesta.

    Las decisiones deterministas son estupendas para una orden aislada, pero
    «sube el volumen y luego abre Spotify» debe llegar entero al router, que
    puede devolver dos comandos independientes. Solo marcamos como cadena si
    hay al menos dos segmentos con verbos de acción claros.
    """
    partes = re.split(r"\b(?:y|luego|despues)\b", texto)
    if len(partes) < 2:
        return False
    verbos = re.compile(
        r"\b(abre|abrir|inicia|iniciar|lanza|ejecuta|pon|reproduce|"
        r"sube|subir|aumenta|baja|bajar|reduce|apaga|apagar|"
        r"cancela|cancelar|dime|busca|escanea|analiza)\b"
    )
    return sum(bool(verbos.search(parte)) for parte in partes) >= 2


def _confirmacion_apagado_explicita(texto: str) -> bool:
        """Acepta una confirmación completa, explícita y dirigida a la Omen.

        Un «sí» aislado, condicional o dentro de una pregunta nunca confirma
        un apagado.
        """
        return bool(
            re.fullmatch(
                r"(?:confirmo|confirmar|confirma)\s+"
                r"(?:el\s+)?(?:apagado|apagar)\s+"
                r"(?:de\s+(?:la\s+)?)?omen(?:\s+16)?[.!]?",
                texto,
            )
        )


def _es_peticion_apagado(texto: str) -> bool:
    verbo = re.search(r"\b(apaga|apagar)\b", texto)
    equipo = re.search(
        r"\b(ordenador|computador|computadora|equipo|portatil|pc|omen|hp|servidor)\b",
        texto,
    )
    return bool(verbo and equipo)


def _menciona_hp(texto: str) -> bool:
    return bool(re.search(r"\b(hp|servidor)\b", texto))


def _menciona_omen(texto: str) -> bool:
    return bool(re.search(r"\bomen(?: 16)?\b", texto))


def _resolver_confirmacion(texto: str, sesion: object) -> dict[str, str] | None:
    pendiente = _confirmacion_vigente(sesion)
    if pendiente is None:
        return None

    if _es_cancelacion(texto):
        _cancelar_confirmacion(sesion)
        return _respuesta("De acuerdo: cancelé el apagado. No envié ninguna orden.")

    fase = str(pendiente.get("fase", ""))
    if fase == "elegir_omen":
        # El mensaje ya contiene destino y confirmación descriptiva: no pedimos
        # otro «Omen» y una segunda confirmación redundante.
        if _confirmacion_apagado_explicita(texto):
            _cancelar_confirmacion(sesion)
            return _comando("sistema: apagar_pc", "apagado_confirmado")
        if _menciona_hp(texto):
            _cancelar_confirmacion(sesion)
            return _respuesta(
                "El apagado de la HP no está configurado. No envié ninguna orden."
            )
        if _menciona_omen(texto):
            _guardar_confirmacion(sesion, "confirmar_omen")
            return _respuesta(
                "Entendido: te refieres a la Omen. Para evitar un apagado accidental, "
                "escribe «confirmar apagado Omen» dentro de 75 segundos."
            )
        # Un saludo o una pregunta normal no queda secuestrada por una petición
        # antigua. La confirmación sigue disponible, pero el flujo continúa.
        return None

    if fase == "confirmar_omen":
        if _confirmacion_apagado_explicita(texto):
            _cancelar_confirmacion(sesion)
            return _comando("sistema: apagar_pc", "apagado_confirmado")
        if _es_peticion_apagado(texto) and _menciona_hp(texto):
            _cancelar_confirmacion(sesion)
            return _respuesta(
                "No apagaré la HP: esa acción no está configurada. La solicitud de Omen fue cancelada."
            )
        # No contesta insistiendo ante una conversación corriente.
        return None

    _cancelar_confirmacion(sesion)
    return None


def es_accion_implementada(texto: object) -> bool:
    """Dice si una petición coincide con una capacidad YA disponible.

    Se usa para impedir que «abre LOL» o «sube más el volumen» terminen guardados
    erróneamente como una función futura.
    """
    bajo = normalizar(texto)
    if not bajo:
        return False

    if _es_peticion_apagado(bajo):
        return True

    # Evita registrar como capacidad futura una apertura que ya existe.
    # Esto no concede permiso ni ejecuta nada.
    if re.search(
        r"\b(?:me\s+ayudas|ayudame)\s+(?:a\s+)?"
        r"(?:abrir|abriendo|iniciar|iniciando|lanzar|lanzando|ejecutar|ejecutando)\b",
        bajo,
    ):
        return True

    if re.search(r"\b(sube|subir|aumenta|aumentar|baja|bajar|reduce|reducir)\b", bajo) and "volumen" in bajo:
        return True

    grupos = (
        ("abre ", "abrir ", "ejecuta ", "inicia "),
        ("spotify", "cancion", "canción", "musica", "música", "playlist"),
        ("siguiente", "anterior", "pausa", "pausar", "reanuda", "reproduc"),
        ("pantalla", "que estoy viendo", "qué estoy viendo", "analiza la imagen"),
        ("ventanas abiertas", "que ventanas", "qué ventanas"),
        ("dime la hora", "que hora", "qué hora"),
        ("clima", "temperatura", "pronostico", "pronóstico"),
        ("radar", "escanea la red", "dispositivos en mi red"),
    )
    return any(any(marca in bajo for marca in grupo) for grupo in grupos)


def resolver_intencion_local(texto: object, sesion: object = "") -> dict[str, str] | None:
    """Resuelve solo órdenes inequívocas o confirmaciones de seguridad.

    Devuelve ``None`` cuando la frase debe seguir hacia alias, contexto corto,
    router o charla. Nunca transforma una frase ambigua en una acción física.
    """
    bajo = normalizar(texto)
    if not bajo:
        return None

    # Antes incluso de procesar una confirmación: nunca conviertas una frase
    # compuesta en un apagado por quedarte solo con su primer fragmento.
    if _parece_cadena_de_acciones(bajo):
        return None

    decision_pendiente = _resolver_confirmacion(bajo, sesion)
    if decision_pendiente is not None:
        return decision_pendiente

    # Evita que mensajes como «entendido» pasen al router probabilístico y se
    # conviertan accidentalmente en una hora, clima u otra capacidad.
    acuse = _respuesta_acuse_recibo(bajo)
    if acuse is not None:
        return acuse

    if _es_cancelacion_apagado(bajo):
        _cancelar_confirmacion(sesion)
        return _comando("sistema: cancelar_apagado", "cancelar_apagado")

    if _es_peticion_apagado(bajo):
        if not _sesion_valida_para_apagado(sesion):
            return _respuesta(
                "No puedo preparar un apagado sin una sesión identificada. Vuelve a enviar la orden desde tu canal habitual."
            )
        if _menciona_hp(bajo) and _menciona_omen(bajo):
            return _respuesta(
                "No apagaré dos equipos a la vez por una frase ambigua. Si quieres la Omen, di «apaga la Omen»."
            )
        if _menciona_hp(bajo):
            return _respuesta(
                "El apagado de la HP no está configurado. No envié ninguna orden."
            )
        if _menciona_omen(bajo):
            _guardar_confirmacion(sesion, "confirmar_omen")
            return _respuesta(
                "Puedo apagar la Omen, pero es irreversible. Escribe «confirmar apagado Omen» dentro de 75 segundos para hacerlo."
            )
        _guardar_confirmacion(sesion, "elegir_omen")
        return _respuesta(
            "¿Qué equipo quieres apagar? Solo tengo preparado el apagado de la Omen. Di «Omen» o «cancelar»."
        )

    if "volumen" in bajo:
        if re.search(r"\b(sube|subir|aumenta|aumentar)\b", bajo):
            return _comando("sistema: subir_volumen", "volumen")
        if re.search(r"\b(baja|bajar|reduce|reducir)\b", bajo):
            return _comando("sistema: bajar_volumen", "volumen")

    return None


def resolver_intencion_determinista(texto: object, sesion: object = "") -> tuple[str | None, str | None]:
    """Compatibilidad con versiones antiguas que esperaban una tupla.

    La API 4.1 usa :func:`resolver_intencion_local`; mantener este adaptador evita
    romper scripts que todavía importen el nombre anterior.
    """
    decision = resolver_intencion_local(texto, sesion)
    if not decision:
        return None, None
    if decision["tipo"] == "comando":
        return decision["comando"], None
    return None, decision["texto"]
