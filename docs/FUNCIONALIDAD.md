# Documentación funcional — Tourplan FX Bot

Qué hace este bot y qué esperar de él, sin entrar en detalles de código
(para eso ver [CODIGO.md](CODIGO.md)).

## Qué problema resuelve

Todos los días alguien tenía que entrar a Tourplan NX y actualizar a mano 3
tipos de cambio. Es una tarea repetitiva, propensa a error humano (tipear
mal un valor, olvidarse un día) y que depende de que una persona esté
disponible a esa hora. El bot automatiza esa carga diaria.

## Qué carga

Tres tipos de cambio, todos los días, sin intervención humana:

| Tipo de cambio | De dónde sale |
|---|---|
| **Dólar MEP** | Valor de venta publicado en dolarhoy.com |
| **Dólar Oficial** | Valor de venta publicado en dolarhoy.com |
| **Dólar Emisivo** | Se calcula automáticamente: Dólar Oficial + 10 pesos |

## Cuándo corre

Una vez por día, a las 8am (tarea programada / cron en el servidor). No
requiere que nadie lo dispare manualmente.

## Qué hace, paso a paso

1. **Consulta la cotización** del día en dolarhoy.com (Dólar MEP y Dólar
   Oficial; el Emisivo se calcula).
2. **Valida que el valor sea razonable**: lo compara contra el último valor
   cargado el día anterior. Si la diferencia es mayor al 15% (umbral
   configurable), el bot **no confía en el dato** y no lo carga.
3. **Carga los 3 valores en Tourplan NX**, simulando en el navegador los
   mismos pasos que haría una persona (login, menú, filtros, edición de
   cada fila).
4. **Si algo sale mal en cualquier paso anterior**, el bot corta ahí mismo
   (nunca deja Tourplan a medio actualizar) y **manda un email de alerta**
   explicando qué pasó.

## Qué pasa si algo falla

| Situación | Qué hace el bot | Qué tiene que hacer una persona |
|---|---|---|
| No se pudo obtener la cotización del día (el sitio de origen está caído o cambió de formato) | Corta antes de tocar Tourplan. Envía email de alerta. | Revisar el sitio de origen; si el bot no se recupera solo al otro día, avisar a soporte técnico. |
| La cotización obtenida varía más de 15% respecto de ayer (posible dato erróneo) | **No carga nada en Tourplan.** Envía email de alerta con los valores obtenidos. | Si la variación es real (movimiento genuino del mercado), cargar el valor a mano en Tourplan ese día. Al día siguiente el bot va a tomar ese valor cargado a mano como referencia, así que no hace falta ninguna otra acción. |
| La cotización es correcta pero falla la carga en Tourplan (ej. Tourplan caído, cambio en la pantalla, sesión vencida) | No deja nada a medio cargar. Envía email de alerta con los valores que debían cargarse. | Cargar los valores a mano en Tourplan ese día; avisar a soporte técnico si el problema persiste al día siguiente. |
| Todo funcionó bien | No pasa nada visible — el bot no manda email cuando todo sale OK. | Ninguna. |

En todos los casos de falla, el email de alerta incluye qué pasó y (cuando
corresponde) los valores concretos que se habían obtenido, para que la
persona que lo reciba pueda decidir rápido si hace falta cargar algo a mano.

## Cómo se protege de cargar un valor erróneo

El paso de validación corre **siempre antes** de tocar Tourplan. Si el valor
obtenido se aleja demasiado del último cargado, el proceso se corta ahí —
nunca se llega a modificar nada en Tourplan con un dato dudoso. Ese umbral de
variación (15% por defecto) es configurable.

## Requisitos para que funcione

- Credenciales de un usuario de Tourplan con acceso a System → Code Setup →
  Exchange Rates (actualmente el usuario `BOT`).
- Una sesión de Tourplan pre-autenticada (por las licencias limitadas de
  Tourplan, el bot reutiliza una sesión guardada en vez de loguear en cada
  corrida; hay que dejarla lista una vez).
- Configuración de un servidor de email (SMTP) para poder mandar las alertas
  — sin esto, el bot sigue funcionando pero las fallas no se notifican por
  mail (solo quedan registradas en el log del servidor).

## Qué NO hace

- No corrige ni ajusta tipos de cambio cargados manualmente por otra
  persona: solo actualiza la fila vigente de cada una de las 3 divisas que
  gestiona.
- No reintenta la carga automáticamente si Tourplan cambió de versión o de
  pantalla — en ese caso hace falta ajustar el bot (ver
  [CODIGO.md](CODIGO.md)) y, mientras tanto, cargar a mano.
- No decide si una variación grande de cotización es "correcta" o no: eso
  queda a criterio de la persona que recibe la alerta.
