"""Políticas y aceptación explícita de la devolución; cambios se incorporan en otro bloque."""
from decimal import Decimal
from fastapi import HTTPException
from pydantic import BaseModel,Field,model_validator,ConfigDict
from psycopg2.extras import Json
from app.core.contexto_empresa import exigir_empresa_id
from app.modules.autenticacion.repositories.perfil_repository import _transaccion
from app.modules.autenticacion.repositories.administracion_acceso_repository import LOCK_ACCESOS,validar_actor

class Politica(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cambios_habilitados:bool=False
    plazo_cambios:int|None=Field(default=None,ge=1,le=365)
    condiciones_cambios:str=Field(default='',max_length=2000)
    devoluciones_habilitadas:bool=False
    plazo_devoluciones:int|None=Field(default=None,ge=1,le=365)
    condiciones_devoluciones:str=Field(default='',max_length=2000)
    version:int=Field(default=0,ge=0)
    @model_validator(mode='after')
    def completa(self):
        for t in ('cambios','devoluciones'):
            if getattr(self,t+'_habilitados' if t=='cambios' else 'devoluciones_habilitadas') and (getattr(self,'plazo_'+t) is None or len(getattr(self,'condiciones_'+t).strip())<5):raise ValueError('Configura plazo y condiciones antes de habilitar la política.')
        return self


def politica(empresa,cur=None):
    if cur is None:
        with _transaccion() as c:return politica(empresa,c)
    cur.execute('SELECT * FROM politica_posventa WHERE empresa_id=%s',(empresa,))
    r=cur.fetchone();return dict(r) if r else Politica().model_dump()


def guardar(actor,p):
    e=exigir_empresa_id(actor)
    with _transaccion() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ACCESOS,));cur.execute('SELECT id FROM empresa WHERE id=%s FOR UPDATE',(e,));validar_actor(cur,actor)
        actual=politica(e,cur)
        if actual['version']!=p.version:raise HTTPException(409,'La política cambió. Actualiza antes de guardar.')
        d=p.model_dump();d['version']+=1
        cur.execute('''INSERT INTO politica_posventa(empresa_id,cambios_habilitados,plazo_cambios,condiciones_cambios,devoluciones_habilitadas,plazo_devoluciones,condiciones_devoluciones,version)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(empresa_id) DO UPDATE SET cambios_habilitados=EXCLUDED.cambios_habilitados,plazo_cambios=EXCLUDED.plazo_cambios,condiciones_cambios=EXCLUDED.condiciones_cambios,
          devoluciones_habilitadas=EXCLUDED.devoluciones_habilitadas,plazo_devoluciones=EXCLUDED.plazo_devoluciones,condiciones_devoluciones=EXCLUDED.condiciones_devoluciones,version=EXCLUDED.version''',
          (e,d['cambios_habilitados'],d['plazo_cambios'],d['condiciones_cambios'].strip(),d['devoluciones_habilitadas'],d['plazo_devoluciones'],d['condiciones_devoluciones'].strip(),d['version']))
        cur.execute("INSERT INTO bitacora(usuario_id,empresa_id,accion,modulo,descripcion) VALUES(%s,%s,'CONFIGURAR','POSVENTA',%s)",(actor['id'],e,f"Política versión {d['version']}"))
        return politica(e,cur)


