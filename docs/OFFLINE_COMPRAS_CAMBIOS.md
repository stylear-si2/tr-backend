# Compras pendientes y cambios offline

Web: Operaciones offline. Flutter: nube tachada → Operaciones offline.

## Cliente

- Reservar conserva el flujo existente y su límite de unidades.
- Compra pendiente permite elegir unidades del catálogo descargado sin fecha de reserva ni confirmación de efectivo.
- Al sincronizar se obtiene una cotización actualizada: no se crea una venta, no se aparta stock y no se modifica el carrito online.
- En Historial, el cliente acepta el total y continúa con Stripe o QR. La orden se crea con una clave estable y los precios/stock se verifican otra vez. Reintentar el mismo pago reutiliza la orden.
- Si los precios cambiaron antes del pago, actualizar cotización permite revisar el nuevo importe. Con una orden existente hay que resolverla desde Mis pagos antes de iniciar otro intento.
- Cambios muestra las compras elegibles descargadas, el plazo conocido y sus líneas. Se guardan motivo, cantidad y un reemplazo opcional. La solicitud se valida nuevamente al sincronizar; guardar offline no garantiza aprobación ni existencia del reemplazo.

## Personal autorizado

- Administrador, encargado y cajero conservan la venta de contado offline.
- Cambios permite guardar una inspección/aceptación o rechazo de solicitudes descargadas. Aceptar requiere confirmar recepción física. La versión de la solicitud evita sobreescrituras; repetir la misma inspección ya aplicada no la duplica.
- Para cambios descargados en LISTO, con diferencias liquidadas y reemplazo reservado, se puede registrar la entrega física. El servidor confirma inventario y comprobante al sincronizar. Si se produjo un conflicto, la operación queda visible para conciliación.
- La aceptación de una diferencia, su cobro electrónico y las opciones que requieren precios centrales se continúan en el flujo online existente.

## Persistencia y aislamiento

Flutter actualiza SQLite de versión 1 a 2 conservando la cola previa. La cotización y la posventa se guardan por empresa, usuario y roles. La PWA amplía los sobres cifrados existentes en IndexedDB. Los estados pendientes, confirmados, rechazados o en conflicto se conservan después de reiniciar; un intento de envío no borra operaciones.

No requiere migración de PostgreSQL ni nuevas claves. Es necesario desplegar backend/web y recompilar la APK para probar todos los controles nuevos. Los correos y avisos siguen el flujo existente después de la confirmación del pago; cotizar una compra no envía una nota de venta.
