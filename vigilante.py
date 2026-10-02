"""
🛡️ VIGILANTE — El guardián de tu red mientras no estás.

Cada X segundos hace una "onda de red" y, si aparece un dispositivo que
NUNCA había visto, te manda una alerta al móvil por ntfy.
Ideal para viajes: sabrás si alguien se conecta a tu WiFi de casa.

Se ejecuta aparte del servidor:  python vigilante.py
(o prográmalo en el Programador de tareas de Windows para que arranque solo).

CONFIG en el .env:
    VIGILANTE_INTERVALO=300     # segundos entre rondas (300 = 5 min)
    NTFY_TOPIC=...              # el mismo tema que en notificador.py
"""

import os
import time
from dotenv import load_dotenv

from onda_red import censar
from notificador import notificar

load_dotenv()
INTERVALO = int(os.getenv("VIGILANTE_INTERVALO", "300"))
# Cada cuántas rondas mandar un resumen "todo tranquilo" aunque no haya intrusos.
# 0 = nunca (solo avisa de intrusos). Ej: con INTERVALO=300 y RESUMEN_CADA=288 -> 1 vez/día.
RESUMEN_CADA = int(os.getenv("VIGILANTE_RESUMEN_CADA", "0"))

_rondas = 0


def ronda():
    """Una pasada: escanea, avisa de intrusos y (opcional) manda un resumen periódico."""
    global _rondas
    _rondas += 1

    info = censar()
    nuevos = info.get("nuevos", [])

    if nuevos:
        for d in nuevos:
            cuerpo = (
                f"Dispositivo DESCONOCIDO en tu red de casa:\n"
                f"• {d.get('fabricante', '?')}\n"
                f"• IP: {d['ip']}\n"
                f"• MAC: {d['mac']}"
            )
            notificar(cuerpo, titulo="Deisy - Posible intruso",
                      prioridad="high", tags=["rotating_light"])
        print(f"🚨 {len(nuevos)} dispositivo(s) nuevo(s). Alerta enviada al móvil.")
    else:
        print(f"✅ Ronda OK: {info['total']} dispositivos, ninguno nuevo.")

    # Resumen periódico "todo tranquilo" (solo si lo activas en el .env)
    if RESUMEN_CADA and _rondas % RESUMEN_CADA == 0:
        notificar(f"Todo tranquilo en casa: {info['total']} dispositivos conocidos y ningún intruso. 🛡️",
                  titulo="Deisy", prioridad="low", tags=["white_check_mark"])
        print("📊 Resumen periódico enviado.")


if __name__ == "__main__":
    print("🛡️ Vigilante de red arrancando...")

    # 1) Línea base SILENCIOSA: aprende lo que hay ahora en casa SIN avisar,
    #    para no bombardearte con todos tus propios aparatos en el primer arranque.
    print("📋 Estableciendo línea base de tu red (sin avisos)...")
    base = censar()
    print(f"   Conozco {base['total']} dispositivos de partida.")

    # 2) Confirmación de que el canal funciona
    notificar(
        "Vigilante activado. Ya conozco tu red; te avisaré si aparece alguien nuevo. 🛡️",
        titulo="Deisy", prioridad="low", tags=["shield"],
    )

    # 3) Bucle de vigilancia
    print(f"👁️ Vigilando cada {INTERVALO}s. (Ctrl+C para parar)")
    while True:
        try:
            ronda()
        except Exception as e:
            print(f"Error en la ronda: {e}")
        time.sleep(INTERVALO)
