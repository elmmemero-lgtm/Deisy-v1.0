"""Puerta de intención de Deisy.

Nunca ejecuta acciones. Solo decide si un texto humano es charla, una acción
explícita o una petición ambigua que debe aclararse antes de llegar a Groq.
"""

from __future__ import annotations

from collections import deque
import re
import threading
import time
import unicodedata


# Una aclaración no puede vivir para siempre: evita que una respuesta tardía
# como "sí" active algo que se preguntó hace mucho.
TTL_ACLARACION = 90
TTL_CONTEXTO_CHAT = 20 * 60
MAX_TURNOS = 4

# Oferta conversacional básica: vive solo en RAM y expira rápido.
TTL_OFERTA_BASICA = 90

# Catálogo cerrado. Aquí hay códigos declarativos, no texto libre de Groq.
# Cada oferta enumera exactamente las acciones permitidas y su familia actual.
_OFERTAS_BASICAS = {
    "abrir_spotify_omen": {
        "acciones": ("buscar: Spotify",),
        "familias": frozenset({"apps"}),
        "texto": "Si te apetece, puedo abrir Spotify en la Omen. ¿Lo hago?",
    },
}

# Segunda barrera: aunque alguien añada una plantilla por error, esta fase solo
# admite UNA acción de la allowlist. No existen lotes todavía.
_ACCIONES_BASICAS_PERMITIDAS = {"buscar: Spotify"}

# Aceptamos pocas expresiones completas. "vale" queda fuera a propósito: puede
# ser solo un acuse de recibo y no una autorización.
_ACEPTACIONES_OFERTA = {
    "si", "si por favor", "dale", "claro", "hazlo", "adelante",
}
_CANCELACIONES_OFERTA = {
    "no", "no gracias", "cancelar", "cancela", "dejalo", "olvidalo", "da igual",
}

_bloqueo = threading.RLock()
_sesiones = {}


def normalizar(texto):
    """Minúsculas, sin tildes y con espacios consistentes para comparar."""
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )
    return re.sub(r"\s+", " ", texto).strip()


def _estado(sesion):
    """Obtiene el estado efímero aislado para un canal o dispositivo."""
    sesion = str(sesion or "general").strip()[:120] or "general"
    ahora = time.monotonic()

    with _bloqueo:
        dato = _sesiones.setdefault(
            sesion,
            {
            "aclaracion": None,
            "oferta": None,
            "turnos": deque(maxlen=MAX_TURNOS),
            },
        )

        pendiente = dato.get("aclaracion")
        if pendiente and ahora >= pendiente["caduca"]:
            dato["aclaracion"] = None

        oferta = dato.get("oferta")
        if oferta and ahora >= oferta["caduca"]:
            dato["oferta"] = None

        # El contexto de charla caduca; no es memoria persistente.
        while dato["turnos"] and ahora - dato["turnos"][0]["cuando"] > TTL_CONTEXTO_CHAT:
            dato["turnos"].popleft()

        return dato


def _decision(clase, familias=(), respuesta="", comando_preparado="", contexto=""):
    """Formato único que devuelve clasificar_entrada()."""
    return {
        "clase": clase,              # charla | accion_explicita | ambigua
        "familias": set(familias),
        "respuesta": respuesta,
        "comando_preparado": comando_preparado,
        "contexto": contexto,
    }


def familia_de_comando(comando):
    """Traduce el prefijo interno de un comando a una familia funcional."""
    comando = str(comando or "").strip().lower()

    if comando.startswith(("buscar:", "start:")):
        return "apps"
    if comando.startswith((
        "musica:",
        "musica_app:",
        "combo_musica:",
        "tecla:",
        "iphone:",
    )):
        return "musica"
    if comando.startswith("ver:"):
        return "vision"
    if comando.startswith("red:"):
        return "red"
    if comando.startswith("navegar:"):
        return "internet"
    if comando.startswith(("hora:","clima", "radar:", "estado_contexto:", "estado_enfoque:")):
        return "consulta"
    if comando.startswith("sistema:"):
        return "sistema"
    if comando.startswith(("preguntar:", "charla:")):
        return "charla"
    return "desconocida"


