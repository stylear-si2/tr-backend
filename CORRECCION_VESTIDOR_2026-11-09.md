# Corrección del vestidor virtual — 9 de noviembre de 2026

## Alcance y decisiones

Trabajo exclusivo en `C:\MATERIAS\SI2\stylear-si2`, sobre la copia local `stylear_si2_local`. No se modificaron Presentacion ni Railway. Se conserva el catálogo público y el modo de prueba con foto, además del vestidor en vivo. Únicamente el rol CLIENTE autenticado puede iniciar operaciones de IA.

El cliente decidió que los resultados de las fotos no deben conservarse en el servidor. Solo se guardan permanentemente en el teléfono si pulsa Guardar. No hay historial ni URL pública de resultados nuevos.

## Identidad, sesiones y proveedor

Las rutas de registro de prueba, visor, creación de sesión, cambio de prenda y pausa validan al cliente y su empresa. La sesión de streaming queda vinculada a empresa, usuario y sesión de acceso; las operaciones de otra persona o empresa devuelven 404. La identidad de cliente para telemetría se obtiene en el servidor, no del cliente_id enviado por la app.

El visor WebView recibe una capacidad aleatoria de alcance exclusivo al vestidor, válida durante diez minutos. No se inserta el JWT de la cuenta en el HTML, URL ni JavaScript. Las solicitudes con esa capacidad revalidan el usuario y la sesión. El HTML utiliza no-store y no-referrer. Se mantiene la cámara frontal/trasera y el cambio de prenda sobre el socket existente.

Se limita a una sesión activa por cliente, cinco por empresa y 120 segundos por sesión. Crear sesiones admite tres intentos por minuto y cambiar prenda veinte por minuto; también hay un límite agregado de empresa. Las negociaciones iniciales tienen tiempo máximo. Los sockets y clientes HTTP se cierran al pausar, finalizar, fallar o apagar el backend. Un control cada cinco segundos corta sesiones revocadas. Mobile suspende el streaming y libera la cámara al ir a segundo plano o abandonar el visor. Solo se concede permiso de cámara, no de micrófono.

El endpoint heredado token-en-vivo devuelve 410 a clientes autenticados: emitir credenciales directas del proveedor permitiría saltar el cierre y límites del servidor. La app actual usa sesion-realtime y no necesita ese endpoint.

Los errores devueltos por Decart se sustituyen por mensajes controlados con una referencia aleatoria. Los registros incluyen operación, clase de error y estado, sin texto original de la excepción, URL, cuerpo del proveedor ni claves. La prueba de rechazo 401 usa una clave ficticia y verifica que no aparece en respuesta ni logs. No se rotaron claves reales ni se inspeccionaron registros históricos de Soporte.

## Foto y guardado

El procesamiento usa un directorio temporal privado por solicitud dentro de .private/vestidor_temporal. Los originales, intermedios y resultados de ese trabajo se eliminan mediante un bloque de limpieza incluso si falla el motor. La respuesta devuelve el JPEG en base64 con Cache-Control: no-store, private; no devuelve imagen_resultado_url. Se admiten JPEG, PNG o WebP, hasta 10 MB y 16 MP. Hay una prueba de foto simultánea por cliente y tres solicitudes por minuto. La sesión se vuelve a comprobar antes de entregar el resultado.

El resultado se presenta con Image.memory. El cambio de sesión descarta el resultado y se rechazan respuestas tardías de otra empresa o sesión. La imagen no se descarga automáticamente a documentos ni a galería.

Guardar abre el selector nativo de Android con ACTION_CREATE_DOCUMENT, tipo image/jpeg y destino local. El cliente elige dónde crear la foto. El archivo queda fuera del directorio privado de la app y puede conservarse al desinstalarla. Cancelar no guarda la foto. El botón usa un canal separado y no modifica la descarga de reportes existentes. El guardado implementado corresponde a Android; no se afirma su soporte en iOS.

La carpeta antigua /static/tryon queda bloqueada por el servidor de archivos, incluidas rutas codificadas o normalizadas. Los archivos heredados no se borraron del disco ni de Git; bloquear su URL en esta copia no elimina copias históricas publicadas previamente.

## Verificación

- 38 pruebas backend aprobadas: identidad, roles, aislamiento por usuario/empresa, límites, duración, cierre, revocación, excepción con clave ficticia, privacidad estática y limpieza de fotos en éxito y fallo.
- 103 pruebas Flutter aprobadas, incluyendo autenticación de carga, resultado en memoria, guardado explícito/cancelación y bloqueo de visitantes.
- flutter analyze sin problemas.
- APK Android debug compilado con API local y Firebase deshabilitado; configuración de Firebase pendiente por decisión del usuario.
- API local responde 200 en salud y 401 para visor e inicio de IA sin sesión.

Las llamadas a Decart y las fotos de las pruebas fueron simuladas. No se consumieron créditos ni se enviaron fotos personales a proveedores. La calidad visual con Decart real y la creación física del archivo mediante el selector del teléfono requieren la prueba manual del cliente; no se declaran comprobadas por las pruebas simuladas.

## Operación

Los controles y sesiones actuales son de memoria del proceso, coherentes con el arranque local y el Dockerfile actual de un proceso Uvicorn. Para varias réplicas o workers deben centralizarse antes los límites/capacidades/sesiones; no se considera verificado ese despliegue distribuido. La limpieza temporal cubre el fin normal y las excepciones del trabajo, no garantiza ejecución de finally ante un corte abrupto del proceso o del equipo.

Si Soporte utilizó la versión vulnerable con credenciales reales, el administrador debe revisar la exposición histórica y rotar la clave afectada. Esta reparación local no demuestra que haya ocurrido una exposición real ni publica automáticamente la corrección en Soporte.

APK debug instalado en el Redmi mediante adb install -r (Success) y app abierta, conservando datos. Se mantiene el reenvío USB a la API local. Esto no equivale a una prueba física de inferencia Decart ni de guardar mediante el selector.
