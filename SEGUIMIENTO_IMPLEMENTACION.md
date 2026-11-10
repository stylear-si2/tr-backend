# Seguimiento de implementación de StyleAR

Autor: Angeles Rosales Colque.

Este archivo reúne el arranque local, las reparaciones y el avance por bloques. Se actualizará con cada implementación.

## Cierre local CU22–CU27 (07/11/2026; registro histórico)

Implementación de los siete bloques completada en web y Flutter, incluida administración SaaS móvil. R2 conectado y verificado con respaldo/restauración reales sobre datos sintéticos: empresa con archivos y aislamiento, y copia global restaurada en otra base vacía. Base de trabajo con 18/18 migraciones. Regresión final: 51 comprobaciones backend aprobadas (48 de integración CU22–27, una adicional de renovación/cambio de empresa y dos con R2 real), 33 web y 83 Flutter. Se retiraron bases, cuentas y objetos de ensayo.

Backend local http://127.0.0.1:8000 y web http://127.0.0.1:4200/empresa iniciados y comprobados con HTTP 200, junto con el directorio de empresas. No iniciar otra instancia en esos puertos mientras sigan activos. Los procesos y logs del arranque están en .private/local_procesos.json y .private/local_{backend,frontend}.{out,err}.log.

El APK release de prueba ya instalado utiliza API_URL=http://127.0.0.1:8000. Al cierre ADB no detecta el Redmi. Para usarlo con esta API, reconectar el USB autorizado y ejecutar `adb reverse tcp:8000 tcp:8000`. La cuenta temporal de Atelier Norte fue retirada; cerrar esa sesión y elegir la empresa real StyleAR para las pruebas locales. No se borraron datos de la aplicación ni se cambió su instalación.

Límite externo: BCP QR real no puede habilitarse sin la documentación y credenciales sandbox del banco. Se conserva la simulación identificada como tal; no se anuncia conexión bancaria real. No hay despliegue ni publicación de APK de tienda.

Validación física confirmada: aviso en primer plano, segundo plano, proceso cerrado, pantalla bloqueada y bandeja con permiso Android desactivado/reactivado. Cambio de cuenta, renovación y categoría silenciada tienen cobertura automatizada de servidor; no se acreditan como ensayos físicos. Dictado y recorrido físico completo de las pantallas administrativas siguen sin acreditación manual, sin pendientes de implementación identificados por las pruebas.

## Desarrollo local en Windows

Desde `tr-backend`, usando el entorno instalado:

```powershell
.\.venv\Scripts\python.exe init_db.py
if ($LASTEXITCODE -eq 0) {
    .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
}
```

En otra terminal, desde `tr-frontend`:

```powershell
npm start -- --host 127.0.0.1
```

Web: http://127.0.0.1:4200/
API: http://127.0.0.1:8000/docs

La conexión se toma del `.env` del backend. Las migraciones versionadas se
registran en `schema_migrations`; volver a ejecutar el inicializador las omite.
No registrar versiones manualmente para eludir errores.

### Reparación local del 24 de octubre de 2026

La primera migración multitenant fallaba porque los SKU `pol1` y `POl1`
colisionaban con el nuevo índice que ignora mayúsculas. Se mantuvo la variante
3 con `pol1` y se cambió únicamente el SKU de la variante 7 a `POl1-7`.
No se eliminaron variantes ni sus referencias.

Se corrigieron las migraciones antiguas de tipos de movimiento para aceptar
`DEVOLUCION_VENTA`, evitando fallos al ejecutarlas sobre datos de devoluciones.
Las cuatro migraciones versionadas quedaron aplicadas.

Respaldo previo: `.private/antes_migraciones_20261024.dump`.
Registro del cambio de SKU: `.private/reparacion_sku_20261024.json`.
Estos archivos están excluidos de Git.

## Bloque 1: CU06 — Gestionar ciudades y sucursales

Autor: Angeles Rosales Colque. Fecha: 24/10/2026.

Primer bloque de implementación de las fichas CU06–CU10 del 23/10/2026.
CU06, CU07 y CU09 tienen interfaz web. CU08 y CU10 incluyen además las
consultas autorizadas del proveedor en Flutter, en los bloques posteriores.

### Comportamiento implementado

- El administrador comercial gestiona las ciudades y sucursales de su empresa.
  El administrador SaaS no gestiona ciudades comerciales.
- Las ciudades originales mantienen sus identificadores y pertenecen a StyleAR.
  Si una base heredada contiene referencias de otra empresa, la migración crea
  copias de esas ciudades para esa empresa y actualiza únicamente sus referencias.
- Los nombres de ciudad/departamento no se repiten dentro de una empresa, ignorando
  mayúsculas. Distintas empresas pueden utilizar los mismos nombres.
- Sucursales y direcciones sólo pueden referenciar ciudades de su empresa. La API
  valida la asociación y PostgreSQL añade una barrera de integridad independiente.
- Crear o editar una sucursal requiere una ciudad activa. No se desactiva una
  ciudad con sucursales activas. Una sucursal no se reactiva si su ciudad está inactiva.
- Las desactivaciones conservan los registros. Las pantallas administrativas
  muestran también inactivos para permitir reactivarlos. Los selectores mantienen
  la consulta de ciudades activas como comportamiento predeterminado.
- El alta/reactivación de sucursales controla el límite del plan dentro de una
  transacción con bloqueo de empresa. Las consultas y desactivaciones siguen
  disponibles con suscripción suspendida; las altas y reactivaciones se rechazan.
- Las operaciones administrativas exigen rol de administrador de empresa y el
  permiso correspondiente. Las cuentas comerciales conservan la consulta de
  ciudades de su empresa necesaria para sus direcciones.
- Los respaldos selectivos incluyen las ciudades como datos comerciales y ya
  no como referencias globales.
- Las pantallas CU06 tienen contraste y jerarquía de encabezados revisados con
  AXE. Los formularios modales contienen el foco, cierran con Escape y devuelven
  el foco al control de apertura. La ubicación de sucursal admite coordenadas
  mediante teclado además del mapa.

### Persistencia y arranque

Nueva migración: `2026_10_24_cu06_01`, registrada mediante `init_db.py`.
La API verifica su presencia al iniciar. No ejecutar el SQL manualmente ni
insertar la versión para eludir un error.