def comando_autorizado(comando, decision):
    """Impide que una ruta local salga de lo que el usuario autorizó."""
    if decision.get("clase") != "accion_explicita":
        return False
    return familia_de_comando(comando) in decision.get("familias", set())


def _parece_codigo(texto_original):
    """Código pegado se revisa como charla; nunca se convierte en una orden."""
    texto = str(texto_original or "")
    senales = (
        "\nimport ", "\nfrom ", "\ndef ", "\nclass ", "```", '"""',
        "print(", "os.getenv(", "requests.", "pyautogui.",
    )
    return len(texto) > 500 or any(senal in texto for senal in senales)


def _tiene(texto, *palabras):
    return any(
        re.search(rf"(?<!\w){re.escape(palabra)}(?!\w)", texto)
        for palabra in palabras
    )


def _es_explicacion_o_hipotesis(texto):
    """Una pregunta sobre una acción no autoriza hacerla."""
    frases = (
        "como abrir", "como se abre", "como puedo", "como hago",
        "que pasaria", "que pasaria si", "me gustaria", "me gusta",
        "no quiero", "no abras", "no apagues", "podrias explicarme",
        "puedes explicarme",
    )
    return any(frase in texto for frase in frases)


def _es_acuse(texto):
    return texto in {
        "ok", "okei", "okay", "vale", "entendido", "perfecto",
        "gracias", "bien", "muy bien", "si", "sí", "no",
    }


# Gramática pequeña y conservadora para solicitar abrir una aplicación.
# No incluye «me gustaría abrir...» ni «estaba abriendo...»: son charla.
_PETICION_APERTURA_DIRECTA = re.compile(
    r"^\s*(?:[¿¡]\s*)?"
    r"(?:(?:por\s+favor|oye(?:\s+deisy)?|deisy)\s*,?\s*)?"
    r"(?:(?:(?:me\s+)?(?:puedes|podrias)|quiero)\s+)?"
    r"(?:abre|abreme|abrir|inicia|iniciar|lanza|lanzar|ejecuta|ejecutar)\b",
    re.IGNORECASE,
)

_PETICION_APERTURA_AYUDADA = re.compile(
    r"^\s*(?:[¿¡]\s*)?"
    r"(?:(?:por\s+favor|oye(?:\s+deisy)?|deisy)\s*,?\s*)?"
    r"(?:(?:me\s+)?ayudas|ayudame)(?:\s+a)?\s+"
    r"(?:abrir|abriendo|iniciar|iniciando|lanzar|lanzando|ejecutar|ejecutando)\b",
    re.IGNORECASE,
)


def es_peticion_apertura(texto: object) -> bool:
    """Reconoce una orden de apertura, no una charla sobre abrir algo."""
    normal = normalizar(texto).lstrip("¿¡ ")
    return bool(
        _PETICION_APERTURA_DIRECTA.match(normal)
        or _PETICION_APERTURA_AYUDADA.match(normal)
)


def _accion_musica(texto):
    """Devuelve un comando seguro y reversible, o cadena vacía."""
    if _tiene(texto, "pausa", "pausar"):
        return "tecla: pausa"
    if _tiene(texto, "reanuda", "reanudar", "continuar"):
        return "tecla: play"
    if "siguiente" in texto or "proxima cancion" in texto:
        return "tecla: siguiente"
    if "anterior" in texto or "atras" in texto:
        return "tecla: anterior"
    if _tiene(texto, "pon", "poner", "reproduce", "reproducir") and (
        "musica" in texto or "spotify" in texto or "cancion" in texto
    ):
        # Señal multimedia global y reversible. No promete que Spotify haya
        # empezado a sonar ni abre una ruta fuera del inventario.
        return "tecla: play"
    return ""


