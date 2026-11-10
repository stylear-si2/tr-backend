"""Comprobantes que se encolan en la misma transacción que el pago confirmado."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from psycopg2.extras import Json
from reportlab.platypus.doctemplate import LayoutError

from app.core import email
from app.modules.autenticacion.repositories.perfil_repository import _transaccion
from app.modules.ventas_inventario.repositories.nota_venta_repository import emitir_nota_cursor
from .comprobante_pdf import generar_comprobante_pdf


def _prenda(item):
    return ' · '.join(str(item[campo]) for campo in ('producto', 'talla', 'color') if item.get(campo))


def _metodo(metodo, proveedor=None):
    if proveedor == 'STRIPE' or metodo == 'STRIPE':
        return 'Tarjeta · Stripe'
    if proveedor == 'QR_BCP':
        return 'QR BCP'
    return {
        'EFECTIVO': 'Efectivo', 'TARJETA': 'Tarjeta', 'QR': 'QR', 'TRANSFERENCIA': 'Transferencia',
    }.get(metodo, metodo)


def datos_nota(nota):
    datos = nota['datos']
    config = datos.get('comprobante_config') or {}
    lineas = []
    for item in datos['detalles']:
        concepto = _prenda(item) + f" · Bs {Decimal(item['precio_unitario']):.2f} por unidad"
        if item.get('paquete_origen_id'):
            concepto += f" · Paquete #{item['paquete_origen_id']} ({item.get('descuento_mayorista') or 0}% mayorista)"
        if Decimal(item['descuento']):
            concepto += f" · Descuento Bs {Decimal(item['descuento']):.2f}"
        lineas.append((concepto, item['cantidad'], item['subtotal']))
    importes = [('Subtotal de prendas', datos['subtotal'])]
    if Decimal(datos['descuento']):
        importes.append(('Descuento / anticipo aplicado', datos['descuento']))
    if datos.get('tipo_entrega') == 'DELIVERY':
        importes.append(('Envío a domicilio', datos.get('costo_delivery') or 0))
    referencias = [('Venta', datos['codigo'])]
    if datos.get('vendedor'):
        referencias.append(('Atendido por', datos['vendedor']))
    return {
        'titulo': 'Nota de venta', 'referencia': f"N.º {nota['numero']:08d}",
        'empresa': datos['empresa'], 'sucursal': datos['sucursal'],
        'direccion': datos.get('direccion'), 'telefono': datos.get('telefono'),
        'fecha': datos['fecha_venta'], 'logo': config.get('logo_archivo'),
        'cliente': datos.get('cliente') or 'Consumidor final', 'moneda': 'BOB',
        'metodo': _metodo(datos.get('metodo_pago')), 'total': datos['total'],
        'lineas': lineas, 'importes': importes, 'referencias': referencias,
        'condiciones': config.get('condiciones') or 'Conserve este comprobante para consultar las condiciones de cambios.',
    }


def encolar(cur, empresa, clave, destinatario, datos):
    if Decimal(str(datos['total'])) <= 0:
        return
    cur.execute('''
        INSERT INTO comprobante_pago_envio(empresa_id,clave,destinatario,datos)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT(empresa_id,clave) DO NOTHING
    ''', (empresa, clave, destinatario, Json(datos, dumps=lambda valor: json.dumps(valor, default=str))))


def encolar_venta(cur, empresa, venta_id, *, nota=None, orden=None, reserva_id=None):
    nota = nota or emitir_nota_cursor(cur, venta_id, empresa)
    datos = datos_nota(nota)
    if reserva_id is not None:
        cur.execute('SELECT codigo FROM reserva WHERE id=%s AND empresa_id=%s', (reserva_id, empresa))
        datos['referencias'].append(('Reserva', cur.fetchone()['codigo']))
        datos['titulo'] = 'Pago final de reserva'
    if orden is not None:
        datos['referencias'].append(('Orden de pago', orden['id']))
        datos['fecha'] = orden.get('fecha_pago') or datos['fecha']
        datos['metodo'] = _metodo(orden['metodo'], orden.get('proveedor'))
        datos['total'] = orden['monto_total']
        datos['moneda'] = orden['moneda']
    encolar(cur, empresa, f'venta:{venta_id}', nota['datos'].get('correo'), datos)


def encolar_anticipo(cur, orden):
    empresa = orden['empresa_id']
    cur.execute('''
        SELECT e.nombre AS empresa, e.comprobante_config, s.nombre AS sucursal,
               r.codigo, r.total, u.correo, concat_ws(' ',u.nombre,u.apellido) AS cliente
        FROM reserva r JOIN empresa e ON e.id=r.empresa_id
        JOIN sucursal s ON s.id=r.sucursal_id AND s.empresa_id=e.id
        JOIN cliente c ON c.id=r.cliente_id AND c.empresa_id=e.id
        JOIN usuario u ON u.id=c.usuario_id AND u.empresa_id=e.id
        WHERE r.id=%s AND r.empresa_id=%s
    ''', (orden['reserva_id'], empresa))
    reserva = cur.fetchone()
    datos = {
        'titulo': 'Anticipo de reserva', 'referencia': reserva['codigo'],
        'empresa': reserva['empresa'], 'sucursal': reserva['sucursal'],
        'logo': (reserva.get('comprobante_config') or {}).get('logo_archivo'),
        'cliente': reserva['cliente'], 'fecha': orden['fecha_pago'], 'moneda': orden['moneda'],
        'metodo': _metodo(orden['metodo'], orden.get('proveedor')), 'total': orden['monto_total'],
        'lineas': [('Anticipo aplicado a la reserva', 1, orden['monto_total'])],
        'referencias': [('Orden de pago', orden['id'])],
        'importes': [('Total de la reserva', reserva['total'])],
    }
    encolar(cur, empresa, f"orden:{orden['id']}", reserva['correo'], datos)


def encolar_cambio(cur, empresa, orden, cambio):
    cur.execute('''
        SELECT e.nombre AS empresa, e.comprobante_config, s.nombre AS sucursal,
               u.correo, concat_ws(' ',u.nombre,u.apellido) AS cliente
        FROM devolucion d JOIN empresa e ON e.id=d.empresa_id
        JOIN sucursal s ON s.id=d.sucursal_id AND s.empresa_id=e.id
        JOIN cliente c ON c.id=d.cliente_id AND c.empresa_id=e.id
        JOIN usuario u ON u.id=c.usuario_id AND u.empresa_id=e.id
        WHERE d.id=%s AND d.empresa_id=%s
    ''', (cambio['id'], empresa))
    origen = cur.fetchone()
    cantidad = cambio['cantidad']
    lineas = [
        (f'Prenda devuelta · {_prenda(item)}', cantidad, None)
        for item in cambio['original']['detalles']
    ]
    lineas.append((f"Reemplazo · {_prenda(cambio['reemplazo'])}", cantidad, cambio['precio_reemplazo'] * cantidad))
    if cambio.get('adicional'):
        lineas.append((f"Prenda adicional · {_prenda(cambio['adicional'])}", 1, cambio['precio_adicional']))
    datos = {
        'titulo': 'Pago de diferencia por cambio', 'referencia': f"CAM-{cambio['id']}",
        'empresa': origen['empresa'], 'sucursal': origen['sucursal'], 'cliente': origen['cliente'],
        'logo': (origen.get('comprobante_config') or {}).get('logo_archivo'),
        'fecha': orden['fecha_pago'], 'moneda': orden['moneda'], 'total': orden['monto_total'],
        'metodo': _metodo(orden['metodo'], orden['proveedor']),
        'referencias': [('Venta original', cambio['original']['venta_codigo']), ('Orden de pago', orden['id'])],
        'lineas': lineas, 'importes': [('Crédito aplicado', cambio['credito'])],
    }
    encolar(cur, empresa, f"orden:{orden['id']}", origen['correo'], datos)


def encolar_reembolso(cur, empresa, devolucion_id):
    """Fotografía del reintegro confirmado; una sola entrega por devolución."""
    from ..repositories.devolucion_repository import _obtener_devolucion_cursor
    devolucion = _obtener_devolucion_cursor(cur, devolucion_id, empresa)
    if not devolucion or devolucion['estado'] != 'REEMBOLSADA':
        return
    cur.execute('SELECT nombre,comprobante_config FROM empresa WHERE id=%s', (empresa,))
    negocio = cur.fetchone()
    importe = devolucion['importe_reintegro']
    if importe is None:
        importe = devolucion['monto_aprobado']
    datos = {
        'titulo': 'Comprobante de reembolso', 'referencia': devolucion['codigo'],
        'empresa': negocio['nombre'], 'sucursal': devolucion['sucursal'],
        'logo': (negocio.get('comprobante_config') or {}).get('logo_archivo'),
        'cliente': devolucion.get('cliente_nombre'), 'fecha': devolucion['fecha_reembolso'],
        'moneda': devolucion.get('moneda') or 'BOB', 'total': importe,
        'metodo': _metodo(devolucion['metodo_reembolso']),
        'etiqueta_metodo': 'Método de reintegro', 'etiqueta_total': 'Total reembolsado',
        'mensaje_correo': 'Adjuntamos el comprobante de tu reembolso confirmado en StyleAR.',
        'referencias': [('Venta original', devolucion['venta_codigo']),
                        ('Referencia del reintegro', devolucion.get('proveedor_refund_id')
                         or devolucion.get('referencia_reembolso') or devolucion['codigo'])],
        'lineas': [(f"Prenda devuelta · {_prenda(item)}", item['cantidad_aceptada'],
                    item['monto_aprobado'] if devolucion['tipo_operacion'] != 'CAMBIO' else None)
                   for item in devolucion['detalles']
                   if item['cantidad_aceptada'] > 0],
        'condiciones': 'Este documento acredita el reintegro; no representa una nueva compra.',
    }
    if devolucion['tipo_operacion'] == 'CAMBIO':
        datos['condiciones'] = 'Reintegro de la diferencia a favor del cliente por cambio de prendas.'
    encolar(cur, empresa, f'reembolso-devolucion:{devolucion_id}',
            devolucion.get('cliente_correo'), datos)


def procesar_envios(limite=10):
    """Entrega por Resend con una clave estable y el mismo adjunto en cada intento."""
    if email.MAIL_PROVIDER != 'resend' or not all((email.RESEND_API_KEY, email.RESEND_FROM, email.MAIL_TO_OVERRIDE)):
        return 0
    enviados = 0
    for _ in range(limite):
        with _transaccion() as cur:
            cur.execute('''
                SELECT * FROM comprobante_pago_envio
                WHERE estado='PENDIENTE' AND proximo_intento<=CURRENT_TIMESTAMP
                ORDER BY proximo_intento,id LIMIT 1 FOR UPDATE SKIP LOCKED
            ''')
            fila = cur.fetchone()
            if not fila:
                break
            id_envio = fila['id']
            ahora = datetime.now(timezone.utc)
            primero = fila['primer_intento'] or ahora
            if fila['intentos'] >= 8 or ahora - primero >= timedelta(hours=23):
                cur.execute("UPDATE comprobante_pago_envio SET estado='FALLIDO' WHERE id=%s", (id_envio,))
                continue
            intento = fila['intentos'] + 1
            datos = fila['datos']
            try:
                pdf = bytes(fila['pdf']) if fila['pdf'] is not None else generar_comprobante_pdf(datos)
            except (ValueError, KeyError, TypeError, OSError, LayoutError):
                cur.execute("UPDATE comprobante_pago_envio SET estado='FALLIDO' WHERE id=%s", (id_envio,))
                continue
            cur.execute('''
                UPDATE comprobante_pago_envio SET pdf=%s, intentos=%s, primer_intento=%s,
                    proximo_intento=%s WHERE id=%s
            ''', (pdf, intento, primero, ahora + timedelta(minutes=2), id_envio))
        try:
            email.enviar_correo_con_adjunto(
                fila['destinatario'], f"{datos['titulo']} {datos['referencia']} - {datos['empresa']}",
                datos.get('mensaje_correo', 'Adjuntamos el comprobante de tu pago confirmado en StyleAR.'),
                pdf, f'comprobante-stylear-{id_envio}.pdf', datos.get('emisor', datos['empresa']),
                clave_idempotencia=f"stylear/comprobante/{fila['clave_resend']}",
            )
        except (OSError, ValueError):
            with _transaccion() as cur:
                cur.execute('''
                    UPDATE comprobante_pago_envio SET proximo_intento=CURRENT_TIMESTAMP + %s
                    WHERE id=%s AND intentos=%s AND estado='PENDIENTE'
                ''', (timedelta(minutes=min(2 ** intento, 120)), id_envio, intento))
        else:
            with _transaccion() as cur:
                cur.execute('''
                    UPDATE comprobante_pago_envio SET estado='ENVIADO',enviado_en=CURRENT_TIMESTAMP,pdf=NULL
                    WHERE id=%s AND estado='PENDIENTE'
                ''', (id_envio,))
                enviados += cur.rowcount
    return enviados
