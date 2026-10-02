"""Filtro determinista de la salida de Groq.

Groq puede sugerir texto; Python decide qué sintaxis llega a un manejador.
Este archivo es una barrera de seguridad y de estabilidad, no un segundo router.
"""

from __future__ import annotations
from politica_entrada import familia_de_comando
import re
import unicodedata
from urllib.parse import urlparse


def normalizar(texto: object) -> str:
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()


def pide_hora(texto: object) -> bool:
    bajo = normalizar(texto)
    return bool(
        re.search(
            r"\b(que hora es|que hora son|dime que hora son|dime la hora|hora actual|tienes hora)\b",
            bajo,
        )
    )


def es_continuacion_de_charla(texto: object) -> bool:
    """Detecta una continuación humana, no una orden física incompleta.

    «sube más» y «la siguiente» NO se consideran charla: contexto_ordenes.py
    tiene prioridad y decide si hay una acción reciente confirmada.
    """
    bajo = normalizar(texto)
    if bajo in {"mas", "más", "lo mismo", "otra", "otra vez", "y tu", "y tú"}:
        return True
    return bajo.startswith((
        "pero ", "no, ", "no es ", "eso no ", "me referia ",
        "me refería ", "en realidad ", "te equivocas",
    ))


def _spotify_destino_valido(destino: str) -> bool:
    destino = str(destino or "").strip()
    if re.fullmatch(r"spotify:(?:track|album|playlist|artist|show|episode):[A-Za-z0-9]+", destino):
        return True
    partes = urlparse(destino)
    return (
        partes.scheme == "https"
        and partes.netloc.lower().split(":", 1)[0] == "open.spotify.com"
    )


def _limpiar(comando: object) -> str:
    comando = str(comando or "").strip()
    comando = comando.replace("`", "").replace("*", "")
    comando = re.sub(r"^[-•\d.\s]+", "", comando)
    return re.sub(r"\s+", " ", comando).strip()


def _red_nombrar_seguro(cuerpo: object) -> str:
    """Valida el único subcomando de red que escribe un alias en MySQL.

    Los parámetros se envían a MySQL parametrizados más tarde, pero además
    limitamos esta gramática para que el router no convierta una frase libre en
    una operación de escritura inesperada.
    """
    normal = normalizar(cuerpo)
    coincidencia = re.fullmatch(r"nombrar\s+(.{1,80}?)\s+como\s+(.{1,80})", normal)
    if not coincidencia:
        return ""
    clave, alias = (parte.strip() for parte in coincidencia.groups())
    permitido = re.compile(r"^[a-z0-9 .:_-]+$")
    if not (permitido.fullmatch(clave) and permitido.fullmatch(alias)):
        return ""
    return f"red: nombrar {clave} como {alias}"


