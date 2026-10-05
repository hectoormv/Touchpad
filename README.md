# TouchPad

Usa tu móvil (Android o iPhone) como trackpad y mando de presentaciones para Windows, por la Wi-Fi de casa. Sin instalar nada en el móvil: el PC le sirve una página web.

## Uso

1. Descarga `TouchPad.exe` desde **Releases** y ábrelo.
2. Pulsa **Instalar**. Windows pedirá permiso para el firewall: pulsa «Sí».
3. Pulsa **Activar** y escanea el QR con la cámara del móvil (misma Wi-Fi).
4. En el móvil, guárdalo con «Añadir a pantalla de inicio».

Para quitarlo: botón **Desinstalar** dentro de la app, o desde Configuración > Aplicaciones.

## Cómo funciona

- `touchpad/app.py`: la app de escritorio (ventana con pywebview, icono en la bandeja, instalación y desinstalación).
- `touchpad/server.py`: servidor HTTP (página del móvil) + WebSocket (gestos) que mueve el ratón con `SendInput` de la API de Windows.
- `touchpad/ui.html` / `touchpad/phone.html`: interfaces del PC y del móvil.
- El enlace lleva un token secreto: sin él, el servidor rechaza la conexión. El firewall solo se abre en redes privadas (puertos 8765 y 8766).

## Compilar

GitHub Actions compila el `.exe` en cada push (`.github/workflows/build.yml`). Para publicarlo en Releases:

```
git tag v1.0.0
git push origin v1.0.0
```

En local (Windows): `pip install -r requirements.txt pyinstaller`, `python make_icon.py` y el comando de PyInstaller del workflow.
