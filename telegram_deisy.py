"""Pasarela Telegram -> API central de Deisy.

No contiene un segundo cerebro. Su única tarea es autenticar el chat, reenviar
mensaje/foto a api_deisy.py y devolver la respuesta. Las llamadas HTTP se hacen
fuera del bucle asíncrono para que una foto lenta no bloquee todos los mensajes.
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters


load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
DEISY_TOKEN = os.getenv("DEISY_TOKEN", "").strip()
API_LOCAL_URL = os.getenv("DEISY_LOCAL_API_URL", "http://127.0.0.1:5000").strip().rstrip("/")

# Admite un solo chat o varios separados por comas. Ejemplo: 12345,67890
ALLOWED_CHAT_IDS = {
    parte.strip() for parte in os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").split(",") if parte.strip()
}


def _autorizado(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    if not ALLOWED_CHAT_IDS:
        # Es útil para enlazar el primer chat, pero no aceptamos ni reenviamos
        # su contenido hasta que Eddie copie este id al .env de la HP.
        print(
            "⚠️ Telegram bloqueado: falta TELEGRAM_ALLOWED_CHAT_ID. "
            f"ID del chat que intentó usarlo: {chat.id}"
        )
        return False
    return str(chat.id) in ALLOWED_CHAT_IDS


def _mensaje_no_autorizado() -> str:
    if not ALLOWED_CHAT_IDS:
        return "⚠️ El bot aún no está vinculado a un chat autorizado. Revisa la consola de la HP."
    return "No autorizado."


def _post_json(endpoint: str, payload: dict[str, Any], lectura: int) -> tuple[int, dict[str, Any] | None, str]:
    """Función síncrona que se invoca mediante asyncio.to_thread()."""
    respuesta = requests.post(
        f"{API_LOCAL_URL}{endpoint}",
        json=payload,
        headers={"X-Deisy-Token": DEISY_TOKEN, "Accept": "application/json"},
        timeout=(4, lectura),
    )
    try:
        datos = respuesta.json()
    except ValueError:
        datos = None
    return respuesta.status_code, datos if isinstance(datos, dict) else None, respuesta.text[:300]


def _error_http(codigo: int, datos: dict[str, Any] | None, cuerpo: str) -> str:
    detalle = ""
    if datos:
        detalle = str(datos.get("error") or datos.get("mensaje") or datos.get("respuesta") or "").strip()
    if codigo == 401:
        return "⚠️ La API rechazó la petición: revisa DEISY_TOKEN."
    if codigo == 422:
        # La foto llegó correctamente, pero el modelo no produjo una respuesta
        # final segura. Mostramos el mensaje honesto de la API, no un falso 500.
        return f"⚠️ {detalle or 'No recibí una descripción utilizable de la foto. No voy a inventar qué muestra.'}"
    if codigo == 503:
        return "⚠️ La función no está disponible ahora en la HP. Revisa la configuración de visión."
    if codigo >= 500:
        return "⚠️ La API tuvo un error interno al procesar la petición. Revisa su consola."
    return f"⚠️ La API devolvió HTTP {codigo}{': ' + detalle if detalle else ''}."


async def manejar_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _autorizado(update):
        await update.effective_message.reply_text(_mensaje_no_autorizado())
        return
    mensaje = update.effective_message
    if not mensaje or not mensaje.photo:
        return

    try:
        await mensaje.reply_text("📸 Foto recibida. La analizaré ahora mismo…")
        archivo = await mensaje.photo[-1].get_file()
        # Sin archivos temporales: la foto vive solo en memoria durante el POST.
        imagen = await archivo.download_as_bytearray()
        if len(imagen) > 4 * 1024 * 1024:
            await mensaje.reply_text("⚠️ La foto es demasiado grande para analizarla de forma segura.")
            return

        payload = {
            "imagen_b64": base64.b64encode(bytes(imagen)).decode("ascii"),
            "mime": "image/jpeg",
            "pregunta": (mensaje.caption or "Describe esta imagen.")[:1200],
        }
        codigo, datos, cuerpo = await asyncio.to_thread(_post_json, "/analizar_foto", payload, 90)
        if codigo != 200:
            await mensaje.reply_text(_error_http(codigo, datos, cuerpo))
            return
        texto = str((datos or {}).get("respuesta") or "").strip()
        await mensaje.reply_text(texto or "⚠️ La API no devolvió un análisis de la foto.")
    except requests.exceptions.Timeout:
        await mensaje.reply_text("⚠️ El análisis tardó demasiado. Prueba otra vez con una foto más pequeña.")
    except requests.RequestException as error:
        print(f"⚠️ Error de red procesando foto de Telegram: {error}")
        await mensaje.reply_text("⚠️ No pude conectar con api_deisy.py para analizar la foto.")
    except Exception as error:
        print(f"⚠️ Error procesando foto de Telegram: {error}")
        await mensaje.reply_text("⚠️ No pude analizar la foto. Revisa la consola de Telegram y de la API.")


async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _autorizado(update):
        await update.effective_message.reply_text(_mensaje_no_autorizado())
        return
    mensaje = update.effective_message
    pregunta = str(mensaje.text or "").strip() if mensaje else ""
    if not pregunta:
        return
    if len(pregunta) > 4000:
        await mensaje.reply_text("⚠️ El mensaje es demasiado largo. Divídelo en dos partes.")
        return

    chat_id = update.effective_chat.id
    print(f"📱 Telegram -> cerebro central: {pregunta[:250]}")
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    payload = {
        "mensaje": pregunta,
        "dispositivo": "Telegram",
        "sesion": f"telegram:{chat_id}",
        # Telegram no aporta GPS por este flujo. No se manda una ciudad ficticia.
    }
    try:
        codigo, datos, cuerpo = await asyncio.to_thread(_post_json, "/preguntar", payload, 90)
        if codigo != 200:
            await mensaje.reply_text(_error_http(codigo, datos, cuerpo))
            return
        texto = str((datos or {}).get("respuesta") or "").strip()
        await mensaje.reply_text(texto or "⚠️ La API respondió sin texto.")
    except requests.exceptions.Timeout:
        await mensaje.reply_text("⚠️ La API tardó demasiado. Puede estar iniciando o atendiendo otra petición.")
    except requests.RequestException as error:
        print(f"⚠️ Error de red con api_deisy.py: {error}")
        await mensaje.reply_text("⚠️ No pude conectar con api_deisy.py. ¿Está encendida?")
    except Exception as error:
        print(f"⚠️ Error procesando Telegram: {error}")
        await mensaje.reply_text("⚠️ Error controlado en la pasarela de Telegram.")


def main() -> None:
    if not TOKEN:
        raise RuntimeError("Falta TELEGRAM_TOKEN en el .env de la HP.")
    if not DEISY_TOKEN:
        raise RuntimeError("Falta DEISY_TOKEN en el .env de la HP.")
    if not ALLOWED_CHAT_IDS:
        print(
            "⚠️ TELEGRAM_ALLOWED_CHAT_ID no está configurado. El bot arrancará "
            "bloqueado: rechazará todos los mensajes y mostrará el chat id en esta consola."
        )

    print("🚀 Pasarela de Telegram activa. Reenvía al servidor local de Deisy cuando el chat esté autorizado.")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, manejar_foto))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
