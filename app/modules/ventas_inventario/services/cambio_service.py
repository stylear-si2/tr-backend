"""CU19: créditos de compra, reservas y liquidación de diferencias por empresa."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg2.extras import Json

from app.core.contexto_empresa import exigir_empresa_id
from app.modules.autenticacion.repositories.perfil_repository import _transaccion
from app.modules.reservas.repositories.carrito_repository import _obtener_precio_final_variante_cursor
from ..repositories import devolucion_repository as devoluciones
from ..repositories.inventario_repository import _aplicar_movimiento
from ..repositories.nota_venta_repository import emitir_nota_cursor
from . import posventa_service as posventa


class Solicitud(BaseModel):
    model_config = ConfigDict(extra='forbid')
    clave_operacion: UUID
    venta_id: int = Field(gt=0)
    venta_detalle_id: int = Field(gt=0)
    cantidad: int = Field(gt=0, strict=True)
    motivo: str = Field(min_length=5, max_length=500)
    reemplazo_id: int | None = Field(default=None, gt=0)

    @field_validator('motivo')
    @classmethod
    def motivo_valido(cls, v):
        if len(v.strip()) < 5:raise ValueError('Describe el motivo del cambio.')
        return v.strip()


class Revision(BaseModel):
    version: int = Field(ge=1)
    aceptar: bool
    no_vendibles: int = Field(default=0, ge=0, strict=True)
    condicion: str = Field(min_length=5, max_length=500)
    recepcion_confirmada: bool = False

    @field_validator('condicion')
    @classmethod
    def condicion_valida(cls, v):
        if len(v.strip()) < 5:raise ValueError('Describe la condición de la prenda o el rechazo.')
        return v.strip()


class Version(BaseModel):
    version: int = Field(ge=1)


class Cotizacion(Version):
    reemplazo_id: int = Field(gt=0)
    adicional_id: int | None = Field(default=None, gt=0)


class Aceptacion(Version):
    diferencia: Decimal = Field(max_digits=12, decimal_places=2)


class Entrega(Version):
    entrega_confirmada: bool


def _acceso(cur, actor, cid, gestion=False):
    e = exigir_empresa_id(actor)
    posventa.bloquear(cur, e, actor['id'], cid if gestion else None)
    cur.execute('''SELECT c.*,d.empresa_id,d.cliente_id,d.sucursal_id,d.venta_id,
        d.codigo,d.tipo_operacion,d.monto_solicitado,d.monto_aprobado,d.estado estado_devolucion,
        d.aceptada_cliente_en,d.referencia_reembolso,d.metodo_reembolso,d.proveedor_refund_id,
        d.proveedor_refund_estado
        FROM cambio_prenda c JOIN devolucion d ON d.id=c.id
        WHERE c.id=%s AND d.empresa_id=%s AND d.activo FOR UPDATE OF c,d''', (cid,e))
    c = cur.fetchone()
    if not c:
        raise HTTPException(404, 'Cambio no encontrado.')
    if not gestion:
        cur.execute('SELECT 1 FROM cliente WHERE id=%s AND empresa_id=%s AND usuario_id=%s AND activo', (c['cliente_id'],e,actor['id']))
        if not cur.fetchone():
            raise HTTPException(404, 'Cambio no encontrado en tu cuenta.')
    return dict(c)


def _version(c, version):
    if c['version'] != version:
        raise HTTPException(409, 'El cambio fue actualizado. Recarga y revisa sus condiciones.')


def _actualizar(cur, cid, estado):
    cur.execute('UPDATE cambio_prenda SET estado=%s,version=version+1,actualizado_en=CURRENT_TIMESTAMP WHERE id=%s', (estado,cid))


def _detalle(cur, cid, e):
    cur.execute('SELECT * FROM cambio_prenda WHERE id=%s', (cid,))
    c = dict(cur.fetchone())
    c['original'] = devoluciones._obtener_devolucion_cursor(cur,cid,e)
    for campo in ('reemplazo', 'adicional'):
        variante = c[f'{campo}_id']
        if variante is None:
            continue
        cur.execute('''SELECT p.nombre producto,COALESCE(t.nombre,'Sin talla') talla,COALESCE(co.nombre,'Sin color') color
            FROM producto_variante v JOIN producto p ON p.id=v.producto_id AND p.empresa_id=v.empresa_id
            LEFT JOIN talla t ON t.id=v.talla_id LEFT JOIN color co ON co.id=v.color_id
            WHERE v.id=%s AND v.empresa_id=%s''', (variante, e))
        c[campo] = dict(cur.fetchone())
    if c['orden_id']:
        from .pagos_empresa_service import habilitado_pruebas
        cur.execute('SELECT id,estado,checkout_url,proveedor,proveedor_session_id,monto_total FROM orden_pago WHERE id=%s AND empresa_id=%s', (c['orden_id'],e))
        o = dict(cur.fetchone())
        o['simulable'] = habilitado_pruebas() and str(o['proveedor_session_id']).startswith(('cs_simulada_','qr_prueba_'))
        o.pop('proveedor_session_id')
        c['pago'] = o
    return c


def listar(actor, gestion=False):
    e = exigir_empresa_id(actor)
    with _transaccion() as cur:
        posventa.bloquear(cur,e,actor['id'])
        if gestion:
            cur.execute('''SELECT DISTINCT d.id FROM devolucion d JOIN cambio_prenda c ON c.id=d.id
                WHERE d.empresa_id=%s AND (EXISTS(SELECT 1 FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id
                WHERE ur.usuario_id=%s AND ur.activo AND r.activo AND r.nombre='ADMINISTRADOR')
                OR EXISTS(SELECT 1 FROM empleado em JOIN usuario_rol ur ON ur.usuario_id=em.usuario_id AND ur.activo
                JOIN rol r ON r.id=ur.rol_id AND r.activo AND r.nombre IN ('CAJERO','ENCARGADO_SUCURSAL')
                WHERE em.usuario_id=%s AND em.empresa_id=d.empresa_id AND em.sucursal_id=d.sucursal_id AND em.activo))
                ORDER BY d.id DESC''', (e,actor['id'],actor['id']))
        else:
            cur.execute('''SELECT d.id FROM devolucion d JOIN cambio_prenda c ON c.id=d.id
                JOIN cliente cl ON cl.id=d.cliente_id AND cl.empresa_id=d.empresa_id
                WHERE d.empresa_id=%s AND cl.usuario_id=%s ORDER BY d.id DESC''', (e,actor['id']))
        return [_detalle(cur,x['id'],e) for x in cur.fetchall()]


def elegibles(actor):
    e = exigir_empresa_id(actor)
    pol = posventa.politica(e)
    cliente = devoluciones.cliente_id_por_usuario(actor['id'],e)
    if not pol['cambios_habilitados'] or not cliente:
        return []
    return devoluciones.listar_ventas_elegibles(cliente,pol['plazo_cambios'],e)


def solicitar(actor, datos):
    e = exigir_empresa_id(actor)
    cliente = devoluciones.cliente_id_por_usuario(actor['id'],e)
    if not cliente:
        raise HTTPException(403, 'Se requiere una cuenta de cliente.')
    try:
        d = devoluciones.crear_devolucion(cliente,actor['id'],datos.venta_id,datos.motivo,None,
            [{'venta_detalle_id':datos.venta_detalle_id,'cantidad':datos.cantidad}],0,e,str(datos.clave_operacion),'CAMBIO',
            reemplazo_solicitado_id=datos.reemplazo_id)
    except ValueError as ex:
        raise HTTPException(409,str(ex)) from ex
    with _transaccion() as cur:
        _acceso(cur,actor,d['id'])
        return _detalle(cur,d['id'],e)


def opciones_solicitud(actor, venta_id, linea_id, cantidad):
    """Estimación propia; no aprueba inspección, reserva stock ni inicia pagos."""
    venta = next((v for v in elegibles(actor) if v['venta_id'] == venta_id), None)
    linea = next((l for l in (venta or {}).get('detalles', []) if l['venta_detalle_id'] == linea_id), None)
    if not linea:
        raise HTTPException(404, 'Compra no encontrada entre tus prendas elegibles.')
    if cantidad > linea['cantidad_disponible']:
        raise HTTPException(409, 'La cantidad supera las unidades elegibles.')
    with _transaccion() as cur:
        cur.execute('SELECT sucursal_id FROM venta WHERE id=%s AND empresa_id=%s', (venta_id, exigir_empresa_id(actor)))
        sucursal = cur.fetchone()['sucursal_id']
        credito = devoluciones._calcular_parcial(devoluciones._subtotal_pagado_cursor(cur, linea_id), linea['cantidad_vendida'], cantidad)
        return {'credito_estimado': str(credito), 'opciones': _opciones_cursor(cur, sucursal, exigir_empresa_id(actor), cantidad)}


def revisar(actor,cid,datos):
    with _transaccion() as cur:
        c = _acceso(cur,actor,cid,True)
        _version(c,datos.version)
        if c['estado'] != 'SOLICITADO':
            raise HTTPException(409,'Esta solicitud ya fue revisada.')
        if datos.no_vendibles > c['cantidad']:
            raise HTTPException(422,'Las unidades no vendibles superan la cantidad solicitada.')
        if datos.aceptar and not datos.recepcion_confirmada:
            raise HTTPException(409,'Confirma la recepción física antes de aceptar el cambio.')
        cur.execute('''UPDATE devolucion SET estado=%s,observacion=%s,revisado_por_usuario_id=%s,
            recibido_por_usuario_id=%s,fecha_revision=CURRENT_TIMESTAMP,fecha_recepcion=CASE WHEN %s THEN CURRENT_TIMESTAMP END,
            monto_aprobado=CASE WHEN %s THEN monto_solicitado ELSE 0 END WHERE id=%s''',
            ('APROBADA' if datos.aceptar else 'RECHAZADA',datos.condicion,actor['id'],actor['id'],datos.aceptar,datos.aceptar,cid))
        cur.execute('''UPDATE devolucion_detalle SET cantidad_aceptada=CASE WHEN %s THEN cantidad_solicitada ELSE 0 END,
            cantidad_rechazada=CASE WHEN %s THEN 0 ELSE cantidad_solicitada END,
            cantidad_no_vendible=%s,observacion=%s WHERE devolucion_id=%s''',
            (datos.aceptar,datos.aceptar,datos.no_vendibles if datos.aceptar else 0,datos.condicion,cid))
        # El crédito consume el importe realmente pagado, con ajuste final de centavos.
        if datos.aceptar:
            cur.execute('SELECT dd.*,vd.cantidad FROM devolucion_detalle dd JOIN venta_detalle vd ON vd.id=dd.venta_detalle_id WHERE dd.devolucion_id=%s',(cid,))
            l=cur.fetchone()
            cur.execute('''SELECT COALESCE(SUM(dd.cantidad_aceptada),0) cantidad,COALESCE(SUM(dd.monto_aprobado),0) monto
                FROM devolucion_detalle dd JOIN devolucion d ON d.id=dd.devolucion_id WHERE dd.venta_detalle_id=%s
                AND d.id<>%s AND d.estado IN ('APROBADA','ESPERA_ACEPTACION','REEMBOLSO_PENDIENTE','ERROR_REEMBOLSO','REEMBOLSADA','CAMBIO_COMPLETADO')''',(l['venta_detalle_id'],cid))
            previo=cur.fetchone();neto=devoluciones._subtotal_pagado_cursor(cur,l['venta_detalle_id'])
            credito=min(neto-previo['monto'],devoluciones._calcular_parcial(neto,l['cantidad'],c['cantidad']))
            if previo['cantidad']+c['cantidad']==l['cantidad']:credito=neto-previo['monto']
            if credito<=0:raise HTTPException(409,'La prenda no conserva un importe disponible para cambio.')
            cur.execute('UPDATE devolucion SET monto_aprobado=%s,monto_solicitado=GREATEST(monto_solicitado,%s) WHERE id=%s',(credito,credito,cid))
            cur.execute('UPDATE devolucion_detalle SET monto_aprobado=%s WHERE devolucion_id=%s',(credito,cid))
            cur.execute('UPDATE cambio_prenda SET credito=%s WHERE id=%s',(credito,cid))
        _actualizar(cur,cid,'APROBADO' if datos.aceptar else 'RECHAZADO')
        if datos.aceptar and c['reemplazo_id'] is not None:
            # La elección inicial se valida después de la inspección, sin reservar ni cobrar.
            c['credito'] = credito
            try:
                _cotizar_cursor(cur,c,cid,Cotizacion(version=c['version'],reemplazo_id=c['reemplazo_id']))
            except HTTPException as error:
                if error.status_code != 409:
                    raise
                # La inspección se conserva; el cliente puede actualizar o cambiar la opción.
                pass
        posventa.avisar(cur,c,'revision','La tienda revisó tu solicitud de cambio.')
        return _detalle(cur,cid,c['empresa_id'])


def opciones(actor, cid, incluir_adicionales=False):
    with _transaccion() as cur:
        c=_acceso(cur,actor,cid)
        return _opciones_cursor(cur, c['sucursal_id'], c['empresa_id'], 1 if incluir_adicionales else c['cantidad'])


def _opciones_cursor(cur, sucursal_id, empresa_id, cantidad):
        cur.execute('''SELECT v.id,p.id producto_id,p.nombre producto,COALESCE(t.nombre,'Sin talla') talla,COALESCE(co.nombre,'Sin color') color,
            i.stock_disponible-i.stock_reservado disponible FROM producto_variante v
            JOIN producto p ON p.id=v.producto_id AND p.empresa_id=v.empresa_id
            JOIN inventario_sucursal i ON i.producto_variante_id=v.id AND i.empresa_id=v.empresa_id AND i.sucursal_id=%s
            LEFT JOIN talla t ON t.id=v.talla_id LEFT JOIN color co ON co.id=v.color_id
            WHERE v.empresa_id=%s AND v.activo AND p.activo AND i.activo
            AND i.stock_disponible-i.stock_reservado>=%s ORDER BY p.nombre,v.id LIMIT 500''',
            (sucursal_id, empresa_id, cantidad))
        opciones=[]
        for o in cur.fetchall():
            precio=_obtener_precio_final_variante_cursor(cur,o['id'],sucursal_id)
            if precio and precio>0:opciones.append({**dict(o),'precio':precio})
        return opciones


def _lineas(c):
    cantidades = {c['reemplazo_id']: c['cantidad']}
    if c['adicional_id'] is not None:
        adicional = c['adicional_id']
        cantidades[adicional] = cantidades.get(adicional, 0) + 1
    return sorted(cantidades.items())


def _stock(cur, c, variante, cantidad):
    cur.execute('''SELECT i.* FROM inventario_sucursal i JOIN producto_variante v ON v.id=i.producto_variante_id AND v.empresa_id=i.empresa_id
        JOIN producto p ON p.id=v.producto_id AND p.empresa_id=v.empresa_id
        WHERE i.empresa_id=%s AND i.sucursal_id=%s AND i.producto_variante_id=%s AND i.activo AND v.activo AND p.activo FOR UPDATE OF i FOR SHARE OF v,p''',
        (c['empresa_id'],c['sucursal_id'],variante))
    i=cur.fetchone()
    if not i or i['stock_disponible'] - i['stock_reservado'] < cantidad:
        raise HTTPException(409,'Una de las prendas ya no tiene existencias suficientes. Selecciona otra opción.')
    return i


def cotizar(actor,cid,datos):
    with _transaccion() as cur:
        c=_acceso(cur,actor,cid);_version(c,datos.version)
        if c['estado'] not in ('APROBADO','COTIZADO'):
            raise HTTPException(409,'Primero debe aprobarse e inspeccionarse la prenda original.')
        _cotizar_cursor(cur,c,cid,datos)
        return _detalle(cur,cid,c['empresa_id'])

def _cotizar_cursor(cur,c,cid,datos):
    c.update(reemplazo_id=datos.reemplazo_id, adicional_id=datos.adicional_id)
    for variante, cantidad in _lineas(c):
        _stock(cur, c, variante, cantidad)
    precio=_obtener_precio_final_variante_cursor(cur,datos.reemplazo_id,c['sucursal_id'])
    if precio is None or precio<=0:raise HTTPException(409,'El reemplazo no tiene precio vigente.')
    subtotal = precio * c['cantidad']
    adicional = None
    if datos.adicional_id is not None:
        if subtotal >= c['credito']:
            raise HTTPException(409, 'La prenda adicional se puede agregar cuando queda saldo a favor.')
        adicional = _obtener_precio_final_variante_cursor(cur, datos.adicional_id, c['sucursal_id'])
        if adicional is None or adicional <= 0:
            raise HTTPException(409, 'La prenda adicional no tiene precio vigente.')
    diferencia = (subtotal + (adicional or Decimal(0)) - c['credito']).quantize(Decimal('.01'))
    cur.execute("""UPDATE cambio_prenda SET reemplazo_id=%s,precio_reemplazo=%s,adicional_id=%s,
        precio_adicional=%s,diferencia=%s,cotizacion_vence=CURRENT_TIMESTAMP+INTERVAL '15 minutes' WHERE id=%s""",
        (datos.reemplazo_id, precio, datos.adicional_id, adicional, diferencia, cid))
    _actualizar(cur,cid,'COTIZADO')



def aceptar(actor,cid,datos):
    with _transaccion() as cur:
        c=_acceso(cur,actor,cid)
        if c['aceptado_en'] and c['diferencia']==datos.diferencia:return _detalle(cur,cid,c['empresa_id'])
        _version(c,datos.version)
        if c['estado']!='COTIZADO' or c['cotizacion_vence']<=datetime.now(timezone.utc):
            raise HTTPException(409,'La cotización venció. Selecciona nuevamente el reemplazo.')
        reservas = [(_stock(cur, c, variante, cantidad)['id'], cantidad) for variante, cantidad in _lineas(c)]
        precio=_obtener_precio_final_variante_cursor(cur,c['reemplazo_id'],c['sucursal_id'])
        adicional = (_obtener_precio_final_variante_cursor(cur, c['adicional_id'], c['sucursal_id'])
                     if c['adicional_id'] is not None else None)
        if precio!=c['precio_reemplazo'] or adicional!=c['precio_adicional'] or datos.diferencia!=c['diferencia']:
            raise HTTPException(409,'Cambió el precio o la diferencia. Revisa una nueva cotización.')
        for inventario_id, cantidad in reservas:
            cur.execute('UPDATE inventario_sucursal SET stock_reservado=stock_reservado+%s WHERE id=%s',
                        (cantidad, inventario_id))
        cur.execute('UPDATE cambio_prenda SET reservado=TRUE,aceptado_en=CURRENT_TIMESTAMP WHERE id=%s',(cid,))
        cur.execute("UPDATE devolucion SET estado='REEMBOLSO_PENDIENTE',aceptada_cliente_en=CURRENT_TIMESTAMP,importe_reintegro=%s WHERE id=%s",(max(-c['diferencia'],Decimal(0)),cid))
        _actualizar(cur,cid,'ESPERA_PAGO' if c['diferencia']>0 else 'ESPERA_REINTEGRO' if c['diferencia']<0 else 'LISTO')
        posventa.avisar(cur,c,'aceptacion','Aceptaste las condiciones del cambio. Consulta el pago o reintegro y la entrega.')
        return _detalle(cur,cid,c['empresa_id'])


def _liberar(cur,c):
    if c['reservado']:
        for variante, cantidad in _lineas(c):
            cur.execute('''UPDATE inventario_sucursal SET stock_reservado=stock_reservado-%s
                WHERE empresa_id=%s AND sucursal_id=%s AND producto_variante_id=%s AND stock_reservado>=%s''',
                (cantidad,c['empresa_id'],c['sucursal_id'],variante,cantidad))
            if cur.rowcount!=1:raise HTTPException(409,'La reserva requiere revisión de inventario.')
        cur.execute('UPDATE cambio_prenda SET reservado=FALSE WHERE id=%s',(c['id'],))


def cancelar(actor,cid,version):
    with _transaccion() as cur:
        c=_acceso(cur,actor,cid)
        if c['estado']=='CANCELADO':return _detalle(cur,cid,c['empresa_id'])
        _version(c,version)
        if c['estado'] not in ('SOLICITADO','APROBADO','COTIZADO','ESPERA_PAGO','LISTO') or (c['aceptado_en'] and (c['diferencia'] or 0)<0):
            raise HTTPException(409,'La liquidación ya está en curso; no se puede cancelar este cambio.')
        if c['orden_id']:
            cur.execute('SELECT * FROM orden_pago WHERE id=%s FOR UPDATE',(c['orden_id'],));o=cur.fetchone()
            if o['pago_recibido'] or not str(o['proveedor_session_id']).startswith(('cs_simulada_','qr_prueba_')):
                raise HTTPException(409,'Debe conciliarse el pago con el proveedor antes de liberar la reserva.')
            cur.execute("UPDATE orden_pago SET estado='CANCELADO' WHERE id=%s AND estado='PENDIENTE'",(o['id'],))
            cur.execute("UPDATE venta SET estado='CANCELADA' WHERE id=%s",(o['venta_id'],))
        _liberar(cur,c);_actualizar(cur,cid,'CANCELADO')
        cur.execute("UPDATE devolucion SET estado='CANCELADA' WHERE id=%s",(cid,))
        posventa.avisar(cur,c,'cancelado','Solicitud de cambio cancelada; el reemplazo ya no está reservado.')
        return _detalle(cur,cid,c['empresa_id'])


def convertir(actor, cid, version, gestion=False):
    with _transaccion() as cur:
        c = _acceso(cur, actor, cid, gestion)
        if c['estado']=='DEVOLUCION':return _detalle(cur,cid,c['empresa_id'])
        _version(c,version)
        estados = ('ESPERA_PAGO', 'ESPERA_REINTEGRO', 'LISTO') if gestion else ('APROBADO', 'COTIZADO')
        if c['estado'] not in estados:
            raise HTTPException(409, 'El estado actual del cambio no permite continuar por devolución.')
        if c['estado_devolucion'] == 'REEMBOLSADA' or c['proveedor_refund_estado'] or c['proveedor_refund_id']:
            raise HTTPException(409, 'Ya se inició un reintegro del cambio. Consulta su estado antes de modificarlo.')
        if c['orden_id'] is not None:
            from .pagos_empresa_service import solicitar_reembolso
            cur.execute('SELECT * FROM orden_pago WHERE id=%s AND empresa_id=%s FOR UPDATE',
                        (c['orden_id'], c['empresa_id']))
            orden = cur.fetchone()
            if orden['pago_recibido']:
                solicitar_reembolso(cur, orden, 'El reemplazo del cambio no está disponible.')
            else:
                cur.execute("UPDATE orden_pago SET estado='CANCELADO' WHERE id=%s AND estado='PENDIENTE'", (orden['id'],))
                cur.execute("UPDATE venta SET estado='CANCELADA' WHERE id=%s", (orden['venta_id'],))
        _liberar(cur, c)
        cur.execute("""UPDATE devolucion SET tipo_operacion='DEVOLUCION',estado='ESPERA_ACEPTACION',
            aceptada_cliente_en=NULL,importe_reintegro=NULL WHERE id=%s""", (cid,))
        _actualizar(cur,cid,'DEVOLUCION')
        posventa.avisar(cur,c,'conversion','El cambio continúa como devolución. Revisa y acepta el importe en Mis devoluciones.')
        return _detalle(cur,cid,c['empresa_id'])


def crear_pago(actor,cid,version,medio):
    from . import pagos_empresa_service as pagos
    from ..repositories.pago_repository import _crear_venta_pendiente,actualizar_checkout_stripe
    e=exigir_empresa_id(actor)
    with pagos.bloqueo_checkout(e,actor['id']):
        with _transaccion() as cur:
            c=_acceso(cur,actor,cid)
            if c['estado']!='ESPERA_PAGO':raise HTTPException(409,'El cambio no espera un pago de diferencia.')
            if c['orden_id']:
                cur.execute('SELECT * FROM orden_pago WHERE id=%s',(c['orden_id'],));o=dict(cur.fetchone())
                if o['proveedor']!=medio:raise HTTPException(409,'Consulta o cancela el pago existente antes de cambiar de medio.')
            else:
                _version(c,version)
                cfg=pagos.configuracion(e)
                if not cfg['stripe_habilitado' if medio=='STRIPE' else 'qr_habilitado']:
                    raise HTTPException(409,'La empresa no habilitó este medio de pago.')
                vid=_crear_venta_pendiente(cur,actor['id'],c['cliente_id'],c['sucursal_id'],c['diferencia'],c['diferencia'],Decimal(0),'RECOJO_SUCURSAL',e)
                cur.execute('''INSERT INTO orden_pago(empresa_id,venta_id,cliente_id,concepto,monto_total,moneda,metodo,estado,proveedor,modo_pago,titular_pago,cambio_id)
                    VALUES(%s,%s,%s,'DIFERENCIA_CAMBIO',%s,'BOB',%s,'PENDIENTE',%s,'PRUEBA',%s,%s) RETURNING *''',
                    (e,vid,c['cliente_id'],c['diferencia'],'TARJETA' if medio=='STRIPE' else 'QR',medio,cfg['titular'],cid))
                o=dict(cur.fetchone());cur.execute('UPDATE cambio_prenda SET orden_id=%s WHERE id=%s',(o['id'],cid))
        if not o['proveedor_session_id']:
            sesion=pagos.crear_sesion_stripe(o) if medio=='STRIPE' else {'id':f"qr_prueba_{e}_{o['id']}",'url':None,'payment_intent':None}
            actualizar_checkout_stripe(o['id'],sesion['id'],sesion['url'],sesion['payment_intent'],e)
        with _transaccion() as cur:
            _acceso(cur,actor,cid)
            return _detalle(cur,cid,e)


def evento_pago(cur,o,evento):
    """Se ejecuta tras validar firma/importe y deduplicar el evento de pago CU16."""
    from .pagos_empresa_service import solicitar_reembolso
    cur.execute('''SELECT c.*,d.empresa_id,d.cliente_id,d.sucursal_id FROM cambio_prenda c
        JOIN devolucion d ON d.id=c.id WHERE c.id=%s FOR UPDATE OF c''',(o['cambio_id'],))
    c=dict(cur.fetchone())
    if evento.tipo=='RECHAZADO':
        if not o['pago_recibido'] and o['estado']=='PENDIENTE':
            cur.execute("UPDATE orden_pago SET estado='RECHAZADO' WHERE id=%s",(o['id'],))
            cur.execute("UPDATE venta SET estado='RECHAZADA' WHERE id=%s",(o['venta_id'],))
            _liberar(cur,c)
            cur.execute('UPDATE cambio_prenda SET aceptado_en=NULL,orden_id=NULL WHERE id=%s',(c['id'],))
            cur.execute("UPDATE devolucion SET estado='APROBADA',aceptada_cliente_en=NULL,importe_reintegro=NULL WHERE id=%s",(c['id'],))
            _actualizar(cur,c['id'],'APROBADO')
        return
    if o['pago_recibido']:return
    cur.execute('UPDATE orden_pago SET pago_recibido=TRUE,proveedor_pago_id=%s,fecha_pago=CURRENT_TIMESTAMP WHERE id=%s',(evento.pago_id,o['id']))
    if o['estado']!='PENDIENTE' or c['estado']!='ESPERA_PAGO' or c['orden_id']!=o['id'] or not c['reservado']:
        solicitar_reembolso(cur,o,'Pago tardío de una diferencia de cambio cerrada.')
        return
    cur.execute("UPDATE orden_pago SET estado='PAGADO' WHERE id=%s",(o['id'],))
    cur.execute("UPDATE venta SET estado='COMPLETADA',metodo_pago=%s WHERE id=%s",(o['metodo'],o['venta_id']))
    _actualizar(cur,c['id'],'LISTO')
    from .comprobante_pago_service import encolar_cambio
    cur.execute('SELECT fecha_pago FROM orden_pago WHERE id=%s AND empresa_id=%s', (o['id'], o['empresa_id']))
    encolar_cambio(cur, o['empresa_id'], {**o, **cur.fetchone()}, _detalle(cur, c['id'], o['empresa_id']))
    posventa.avisar(cur,c,'pago','El pago de la diferencia fue confirmado. El reemplazo está listo para entrega.')


def reembolsar(actor,cid):
    with _transaccion() as cur:
        c=_acceso(cur,actor,cid,True)
        if c['estado']=='LISTO':return _detalle(cur,cid,c['empresa_id'])
        if c['estado']!='ESPERA_REINTEGRO':raise HTTPException(409,'Este cambio no espera un reintegro.')
    posventa.reembolsar(actor,cid,para_cambio=True)
    with _transaccion() as cur:
        c=_acceso(cur,actor,cid,True)
        if c['estado_devolucion']=='REEMBOLSADA' and c['estado']=='ESPERA_REINTEGRO':_actualizar(cur,cid,'LISTO')
        return _detalle(cur,cid,c['empresa_id'])


def completar(actor,cid,datos):
    with _transaccion() as cur:
        c=_acceso(cur,actor,cid,True)
        if c['estado']=='COMPLETADO':return _detalle(cur,cid,c['empresa_id'])
        _version(c,datos.version)
        if c['estado']!='LISTO' or not datos.entrega_confirmada or not c['reservado']:
            raise HTTPException(409,'Se requiere liquidación confirmada y entrega física del reemplazo.')
        _liberar(cur,c)
        try:
            for variante, cantidad in _lineas(c):
                _aplicar_movimiento(cur, actor['id'], c['sucursal_id'], variante, 'VENTA_DIGITAL', cantidad,
                                    'Entrega por cambio', 'CAMBIO', cid, empresa_id=c['empresa_id'])
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        cur.execute('SELECT dd.*,vd.producto_variante_id FROM devolucion_detalle dd JOIN venta_detalle vd ON vd.id=dd.venta_detalle_id WHERE dd.devolucion_id=%s',(cid,))
        l = cur.fetchone()
        devoluciones.registrar_reingreso_cursor(cur, actor['id'], c, l, 'CAMBIO')
        nota_original = emitir_nota_cursor(cur, c['venta_id'], c['empresa_id'])
        comprobante={'codigo':f'CAM-{cid}','venta_original_id':c['venta_id'],'original_variante_id':l['producto_variante_id'],
            'nota_venta_numero':nota_original['numero'],
            'reemplazo_id':c['reemplazo_id'],'cantidad':c['cantidad'],'no_vendibles':l['cantidad_no_vendible'],
            'credito':str(c['credito']),'precio_reemplazo':str(c['precio_reemplazo']),'diferencia':str(c['diferencia']),
            'moneda':'BOB','orden_diferencia_id':c['orden_id'],'referencia_reintegro':c['referencia_reembolso'] or c['proveedor_refund_id']}
        if c['adicional_id'] is not None:
            comprobante.update(adicional_id=c['adicional_id'], precio_adicional=str(c['precio_adicional']))
        cur.execute('UPDATE cambio_prenda SET comprobante=%s WHERE id=%s',(Json(comprobante),cid))
        cur.execute("UPDATE devolucion SET estado='CAMBIO_COMPLETADO' WHERE id=%s",(cid,))
        _actualizar(cur,cid,'COMPLETADO')
        cur.execute("INSERT INTO bitacora(empresa_id,usuario_id,accion,modulo,descripcion) VALUES(%s,%s,'COMPLETAR','CAMBIOS',%s)",(c['empresa_id'],actor['id'],f'Cambio {cid} entregado con comprobante.'))
        posventa.avisar(cur,c,'completo','Cambio completado. Tu comprobante está disponible en Mis cambios.')
        return _detalle(cur,cid,c['empresa_id'])
