@echo off
title Lanzador de Daisy
cls

echo ===================================================
echo   🚀 INICIANDO EL CEREBRO CENTRAL DE DAISY
echo ===================================================
echo.

:: Nos movemos a la carpeta de tu proyecto
cd /d "C:\Users\FYDILOVER\Documents\DEISY"

:: 1. Encendiendo el Cerebro Central (Waitress en puerto 5000)
echo [+] Lanzando api_deisy.py (Minimizado)...
start "API Deisy" /min python api_deisy.py

:: Le damos 3 segundos para que el puerto se abra antes de conectar el resto
timeout /t 3 /nobreak > nul

:: 2. Conectando la Pasarela de Telegram
echo [+] Lanzando telegram_deisy.py (Minimizado)...
start "Telegram Proxy" /min python telegram_deisy.py

:: 3. Abriendo el túnel seguro hacia tu iPhone
echo [+] Lanzando Tailscale Funnel (Minimizado)...
start "Tailscale" /min tailscale funnel 5000

:: 4. Activando el radar de dispositivos
echo [+] Lanzando Vigilante de Red (Minimizado)...
start "Vigilante" /min python vigilante.py

echo.
echo ===================================================
echo   ¡Todo listo, Eddie! Daisy esta operativa en la HP.
echo ===================================================
timeout /t 2 > nul
exit