def bloquear(cur,e,uid,did=None):
    cur.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ACCESOS,));cur.execute('SELECT id FROM empresa WHERE id=%s FOR UPDATE',(e,))
    cur.execute('SELECT id FROM usuario WHERE id=%s AND empresa_id=%s AND activo',(uid,e))
    if not cur.fetchone():raise HTTPException(401,'La cuenta ya no está disponible.')
    if did is None:return
    cur.execute('SELECT sucursal_id FROM devolucion WHERE id=%s AND empresa_id=%s',(did,e));d=cur.fetchone()
    if not d:raise HTTPException(404,'Devolución no encontrada.')
    cur.execute('SELECT r.nombre FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id AND r.activo WHERE ur.usuario_id=%s AND ur.activo',(uid,))
    roles={r['nombre'] for r in cur.fetchall()}
    if 'ADMINISTRADOR' in roles:return
    if not roles.intersection({'CAJERO','ENCARGADO_SUCURSAL'}):raise HTTPException(403,'Gestión reservada al personal de la sucursal.')
    cur.execute('SELECT id FROM empleado WHERE usuario_id=%s AND empresa_id=%s AND sucursal_id=%s AND activo',(uid,e,d['sucursal_id']))
    if not cur.fetchone():raise HTTPException(404,'Devolución no encontrada en tu sucursal.')


def avisar(cur,d,evento,texto):
    from .notificaciones_service import emitir_cliente
    cur.execute('SELECT tipo_operacion,estado FROM devolucion WHERE id=%s AND empresa_id=%s',(d['id'],d['empresa_id']))
    fila=cur.fetchone()
    tipo='CAMBIO' if fila and fila['tipo_operacion']=='CAMBIO' else 'DEVOLUCION'
    categoria='REEMBOLSO' if evento in ('reembolsada','reembolso') else tipo
    if tipo=='CAMBIO' and evento=='solicitud':
        texto='Solicitud de cambio recibida.'
    nombre = 'Cambio' if tipo == 'CAMBIO' else 'Devolución'
    estado = fila.get('estado') if fila else None
    if estado == 'RECHAZADA':
        titulo = nombre + (' rechazado' if tipo == 'CAMBIO' else ' rechazada')
        texto = 'La tienda rechazó tu solicitud. Consulta el motivo en el detalle.'
    elif evento in ('revision', 'inspeccion') and estado in ('APROBADA', 'ESPERA_ACEPTACION'):
        titulo = nombre + (' aprobado' if tipo == 'CAMBIO' else ' aprobada')
    else:
        titulo = {
            'solicitud': nombre + ': solicitud recibida',
            'aceptada': 'Devolución confirmada',
            'aceptacion': 'Condiciones del cambio aceptadas',
            'cancelado': 'Cambio cancelado',
            'conversion': 'Cambio convertido en devolución',
            'pago': 'Pago del cambio confirmado',
            'completo': 'Cambio completado',
            'reembolsada': 'Reintegro confirmado',
        }.get(evento, 'Actualización de ' + tipo.lower())
    if evento == 'reembolsada' and estado == 'REEMBOLSADA':
        from .comprobante_pago_service import encolar_reembolso
        encolar_reembolso(cur, d['empresa_id'], d['id'])
    return emitir_cliente(cur,d['empresa_id'],d['cliente_id'],f"devolucion:{d['id']}:{evento}",
                          titulo,texto,categoria,tipo,d['id'])


