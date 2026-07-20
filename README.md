# Tourplan FX Bot

Automatiza la carga diaria de 3 tipos de cambio en Tourplan NX (Dólar MEP,
Dólar Oficial y Dólar Emisivo), a partir de las cotizaciones publicadas en
dolarhoy.com y bna.com.ar.

## Qué hace

1. Extrae el Dólar MEP (venta) de dolarhoy.com y el Dólar Oficial (venta) del
   Banco Nación.
2. Calcula Dólar Emisivo = Dólar Oficial + 10.
3. Valida que los valores no se disparen más de un 15% respecto del día
   anterior (evita cargar un valor erróneo por un problema de scraping).
4. Entra a Tourplan NX y actualiza las 3 divisas siguiendo el flujo:
   System > Code Setup > System > Exchange Rates > filtro Rate To
   31/12/2049 > actualizar cada línea (Applies From = hoy, Applies To =
   31/12/2049, Exchange Rate, checkboxes Rate Divider / Update Inverse Rate,
   Save).
5. Si algo falla en cualquier paso, corta antes de dejar Tourplan a medio
   actualizar y manda un email de alerta.
6. Deja todo registrado en `logs/tourplan_fx_bot.log` (con rotación diaria,
   se conservan 90 días).

## ⚠️ Antes de dejarlo en piloto automático

El módulo `tourplan_automation.py` está escrito siguiendo tu instructivo,
pero yo no tengo acceso a tu instancia real de Tourplan NX (requiere tus
credenciales), así que los selectores de los campos están marcados con
`# AJUSTAR:` en el código. Hay que:

1. Instalar todo (ver pasos abajo).
2. Correr una vez con `HEADLESS=false` en `.env` para ver el navegador
   en acción y confirmar que cada paso encuentra lo que tiene que
   encontrar.
3. Si algún selector no encuentra el campo (el error lo va a decir
   claramente: "Timeout esperando..." o "No se encontró ninguna fila..."),
   la forma más rápida de corregirlo es grabar el flujo una vez con
   Playwright Codegen:

   ```bash
   playwright codegen https://tuinstancia.tourplan.net/
   ```

   Hacés el login y la carga de una divisa a mano mientras Codegen graba,
   y te genera el selector exacto para pegar en el lugar marcado
   `# AJUSTAR:` correspondiente.

Una vez ajustado y probado un par de días con `HEADLESS=false`, se pasa a
`HEADLESS=true` para correr desatendido en el servidor.

## Instalación en el servidor (Linux)

```bash
# 1. Clonar/copiar esta carpeta al servidor, por ejemplo en /opt/tourplan_fx_bot

cd /opt/tourplan_fx_bot

# 2. Crear entorno virtual e instalar dependencias
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Instalar el navegador que usa Playwright (Chromium) + sus librerías del SO
playwright install --with-deps chromium

# 4. Configurar credenciales
cp .env.example .env
nano .env   # completar TOURPLAN_URL, TOURPLAN_USER, TOURPLAN_PASSWORD, SMTP_*, etc.

# 5. Probar una corrida manual
python main.py
```

Revisá la salida y `logs/tourplan_fx_bot.log`. Si algo falla, el mensaje de
error indica en qué paso fue.

## Programarlo a las 9am (cron)

```bash
crontab -e
```

Agregar la línea (ajustando la ruta si copiaste el proyecto a otro lugar):

```
0 9 * * * cd /opt/tourplan_fx_bot && /opt/tourplan_fx_bot/venv/bin/python main.py >> /opt/tourplan_fx_bot/logs/cron.log 2>&1
```

Esto corre el bot todos los días a las 9:00 (hora del servidor — confirmar
que el servidor esté en horario de Argentina, o ajustar la hora del cron
según corresponda: `TZ=America/Argentina/Buenos_Aires` se puede definir al
inicio del crontab si el servidor está en otro huso horario).

## Correrlo con Docker (alternativa a instalar Python + cron a mano)

Requiere Docker y Docker Compose. La imagen base ya trae Chromium y todas
las librerías del sistema que necesita Playwright, así que no hay que
instalar nada más en el servidor.

```bash
# 1. Completar el .env como en la instalación manual (paso 4 de arriba)
cp .env.example .env
nano .env

# 2. Levantar el contenedor (build + start en segundo plano)
docker compose up -d --build

# 3. Ver los logs en vivo
docker compose logs -f

# 4. Probar una corrida manual sin esperar al cron
docker compose exec tourplan-fx-bot python main.py
```

El contenedor queda corriendo con un cron interno que ejecuta `main.py`
todos los días a las 9:00 hora de Argentina (`TZ=America/Argentina/Buenos_Aires`
fijo en el `Dockerfile`) y manda la salida a `docker compose logs`.
`HEADLESS=true` queda forzado vía `docker-compose.yml` porque el contenedor
no tiene pantalla — para debuguear con navegador visible, seguí usando la
instalación manual (paso "Antes de dejarlo en piloto automático" más arriba).

Las carpetas `data/` y `logs/` del host se montan como volúmenes dentro del
contenedor, así que la sesión guardada (`data/tourplan_session.json`), el
historial de cotizaciones y los logs sobreviven a un `docker compose down` /
rebuild de la imagen.

Para actualizar el código tras un cambio:

```bash
docker compose up -d --build
```

## Estructura del proyecto

```
tourplan_fx_bot/
├── main.py                  # Orquestador (esto es lo que corre cron)
├── scrapers.py               # Extracción Dólar MEP + Dólar Oficial (BNA)
├── validacion.py              # Chequeo de variación anómala + historial
├── tourplan_automation.py    # Automatización del navegador (Playwright)
├── alertas.py                 # Envío de email si algo falla
├── requirements.txt
├── .env.example                # Plantilla de configuración (copiar a .env)
├── data/
│   └── historial_cotizaciones.json   # Se crea solo, guarda valores día a día
└── logs/
    ├── tourplan_fx_bot.log      # Log de cada corrida (rotación diaria)
    └── error_*.png               # Screenshots automáticos si falla Tourplan
```

## Sobre la fuente del "Dólar Oficial" en el BNA

La página del BNA muestra dos tablas: "Cotización Billetes" (cable/efectivo)
y "Cotización Divisas" (mayorista/transferencia), con valores distintos.
Por defecto el bot usa "Billetes" (la que habitualmente se reporta como
"dólar oficial"). Si la agencia factura con la cotización mayorista, cambiar
`BNA_TABLA=divisas` en `.env`.

## Ajustar el umbral de validación

Si un día el dólar realmente se mueve más de 15% (evento excepcional), el
bot NO va a cargar nada en Tourplan y te va a avisar por mail con los
valores detectados. En ese caso, cargalos manualmente ese día — al día
siguiente el bot va a tomar ese valor cargado como referencia y seguir
normal. El umbral se puede ajustar con `UMBRAL_VARIACION_PCT` en `.env`.
