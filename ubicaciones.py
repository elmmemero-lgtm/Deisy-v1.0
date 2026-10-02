"""Traduce una ubicación declarada por el iPhone a un lugar conocido.

No abre otra conexión con un host diferente: utiliza DB_HOST/DB_PORT mediante
base_datos.conectar_db(), igual que el resto de la HP.
"""

from __future__ import annotations

from base_datos import conectar_db


def obtener_mapa_mental() -> dict[str, str]:
    """Devuelve ``keyword -> nombre_lugar`` desde MySQL, o un mapa vacío."""
    conexion = conectar_db()
    if conexion is None:
        return {}
    cursor = None
    try:
        cursor = conexion.cursor()
        cursor.execute("SELECT nombre_lugar, direccion_keyword FROM lugares_conocidos")
        lugares: dict[str, str] = {}
        for nombre, keyword in cursor.fetchall():
            keyword = str(keyword or "").strip().casefold()
            nombre = str(nombre or "").strip()
            if keyword and nombre:
                lugares[keyword] = nombre
        return lugares
    except Exception as error:
        print(f"⚠️ Error al leer la tabla de ubicaciones: {error}")
        return {}
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        try:
            if conexion.is_connected():
                conexion.close()
        except Exception:
            pass


def identificar_lugar(direccion_bruta: object) -> str:
    """Devuelve un alias conocido sin propagar direcciones privadas completas.

    Si no hay coincidencia, se devuelve una etiqueta prudente en lugar de una
    dirección callejera. El radar conserva sus enlaces de Maps en su endpoint
    separado y autorizado.
    """
    direccion = str(direccion_bruta or "").strip()
    if not direccion:
        return "Ubicación no indicada"
    baja = direccion.casefold()
    for keyword, nombre_lugar in obtener_mapa_mental().items():
        if keyword in baja:
            return nombre_lugar
    return "Ubicación no identificada"
