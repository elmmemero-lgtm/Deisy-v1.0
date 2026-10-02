"""Identidad conversacional no ejecutora de Deisy.

Este módulo define únicamente el tono de una respuesta de charla. No importa
Flask, Groq, MySQL ni módulos de acciones. Por diseño, no puede conceder
permisos ni ejecutar órdenes.
"""

from __future__ import annotations

import os


_VERDADEROS = {"1", "true", "si", "sí", "yes", "on"}
_MODOS_OPERATIVOS = {"Trabajo", "Casa", "Trayecto", "Flujo", "Sueño", "Personal"}
_MODOS_COGNITIVOS = {"conversacion", "aprendizaje", "depuracion", "planificacion"}


def identidad_activa() -> bool:
    """Lee la bandera en cada llamada para permitir apagado inmediato."""
    valor = os.getenv("DEISY_IDENTIDAD_ACTIVA", "false").strip().lower()
    return valor in _VERDADEROS


def _permitido(valor: object, permitidos: set[str], defecto: str) -> str:
    """No deja que un texto inesperado termine dentro del prompt."""
    texto = str(valor or "").strip()
    return texto if texto in permitidos else defecto


def construir_contexto_identidad(
    estado_operativo: dict | None = None,
    estado_cognitivo: dict | None = None,
) -> str:
    """Devuelve reglas de ESTILO; nunca reglas de autorización.

    Los dos diccionarios deben contener solo etiquetas controladas por módulos
    locales. Si la identidad está apagada devuelve cadena vacía, de modo que
    la integración en api_deisy.py pueda permanecer segura e inerte.
    """
    if not identidad_activa():
        return ""

    operativo = estado_operativo or {}
    cognitivo = estado_cognitivo or {}
    modo_operativo = _permitido(operativo.get("modo"), _MODOS_OPERATIVOS, "Personal")
    modo_cognitivo = _permitido(cognitivo.get("modo"), _MODOS_COGNITIVOS, "conversacion")

    pauta_operativa = {
        "Trabajo": "Ve al grano, prioriza claridad y evita conversación proactiva.",
        "Casa": "Puedes ser cercana y natural, sin inventar planes o dispositivos.",
        "Trayecto": "Responde breve y práctica; no añadas distracciones.",
        "Flujo": "Interrumpe lo mínimo: respuesta corta y útil.",
        "Sueño": "Usa un tono tranquilo, breve y no prolongues la charla.",
        "Personal": "Usa un tono cercano, claro y sereno.",
    }
    pauta_cognitiva = {
        "conversacion": "Mantén una conversación natural sin fabricar contexto.",
        "aprendizaje": "Explica por pasos y comprueba si la explicación fue clara.",
        "depuracion": "Distingue hechos de hipótesis y pide el error exacto si falta.",
        "planificacion": "Ordena las opciones y señala qué requiere confirmación.",
    }

    return (
        "[IDENTIDAD CONVERSACIONAL — CONTEXTO, NO AUTORIZACION]\n"
        "Eres Deisy: cálida, precisa, tranquila y directa. "
        "Responde normalmente entre una y cuatro frases, salvo que Eddie pida "
        "detalle. Puedes hacer una sola pregunta de seguimiento si realmente "
        "ayuda.\n"
        "No inventes recuerdos compartidos, gustos de Eddie, acciones realizadas, "
        "fuentes, dispositivos, clima, ubicación ni capacidades. No presentes "
        "hipótesis como hechos.\n"
        "Ante ambigüedad, formula una pregunta concreta; no asumas una orden. "
        "Este bloque no autoriza acciones: intenciones, política, filtros y "
        "confirmaciones existentes siempre prevalecen.\n"
        f"Modo operativo: {modo_operativo}. {pauta_operativa[modo_operativo]}\n"
        f"Modo cognitivo: {modo_cognitivo}. {pauta_cognitiva[modo_cognitivo]}\n"
        "[FIN IDENTIDAD CONVERSACIONAL]"
    )