"""
🌊 ONDA DE RED — El sentido de "presencia" de Deisy.

Barre la red local (WiFi/LAN), descubre qué dispositivos están conectados,
identifica su fabricante por la MAC y hace un censo en MySQL.
- La primera vez que ve un dispositivo, lo marca como NUEVO (posible intruso).
- Tú luego lo bautizas ("la tele del salón") y pasa a ser conocido.

Sin coste, sin API key obligatoria (el fabricante se consulta a una API
gratuita y se cachea en la BD para no repetir la consulta).
"""

import re
import time
import socket
import platform
import ipaddress
import subprocess
from concurrent.futures import ThreadPoolExecutor

import requests

ES_WINDOWS = platform.system().lower().startswith("win")


def _ip_local():
    """Averigua la IP local de esta máquina en la red (sin enviar datos reales)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _subred_24(ip):
    """Devuelve la red /24 (ej. 192.168.1.0/24) a la que pertenece la IP."""
    return ipaddress.ip_network(ip + "/24", strict=False)


def _ping(ip):
    ip = str(ip)
    if ES_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", "400", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return ip if r.returncode == 0 else None
    except Exception:
        return None


def ping_sweep(red):
    """Hace ping en paralelo a toda la subred para 'despertar' la tabla ARP."""
    vivos = []
    with ThreadPoolExecutor(max_workers=64) as ex:
        for res in ex.map(_ping, red.hosts()):
            if res:
                vivos.append(res)
    return vivos


def leer_arp():
    """Lee la tabla ARP del sistema y mapea IP -> MAC."""
    mapa = {}
    try:
        salida = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout
    except Exception:
        return mapa

    for linea in salida.splitlines():
        m_ip = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", linea)
        m_mac = re.search(r"(([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})", linea)
        if m_ip and m_mac:
            ip_d = m_ip.group(1)
            mac = m_mac.group(1).replace("-", ":").lower()
            # Ignoramos broadcast y multicast (no son equipos reales):
            #  - MAC de broadcast/nula
            #  - MAC multicast IPv4 (01:00:5e...) e IPv6 (33:33...)
            #  - IP multicast (224.x.x.x a 239.x.x.x) y broadcast (.255)
            if mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
                continue
            if mac.startswith("01:00:5e") or mac.startswith("33:33"):
                continue
            if 224 <= int(ip_d.split(".")[0]) <= 239 or ip_d.endswith(".255"):
                continue
            mapa[ip_d] = mac
    return mapa


def vendor_por_mac(mac):
    """Consulta el fabricante por la MAC (macvendors.com, gratis, sin key)."""
    try:
        r = requests.get(f"https://api.macvendors.com/{mac}", timeout=4)
        if r.status_code == 200 and r.text.strip():
            return r.text.strip()
    except Exception:
        pass
    return "Desconocido"


def escanear_red():
    """Escaneo crudo: devuelve lista de {ip, mac, hostname} de la red local."""
    ip = _ip_local()
    if not ip:
        return []

    red = _subred_24(ip)
    ping_sweep(red)          # rellena la tabla ARP del sistema
    arp = leer_arp()

    dispositivos = []
    for ip_d, mac in arp.items():
        try:
            host = socket.gethostbyaddr(ip_d)[0]
        except Exception:
            host = ""
        dispositivos.append({"ip": ip_d, "mac": mac, "hostname": host})
    return dispositivos


def censar():
    """Escanea, compara con la BD y guarda. Devuelve {total, dispositivos, nuevos}."""
    from base_datos import dispositivos_estado, guardar_dispositivo

    dispositivos = escanear_red()
    conocidos_db = dispositivos_estado()   # mac -> {fabricante, alias, es_conocido}
    nuevos = []

    for d in dispositivos:
        mac = d["mac"]
        prev = conocidos_db.get(mac)

        if prev:
            # Ya lo conocíamos: solo refrescamos IP y "última vez visto"
            fabricante = prev.get("fabricante") or "Desconocido"
            d["alias"] = prev.get("alias")
            d["es_conocido"] = bool(prev.get("es_conocido"))
        else:
            # Nuevo: buscamos su fabricante (con pausa para no saturar la API gratis)
            fabricante = vendor_por_mac(mac)
            time.sleep(0.3)
            d["alias"] = None
            d["es_conocido"] = False
            nuevos.append(d)

        d["fabricante"] = fabricante
        guardar_dispositivo(mac, d["ip"], fabricante)

    return {"total": len(dispositivos), "dispositivos": dispositivos, "nuevos": nuevos}


if __name__ == "__main__":
    # Prueba rápida por consola
    info = censar()
    print(f"\n🌊 {info['total']} dispositivos en la red. Nuevos: {len(info['nuevos'])}")
    for d in info["dispositivos"]:
        etiqueta = d.get("alias") or d.get("fabricante") or "?"
        print(f"  {d['ip']:<15} {d['mac']}  {etiqueta}")