```powershell
cd C:\MATERIAS\SI2\Presentacion\tr-backend
.\.venv\Scripts\python.exe init_db.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

En otra terminal:

```powershell
cd C:\MATERIAS\SI2\Presentacion\tr-frontend
npm start
```

Respaldo local previo a CU06:
`.private/antes_cu06_20261024_230425.dump`, excluido de Git.
Se verificó que la migración local conserva IDs/nombres de ciudades y las
referencias de sucursales, y que repetir el inicializador omite la versión aplicada.

### Validación

Las pruebas PostgreSQL usan únicamente una base local desechable `stylear_test_*`
con `DATABASE_URL` y `STYLEAR_TEST_DATABASE_URL` explícitas e idénticas.
No ejecutar la suite de integración sobre la base StyleAR.

100 pruebas aprobadas en la suite de CU06, multitenant, cuentas, CU04/CU05
y fundamentos. Incluyen duplicados, asociación ajena, acceso por ID, historial,
reactivación, cupo concurrente, estado de suscripción, direcciones y restauración.

Pruebas de navegador: `tr-frontend/e2e/cu06.spec.ts`, usando la fixture PostgreSQL
real descrita en `tr-frontend/e2e/README.md`. Comprueban las dos pantallas,
aislamiento, formularios, estados y accesibilidad con AXE.
Resultado: 2 recorridos aprobados, sin incidencias AXE en las vistas comprobadas;
compilación Angular de desarrollo correcta. Las bases desechables se retiraron
al terminar las pruebas.

### Recorrido manual

1. Ingresar como administrador de StyleAR; abrir Ciudades y comprobar los datos originales.
2. Crear una ciudad y una sucursal asociada. Intentar desactivar la ciudad: se debe rechazar.
3. Desactivar la sucursal y luego la ciudad. Ambas deben permanecer visibles como inactivas.
4. Intentar reactivar primero la sucursal: se debe rechazar. Activar la ciudad y luego la sucursal.
5. Ingresar como administrador de otra empresa: no debe ver las ciudades/sucursales de StyleAR.
6. Comprobar que un nombre de ciudad puede repetirse en otra empresa, pero no en la misma.

CU07–CU08 se completan en el bloque 2 descrito más abajo; CU09–CU10 siguen pendientes. Este bloque no agrega
pantallas administrativas en móvil ni modifica la carga de existencias del CU14.

## Bloque 2: CU07–CU08 — Implementado

Empleados: solo web. Proveedores: administración web y consulta autorizada en web/móvil.

### CU07 — Empleados, solo web

- El administrador registra una cuenta y su empleado en una transacción, o
  asigna una cuenta existente mediante un selector de su empresa.
- Sólo se admiten CAJERO, ENCARGADO_SUCURSAL y PERSONAL_VENTAS como roles laborales.
  No se permite elevar cuentas a administrador desde este módulo ni modificar
  cuentas administrativas mediante vínculos de empleado.
- El encargado consulta únicamente los empleados de su sucursal activa asignada.
  No dispone de formularios de modificación y la API rechaza todas sus escrituras.
  El modelo actual mantiene una asignación de sucursal por empleado.
- El administrador puede editar asignaciones, desactivar y reactivar empleados.
  La desactivación conserva el rol laboral en la ficha; la reactivación pide
  confirmarlo, sin sustituirlo por un rol de mayor privilegio.
- Los cambios de rol, asignación o estado cierran las sesiones afectadas. Los
  datos e historial se conservan; no se elimina la cuenta por desactivar al empleado.
- Los límites de usuarios y empleados se comprueban dentro de la transacción;
  un fallo no deja una cuenta sin empleado. Los códigos se separan por empresa.
  Se mantiene la regla existente de usuario y correo únicos globalmente.

### CU08 — Proveedores, web y móvil

- El administrador registra y modifica proveedores, gestiona su estado, crea
  cuentas vinculadas o vincula cuentas comerciales existentes de su empresa.
- Crear la cuenta, asignar PROVEEDOR y guardar el vínculo es una sola operación.
  Vincular una cuenta existente asigna ese rol y exige ingresar nuevamente.
  No depende de crear primero un usuario PROVEEDOR desde Usuarios, que requería
  un vínculo todavía inexistente.
- Cada cuenta tiene como máximo un vínculo activo de proveedor. La plataforma
  nunca selecciona arbitrariamente el primero entre varios vínculos. Distintas
  cuentas pueden representar a un mismo proveedor. La migración falla si hay
  ambigüedades heredadas, en lugar de retirar autorizaciones silenciosamente.
- El administrador asocia o retira productos de su empresa desde la ficha del
  proveedor. Se conserva el vínculo histórico con estado inactivo al retirarlo.
- Los paneles web y Flutter muestran únicamente el perfil, productos, existencias
  y entregas autorizados del proveedor vinculado; no ofrecen acciones de escritura.
  El servidor obtiene el proveedor de la sesión, no de un ID enviado por el móvil.
- Desvincular la cuenta o desactivar el proveedor revoca el acceso y cierra las
  sesiones afectadas. La reactivación recupera los vínculos conservados y exige
  nuevo ingreso. Las entregas e inventarios históricos no se eliminan.
- Flutter ofrece navegación propia del proveedor hacia su panel y cuenta, sin
  cargar carrito, reservas de cliente ni gestión de catálogo. Permite actualizar
  y reintentar, retira datos ante rechazos y descarta respuestas de otra sesión/empresa.
- Inventario conserva un selector mínimo de proveedores activos para el encargado,
  sin concederle acceso a la administración CU08.

### Migración, pruebas y respaldo del bloque 2

Migración: `2026_10_24_cu07_cu08_01`. Añade el rol laboral conservado y la
unicidad del vínculo activo por cuenta de proveedor. `init_db.py` la registra;
la API exige esta versión antes de servir peticiones.

Respaldo local previo: `.private/antes_cu0708_20261024_233948.dump`.
Se verificó que se conservan ciudades, referencias de sucursales, empleados y
sus estados. El respaldo es privado y está excluido de Git.

Validaciones:

- 100 regresiones de backend de los bloques anteriores aprobadas.
- 14 pruebas PostgreSQL de CU07/CU08 aprobadas, incluyendo aislamiento, permisos,
  roles prohibidos, cuotas concurrentes, atomicidad, cierre de sesiones, cuentas
  y vínculos de proveedores, productos, stock y entregas reales.
- 4 recorridos Playwright de CU07/CU08 aprobados, con AXE sobre las pantallas
  y formularios verificados: administrador de empleados, encargado de consulta,
  administrador de proveedores y panel propio del proveedor.
- 24 pruebas Flutter aprobadas; análisis sin incidencias y compilación web correcta.
  Las pruebas móviles usan HTTP simulado; el aislamiento real del panel se valida
  con PostgreSQL/API. No sustituyen una prueba manual en teléfono físico.
- Compilación Angular de desarrollo correcta.

### Recorrido manual del bloque 2

1. Iniciar backend y frontend con los comandos del comienzo de este archivo.
2. Como administrador, abrir Empleados, crear una cuenta y seleccionar sucursal
   y rol. Editar su cargo, desactivar y reactivar confirmando el rol laboral.
3. Como encargado, abrir Empleados: sólo debe consultar registros de su sucursal.
4. Como administrador, crear un proveedor, abrir su ficha con Editar y crear
   una cuenta vinculada. Asociar un producto propio desde la misma ficha.
5. Ingresar con esa cuenta de proveedor: comprobar sus productos, existencias
   y entregas. Repetir en Flutter mediante el panel Mi proveedor.
6. Retirar el vínculo o desactivar el proveedor desde el administrador. La sesión
   anterior debe dejar de tener acceso. Reactivar y volver a ingresar para consultar.

Móvil, desde `tr-mobile`, con un emulador Android iniciado:

```powershell
flutter run --dart-define=API_URL=http://10.0.2.2:8000
```

En teléfono físico conectado a la misma red, sustituir `10.0.2.2` por la IPv4
local de la PC. El backend debe escuchar en `0.0.0.0` como se indica al comienzo.
El móvil usa Railway por defecto si se omite `API_URL`.

### Pendientes

CU09 se desarrolla en el bloque 3 de este mismo archivo. El siguiente bloque
es CU10 (productos, variantes y promociones, con el alcance móvil acordado).
Las consultas de productos del proveedor ya están conectadas por CU08; la
administración completa de CU10 se revisará en su bloque. CU14 no se modifica
para cargar stock al crear productos.

Este es el único archivo de seguimiento nuevo: reemplaza ARRANQUE_LOCAL.md e
IMPLEMENTACION_CU06_2026-10-24.md. Los informes que ya existían en el repositorio
sobre otros bloques se conservan como antecedentes.


## Bloque 3: CU09 — Gestionar clasificaciones del catálogo

Autor: Angeles Rosales Colque. Fecha: 24/10/2026.

Alcance: categorías, tallas, colores, marcas, temporadas y colecciones, sólo web.
El administrador comercial consulta, crea, edita, desactiva y reactiva sus
clasificaciones. El móvil no incorpora administración de CU09.

### Comportamiento implementado

- Las seis escrituras verifican la empresa y el administrador dentro de la
  transacción, respetan la vigencia del plan y registran la auditoría en el mismo
  commit. Un fallo revierte tanto el registro como su auditoría.
- Las operaciones de clasificación comparten los bloqueos administrativos de
  CU04/CU07/CU08. Los duplicados concurrentes devuelven conflicto, conservando una
  sola clasificación. Se ignoran mayúsculas y espacios exteriores en el nombre.
  Los inactivos también reservan su identidad para conservar el historial.
- La identidad de talla incluye el tipo de prenda; la de temporada incluye el
  año; la de colección incluye su temporada. Distintas empresas pueden utilizar
  los mismos nombres sin compartir sus registros.
- Categorías: padre activo de la misma empresa; no se admiten ciclos ni elegir
  descendientes como padre. No se desactiva una categoría con hijos o productos
  activos. Para desactivar una jerarquía se comienza por los hijos.
- No se desactivan tallas o colores con variantes activas, marcas con productos
  activos, temporadas con colecciones activas ni colecciones con productos
  activos asociados. Las relaciones históricas no se eliminan.
- Categorías y colecciones sólo se reactivan con su padre/temporada activo.
  Un intento inválido conserva los datos y su estado anterior.
- La API limita los textos a la capacidad de las columnas. Las tallas admiten
  SUPERIOR, INFERIOR o VESTIDO y medidas opcionales entre 0,01 y 9999,99 cm;
  se rechazan valores no finitos. Se conserva la validación hexadecimal de
  colores y el intervalo 2000–2200 para los años.
- Los formularios web validan nombres y longitudes, muestran errores de API
  dentro del diálogo y conservan lo escrito ante un rechazo. Se confirma cada
  cambio de estado. La consulta fallida ofrece Reintentar consulta.
- Se corrigió el formulario de marcas que permanecía abierto y vacío después
  de guardar. Las seis pantallas usan encabezados, mensajes y contraste
  revisados, con los modales accesibles compartidos.

### Base de datos y validación

Este bloque no necesita cambios de esquema ni una nueva migración. Se verificó
la base local en modo de lectura: las seis versiones existentes están aplicadas,
no hay ciclos ni duplicados en las clasificaciones y la API inicia con health 200.
También se verificó la inicialización de una base desechable y su segunda
ejecución, que omite correctamente las migraciones ya aplicadas.

Las pruebas de API y navegador usan bases PostgreSQL locales desechables;
no insertan datos de prueba en StyleAR.

- 22 casos de CU09 aprobados en PostgreSQL/API: CRUD de las seis clasificaciones,
  aislamiento, permisos, historial, validación, duplicados concurrentes, ciclos
  concurrentes, dependencias y reversión de datos/auditoría antes del commit.
- 16 pruebas de regresión multitenant aprobadas.
- Compilación Angular de desarrollo correcta.
- 8 recorridos Playwright de CU09 aprobados: verifican el CRUD de las seis pantallas,
  el rechazo de duplicados y la recuperación tras un fallo de comunicación,
  además de AXE en listados y formularios.

### Recorrido manual del bloque 3

1. Iniciar backend y frontend con los comandos del comienzo de este archivo.
2. Como administrador, abrir cada clasificación; crear, buscar, editar,
   desactivar y reactivar un registro sin asociaciones activas.
3. Crear una categoría hija y comprobar que el padre no puede desactivarse ni
   elegirse a sí mismo o a su hija como padre.
4. Crear temporada y colección. Intentar desactivar primero la temporada:
   debe explicar el conflicto. Desactivar la colección y después la temporada;
   la colección no podrá reactivarse hasta reactivar su temporada.
5. Probar un nombre duplicado: el diálogo conserva los datos y explica el
   conflicto. Revisar los filtros Todos/Inactivos para recuperar registros.
6. Con otra empresa, verificar que sólo aparecen sus propias clasificaciones.

CU10 se implementa en el bloque 4 de este archivo. CU14 no se utiliza
para cargar stock al crear productos.

Los servicios temporales de prueba se detuvieron y sus dos bases desechables
se eliminaron al cerrar el bloque. No se dejan backend ni frontend ejecutándose.


## Bloque 4: CU10 — Gestionar productos, variantes y promociones

Autor: Angeles Rosales Colque. Fecha: 25/10/2026.

Alcance confirmado con la ficha del 23/10/2026: administrador de empresa en web;
proveedor con consulta de sus productos y existencias en web y Flutter. No se
incorpora carga de stock al crear productos o variantes; corresponde a CU14.

### Comportamiento implementado

- El alta de producto permite agregar varias variantes y precios iniciales en
  el mismo formulario. Se confirma mediante un único POST /api/v1/productos;
  cualquier error revierte producto, variantes, imágenes, precios y vínculos.
  También se admite guardar el producto y completar sus variantes después.
- Desde cada producto se abren sus variantes/precios o una promoción con ese
  producto preseleccionado. Se conservan los formularios separados para la
  administración posterior.
- Todas las escrituras de CU10 verifican empresa, administrador, versión de
  acceso y vigencia del plan dentro de la transacción. El cupo de productos se
  cuenta después del bloqueo de empresa, tanto al crear como al reactivar.
  La auditoría se guarda con el identificador afectado en el mismo commit.
- Categorías, marcas, colecciones, temporadas y proveedores deben ser propios
  y activos. Las tallas deben corresponder al tipo de prenda. Cambiar el tipo
  del producto se rechaza si contradice las tallas de sus variantes existentes.
- No se repite un SKU dentro de la empresa, ignorando mayúsculas y espacios
  exteriores, ni una combinación talla/color dentro del producto. Las
  características no aplicables también cuentan como una combinación. Los
  inactivos conservan su identidad para no duplicar registros históricos.
- Los textos, tipos de prenda/corte, medidas e importes se validan antes del
  guardado. Las medidas deben estar entre 0,01 y 9999,99 cm; los precios son
  positivos, finitos y admiten hasta dos decimales.
- Se conserva el administrador de imágenes por URL o archivo del equipo. Se
  valida formato, tamaño de archivo de hasta 5 MB, URLs y una sola principal.
  Retirar una imagen la desactiva; reincorporar su URL conserva su identificador.
  La URL opcional del modelo 3D también se conserva al editar desde web.
- Cambiar proveedores o colecciones retira sus vínculos mediante estado lógico,
  conservando las relaciones anteriores. Las consultas administrativas, de
  proveedores, catálogo y recomendaciones utilizan únicamente vínculos activos.
- No se desactiva un producto o variante con existencias reservadas. La creación
  de reservas comparte bloqueos de producto/variante antes de comprometer stock,
  para evitar aceptar una variante desactivada durante la operación.
- Desactivar un producto desactiva sus variantes sin borrar inventario ni precios.
  Reactivar el producto no reactiva automáticamente todas sus variantes: éstas
  se habilitan individualmente, validando nuevamente producto, talla y color.
- Los precios programados conservan el precio anterior hasta su fecha de inicio.
  Un precio temporal mantiene también el tramo posterior cuando corresponde.
  El historial distingue vigente, programado, finalizado y reemplazado.
- Las promociones validan fechas, porcentaje y productos/sucursales de la empresa.
  Un descuento fijo se rechaza si supera un precio asociado en el período; bajar
  un precio también comprueba los descuentos fijos que le aplicarían.
- La web permite seleccionar sucursales y conserva ese alcance al editar.
  Sin sucursales seleccionadas, aplica a toda la empresa. Los vínculos retirados
  de productos/sucursales quedan históricos y no siguen aplicándose al catálogo
  ni al precio utilizado por el carrito. Una promoción vencida requiere ajustar
  su vigencia antes de reactivarse.
- El proveedor no puede escribir productos, variantes, precios ni promociones.
  En Flutter puede desplegar un producto autorizado y consultar material, género
  y existencias por SKU/sucursal con los datos recibidos por su sesión. No se
  agregan controles de creación, edición ni carga de stock.

### Migración y conservación de datos

Nueva migración versionada: 2026_10_25_cu10_01, ejecutada mediante init_db.py.
Añade estados lógicos a producto_coleccion, promocion_producto y
promocion_sucursal; admite características no aplicables y protege las
combinaciones únicas de variantes incluyendo valores nulos. La API comprueba
esta versión al arrancar.

Se verificaron las combinaciones heredadas antes de migrar. Respaldo local:
`.private/antes_cu10_20261025_001244.dump`, excluido de Git.
La migración se aplicó en la base local y una segunda ejecución omitió las siete
versiones ya aplicadas. Se compararon los productos, variantes, precios,
vínculos, promociones y existencias antes/después sin cambios en sus datos.

### Validación del bloque

- 22 casos PostgreSQL/API de CU10: registro conjunto y reversión, referencias,
  permisos, aislamiento, SKU/combinaciones concurrentes, cuotas concurrentes,
  historial, dependencias de reservas, precios futuros/temporales y promociones.
- 52 regresiones aprobadas de CU07/CU08, CU09 y multitenant; las 16 multitenant
  se volvieron a ejecutar tras reforzar la validación de reservas.
- 3 recorridos Playwright aprobados con AXE en las pantallas y formularios
  revisados: producto con imagen/variante/precio, variantes e historial de
  precios, y promociones con sucursales y validación de descuentos.
- Angular: compilación de desarrollo correcta.
- Flutter: 25 pruebas aprobadas, análisis sin incidencias y compilación web
  correcta. El panel móvil se
  prueba con HTTP simulado; el aislamiento y rechazo de escrituras del proveedor
  también se prueban sobre PostgreSQL/API. No sustituye una prueba en teléfono.

### Recorrido manual del bloque 4

1. Iniciar backend y frontend con los comandos del comienzo del archivo.
2. Como administrador, abrir Productos, completar sus asociaciones e imágenes
   y agregar variantes iniciales con precios. Confirmar el registro.
3. Abrir Variantes y precios desde su fila y comprobar el filtro del producto.
   Editar la variante o agregar otra combinación. Intentar repetir una para
   comprobar que se conserva el formulario y se explica el conflicto.
4. Programar un precio futuro o temporal y revisar el historial. El precio
   actual sigue aplicándose hasta el inicio del nuevo intervalo.
5. Crear una promoción, seleccionar sus productos y sucursales, guardar y
   volver a editar. Comprobar que el alcance se conserva. Intentar un descuento
   inválido o fechas invertidas para revisar el rechazo.
6. Con un proveedor vinculado, consultar sus productos y stock desde web y
   Flutter. En móvil desplegar la ficha de un producto; no debe haber controles
   de escritura. Otra empresa no debe acceder a estos recursos.

Estado: CU06–CU10 implementados en los cuatro bloques registrados. No se
realizaron commits, push ni despliegue remoto. Las pruebas usan bases locales
desechables, independientes de StyleAR.

Al cerrar CU10 se detuvo el servidor temporal y se eliminaron sus dos bases
desechables. No quedan backend ni frontend ejecutándose. La base local StyleAR
conserva sus datos y la migración CU10 aplicada.

## CU16–CU20 · Bloque 1: pagos de prueba por empresa y bases de reintegros y avisos

Fecha: 30/10/2026. Autor: Angeles Rosales Colque.

### Alcance y diferencias con los pagos anteriores

- Cada empresa configura sus medios de prueba desde **Pagos de prueba**. Sólo
  el administrador comercial puede guardar esa configuración. Las credenciales
  se resuelven en el servidor por ID de empresa; no se envían al navegador ni al
  móvil. Las compras nuevas no usan la clave Stripe global de otro comercio.
- Stripe admite únicamente claves `sk_test_`. Sin clave de esa empresa se usa un
  simulador local identificado como tal. El QR es una **simulación de StyleAR**,
  sin cuenta bancaria ni dinero real; el código comienza con
  `STYLEAR-PRUEBA-NO-PAGAR`. La conexión oficial BCP queda pendiente de contrato,
  documentación y credenciales. Su endpoint no confirma pagos: responde 503.
- Abrir una URL de retorno no confirma la compra. Web y Flutter consultan el
  estado del servidor. Las acciones de simulación sólo aparecen cuando el
  servidor las autoriza; no pueden aprobar una sesión Stripe conectada.
- La clave de operación se conserva para reintentar una solicitud cuya respuesta
  se perdió. La API evita crear otra venta o sesión de pago para el mismo intento,
  comprueba el total mostrado y conserva el precio de la orden. Un intento
  pendiente se puede recuperar desde el carrito o el historial.
- Los eventos firmados verifican empresa, sesión, importe, moneda y referencia.
  Repetir una confirmación no vuelve a descontar stock ni duplica la compra.
  La compra y sus movimientos se confirman en una transacción; si falla una
  variante, se revierten también las anteriores. El carrito conserva las unidades
  agregadas después del checkout.
- Si llega dinero y la compra ya no puede completarse, por ejemplo por falta de
  stock o por llegar después de cancelar el intento, se registra un **reembolso
  pendiente**. No se presenta como compra exitosa ni se afirma que ya se devolvió
  dinero. La confirmación del reintegro es otra operación, con referencia propia.
  Stripe test usa una misma clave de reintegro en los reintentos; el simulador
  permite al administrador probar la confirmación sin mover fondos.
- Se conserva la consulta del historial y la compatibilidad de los anticipos de
  reservas anteriores. Su webhook global queda limitado a las operaciones
  heredadas de prueba; las compras nuevas requieren el webhook de su empresa.
- Se corrigió además una omisión del carrito: variantes con talla o color
  opcionales ya no desaparecen del cálculo del total.

Este bloque prepara CU16 y la base técnica de avisos y reintegros. **No completa
CU17–CU20** ni cambia todavía precios por mayor, promociones, políticas,
rutas, cambios de talla o devoluciones comerciales completas.

### Notificaciones y Flutter

- Bandeja propia en web y Flutter, con lectura y consulta de la orden. No depende
  de que llegue un push y sólo muestra la cuenta y empresa autenticadas.
- Registro privado de instalaciones vinculado a la sesión. Cambiar de cuenta,
  retirar el dispositivo, cerrar la sesión o revocar el acceso impide reutilizar
  su registro para enviar información de la cuenta anterior.
- Cola de salida transaccional y trabajador FCM HTTP v1 con reintentos limitados.
  Sin credenciales conserva los pendientes. Registra aceptación del proveedor,
  que **no equivale a entrega en el teléfono**. El aviso del sistema es genérico;
  los detalles se consultan dentro de la aplicación autenticada.
- Flutter incluye Firebase Core/Messaging y activación voluntaria de avisos.
  Inicia sin Firebase configurado y explica esa situación sin bloquear la bandeja.
  El push de este bloque se prepara para Android/iOS; Flutter web usa la bandeja.
- Se retiró el contador local de 15 minutos del QR: no correspondía a un
  vencimiento confirmado por el servidor. El modal web reutiliza el componente
  accesible del proyecto, con foco, Escape y desplazamiento en pantallas pequeñas.

### Configuración local y externa pendiente

La base local tiene `STYLEAR_PAGOS_PRUEBA=1` en el `.env` ignorado por Git. Cada
empresa mantiene sus medios deshabilitados hasta que su administrador los
habilite en **Pagos de prueba**. Para probar el recorrido local no se necesita
una cuenta bancaria ni credenciales Firebase.

Variables opcionales del servidor, sustituyendo `<id>` por el ID real de empresa:

```text
STYLEAR_PAGOS_<id>_STRIPE_SECRET_KEY=sk_test_...
STYLEAR_PAGOS_<id>_STRIPE_WEBHOOK_SECRET=whsec_...
STYLEAR_PAGOS_<id>_EVENTOS_PRUEBA_SECRET=<secreto aleatorio de al menos 32 caracteres>
STYLEAR_FIREBASE_PROJECT_ID=<proyecto>
STYLEAR_FIREBASE_CREDENTIALS=<ruta privada al JSON de cuenta de servicio>
```

El webhook Stripe de compras nuevas es
`POST /api/v1/pagos-empresa/stripe/webhook/<id>`. El endpoint
`POST /api/v1/pagos-empresa/eventos-prueba/<id>` es exclusivamente un contrato
interno de simulación, con HMAC SHA256 sobre `timestamp.cuerpo`, cabeceras
`X-Stylear-Timestamp` y `X-Stylear-Signature`, y antigüedad máxima de cinco minutos.
**No representa una API bancaria BCP.**

Nunca guardar secretos, JSON de servicio o tokens de dispositivos en Git. Para
FCM real se debe aportar el proyecto y una cuenta de servicio autorizada para
Firebase Messaging. Ejecutar el trabajador desde el backend:

```powershell
.\.venv\Scripts\python.exe -m app.modules.ventas_inventario.services.push_worker
```

El trabajador se ejecuta bajo demanda en este bloque; no se instaló un servicio
ni un proceso periódico. En Flutter habilitar Firebase sólo después de configurar
el proyecto y sus aplicaciones nativas, usando estas definiciones:

```text
FIREBASE_ENABLED=true
FIREBASE_API_KEY=...
FIREBASE_APP_ID=...
FIREBASE_MESSAGING_SENDER_ID=...
FIREBASE_PROJECT_ID=...
FIREBASE_IOS_BUNDLE_ID=...
```

Se pasan con `--dart-define=NOMBRE=valor` a Flutter. Android/iOS deben corresponder
al proyecto registrado; iOS requiere además la configuración APNs y capacidades
push correspondientes. No se aportaron credenciales externas ni se verificó
entrega real en teléfono. Las pruebas de transporte usan proveedores simulados.

### Migración y preservación de datos

- Nueva versión `2026_10_30_cu16_bloque1`, archivo
  `database/migracion_cu16_bloque1.sql`: configuración por empresa, claves de
  operación, eventos, reintegros, bandeja, dispositivos y cola push.
- Aplicada en StyleAR local con respaldo previo
  `.private/antes_cu16_bloque1_20261030_141204.dump`.
- También se aplicaron tres migraciones anteriores pendientes en esta base:
  `cu09_unicode_01`, `cu11_cu15_01` y `registro_empresa_01`.
- Se compararon pagos, ventas, existencias y movimientos antes y después:
  conservados. El inicializador se ejecutó dos veces para comprobar repetición
  segura. Se verificó el arranque de la API con la base local actualizada.
- No se cambiaron contraseñas administrativas ni se habilitaron cobros reales.

### Validaciones del bloque

- PostgreSQL/API: **53 pruebas aprobadas**, incluyendo 17 de este bloque,
  regresiones CU11–CU15 y aislamiento multitenant. Incluyen firma inválida,
  eventos repetidos, empresas distintas, importe incorrecto, concurrencia,
  stock insuficiente, reintegros, sesiones y envío FCM simulado.
- Flutter: **36 pruebas aprobadas**, análisis sin incidencias y compilación web
  correcta. Las cuatro pruebas nuevas cubren reintentos con la misma clave,
  separación por empresa, retorno Stripe sin aprobación automática y bandeja
  con lectura, error/reintento y Firebase pendiente.
- Angular: compilación de producción correcta. Conserva avisos existentes de
  dependencias CommonJS (`leaflet`, `qrcode`). La descarga de Google Fonts falló
  temporalmente durante una compilación y pasó al reintentar.
- Edge/Playwright y AXE: recorrido de configuración, QR simulado, estados de
  pago, bandeja, lectura y separación entre empresas; contrastes corregidos.
  Los datos de las pruebas viven exclusivamente en bases locales desechables.

### Recorrido manual para probar

1. Iniciar backend y frontend con los comandos del principio del documento.
   En Flutter ejecutar `flutter pub get` tras incorporar las dependencias y luego
   `flutter run -d chrome` para web, o seleccionar el dispositivo Android/iOS.
2. Ingresar como administrador comercial y abrir **Pagos de prueba**. Habilitar
   Stripe test y/o QR simulado y guardar. No introducir credenciales en esa pantalla.
3. Como cliente de esa misma empresa, agregar prendas con stock y abrir carrito.
   Iniciar QR de prueba y consultar el estado. Simular aprobación; revisar la
   compra y la nueva notificación. Reconsultar no debe duplicar el descuento.
4. Para probar un reintegro, crear un checkout y agotar su stock mediante otra
   operación antes de confirmar. Al recibir la confirmación se mostrará
   **Reembolso pendiente**. El administrador podrá simular su confirmación y el
   cliente verá **Reembolsado**. Estas pruebas no mueven dinero real.
5. En Flutter abrir Perfil → Notificaciones. La bandeja funciona sin Firebase;
   activar avisos explica qué falta mientras no exista configuración externa.

### Reglas reservadas para el siguiente bloque

Compra unitaria con cantidades libres, y compra por mayor en paquetes fijos por
modelo: cuarta de 3, media docena de 6 y docena de 12. Cuarta exige al menos dos
tallas y dos colores; media exige tres de cada uno; docena exige todas las tallas
definidas para ese producto. Un producto con una sola talla no habilita mayorista.
Sin variedad o existencias suficientes se mantiene sólo la compra unitaria,
sin excepciones por escasez. Se podrán combinar paquetes y unidades normales
como conceptos separados. Los precios/descuentos deberán configurarse por empresa.
No se implementaron aún esas reglas en este bloque.

Resultado final de navegador: **2 pruebas Edge/Playwright aprobadas**, con AXE
sin infracciones en configuración, QR, pago pendiente/aprobado, notificaciones
y reintegros pendientes/confirmados. Al cerrar se detuvieron las instancias de
prueba y se eliminaron las tres bases CU16 desechables y sus archivos temporales
de conexión. No quedan backend ni frontend levantados por esta implementación.
No se realizaron commits, push ni despliegue remoto.

## CU16–CU20 · Bloque 2: compra unitaria y paquetes mayoristas

Fecha: 30/10/2026. Autor: Angeles Rosales Colque.

### Aclaración incorporada del usuario

Los descuentos son **porcentajes independientes por producto**, editables para
cuarta, media docena y docena. No se fijaron porcentajes arbitrarios ni se copiaron
los de un modelo a otro. Un paquete sin porcentaje configurado permanece
inhabilitado. La configuración inicial no cambia los precios unitarios existentes.

### Comportamiento terminado en este bloque

- Administración web: Productos → **Descuentos mayoristas**. El administrador de
  la empresa puede configurar cada porcentaje (mayor que 0 y menor que 100,
  hasta dos decimales), habilitar el paquete o deshabilitarlo. Un control de versión
  impide sobrescribir cambios de otro administrador sin actualizar la pantalla.
- Compra unitaria: cantidades libres, de la misma talla/color o de combinaciones
  distintas, siempre sujetas al stock. La cantidad por sí sola nunca convierte
  esa línea en mayorista. El límite de dos unidades de la reserva gratuita ya
  no deshabilita los botones de pago de una compra directa.
- Compra por mayor en web y Flutter: apartado propio en el detalle del producto,
  con elección de sucursal y cantidades por variante para **un paquete fijo**.
  Se pueden agregar varios paquetes y después unidades normales.
- Cuarta: exactamente **3 prendas**, al menos **2 tallas y 2 colores distintos**.
  Media docena: exactamente **6 prendas**, al menos **3 tallas y 3 colores**.
  Docena: exactamente **12 prendas**, al menos una de **cada talla definida en
  las variantes activas del producto**. No agrega una regla de colores para docena.
- Un modelo con una sola talla no admite paquetes mayoristas. Las tallas definidas
  sin existencias siguen siendo necesarias para la docena; no se rebaja la regla
  por falta de stock. Si tiene más de doce tallas, no cabe una docena válida.
- Sin porcentaje, variedad, precio vigente o stock suficiente se explica el motivo
  y se inhabilita la opción. La API vuelve a validar la composición; alterar el
  cliente o seleccionar prendas de otra empresa/modelo no evita estas reglas.
- El porcentaje se calcula sobre el **precio unitario vigente de cada variante**,
  con redondeo monetario a dos decimales. No se acumula con la promoción unitaria.
  Ejemplo: precio vigente Bs 50, cuarta 10% → Bs 45 por prenda en ese paquete.
  Dos unidades normales siguen con su propio precio/promoción, como otra línea.
- El stock requerido suma unidades y paquetes. No se modifica la cantidad de una
  línea del paquete desde el carrito: se retira el paquete completo y se arma
  otro. Las unidades normales mantienen sus controles de cantidad.
- Los paquetes tienen identificación propia e idempotencia para reintentos.
  La venta guarda el tipo de compra, grupo de origen, porcentaje y precio neto.
  El historial y la nota de venta conservan la diferencia entre unidad y paquete.
- Antes de generar el pago se recalculan el precio y la disponibilidad. Si cambia
  el total mostrado, el cliente debe revisar el nuevo resumen. Una orden ya creada
  conserva su instantánea aunque después se edite el porcentaje del producto.
- La confirmación de pago descuenta sólo las líneas originales del carrito;
  conserva unidades o paquetes agregados después. No repite venta ni movimientos
  al recibir otra vez la misma confirmación. Sigue usando los pagos de prueba
  seguros del bloque 1 y su tratamiento de falta de stock/reintegro pendiente.
- Los paquetes de este bloque son para **compra directa**, sin incorporarlos a la
  reserva gratuita. La reserva existente mantiene su límite de dos por variante.

### Migración y datos locales

Versión `2026_10_30_cu17_mayorista`, archivo
`database/migracion_cu17_mayorista.sql`. Agrega configuración por modelo,
agrupación de paquetes en carrito y metadatos comerciales en el detalle de venta.
Las líneas anteriores conservan el concepto `UNIDAD` y su precio histórico.

Aplicada a StyleAR local después de crear el respaldo:
`.private/antes_cu17_mayorista_20261030_151441.dump`.
Se ejecutó el inicializador dos veces y se compararon pagos, ventas, existencias
y movimientos antes/después: conservados. La API inicia con el esquema actualizado.
No se asignaron porcentajes de prueba a productos de la base real local.

### Validaciones

- **69 pruebas de integración aprobadas** en base nueva desechable: las primeras
  16 de mayorista y las 53 de pagos, reservas CU11–CU15 y multitenant.
- Después se aprobaron **4 comprobaciones focalizadas**: tres casos nuevos
  (colores/tallas insuficientes, porcentajes por producto sin acumular promociones,
  cambio de total y rechazo de reserva mayorista) y la repetición del pago mixto
  con sus metadatos de historial. En total, **72 casos distintos verificados**,
  con 19 pruebas propias de este bloque.
- Flutter: **39 pruebas aprobadas**, incluidas tres nuevas de modelos de carrito,
  reintento del paquete con la misma clave, redondeo monetario y selector de
  composición. Pruebas HTTP simuladas; no sustituyen verificación en teléfono.
- Angular: compilación de producción correcta.
- Edge/Playwright: recorrido de administrador configurando el porcentaje por
  producto; cliente armando una cuarta, combinándola con dos unidades, pagando y
  consultando el comprobante con conceptos separados. AXE sin infracciones en
  las pantallas verificadas. Se corrigió el contraste del encabezado del detalle.
- Todos los datos de estas pruebas pertenecen a bases desechables. No se usaron
  credenciales de banco ni se movió dinero real.

### Recorrido manual

1. Iniciar backend, web y/o Flutter con los comandos registrados en este documento.
2. Como administrador: Productos → Descuentos mayoristas. Configurar y guardar
   los porcentajes deseados para ese producto, habilitando cada opción.
3. Como cliente: abrir el producto. En web usar **Comprar por mayor**, debajo de
   la compra unitaria; en Flutter tocar **Comprar por mayor · paquetes**.
4. Elegir sucursal, paquete y distribución. Completar exactamente la cantidad
   del paquete y cumplir tallas/colores. Agregarlo y, si se desea, añadir unidades
   desde el recorrido unitario habitual.
5. En carrito comprobar que unidades y paquete tienen conceptos/precios distintos.
   Eliminar una fila mayorista retira todo ese paquete; no altera las unidades.
6. Pagar con el medio de prueba habilitado por el administrador y revisar el
   detalle del pago. No usar el QR simulado para transferencias bancarias.

### Pendientes obligatorios del cierre final (acuerdo del usuario)

Las conexiones externas se posponen **hasta el cierre**, no se eliminan del alcance.
No se declarará terminado el conjunto CU16–CU20 mientras falte resolver y verificar:

- [ ] BCP: obtener el acceso y documentación oficial, implementar el contrato
  real de cobro QR y conciliación por empresa, validar firmas y verificar un
  cobro/reintegro autorizado. El endpoint 503 actual y el QR simulado no cuentan
  como integración bancaria terminada.
- [ ] Stripe: aportar claves de prueba por empresa y configurar/verificar sus
  webhooks con Stripe, incluyendo confirmación, importe/moneda, repetición de
  eventos y reintegro. Los mocks actuales no sustituyen esa verificación externa.
- [ ] Firebase: configurar proyecto y aplicaciones Android/iOS, credenciales del
  servidor y APNs para iOS; verificar recepción en dispositivos y cierre/cambio
  de sesión. La bandeja funciona ahora, pero no se afirma entrega push real.
- [ ] Completar y probar los demás bloques de las fichas CU16–CU20, incluidos
  políticas, entrega/rutas y los flujos completos de cambios y devoluciones,
  según corresponda a cada ficha.
- [ ] Ejecutar el recorrido integrado final y actualizar aquí la evidencia y
  cualquier requisito externo que necesite datos o acceso del usuario.

Este registro distingue funcionalidad local terminada, conexiones verificadas y
pendientes concretos. Las credenciales externas siguen fuera del repositorio.

Cierre del bloque 2: análisis Flutter sin incidencias y compilación web Flutter
correcta. Se detuvieron los servidores temporales y se eliminaron las tres bases
CU17 desechables y sus archivos de conexión; no quedaron procesos del proyecto
levantados por estas pruebas. Se conserva el respaldo local. Sin commits, push
ni despliegue remoto.

## CU16–CU20 · Bloque 3: CU18 — entregas a domicilio

Fecha: 31/10/2026. Autor: Angeles Rosales Colque.

### Funcionalidad implementada

- Cotización autenticada vinculada con cliente, empresa, sucursal y destino.
  Se puede seleccionar una dirección guardada o escribir otra y confirmar su
  ubicación. Elegir una dirección de otra cuenta es rechazado por la API.
- La cotización guarda distancia, minutos, costo, trazado ORS y ubicación de
  origen. Tiene **15 minutos de vigencia para iniciar el pago**. Web y Flutter
  solicitan aceptar el resumen antes de continuar.
- Cambiar sucursal, dirección, referencia o coordenadas invalida la cotización.
  También se comprueba si cambió la ubicación de la sucursal antes del checkout.
  El costo enviado por el cliente no es una fuente de autoridad: se usa la
  cotización guardada. El precio aceptado queda fijo en la orden ya creada;
  reintentar esa misma orden no recalcula ORS ni duplica compras.
- Se conserva la tarifa previa: hasta 3 km Bs 8; hasta 6 km Bs 12; hasta 10 km
  Bs 18. Fuera de sus tramos no se ofrece un envío gratuito. El límite adicional
  `DELIVERY_MAX_KM`, si existe, puede restringir esa cobertura.
- Se corrigió el formato de respuesta ORS. El POST usa el endpoint `/geojson`,
  correspondiente a `features` y geometría `LineString`; se verifican sus datos.
  Se retiró el cálculo Haversine usado silenciosamente como reemplazo ante
  fallos. Sin clave, sin ruta o con respuesta inválida se comunica el problema;
  no se inventan distancia, tiempo ni costo.
- La entrega operativa se crea **en la misma transacción que confirma la compra
  pagada y descuenta stock**. Una compra pendiente/rechazada o un pago que exige
  reintegro por falta de stock no genera un despacho. Repetir el callback no
  crea otra entrega, aviso ni movimiento.
- El envío se paga junto con la compra mediante Stripe o QR; no se ofrece efectivo
  contra entrega. El antiguo endpoint `delivery/solicitar` sólo reconoce una
  entrega ya existente con el mismo destino: no agrega cargos a ventas pagadas,
  no cambia el total ni crea una entrega sin pago.
- Seguimiento con control de versión y transiciones:
  `PENDIENTE → EN_PREPARACION → EN_CAMINO → ENTREGADO`.
  La cancelación se permite antes de salir a reparto y exige motivo. No se
  habilitan retrocesos ni sobrescritura de cambios concurrentes.
- El administrador puede gestionar las entregas de su empresa; el encargado
  sólo las de su sucursal activa. El cajero mantiene consulta de su sucursal,
  sin controles de gestión de CU18. El cliente consulta exclusivamente las suyas.
- Finalizar exige una **confirmación explícita de entrega física** y no admite
  una incidencia abierta. Se registra quién confirmó y la fecha efectiva.
- Las incidencias y sus resoluciones conservan motivo, fecha, actor y versión.
  Sus reintentos reconocibles no duplican el historial. Una cancelación abre una
  incidencia de coordinación; **cancelar la entrega no confirma un reembolso**.
  Si corresponde devolver dinero se continúa con CU20, todavía pendiente del
  bloque de devoluciones. Este bloque no inventa una devolución de fondos.
- Registro de notificación y cola push transaccional por cada cambio relevante;
  el envío FCM real conserva las dependencias externas del bloque 1. El cliente
  puede consultar el historial aunque no reciba push.
- Web muestra el trazado aceptado y el destino; Flutter dibuja la geometría de
  la ruta. Se retiraron las líneas rectas presentadas como rutas. Los registros
  anteriores sin geometría lo indican expresamente. No se presenta seguimiento
  GPS en tiempo real de un repartidor.
- Las pantallas retiran los datos anteriores al fallar una actualización o cambiar
  de sesión. Se descartaron cotizaciones en vuelo si el destino ya cambió y se
  corrigió una escritura de señales durante el renderizado del carrito Angular.
  Los marcadores del mapa tienen nombres accesibles.

Referencia técnica del formato ORS, consultada para esta reparación:
[Requests and Return Types — documentación oficial de OpenRouteService](https://giscience.github.io/openrouteservice/api-reference/endpoints/directions/requests-and-return-types).

### Migración local y preservación

Versión `2026_10_31_cu18_delivery`, archivo
`database/migracion_cu18_delivery.sql`. Agrega cotizaciones aceptadas, referencia
con la orden de pago y la entrega, historial de seguimiento, versión, incidencia
abierta y registro de recepción física.

Aplicada con respaldo previo:
`.private/antes_cu18_delivery_20261031_203810.dump`.
Se ejecutó el inicializador dos veces. Se conservaron pagos, ventas, existencias,
movimientos y datos de las entregas existentes. Las entregas anteriores pendientes
sin pago verificable se identifican como `PENDIENTE_PAGO` y no se despachan; no se
inventan pagos para habilitarlas. La API inicia correctamente con la base migrada.
No se alteraron contraseñas ni se habilitaron cobros reales.

### Validaciones del bloque

- **88 pruebas PostgreSQL/API aprobadas**, incluidas 16 de CU18 y regresiones
  de mayorista, pagos, reservas CU11–CU15 y aislamiento entre empresas.
- Cubren cotización vencida/ajena, cambios del destino y origen, importe falso,
  falta de stock, pago repetido, concurrencia de estados, permisos por sucursal,
  confirmación física, incidencias y continuidad de despachos pagados aunque
  se suspenda el plan comercial. Las respuestas ORS inválidas o ausentes no
  producen cotizaciones ficticias.
- Flutter: **44 pruebas aprobadas**. Las cinco nuevas comprueban ID de cotización,
  geometría, versión de incidencias, confirmación física, errores recuperables
  y retiro de datos del historial al cambiar de sesión. Análisis sin incidencias.
- Angular: compilación de producción correcta, con los avisos existentes de
  CommonJS para `leaflet` y `qrcode`.
- Edge/Playwright + AXE: recorrido completo con dirección guardada, aceptación
  del envío, pago QR simulado, consulta del cliente, gestión por encargado,
  incidencia/resolución y confirmación física. Otra empresa no ve la dirección.
  El recorrido aprobado pasó los controles de accesibilidad verificados.
- El navegador usó una base desechable y un proveedor ORS simulado exclusivamente
  en la fixture. Esto **no equivale a una consulta real a ORS ni a entrega física**.

### Recorrido manual

1. Iniciar los proyectos con los comandos del inicio de este documento.
2. Configurar la ubicación real de la sucursal y, para cotizar con ORS real,
   aportar `OPENROUTE_API_KEY` en el `.env` privado del backend.
3. Como cliente, agregar prendas y elegir **Delivery** en el carrito. Seleccionar
   una dirección guardada o escribir otra, revisar la ubicación y cotizar.
4. Revisar distancia, estimación y costo; aceptar la cotización. Pagar compra y
   envío con el medio habilitado. Mientras el pago siga pendiente no aparece
   una entrega operativa en Mis deliveries.
5. Con el pago confirmado, abrir **Mis deliveries**. Como encargado de la
   sucursal, abrir **Delivery**, avanzar los estados y registrar incidencias
   cuando corresponda. Resolverlas antes de finalizar.
6. Para finalizar, elegir `ENTREGADO` y marcar la confirmación de entrega física.
   El cliente ve el estado y el historial, y tiene el aviso en su bandeja.

### Estado de la conexión ORS y cierre pendiente

Se comprobó que la base de desarrollo **no tiene `OPENROUTE_API_KEY` configurada**.
Por ese motivo no se ejecutó una consulta real al proveedor. La cotización local
responderá que falta configurar el servicio; el retiro en sucursal sigue disponible.
No se activó ningún simulador en la aplicación normal ni se guardó una clave
sintética en el `.env` local.

Se añade al cierre obligatorio ya acordado:

- [ ] ORS: aportar la clave autorizada, verificar las coordenadas reales de cada
  sucursal y ejecutar una cotización real, incluyendo fallo de proveedor y
  destino sin cobertura. Las pruebas de la fixture no cierran esta verificación.

Siguen pendientes BCP, Stripe, Firebase y los flujos completos CU19–CU20 del
registro anterior. La siguiente implementación debe abordar las políticas por
empresa y los cambios/devoluciones; este bloque cierra el funcionamiento de
entregas probado con proveedores simulados, sin declarar cerrado el conjunto.

Cierre del bloque 3: Flutter compila para web y su análisis quedó sin incidencias.
Se detuvieron las instancias temporales y se eliminaron las tres bases CU18
utilizadas para las pruebas y sus archivos de conexión. No quedaron servidores
levantados por esta implementación. Se conserva el respaldo de StyleAR local.
No se hicieron commits, push ni despliegues remotos.

## Bloque 4 — Políticas y devoluciones con aceptación (31/10/2026)

Se implementó la política por empresa y el flujo de devolución CU20 en web y
mobile. La política de cambios CU19 queda preparada; el reemplazo de prendas,
reserva de la nueva variante y liquidación de diferencias corresponden al
siguiente bloque y **todavía no están implementados**.

### Comportamiento implementado

- El administrador configura plazos y condiciones en **Políticas de posventa**.
  Las políticas comienzan deshabilitadas y no se activan sin plazo y condiciones.
  Cada empresa mantiene sus valores y versión; una edición concurrente exige
  actualizar antes de guardar. No se impuso un plazo comercial arbitrario.
- El cliente consulta su política y solicita cantidades elegibles de compras
  completadas y pagadas. Se conserva una copia de la política aplicable al
  solicitar; deshabilitarla después no bloquea solicitudes existentes.
- Se conserva la clave de una solicitud cuando falla su respuesta para permitir
  reintentos. Las unidades comprometidas en inspección o reintegro no pueden
  solicitarse nuevamente. Se excluye el envío y se prorratea el importe pagado
  después de descuentos, con ajuste de centavos en la última devolución.
- AD y personal ES/CA autorizado revisan y reciben dentro de su empresa y
  sucursal. El servidor revalida el usuario, los roles y el empleado al escribir.
  Se corrigieron los permisos heredados del cajero para este flujo.
- La recepción distingue cantidades aceptadas, rechazadas y **no vendibles**.
  Estas últimas requieren describir su condición. La inspección no aumenta
  existencias: queda en `ESPERA_ACEPTACION` hasta que el cliente acepte el importe.
- La aceptación registra un comprobante estructurado con cantidades e importe,
  repone solo prendas vendibles una vez y pasa a `REEMBOLSO_PENDIENTE`.
  Comprobante comercial y confirmación del dinero son estados separados.
- El reintegro electrónico usa el medio original. No se permite registrar un
  pago QR/Stripe como entrega de efectivo u otro método. Para efectivo se exige
  aceptación previa y referencia del recibo de caja; repetirlo no registra otra
  entrega. Los simuladores solo confirman reintegros de pagos simulados cuando
  está habilitado el modo de prueba.
- Stripe usa credenciales propias de la empresa, referencia del pago original
  e idempotencia. Su conciliación valida empresa, moneda, importe y referencia;
  los nuevos reintegros se reciben en el webhook específico de la empresa.
  Esto se verificó con datos de prueba, **sin mover dinero real**.
- Se generan avisos en la bandeja y cola push de forma transaccional. El envío
  FCM real mantiene su dependencia externa pendiente.
- Flutter retira datos al cambiar de sesión y descarta respuestas anteriores.
  Se corrigieron contrastes, foco visible y el landmark duplicado en web.

### Migración y validación

Migración `2026_10_31_posventa_politicas`, archivo
`database/migracion_posventa_politicas.sql`, aplicada localmente con respaldo
`.private/antes_posventa_20261031_211202.dump`. Dos ejecuciones del inicializador
preservaron pagos, ventas, devoluciones, entregas, existencias y movimientos.
La API inicia correctamente con StyleAR local. No se cambiaron contraseñas.

- Regresión comercial: **96 pruebas API/PostgreSQL aprobadas** en base desechable
  (incluía las primeras ocho de posventa). Tras ampliar cobertura, **11 pruebas
  específicas de posventa aprobadas**, incluidas efectivo, cantidades en espera
  de aceptación y permisos del cajero por sucursal.
- Nueve regresiones de cambios remotos aprobadas; se adaptó la prueba del
  reintegro a su nuevo servicio conservando propagación de 404 y actor/empresa.
- Flutter: **47 pruebas aprobadas**, análisis sin incidencias. Las tres nuevas
  verifican aceptación con comprobante y dinero pendiente, clave tras error e
  inspección con unidades no vendibles.
- Angular: compilación de producción correcta; avisos CommonJS existentes.
- Edge/Playwright y AXE: recorrido completo aprobado desde configuración hasta
  reintegro QR simulado, incluyendo inspección no vendible y aceptación del cliente.

### Cómo probar

1. Iniciar los proyectos con los comandos del principio del documento.
2. Como AD, abrir **Políticas de posventa**, habilitar devoluciones y guardar el
   plazo y las condiciones reales de la empresa. Las políticas locales siguen
   deshabilitadas hasta que se configuren; los valores de prueba no se cargaron
   en StyleAR.
3. Como cliente, abrir **Mis devoluciones**, seleccionar una compra pagada y
   enviar motivo y cantidades.
4. Como personal autorizado de su sucursal, abrir **Devoluciones**, aprobar y
   registrar recepción e inspección. Marcar no vendibles cuando corresponda.
5. Como cliente, revisar el importe y aceptar. Consultar el comprobante.
6. Como personal, solicitar el reintegro original o registrar la entrega efectiva
   de efectivo con recibo si la compra original fue en efectivo.

### Pendientes que no se consideran cerrados

- **CU19**: selección y reserva de reemplazo, operación de cambio y aceptación
  de diferencia económica. Utilizará esta base de políticas y cantidades.
- **CU20 real**: conexión y validación de reintegros bancarios QR y Stripe por
  empresa. Un QR real sin integración responde que falta la conexión bancaria;
  no se convierte en un reintegro manual ficticio.
- Se mantienen como cierre obligatorio BCP, Stripe, Firebase y ORS. Faltan sus
  credenciales/configuración autorizadas y pruebas reales. No se declararon
  completados mediante los simuladores ni se alteró el `.env` para aparentarlo.

Se detuvieron las instancias temporales web/API y se eliminaron las bases
posventa desechables y sus archivos de conexión. Se conserva el respaldo local.
No se hicieron commits, push ni despliegues remotos.

Cierre de compilación mobile: **APK Android debug generado correctamente** en
`tr-mobile/build/app/outputs/flutter-apk/app-debug.apk`. No se instaló ni se probó
en un dispositivo físico. Se excluyó del control de versiones la caché local de
Kotlin creada al compilar. Permanecen los avisos de compatibilidad futura de
Kotlin/Firebase y Java existentes en la herramienta, sin impedir la compilación.

## Bloque 5 — CU19: cambios de prendas (31/10/2026)

Se implementó CU19 en web y mobile tomando como referencia la ficha de cambios
acordada en «Estructura informe con ICONIX». El cliente solicita un cambio,
ES/CA (o AD) inspecciona en su sucursal y el cliente elige y acepta el reemplazo.

### Flujo y reglas implementadas

- Cada solicitud corresponde a una línea de compra y su cantidad. Para cambiar
  líneas distintas se generan solicitudes independientes. Se comprueban propiedad,
  pago de la compra, plazo y política de cambios de la empresa.
- Cambios y devoluciones comparten el cálculo de unidades comprometidas: una
  unidad solicitada, aceptada o ya cambiada no puede tramitarse dos veces.
  La solicitud conserva su clave al reintentar una respuesta incierta.
- La tienda confirma la recepción física, registra la condición y distingue las
  unidades no vendibles. Rechazar exige una descripción; todavía no repone stock.
- El crédito usa el importe efectivamente pagado por las prendas originales,
  después de descuentos y sin envío. Se conservan sus condiciones originales.
  Los centavos se ajustan al consumir las últimas unidades elegibles.
- Se ofrecen variantes de la misma sucursal, incluidas tallas, colores y modelos
  distintos, con suficientes existencias libres. El reemplazo se cotiza por
  unidad al precio y promoción vigentes; no se crea un paquete mayorista nuevo.
- La cotización dura 15 minutos. Antes de aceptarla se verifican otra vez precio,
  stock y versión. El cliente acepta expresamente el reemplazo y la diferencia.
  Entonces se reserva la cantidad; todavía no se registra su salida definitiva.
- Diferencia cero: el cambio queda listo para entrega, sin crear un cobro.
- Diferencia positiva: se crea una orden específica de diferencia por Stripe o
  QR, vinculada con el cambio. La confirmación validada del pago habilita la
  entrega. Los reintentos concurrentes comparten la misma orden y sesión.
- Diferencia negativa: se reintegra **solo el excedente**, por el medio original
  y con la aceptación del cliente. Si la compra fue en efectivo se exige la
  referencia de su entrega en caja. Un pago electrónico no se registra como
  efectivo. La entrega del reemplazo espera la confirmación del reintegro.
- El cliente puede desistir antes de liquidar. Se libera la reserva cuando es
  seguro; un pago tardío de un intento cerrado pasa al reintegro por operación
  incumplida. No se libera una operación conectada con resultado incierto sin
  conciliarla con su proveedor.
- Si no se desea o no hay reemplazo disponible, el cliente puede continuar por
  devolución después de la inspección. La solicitud existente pasa a CU20, sin
  duplicar cantidades; exige aceptar su importe antes de reponer y reintegrar.
- ES/CA/AD confirma la entrega física antes de completar. En una transacción se
  libera la reserva, registra la salida del reemplazo y repone únicamente las
  prendas originales vendibles. Se emite el comprobante vinculado con la venta
  original. Repetir la entrega no repite movimientos ni comprobante.
- Se guarda el estado económico separado de la inspección y entrega. Un callback
  de reintegro repetido o tardío no degrada un cambio ya completado.
- El cliente consulta **Mis cambios** en la web y **Cambios de prendas** en su
  perfil mobile. El personal accede a la gestión de su sucursal. Las versiones,
  roles y sucursal se verifican en el servidor al ejecutar las acciones.
- Se generan avisos en la bandeja y en la cola push. La entrega FCM real sigue
  pendiente de la conexión externa acordada.

Se corrigió también una carrera en la pantalla de políticas: se bloquea el
formulario hasta terminar de cargar, para que la respuesta inicial no sobrescriba
una edición. La vista de cambios gestiona el foco en su detalle y Flutter retira
los datos al cambiar la sesión.

### Migración y preservación local

Migración `2026_10_31_cu19_cambios`, archivo
`database/migracion_cu19_cambios.sql`. Añade el registro de cambio, sus versiones,
reserva, cotización, comprobante y vínculo con la orden de diferencia. La base de
solicitudes comparte la elegibilidad con CU20, manteniendo separados sus flujos.

Aplicada localmente con respaldo previo:
`.private/antes_cu19_cambios_20261031_214745.dump`.
Dos ejecuciones del inicializador conservaron ventas, pagos, devoluciones,
existencias, movimientos y entregas anteriores. La API inicia con StyleAR migrado.
No se alteraron contraseñas ni se cargaron configuraciones comerciales de prueba
en la base local.

### Validaciones del bloque

- Las primeras nueve pruebas API de CU19 aprobaron precio igual, mayor y menor,
  reserva/entrega, unidades no vendibles, reintentos concurrentes, conversión a
  devolución, precio/stock/cotización inválidos y pagos tardíos tras cancelar.
- Se ampliaron los casos con tres cambios parciales de una compra con descuento:
  sus créditos suman exactamente lo pagado y el excedente total coincide con la
  diferencia, incluidos los centavos. También se verifican límites por sucursal,
  el callback del excedente y datos vacíos rechazados sin efectos comerciales.
- Flutter: **51 pruebas aprobadas**, análisis sin incidencias y APK Android debug
  compilado en `tr-mobile/build/app/outputs/flutter-apk/app-debug.apk`. No se
  instaló ni se probó en un dispositivo físico.
- Angular: compilación de producción correcta; permanecen los avisos CommonJS
  existentes de `leaflet` y `qrcode`.
- Edge/Playwright y AXE: recorrido completo aprobado con política configurada,
  solicitud del cliente, inspección de cajero, prenda no vendible, selección y
  aceptación del reemplazo, pago QR simulado de diferencia, entrega y comprobante.
- Nueve regresiones de integración remota aprobadas. El inicio de la API con
  StyleAR local migrado fue verificado sin levantar un servidor permanente.

### Recorrido manual

1. Iniciar los proyectos con los comandos del principio del documento.
2. Como AD, configurar **Políticas de posventa → Cambios**: plazo y condiciones.
3. Como cliente, abrir **Mis cambios**, elegir la línea y cantidad de una compra
   elegible e indicar el motivo.
4. Como personal autorizado de la sucursal, abrir **Cambios de prendas**, recibir
   e inspeccionar la prenda, registrar su condición y aprobar o rechazar.
5. Como cliente, consultar reemplazos, seleccionar la variante, cotizar y aceptar
   la diferencia. Se puede elegir otra variante o desistir antes de liquidar.
6. Si corresponde pagar, completar la orden de diferencia. En modo simulado se
   identifica claramente que la confirmación de prueba no mueve dinero real.
7. Si corresponde un excedente, el personal solicita el reintegro original, o
   registra el recibo si el pago original fue en efectivo. Esperar confirmación.
8. Como personal, confirmar la entrega física y completar. El cliente consulta
   su comprobante. Si se elige devolución, continuar en **Mis devoluciones**.

### Cierre externo obligatorio todavía pendiente

Este bloque no habilitó cobros ni reintegros bancarios reales. Se mantienen como
pendientes BCP, Stripe por empresa, Firebase y ORS con credenciales/configuración
autorizadas y pruebas reales. Las verificaciones de CU19 con simuladores y mocks
no cierran estas conexiones. No se presenta un QR sin conexión bancaria como
un pago real confirmado ni un reintegro electrónico como entrega manual de caja.

Cierre de validación del bloque 5:

- **111 pruebas API/PostgreSQL de regresión comercial aprobadas** en una base
  desechable, incluidas las primeras 12 de CU19 y los bloques anteriores.
- Dos casos adicionales aprobados: validaciones de textos vacíos sin efectos
  y desistimiento de una cotización de menor precio antes de aceptarla.
- Se amplió y volvió a aprobar la prueba del callback para comprobar que un
  reintegro tardío no degrada un cambio ya completado.
- Las instancias temporales web/API quedaron detenidas. Se eliminaron las bases
  CU19 de prueba y sus archivos de conexión; se conservó el respaldo de StyleAR.
- No se hicieron commits, push ni despliegues remotos. El siguiente cierre es
  el de conexiones externas reales ya enumeradas, que sigue pendiente.

## Cierre externo — configuración guiada iniciada (31/10/2026)

La usuaria confirmó que necesita guía para crear/configurar Firebase, ORS y
Stripe, y que todavía no solicitó acceso a la API QR del BCP. El cierre externo
permanece abierto. No se crearon cuentas ni se enviaron solicitudes al banco.

Diagnóstico local sin exponer valores:

- No había clave ORS. Se añadió `OPENROUTE_API_KEY=` vacío al `.env` privado para
  completarlo localmente. No se ejecutó una consulta real de rutas.
- Existen variables Stripe globales heredadas. Esto no demuestra que estén
  operativas ni autoriza asociarlas a todas las empresas; la integración nueva
  requiere identificar la cuenta y configurar credenciales por empresa.
- No se encontró configuración Firebase del servidor ni archivos de registro
  Android en el proyecto. No se verificó entrega FCM en un dispositivo.
- `.env.example` incluye los nombres de variables de estas integraciones, sin
  credenciales. Mobile tiene `firebase.defines.example.json`, deshabilitado y
  vacío; los valores locales se guardarán en `firebase.defines.local.json`.
  La cuenta de servicio privada se guarda solo en el backend, nunca en el APK.

### Paso 1: OpenRouteService

1. Abrir https://openrouteservice.org/log-in/ y crear o iniciar una cuenta desde
   su enlace oficial. Completar la verificación de correo solicitada.
2. En su panel de desarrollador, crear una API key para StyleAR.
3. Guardar su valor en `tr-backend/.env`, en la variable ya preparada
   `OPENROUTE_API_KEY`. No compartirla en el chat ni subir `.env` al repositorio.
4. Avisar una vez guardada. Se verificará una ruta real con ubicaciones de
   sucursal/destino y después la cotización desde el carrito. La existencia de
   la variable por sí sola no se registrará como conexión verificada.

Referencia oficial: [Acceso y obtención de API key de ORS](https://openrouteservice.org/log-in/).

### Paso 2: Firebase — siguiente configuración guiada

Crear un proyecto propio en https://console.firebase.google.com/ y registrar la
aplicación Android con su identificador actual `com.example.mobile_tr`.
Después se configurarán los datos públicos de la aplicación y su integración
nativa, y las credenciales privadas de envío únicamente en el servidor. El cierre
requiere permiso de notificaciones, registro de una instalación y recepción de
un aviso en un teléfono o emulador compatible; que FCM acepte una petición no
prueba por sí solo que el teléfono recibió el aviso.

Referencias oficiales: [Configuración Flutter](https://firebase.google.com/docs/flutter/setup),
[Cloud Messaging en Flutter](https://firebase.google.com/docs/cloud-messaging/flutter/get-started).

### BCP — solicitud pendiente

Solicitar al banco acceso de pruebas para generación y consulta de QR, la forma
oficial de confirmar pagos, credenciales y URL de sandbox, firma/autenticación,
reglas de reintentos y conciliación, habilitación por comercio/cuenta receptora,
y el procedimiento para reintegrar fondos. La página pública no basta para
implementar un webhook o una API de reintegros que no se ha documentado.

Referencia oficial: [BCP Bolivia — API Pagos QR SIMPLE](https://www.bcp.com.bo/Desarrollo/ApiPagosQR).

### Stripe — disponibilidad y titularidad antes de activar

Bolivia no figura en la lista oficial de países de alta directa para Stripe
Payments. Esto es distinto de poder presentar importes en BOB, moneda incluida
entre las admitidas para ciertos pagos. No se debe registrar un país ficticio
ni confundir recibir pagos mediante otra plataforma con disponer de una cuenta
propia de comercio. Antes de conectar se verificará la cuenta habilitada y su
modo de prueba, la titularidad por empresa y sus webhooks. Las variables globales
existentes no se copiarán automáticamente a los comercios.

Referencias oficiales: [Disponibilidad de Stripe](https://stripe.com/global),
[Monedas admitidas](https://docs.stripe.com/currencies).

### ORS: conexión real verificada (31/10/2026)

La usuaria guardó la clave localmente. Se comprobó su presencia sin mostrarla y
se ejecutó `_calcular_ruta` contra el endpoint real de OpenRouteService, sin mocks.
Desde la ubicación configurada de la sucursal se consultó un punto técnico cercano
(sin usar direcciones de clientes): resultado **2,15 km**, **6 minutos estimados**
y **36 puntos de geometría**. La tarifa local produjo **Bs 8,00** y la distancia
está dentro del máximo configurado. El costo lo calcula StyleAR; no es una tarifa
cobrada o determinada por ORS.

No se crearon compras, cotizaciones persistentes, pagos ni entregas. No se mostró
ni se copió la clave a archivos versionados. No se dejaron servidores corriendo.

Se considera verificada la autenticación y consulta real de rutas. Todavía se
requiere probar el recorrido desde el carrito y confirmar que las coordenadas
configuradas representan los locales físicos: actualmente las dos sucursales de
StyleAR tienen las mismas coordenadas. No se modificaron estas ubicaciones.

Siguiente paso guiado: crear el proyecto Firebase, registrar Android con
`com.example.mobile_tr` y guardar su archivo de configuración público en
`tr-mobile/android/app/google-services.json`. Después se completará la integración
nativa y el envío desde el servidor, sin incluir credenciales privadas en la app.

### Firebase Android: archivo recibido y configuración nativa (31/10/2026)

Se recibió y validó el JSON local para el paquete `com.example.mobile_tr`.
Se configuró el plugin oficial Google Services 4.5.0, aplicado cuando existe
`android/app/google-services.json`, y se generó `firebase.defines.local.json`
con Firebase habilitado y los datos públicos del registro Android. Ambos archivos
locales se comprobaron excluidos de Git. No contienen una cuenta de servicio
privada del backend.

El `.env` privado del backend identifica el proyecto Firebase. La ruta de cuenta
de servicio sigue vacía porque el archivo todavía no fue proporcionado. Se
comprobó que el backend informa `push_configurado: false` y mantiene disponible
la bandeja. No se presenta esta preparación como un envío FCM verificado.

Verificaciones: 51 pruebas Flutter aprobadas y análisis estático sin incidencias.
Se corrigió una advertencia de formato de una prueba de CU19. No se detectaron
telefonos ni emuladores Android conectados; no se instaló el APK ni se verificó
la recepción física de avisos.

Siguiente paso de la usuaria:

1. Firebase Console → Configuración del proyecto → Cuentas de servicio.
2. En Firebase Admin SDK, generar una nueva clave privada y descargar su JSON.
3. Guardarlo solo en
   `tr-backend/.private/firebase-service-account.json`.
4. Confirmar que Cloud Messaging API (V1) está habilitada en el proyecto.
5. Avisar cuando esté guardado; se validará que pertenece al mismo proyecto y se
   configurará la ruta privada del servidor. Después se probará el registro y
   recepción en un teléfono/emulador con Google Play Services.

La cuenta de servicio no se guarda en mobile ni se copia al APK. No se solicita
pegar su contenido en el chat.

Referencias oficiales consultadas:
[Configuración Android y plugin Google Services](https://firebase.google.com/docs/android/setup),
[Autorización de envío FCM HTTP v1](https://firebase.google.com/docs/cloud-messaging/send/v1-api).

Cierre de la preparación Android: APK debug compilado correctamente con Firebase
habilitado. Se verificaron los recursos nativos generados contra el registro
Android y la presencia de `POST_NOTIFICATIONS` en el manifiesto combinado.

Para mantener esta configuración al ejecutar o recompilar desde `tr-mobile`:

```powershell
$env:PUB_CACHE='C:\Users\Anghy\AppData\Local\Pub\Cache'
C:\src\flutter\bin\flutter.bat run --dart-define-from-file=firebase.defines.local.json
# Para generar el APK:
C:\src\flutter\bin\flutter.bat build apk --debug --dart-define-from-file=firebase.defines.local.json
```

La compilación sin estas definiciones mantiene `FIREBASE_ENABLED=false` por
omisión. El APK generado en este paso sí usa las definiciones locales habilitadas.
No se envió ninguna notificación ni se dio por verificada su recepción. El backend
sigue esperando la cuenta de servicio privada del mismo proyecto.

### Firebase servidor: autorización real verificada (31/10/2026)

Se validó el archivo `.private/firebase-service-account.json`: es una cuenta de
servicio válida, su clave se puede cargar y el proyecto coincide con Android.
El `.env` privado ya apunta a este archivo y proyecto. La credencial continúa
excluida de Git y no se copió a mobile ni al APK.

Se verificaron contra Google, sin mocks:

- Obtención correcta de autorización OAuth con el alcance de Firebase Messaging.
- Petición FCM HTTP v1 aceptada en modo `validate_only=true`. Este modo valida
  sin entregar el mensaje: no se envió una notificación a usuarios ni se marcó
  ningún registro de la cola como enviado.

Referencia oficial de esta prueba:
[FCM messages.send — validate_only](https://firebase.google.com/docs/reference/fcm/rest/v1/projects.messages/send).

La usuaria eligió probar con un teléfono Android. Todavía no se detectó uno
conectado/autorizado; la recepción sigue pendiente de la prueba física.

Se preparó un APK debug con Firebase habilitado y `API_URL=http://127.0.0.1:8000`
en las definiciones locales ignoradas. Para este APK de desarrollo se usará USB
con `adb reverse`, para llegar al backend de esta computadora. No apunta al
backend Railway anterior. Esta dirección de pruebas requiere el túnel USB y no
se considera configuración de despliegue de producción.