def aceptar(actor,did,monto):
    from ..repositories import devolucion_repository as repo
    from ..repositories.nota_venta_repository import emitir_nota_cursor
    e=exigir_empresa_id(actor)
    with _transaccion() as cur:
        bloquear(cur,e,actor['id'])
        cur.execute('''SELECT d.* FROM devolucion d JOIN cliente c ON c.id=d.cliente_id AND c.empresa_id=d.empresa_id
          WHERE d.id=%s AND d.empresa_id=%s AND c.usuario_id=%s FOR UPDATE OF d''',(did,e,actor['id']))
        d=cur.fetchone()
        if not d:raise HTTPException(404,'Devolución no encontrada en tu cuenta.')
        if d['tipo_operacion']=='CAMBIO':raise HTTPException(409,'Acepta el reemplazo desde Mis cambios.')
        if Decimal(monto)!=d['monto_aprobado']:raise HTTPException(409,'El importe cambió. Revisa la devolución.')
        if d['aceptada_cliente_en']:return repo._obtener_devolucion_cursor(cur,did,e)
        if d['estado'] not in ('ESPERA_ACEPTACION','REEMBOLSO_PENDIENTE','ERROR_REEMBOLSO'):raise HTTPException(409,'La inspección todavía no presenta un importe aceptable.')
        cur.execute('SELECT dd.*,vd.producto_variante_id FROM devolucion_detalle dd JOIN venta_detalle vd ON vd.id=dd.venta_detalle_id WHERE dd.devolucion_id=%s ORDER BY vd.producto_variante_id FOR UPDATE OF dd',(did,))
        lineas=[dict(x) for x in cur.fetchall()]
        for l in lineas:
            repo.registrar_reingreso_cursor(cur, actor['id'], d, l, 'DEVOLUCION')
        nota_original = emitir_nota_cursor(cur, d['venta_id'], e)
        recibo={'codigo':d['codigo'],'empresa_id':e,'venta_id':d['venta_id'],'nota_venta_numero':nota_original['numero'],
            'monto':str(d['monto_aprobado']),'moneda':d['moneda'],
            'articulos':[{'venta_detalle_id':l['venta_detalle_id'],'cantidad_aceptada':l['cantidad_aceptada'],'no_vendibles':l['cantidad_no_vendible']} for l in lineas]}
        cur.execute("UPDATE devolucion SET aceptada_cliente_en=CURRENT_TIMESTAMP,estado='REEMBOLSO_PENDIENTE',comprobante=%s WHERE id=%s",(Json(recibo),did))
        avisar(cur,d,'aceptada','Devolución confirmada. El reintegro todavía está pendiente.')
        return repo._obtener_devolucion_cursor(cur,did,e)


def reembolsar(actor,did,para_cambio=False):
    from ..repositories import devolucion_repository as repo
    from .pagos_empresa_service import secreto,habilitado_pruebas
    e=exigir_empresa_id(actor)
    with _transaccion() as cur:
        bloquear(cur,e,actor['id'],did)
        cur.execute('''SELECT d.*,o.proveedor,o.proveedor_session_id,o.proveedor_pago_id,o.proveedor_payment_intent_id,o.modo_pago,v.metodo_pago
          FROM devolucion d JOIN venta v ON v.id=d.venta_id AND v.empresa_id=d.empresa_id
          LEFT JOIN LATERAL(SELECT * FROM orden_pago WHERE venta_id=v.id AND empresa_id=d.empresa_id AND estado='PAGADO' ORDER BY id DESC LIMIT 1)o ON TRUE
          WHERE d.id=%s AND d.empresa_id=%s FOR UPDATE OF d''',(did,e));d=cur.fetchone()
        if (d['tipo_operacion'] == 'CAMBIO') != para_cambio:
            raise HTTPException(409, 'Gestiona el reintegro desde la pantalla de la operación actual.')
        importe=d['importe_reintegro'] if d['importe_reintegro'] is not None else d['monto_aprobado']
        if importe<=0:raise HTTPException(409,'No hay un importe que reintegrar.')
        if d['estado']=='REEMBOLSADA':return repo._obtener_devolucion_cursor(cur,did,e)
        if not d['aceptada_cliente_en'] or d['estado'] not in ('REEMBOLSO_PENDIENTE','ERROR_REEMBOLSO'):raise HTTPException(409,'El cliente debe aceptar la devolución antes del reintegro.')
        if not d['proveedor']:raise HTTPException(409,'Si el pago original fue en efectivo registra la entrega efectiva en caja.')
        sim=str(d['proveedor_session_id']).startswith(('cs_simulada_','qr_prueba_'))
        if sim and habilitado_pruebas():
            cur.execute("UPDATE devolucion SET estado='REEMBOLSADA',metodo_reembolso=%s,referencia_reembolso=%s,fecha_reembolso=CURRENT_TIMESTAMP WHERE id=%s",('STRIPE' if d['proveedor']=='STRIPE' else 'QR',f"reintegro_simulado_{e}_{did}",did))
            avisar(cur,d,'reembolsada','Reintegro simulado confirmado. No se movió dinero real.')
            return repo._obtener_devolucion_cursor(cur,did,e)
        if d['proveedor']!='STRIPE':raise HTTPException(503,'La integración de reintegros QR reales sigue pendiente de conexión bancaria.')
        key=secreto(e,'STRIPE_SECRET_KEY');pi=d['proveedor_pago_id'] or d['proveedor_payment_intent_id']
        if not key.startswith('sk_test_') or not pi:raise HTTPException(503,'Faltan credenciales Stripe test o la referencia original de esta empresa.')
        datos=dict(d);datos['importe']=importe
        if para_cambio and d['proveedor_refund_estado'] is None:
            cur.execute("UPDATE devolucion SET proveedor_refund_estado='INICIADO' WHERE id=%s", (did,))
    import stripe
    try:
        r=stripe.Refund.create(api_key=key,idempotency_key=f'devolucion-{e}-{did}',payment_intent=pi,amount=int(datos['importe']*100),metadata={'empresa_id':str(e),'devolucion_id':str(did)})
        conciliar(e,dict(r))
    except Exception:raise HTTPException(503,'El proveedor no confirmó el reintegro. Consulta el estado antes de reintentar la misma operación.')
    return repo.obtener_devolucion(did,e)


