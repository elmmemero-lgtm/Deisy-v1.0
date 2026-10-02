"""
📍 PROACTIVO — Daisy te habla al móvil según tu ubicación.

Cuando el iPhone le dice a la HP dónde estás (vía un Atajo), Daisy decide si
vale la pena mandarte un tip de esa zona (un sitio para comer, algo que ver...)
acorde a tu perfil. Con anti-spam: SOLO avisa cuando CAMBIAS de zona, no cada
vez, para no volverse pesada.

CONFIG en el .env:
    PROACTIVO_UBICACION=1        # 1 = activado, 0 = desactivado
    PROACTIVO_COOLDOWN=1800      # segundos mínimos entre avisos (1800 = 30 min)
"""

import os
import json
import time

from dotenv import load_dotenv
from ddgs import DDGS

from cerebro import client, obtener_modelo_activo, leer_perfil
from notificador import notificar

load_dotenv()

ACTIVO = os.getenv("PROACTIVO_UBICACION", "1") == "1"
COOLDOWN = int(os.getenv("PROACTIVO_COOLDOWN", "1800"))
ESTADO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado_proactivo.json")


def _cargar():
    try:
        with open(ESTADO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _guardar(datos):
    try:
        with open(ESTADO_FILE, "w", encoding="utf-8") as f:
            json.dump(datos, f)
    except Exception as e:
        print(f"⚠️ No pude guardar el estado proactivo: {e}")


def _zona(ubicacion):
    """Clave gruesa de la zona: palabras largas (ciudad/sitio), sin números ni orden."""
    solo_letras = "".join(c if (c.isalpha() or c.isspace()) else " " for c in ubicacion)
    palabras = [w.lower() for w in solo_letras.split() if len(w) > 3]
    return " ".join(sorted(set(palabras)))


def _deberia_avisar(ubicacion):
    """Avisa solo si has CAMBIADO de zona y pasó el cooldown (anti-spam)."""
    estado = _cargar()
    zona_actual = _zona(ubicacion)
    ahora = time.time()

    misma_zona = estado.get("zona") == zona_actual
    reciente = (ahora - estado.get("ts", 0)) < COOLDOWN
    if misma_zona or reciente:
        return False
    return True


def _buscar(consulta):
    try:
        resultados = DDGS().text(consulta, max_results=3)
        return " ".join(r["body"] for r in resultados) if resultados else ""
    except Exception:
        return ""


def _generar_tip(ubicacion):
    """Crea un mensaje corto y con chispa sobre la zona, según el perfil de Eddie."""
    contexto = _buscar(f"qué hacer, qué ver y dónde comer cerca de {ubicacion}")
    perfil = leer_perfil()

    prompt = f"""Eres Daisy, la asistente y compañera de Eddie. Acaba de llegar a: {ubicacion}.
INFO DE INTERNET SOBRE LA ZONA: {contexto}
{('GUSTOS DE EDDIE (perfil): ' + perfil) if perfil else ''}

Escríbele UN mensaje corto para el móvil (máximo 2 frases), proponiéndole algo
CONCRETO y útil de ESA zona acorde a sus gustos: un sitio para comer, algo que
ver o un plan. Nada de saludos largos ni listas. Directo, natural y con chispa."""

    try:
        respuesta = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=obtener_modelo_activo(),
            max_tokens=140,
        )
        return respuesta.choices[0].message.content.strip()
    except Exception as e:
        print(f"⚠️ No pude generar el tip: {e}")
        return ""


def avisar_por_ubicacion(ubicacion):
    """Punto de entrada: recibe la ubicación y, si toca, manda el tip al móvil."""
    if not ACTIVO or not ubicacion:
        return False
    if not _deberia_avisar(ubicacion):
        return False

    tip = _generar_tip(ubicacion)
    if not tip:
        return False

    ok = notificar(tip, titulo="Deisy", prioridad="default", tags=["round_pushpin"])
    if ok:
        _guardar({"zona": _zona(ubicacion), "ts": time.time()})
        print(f"📍 Tip de zona enviado para: {ubicacion}")
    return ok