El trabajador de avisos ahora permite atender continuamente la cola:

```powershell
# Desde tr-backend, con Firebase configurado y la API funcionando:
.\.venv\Scripts\python.exe -m app.modules.ventas_inventario.services.push_worker --continuo --intervalo 10
```

El modo anterior de un solo lote se conserva. Se verificaron tres pruebas
sin red ni base de datos: detención sin configuración, recuperación ante fallo
transitorio sin exponer sus detalles y compatibilidad con procesamiento de un
solo lote. Las tres aprobaron. El APK con la URL de pruebas compiló correctamente.
No se arrancó el trabajador sobre la cola real ni se dejaron servidores corriendo.

Siguiente paso: conectar el teléfono por USB, habilitar depuración y autorizar la
computadora. Después se instalará el APK actualizado, se iniciará el backend
local y se registrará la instalación desde **Activar avisos push**. El cierre
requiere comprobar un aviso recibido en ese teléfono, además de su aceptación
por FCM. BCP y Stripe mantienen los pendientes externos ya documentados.

### Prueba Android por USB iniciada (31/10/2026)

Se detectó un único teléfono Android autorizado. Se instaló el APK debug con
`adb install -r` correctamente, conservando datos, y se configuró
`adb reverse tcp:8000 tcp:8000`. Se abrió StyleAR en el teléfono y se inició
la API local en `127.0.0.1:8000` para esta prueba. Su health check respondió.

