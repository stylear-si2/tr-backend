"""CU16, bloque 1. Contratos de pruebas por comercio; no admite cobros reales."""
from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID
import hashlib
import hmac
import json
import os

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.core.contexto_empresa import exigir_empresa_id
from app.core.plan_empresa import exigir_operaciones_empresa
from app.database.connection import get_connection
from app.modules.autenticacion.repositories.perfil_repository import _transaccion
from app.modules.administracion.repositories.control_comercial import preparar_escritura
from app.modules.autenticacion.repositories.administracion_acceso_repository import validar_actor
from app.modules.ventas_inventario.repositories import pago_repository as repo
from app.modules.ventas_inventario.repositories.inventario_repository import _aplicar_movimiento


class ConfiguracionPago(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stripe_habilitado: bool = False
    qr_habilitado: bool = False
    titular: str = Field(default='',max_length=180)
    version: int = Field(default=0,ge=0)


class EventoPrueba(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evento_id: str = Field(min_length=1,max_length=200)
    orden_id: int = Field(gt=0)
    empresa_id: int = Field(gt=0)
    proveedor: str = Field(pattern='^(STRIPE|QR_BCP)$')
    session_id: str = Field(min_length=1,max_length=255)
    pago_id: str = Field(min_length=1,max_length=255)
    monto_centavos: int = Field(gt=0,strict=True)
    moneda: str = Field(pattern='^BOB$')
    tipo: str = Field(pattern='^(PAGADO|RECHAZADO)$')


def habilitado_pruebas():
    return os.getenv('STYLEAR_PAGOS_PRUEBA','0') == '1'


def secreto(empresa, nombre):
    # Cada secreto tiene un nombre derivado por el servidor, nunca elegido por el cliente.
    especifico = os.getenv(f'STYLEAR_PAGOS_{empresa}_{nombre}', '').strip()
    if nombre == 'STRIPE_SECRET_KEY' and not especifico:
        compartido = os.getenv('STRIPE_SECRET_KEY', '').strip()
        if compartido.startswith('sk_test_') and not compartido.startswith('sk_test_placeholder'):
            return compartido
    return especifico


def usa_stripe_compartido(empresa):
    return (
        not os.getenv(f'STYLEAR_PAGOS_{empresa}_STRIPE_SECRET_KEY', '').strip()
        and bool(secreto(empresa, 'STRIPE_SECRET_KEY'))
    )


def configuracion(empresa):
    with _transaccion() as cur:
        cur.execute('SELECT * FROM configuracion_pago_empresa WHERE empresa_id=%s',(empresa,))
        fila = cur.fetchone()
    return dict(fila) if fila else dict(empresa_id=empresa,modo='PRUEBA',stripe_habilitado=False,qr_habilitado=False,titular='',banco='BCP',version=0)


def configuracion_publica(empresa):
    cfg = configuracion(empresa)
    return {**cfg,'confirmacion_prueba':habilitado_pruebas(),
            'stripe_conexion':'STRIPE_TEST' if secreto(empresa,'STRIPE_SECRET_KEY').startswith('sk_test_') else 'SIMULADO',
            'qr_conexion':'SIMULADO_BCP_PENDIENTE_DE_INTEGRACION',
            'stripe_webhook_configurado':bool(
                os.getenv('STRIPE_WEBHOOK_SECRET', '').strip()
                if usa_stripe_compartido(empresa) else secreto(empresa, 'STRIPE_WEBHOOK_SECRET')
            ),
            'eventos_prueba_configurados':bool(secreto(empresa,'EVENTOS_PRUEBA_SECRET'))}


def guardar_configuracion(actor, datos):
    empresa = exigir_empresa_id(actor)
    with _transaccion() as cur:
        preparar_escritura(cur,actor['id'],empresa,None,nueva=False)
        validar_actor(cur,actor)
        cur.execute('SELECT version FROM configuracion_pago_empresa WHERE empresa_id=%s FOR UPDATE',(empresa,))
        actual = cur.fetchone()
        version = actual['version'] if actual else 0
        if version != datos.version:
            raise HTTPException(409,'La configuración cambió. Vuelva a cargarla antes de guardar.')
        cur.execute('''INSERT INTO configuracion_pago_empresa(empresa_id,stripe_habilitado,qr_habilitado,titular,version)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(empresa_id) DO UPDATE SET
            stripe_habilitado=EXCLUDED.stripe_habilitado,qr_habilitado=EXCLUDED.qr_habilitado,
            titular=EXCLUDED.titular,version=EXCLUDED.version,actualizado_en=CURRENT_TIMESTAMP''',
            (empresa,datos.stripe_habilitado,datos.qr_habilitado,datos.titular.strip(),version+1))
        cur.execute("INSERT INTO bitacora(usuario_id,empresa_id,accion,modulo,descripcion) VALUES(%s,%s,'CONFIGURAR','PAGOS','Configuración de pruebas del comercio actualizada.')",(actor['id'],empresa))
    return configuracion_publica(empresa)


@contextmanager
def bloqueo_checkout(empresa, cliente):
    conn = get_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',(f'checkout:{empresa}:{cliente}',))
        yield
    finally:
        # El cierre libera también el bloqueo si una conexión interrumpida impide desbloquear.
        conn.close()


def _propia(actor, orden):
    if not orden or orden['empresa_id'] != exigir_empresa_id(actor):
        raise HTTPException(404,'Pago no encontrado.')
    if 'ADMINISTRADOR' in actor.get('roles',[]):
        return
    with _transaccion() as cur:
        cur.execute('SELECT usuario_id FROM cliente WHERE id=%s AND empresa_id=%s',(orden['cliente_id'],orden['empresa_id']))
        cliente = cur.fetchone()
    if not cliente or cliente['usuario_id'] != actor['id']:
        raise HTTPException(404,'Pago no encontrado.')


def crear_checkout(actor, request, proveedor):
    from .delivery_service import preparar_delivery_para_checkout
    empresa = exigir_empresa_id(actor)
    with _transaccion() as cur:
        cur.execute('SELECT id FROM cliente WHERE usuario_id=%s AND empresa_id=%s AND activo',(actor['id'],empresa))
        fila = cur.fetchone()
    if not fila or 'CLIENTE' not in actor.get('roles',[]):
        raise HTTPException(403,'Se requiere una cuenta de cliente para comprar.')
    cliente = fila['id']
    datos = request.model_dump(mode='json')
    if datos.get('items_offline') is None: datos.pop('items_offline',None)
    elif request.total_esperado is None or request.tipo_entrega != 'RECOJO_SUCURSAL':
        raise HTTPException(422,'La compra offline requiere aceptar el total actualizado y recoger en sucursal.')
    huella = hashlib.sha256(json.dumps({k:v for k,v in datos.items() if k!='clave_operacion'},sort_keys=True).encode()).hexdigest()
    with bloqueo_checkout(empresa,cliente):
        with _transaccion() as cur:
            cur.execute('SELECT * FROM orden_pago WHERE empresa_id=%s AND cliente_id=%s AND clave_operacion=%s',(empresa,cliente,str(request.clave_operacion)))
            previa = cur.fetchone()
        cfg = configuracion(empresa)
        if previa:
            if previa['solicitud_huella'] != huella or previa['proveedor'] != proveedor:
                raise HTTPException(409,'La clave de operación ya corresponde a otra compra. Consulte el intento anterior.')
            orden = dict(previa)
        else:
            if not cfg['stripe_habilitado' if proveedor=='STRIPE' else 'qr_habilitado']:
                raise HTTPException(409,'La empresa no tiene habilitado este medio de pago de prueba.')
            if request.tipo_entrega not in ('RECOJO_SUCURSAL','DELIVERY'):
                raise HTTPException(422,'Tipo de entrega inválido.')
            delivery = preparar_delivery_para_checkout(request.delivery,actor) if request.tipo_entrega=='DELIVERY' else None
            try:
                orden = repo.crear_orden_desde_carrito(cliente,actor['id'],int(delivery['sucursal_id']) if delivery else request.sucursal_id,
                    delivery,metodo='TARJETA' if proveedor=='STRIPE' else 'QR',proveedor=proveedor,empresa_id=empresa,
                    clave_operacion=str(request.clave_operacion),solicitud_huella=huella,
                    configuracion_version=cfg['version'],titular_pago=cfg['titular'],total_esperado=request.total_esperado,version_actor=actor.get('version_acceso',0),
                    items_offline=[i.model_dump() for i in request.items_offline] if request.items_offline is not None else None)
            except ValueError as error:
                raise HTTPException(409,str(error)) from error
        if not orden.get('proveedor_session_id'):
            if proveedor=='STRIPE':
                sesion = crear_sesion_stripe(orden)
                orden = repo.actualizar_checkout_stripe(orden['id'],sesion['id'],sesion['url'],sesion.get('payment_intent'),empresa)
            else:
                # Es un identificador de prueba; deliberadamente no se presenta como QR bancario.
                payload = f"STYLEAR-PRUEBA-NO-PAGAR:{empresa}:{orden['id']}:{orden['monto_total']}"
                orden = repo.actualizar_orden_qr(orden['id'],payload,f"qr_prueba_{empresa}_{orden['id']}",empresa)
        base = dict(orden_id=orden['id'],venta_id=orden['venta_id'],estado=orden['estado'],modo_pago='PRUEBA',
                    confirmacion_prueba=habilitado_pruebas() and str(orden['proveedor_session_id']).startswith(('cs_simulada_','qr_prueba_')))
        if proveedor=='STRIPE':
            return {**base,'checkout_url':orden['checkout_url']}
        return {**base,'monto_total':orden['monto_total'],'moneda':orden['moneda'],'qr_payload':orden['checkout_url'],
                'alias':orden['titular_pago'] or 'Comercio de prueba','titular':orden['titular_pago'] or 'Comercio de prueba',
                'banco':'SIMULACIÓN · BCP no conectado','cuenta':'NO TRANSFERIR DINERO','glosa':f"PRUEBA-{orden['id']}",
                'vencimiento':'Sin validez bancaria'}


def crear_sesion_stripe(orden):
    empresa = orden['empresa_id']
    key = secreto(empresa,'STRIPE_SECRET_KEY')
    frontend = os.getenv('FRONTEND_URL','http://localhost:4200').rstrip('/')
    if not key:
        return {'id':f"cs_simulada_{empresa}_{orden['id']}",'url':f"{frontend}/pago/resultado?orden_id={orden['id']}",'payment_intent':None}
    if not key.startswith('sk_test_'):
        raise HTTPException(409,'Sólo se permiten credenciales Stripe de prueba para esta empresa.')
    import stripe
    try:
        sesion = stripe.checkout.Session.create(api_key=key,idempotency_key=f"empresa-{empresa}-orden-{orden['id']}",
            mode='payment',payment_method_types=['card'],client_reference_id=str(orden['id']),
            line_items=[{'quantity':1,'price_data':{'currency':'bob','unit_amount':int(Decimal(orden['monto_total'])*100),
                'product_data':{'name':f"Compra {orden['id']} · {orden['titular_pago'] or 'Comercio'}"}}}],
            success_url=f"{frontend}/pago/resultado?orden_id={orden['id']}",cancel_url=f"{frontend}/pago/cancelado?orden_id={orden['id']}",
            metadata={'empresa_id':str(empresa),'orden_id':str(orden['id'])},
            payment_intent_data={'metadata':{'empresa_id':str(empresa),'orden_id':str(orden['id'])}})
        return {'id':sesion.id,'url':sesion.url,'payment_intent':sesion.payment_intent}
    except Exception as error:
        raise HTTPException(503,'No se pudo obtener el checkout de Stripe. Reintente la misma operación; no cree otro pago.') from error


def notificar(cur, orden, clave, titulo, mensaje):
    from .notificaciones_service import emitir_cliente
    categoria='PAGO'
    tipo,recurso='ORDEN_PAGO',orden['id']
    if clave.startswith('compra:'):
        categoria='COMPRA'
    elif clave.startswith(('reembolso','reembolsado:')):
        categoria='REEMBOLSO'
    elif clave.startswith('delivery:'):
        categoria='ENTREGA'
        tipo,recurso='DELIVERY',int(clave.split(':')[1])
    return emitir_cliente(cur,orden['empresa_id'],orden['cliente_id'],clave,titulo,mensaje,
                          categoria,tipo,recurso,orden_id=orden['id'])


def solicitar_reembolso(cur, orden, motivo):
    cur.execute("UPDATE orden_pago SET estado='REEMBOLSO_PENDIENTE',motivo_incidencia=%s,pago_recibido=TRUE WHERE id=%s",(motivo,orden['id']))
    cur.execute("UPDATE venta SET estado='CANCELADA' WHERE id=%s AND empresa_id=%s",(orden['venta_id'],orden['empresa_id']))
    cur.execute('''INSERT INTO reembolso_pago(empresa_id,orden_id,monto,moneda,metodo,motivo)
        VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(orden_id) DO NOTHING''',
        (orden['empresa_id'],orden['id'],orden['monto_total'],orden['moneda'],orden['metodo'],motivo))
    notificar(cur,orden,f"reembolso-pendiente:{orden['id']}",'Reembolso pendiente',
        'Recibimos el pago, pero no se completó la compra. El reembolso todavía no está confirmado.')


def procesar_evento(evento):
    empresa = evento.empresa_id
    huella = hashlib.sha256(evento.model_dump_json().encode()).hexdigest()
    with _transaccion() as cur:
        cur.execute('SELECT id FROM empresa WHERE id=%s FOR UPDATE',(empresa,))
        cur.execute('SELECT * FROM orden_pago WHERE id=%s AND empresa_id=%s FOR UPDATE',(evento.orden_id,empresa))
        fila = cur.fetchone()
        if not fila:
            raise HTTPException(404,'Orden no encontrada.')
        orden = dict(fila)
        if (orden['modo_pago']!='PRUEBA' or orden['proveedor']!=evento.proveedor or
            orden['proveedor_session_id']!=evento.session_id or int(Decimal(orden['monto_total'])*100)!=evento.monto_centavos or orden['moneda']!=evento.moneda):
            raise HTTPException(409,'La confirmación no corresponde a la empresa, sesión, moneda o importe esperado.')
        if orden['proveedor']=='STRIPE' and orden.get('proveedor_payment_intent_id') and orden['proveedor_payment_intent_id'] != evento.pago_id:
            raise HTTPException(409,'La referencia de cobro Stripe no corresponde a la sesión esperada.')
        if orden['proveedor_pago_id'] and orden['proveedor_pago_id'] != evento.pago_id:
            raise HTTPException(409,'La orden ya tiene otra referencia de pago; requiere conciliación.')
        cur.execute('SELECT huella FROM evento_pago WHERE empresa_id=%s AND proveedor=%s AND evento_id=%s',(empresa,evento.proveedor,evento.evento_id))
        anterior = cur.fetchone()
        if anterior:
            if anterior['huella'] != huella:
                raise HTTPException(409,'El evento ya se recibió con otros datos.')
            return orden
        cur.execute('INSERT INTO evento_pago(empresa_id,orden_id,proveedor,evento_id,huella,tipo) VALUES(%s,%s,%s,%s,%s,%s)',
            (empresa,orden['id'],evento.proveedor,evento.evento_id,huella,evento.tipo))
        if orden.get('cambio_id'):
            from .cambio_service import evento_pago
            evento_pago(cur,orden,evento)
            cur.execute('SELECT * FROM orden_pago WHERE id=%s',(orden['id'],))
            return dict(cur.fetchone())
        if evento.tipo=='RECHAZADO':
            if not orden['pago_recibido'] and orden['estado']=='PENDIENTE':
                cur.execute("UPDATE orden_pago SET estado='RECHAZADO' WHERE id=%s",(orden['id'],))
                cur.execute("UPDATE venta SET estado='RECHAZADA' WHERE id=%s AND empresa_id=%s",(orden['venta_id'],empresa))
                notificar(cur,orden,f"rechazo:{orden['id']}",'Pago rechazado','El pago fue rechazado. No se completó la compra.')
        elif not orden['pago_recibido']:
            cur.execute('UPDATE orden_pago SET pago_recibido=TRUE,proveedor_pago_id=%s,fecha_pago=CURRENT_TIMESTAMP WHERE id=%s',(evento.pago_id,orden['id']))
            if orden['estado']!='PENDIENTE':
                solicitar_reembolso(cur,orden,'Pago recibido después de cerrar el intento de compra.')
            else:
                cur.execute('SAVEPOINT venta_pagada')
                try:
                    # Las operaciones nuevas requieren un comercio habilitado incluso si el callback es tardío.
                    exigir_operaciones_empresa(empresa,cur)
                    cur.execute('''SELECT c.id FROM cliente c JOIN usuario u ON u.id=c.usuario_id AND u.empresa_id=c.empresa_id
                        WHERE c.id=%s AND c.empresa_id=%s AND c.activo AND u.activo FOR SHARE OF c,u''',(orden['cliente_id'],empresa))
                    if not cur.fetchone():
                        raise ValueError('La cuenta del cliente ya no está disponible.')
                    cur.execute('SELECT sucursal_id,usuario_id FROM venta WHERE id=%s AND empresa_id=%s FOR UPDATE',(orden['venta_id'],empresa))
                    venta = cur.fetchone()
                    cur.execute('SELECT producto_variante_id,cantidad FROM venta_detalle WHERE venta_id=%s ORDER BY producto_variante_id,id',(orden['venta_id'],))
                    for detalle in cur.fetchall():
                        _aplicar_movimiento(cur,venta['usuario_id'],venta['sucursal_id'],detalle['producto_variante_id'],
                            'VENTA_DIGITAL',detalle['cantidad'],'Pago de prueba confirmado','VENTA',orden['venta_id'],empresa_id=empresa)
                    cur.execute("UPDATE venta SET estado='COMPLETADA',metodo_pago=%s WHERE id=%s AND empresa_id=%s",(orden['metodo'],orden['venta_id'],empresa))
                    cur.execute("UPDATE orden_pago SET estado='PAGADO' WHERE id=%s RETURNING fecha_pago",(orden['id'],))
                    orden['fecha_pago'] = cur.fetchone()['fecha_pago']
                    from .delivery_cu18_service import confirmar_cursor
                    confirmar_cursor(cur,orden)
                    # Retirar sólo las unidades que formaron esta compra, conservando adiciones posteriores.
                    cur.execute('SELECT carrito_item_origen_id,producto_variante_id,SUM(cantidad)::int cantidad FROM venta_detalle WHERE venta_id=%s GROUP BY carrito_item_origen_id,producto_variante_id',(orden['venta_id'],))
                    for detalle in cur.fetchall():
                        cur.execute('''UPDATE carrito_item SET cantidad=GREATEST(cantidad-%s,1),activo=(cantidad>%s),fecha_actualizacion=CURRENT_TIMESTAMP
                            WHERE carrito_id=%s AND activo AND ((%s IS NOT NULL AND id=%s) OR (%s IS NULL AND producto_variante_id=%s AND paquete_id IS NULL))''',
                            (detalle['cantidad'],detalle['cantidad'],orden['carrito_origen_id'],detalle['carrito_item_origen_id'],detalle['carrito_item_origen_id'],detalle['carrito_item_origen_id'],detalle['producto_variante_id']))
                    from .comprobante_pago_service import encolar_venta
                    encolar_venta(cur, empresa, orden['venta_id'], orden=orden)
                    notificar(cur,orden,f"compra:{orden['id']}",'Compra confirmada','Tu pago fue confirmado y la compra se completó.')
                except (ValueError,HTTPException) as error:
                    cur.execute('ROLLBACK TO SAVEPOINT venta_pagada')
                    solicitar_reembolso(cur,orden,'La compra no pudo completarse: '+str(getattr(error,'detail',error)))
        cur.execute("INSERT INTO bitacora(empresa_id,accion,modulo,descripcion) VALUES(%s,'EVENTO','PAGOS',%s)",
                    (empresa,f"Orden {orden['id']}: {evento.tipo}"))
        cur.execute('UPDATE orden_pago SET fecha_actualizacion=CURRENT_TIMESTAMP WHERE id=%s AND empresa_id=%s',(orden['id'],empresa))
        cur.execute('SELECT * FROM orden_pago WHERE id=%s AND empresa_id=%s',(orden['id'],empresa))
        return dict(cur.fetchone())


def simulacion_reserva_legada(orden):
    return (orden.get('modo_pago')=='LEGADO' and orden.get('concepto') in ('RESERVA_ANTICIPO','RESERVA_SALDO')
            and orden.get('proveedor')=='STRIPE' and str(orden.get('proveedor_session_id','')).startswith('cs_test_simulada_'))


def confirmar_simulacion(actor, oid, aprobar=True, proveedor=None):
    orden = repo.obtener_orden_pago(oid,exigir_empresa_id(actor))
    _propia(actor,orden)
    if habilitado_pruebas() and simulacion_reserva_legada(orden) and proveedor=='STRIPE':
        return repo.marcar_orden_pagada(oid,actor['id'],orden['empresa_id']) if aprobar else repo.marcar_orden_no_pagada(oid,'RECHAZADO','RECHAZADA',orden['empresa_id'])
    if not habilitado_pruebas() or orden['modo_pago']!='PRUEBA' or not str(orden['proveedor_session_id']).startswith(('cs_simulada_','qr_prueba_')):
        raise HTTPException(403,'La confirmación manual sólo está disponible para simulaciones habilitadas; no confirma pagos bancarios.')
    if proveedor and orden['proveedor']!=proveedor:
        raise HTTPException(409,'El medio de pago no corresponde a esta orden.')
    evento = EventoPrueba(evento_id=f"simular:{oid}:{aprobar}",orden_id=oid,empresa_id=orden['empresa_id'],proveedor=orden['proveedor'],
        session_id=orden['proveedor_session_id'],pago_id=f"pago_simulado_{orden['empresa_id']}_{oid}",
        monto_centavos=int(Decimal(orden['monto_total'])*100),moneda=orden['moneda'],tipo='PAGADO' if aprobar else 'RECHAZADO')
    return procesar_evento(evento)


def cancelar(actor, oid):
    empresa = exigir_empresa_id(actor)
    orden = repo.obtener_orden_pago(oid,empresa)
    _propia(actor,orden)
    if orden.get('cambio_id'):
        from .cambio_service import cancelar as cancelar_cambio,_acceso
        with _transaccion() as cur:c=_acceso(cur,actor,orden['cambio_id'])
        cancelar_cambio(actor,c['id'],c['version'])
        return repo.obtener_orden_pago(oid,empresa)
    if orden['modo_pago']!='PRUEBA' or not str(orden['proveedor_session_id']).startswith(('cs_simulada_','qr_prueba_')):
        raise HTTPException(409,'Este pago requiere consultar su resultado con el proveedor antes de cancelarlo.')
    with _transaccion() as cur:
        cur.execute('SELECT id FROM empresa WHERE id=%s FOR UPDATE',(empresa,))
        cur.execute('SELECT estado FROM orden_pago WHERE id=%s AND empresa_id=%s FOR UPDATE',(oid,empresa))
        estado = cur.fetchone()['estado']
        if estado=='PENDIENTE':
            cur.execute("UPDATE orden_pago SET estado='CANCELADO' WHERE id=%s",(oid,))
            cur.execute("UPDATE venta SET estado='CANCELADA' WHERE id=%s AND empresa_id=%s",(orden['venta_id'],empresa))
    return repo.obtener_orden_pago(oid,empresa)


def reembolsos(actor):
    empresa = exigir_empresa_id(actor)
    with _transaccion() as cur:
        if 'ADMINISTRADOR' not in actor.get('roles',[]):
            cur.execute('''SELECT r.* FROM reembolso_pago r JOIN orden_pago o ON o.id=r.orden_id AND o.empresa_id=r.empresa_id
                JOIN cliente c ON c.id=o.cliente_id AND c.empresa_id=o.empresa_id
                WHERE r.empresa_id=%s AND c.usuario_id=%s ORDER BY r.id DESC''',(empresa,actor['id']))
        else:
            cur.execute('SELECT * FROM reembolso_pago WHERE empresa_id=%s ORDER BY id DESC',(empresa,))
        return [dict(r) for r in cur.fetchall()]


def simular_reembolso(actor, rid, aprobar):
    empresa = exigir_empresa_id(actor)
    if not habilitado_pruebas():
        raise HTTPException(403,'El entorno de simulación no está habilitado.')
    with _transaccion() as cur:
        preparar_escritura(cur,actor['id'],empresa,None,nueva=False)
        validar_actor(cur,actor)
        cur.execute('''SELECT o.* FROM orden_pago o JOIN reembolso_pago r ON r.orden_id=o.id AND r.empresa_id=o.empresa_id
            WHERE r.id=%s AND r.empresa_id=%s FOR UPDATE OF o''',(rid,empresa))
        fila = cur.fetchone()
        if not fila:
            raise HTTPException(404,'Reembolso no encontrado.')
        orden = dict(fila)
        if not str(orden['proveedor_session_id']).startswith(('cs_simulada_','qr_prueba_')):
            raise HTTPException(403,'Un reembolso Stripe de prueba conectado requiere confirmación del proveedor.')
        cur.execute('SELECT estado FROM reembolso_pago WHERE id=%s AND empresa_id=%s FOR UPDATE',(rid,empresa))
        estado = cur.fetchone()['estado']
        if estado!='CONFIRMADO':
            cur.execute('''UPDATE reembolso_pago SET estado=%s,referencia=%s,actualizado_en=CURRENT_TIMESTAMP WHERE id=%s AND empresa_id=%s''',
                ('CONFIRMADO' if aprobar else 'FALLIDO',f"reembolso_simulado_{rid}" if aprobar else None,rid,empresa))
            if aprobar:
                cur.execute("UPDATE orden_pago SET estado='REEMBOLSADO' WHERE id=%s",(orden['id'],))
                notificar(cur,orden,f"reembolsado:{rid}",'Reembolso de prueba confirmado','La simulación del reembolso fue confirmada. No se movió dinero real.')
        cur.execute('SELECT * FROM reembolso_pago WHERE id=%s AND empresa_id=%s',(rid,empresa))
        return dict(cur.fetchone())
