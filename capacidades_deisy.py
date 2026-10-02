"""Inventario honesto de lo que Deisy puede y no puede hacer.

Este inventario no ejecuta nada. Sirve para responder de forma consistente,
evitar promesas falsas y decidir qué solicitudes pueden guardarse como mejoras.
"""

from __future__ import annotations

import re
import unicodedata


def normalizar(texto: object) -> str:
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


# «Implementada» significa que hay un manejador. No garantiza que el equipo,
# Internet o la Omen estén conectados en ese momento.
CAPACIDADES = {
    "hora": {
        "nombre": "decir la hora de la HP",
        "estado": "implementada",
    },
    "clima": {
        "nombre": "consultar clima y previsión con Open-Meteo",
        "estado": "implementada",
        "alternativa": "Si la fuente no responde, diré que no tengo un dato verificado.",
    },
    "noticias": {
        "nombre": "leer titulares RSS",
        "estado": "implementada",
    },
    "internet": {
        "nombre": "preparar enlaces o investigar explícitamente en Internet",
        "estado": "implementada",
        "alternativa": "No abro automáticamente el navegador ni presento una búsqueda como un hecho verificado.",
    },
    "contexto": {
        "nombre": "consultar contexto y enfoque comunicados por tus iPhone",
        "estado": "implementada",
        "alternativa": "No equivale a GPS en directo.",
    },
    "radar": {
        "nombre": "consultar la última coordenada que un Atajo autorizó enviar",
        "estado": "implementada",
        "alternativa": "No localiza nada hasta recibir coordenadas válidas.",
    },
    "memoria": {
        "nombre": "citar mensajes anteriores que tú escribiste y que MySQL guardó",
        "estado": "implementada",
        "alternativa": "No convierte respuestas antiguas de la IA en hechos.",
    },
    "red": {
        "nombre": "censar dispositivos de la red de la HP",
        "estado": "implementada",
    },
    "omen": {
        "nombre": "mandar órdenes registradas a la Omen",
        "estado": "implementada",
        "alternativa": "Solo confirmo la acción si el brazo de la Omen responde correctamente.",
    },
    "acciones_omen": {
        "nombre": "abrir aplicaciones inventariadas, controlar volumen y multimedia, listar ventanas y analizar pantalla bajo petición",
        "estado": "implementada",
        "alternativa": "El apagado de Omen exige una confirmación explícita y puede estar en modo seco.",
    },
    "spotify": {
        "nombre": "abrir Spotify o un enlace de Spotify en la Omen",
        "estado": "implementada",
        "alternativa": "Abrir Spotify no confirma que una canción esté reproduciéndose.",
    },
    "vision": {
        "nombre": "analizar una foto o captura enviada explícitamente",
        "estado": "implementada",
        "alternativa": "La visión automática permanece apagada por privacidad.",
    },
    "cola_iphone": {
        "nombre": "dejar una orden para que un Atajo del iPhone la recoja",
        "estado": "implementada",
        "alternativa": "Encolada no equivale a ejecutada por el iPhone.",
    },
    # Capacidades futuras: se guardan SOLO cuando se piden de forma explícita.
    "notificaciones_windows": {
        "nombre": "leer notificaciones de Windows",
        "estado": "pendiente",
        "aliases": ("notificaciones", "mis avisos", "lee mis notificaciones"),
        "alternativa": "Aún no hay un recolector real del Centro de notificaciones.",
    },
    "control_siri": {
        "nombre": "controlar Siri directamente",
        "estado": "pendiente",
        "aliases": ("siri", "controla siri", "habla con siri"),
        "alternativa": "Lo más cercano hoy es dejar una orden para un Atajo del iPhone.",
    },
    "domotica": {
        "nombre": "controlar domótica",
        "estado": "pendiente",
        "aliases": ("domotica", "domótica", "luces", "enchufe inteligente"),
        "alternativa": "Aún no hay una integración de Home Assistant, HomeKit o similar.",
    },
    "trafico": {
        "nombre": "consultar tráfico en tiempo real",
        "estado": "pendiente",
        "aliases": ("trafico", "tráfico", "atasco", "carretera"),
        "alternativa": "Todavía no hay una fuente de tráfico configurada.",
    },
}


def detectar_capacidad_pendiente(peticion: object) -> dict | None:
    """Devuelve solo una capacidad futura conocida; nunca una implementada."""
    texto = normalizar(peticion)
    for capacidad in CAPACIDADES.values():
        if capacidad.get("estado") != "pendiente":
            continue
        if any(normalizar(alias) in texto for alias in capacidad.get("aliases", ())):
            return capacidad
    return None


def parece_peticion_de_mejora(peticion: object) -> bool:
    """Distingue una mejora explícita de una conversación normal.

    Este límite evita que «hola» o «¿cómo estás?» acaben en la lista de tareas.
    """
    texto = normalizar(peticion)
    marcadores = (
        "quiero que puedas", "me gustaria que pudieras", "anade una funcion",
        "agrega una funcion", "implementa", "desarrolla una funcion",
        "te falta poder", "guarda como mejora", "anota como mejora",
        "puedes leer mis notificaciones", "puedes controlar siri",
    )
    return any(marcador in texto for marcador in marcadores)


def texto_capacidades() -> str:
    """Resumen corto y humano para «¿qué puedes hacer?».

    No enumera internals ni promete capacidades pendientes.
    """
    return (
        "Puedo ayudarte con conversación, hora, clima verificado, titulares, "
        "memoria de tus mensajes guardados, contexto de tus iPhone, radar, red, "
        "y acciones concretas en la Omen: aplicaciones inventariadas, Spotify, "
        "volumen, multimedia, ventanas y visión cuando me la pides. "
        "Aún no leo notificaciones de Windows ni controlo Siri, domótica o tráfico en tiempo real."
    )


def contexto_capacidades_para_modelo() -> str:
    """Contrato que ve Groq; Python sigue siendo la autoridad final."""
    hechas = [c["nombre"] for c in CAPACIDADES.values() if c["estado"] == "implementada"]
    pendientes = [c["nombre"] for c in CAPACIDADES.values() if c["estado"] == "pendiente"]
    return (
        "[CAPACIDADES REALES DE DEISY]\n"
        "Implementadas: " + "; ".join(hechas) + ".\n"
        "Pendientes: " + "; ".join(pendientes) + ".\n"
        "No afirmes que una pendiente está disponible. Si una petición ambigua se "
        "parece a una capacidad real, pide una aclaración breve; no inventes una acción.\n"
        "[FIN CAPACIDADES REALES]"
    )
