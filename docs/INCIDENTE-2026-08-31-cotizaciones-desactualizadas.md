# Incidente: el bot dejó de actualizar el dólar sin que nadie se enterara

**Resuelto el 31 de agosto de 2026 · duración del problema: ~40 días**

Tourplan mostraba un Dólar Oficial de hace más de un mes. Esto documenta qué
pasó, por qué no saltó ninguna alarma, y qué hacer si el número vuelve a
verse raro. Ver también [FUNCIONALIDAD.md](FUNCIONALIDAD.md) y
[CODIGO.md](CODIGO.md) para el resto de la documentación del bot.

## Resumen

Todos los días a las 8am, el bot tiene que leer la cotización del dólar y
cargarla en Tourplan. El 31 de agosto se notó que el Dólar Oficial cargado
en Tourplan (1500) no coincidía con el cierre real del viernes anterior
(1535).

| | Valor |
|---|---|
| Oficial en Tourplan (viejo) | $1500,00 |
| Cierre real del viernes | $1535,00 |
| Corregido en Tourplan | $1530,00 |

La investigación encontró algo más grave que un valor desactualizado por un
día: **el bot no había completado una carga automática exitosa desde, como
mínimo, el 22 de julio**. El valor que se veía en Tourplan era el de la
última carga manual, con más de un mes de atraso — y el sistema no había
avisado nada en todo ese tiempo.

## Línea de tiempo

- **22 jul 2026, 08:00** — Primera falla registrada en los logs: el proceso
  corre y corta de inmediato por falta de configuración. Es el registro más
  viejo disponible; es posible que venga fallando desde antes.
- **23 jul – 30 ago 2026, 08:00 todos los días** — La misma falla, sin
  excepción, cuarenta veces seguidas. Ninguna corrida llegó a tocar
  Tourplan. Ninguna mandó aviso por mail — el problema era, justamente, que
  faltaba la configuración necesaria para mandar el mail.
- **31 ago 2026, 08:00** — Se detecta el número raro: se compara el Oficial
  cargado (1500) contra el cierre real del viernes (1535) y no cierra.
  Arranca la investigación.
- **31 ago 2026, 16:22** — Corregido: se arregla la causa de fondo, se
  dispara una carga real, y Tourplan queda con MEP 1535,10 · Oficial
  1530,00 · Emisivo 1540,00 — acorde al mercado.

## La causa

El proceso corre dentro de un contenedor Docker, programado con `cron` (el
"reloj despertador" que dispara la tarea todos los días a las 8am). Las
credenciales de Tourplan (URL, usuario, contraseña) se le pasan al
contenedor como variables de entorno.

El problema: **cron no le pasa esas variables a las tareas que dispara**.
Cada mañana, cuando cron lanzaba el bot, lo hacía con una "pizarra en
blanco" — sin las credenciales — así que el bot cortaba en el primer paso,
antes de siquiera mirar el dólar.

**Por qué no avisó nadie**: el aviso por mail también depende de esa misma
configuración (usuario y contraseña de correo). Si la configuración entera
está ausente, el bot no tiene con qué mandar el mail de alerta — así que la
falla quedó guardada solo en un archivo de log que nadie mira a diario.

Al revisar manualmente el contenedor con `docker exec ... env`, las
variables aparecían bien seteadas — lo cual llevó a descartar esta pista al
principio. Eso pasa porque esa forma de revisar hereda el entorno del
contenedor directamente, mientras que cron arranca sus tareas con un
entorno propio y separado. Dos formas de mirar el mismo contenedor, dos
resultados distintos.

## Qué se arregló

Se cambió la forma en que el archivo de configuración (`.env`) llega al
bot: en vez de depender de que cron reparta las variables, ahora el
archivo se monta directamente adentro del contenedor
([docker-compose.yml](../docker-compose.yml)). El bot lo lee del disco en
cada corrida, sin importar qué entorno le haya dado cron.

Además, como la referencia guardada para comparar cotizaciones del día a
día (`data/historial_cotizaciones.json`) había quedado pisada por más de un
mes de fallas, se reinició esa referencia antes de la primera carga real —
así la comparación de mañana en adelante es contra un valor fresco, no
contra algo de hace 40 días.

## Señales de que puede estar pasando de nuevo

No hace falta acceso al servidor para notar esto. Alcanza con comparar de
vez en cuando:

- El Dólar Oficial/MEP/Emisivo en Tourplan (menú System → Code Setup →
  Exchange Rates) se ve igual varios días seguidos, aunque el mercado se
  mueva.
- Comparado contra una casa de cambio online conocida, la diferencia es
  mayor a un par de puntos porcentuales sin una razón de mercado clara.
- No llegó ningún mail de alerta del bot en varios días — pero tampoco hubo
  confirmación de que las cargas salieron bien.

## Si vuelve a pasar

### Sin acceso al servidor (la mayoría del equipo)

1. Anotá la fecha, el valor que ves en Tourplan y el valor real de mercado
   que te hicieron notar.
2. Avisá a quien administra el servidor (o a soporte técnico) con esos dos
   datos — no hace falta que entiendas la causa, con la comparación
   alcanza.
3. No cargues el valor corregido a mano en Tourplan todavía: si el bot se
   arregla y corre después, puede pisar tu carga o generar una fila de
   continuación confusa. Esperá la confirmación técnica.

### Con acceso al servidor (quien administra el contenedor)

1. Revisar si la corrida de hoy completó el flujo entero (buscar
   `Proceso completado OK` en el log del día).
2. Si cortó antes de llegar a Tourplan, revisar el mensaje de error — la
   falta de variables de entorno es la causa ya conocida, pero puede haber
   otras.
3. Si el historial de referencia quedó desactualizado por varios días de
   fallas, respaldarlo y reiniciarlo antes de la próxima carga real, para
   que la comparación no bloquee por una diferencia acumulada que no es un
   error de datos.

Comandos de referencia:

```bash
# Ver si la corrida de hoy terminó bien
docker exec tourplan-fx-bot cat logs/tourplan_fx_bot.log

# Ver historial de éxitos/fallas de los últimos días
docker exec tourplan-fx-bot sh -c 'grep -h "Proceso completado OK\|Faltan variables\|Cotizaciones obtenidas" logs/tourplan_fx_bot.log*'

# Respaldar y reiniciar la referencia de comparación (no compara contra nada
# hasta la próxima carga exitosa; ver primero el archivo antes de tocarlo)
docker exec tourplan-fx-bot cat data/historial_cotizaciones.json
docker exec tourplan-fx-bot sh -c 'mv data/historial_cotizaciones.json data/historial_cotizaciones.json.bak_$(date +%Y%m%d)'

# Disparar una carga real ahora mismo, sin esperar al cron de mañana
# (esto SÍ actualiza Tourplan de verdad)
docker exec tourplan-fx-bot python3 main.py
```

**Ojo**: si después de todo esto el bot sigue sin poder mandar el mail de
alerta, revisar que `SMTP_HOST` y `ALERTA_EMAIL_TO` estén completos en el
`.env` — sin eso, cualquier falla futura vuelve a quedar en silencio.

## Lo que queda pendiente

La causa puntual ya está resuelta, pero el problema de fondo — que la única
vía de alerta depende de la misma configuración que puede fallar — sigue
latente. La mejora recomendada es un aviso de "estoy vivo" independiente
del `.env` de la app (por ejemplo, un ping diario a un servicio externo de
monitoreo), para que una falla de este tipo se note en horas y no en 40
días.