def _familias_explicitas(texto):
    """Permite únicamente acciones con verbo y objeto suficientemente claros."""
    familias = set()
    if _es_explicacion_o_hipotesis(texto):
        return familias

    # "abre LOL" sí; "me gusta LOL" no.
    if es_peticion_apertura(texto):
        familias.add("apps")

    if _accion_musica(texto):
        familias.add("musica")

    if (
        ("volumen" in texto and _tiene(texto, "sube", "subir", "baja", "bajar", "aumenta", "disminuye"))
        or ("ventanas" in texto and _tiene(texto, "muestra", "dime", "ensena", "enseña", "lista"))
        or _tiene(texto, "apaga", "apagar", "reinicia", "reiniciar")
    ):
        familias.add("sistema")

    if (
        ("pantalla" in texto and _tiene(texto, "ve", "ver", "mira", "mirar", "analiza", "analizar", "dime"))
        or "que estoy viendo" in texto
    ):
        familias.add("vision")

    # Mencionar "red" no basta: debe pedir escanear/listar/mostrar.
    if (
        ("red" in texto or "dispositivos" in texto)
        and _tiene(texto, "escanea", "escanear", "lista", "listar", "muestra", "mostrar")
    ):
        familias.add("red")

    if _tiene(texto, "busca", "buscar", "investiga", "investigar", "navega", "navegar"):
        familias.add("internet")

    if (
        "hora" in texto
        or "en que modo estoy" in texto
        or "donde estoy" in texto
        or texto == "radar"
        # Consultas locales no ejecutan dispositivos, pero deben conservar sus
        # bypasses deterministas existentes en api_deisy.py.
        or "clima" in texto
        or "temperatura" in texto
        or "que puedes hacer" in texto
        or "que sabes hacer" in texto
        or "tus funciones" in texto
        or "tus capacidades" in texto
        or "que enfoque tengo" in texto
        or "estado del iphone 11" in texto
        or "estado de mi iphone 11" in texto
        or (
            "localiza" in texto
            and any(x in texto for x in ("iphone", "telefono", "movil", "dispositivo"))
        )
        or (
            "donde esta" in texto
            and any(x in texto for x in ("iphone", "telefono", "movil", "dispositivo"))
        )
    ):
        familias.add("consulta")

    return familias


def _guardar_aclaracion(sesion, tipo, pregunta, comando=""):
    estado = _estado(sesion)
    with _bloqueo:
        # Una aclaración explícita tiene prioridad y reemplaza una oferta vieja.
        estado["oferta"] = None
        estado["aclaracion"] = {
            "tipo": tipo,
            "pregunta": pregunta,
            "comando": comando,
            "caduca": time.monotonic() + TTL_ACLARACION,
        }


def _normalizar_respuesta_oferta(texto):
    """Normaliza respuestas cortas como «Sí, por favor» sin interpretar frases largas."""
    texto = normalizar(texto)
    return re.sub(r"[¿?¡!.,;:]+", "", texto).strip()


def registrar_oferta_basica(sesion, codigo):
    """Registra una oferta local cerrada y devuelve su texto visible.

    No ejecuta nada. Si ya hay una aclaración u oferta en la sesión, no pisa
    ese estado: evita competir con «¿Omen o iPhone?».
    """
    plantilla = _OFERTAS_BASICAS.get(str(codigo or "").strip())
    if not plantilla:
        return ""

    acciones = tuple(plantilla.get("acciones", ()))
    familias = frozenset(plantilla.get("familias", ()))
    # Fallo cerrado: no aceptamos que una ampliación futura convierta un «sí»
    # en dos órdenes, ni que introduzca una familia fuera del catálogo R1.
    if (
        len(acciones) != 1
        or acciones[0] not in _ACCIONES_BASICAS_PERMITIDAS
        or familias != frozenset({"apps"})
    ):
        return ""

    estado = _estado(sesion)
    with _bloqueo:
        if estado.get("aclaracion") or estado.get("oferta"):
            return ""
        estado["oferta"] = {
            "origen": "oferta_local",
            "acciones": acciones,
            "familias": familias,
            "caduca": time.monotonic() + TTL_OFERTA_BASICA,
        }
    return str(plantilla["texto"])


