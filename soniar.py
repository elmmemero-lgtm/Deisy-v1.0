import os
import mysql.connector
from mysql.connector import Error
from groq import Groq
from dotenv import load_dotenv

# Cargamos variables de entorno (Asegúrate de tener un .env en el portátil HP con tu GROQ_API_KEY)
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def obtener_historial_hoy():
    """Se conecta al MySQL central de 'Mamá de Deisy' y saca la charla reciente."""
    try:
        conexion = mysql.connector.connect(
            host=os.getenv("DB_CENTRAL_HOST", "100.85.34.51"),
            port=3306,
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),  # ¡La contraseña SOLO en el .env!
            database=os.getenv("DB_NAME", "deisy_db")
        )
        if not conexion.is_connected():
            return ""

        cursor = conexion.cursor()
        # Traemos las últimas 50 interacciones
        query = """
        SELECT usuario_dice, deisy_respondio
        FROM historial_aprendizaje
        ORDER BY id DESC LIMIT 50;
        """
        cursor.execute(query)
        filas = cursor.fetchall()

        cursor.close()
        conexion.close()

        if not filas:
            return ""

        historial_texto = ""
        for usuario, deisy in reversed(filas):
            historial_texto += f"Eddie: {usuario}\nDaisy: {deisy}\n\n"
        return historial_texto

    except Error as e:
        print(f"Error conectando a la BD de la Mamá: {e}")
        return ""

def procesar_sueno():
    # Buscamos el archivo de texto en la misma carpeta que este script
    ruta_perfil = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perfil_eddie.txt")

    perfil_actual = ""
    if os.path.exists(ruta_perfil):
        with open(ruta_perfil, 'r', encoding='utf-8') as f:
            perfil_actual = f.read()

    historial_hoy = obtener_historial_hoy()

    if not historial_hoy:
        print("💤 Daisy ha dormido profundamente. No hay conversaciones nuevas que analizar.")
        return

    print("🧠 Daisy está soñando y analizando el día...")

    # 🛠️ PROMPT ULTRA-ESPECÍFICO PARA ACTUALIZAR SIN ROMPER NADA
    prompt = f"""
    Eres el subconsciente analítico de Daisy. Lee la conversación de hoy con Eddie y actualiza su perfil.

    REGLAS DE ACTUALIZACIÓN:
    1. Si Eddie menciona un GUSTO, HÁBITO, o DATO NUEVO que no está en el perfil, AÑÁDELO como un nuevo punto (- TEMA: detalle).
    2. Si Eddie da información que ACTUALIZA o CAMBIA algo que ya existe en el perfil, SOBREESCRIBE ese punto específico.
    3. MANTÉN INTACTOS los demás puntos del perfil que no tengan relación con la charla de hoy. No elimines información antigua a menos que haya sido directamente contradicha hoy.
    4. Cero charla, saludos, ni explicaciones. Devuelve ÚNICAMENTE el texto final del perfil actualizado.

    PERFIL ACTUAL DE EDDIE:
    {perfil_actual}

    CONVERSACIÓN DE HOY:
    {historial_hoy}
    """

    try:
        respuesta = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            max_tokens=800
        )

        nuevo_perfil = respuesta.choices[0].message.content.strip()

        # Salvaguarda: Si la IA falla y devuelve algo muy corto (error), no sobreescribimos
        if len(nuevo_perfil) < 50:
            print("⚠️ El sueño fue confuso. No se ha modificado el perfil para evitar pérdida de datos.")
            return

        with open(ruta_perfil, 'w', encoding='utf-8') as f:
            f.write(nuevo_perfil)

        print("✅ El sueño ha terminado: el perfil de Eddie se ha optimizado.")
        print(f"\n--- PERFIL ACTUALIZADO ---\n{nuevo_perfil}\n--------------------------")

    except Exception as e:
        print(f"❌ Pesadilla en el sistema (Error en API): {e}")

if __name__ == "__main__":
    procesar_sueno()