def conciliar(e,obj):
    from ..repositories import devolucion_repository as repo
    try:
        did=int(obj['metadata']['devolucion_id']);assert int(obj['metadata']['empresa_id'])==e
    except (KeyError,ValueError,TypeError,AssertionError):raise HTTPException(409,'Metadatos de reintegro incompatibles.')
    with _transaccion() as cur:
        cur.execute('SELECT id FROM empresa WHERE id=%s FOR UPDATE',(e,))
        cur.execute('''SELECT d.*,o.proveedor_pago_id,o.proveedor_payment_intent_id,o.proveedor FROM devolucion d JOIN orden_pago o ON o.venta_id=d.venta_id AND o.empresa_id=d.empresa_id
           WHERE d.id=%s AND d.empresa_id=%s AND o.estado='PAGADO' ORDER BY o.id DESC LIMIT 1 FOR UPDATE OF d''',(did,e));d=cur.fetchone()
        if not d or not d['aceptada_cliente_en'] or d['proveedor']!='STRIPE' or obj.get('livemode') is True or obj.get('amount')!=int((d['importe_reintegro'] if d['importe_reintegro'] is not None else d['monto_aprobado'])*100) or str(obj.get('currency','')).upper()!=d['moneda'] or obj.get('payment_intent')!=(d['proveedor_pago_id'] or d['proveedor_payment_intent_id']):raise HTTPException(409,'El reintegro no coincide con el pago original.')
        if not obj.get('id') or (d['proveedor_refund_id'] and d['proveedor_refund_id']!=obj['id']):raise HTTPException(409,'Referencia incompatible.')
        if d['estado'] not in ('REEMBOLSADA','CAMBIO_COMPLETADO'):
            estado={'succeeded':'REEMBOLSADA','failed':'ERROR_REEMBOLSO'}.get(obj.get('status'),'REEMBOLSO_PENDIENTE')
            cur.execute("UPDATE devolucion SET estado=%s,proveedor_refund_id=%s,proveedor_refund_estado=%s,metodo_reembolso='STRIPE',fecha_reembolso=CASE WHEN %s='REEMBOLSADA' THEN CURRENT_TIMESTAMP ELSE fecha_reembolso END WHERE id=%s",(estado,obj['id'],obj.get('status'),estado,did))
            if estado=='REEMBOLSADA':
                avisar(cur,d,'reembolsada','Stripe confirmó el reintegro en su entorno de pruebas.')
                if d['tipo_operacion']=='CAMBIO':cur.execute("UPDATE cambio_prenda SET estado='LISTO',version=version+1 WHERE id=%s AND estado='ESPERA_REINTEGRO'",(did,))
    return {'estado':'RECIBIDO'}