def cancelar_oferta_basica(sesion):
    """Elimina una oferta pendiente; se usa al recibir otra orden clara."""
    estado = _estado(sesion)
    with _bloqueo:
        estado["oferta"] = None


def resolver_oferta_basica(sesion, texto):
    """Consume una aceptación estricta de una oferta local visible.

    Devuelve `None` si no había oferta o si llegó una frase nueva no relacionada.
    En ese último caso también borra la oferta para que un «sí» posterior no
    pueda reactivar algo antiguo.
    """
    estado = _estado(sesion)
    respuesta = _normalizar_respuesta_oferta(texto)

    with _bloqueo:
        oferta = estado.get("oferta")
        if not oferta:
            return None

        if respuesta in _CANCELACIONES_OFERTA:
            estado["oferta"] = None
            return {
                "estado": "cancelada",
                "respuesta": "De acuerdo, no abriré Spotify.",
            }

        if respuesta in _ACEPTACIONES_OFERTA:
            # La reclamamos ANTES de devolverla: un segundo mensaje duplicado
            # de Telegram ya no podrá disparar una segunda ejecución.
            estado["oferta"] = None
            return {
                "estado": "aceptada",
                "acciones": tuple(oferta["acciones"]),
                "familias": set(oferta["familias"]),
            }

        if respuesta == "cualquiera":
            estado["oferta"] = None
            return {
                "estado": "aclarar",
                "respuesta": (
                    "Para esta oferta solo puedo abrir Spotify; aún no tengo una "
                    "playlist predeterminada. Si quieres abrirlo, responde «sí»."
                ),
            }

        # Una frase nueva cambia el tema y cancela la oferta vieja. No devolvemos
        # error: api_deisy.py podrá tratarla como conversación u orden normal.
        estado["oferta"] = None
        return None


def _resolver_aclaracion(sesion, texto):
    """Solo resuelve aclaraciones seguras y recientes de ESA misma sesión."""
    estado = _estado(sesion)
    pendiente = estado.get("aclaracion")
    if not pendiente:
        return None

    if texto in {"olvida", "dejalo", "déjalo", "da igual", "cambiemos"}:
        with _bloqueo:
            estado["aclaracion"] = None
        return _decision("charla")

    # Un "sí" jamás se vuelve una orden de dispositivo.
    if _es_acuse(texto):
        return _decision(
            "ambigua",
            respuesta=(
                "Para no asumir una acción, necesito una respuesta concreta. "
                f"{pendiente['pregunta']}"
            ),
        )
        

    # Solo se acepta destino explícito para música: es reversible y la
    # pregunta anterior enumera exactamente lo que ocurrirá.
    if pendiente["tipo"] == "destino_musica":
        if "omen" in texto:
            comando = pendiente["comando"]
            with _bloqueo:
                estado["aclaracion"] = None
            return _decision(
                "accion_explicita", familias={"apps"}, comando_preparado=comando
            )
        if "iphone" in texto:
            return _decision(
                "ambigua",
                respuesta=(
                    "Aún no controlo la reproducción del iPhone desde aquí. "
                    "¿Quieres ejecutar esa acción en la Omen?"
                ),
            )

    # Diagnóstico de conexión: da contexto a una charla, nunca dispara un scan.
    if pendiente["tipo"] == "conexion_iphone":
        opciones = {
            "atajo": "El usuario aclara que el problema se refiere al Atajo del iPhone.",
            "tailscale": "El usuario aclara que el problema se refiere a Tailscale.",
            "red": "El usuario aclara que el problema se refiere a la conexión de red.",
        }
        for palabra, contexto in opciones.items():
            if palabra in texto:
                with _bloqueo:
                    estado["aclaracion"] = None
                return _decision("charla", contexto=contexto)
    return None


