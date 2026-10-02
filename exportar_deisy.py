import os
import subprocess

def exportar_proyecto():
    # Detecta automáticamente la carpeta donde está este script
    ruta_proyecto = os.path.dirname(os.path.abspath(__file__))
    archivo_salida = os.path.join(ruta_proyecto, "codigo_completo_deisy.txt")
    
    # 🛡️ SEGURIDAD: Archivos que NO queremos incluir
    archivos_ignorados = [".env", "codigo_completo_deisy.txt"]
    carpetas_ignoradas = ["__pycache__", ".git", "venv", "env"]
    
    codigo_total = "=== CÓDIGO FUENTE DEL PROYECTO DEISY ===\n\n"
    
    print("Recopilando el ADN del proyecto...")

    # Recorremos la carpeta buscando archivos
    for raiz, carpetas, archivos in os.walk(ruta_proyecto):
        
        # Ignorar carpetas del sistema o entornos virtuales
        if any(ignorada in raiz for ignorada in carpetas_ignoradas):
            continue
            
        for archivo in archivos:
            # Solo queremos archivos de Python y evitar los ignorados
            if archivo.endswith(".py") and archivo not in archivos_ignorados:
                ruta_completa = os.path.join(raiz, archivo)
                
                try:
                    with open(ruta_completa, 'r', encoding='utf-8') as f:
                        contenido = f.read()
                        
                        # Formato bonito para separar cada archivo
                        codigo_total += f"\n{'='*60}\n"
                        codigo_total += f"📁 ARCHIVO: {archivo}\n"
                        codigo_total += f"{'='*60}\n\n"
                        codigo_total += contenido + "\n"
                except Exception as e:
                    print(f"Error leyendo {archivo}: {e}")

    # Guardamos todo el texto recopilado en un Bloc de Notas
    try:
        with open(archivo_salida, 'w', encoding='utf-8') as f:
            f.write(codigo_total)
        print(f"¡Éxito! Todo el código se ha guardado en: {archivo_salida}")
        
        # 🚀 Magia de Windows: Abrimos el Bloc de Notas automáticamente
        subprocess.Popen(["notepad.exe", archivo_salida])
        
    except Exception as e:
        print(f"Error al crear el archivo de salida: {e}")

if __name__ == "__main__":
    exportar_proyecto()