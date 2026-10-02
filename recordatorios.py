import os
import json
import threading
from datetime import datetime, timedelta

from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

RECORDATORIOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordatorios.json")

# 🔒 Un solo candado para todo el fichero: evita que el hilo del checador y el
# hilo del servidor escriban a la vez y dejen el JSON a medias (corrupto).
_LOCK_RECORDATORIOS = threading.Lock()


def _cargar():
    with _LOCK_RECORDATORIOS:
        if not os.path.exists(RECORDATORIOS_FILE):
            return []
        try:
            with open(RECORDATORIOS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []


def _guardar(datos):
    # Escritura ATÓMICA: escribimos en un .tmp y luego lo renombramos de golpe,
    # así nunca queda un JSON a medio escribir si algo falla.
    temporal = RECORDATORIOS_FILE + ".tmp"
    with _LOCK_RECORDATORIOS:
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=4)
        os.replace(temporal, RECORDATORIOS_FILE)


def gestionar_recordatorio(pregunta):
    ahora = datetime.now()
    fecha_actual = ahora.strftime("%Y-%m-%d")
    hora_actual = ahora.strftime("%H:%M")

    prompt = f"""
    Analiza la petición. Hoy es {fecha_actual} y son las {hora_actual}.
    Petición: "{pregunta}"

    Devuelve un JSON estrictamente con esta estructura:
    {{
        "accion": "crear, listar o borrar",
        "texto": "lo que tiene que hacer (ej: llamar a pepe). Usa 'todo' si pide borrar todos.",
        "fecha": "YYYY-MM-DD (o null)",
        "hora": "HH:MM (en 24h, o null)",
        "minutos_extra": numero (o null),
        "repetir": "unica o toda_la_semana",
        "duda": "Pregunta si falta hora/fecha al CREAR. Si todo está claro, null."
    }}

    REGLAS:
    1. Si pide saber qué recordatorios tiene o qué toca hoy, accion es "listar".
    2. Si pide borrar, quitar o cancelar uno, accion es "borrar".
    3. Si pide añadir o recordar, accion es "crear".
    """

    try:
        respuesta = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
            temperature=0.1
        )
        res_json = json.loads(respuesta.choices[0].message.content.strip())
        accion = res_json.get("accion", "crear")

        recordatorios = _cargar()

        # ─── 1. LISTAR ───
        if accion == "listar":
            if not recordatorios:
                return "Tienes la agenda en blanco, Eddie. Ningún recordatorio pendiente."

            mensaje = "📅 Aquí tienes tus recordatorios:\n"
            for r in recordatorios:
                mensaje += f"• {r['fecha']} a las {r['hora']} -> {r['texto']}\n"
            return mensaje

        # ─── 2. BORRAR ───
        elif accion == "borrar":
            if not recordatorios:
                return "No hay nada que borrar, tu agenda ya estaba limpia."

            texto_a_borrar = res_json.get("texto", "").lower()
            if texto_a_borrar == "todo" or "todo" in texto_a_borrar:
                _guardar([])
                return "💥 Hecho. He borrado todos tus recordatorios de golpe."

            nuevos_rec = [r for r in recordatorios if texto_a_borrar not in r["texto"].lower()]
            borrados = len(recordatorios) - len(nuevos_rec)

            if borrados > 0:
                _guardar(nuevos_rec)
                return f"🗑️ Listo, he eliminado {borrados} recordatorio(s) sobre '{res_json['texto']}'."
            else:
                return "No he encontrado ningún recordatorio que se llame así. ¿Seguro que lo anotaste?"

        # ─── 3. CREAR ───
        else:
            if res_json.get("duda"):
                return res_json["duda"]

            if res_json.get("minutos_extra"):
                tiempo_futuro = ahora + timedelta(minutes=int(res_json["minutos_extra"]))
                fecha_final = tiempo_futuro.strftime("%Y-%m-%d")
                hora_final = tiempo_futuro.strftime("%H:%M")
            else:
                fecha_final = res_json.get("fecha")
                hora_final = res_json.get("hora")

            nuevo = {
                "texto": res_json["texto"],
                "fecha": fecha_final,
                "hora": hora_final,
                "repetir": res_json["repetir"] or "unica",
                "creado": ahora.strftime("%Y-%m-%d %H:%M")
            }
            recordatorios.append(nuevo)
            _guardar(recordatorios)

            return f"¡Hecho Eddie! Anotado: '{res_json['texto']}' para el {fecha_final} a las {hora_final}."

    except Exception as e:
        return f"Se me ha trabado la agenda: {e}"
