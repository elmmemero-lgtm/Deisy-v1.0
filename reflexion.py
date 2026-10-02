"""Reflexión local no persistente de Deisy.

Detecta únicamente propuestas explícitas para revisar más adelante. No usa
Groq, no guarda nada, no modifica alias/código/configuración y no ejecuta.
"""

from __future__ import annotations

import os
import re
import unicodedata


_VERDADEROS = {"1", "true", "si", "sí", "yes", "on"}
_PATRON_SENSIBLE = re.compile(
    r"(?i)\b(?:api[_ -]?key|groq_api_key|token|password|contrase(?:n|ñ)a|secret|bearer)\b"
)
_PATRON_CODIGO = re.compile(
    r"(?m)^\s*(?:from|import|def|class)\s+|```|\"\"\"|'''"
)


def reflexion_activa() -> bool:
    """Bandera independiente: observar no equivale a aprender."""
    valor = os.getenv("DEISY_REFLEXION_ACTIVA", "false").strip().lower()
    return valor in _VERDADEROS


def _normalizar(texto: object) -> str:
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


def _evidencia_redactada(texto: object) -> str:
    """No manda fragmentos sensibles o código a la consola de reflexión."""
    bruto = str(texto or "").strip()
    if not bruto:
        return ""
    if len(bruto) > 320 or _PATRON_SENSIBLE.search(bruto) or _PATRON_CODIGO.search(bruto):
        return "[contenido tecnico o sensible omitido]"
    return re.sub(r"\s+", " ", bruto)[:240]


def proponer_reflexion(
    mensaje: object,
    respuesta: object = "",
    modulo: object = "",
    exito: bool = True,
) -> dict | None:
    """Devuelve una propuesta; el llamador decide solo imprimirla.

    `respuesta`, `modulo` y `exito` se reciben para tener contexto de auditoría,
    pero esta función no usa el texto de la respuesta para inferir nuevos hechos.
    """
    if not reflexion_activa():
        return None

    texto = _normalizar(mensaje)
    evidencia = _evidencia_redactada(mensaje)
    if not texto or not evidencia:
        return None

    tipo = ""
    propuesta = ""
    if any(frase in texto for frase in (
        "no, queria", "no queria", "me referia a", "eso no era",
        "te equivocaste", "te has equivocado",
    )):
        tipo = "correccion"
        propuesta = "Revisar manualmente la interpretación; no cambiar reglas sin aprobación."
    elif any(frase in texto for frase in (
        "quiero que puedas", "me gustaria que pudieras", "seria bueno que",
        "anade una funcion", "añade una función",
    )):
        tipo = "funcion_futura"
        propuesta = "Evaluar la capacidad como mejora futura; no implementarla automáticamente."
    elif any(frase in texto for frase in (
        "prefiero que", "no me gusta que", "me gusta que", "llamame",
    )):
        tipo = "preferencia"
        propuesta = "Pedir aprobación antes de convertir esta preferencia en memoria permanente."

    if not tipo:
        return None

    return {
        "estado": "propuesta_no_aplicada",
        "tipo": tipo,
        "evidencia": evidencia,
        "propuesta": propuesta,
        "modulo_origen": str(modulo or "")[:80],
        "resultado_previo_exitoso": bool(exito),
        "requiere_aprobacion": True,
    }


def formatear_propuesta(propuesta: dict) -> str:
    """Produce un log claro; no persiste ni ejecuta la propuesta."""
    return (
        "🪞 Reflexion propuesta (NO aplicada) | "
        f"tipo={propuesta.get('tipo', 'desconocido')} | "
        f"evidencia={propuesta.get('evidencia', '')!r} | "
        f"propuesta={propuesta.get('propuesta', '')}"
    )