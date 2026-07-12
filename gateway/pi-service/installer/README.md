# ModuLinkr Gateway Installer

Instalador del gateway ModuLinkr sobre el Raspberry Pi. Instala las dependencias, crea un venv dedicado, pregunta la configuración (puerto serie, red LoRa, seguridad AES-CCM y broker MQTT), guarda los secretos fuera del repo y deja el servicio bajo systemd. Está pensado para Raspberry Pi OS (Debian) y para reejecutarse sin romper una instalación previa.

## Uso

```bash
cd ~/pi-service/installer
sudo ./install.sh
```

El instalador pregunta lo que haga falta. La contraseña MQTT y la clave de red se piden sin eco y por duplicado para confirmar que coinciden. Con `--config modulinkr-gateway.conf` toma los ajustes no sensibles de un archivo, y con `-y` corre sin preguntas usando ese config y los valores por defecto.

## Qué hace

Instala de apt `python3`, `python3-venv`, `python3-serial` y `python3-cryptography` (estas dos últimas desde apt a propósito, para no compilar `cryptography` en el Pi), crea un venv dedicado del gateway con `--system-site-packages` y le añade `paho-mqtt`. Luego escribe la configuración y los secretos en `/etc/modulinkr/gateway.env` (solo root), genera la unidad `modulinkr-gateway.service` con las rutas de esta instalación, y la habilita y arranca.

## Credenciales y secretos

Nada sensible se guarda en el repositorio ni se pasa por el config. La contraseña MQTT y la clave de red AES-CCM (`MODULINKR_SEC_KEY`, 32 caracteres hex que deben coincidir con los nodos) se preguntan durante la instalación, con confirmación. Todo, config y secretos, queda en `/etc/modulinkr/gateway.env` con permisos de root. El servicio corre como el usuario del sistema (por defecto el que invocó sudo), pero systemd lee el `EnvironmentFile` como root antes de bajar privilegios, así que el usuario del servicio no necesita leer el archivo de secretos.

El `config` de ejemplo (`config/modulinkr-gateway.conf.example`) solo lleva ajustes no sensibles (usuario, puerto serie, network id, host y usuario del broker). Un `modulinkr-gateway.conf` rellenado está en `.gitignore`.

## Idempotencia

Reejecutar es seguro. Al arrancar, el instalador carga `/etc/modulinkr/gateway.env` si ya existe, de modo que no vuelve a preguntar los secretos ya definidos y reutiliza la configuración anterior. Los paquetes de apt, el venv y el usuario solo se crean si faltan. Para cambiar un valor, se corre de nuevo y se responde distinto, o se edita `gateway.env` a mano.

## Estructura

```
installer/
  install.sh                          punto de entrada: banner, argumentos, orquestación
  lib/common.sh                       registro, preguntas, ask_secret, idempotencia
  lib/gateway.sh                      módulo del gateway (deps, venv, config, systemd)
  config/modulinkr-gateway.conf.example
```

`common.sh` es el mismo juego de utilidades que usa el instalador del servidor (`../../server/lib/common.sh`), copiado aquí para que el instalador del gateway sea autónomo.

## Operación posterior

```bash
sudo systemctl status modulinkr-gateway --no-pager
journalctl -u modulinkr-gateway -f
sudo systemctl restart modulinkr-gateway
```

Para cortar el envío a cloud dejando LoRa activo, poner `MODULINKR_MQTT_HOST=` vacío en `/etc/modulinkr/gateway.env` y reiniciar el servicio.