Al comprobar el registro había cero instalaciones Android activas. Se espera que
la usuaria seleccione StyleAR, inicie sesión y pulse **Notificaciones → Activar
avisos push**, aceptando el permiso de Android. Aún no se envió un mensaje de
prueba ni se declaró recepción verificada. La API se mantiene temporalmente
corriendo para permitir el registro durante esta prueba interactiva.

### Firebase: recepción real confirmada en Android (31/10/2026)

La usuaria confirmó **«Dispositivo registrado»** desde la app. Se verificó una
única instalación Android de StyleAR con usuario activo, versión de acceso válida
y sesión vigente. Se creó un aviso de prueba y su registro de salida para esa
instalación; se utilizó el mismo envío FCM del servidor, sin procesar mensajes de
otros usuarios ni realizar compras/cobros.

FCM aceptó el envío real y se conservó su referencia en la salida correspondiente.
La usuaria confirmó **«Sí, llegó la notificación»** en su Redmi Note 12 Pro.
Quedan verificadas configuración Android, autorización del servidor, registro de
instalación y recepción real en el teléfono. Se conserva el aviso «Prueba de
Firebase» en la bandeja como evidencia. No se considera esta prueba una
verificación de APNs/iOS ni de todas las condiciones de entrega de Android.

El teléfono restringió la navegación de pantalla mediante ADB; se continuó con
la usuaria colocando la app en segundo plano manualmente, sin cambiar permisos
adicionales del teléfono.

Se cierra la instancia temporal de API y el túnel USB usados en esta prueba.
Para volver a probar localmente se necesitan la API, el trabajador de cola si
se esperan avisos automáticos y el túnel USB para este APK de desarrollo:

```powershell
# Terminal 1, desde tr-backend:
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Terminal 2, desde tr-backend:
.\.venv\Scripts\python.exe -m app.modules.ventas_inventario.services.push_worker --continuo --intervalo 10
# Con el teléfono conectado y autorizado:
& 'C:\Users\Anghy\AppData\Local\Android\Sdk\platform-tools\adb.exe' reverse tcp:8000 tcp:8000
```

Firebase deja de estar pendiente para el entorno Android/local verificado.
ORS ya tiene consulta real verificada; conserva pendiente validar ubicaciones
físicas de sucursales y recorrido de carrito. Stripe por empresa y la API QR
real del BCP continúan abiertos; no se habilitaron movimientos bancarios reales.

## Stripe por empresa — conexión TEST iniciada (01/11/2026)

La usuaria confirmó que tiene acceso a la cuenta de las claves de prueba
existentes. Se verificaron consultas reales de cuenta y balance: autenticación
correcta, modo TEST confirmado, país de cuenta US y cobros comerciales todavía
no habilitados. No se cambió esa condición ni se usaron claves LIVE.

Se comprobó con Stripe real, en modo de pruebas, la creación de una sesión de
Checkout en BOB y su idempotencia (el reintento devuelve la misma sesión). Esa
sesión técnica se cerró sin pagar. La clave se configuró exclusivamente como
`STYLEAR_PAGOS_1_STRIPE_SECRET_KEY` para StyleAR; no se asignó a otras empresas
reales. La firma global heredada no se copió a los webhooks por empresa.

Se instaló Stripe CLI 1.55.6 dentro de `.private/stripe-cli`, desde la publicación
oficial y verificando el checksum SHA-256 específico de Windows. No se modificó
el PATH global. Se añadió `scripts/escuchar_stripe.py` para:

- Exigir la clave TEST propia de la empresa, sin fallback a claves globales.
- Reenviar eventos al webhook local de esa empresa.
- Guardar la firma anunciada por Stripe CLI sin imprimirla ni pasar la API key
  en argumentos del proceso.
- Detener únicamente su proceso hijo cuando termina el receptor.

Tres pruebas del receptor aprobaron: aislamiento de claves, resguardo de la
firma y rechazo de claves LIVE.

### Verificación completa en curso

Se creó una base **desechable** `stylear_test_stripe_*` y una empresa ficticia
para verificar Checkout con la misma cuenta TEST autorizada. Su clave y firma
existen solo en `.private/stripe_prueba.env`, separadas del `.env` de StyleAR.
No se modificaron compras ni stock de StyleAR local.

La API temporal y Stripe CLI están atendiendo el puerto 8000 para esta prueba.
Se confirmó rechazo HTTP 400 de una firma inválida. Antes del pago, la compra
estaba pendiente, `pago_recibido=false` y su stock de prueba seguía en 10.

Se proporcionó el Checkout alojado por Stripe a la usuaria para completar Bs 50
con la tarjeta de pruebas 4242 4242 4242 4242, fecha futura y CVC de prueba.
Se espera su resultado para comprobar el webhook real, la venta, inventario,
reintentos y reintegro. Esta fase todavía no se considera cerrada.