def clasificar_entrada(mensaje, sesion):
    """Punto de entrada: debe correr antes de alias, bypasses y router."""
    original = str(mensaje or "")
    texto = normalizar(original)
    if not texto:
        return _decision("charla")

    familias = _familias_explicitas(texto)
    if familias:
        # Una orden nueva reemplaza una pregunta pendiente antigua.
        estado = _estado(sesion)
        with _bloqueo:
            estado["aclaracion"] = None
            estado["oferta"] = None
    else:
        continuacion = _resolver_aclaracion(sesion, texto)
        if continuacion is not None:
            return continuacion

    if _parece_codigo(original):
        return _decision("charla", contexto="El usuario ha compartido código para revisión.")
    if _es_acuse(texto) or _es_explicacion_o_hipotesis(texto):
        return _decision("charla")

    if "iphone" in texto and any(frase in texto for frase in (
        "no se conecta", "no conecta", "no funciona", "no responde",
    )):
        pregunta = "¿Quieres revisar el Atajo del iPhone, Tailscale o la red?"
        _guardar_aclaracion(sesion, "conexion_iphone", pregunta)
        return _decision("ambigua", respuesta=pregunta)

    if "encontrar" in texto and any(palabra in texto for palabra in (
        "persona", "alguien", "amigo", "chica", "chico",
    )):
        return _decision(
            "ambigua",
            respuesta=(
                "¿Te refieres a buscar información pública o a localizar uno de "
                "tus dispositivos autorizados? No puedo rastrear personas."
            ),
        )

    comando_musica = _accion_musica(texto)
    if comando_musica and "omen" not in texto and "iphone" not in texto:
        pregunta = "¿Dónde quieres la música: en la Omen o en el iPhone?"
        _guardar_aclaracion(sesion, "destino_musica", pregunta, comando_musica)
        return _decision("ambigua", respuesta=pregunta)

    if familias:
        return _decision("accion_explicita", familias=familias)

    # Si menciona una capacidad sin pedirla, no hacemos inferencias.
    if any(palabra in texto for palabra in (
        "abrir", "apagar", "volumen", "spotify", "red", "omen", "pantalla",
    )):
        return _decision(
            "ambigua",
            respuesta=(
                "No quiero interpretar una mención como una orden. "
                "¿Quieres que haga alguna acción concreta?"
            ),
        )
    return _decision("charla")


def registrar_turno_chat(sesion, usuario, respuesta):
    """Guarda contexto efímero de conversación, nunca permisos de acción."""
    estado = _estado(sesion)
    usuario = str(usuario or "").strip()
    respuesta = str(respuesta or "").strip()
    if _parece_codigo(usuario) or re.search(
        r"(api[_ -]?key|token|password|contrasena)\s*=", usuario, flags=re.IGNORECASE
    ):
        usuario = "[El usuario compartió contenido técnico o sensible.]"
    with _bloqueo:
        estado["turnos"].append({
            "usuario": usuario[:700],
            "deisy": respuesta[:700],
            "cuando": time.monotonic(),
        })


def contexto_chat_reciente(sesion):
    """Contexto para la charla final; NUNCA se entrega al router de acciones."""
    estado = _estado(sesion)
    if not estado["turnos"]:
        return ""
    lineas = ["[CONTEXTO BREVE DE ESTA CONVERSACIÓN]"]
    for turno in estado["turnos"]:
        lineas.append(f"Eddie: {turno['usuario']}")
        lineas.append(f"Deisy: {turno['deisy']}")
    lineas.append(
        "Este contexto sirve solo para continuar una charla. "
        "Nunca deduzcas ni ejecutes acciones a partir de él."
    )
    return "\n".join(lineas)