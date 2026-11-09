from fastapi import HTTPException

from app.core.contexto_empresa import exigir_empresa_id
from app.modules.ventas_inventario.repositories.nota_venta_repository import obtener_nota
from app.modules.ventas_inventario.services.inventario_service import obtener_venta_service
from .comprobante_pago_service import datos_nota
from .comprobante_pdf import DIRECTORIO_LOGOS, generar_comprobante_pdf


def preparar_nota(usuario, venta_id):
    obtener_venta_service(usuario,venta_id)  # Empresa + sucursal antes de emitir/leer.
    try:
        return obtener_nota(venta_id,exigir_empresa_id(usuario))
    except ValueError as error:
        raise HTTPException(409,str(error)) from error


def generar_pdf(nota):
    return generar_comprobante_pdf(datos_nota(nota), directorio_logos=DIRECTORIO_LOGOS)


def enviar_nota(usuario, venta_id):
    from app.core.email import enviar_correo_con_adjunto, usa_destino_fijo
    nota=preparar_nota(usuario,venta_id)
    datos=nota['datos']
    if not datos.get('correo') and not usa_destino_fijo():
        raise HTTPException(409,'La venta no tiene un cliente con correo registrado.')
    try:
        enviar_correo_con_adjunto(datos.get('correo'),f'Nota de venta {nota["numero"]} — {datos["empresa"]}',
            f'Adjuntamos su nota de venta de {datos["empresa"]}, sucursal {datos["sucursal"]}.',
            generar_pdf(nota),f'nota-venta-{nota["numero"]:08d}.pdf',datos['empresa'])
    except (ValueError,OSError) as error:
        raise HTTPException(503,'No se pudo enviar el correo. La venta sigue registrada y el PDF está disponible.') from error
    return {'mensaje':('Nota de venta enviada al correo de recepción configurado.'
                      if usa_destino_fijo() else 'Nota de venta enviada al correo registrado del cliente.')}
