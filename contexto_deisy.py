"""Contexto persistente de Deisy: situación del iPhone 17 + enfoque del iPhone 11."""

from __future__ import annotations

import re
import unicodedata
import json
from datetime import datetime
from pathlib import Path
from threading import RLock


_bloqueo = RLock()
_archivo_estado = Path(__file__).with_name("estado_contexto_deisy.json")
_ultimo_aviso_sueno = None

# Casa queda por compatibilidad con automatizaciones antiguas. En tu esquema
# actual rednoparati activa el enfoque Flujo, que tiene prioridad sobre Trayecto.
SITUACIONES_VALIDAS = {"Trabajo", "Casa", "Trayecto", "Desconocido"}
ENFOQUES_VALIDOS = {"Sueño", "Flujo", "No molestar", "Personal"}


def _fecha(valor):
    try:
        return datetime.fromisoformat(str(valor)) if valor else None
    except (TypeError, ValueError):
        return None


def _bloque_valido(datos, defecto):
    if not isinstance(datos, dict):
        return dict(defecto)
    return {
        "valor": str(datos.get("valor", defecto["valor"])),
        "origen": str(datos.get("origen", ""))[:80],
        "cuando": _fecha(datos.get("cuando")),
    }


def _cargar_estado():
    situacion_defecto = {"valor": "Desconocido", "origen": "", "cuando": None}
    enfoque_defecto = {"valor": "Personal", "origen": "", "cuando": None}
    try:
        datos = json.loads(_archivo_estado.read_text(encoding="utf-8"))
        situacion = _bloque_valido(datos.get("situacion"), situacion_defecto)
        enfoque = _bloque_valido(datos.get("enfoque"), enfoque_defecto)
        if situacion["valor"] not in SITUACIONES_VALIDAS:
            situacion = situacion_defecto
        if enfoque["valor"] not in ENFOQUES_VALIDOS:
            enfoque = enfoque_defecto
        print("🧭 Contexto anterior restaurado desde disco.")
        return situacion, enfoque
    except FileNotFoundError:
        return situacion_defecto, enfoque_defecto
    except Exception as error:
        print(f"⚠️ No pude restaurar el contexto anterior: {error}")
        return situacion_defecto, enfoque_defecto


_situacion, _enfoque = _cargar_estado()


def _guardar_estado():
    datos = {
        "situacion": {**_situacion, "cuando": _situacion["cuando"].isoformat() if _situacion["cuando"] else None},
        "enfoque": {**_enfoque, "cuando": _enfoque["cuando"].isoformat() if _enfoque["cuando"] else None},
    }
    temporal = _archivo_estado.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    temporal.replace(_archivo_estado)


def _normalizar(valor: object) -> str:
        texto = unicodedata.normalize("NFD", str(valor or "").casefold())
        texto = "".join(
            caracter
            for caracter in texto
            if unicodedata.category(caracter) != "Mn"
        )
        return re.sub(r"\s+", " ", texto).strip()


def normalizar_contexto(tipo: object, valor: object) -> tuple[str, str]:
        """Valida y devuelve el valor canónico usado por todos los endpoints."""
        tipo_normalizado = _normalizar(tipo)
        valor_normalizado = _normalizar(valor)

        situaciones = {
            "trabajo": "Trabajo",
            "casa": "Casa",
            "trayecto": "Trayecto",
            "desconocido": "Desconocido",
        }
        enfoques = {
            "sueno": "Sueño",
            "flujo": "Flujo",
            "no molestar": "No molestar",
            "personal": "Personal",
        }

        if tipo_normalizado == "situacion":
            resultado = situaciones.get(valor_normalizado)
            if resultado:
                return "situacion", resultado
            raise ValueError("Situación no permitida")

        if tipo_normalizado == "enfoque":
            resultado = enfoques.get(valor_normalizado)
            if resultado:
                return "enfoque", resultado
            raise ValueError("Enfoque no permitido")

        raise ValueError("Tipo inválido: usa situacion o enfoque")