Referencia oficial:
[Stripe — pruebas con tarjetas](https://docs.stripe.com/testing),
[Stripe CLI — receptor de eventos](https://github.com/stripe/stripe-cli/wiki/listen-command).

Para pruebas locales posteriores de StyleAR, iniciar primero el receptor y luego
la API, para que esta cargue la firma guardada:

```powershell
.\.venv\Scripts\python.exe scripts/escuchar_stripe.py --empresa 1
# Cuando indique que guardó la firma, en otra terminal:
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Ese receptor local debe permanecer funcionando durante la prueba. No sustituye
un webhook HTTPS de un despliegue. La activación de cobros comerciales y la API
QR del BCP continúan pendientes.

### Stripe TEST: pago, webhook y reintegro verificados (01/11/2026)

La usuaria completó el Checkout alojado por Stripe con la tarjeta de prueba.
El receptor oficial entregó `checkout.session.completed` al backend con respuesta
HTTP 200. Se comprobaron en la base desechable:

- Orden `PAGADO`, pago recibido y venta `COMPLETADA`.
- Stock de prueba de 10 a 9, con un único evento de pago registrado.
- Dos reproducciones del evento real, firmadas para la prueba local, devolvieron
  éxito sin crear otra venta, evento ni descuento de stock.

Se ejecutó CU20 por API en la misma base aislada: política, solicitud, revisión,
recepción vendible, aceptación del importe por el cliente y reintegro. Stripe
confirmó un único reintegro de **Bs 50,00**, estado `succeeded`; la devolución
quedó `REEMBOLSADA` y el stock volvió a 10. Reintentar la solicitud de reintegro
conservó la misma referencia. Los eventos de reintegro recibieron HTTP 200.

La comprobación final contrastó la referencia e importe del reintegro con la
operación de Stripe y el PaymentIntent en modo de prueba. El objeto Refund de
esta versión de la API no incluye `livemode`; el modo se comprobó mediante el
PaymentIntent, las claves TEST y los eventos firmados. No se movió dinero real.

### Configuración de StyleAR local

- Credencial TEST y firma del receptor configuradas únicamente para empresa 1,
  código `stylear`. Las otras empresas reales no recibieron estas credenciales.
- Stripe de pruebas quedó habilitado en su configuración de pagos, conservando
  QR, titular y demás valores existentes. Se guardó la configuración anterior
  en `.private/stripe_configuracion_antes_20261101.json` y se auditó el cambio
  como configuración local, sin atribuirlo a una sesión de usuario ficticia.
- La base `stylear_test_stripe_*`, sus JWT y su archivo temporal de credenciales
  fueron eliminados. Se conserva un resumen sin secretos en
  `.private/stripe_resultado_20261101.json`.
- API y receptores temporales cerrados. No quedaron servidores de esta prueba
  corriendo. No se hicieron commits, push ni despliegues.

Para usar Stripe TEST en la web local:

1. Iniciar `scripts/escuchar_stripe.py --empresa 1` con el Python de `.venv`.
2. Cuando confirme que guardó la firma, iniciar o reiniciar la API local para
   que cargue esa firma desde `.env`.
3. Iniciar el frontend y completar una compra mediante Stripe con tarjeta de
   prueba. El pago se confirma automáticamente por webhook; no usar el botón
   de confirmar simulaciones para una sesión conectada a Stripe.
4. Mantener el receptor y API funcionando mientras se prueba. Las devoluciones
   y diferencias de cambio utilizan la misma cuenta y receptor de la empresa.

Se considera verificada **la conexión Stripe TEST local**: Checkout, firma,
confirmación automática, idempotencia y reintegro CU20. La cuenta continúa sin
cobros comerciales habilitados y el proyecto mantiene el guard de claves TEST.
Este resultado no habilita Stripe LIVE ni constituye validación de un despliegue
HTTPS. La API bancaria real del BCP sigue pendiente de solicitud y documentación.

## BCP — solicitud de habilitación pendiente (01/11/2026)

La usuaria confirmó que tiene una **cuenta personal BCP**, pero todavía no ha
solicitado acceso a la API. La página oficial presenta generación y consulta de
pagos QR; eso no acredita habilitación para una cuenta o proyecto particular.
No se dispone aún de un contrato técnico vigente, URL de sandbox, credenciales,
certificados ni mecanismo oficial de confirmación para StyleAR.

Se revisó la información pública oficial. La consulta directa adicional desde
Python al sitio fue interrumpida por el servidor remoto; no se desactivó TLS ni
se utilizaron documentos de terceros para inventar el contrato bancario.

Referencia: [BCP Bolivia — API Pagos QR SIMPLE](https://www.bcp.com.bo/Desarrollo/ApiPagosQR).

### Solicitud lista para copiar — no enviada

Asunto: Consulta de acceso sandbox API QR SIMPLE para proyecto académico StyleAR

Buenos días:

Soy titular de una cuenta personal BCP y desarrollo StyleAR como proyecto
académico, una plataforma web y móvil para tiendas de ropa. Deseo consultar si
puedo acceder a un ambiente de pruebas de la API QR SIMPLE y qué requisitos
aplican para persona natural o comercio.

La integración necesita generar un QR por compra, consultar y confirmar pagos
automáticamente, conciliar transacciones y gestionar reintegros totales o
parciales. El sistema contempla varias empresas; cada una debe recibir los
cobros en su propia cuenta, sin centralizar los fondos en la cuenta de StyleAR.

¿Podrían indicarme:

1. Si ofrecen acceso sandbox para este tipo de proyecto y cómo solicitarlo.
2. Requisitos para una cuenta personal y, si corresponde, para cada comercio.
3. Documentación técnica vigente, URL de pruebas y procedimiento de entrega de
   credenciales o certificados.
4. Mecanismo oficial de confirmación: notificaciones/callback o consultas de
   estado, autenticación, reintentos y conciliación.
5. Soporte y procedimiento para reintegros o reversas, incluidos los parciales.
6. Condiciones de integración con comercios independientes, costos y requisitos
   adicionales antes de pasar a producción.

Por ahora solicito información y acceso de pruebas, sin activar cobros reales.

Muchas gracias.
[Nombre y medio de contacto]

### Trabajo que depende de la respuesta

Después de recibir la documentación y habilitación del banco se implementarán
el adaptador concreto, autenticación, generación QR, confirmación verificable,
conciliación y reintegros admitidos por el contrato. Se probará el aislamiento
por empresa, reintentos, estados pendientes, fallos y ausencia de duplicaciones.
No se puede afirmar que el banco ofrezca un endpoint de reintegro o callbacks
específicos hasta que lo confirme su documentación.

El endpoint BCP real conserva su respuesta de integración pendiente y no se
convierte un QR simulado en un cobro bancario. Stripe TEST, Firebase Android y
la consulta real ORS mantienen las verificaciones anteriores. No se enviaron
mensajes al banco, se activaron servicios bancarios ni se modificaron compras,
stock, credenciales o configuraciones de pago en este paso.

### 2026-11-01 — Cuentas de prueba por rol
- Creadas en StyleAR: admin.prueba (ADMINISTRADOR), cajero.prueba (CAJERO), encargado.prueba (ENCARGADO_SUCURSAL), ventas.prueba (PERSONAL_VENTAS) y proveedor.prueba (PROVEEDOR).
- Cajero, encargado y ventas asignados a Suc Banzer; proveedor vinculado a una ficha exclusiva de pruebas.
- No se crearon clientes ni administradores SaaS ni se modificaron cuentas existentes. Se respetaron los limites del plan y se registro la creacion en bitacora.
- Verificado el inicio de sesion de las cinco cuentas con su rol exclusivo y empresa stylear; sesiones de verificacion cerradas. Claves fuera del seguimiento y del codigo versionado.

### 2026-11-06 — CU22 a CU27, bloque 1: migraciones, sesión SaaS móvil y capacidades

Alcance acordado: integración local en web y Flutter, incluyendo administración SaaS móvil con el mismo alcance funcional de plataforma que web. Se conservan los módulos y pantallas anteriores. No se desplegó ni se modificaron servicios externos.

- Base PostgreSQL comprobada como local. Respaldo previo: `.private/antes_cu22_cu27_20261106.dump`. Aplicadas CU25 R2 (`2026_11_05_cu25_respaldos_r2`), CU26 (`2026_11_05_cu26_planes`) y CU22/CU27 (`2026_11_06_cu22_cu27_mejoras`). Segunda ejecución sin reaplicar versiones. Lectura real de capacidades StyleAR verificada: 12 funciones y 5 cupos.
- Flutter: entrada «Administración de la plataforma» desde el directorio; login SaaS sin empresa, restauración de sesión, salida y pantalla inicial con empresas/planes desde API. La sesión SaaS no envía X-Empresa-Codigo y el cliente bloquea rutas comerciales en ese ámbito. El servidor mantiene la autorización definitiva. Los formularios de gestión se completan en bloque 2.
- Flutter: modelos de capacidades y PlanProvider compartido, separado de permisos de usuario. Consulta al iniciar sesión y cada minuto, refresco tras escrituras que consumen cupos y rechazos comerciales; el estado se reemplaza al cambiar generación. SaaS no consulta capacidades de empresa ni registra automáticamente avisos push comerciales.
- Web: EstadoPlanService conserva capacidades efectivas, funciones habilitadas, cupos y motivos. Se consulta para todos los usuarios de empresa al navegar y cada minuto; refresca tras consumos/rechazos y descarta solicitudes al invalidar contexto. No usa nombres de planes como regla comercial.
- Web: alineada continuidad con backend para chat, dashboard, detalle, pausa de reportes y previsualización/restauración de respaldos. El interceptor conserva el detalle estructurado y cuerpo original además del mensaje compatible con pantallas anteriores.
- Archivos principales: mobile `core/session/contexto_sesion.dart`, `core/network/api_client.dart`, `providers/auth_provider.dart`, `providers/plan_provider.dart`, `models/capacidades_plan.dart`, `screens/auth`, `screens/saas/plataforma_screen.dart`, `main.dart`; web `core/models/capacidades-plan.models.ts`, `core/services/estado-plan.service.ts`, `error-api.ts`, `core/interceptors/auth.interceptor.ts`, `shared/components/estado-empresa`.
- Verificación: build Angular de producción aprobado (advertencias existentes leaflet/qrcode); 19 pruebas web aprobadas. Node 25 requiere ejecutar pruebas con NODE_OPTIONS=--no-experimental-webstorage para usar localStorage de JSDOM. Análisis Flutter limpio y suite previa con cinco pruebas nuevas aprobada: 56 pruebas. La prueba adicional de la pantalla SaaS se verifica por separado al cierre del bloque. No se afirma prueba física ni APK de distribución en este bloque.

Bloques pendientes: 2) CRUD SaaS y Mi plan; 3) respaldos; 4) reportes; 5) recomendaciones y chat; 6) notificaciones y Android; 7) recorridos completos y APK físico. La pantalla inicial SaaS de este bloque no representa todavía la gestión completa de plataforma.

Cierre de verificación del bloque 1: la prueba visual automatizada de la pantalla inicial SaaS aprobó; las seis pruebas del nuevo archivo `test/saas_sesion_test.dart` pasaron, incluyendo pantalla, restauración y separación de sesiones. Total de pruebas Flutter verificadas: 57 (51 anteriores y 6 nuevas). La preparación de almacenamiento del widget usa runAsync para no bloquear el reloj simulado. No se dejaron servidores de pruebas iniciados.

### 2026-11-06 — CU26, bloque 2: planes, empresas y suscripciones en web y Flutter

Web:
- Ampliadas las rutas existentes `/saas/planes`, `/saas/empresas` y `/mi-plan`; se conserva el CRUD y la asignación/renovación existente.
- Planes consume el catálogo comercial real. Añade descripción, condiciones, precio de referencia, moneda, publicación y estado; muestra cupos y funciones editables de reportes/respaldos. No ofrece interruptores para retirar funciones esenciales ni incorpora productos como diferenciador nuevo.
- Edición con previsualización de impacto, empresas afectadas, excesos de uso, reportes a pausar y restablecimiento de horario. Guardado con el mismo contenido y token; volver a editar invalida la previa. Ante 409 conserva el formulario, descarta la confirmación y exige revisar nuevamente. Solo se envían campos modificados, para conservar metadatos y claves históricas no editadas, incluidos cambios simultáneos de otra sesión.
- Añadidos historial de planes (antes/después), comparación y conteos de empresas únicas, vigentes y no vigentes. Enlace por plan al listado remoto, filtros por nombre/código/estado y páginas de 20 empresas.
- Configuración de empresa incluye capacidades efectivas e historial de suscripciones, manteniendo nombre, estado, aprobación, alta y renovación. El contacto de solicitudes pendientes se consulta desde la API existente al abrir el detalle. Respuestas tardías de listas/detalles y datos al cambiar sesión se descartan.
- Mi plan usa el estado compartido de capacidades, sin fallback por nombre/código de plan. Muestra uso, disponibles, cero/sin límite, vigencia y motivos. Una actualización del estado compartido se refleja en la vista ya abierta.
- Archivos principales: `modules/administracion/models/saas.models.ts`, `services/saas.service.ts`, `pages/saas/planes.page.ts` y `.html`, `empresas.page.ts`, `mi-plan.page.ts`, componente reutilizable `capacidades-plan.component.ts` y estilos existentes `saas.css`.

Flutter:
- La entrada SaaS incorpora accesos a Gestionar planes, Gestionar empresas y Auditoría de plataforma.
- Nuevas pantallas dentro de `lib/screens/saas`: listado/comparación/conteos de planes, editor con previsualización y confirmación, historial, listado paginado de empresas y registro/configuración de empresa. Incluyen alta del administrador inicial, cambio de nombre/estado, aprobación por suscripción, asignación/renovación, capacidades e historial.
- Se mantienen los alcances separados: ADMIN_SAAS opera solo rutas de plataforma. El administrador de empresa tiene Mi plan en Perfil, sin editar planes ni asignar suscripciones.
- Dependencias del catálogo y límites validados, sin enviar null para borrar cupos. La edición parcial y el 409 siguen las mismas reglas de web. El formulario bloquea envíos duplicados y muestra el error de conflicto al volver al inicio de la pantalla.
- Agregados `models/saas_models.dart`, `services/saas_service.dart`, widgets de sesión/errores y `screens/perfil/mi_plan_screen.dart`; reutilizado PlanProvider y ApiClient. El APK de debug utiliza el archivo local de defines existente.

Verificación del bloque:
- Flutter: análisis limpio; 62 pruebas aprobadas, incluidas cinco de CU26 (dependencias/fechas, edición parcial, contrato preview/token, conflicto 409 y empresas filtradas). APK debug actualizado: `tr-mobile/build/app/outputs/flutter-apk/app-debug.apk`.
- Web: 19 pruebas unitarias aprobadas. Recorridos Playwright/Edge contra PostgreSQL y FastAPI desechables: creación/edición/impacto/conflicto/conservación de claves e historial; empresas por plan, paginación con 21 registros, capacidades, historial y renovación; actualización de capacidades de una sesión de empresa ya abierta. Incluyen AXE, foco de revisión/historial y pantalla estrecha. Se corrigen regiones desplazables para teclado y niveles de encabezados.
- Las cuentas/empresas creadas por los recorridos pertenecen exclusivamente a la base desechable `stylear_test_cu26_ui_*`, no a la base local de trabajo. No se hicieron despliegues, cobros ni restauraciones. No se afirma prueba física de las pantallas nuevas ni APK de distribución.
- Respaldos R2, programación y presentación push nueva siguen en sus bloques correspondientes. La fixture antigua informa fallos de respaldos automáticos por falta de configuración R2; esos avisos no acreditan funcionamiento de CU25 ni se trataron como éxito.

Siguiente bloque: CU25, respaldos de empresa en ambas aplicaciones y ampliación del alcance SaaS, con almacenamiento simulado en las pruebas y sin restaurar la base de trabajo.

Cierre del bloque 2: build web de producción aprobado con las advertencias CommonJS existentes; 19 pruebas unitarias web aprobadas. Los tres recorridos nuevos de Edge/AXE aprobaron (plan y empresas en la ejecución conjunta; actualización de sesión en repetición aislada tras corregir una carrera de navegación del propio test). Flutter terminó con análisis limpio, 62 pruebas aprobadas y APK debug compilado con el código final. La API temporal fue detenida y la base desechable, su DSN y los archivos de fixture/correo fueron eliminados. No se dejó un servidor nuevo corriendo ni se instaló el APK en el teléfono.

### 2026-11-06 — CU25, bloque 3: respaldos de empresa y plataforma

Implementación:
- Web: nueva ruta `/respaldos` para ADMINISTRADOR, enlazada desde Administración. Se reutiliza la vista de respaldos existente con ámbito explícito EMPRESA/PLATAFORMA; `/saas/respaldos` conserva ADMIN_SAAS y su selector global. El servicio de empresa usa exclusivamente `/api/v1/respaldos` y la empresa de la sesión, sin selector ni purga de plataforma.
- Flutter: pantalla `screens/respaldos/respaldos_screen.dart` con ámbito explícito, accesible desde Perfil para ADMINISTRADOR y desde la entrada SaaS para ADMIN_SAAS. Incluye listado, creación, programación de empresa, revisión, eliminación y restauración; la purga de vencidos existe solo en plataforma.
- Listados con origen MANUAL/AUTOMATICO/PRE_RESTAURACION, alcance GLOBAL/EMPRESA, fecha, vencimiento, tamaño y estado. No se agregaron enlaces de descarga, URLs R2 ni secretos a los clientes.
- Cuota manual móvil de siete días desde capacidades, con uso/disponibles y distinción entre cero y sin límite. Eliminar no devuelve cupo; automáticos y preventivos no lo consumen. Retirar creación manual mantiene consulta y revisión/restauración de las copias disponibles.
- Programación: activa, fecha de inicio, hora HH:MM y zona. Se normaliza HH:MM:SS. Cuando el plan no permite editar horario, se muestra y envía 02:00/America/La_Paz. Frecuencia y retención se consultan desde el plan, sin campos de edición para el administrador de empresa. No se infieren cupos a partir de nombres de planes.
- Restauración: revisión previa y frase exacta escrita por el usuario, sin autocompletar. Enter no restaura. Para empresa se comprueba suspensión, se informa de la copia preventiva, cierre de sesiones y conservación de la suscripción actual. La restauración global se mantiene exclusivamente en un ensayo vacío separado de la base activa. Una restauración de empresa pide nuevo ingreso; SaaS conserva su sesión.
- Operaciones en curso bloqueadas para impedir doble envío. Ante timeout/resultado incierto no hay repetición automática: se bloquea repetir hasta consultar listado/uso mediante Actualizar. Se limpia selección/confirmación y estado al cambiar contexto. Los mensajes de servicio no disponible no muestran variables privadas del servidor.
- Se ajustó PlanProvider para compartir y esperar una consulta de capacidades ya en curso, evitando decidir sobre un cupo antes de recibir la respuesta.

Archivos principales:
- Web: `pages/saas/respaldos.page.ts` y nueva plantilla `.html`, `services/respaldos-empresa.service.ts`, modelos SaaS, rutas, menú y mensaje de ingreso posterior a restauración.
- Flutter: `models/respaldo_models.dart`, `services/respaldos_service.dart`, `screens/respaldos/respaldos_screen.dart`, enlaces en Perfil/Plataforma y PlanProvider.
- Pruebas: `respaldos.page.spec.ts`, `e2e/cu25-respaldos.spec.ts`, `test/cu25_respaldos_test.dart`; fixture E2E con R2 en memoria y ejecutores automáticos desactivados únicamente en modo STYLEAR_E2E_RESPALDOS. Instrucciones añadidas al README E2E existente.

Validación:
- 23 pruebas unitarias web aprobadas; 68 pruebas Flutter aprobadas y análisis limpio. APK debug actualizado con el archivo de defines local existente. No instalado ni probado físicamente en este bloque.
- Edge/AXE contra FastAPI/PostgreSQL reales y R2 en memoria: programación fija, dos copias manuales, 429 de cuota, eliminación sin liberar cupo, separación A/B (404 al acceder a copia ajena), bloqueo por rol, revisión de empresa activa, suspensión, frase exacta, Enter sin restaurar, restauración real de empresa, sesión anterior revocada, nombre del perfil recuperado, suscripción Básico actual preservada y copia preventiva registrada. La sesión de B permanece válida.
- Restauración global real mediante pg_dump/pg_restore hacia una segunda base desechable y vacía: clon con 18 versiones de esquema y cero sesiones activas. La base API original y la sesión SaaS permanecen disponibles. Incluye vista estrecha y AXE.
- Las bases utilizadas tienen prefijo `stylear_test_cu25_ui_*`. No se restauró ni modificó la base local de trabajo ni se usó un bucket real para las pruebas.

Pendiente externo concreto:
La comprobación local de configuración devolvió incompleta. Para crear copias reales en local deben completarse en el servidor `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT_URL` y `STYLEAR_BACKUP_KEY` (con R2_REGION opcional). La clave de cifrado debe ser estable y corresponder a las copias existentes; no reemplazarla ni regenerarla si ya hay respaldos que dependan de ella. No se afirma validación contra R2 real. La interfaz y el contrato están implementados y probados con almacenamiento simulado.

Siguiente bloque: CU22, dashboard comercial, reportes paginados y programación/historial en ambas aplicaciones.

Cierre adicional del bloque 3: build web de producción aprobado; APK debug final compilado. La API de pruebas fue detenida y se retiraron las bases de API y ensayo, sus DSN y los JSON temporales de fixture/correo.

Incidencia del backend descargado detectada durante el recorrido: GET de usuarios y bitácora podía devolver 500 (`set_session cannot be used inside a transaction`). El contexto compartido adquiría un cerrojo antes de que esas consultas configuraran REPEATABLE READ/read-only. Se movió la configuración al inicio de `_transaccion`, antes del primer SELECT, y ambos listados ahora solicitan su política al abrir el contexto. Se mantienen el cerrojo y el aislamiento entre empresas; no se cambian permisos ni datos. Archivos: `perfil_repository.py`, `administracion_acceso_repository.py`, `bitacora_repository.py`.
Verificación de la incidencia: tres pruebas de integración existentes de listado de usuarios, alcance SaaS autorizado y aislamiento/detalle de bitácora aprobaron sobre PostgreSQL desechable. La base de regresión se retiró al finalizar. No se modificaron datos de la base local de trabajo.


### 2026-11-06 — CU22, bloque 4: dashboard, detalle paginado y reportes programados

Implementación web y Flutter:
- Dashboard comercial con rango de fechas, sucursal, canal y comparación opcional con el período anterior calculado por el servidor. Ventas presenciales/digitales, reservas, stock actual, serie, productos más vendidos y definiciones. Flujos separados por moneda; importes mostrados como cadenas decimales del servidor. Neto significa cobrado menos reembolsado, no ganancia. Una base anterior cero se muestra sin porcentaje; no se inventa una variación.
- Detalle mediante POST /reportes/detalle, con páginas remotas de 20 filas y resumen/serie del resultado completo. No se descargan miles de filas para paginar en memoria. Se conservan los filtros de la consulta visible para cambiar de página y exportar aunque el editor haya cambiado. Flutter muestra también las fechas de esa consulta, no las del formulario en edición.
- Exportaciones PDF/Excel sujetas a capacidades efectivas. Más de 5.000 filas siguen siendo consultables; se pide reducir el rango para exportar, sin producir archivos truncados. La exportación HTML móvil preexistente obtiene el reporte completo dentro del mismo límite.
- Interpretación distingue origen TEXTO/VOZ y aplica capacidades de interpretación/dictado, sin decidir por nombre del plan. Conserva revisión de filtros antes de consultar. No se afirma prueba física del micrófono en este bloque.
- ADMINISTRADOR consulta su empresa y administra programaciones. ENCARGADO_SUCURSAL tiene dashboard/reportes de su sucursal asignada, sin selector Todas, tipo USUARIOS ni sección Automáticos. Se habilitó su acceso en rutas/menú sin darle los contadores administrativos globales. ADMIN_SAAS conserva su alcance de plataforma.
- Programaciones: alta, edición parcial con revisión, frecuencia/día/hora/zona/período, sucursal, formato, destinatario, filtros, activo/pausado e historial de ejecuciones. Ante 409 se conserva el formulario y se exige revisar la versión vigente. Solo los cambios explícitos se guardan; se conservan pausas y filtros concurrentes no editados. Cambiar la frecuencia normaliza el día correspondiente.
- Envío manual con confirmación, bloqueo de doble envío y prohibición de ejecutar una programación pausada. Una respuesta incierta bloquea repetir hasta revisar historial y autorizar expresamente otra ejecución; no hay reintentos automáticos. El mensaje refleja el transporte configurado sin afirmar entrega a una dirección que el servidor pudiera sustituir.
- Web mantiene /dashboard y /reportes; Flutter reutiliza ReportesAdminScreen y su sección Automáticos, separando widgets de dashboard/programación para evitar otra implementación paralela. Datos y respuestas se invalidan al cambiar contexto de sesión.

Archivos principales:
- Web: modelos/servicio de reportes; dashboard-comercial.component.ts; reportes.page.ts/.html; programaciones-reportes.component.ts; rutas/menú y dashboard existente.
- Flutter: reporte_models.dart, reporte_service.dart, reportes_admin_screen.dart, dashboard_comercial_tab.dart y programados_reportes_tab.dart.
- Pruebas: reportes.page.spec.ts, e2e/cu22-reportes.spec.ts, test/cu22_reportes_test.dart; fixture exclusiva STYLEAR_E2E_REPORTES documentada en el README E2E existente.

Validación:
- 25 pruebas unitarias web aprobadas. Build de producción aprobado con advertencias CommonJS preexistentes.
- Flutter: 72 pruebas aprobadas y análisis sin incidencias. APK debug generado con el archivo local de defines existente; sin instalación ni validación física de las pantallas nuevas.
- Dos recorridos Edge aprobados contra FastAPI/PostgreSQL reales desechables: 5.200 ventas, total 10.400, 260 páginas, cambio de formulario sin alterar la página consultada, bloqueo de exportación grande, descarga PDF de usuarios, alta/edición 409/formulario conservado/pausa preservada, activación, envío con adjunto capturado e historial ENVIADO; encargado sin opciones prohibidas y 403 al pedir sucursal ajena. AXE sin incidencias en dashboard, detalle y programación a ancho estrecho.
- Los ejecutores automáticos y proveedores externos se sustituyen solo en la fixture. No se enviaron correos reales ni se modificó la base local de trabajo. No se agregaron migraciones en este bloque.

Pendientes: verificación física de pantallas/dictado en el bloque final; configuración R2 real sigue pendiente según el bloque 3. Siguiente bloque: recomendaciones y asistente/chat (CU23–CU24), seguido de notificaciones y recorrido Android.

Cierre del bloque 4: repetición final de 25 pruebas web y build de producción aprobada; análisis Flutter limpio y APK debug final generado. API temporal detenida, puerto 58127 libre y base stylear_test_cu22_ui_* retirada junto con su DSN y los JSON de fixture/correo. No se dejaron servidores nuevos ni se instaló el APK en el teléfono.


### 2026-11-07 — CU23–CU24, bloque 5: recomendaciones, preferencias y asistente

Implementación:
- Se amplían las pantallas actuales: web /para-ti, detalle de prenda y chat flotante; Flutter modo Para ti del catálogo, detalle de prenda y AsistenteScreen. No se crea otro chatbot ni se modifican compras, reservas o pagos para atender consultas.
- Preferencias móviles en un sheet accesible desde el icono de ajustes de la cabecera al entrar en Para ti, sin superponer el acceso flotante al chat. Lectura/guardado de categorías (máximo 10) y usar_historial; carga, fallo recuperable, formulario conservado y bloqueo de doble guardado. Desactivar personalización deja vacía la lista personal y conserva favoritos. Web explicita el mismo comportamiento y añade controles de carga, límite de categorías, errores y contraste.
- Recomendaciones conservan el motivo real del servidor; no convierten score en porcentaje ni prometen uso de IA cuando son sugerencias iniciales. Las tarjetas móviles existentes ya usaban precioFinal. Web corrige precio cero válido; precios ausentes se muestran pendientes. El detalle móvil reconsulta por talla/sucursal y descarta resultados anteriores al cambiar selección.
- Contratos del asistente ampliados con contexto permitido completo (empresa/producto/variante/sucursal, recurso tipo+ID, términos/colores/tallas/precio), fuentes, consultado_en, datos, aclaración y hay_mas. Cada siguiente pregunta reenvía el contexto devuelto, sin mandar historiales completos ni campos arbitrarios. Hay una acción para quitar selección e iniciar otra consulta.
- Desde el detalle se abre el mismo asistente con la prenda/variante/sucursal real; web conserva también talla/color seleccionados cuando todavía hay varias variantes. La selección de una tarjeta puede fijar la variante consultada para el siguiente turno.
- Tarjetas de prendas muestran precio_final_exacto, precio_final o precio_vigente en ese orden, conservando cero y distinguiendo null; moneda, promoción, descripción/material, disponibilidad y motivo. Las alternativas se identifican como posibles resultados que no cumplen todos los filtros. Consultar no reserva stock.
- Datos estructurados se muestran con etiquetas comprensibles: sucursales y políticas públicas, reservas, ventas, órdenes de pago, deliveries, cambios, devoluciones y reembolsos. Se conserva reembolso_confirmado y condiciones de la operación; no se transforma aprobación en dinero reintegrado. Varias operaciones requieren elegir explícitamente tipo+ID, sin seleccionar la primera automáticamente.
- Para RESERVA, ORDEN_PAGO y DELIVERY se abre el detalle existente con su identificador correspondiente. VENTA nunca se trata como orden de pago; los restantes tipos mantienen consulta y seguimiento en el chat, sin inventar relaciones ni rutas de detalle. Acciones de web y móvil usan destinos internos permitidos; no se abren URLs arbitrarias del mensaje.
- Mensajes de hasta 600 caracteres, carga/error, 401 sin repetir como visitante y 429 respetando Retry-After (o espera conservadora), sin reintentos automáticos. ApiException móvil conserva Retry-After sin alterar el tratamiento anterior de errores.
- Cambio de cuenta/empresa limpia conversación, selección, texto y recomendaciones en memoria. Respuestas en vuelo se descartan por generación; cambiar selección también invalida consultas de recomendaciones anteriores. Se limpian preferencias visibles de un sheet que permanece montado. No se guarda conversación permanentemente.

Archivos principales:
- Web: asistente.models.ts, asistente.service.ts, componente asistente-chat, ParaTiPage y detalle de producto.
- Flutter: asistente_models.dart, recomendacion_service.dart, ApiException/ApiClient, AsistenteScreen y helper datos_chat.dart; nuevo preferencias_recomendaciones_sheet.dart dentro de catálogo y ajustes en catálogo/detalle existentes.
- Pruebas: asistente-chat.component.spec.ts, e2e/cu23-cu24.spec.ts y test/cu23_cu24_test.dart. Fixture y README E2E existentes ampliados; no se crea otro archivo de seguimiento.

Verificación realizada:
- 29 pruebas unitarias web aprobadas y build de producción aprobado (advertencias CommonJS existentes). Incluyen contexto devuelto, tipo+ID, cambio de sesión con respuesta pendiente, 429 sin repetición, precio exacto/cero/null y rechazo de destinos arbitrarios.
- 76 pruebas Flutter aprobadas; las cuatro nuevas cubren contratos/precio/destinos, continuidad y limpieza de chat, preferencias desactivadas conservadas ante fallo, y descarte de respuesta de una sesión anterior.
- Recorrido Edge/AXE contra FastAPI/PostgreSQL desechables aprobado: favoritos conservados con personalización apagada, precio promocionado BOB 40.00 y material, continuación con talla M, dos reservas propias sin elección silenciosa, seguimiento de vencimiento, 403/404 al producto de otra empresa y cliente de B sin reservas de A. AXE sin incidencias en Para ti y chat en vista estrecha.
- Se usan datos sintéticos, RULES y ausencia de embeddings externos. No se cambió la configuración real de IA, no se indexaron productos ni se enviaron correos/push. CU23/CU24 no requieren nuevas migraciones.

Límites pendientes: validación física del chat, voz, preferencias y navegación en el teléfono dentro del bloque final; este bloque no acredita proveedor real de IA ni APK de distribución. Configuración R2 real y documentación/credenciales BCP continúan pendientes según registros anteriores. Siguiente bloque: CU27, bandejas/preferencias/destinos y avisos emergentes Android.

Cierre del bloque 5: repetición final del recorrido Edge/AXE aprobada; build web de producción aprobado; análisis Flutter sin incidencias y APK debug generado con los defines locales existentes. No instalado en el teléfono. API temporal detenida y base stylear_test_cu23_ui_* eliminada junto con el DSN y los JSON de fixture/correo. Se conserva la base local de trabajo y no se dejan servidores nuevos corriendo.


### 2026-11-07 — CU27, bloque 6: bandejas, preferencias, destinos y Android

Implementación web:
- La clase NotificacionesPage conserva la ruta /notificaciones y se separa del archivo de pagos de empresa mediante reexportación, sin crear otro módulo. PagoService/modelos existentes incorporan bandeja paginada de 20 avisos, resumen global, lectura individual/todas, preferencias, destino y trazabilidad.
- Lectura/categoría, total filtrado, contador de no leídas global independiente del filtro/página y paginación remota. Marcar todas alcanza la cuenta completa y refresca la consulta; se ajusta la página si el filtro queda vacío. Estados de carga, error y bloqueo de operaciones duplicadas.
- Preferencias dentro de la misma bandeja: categorías GENERAL/COMPRA/PAGO/RESERVA/ENTREGA/CAMBIO/DEVOLUCION/REEMBOLSO. Se guardan solo cambios explícitos para conservar ajustes simultáneos; un fallo mantiene el formulario. Silenciar afecta push y no elimina la bandeja.
- Cada apertura consulta /{id}/destino con la sesión vigente. Reserva, orden de pago y delivery usan sus detalles existentes. Cambio/devolución abren sus flujos con selección inicial. VENTA nunca se trata como orden de pago; ante falta de destino apropiado o recurso no disponible se explica la continuidad en bandeja. No se usan URLs arbitrarias del aviso.
- Trazabilidad distingue leído en bandeja, envíos aceptados por Firebase, pendientes/fallidos/omitidos. No equipara aceptación FCM con visualización. Se limpian listas, preferencias, contadores y trazabilidad al cambiar contexto; respuestas anteriores se descartan.

Implementación Flutter/Android:
- NotificacionesScreen consume el mismo contrato paginado y conserva la sección preexistente de reembolsos de compras no completadas. Preferencias en sheet; selección y errores se conservan y limpian por generación de sesión. La navegación elimina el antiguo PagoStripeScreen(checkoutUrl:'') para consultas.
- Helper de destinos tipados reutiliza ReservaDetalleScreen, DetallePagoScreen(esAdmin:false), DetalleDeliveryScreen(esStaff:false), CambiosScreen y DevolucionesScreen. Estos dos últimos admiten selección inicial autorizada dentro de sus flujos; no se confunden venta y pago por coincidencia de ID.
- Aviso de primer plano mediante puente nativo Android `stylear/notificaciones` en MainActivity.kt, sin dependencias Flutter nuevas ni actualización masiva de SDK. Canal `stylear_transacciones_v1`, importancia alta inicial y sin sobrescribir un canal existente, icono monocromo dedicado y contenido genérico. Se declara POST_NOTIFICATIONS y el canal/icono por defecto de Firebase en AndroidManifest.xml.
- Inicialización de SDK/handlers separada de solicitar permiso. FCM onMessage valida sesión/empresa y acceso al aviso antes de presentar localmente; deduplica por generación/empresa/notificación. En segundo plano se conserva la notificación que presenta Android, sin crear otra en el handler de fondo.
- Aperturas mediante onMessageOpenedApp/getInitialMessage y callback/inicio nativo para avisos locales. PuentePush usa un navegador nuevo por generación y consulta el destino con autorización actual. Sin ingreso se conserva solo referencia mínima; al cambiar cuenta se descarta contenido anterior. No se navega directamente con IDs del payload.
- UUID de instalación persistente y renovación de token con activación previa. Logout intenta desvincular con sesión aún válida antes de revocar el acceso y limpia avisos locales. Los cambios de cuenta invalidan referencias/contadores; el backend conserva sus validaciones de sesión y preferencia en la cola.
- Arranque de la aplicación no espera indefinidamente al SDK; un fallo de preparación puede reintentarse. Sin Firebase o permiso/canal disponible continúa la bandeja. Los ajustes de Android solo se abren por acción del usuario; no se elevan permisos ni importancia silenciosamente.

Validación local completada:
- 33 pruebas unitarias web y build de producción aprobados. Recorrido Edge/AXE aprobado sobre PostgreSQL/FastAPI desechables: más de 40 avisos y tres páginas; contador global al filtrar; marcado de todas fuera de filtro/página; preferencias parciales con cambio concurrente conservado; trazabilidad; compra sin abrir pago; reserva correcta; 404 de aviso ajeno. Vista estrecha sin incidencias AXE.
- 82 pruebas Flutter aprobadas; seis nuevas cubren destinos y referencia mínima, desvinculación con UUID/token de sesión, contador/lectura de todas/reembolsos, preferencias fallidas conservadas, respuesta tardía descartada y puente Android por acción explícita. La prueba antigua de CU16 se actualizó al contrato de bandeja paginada, conservando su verificación de reintento/lectura/Firebase ausente.
- Análisis Flutter limpio. APK release de prueba compilado e instalado mediante actualización, sin borrar datos de la app. La configuración release existente firma con clave de depuración para pruebas locales; no se afirma publicación ni firma de distribución de tienda.
- Manifiesto combinado release verificado: POST_NOTIFICATIONS, canal `stylear_transacciones_v1` e icono dedicado. Dispositivo conectado Android 13/API33; paquete instalado minSdk24/targetSdk36. El permiso de notificaciones ya figuraba concedido al inspeccionar el paquete.

Prueba física en curso:
El usuario confirmó teléfono conectado y disponible. Se instaló el APK y preparó una base `stylear_test_cu27_ui_*`, una cuenta cliente sintética y una API de ensayo sin ejecutores automáticos. Aún no se emitió ningún FCM real en este bloque: el teléfono seguía bloqueado y la base de ensayo tenía cero dispositivos vinculados. Se pidió desbloquear StyleAR, ingresar a la cuenta temporal y pulsar Activar avisos push. No se acredita todavía recuadro visible, apertura real en primer/segundo plano/proceso terminado, pantalla bloqueada, canal/permiso denegado ni renovación física. Mantener esos puntos pendientes hasta obtener evidencia; no sustituirlos por aceptación del proveedor.

Fuentes técnicas consultadas: Firebase Flutter receive-messages (https://firebase.google.com/docs/cloud-messaging/flutter/receive-messages), y referencia del paquete flutter_local_notifications para evaluar compatibilidad (https://pub.dev/packages/flutter_local_notifications). Se eligió el puente Android nativo en la estructura actual; no se añadió ese paquete.

Pendientes externos anteriores: R2 real incompleto y BCP sin documentación/credenciales sandbox. No se modificaron claves, proveedores ni datos de la base local de trabajo al preparar la prueba CU27.

Actualización al cierre local de CU27: APK release final generado y enviado como actualización al Redmi; análisis limpio y 82 pruebas Flutter aprobadas, 33 web aprobadas y build web de producción aprobado. La única regresión de prueba anterior era un mock con la lista plana antigua y fue corregida al contrato paginado. La API de ensayo en 127.0.0.1:58127 sigue activa, con ADB reverse tcp:8000 hacia tcp:58127 y ejecutores automáticos desactivados, para completar la prueba cuando el usuario desbloquee el teléfono y registre la cuenta temporal. Última verificación: cero dispositivos activos en la base de ensayo; no se envió FCM real. La base y su DSN deben retirarse después de la prueba física, no antes.

### 2026-11-07 — Recuperación de conexión del teléfono en CU27
El teléfono informó que no podía conectar. La lista ADB reverse estaba vacía. La verificación HTTP posterior confirmó que la API original de ensayo seguía activa en 127.0.0.1:58127 y aceptaba la misma cuenta; no fue necesario recrear la base ni cambiar credenciales. Se restableció adb reverse tcp:8000 tcp:58127. Una petición HTTP ejecutada desde Android hacia 127.0.0.1:8000 recibió HTTP/1.1 200 OK. Se solicitó reintentar en la app y registrar avisos para continuar la prueba física. El intento de iniciar otra instancia encontró el puerto ocupado y terminó; se mantiene la instancia original. No se emitió FCM como parte de esta recuperación.

Seguimiento de conexión CU27: se observó una nueva reconexión USB (túnel otra vez ausente). Se preparó un ayudante temporal y oculto .private/mantener_tunel_cu27.py, limitado a una hora y al teléfono de ensayo, que solo repone el mapeo si falta; no modifica conexiones existentes. Debe detenerse al terminar la prueba mediante el archivo .private/cu27_tunel_detener o su PID verificado. Se reabrió StyleAR para descartar conexiones previas. GET de empresa y POST de ingreso ejecutados desde Android devolvieron 200 OK. La pantalla de ingreso estaba seleccionando StyleAR, mientras la cuenta sintética pertenece a Atelier Norte; las credenciales originales siguen válidas en la empresa correcta. Android bloqueó la pulsación remota con INJECT_EVENTS, por lo que el cambio de empresa debe hacerlo el usuario manualmente; no se cambió la contraseña ni se intentó eludir ese permiso.

### 2026-11-07 — Validación real CU27 y error de registro Firebase
La app llegó correctamente a la bandeja y mostró Firebase Messaging/unknown con FCM Registration failed al pedir el token. Logcat confirmó ese error y SERVICE_NOT_AVAILABLE; Servicios de Google estaban presentes y habilitados, y se comprobó sin publicar valores que app ID, proyecto, remitente y clave pública coincidían con google-services.json. El registro funcionó posteriormente. El usuario aclaró que siempre utilizó Wi-Fi y no tenía datos móviles: no atribuir el fallo a un bloqueo de Wi-Fi ni afirmar que se resolvió cambiando de red. La causa exacta del fallo inicial no quedó demostrada.
Se verificó un único dispositivo activo vinculado al cliente sintético. Se enviaron tres avisos deliberados (IDs 50, 51 y 52), uno por prueba, solo a ese dispositivo; FCM aceptó los tres en un intento. El recuadro se observó en segundo plano y el usuario confirmó apertura de la reserva. Con StyleAR en primer plano se capturó el recuadro sobre el detalle de la reserva de ensayo. Después se llevó la app al inicio de Android, se cerró su proceso con am kill (sin force-stop) y se comprobó ausencia de PID antes del envío; el usuario confirmó que tocar el nuevo aviso inició la app y abrió la misma reserva.
La evidencia conservada de primer plano pertenece únicamente a StyleAR y a datos sintéticos: .private/cu27_primer_plano_1.png. Las capturas que incluían otras pantallas se descartaron y se añadió una comprobación de foco para capturar solo StyleAR. No interpretar recepción como lectura: el aviso de primer plano permanecía sin leer hasta abrirlo.
Se ajusta la interfaz para traducir fallos Firebase a un mensaje comprensible, conservar la bandeja y ofrecer Reintentar activación para la acción push; una prueba cubre que no se expongan java.io ni el código técnico. Los fallos de consulta mantienen su reintento de bandeja.
Pendientes de la revisión final: pantalla bloqueada, permiso/canal denegados, preferencia silenciada en dispositivo, renovación y cambio de cuenta físicos. La aceptación de esos escenarios no se infiere de las tres pruebas anteriores.

Cierre de la incidencia FCM: 83 pruebas Flutter aprobadas con el nuevo caso de mensaje comprensible y análisis sin incidencias. El estado del servidor confirma lectura de los avisos 50 (segundo plano) y 52 (apertura desde proceso cerrado); el aviso 51 recibido en primer plano permanece sin leer, manteniendo la distinción entre recepción y apertura. El registro y las tres recepciones funcionaron con Wi-Fi según aclaración del usuario. Se conserva la API y cuenta de ensayo para la matriz final pendiente; no se retiró la base todavía.

APK release final con manejo comprensible de error Firebase compilado y actualizado en el Redmi conservando datos. La cuenta de ensayo continúa disponible para los escenarios finales pendientes. Las pruebas efectuadas no determinan la causa exacta del fallo inicial del registro de Google.


### 2026-11-07 — Bloque 7: pantalla bloqueada y preparación de R2
El usuario confirmó que el aviso de ensayo apareció con el Redmi bloqueado (aviso 53; envío manual único aceptado por Firebase). Esta confirmación completa ese escenario físico y se suma a primer plano, segundo plano y apertura con proceso cerrado. Permiso/canal denegados, preferencia silenciada y cambio de cuenta/renovación físicos siguen pendientes; no se infieren de la recepción.
El usuario necesita crear una cuenta Cloudflare. Se inicia guía para registro y activación de R2; todavía no se acredita almacenamiento externo ni respaldos reales en R2. No compartir claves por el chat. La configuración debe usar bucket privado y credenciales S3 limitadas al bucket; la clave de cifrado de respaldos debe conservarse de forma estable.


Actualización bloque 7: el usuario desactivó temporalmente el permiso de notificaciones de Android, volvió a StyleAR y confirmó que la bandeja seguía funcionando; después reactivó el permiso. Esto verifica la continuidad de la bandeja con permiso desactivado, sin afirmar un envío físico durante esa desactivación ni una prueba independiente de canal silenciado. También confirmó cuenta Cloudflare creada y acceso al panel; sigue pendiente activar R2 y configurar el bucket y las credenciales.


### 2026-11-07 — Conexión real R2 verificada
El usuario creó stylear-respaldos-local y guardó las variables R2 en .env privado. Se verificó presencia sin mostrar valores. Faltaba boto3 en .venv, aunque requirements.txt ya declara boto3>=1.35,<2; se instaló boto3 1.43.126 y sus dependencias compatibles (la primera descarga agotó tiempo; el reintento terminó correctamente).
STYLEAR_BACKUP_KEY estaba ausente: se generó una clave Fernet y se guardó exclusivamente en .env, sin imprimirla. Conservar esta clave de forma estable y una copia segura para recuperar los respaldos. Se completó una operación real sobre R2 con contenido sintético pequeño: escritura cifrada, descarga, descifrado comparado con el original y eliminación del objeto temporal; todas aprobadas. No se subió la base de trabajo ni información de usuarios. La prueba acredita conexión/credenciales y cifrado, no un respaldo completo ni una restauración real en R2, que siguen pendientes. Se añadieron placeholders de configuración CU25 a .env.example, sin secretos.


### 2026-11-07 — Cierre del bloque 7: R2 completo, regresión y entorno local

- Nuevas pruebas optativas tests/test_cu25_r2_real.py: requieren STYLEAR_TEST_R2_REAL=1, DATABASE_URL igual a STYLEAR_TEST_DATABASE_URL apuntando a una base local stylear_test_*, destino global vacío con prefijo stylear_test_r2_destino_ en STYLEAR_TEST_R2_RESTORE_URL y bucket stylear-respaldos-local. Sin activación explícita no usan R2. Solo se operan y eliminan objetos creados por la ejecución; los assets son sintéticos y están aislados en tmp_path.
- Empresa: respaldo cifrado real en R2, previsualización, modificación de datos y eliminación válida de imagen/referencia, suspensión, copia preventiva, restauración, recuperación exacta de imagen y producto, empresa B intacta y eliminación mediante el servicio. La primera versión del ensayo dejó una referencia a una foto eliminada y el servidor rechazó correctamente una copia preventiva incompleta; se corrigió el escenario para simular una eliminación válida, sin debilitar esa protección.
- Plataforma: pg_dump de una base sintética, cifrado/subida R2, descarga/verificación y pg_restore en otra base inicialmente vacía; empresas verificadas y sesiones/dispositivos desactivados tras restaurar. Dos pruebas reales aprobadas; los objetos temporales se eliminaron y ambas bases se retiraron.
- Regresión PostgreSQL aislada: 48 pruebas aprobadas de test_cu22_cu27_integration.py, test_cu23_cu24_integration.py y test_cu26_planes_integration.py. Se ejecutó init_db.py dos veces en origen para comprobar idempotencia. Advertencias de deprecación existentes no impiden ejecución.
- Nueva regresión CU27 con PostgreSQL y proveedor FCM simulado: renovación del token en la misma instalación, uso del nuevo token, cambio de empresa, omisión de cola anterior, rechazo 404 de destino ajeno, baja tardía de la cuenta anterior sin desactivar la nueva, preferencia silenciada sin cola y con bandeja, desvinculación del propietario actual. Una prueba adicional aprobada; no se enviaron mensajes externos en ella.
- Verificación final de clientes: 33 pruebas web aprobadas; 83 pruebas Flutter aprobadas. pip check sin incompatibilidades. APK release instalado en la validación anterior; este cierre no cambió código móvil ni requiere otro APK. No se repitieron pruebas físicas que requieren manipulación del teléfono; ADB informa dispositivo ausente.
- Base local inspeccionada de forma solo lectura: 18/18 migraciones aplicadas, ninguna pendiente y ningún respaldo R2 previo registrado. No se restauró ni reemplazó esta base.
- Limpieza: detenidos procesos de la fixture CU27 identificados por su comando; ayudante de túnel marcado para detener; base stylear_test_cu27_ui_* eliminada mediante su DSN validado; retirados JSON de fixture/correo y DSN temporal. Se conservan logs/evidencia sintética ignorados por Git. No se borraron fotos de trabajo ni datos del teléfono.
- Arranque final: API normal con .env actual en 127.0.0.1:8000 y Angular de desarrollo en 127.0.0.1:4200, ambos ocultos y con logs privados. Backend health, directorio público (una empresa) y página /empresa devolvieron HTTP 200. Quedan corriendo para pruebas; no se mantiene la API temporal 58127. La app móvil necesita reconexión USB y reverse 8000→8000, más cierre de su sesión temporal y selección de la empresa real.

Conservar STYLEAR_BACKUP_KEY y las credenciales en un respaldo seguro fuera de Git. La clave no se volvió a generar durante las pruebas. BCP sigue condicionado exclusivamente a la entrega de acceso/documentación por el banco; no es correcto sustituirlo por una API inventada ni presentar el modo simulado como pago real.


### 2026-11-07 — Entorno detenido a petición del usuario
Se detuvieron el backend local 8000, Angular 4200 y sus procesos descendientes identificados mediante el registro privado de arranque. Se dejó la señal de parada del ayudante de túnel CU27. No se borraron bases, configuración, credenciales, archivos ni datos del teléfono. Los servidores ya no quedan corriendo; para retomar, usar los comandos de desarrollo local de este mismo documento.


### 2026-11-07 — Informe consolidado de CU22–CU27
Por solicitud del usuario se creó ../INFORME_IMPLEMENTACION_CU22_CU27.md. Explica los siete bloques, el alcance web/móvil y SaaS, migraciones, correcciones, respaldos R2 reales, pruebas y límites, junto con comandos para retomar el entorno detenido. Es un informe de entrega; este archivo conserva la bitácora única de seguimiento. Crear el documento no inició servidores ni cambió configuración o datos.


### 2026-11-08 — Documento faltante CU16–CU20
Se creó IMPLEMENTACION_CU16_CU20_2026-11-08.md para cubrir el salto documental entre CU11–CU15 y CU22–CU27. Consolida pagos por empresa, mayoristas, entregas, cambios y devoluciones/reembolsos; CU19 y CU20 incluyen actores, precondiciones, flujo, alternativas, reglas y postcondiciones. Distingue Stripe TEST verificado de QR BCP pendiente, y enlaza las fichas académicas, migraciones y pruebas. No se modificó código, configuración ni datos, no se ejecutaron pruebas nuevas y no se iniciaron servidores. Esta sigue siendo la única bitácora de avance.


### 2026-11-08 — Actualización descargada, migraciones y arranque local

Versiones revisadas: backend d87a6fd, frontend 6628f96 y móvil 6aff226. Se conservaron el Markdown CU16–CU20, el seguimiento y las modificaciones preexistentes de registradores de plugins Flutter.

Migraciones aplicadas tras respaldar la base local con pg_dump en formato custom y verificarla mediante pg_restore --list:
- 2026_11_07_cu26_stripe.
- 2026_11_08_cu21_offline.
- 2026_11_08_cu19_prenda_adicional.
- 2026_11_08_posventa_no_vendible.

Respaldo: .private/antes_actualizacion_20261108_123308.dump. Log privado: .private/migraciones_20261108_123308.log. Se ejecutó init_db.py dos veces, quedando 22/22 versiones y conservando los conteos de empresas, usuarios, productos, ventas, órdenes de pago, inventario, movimientos, devoluciones y cambios. No se restauró ni sustituyó la base de trabajo.

Dependencias: pip check sin incompatibilidades; Flutter pub get aprobado. Faltaba @angular/service-worker en node_modules pese a venir declarado en el nuevo lockfile; npm ci sincronizó 431 paquetes. Se conservan avisos EBADENGINE por Node 25 y CommonJS de leaflet/qrcode; la compilación y las 33 pruebas web aprobaron después de sincronizar. Flutter analyze sin incidencias y 84 pruebas aprobadas. APK release actualizado compilado (70,4 MB) con firebase.defines.local.json y API_URL local; no instalado porque ADB no detectó dispositivos conectados. Gradle/Firebase emite advertencias de futura compatibilidad KGP, sin impedir la compilación actual.

Se añadió environment.local.ts y la configuración build local para compilar mediante npm run build -- --configuration production,local: mantiene producción/Service Worker y conecta exclusivamente con http://127.0.0.1:8000. La configuración de producción original de Railway se conserva.

Backend normal iniciado en 8000 y PWA compilada servida en 4200 mediante .private/servir_pwa_local.py, con fallback de rutas Angular. Procesos registrados en .private/local_procesos.json y logs local_backend/local_frontend. Salud, directorio, página /empresa y manifiesto devolvieron HTTP 200. La web servida es compilada: después de modificar el frontend se debe reconstruir o utilizar npm start para desarrollo (sin acreditar PWA offline).

Comprobación en Edge con perfil temporal: la web consultó la API local y registró el Service Worker, pero la recarga offline falló. Diagnóstico confirmado: Kaspersky añade un script kaspersky-labs al HTML obtenido por las peticiones del Service Worker, alterando la huella frente a ngsw.json; Angular pasa a EXISTING_CLIENTS_ONLY. Las huellas del archivo compilado y del HTTP sin inyección coinciden. No se cambió el antivirus, no se omitió la validación de integridad y no se acredita recarga offline exitosa en este equipo. Queda pendiente resolver esta interferencia en el entorno antes de aceptar la prueba física PWA.

La primera regresión aislada aprobó 72 casos y falló uno de diferencia Stripe simulada: la nueva resolución de credenciales puede usar la clave TEST compartida del .env y el endpoint de confirmación manual la rechazó correctamente. Se aisló STRIPE_SECRET_KEY en la fixture api de CU16 (utilizada por CU19), manteniendo los mocks específicos de las pruebas que sí ejercitan Stripe. No se modificó el servicio comercial de pagos ni se deshabilitó su protección. Repetición final: 73 pruebas aprobadas, con 8 advertencias de deprecación existentes, en 493,78 segundos. Las dos bases desechables se retiraron al terminar.

Comprobación adicional en Edge: directorio y /operaciones-offline renderizados online, cero errores JavaScript no controlados. Para probar offline: ingresar primero con red, abrir Operaciones offline, preparar el acceso local y descargar datos; la recarga PWA todavía requiere resolver la alteración de HTML de Kaspersky. Para móvil, instalar build/app/outputs/flutter-apk/app-release.apk cuando se conecte el USB; el archivo ya contiene SQLite y las nuevas dependencias. ADB no detectó ningún dispositivo en este turno.

Cierre de esta actualización: 22/22 migraciones locales, 73 pruebas backend, 33 web y 84 Flutter aprobadas. API y web siguen corriendo en 8000/4200 para pruebas manuales. APK release disponible para instalar; no se instaló sin dispositivo. PWA offline mantiene el bloqueo externo Kaspersky confirmado; no se declara funcionamiento offline completo en este equipo. No hubo commits, push ni despliegue remoto.


### 2026-11-08 — Servidores detenidos después de la actualización
A petición del usuario se detuvieron el backend 8000, la PWA local 4200 y sus descendientes verificados mediante el registro de procesos. Se conservan migraciones, datos, configuración, compilación web y APK. No quedan activos los servidores iniciados en esta actualización.


### 2026-11-08 — Productos, Posventa y selección inicial de talla/color (web y móvil)

Alcance confirmado: web y Flutter; Posventa mediante tarjetas de acceso, solicitudes después de entrar; selección de producto/talla/color durante la solicitud inicial de cambio. La estimación inicial no sustituye la inspección, la validación posterior ni la aceptación económica.

Web:
- Productos incorpora ocho tarjetas cuadradas sobre el listado: Categorías, Tallas, Colores, Marcas, Temporadas, Colecciones, Variantes y precios y Promociones. Se retiraron esos accesos separados del menú lateral. Los tres enlaces de cada fila (mayoristas, variantes y promociones) se presentan como botones con borde, fondo y foco, conservando el producto en sus parámetros.
- /devoluciones y /mis-devoluciones son entradas a las tarjetas Devoluciones/Cambios; Políticas posventa aparece solo para ADMINISTRADOR. Las solicitudes se consultan en /devoluciones/solicitudes y /mis-devoluciones/solicitudes. Los enlaces antiguos de notificaciones con seleccion se redirigen conservando el parámetro. Cambios y Políticas dejan de ser opciones independientes del menú; se agregaron accesos de regreso al módulo.
- Pagos incluye Configurar pagos de prueba para ADMINISTRADOR. La configuración conserva la ruta protegida /pagos-empresa y ofrece regreso a Pagos. No se ampliaron permisos de encargado, cajero o cliente.
- Selector reutilizable de producto → talla → color, agrupado por ID de producto para separar modelos con el mismo nombre. Solo ofrece combinaciones disponibles y muestra precio y existencias. La prueba detectó y corrigió la selección visual de talla al sustituirse las opciones del producto.
- La solicitud inicial exige elegir una variante disponible y muestra pagar/reintegrar/sin diferencia estimada. Cambiar cantidades consulta otra vez y descarta respuestas antiguas. Se conserva la variante en la solicitud; después de inspeccionar vuelve a consultarse y cotizarse antes de aceptar.

Backend:
- Nuevo GET /api/v1/posventa/cambios/opciones-solicitud con venta_id, venta_detalle_id y cantidad, limitado a compras propias elegibles. Devuelve crédito estimado y variantes con producto_id, talla, color, precio y disponibilidad de la sucursal original.
- Solicitud admite reemplazo_id opcional para compatibilidad con clientes anteriores. Los clientes actualizados lo seleccionan al solicitar. La creación valida empresa, stock y precio dentro de su transacción y guarda la selección y diferencia estimada en los campos existentes de cambio_prenda. La huella de idempotencia incluye la variante; reusar una clave con otra selección devuelve conflicto.
- La solicitud continúa SOLICITADO, sin crédito inspeccionado, reserva ni orden de pago. La cotización definitiva sigue requiriendo aprobación/inspección y revalida precio/stock; se conservan el pago de diferencias, reintegro por el medio original y comprobante de entrega.
- No se requirió migración: no se añadieron columnas y no se modificó la base local de trabajo.

Flutter:
- Gestión de productos sustituye las pestañas por tarjetas antes del listado y reutiliza las secciones existentes de categorías, tallas, colores y variantes. Se completaron pantallas de marcas, temporadas, colecciones y promociones con listado, alta, edición y activación/desactivación mediante las API existentes.
- Promociones conserva productos, sucursales, período e importe al editar. No permite guardar si no terminaron de cargarse las referencias necesarias. Se ajustan límites de los campos a sus contratos.
- Perfil agrupa cambios y devoluciones en una entrada. Posventa muestra las tarjetas y la configuración de políticas solo para el administrador. Se agregó el formulario móvil de plazos/condiciones y habilitación, con versión y conservación de campos ante conflicto.
- Historial de pagos incorpora Pagos de prueba solo para el administrador, con configuración de medios/titular y versión; nunca solicita ni muestra claves privadas. Los errores de guardado se muestran junto al botón para que sean visibles al final del formulario.
- Diálogo inicial de cambio con cantidad, motivo, producto/talla/color, precio y diferencia estimada. Usa la misma selección por variantes en la cotización posterior y conserva la preferencia guardada.

Verificación:
- 30 pruebas backend de cambios y posventa aprobadas en PostgreSQL desechable. Dos nuevas comprueban selección inicial persistida, transacción revertida con variante ajena, idempotencia, ausencia de pago/reserva anticipados, recotización con precio nuevo y rechazo de compras ajenas/cantidades excedidas.
- 34 pruebas web aprobadas, incluida selección de talla/color y productos homónimos. Build de producción con API local aprobado.
- Dos recorridos Edge/AXE aprobados: tarjetas de Productos y Posventa con permisos, vista estrecha, selección inicial, estimación, inspección, prenda adicional, diferencia pagada y comprobante; y devolución entrando por las tarjetas, inspección no vendible, aceptación e integración QR simulada. Sin incidencias AXE en las pantallas verificadas. Evidencias sintéticas privadas: productos_tarjetas.png y cambio_talla_color.png.
- Análisis Flutter sin incidencias y 88 pruebas aprobadas. Incluyen selección inicial/precio, tarjetas sin consultas prematuras, ocultación de políticas al cliente, conflicto de política con campos conservados y edición de promoción sin pérdida de vínculos/importe.
- APK release final generado en tr-mobile/build/app/outputs/flutter-apk/app-release.apk (70,7 MB), con configuración local. No instalado porque ADB no detectó un teléfono. Firma local de pruebas preexistente; no se publicó en tienda.
- Se cerraron las APIs/servidores de ensayo y se retiraron las bases y cuentas sintéticas junto con sus DSN/fixture/correos. Los puertos 8000, 18127 y 48123 quedaron libres. En 4200 se detectó otro proceso node.exe de Angular (PID 4712), ajeno a las APIs/servidores de este ensayo, y se conservó. Se conservaron los cambios previos de configuración local y los registradores Flutter que ya estaban modificados antes de esta tarea.

No se hicieron commits, push, despliegues ni cambios en credenciales, antivirus o proveedores externos. El bloqueo PWA de Kaspersky registrado en la revisión anterior no se modificó como parte de esta reorganización.


### Cliente adicional para pruebas (2026-11-08)

Se creó la cuenta `cliente.prueba` con rol CLIENTE en StyleAR, sin modificar las cuentas existentes. Se verificó el inicio de sesión y se cerró la sesión de verificación. Credenciales guardadas únicamente en `.private/cliente_nuevo_20261108.json`.


### Corrección del avance al pago de cambios (2026-11-08)

Al aprobar la inspección, el backend valida y cotiza automáticamente la variante elegida en la solicitud inicial con el crédito reconocido y el precio vigente. No se cobra ni se reserva stock antes de la aceptación del cliente. Si el reemplazo dejó de estar disponible, la inspección se conserva y se permite actualizar la selección.

Web y Flutter permiten confirmar la diferencia sin volver a escoger producto, talla o color. La confirmación muestra las opciones de pago para diferencias positivas; el saldo a favor informa que el reintegro debe procesarlo la tienda por el medio original. La selección posterior queda como modificación opcional o para agregar una prenda con saldo. Las solicitudes aprobadas anteriormente tienen «Continuar con el reemplazo solicitado» y las cotizaciones pueden actualizarse si vencen.

Validación: 22 pruebas de integración CU19 aprobadas en base desechable (incluyen diferencia positiva, negativa y cero desde selección inicial), 6 pruebas Flutter aprobadas (incluye confirmación y acceso a QR/Stripe sin reseleccionar), compilación web production,local correcta. Sin migraciones nuevas ni movimientos de dinero sobre las solicitudes comerciales. Base desechable eliminada al terminar.


### Analítica mediante machine learning — bloque 1 (2026-11-08)

Se revisaron los apartados 8.4 y 8.5 de la página 6 del enunciado y se definieron predicción supervisada de ventas semanales y segmentación no supervisada RFM. El diseño, variables, evaluación temporal, aislamiento por empresa, integración prevista y fases están en `ANALITICA_MACHINE_LEARNING.md`.

Se implementó un generador reproducible de datos exclusivamente sintéticos en el módulo inteligencia y su CLI `scripts/generar_datos_analitica.py`. No conecta con PostgreSQL ni proveedores externos, no requiere nuevas dependencias y no altera tablas comerciales. Genera seis CSV y manifiesto con hashes dentro de `.private/analitica/dataset_v1`; rechaza salidas existentes. Importes en centavos, IDs ficticios y origen explícito.

Ejecutado: 49.857 tickets, 30.240 registros diarios, 4.032 muestras semanales y 299 perfiles RFM, de 2 empresas, 24 productos y 300 clientes. Las particiones cronológicas se prepararon sin utilizar objetivos futuros como variables. Cuatro pruebas aprobadas de integridad, reproducibilidad, referencias, ventanas temporales y protección de salida. Los modelos todavía no se entrenaron; siguen pendientes entrenamiento supervisado/no supervisado, API, dashboard web/móvil y documentación ICONIX de esos bloques.


### Analítica ML — bloque 2 y aclaración de fuente oficial (2026-11-08)

El usuario indicó que la fuente oficial debe ser la del despliegue y que únicamente tiene acceso a su web. Se solicitó la URL y el rol para determinar una exportación autorizada. No se consultó ni modificó la BD oficial; tampoco se entrenó con la BD local. La preparación para extracción oficial queda pendiente de identificar el acceso disponible.

Se implementaron entrenamiento supervisado por empresa, pipeline categórico + Random Forest, selección entre cuatro configuraciones por validación cronológica, evaluación final con MAE/RMSE contra referencia de semana anterior, métricas por sucursal y persistencia de artefactos locales con origen/versiones/hashes. Las dependencias opcionales y fijadas están en `requirements-ml.txt`; `requirements.txt` y el arranque del servidor no cambian.

Ensayo SINTÉTICO para SYN-E01: MAE 4,470 contra 5,565 de referencia, RMSE 5,785 contra 7,542, con 324 muestras de prueba. No se presenta como precisión sobre el despliegue oficial. Artefactos en `.private/analitica/modelos/sintetico_e01_v1`. Ocho pruebas aprobadas de datos/entrenamiento, integridad, temporalidad, aislamiento y persistencia.

Se actualizó `ANALITICA_MACHINE_LEARNING.md` con comandos, métricas de demostración y plan para exportación oficial, incluida la necesidad de revisar si hay historial de precios y stock antes de reutilizar esas variables. Pendientes: fuente/exportador oficial, modelo no supervisado, API/dashboard, reportes y documentación ICONIX de esos bloques.


### ML — auditoría de fuente oficial Railway (2026-11-08)

El usuario configuró `.private/analitica-oficial.env` y autorizó la revisión del historial oficial. Conexión SSL verificada y consultas realizadas en transacciones `REPEATABLE READ READ ONLY`, con tiempos máximos y rollback, limitadas a StyleAR. No se modificaron datos, no se ejecutaron migraciones ni se mostraron credenciales o datos personales.

Resultado definitivo: 8 ventas completadas en 3 días y 1 cliente comprador; 4 ventas con señal de prueba y 2 sin detalle (exclusiones que pueden solaparse). Quedan 4 candidatas con detalle y sin señal de prueba, de un solo cliente, entre 2026-09-21 y 2026-09-29 en America/La_Paz. Todos los pagos conservan modo LEGADO, por lo que no se certifica que las candidatas sean ventas reales. Catálogo: 11 productos, 2 sucursales y 3 clientes registrados.

El usuario confirmó que no existe historial adicional. Se registró estado local HISTORIAL_INSUFICIENTE para predicción/segmentación oficiales y no se entrenó ningún modelo oficial. Se mantiene la demostración académica sintética separada de reportes comerciales oficiales. Evidencia agregada en `.private/analitica/`, sin conexión/contraseñas, y decisión documentada en `ANALITICA_MACHINE_LEARNING.md`. Pendiente implementar el estado en API/dashboard; el JSON local no implica funcionalidad desplegada.


### ML — bloque 3, segmentación RFM (2026-11-08)

Implementados servicio y CLI de segmentación no supervisada por empresa en el módulo inteligencia. Pipeline log1p + StandardScaler + K-Means, selección de cantidad de grupos por silhouette, evaluación interna, estabilidad entre semillas mediante ARI y perfiles descriptivos con medias/medianas. No se usan IDs como variables ni se crean etiquetas comerciales predeterminadas. Se validan hashes, origen, empresas, perfiles, mínimos de datos, fecha y ventana. Se rechaza sobrescribir artefactos.

Ensayo SINTÉTICO: SYN-E01 agrupa 149 clientes en 2 grupos (86/63), silhouette 0,453; SYN-E02 agrupa 150 en 2 grupos (81/69), silhouette 0,493. ARI medio 1,000 entre las semillas adicionales de ambos ensayos; esto no demuestra estabilidad temporal ni exactitud comercial. El cliente sin compras recientes se cuenta aparte. Artefactos privados con pipeline, evaluación y asignaciones listos para consumo posterior por la API/dashboard.

También se entrenó la predicción supervisada sintética de SYN-E02 para completar ambas empresas de demostración: MAE 3,758 frente a referencia 4,840. Se conservaron los artefactos anteriores. Dependencias sin cambios respecto al bloque 2 y ninguna conexión a la base oficial en este bloque.

Validación: 20 pruebas aprobadas de datos, predicción y segmentación (12 de segmentación). Documentación única de ML actualizada en `ANALITICA_MACHINE_LEARNING.md`. API, pantallas, exportaciones e ICONIX de esos recorridos siguen pendientes; fuente oficial permanece con historial insuficiente y sin modelos publicados.


### ML — bloque 4, API protegida de analítica (2026-11-08)

Se agregaron GET de estado, predicciones históricas, métricas y segmentos bajo `/api/v1/reportes/analitica`, registrados en app.main. Reutilizan sesión activa, permisos y plan de Reportes. El administrador ve su empresa; el encargado solo su sucursal y no segmentos/resúmenes globales. SaaS y demás roles reciben 403. No se permite elegir empresa/dataset/ruta mediante parámetros; filtros tipados rechazan parámetros adicionales y límites inválidos.

La API lee JSON/CSV de resultados previamente calculados, valida registro por código+ID de empresa, hashes, origen, pertenencia y rutas confinadas. No carga joblib/sklearn ni entrena o accede a Railway por petición. El origen predeterminado OFICIAL no se sustituye por DEMOSTRACION. El estado oficial expuesto es la auditoría fechada de historial insuficiente, sin predicciones oficiales ni consulta en vivo. Las predicciones sintéticas se identifican como evaluación histórica, no pronóstico oficial vigente.

Se creó el registrador local `scripts/registrar_analitica_demo.py` y `.private/analitica/registro_api.json`, asociando Suc Banzer 1 y Suc Busch 2 a SYN-E01-S01/S02. La tercera sucursal ficticia no se expone como predicción de una sucursal real. La paginación no altera los totales/series/métricas, calculados sobre todo el filtro autorizado.

Validación: 42 pruebas aprobadas de datos/modelos/API, incluidas 22 de API/registro. Comprobación adicional con app.main y sesiones reales locales: administrador 200 en estado/predicciones/segmentos (216 filas de evaluación), encargado 200 en predicciones (108 filas, solo sucursal 1) y 403 en segmentos; sesiones cerradas después del ensayo. Se verificó importación de la aplicación sin cargar sklearn. Sin migraciones ni publicación de artefactos en Railway.

Documentación única ML actualizada en `ANALITICA_MACHINE_LEARNING.md`. Siguiente bloque: dashboard web/móvil; siguen pendientes exportaciones y trazabilidad ICONIX de las pantallas finales.


### ML — bloque 5, dashboard web y móvil (2026-11-08)

Se implementó la ruta Angular diferida `/analitica`, accesible en Reportes, y la pantalla Flutter Analítica inteligente desde la barra de Reportes. Ambas consumen la API ML con origen OFICIAL inicial, estado de historial insuficiente y cambio explícito a DEMOSTRACION sintética.

Incluyen métricas supervisadas, gráfico de unidades observadas/predichas/referencia, valores alternativos accesibles, tabla y filtros por sucursal/producto/fechas; segmentación solo para administrador con barras por grupo, métricas internas, perfiles y detalle RFM. La paginación conserva filtros aplicados. Se aclara que la predicción publicada es evaluación histórica y que silhouette/ARI no son exactitud comercial. No se mezclan datos sintéticos con oficiales.

Servicios/modelos cliente nuevos, manejo de carga/errores/reintentos, descarte de respuestas tardías y limpieza de resultados al cambiar cuenta/empresa. Web cancela solicitudes y Flutter verifica generación/revisión. No se agregaron dependencias gráficas ni persistencia offline para analítica.

Validación: 39 pruebas web, 94 Flutter, análisis Flutter sin observaciones, compilación web local y 2 recorridos Edge/AXE aprobados con administrador y encargado locales. Se probaron anchos de 320 px (Flutter) y 390 px (web), se inspeccionaron capturas y se verificó el alcance por sucursal. En Node 25 se ejecutaron las pruebas web con NODE_OPTIONS=--no-experimental-webstorage para usar localStorage de jsdom.

No se modificaron datos oficiales, no hay migraciones nuevas y no se desplegó en Railway. Sigue pendiente el cierre de exportaciones ML, reportes inteligentes e ICONIX. Se actualizó `ANALITICA_MACHINE_LEARNING.md` con acceso, alcance, comandos y limitaciones.

APK del bloque 5 generado correctamente (70,8 MB) en `tr-mobile/build/app/outputs/flutter-apk/app-release.apk`, con Firebase local y API_URL=http://127.0.0.1:8000. No instalado ni publicado. Hash/tamaño guardados en `.private/analitica_apk_manifest.json`. Validación final: 39 pruebas web, 94 Flutter, 2 Edge/AXE, compilación web y análisis Flutter correctos.


### ML — bloque 6, exportaciones y solicitudes de reportes (2026-11-08)

Se integraron PDF y Excel en web/Flutter sobre las consultas ML existentes, con exportación del filtro completo hasta 5.000 filas (422 si supera, sin truncar). Los archivos incluyen origen, empresa, estado, fuente/fecha de auditoría, modelo/dataset, filtros, métricas y gráficos. PDF con origen en todas las páginas, cabeceras repetidas y detalle separado; XLSX con hojas de resumen/métricas/datos/gráfico, números conservados y protección contra fórmulas en textos. OFICIAL insuficiente produce un reporte de disponibilidad, sin sustituir resultados por demostración.

Se agregó solicitud por texto y dictado con vista previa y aplicación explícita, identificada como interpretación local por reglas (no LLM). Admite análisis registrados, origen, sucursal autorizada, producto, cortes ISO y grupo. Pide aclaración ante ambigüedad o pronósticos vigentes no disponibles; no genera SQL ni entrena. Reutiliza permisos de CU22 y funciones del plan para interpretación, voz y formatos. Controla contexto y respuestas tardías; el encargado no accede a segmentos ni otras sucursales.

Validación: 56 pruebas backend, 44 web, 98 Flutter, análisis Flutter y compilación web correctos; 2 recorridos Edge/AXE con descargas reales y aplicación de propuesta. PDF filtrado por Banzer con 108 filas, Excel con los 149 perfiles y gráficos; muestras completas de 216 filas/149 perfiles verificadas mediante pypdf/openpyxl y render visual de resumen/detalle. API sin cambios a tablas ni datos oficiales. APK local actualizado (70,8 MB), sin instalar ni publicar en Railway.

Se actualizó `ANALITICA_MACHINE_LEARNING.md` con comandos, alcance, limitaciones, ficha/robustez/secuencia de la extensión CU22, trazabilidad requisito→componente→prueba y recorrido manual. Pendientes: prueba física de dictado/apertura en Android, publicación autorizada de código/artefactos y traslado al informe/UML global. El bloque funcional de analítica está integrado localmente.


### ML — prueba física Android aprobada (2026-11-08)

A solicitud del usuario se probó el APK actualizado en el Redmi conectado por USB (Android 13 / SDK 33), conservando datos y usando exclusivamente la API local por adb reverse tcp:8000. Railway quedó sin cambios y no se utilizó su conexión en la prueba.

MIUI bloqueó inyección de toques; se continuó mediante navegación asistida del usuario, sin alterar ajustes de seguridad. Se verificaron instalación, catálogo local, ingreso como administrador y acceso a analítica; se observó OFICIAL con historial insuficiente y DEMOSTRACION separado.

Confirmado por el usuario en el teléfono: dictado transcrito, vista previa SEGMENTOS/DEMOSTRACION, aplicación a grupos, PDF abierto con grupos, Excel abierto correctamente y métricas/gráfico de tres líneas al volver a predicciones. Resultado físico aprobado sin errores funcionales reportados. El rol encargado mantiene sus verificaciones automatizadas; no se afirma prueba física de ese rol.

Registro privado en `.private/analitica/android_qa.json`; documentación ML actualizada. Se retiró el XML de inspección y se conservaron la sesión manual, los reportes descargados y el enlace local para seguir probando. No se publicó en Railway. Pendientes aparte: publicación autorizada y adaptación del informe/modelo UML global para entrega académica.


### ML — interfaz móvil más legible (2026-11-08)

Se reemplazaron los accesos de iconos en Reportes por botones con texto: Analítica inteligente, Reporte por voz y Actualizar. La cabecera y las pestañas usan fondo claro con texto oscuro. Se añadieron espacios entre filtros y secciones, y las explicaciones de auditoría/modelo quedaron desplegables.

Los grupos muestran cantidad, porcentaje de clientes y barras anchas con colores identificables. Los promedios de última compra, frecuencia y gasto se presentan en filas independientes. Silhouette y ARI conservan su significado y se muestran bajo «Ver evaluación del modelo».

El gráfico semanal muestra ventas observadas y predicción; la referencia de semana anterior se activa con un interruptor. La escala vertical se ajusta al rango de valores y sus límites se indican expresamente. Hay cuadrícula, fechas abreviadas, leyenda por serie y valores exactos desplegables. No cambia ningún cálculo ni consulta del backend.

La tabla inferior y su paginación aparecen únicamente al pulsar «Ver detalle del reporte»; se pueden volver a ocultar y se reinicia su visibilidad al aplicar otros filtros. Las exportaciones siguen usando el filtro completo.

Validación: 99 pruebas Flutter aprobadas y flutter analyze sin problemas; incluye pantalla de 320 px, tabla visible solo por petición y rangos del gráfico con valores constantes/cero. Railway no fue modificado. La revisión física previa corresponde al diseño anterior; este cambio no se declara confirmado por el usuario en el teléfono.

APK release actualizado (70.9 MB), instalado en el Redmi mediante `adb install -r` con resultado Success. Se abrió StyleAR y se mantuvo la conexión USB a la API local. No se borraron datos de la app. La comprobación visual del nuevo diseño en el teléfono queda para el usuario.


### Web — dashboard, analítica y delivery reorganizados (2026-11-08)

- Dashboard comercial: los primeros resultados son cuatro gráficos de barras (ventas por fecha, importe BOB por fecha, canales de venta y productos más vendidos), con valores exactos y escalas desde cero. Los filtros quedan desplegables y las métricas/tablas permanecen disponibles debajo. Los datos y permisos siguen siendo los del servicio comercial actual.
- Analítica: gráfico semanal con cuadrícula, escala ajustada y límites expresos; ventas observadas y predicción visibles por defecto, semana anterior opcional mediante casilla. Las series conservan colores y trazos distintos. Grupos con porcentajes y promedios RFM separados; tablas de detalle e identificación del modelo desplegables.
- Carrito/delivery: el botón abre una ventana superpuesta, fuera del resumen. Dirección, mapa y cotización usan tarjetas independientes; en escritorio tienen altura limitada y desplazamiento propio. En pantalla estrecha se apilan dentro de la ventana. El resumen conserva el destino/costo/aceptación y permite volver a editar. Se mantiene la invalidación al cambiar destino o sucursal.
- El mapa se libera al cerrar y reconstruye al reabrir, conservando ubicación y ruta cotizada. Un error de cotización ya no elimina el formulario ni cierra la ventana. La aceptación solo aparece con una cotización disponible. El modal mantiene foco, Escape y retorno al botón; bloquea el desplazamiento del fondo y aparece sobre el asistente flotante. El ancho de los demás modales conserva su valor predeterminado.

Validación: compilación de producción correcta; 46 pruebas unitarias aprobadas. Se ejecutaron cuatro recorridos Edge (administrador/encargado en analítica, dashboard y delivery) con verificaciones AXE sin infracciones en los ámbitos probados. Se inspeccionaron capturas de escritorio y de 390 px. La prueba de interfaz de delivery usa carrito y respuesta de cotización simulados en el navegador: no registra compras ni pagos, ni verifica nuevamente el proveedor ORS. Se comprobó conservar dirección/aceptación al reabrir, Escape y retorno del foco. Los tests de analítica verificaron además exportaciones PDF y Excel locales. No se modificaron Railway, modelos ML ni esquema de BD.

El ejecutor Node 25 necesita NODE_OPTIONS=--no-experimental-webstorage para que las pruebas utilicen localStorage de jsdom; con ese ajuste pasan las 46 pruebas. No se alteró la lógica de sesiones para resolver esa diferencia del entorno de pruebas.


### Web — gráficos rellenos y con volumen (2026-11-08)

Por preferencia del usuario se retiraron las series de contorno del gráfico de analítica. Cada serie se presenta como un área con degradado relleno en su propia tarjeta, usando la misma escala vertical explícita para facilitar comparación y evitar superposiciones. La semana anterior sigue siendo opcional y los valores exactos permanecen desplegables.

El dashboard usa columnas con degradados, iluminación interior, sombra y etiquetas de valor. Los productos más vendidos tienen un ranking con barras anchas, textura segmentada y colores alternos. Los grupos de clientes adoptan barras rellenas con degradado y tarjetas para sus métricas. Los porcentajes, importes, consultas y cálculos no se alteraron.

Verificación: compilación de producción correcta, 46 pruebas unitarias aprobadas, dos recorridos Edge de analítica/dashboard aprobados con AXE sin infracciones, e inspección visual de las capturas actualizadas. Se conserva la respuesta adaptable a pantalla estrecha. Railway no fue modificado.


### 2026-11-09 — Vestidor: identidad, privacidad y guardado explícito

Corrección local en stylear-si2 según diagnóstico de Soporte. IA restringida a clientes; sesiones vinculadas a usuario/empresa, límites y cierre por tiempo/revocación. Errores del proveedor sin URLs ni claves. Fotos en trabajo temporal privado; respuesta en memoria y guardado permanente Android solo al pulsar Guardar. /static/tryon bloqueado. 38 pruebas backend y 103 Flutter aprobadas, análisis correcto y APK debug compilado. Decart simulado, sin créditos ni fotos personales; validación física del calce/guardado pendiente. Firebase se mantiene pendiente. Detalle en CORRECCION_VESTIDOR_2026-11-09.md.

## Avisos detallados Android (09/11/2026)

FCM utiliza titulo y mensaje de la notificacion de la empresa correspondiente. Android en primer plano muestra el mismo contenido con texto expandible; se conserva la validacion de sesion y destino. Posventa y delivery muestran titulos por estado. Nueve pruebas backend aprobadas, flutter analyze limpio y APK actualizado en Redmi. Envio dirigido de ensayo aceptado por FCM; recepcion visual pendiente de confirmacion. Tareas automaticas locales siguen desactivadas.

## Carrito, foto y consulta offline (09/11/2026)

- Carrito: el formulario de reserva/pago inicia cerrado y se abre con Continuar; puede cerrarse con Volver a las prendas.
- Foto: timeout de 90 segundos incluye ahora carga y respuesta completa. Decart tiene un presupuesto de 55 segundos y, si falla, se utiliza el motor local existente. Se presenta el detalle controlado del error del backend. No se guardan fotos automaticamente.
- Consultas offline: instantaneas cifradas de rutas permitidas, separadas por API, empresa, usuario y roles. Solo se recuperan ante falta de conexion, nunca para sustituir 401/403 u otros rechazos HTTP. No se almacenan tokens en estas instantaneas. Solo datos previamente consultados; recursos remotos no descargados no estan garantizados.
- Acceso offline despues de reiniciar: tras desbloqueo del telefono se abre la navegacion habitual con datos disponibles. La vigencia sigue siendo de 24 horas. No se habilita acceso remoto con una identidad local; POST/PUT/PATCH/DELETE requieren conexion.
- Operaciones offline conserva su funcion como registro de solicitudes de reserva y ventas presenciales en efectivo. Las reservas pendientes se validan en el servidor al sincronizar; no garantizan stock antes de la confirmacion. No hay cola de cargos Stripe ni cobros automaticos al reconectar; se usa Checkout online, no Stripe Terminal.
- Verificacion: flutter analyze limpio, 106 pruebas Flutter y 34 pruebas del vestidor aprobadas. Probador real y navegacion offline pendientes de prueba manual en el Redmi con el APK actualizado.

## Ajuste responsive del carrito (09/11/2026)
Formulario dentro del espacio restante con desplazamiento propio, sin porcentaje de altura de pantalla. Al continuar se oculta la lista de prendas y se conserva Volver a las prendas. El total permite salto de linea. Prueba de widget aprobada a 360x640 con texto ampliado y barras superior e inferior.


## Web: usuarios por rol, empleados y bandeja (09/11/2026)

Usuarios abre un panel de tarjetas por rol, acceso a todas las cuentas y cuentas sin roles activos. La tabla y sus filtros aparecen al entrar. El filtro de rol se aplica en el backend antes de contar/paginar y conserva el alcance de empresa y permisos. Las cuentas con varios roles aparecen en cada grupo correspondiente. La vista SaaS conserva su selector de empresa.

Empleados abre tarjetas por tipo laboral (encargados, caja, ventas), equipo completo y sin roles activos. La busqueda se combina con el tipo elegido. Se conserva la gestion existente y el foco de teclado pasa al encabezado de la lista al entrar y vuelve al panel al regresar.

Notificaciones incorpora cabecera, filtros en tarjeta, botones con iconos, indicadores de lectura, colores por categoria y preferencias organizadas. Se mantienen los endpoints y acciones existentes. Diseno adaptable a ventanas estrechas.

Validacion: compilacion web aprobada; cuatro pruebas existentes de notificaciones aprobadas; filtro CLIENTE comprobado contra la copia local (cinco cuentas, incluidos usuarios con varios roles). AXE sin infracciones WCAG A/AA en usuarios, empleados y notificaciones, y con avisos y preferencias a 1280 y 390 px; sin desbordamiento horizontal en esas vistas de notificaciones. Sin despliegue ni cambios en Presentacion.

## Integracion con main (09/11/2026)
Integradas las actualizaciones remotas de reservas, carrito y configuracion de pagos. Unicos conflictos: notificaciones web, resueltos conservando la ultima interfaz local de Anghy. Verificacion: 47 pruebas backend, cinco pruebas web y compilacion de produccion aprobadas. Las credenciales y la base local no forman parte de los commits.

