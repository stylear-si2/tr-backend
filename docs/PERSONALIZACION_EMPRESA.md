# Personalización web por empresa

Acceso: Administración → Personalizar tienda (`/personalizar-tienda`). Solo el administrador de la empresa puede editar; el diseño publicado es público para sus visitantes y clientes.

Se configura nombre comercial de la web, colores principal y secundario, logo, portada, título/mensaje de bienvenida, teléfono, WhatsApp, dirección, horarios y enlaces de Instagram/Facebook/sitio web. No modifica el nombre legal, el código de empresa ni la configuración de comprobantes. Flutter no consume ni modifica esta configuración.

La vista previa usa el borrador sin cambiar la tienda publicada. Guardar publica texto e imágenes en una sola transacción; un archivo inválido deja todo sin cambios. Las ediciones simultáneas se detectan por versión y requieren recargar. Restablecer recupera colores base y quita imágenes, conservando los textos y el contacto.

Los colores admitidos tienen contraste mínimo 4.5:1 con blanco. Solo se admiten enlaces HTTPS. Las imágenes se validan por contenido como PNG/JPEG/WebP estáticos, hasta 5 MB y 16 millones de píxeles; se redimensionan y recodifican a WebP sin metadatos. La base de datos conserva las imágenes, por lo que no dependen del sistema de archivos efímero ni requieren nuevas claves o permisos de R2. La tabla tiene empresa_id y forma parte del respaldo por empresa mediante la detección de esquema existente.

Al desplegar backend se debe ejecutar `python init_db.py`, que aplica la migración versionada `2026_11_10_empresa_apariencia`. El backend comprueba esa versión al iniciar. No ejecutar la migración manualmente en producción además del paso de despliegue existente. Publicar también la web para disponer del formulario.

Lectura pública: `/api/v1/empresas/publica/{codigo}/apariencia` e `/imagen/{logo|portada}`. La empresa cancelada no se publica. Administración: `/api/v1/mi-empresa/apariencia`, `/publicar` y `/restablecer`; la empresa se obtiene de la sesión autenticada, nunca del formulario. Al cambiar de empresa se limpian los datos en memoria y se descartan respuestas anteriores.