def actualizar_contexto(tipo, valor, origen="iPhone"):
    """Actualiza una fuente sin borrar la otra y persiste el resultado."""
    global _situacion, _enfoque, _ultimo_aviso_sueno
    tipo, valor = normalizar_contexto(tipo, valor)
    origen = str(origen or "iPhone").strip()[:80]
    ahora = datetime.now()
    with _bloqueo:
        if tipo == "situacion":
            if valor not in SITUACIONES_VALIDAS:
                raise ValueError("Situación no permitida")
            _situacion = {"valor": valor, "origen": origen, "cuando": ahora}
        elif tipo == "enfoque":
            if valor not in ENFOQUES_VALIDOS:
                raise ValueError("Enfoque no permitido")
            if valor == "Sueño" and _enfoque["valor"] != "Sueño":
                _ultimo_aviso_sueno = None
            _enfoque = {"valor": valor, "origen": origen, "cuando": ahora}
        else:
            raise ValueError("Tipo inválido: usa situacion o enfoque")
        _guardar_estado()
    return estado_actual()


def estado_actual():
    """Calcula prioridad acordada: Sueño > Trabajo > Flujo > Casa > Trayecto."""
    with _bloqueo:
        situacion = dict(_situacion)
        enfoque = dict(_enfoque)
    if enfoque["valor"] == "Sueño":
        modo = "Sueño"
    elif situacion["valor"] == "Trabajo":
        modo = "Trabajo"
    elif enfoque["valor"] == "Flujo":
        # rednoparati usa Flujo: no se deja tapar por el Trayecto anterior.
        modo = "Flujo"
    elif situacion["valor"] == "Casa":
        modo = "Casa"
    elif situacion["valor"] == "Trayecto":
        modo = "Trayecto"
    elif enfoque["valor"] == "No molestar":
        modo = "No molestar"
    else:
        modo = "Personal"
    return {"modo": modo, "situacion": situacion, "enfoque": enfoque}


def _antiguedad(fecha):
    if fecha is None:
        return "sin fecha"
    minutos = max(0, int((datetime.now() - fecha).total_seconds() // 60))
    if minutos < 1:
        return "hace un momento"
    if minutos < 60:
        return f"hace {minutos} min"
    return f"hace {minutos // 60} h"


def respuesta_estado_contexto():
    estado = estado_actual()
    return (
        f"Mi último contexto recibido indica {estado['modo']}: situación "
        f"{estado['situacion']['valor']} ({_antiguedad(estado['situacion']['cuando'])}) "
        f"y enfoque {estado['enfoque']['valor']} ({_antiguedad(estado['enfoque']['cuando'])})."
    )


def contexto_para_modelo():
    estado = estado_actual()
    reglas = {
        "Trabajo": "Responde breve, práctica y sin charla proactiva.",
        "Casa": "Tono normal; ayuda con ocio, música u ordenador solo si se pide.",
        "Trayecto": "Responde corto y no propongas acciones físicas por iniciativa propia.",
        "Flujo": "Sé concisa y evita sugerencias o preguntas adicionales.",
        "Sueño": "Tono tranquilo; responde lo necesario y no alargues la charla.",
        "No molestar": "Sé especialmente breve y no inicies conversación proactiva.",
        "Personal": "Comportamiento normal, claro y respetuoso.",
    }
    return (
        "[CONTEXTO RECIBIDO, NO DEDUCIDO]\n"
        f"Situación: {estado['situacion']['valor']}.\n"
        f"Enfoque: {estado['enfoque']['valor']}.\n"
        f"Modo efectivo: {estado['modo']}.\n"
        f"Regla: {reglas[estado['modo']]}\n"
        "No presentes este contexto como GPS en directo ni como certeza sobre actividad real.\n"
        "[FIN CONTEXTO]"
    )


def sugerencia_sueno_si_toca():
    """Solo avisa al recibir otra petición tras 20 min de Sueño."""
    global _ultimo_aviso_sueno
    estado = estado_actual()
    inicio = estado["enfoque"]["cuando"]
    if estado["modo"] != "Sueño" or inicio is None:
        return ""
    ahora = datetime.now()
    if (ahora - inicio).total_seconds() < 20 * 60:
        return ""
    if _ultimo_aviso_sueno and (ahora - _ultimo_aviso_sueno).total_seconds() < 90 * 60:
        return ""
    _ultimo_aviso_sueno = ahora
    return "Sigues con Sueño activo. ¿Cerramos por hoy o preparo una alarma?"
