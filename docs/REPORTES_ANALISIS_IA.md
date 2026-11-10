# Análisis y voz de reportes

Los reportes conservan sus consultas controladas y autorizadas. La IA recibe únicamente los filtros, el resumen de la consulta completa y hasta 40 elementos de la serie; no recibe filas con datos personales ni ejecuta SQL.

En web y Flutter, **Analizar con IA** solicita `POST /api/v1/reportes/analizar`. El resultado incluye una explicación, hasta tres conclusiones, el gráfico propuesto y advertencias. El servidor valida el contrato, las referencias y las cifras y corrige gráficos incompatibles con los datos. Si el proveedor falla, se identifica expresamente el resumen alternativo como automático.

**Escuchar análisis** solicita `POST /api/v1/reportes/audio` con el UUID del análisis. No admite texto arbitrario enviado por el cliente. Gemini genera un WAV mono de 24 kHz; el usuario puede reproducir y pausar. No se llama al proveedor al generar el reporte, cambiar de página o reproducir otra vez el mismo audio disponible.

## Configuración

- Se reutilizan `AI_REPORTS_PROVIDER`, `GEMINI_API_KEY` y las capacidades del plan: `reportes_interpretacion` y `reportes_voz`.
- `GEMINI_REPORTS_ANALYSIS_MODEL` es opcional, por defecto `gemini-3.8-flash`. Es independiente del modelo usado para interpretar filtros; la prueba real encontró que `gemini-2.5-flash` ya rechazaba la generación con la cuenta actual.
- `GEMINI_TTS_MODEL` es opcional, por defecto `gemini-2.5-flash-preview-tts`.
- Vertex utiliza su configuración existente para generar el análisis; la voz necesita la clave de Gemini.

No se incorporan claves a la web ni a Flutter. Las llamadas tienen tiempo de espera y no realizan reintentos automáticos que multipliquen el consumo.

## Reutilización y exportación

La caché temporal se limita por cuenta, empresa, versión de acceso, roles, sucursal y huella del resultado. Se conserva durante 20 minutos en el proceso; un reinicio o cambio de réplica puede requerir generar otro análisis. Los audios también tienen un límite de cantidad y duración. No se guardan en rutas públicas.

PDF y Excel pueden incluir el análisis seleccionado mediante `analisis_id`. El servidor comprueba el alcance, los filtros y la firma del resultado exportado: si cambiaron los datos, omite el análisis anterior e informa por qué. El UUID no se imprime en el reporte.

La APK debe recompilarse para incorporar los controles nuevos y el reproductor `audioplayers`. Compilar no despliega los endpoints: backend y web deben desplegarse para probar el flujo completo.
