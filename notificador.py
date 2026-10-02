"""
📬 NOTIFICADOR — El canal por el que Deisy te habla al móvil (vía ntfy).

ntfy es gratis y sin cuenta: tú te suscribes a un "tema" (un nombre secreto)
desde la app ntfy del iPhone, y la HP publica mensajes en ese tema.
Funciona por internet normal, así que te llegan aunque estés de viaje.

CONFIG en el .env de la HP:
    NTFY_TOPIC=deisy-eddie-un-nombre-largo-y-secreto   (¡ponlo difícil de adivinar!)
    NTFY_SERVER=https://ntfy.sh                         (opcional, por defecto ntfy.sh)
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")


def notificar(mensaje, titulo="Deisy", prioridad="default", tags=None):
    """
    Envía una notificación push al móvil.
      mensaje   -> texto (admite tildes y emojis, va en el cuerpo UTF-8)
      titulo    -> cabecera corta (mejor sin tildes; es una cabecera HTTP)
      prioridad -> min / low / default / high / urgent
      tags      -> lista de nombres de icono ntfy, ej: ["rotating_light"], ["shield"]
    Devuelve True si se envió.
    """
    if not NTFY_TOPIC:
        print("⚠️ NTFY_TOPIC no está configurado en el .env; no envío notificación.")
        return False

    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    headers = {"Title": titulo, "Priority": prioridad}
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        r = requests.post(url, data=mensaje.encode("utf-8"), headers=headers, timeout=8)
        if r.status_code == 200:
            return True
        print(f"⚠️ ntfy respondió {r.status_code}: {r.text[:120]}")
        return False
    except Exception as e:
        print(f"⚠️ No pude enviar la notificación: {e}")
        return False


if __name__ == "__main__":
    # Prueba rápida: python notificador.py
    ok = notificar(
        "¡Hola Eddie! Si ves esto en el móvil, el canal de Deisy funciona. 🎉",
        titulo="Deisy - Prueba",
        prioridad="high",
        tags=["tada"],
    )
    print("✅ Enviada." if ok else "❌ No se pudo enviar (revisa NTFY_TOPIC en el .env).")