def filtrar_comandos_router(comandos: object, pregunta_original: object, familias_permitidas=None) -> list[str]:
    """Devuelve únicamente comandos pertenecientes al contrato 4.1.

    No deja pasar ``start``, ``taskkill``, ``camara`` ni subcomandos inventados.
    El apagado se rechaza aquí: solo puede venir de intenciones_deterministas.py.
    """
    if isinstance(comandos, str):
        candidatos = comandos.splitlines()
    elif isinstance(comandos, (list, tuple)):
        candidatos = comandos
    else:
        return []

    pregunta = str(pregunta_original or "").strip()
    # El router puede equivocarse y proponer ``hora:`` ante una frase corta
    # como «entendido». Solo la frase ORIGINAL del usuario puede autorizar
    # esta capacidad local; es una segunda barrera tras el acuse determinista.
    usuario_pidio_hora = pide_hora(pregunta)
    resultado: list[str] = []

    for candidato in candidatos[:6]:
        comando = _limpiar(candidato)
        bajo = comando.casefold()
        if not comando or len(comando) > 1400:
            continue

        # Módulos locales seguros.
        if bajo == "hora:":
            if usuario_pidio_hora:
                resultado.append("hora:")
        elif bajo == "radar:":
            resultado.append("radar:")
        elif bajo == "estado_contexto:":
            resultado.append("estado_contexto:")
        elif bajo == "estado_enfoque:":
            resultado.append("estado_enfoque:")       
        elif bajo.startswith("clima:"):
            ciudad = comando.split(":", 1)[1].strip()
            if ciudad.startswith(("[", "{", "<")):
                ciudad = ""
            resultado.append("clima: " + ciudad)
        elif bajo.startswith("noticias:"):
            resultado.append(
                "noticias: " + comando.split(":", 1)[1].strip()
            )
        elif bajo.startswith("navegar:"):
            consulta = comando.split(":", 1)[1].strip()
            if consulta:
                resultado.append("navegar: " + consulta)
        elif bajo.startswith("red:"):
            cuerpo = comando.split(":", 1)[1]
            opcion = normalizar(cuerpo)
            if opcion in {"", "escanear", "lista", "listar", "dispositivos", "conocidos"}:
                resultado.append("red: " + (opcion or "escanear"))
            else:
                nombrar = _red_nombrar_seguro(cuerpo)
                if nombrar:
                    resultado.append(nombrar)
        elif bajo.startswith("iphone:"):
            cuerpo = comando.split(":", 1)[1].strip()
            accion = normalizar(cuerpo).split(" ", 1)[0] if cuerpo else ""
            if accion in {"alarma", "temporizador", "recordatorio", "nota", "abrir", "musica", "mensaje", "llamar"}:
                resultado.append("iphone: " + cuerpo)
        elif bajo.startswith("preguntar:"):
            pregunta_corta = comando.split(":", 1)[1].strip()
            if pregunta_corta:
                resultado.append("preguntar: " + pregunta_corta[:500])
        elif bajo.startswith("charla:"):
            # No dejamos que el modelo reescriba la pregunta ni inyecte una orden
            # antigua; le pasamos la frase real del usuario.
            resultado.append("charla: " + pregunta)

        # Contrato HP -> Omen.
        elif bajo.startswith("buscar:"):
            nombre = comando.split(":", 1)[1].strip()
            nombre_normalizado = normalizar(nombre)
            es_plantilla = (
                "[" in nombre
                or "]" in nombre
                or nombre_normalizado in {
                    "", "app", "aplicacion", "una app", "una aplicacion",
                    "nombre de la aplicacion", "nombre de aplicacion",
                }
            )
            if nombre and len(nombre) <= 160 and not es_plantilla:
                resultado.append("buscar: " + nombre)
            else:
                resultado.append(
                    "preguntar: ¿Qué aplicación quieres abrir en la Omen?"
                )
        elif bajo.startswith("musica_app:"):
            destino = normalizar(comando.split(":", 1)[1])
            if destino in {"spotify", "spoty"}:
                resultado.append("musica_app: spotify")
        elif bajo.startswith("combo_musica:"):
            destino = comando.split(":", 1)[1].strip()
            if _spotify_destino_valido(destino):
                resultado.append("combo_musica: " + destino)
        elif bajo.startswith("tecla:"):
            accion = normalizar(comando.split(":", 1)[1])
            equivalencias = {
                "play": "play", "pausa": "pausa", "pausar": "pausa",
                "reanudar": "play", "reproducir": "play",
                "siguiente": "siguiente", "siguiente cancion": "siguiente",
                "anterior": "anterior", "cancion anterior": "anterior",
            }
            if accion in equivalencias:
                resultado.append("tecla: " + equivalencias[accion])
        elif bajo.startswith("ver:"):
            consulta = comando.split(":", 1)[1].strip() or pregunta
            resultado.append("ver: " + consulta[:1200])
        elif bajo.startswith("sistema:"):
            accion = normalizar(comando.split(":", 1)[1])
            # El apagado deliberadamente no está aquí.
            if accion in {
                "subir volumen", "subir_volumen", "bajar volumen", "bajar_volumen",
                "ventanas", "actualizar apps", "actualizar_apps",
            }:
                accion = accion.replace(" ", "_")
                resultado.append("sistema: " + accion)

        # El resto se elimina y la API caerá en charla honesta. Nunca se intenta
        # interpretar como una orden de shell o una capacidad futura.

    # Se evita ejecutar la misma línea dos veces en una salida de modelo rara.
    sin_duplicados: list[str] = []
    for comando in resultado:
        # La sintaxis puede ser correcta y, aun así, ser una acción que Eddie
        # nunca pidió. Solo permitimos su familia autorizada previamente.
        if familias_permitidas is not None:
            familia = familia_de_comando(comando)
            if familia != "charla" and familia not in familias_permitidas:
                print(
                    f"🚫 Comando descartado por familia: {comando!r}. "
                    f"Permitidas: {sorted(familias_permitidas)}"
                )
                continue

        if comando not in sin_duplicados:
            sin_duplicados.append(comando)
    return sin_duplicados
