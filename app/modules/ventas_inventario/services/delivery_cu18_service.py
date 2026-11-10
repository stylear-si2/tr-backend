"""CU18: cotización aceptada, entrega pagada y seguimiento serializado por sucursal."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from uuid import uuid4
from fastapi import HTTPException
from psycopg2.extras import Json
from app.core.contexto_empresa import exigir_empresa_id
from app.modules.autenticacion.repositories.perfil_repository import _transaccion
from app.modules.autenticacion.repositories.administracion_acceso_repository import LOCK_ACCESOS

TRANSICIONES = {'PENDIENTE': ['EN_PREPARACION','CANCELADO'], 'EN_PREPARACION':['EN_CAMINO','CANCELADO'],
                'EN_CAMINO':['ENTREGADO'], 'ENTREGADO':[], 'CANCELADO':[], 'PENDIENTE_PAGO':[]}


def solicitud(request):
    return dict(sucursal_id=request.sucursal_id, direccion_id=request.direccion_id,
        direccion_entrega=request.direccion_entrega.strip(), referencia=(request.referencia or '').strip() or None,
        latitud_entrega=format(request.latitud_entrega.quantize(Decimal('.0000001')),'f'),
        longitud_entrega=format(request.longitud_entrega.quantize(Decimal('.0000001')),'f'))


def huella(datos):
    return hashlib.sha256(json.dumps(datos,sort_keys=True).encode()).hexdigest()


def cotizar(actor, request):
    from . import delivery_service as rutas
    empresa=exigir_empresa_id(actor)
    datos=solicitud(request)
    if len(datos['direccion_entrega'])<5:raise HTTPException(422,'La dirección debe tener al menos cinco caracteres.')
    with _transaccion() as cur:
        cur.execute('SELECT id FROM cliente WHERE usuario_id=%s AND empresa_id=%s AND activo',(actor['id'],empresa))
        cliente=cur.fetchone()
        if not cliente or 'CLIENTE' not in actor.get('roles',[]):raise HTTPException(403,'Se requiere una cuenta de cliente para cotizar.')
        if request.direccion_id:
            cur.execute('SELECT id FROM direccion_cliente WHERE id=%s AND cliente_id=%s AND activo',(request.direccion_id,cliente['id']))
            if not cur.fetchone():raise HTTPException(404,'Dirección no encontrada en tu cuenta.')
        sucursal=rutas.repo.obtener_sucursal_cursor(cur,request.sucursal_id,empresa)
        if not sucursal or sucursal['latitud'] is None or sucursal['longitud'] is None:
            raise HTTPException(409,'La sucursal no tiene una ubicación válida para cotizar.')
    distancia,tiempo,ruta=rutas._calcular_ruta(sucursal['latitud'],sucursal['longitud'],request.latitud_entrega,request.longitud_entrega)
    costo=rutas._calcular_costo(distancia)
    # Se conserva la tarifa existente; nunca ofrecer envío gratuito fuera de sus tramos.
    if distancia>min(rutas._max_km(),Decimal('10')) or costo<=0:
        return dict(sucursal_id=request.sucursal_id,distancia_km=distancia,tiempo_estimado_min=tiempo,costo_delivery=Decimal('0'),
            disponible=False,mensaje='La dirección está fuera del rango de delivery disponible.',ruta=ruta)
    qid=uuid4();vence=datetime.now(timezone.utc)+timedelta(minutes=15)
    with _transaccion() as cur:
        cur.execute('''INSERT INTO delivery_cotizacion(id,empresa_id,usuario_id,cliente_id,sucursal_id,solicitud,huella,
            origen_latitud,origen_longitud,distancia_km,tiempo_estimado_min,costo_delivery,ruta,vence_en)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (str(qid),empresa,actor['id'],cliente['id'],request.sucursal_id,Json(datos),huella(datos),sucursal['latitud'],sucursal['longitud'],distancia,tiempo,costo,Json(ruta),vence))
    return dict(cotizacion_id=str(qid),vence_en=vence.isoformat(),sucursal_id=request.sucursal_id,distancia_km=distancia,
        tiempo_estimado_min=tiempo,costo_delivery=costo,disponible=True,ruta=ruta,origen_latitud=sucursal['latitud'],origen_longitud=sucursal['longitud'])


def preparar(actor, request):
    if request is None or request.cotizacion_id is None:
        raise HTTPException(409,'Cotiza el envío y acepta su resumen antes de pagar.')
    empresa=exigir_empresa_id(actor)
    with _transaccion() as cur:
        cur.execute('SELECT * FROM delivery_cotizacion WHERE id=%s AND empresa_id=%s AND usuario_id=%s',(str(request.cotizacion_id),empresa,actor['id']))
        q=cur.fetchone()
        if not q:raise HTTPException(404,'Cotización no encontrada en tu cuenta y empresa.')
        if q['huella']!=huella(solicitud(request)):raise HTTPException(409,'La dirección o sucursal cambió. Solicita otra cotización.')
        if q['vence_en']<=datetime.now(timezone.utc):raise HTTPException(409,'La cotización venció. Actualízala antes de pagar.')
        if q['orden_pago_id'] is not None:raise HTTPException(409,'La cotización ya se usó. Consulta el pago anterior o cotiza un nuevo intento.')
        return {**q['solicitud'],'cotizacion_id':str(q['id']),'distancia_km':q['distancia_km'],
            'tiempo_estimado_min':q['tiempo_estimado_min'],'costo_delivery':q['costo_delivery']}


def vincular_cotizacion(cur, actor_id, empresa, orden, datos):
    cur.execute('''SELECT q.*,s.latitud lat,s.longitud lng FROM delivery_cotizacion q JOIN sucursal s ON s.id=q.sucursal_id AND s.empresa_id=q.empresa_id AND s.activo
        WHERE q.id=%s AND q.empresa_id=%s AND q.usuario_id=%s FOR UPDATE OF q''',(datos['cotizacion_id'],empresa,actor_id))
    q=cur.fetchone()
    if not q or q['orden_pago_id'] or q['vence_en']<=datetime.now(timezone.utc):raise HTTPException(409,'Cotización vencida o ya utilizada; actualiza el resumen.')
    if q['origen_latitud']!=q['lat'] or q['origen_longitud']!=q['lng']:raise HTTPException(409,'Cambió la ubicación de la sucursal. Vuelve a cotizar.')
    cur.execute('UPDATE orden_pago SET delivery_cotizacion_id=%s WHERE id=%s AND empresa_id=%s',(str(q['id']),orden['id'],empresa))
    cur.execute('UPDATE delivery_cotizacion SET orden_pago_id=%s WHERE id=%s',(orden['id'],str(q['id'])))


def pagada(cur, empresa, venta):
    cur.execute('''SELECT o.* FROM orden_pago o JOIN venta v ON v.id=o.venta_id AND v.empresa_id=o.empresa_id
        WHERE o.empresa_id=%s AND v.id=%s AND v.estado='COMPLETADA' AND v.tipo_entrega='DELIVERY'
        AND v.metodo_pago IN ('QR','TARJETA') AND o.estado='PAGADO' AND o.monto_total=v.total AND o.moneda='BOB'
        ORDER BY o.id DESC LIMIT 1''',(empresa,venta))
    return cur.fetchone()


def confirmar_cursor(cur, orden):
    from ..repositories.delivery_repository import crear_delivery_para_venta
    if not orden.get('delivery_cotizacion_id'):return
    cur.execute('SELECT * FROM delivery_cotizacion WHERE id=%s AND empresa_id=%s AND orden_pago_id=%s',
                (str(orden['delivery_cotizacion_id']),orden['empresa_id'],orden['id']))
    q=cur.fetchone()
    if not q:raise ValueError('La compra no conserva una cotización aceptada.')
    if not pagada(cur,orden['empresa_id'],orden['venta_id']):raise ValueError('La entrega requiere compra y envío pagados.')
    cur.execute('SELECT id FROM delivery WHERE empresa_id=%s AND venta_id=%s',(orden['empresa_id'],orden['venta_id']))
    if cur.fetchone():return
    d=crear_delivery_para_venta(cur,empresa_id=orden['empresa_id'],venta_id=orden['venta_id'],cliente_id=orden['cliente_id'],
        **{k:q['solicitud'][k] for k in ('sucursal_id','direccion_entrega','referencia','latitud_entrega','longitud_entrega')},
        distancia_km=q['distancia_km'],tiempo_estimado_min=q['tiempo_estimado_min'],costo_delivery=q['costo_delivery'])
    cur.execute('UPDATE delivery SET cotizacion_id=%s WHERE id=%s',(str(q['id']),d['id']))
    registrar(cur,d,None,'REGISTRO',None,'Entrega registrada con compra y envío pagados.')


def registrar(cur,d,uid,tipo,anterior,observacion):
    cur.execute('''INSERT INTO delivery_seguimiento(empresa_id,delivery_id,usuario_id,tipo,estado_anterior,estado_nuevo,observacion,version)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(d['empresa_id'],d['id'],uid,tipo,anterior,d['estado'],observacion,d.get('version',1)))
    from .pagos_empresa_service import notificar
    orden=pagada(cur,d['empresa_id'],d['venta_id'])
    if orden:
        notificar(cur,dict(orden),f"delivery:{d['id']}:{d.get('version',1)}",{'EN_CAMINO':'Delivery en camino','ENTREGADO':'Delivery entregado','EN_PREPARACION':'Delivery en preparación','CANCELADO':'Delivery cancelado','PENDIENTE':'Delivery registrado'}.get(d['estado'],'Actualización de entrega'),
                  f"Entrega #{d['id']}: {d['estado'].replace('_', ' ').lower()}. "+('Existe una incidencia pendiente de coordinación.' if d.get('incidencia_abierta') else 'Consulta el seguimiento desde Mis entregas.'))
    cur.execute("INSERT INTO bitacora(usuario_id,empresa_id,accion,modulo,descripcion) VALUES(%s,%s,%s,'DELIVERY',%s)",
                (uid,d['empresa_id'],tipo,f"Entrega {d['id']}: {d['estado']} · versión {d.get('version',1)}"))


def validar_staff(cur, actor, d, gestionar=True):
    empresa=exigir_empresa_id(actor)
    cur.execute('SELECT activo,version_acceso FROM usuario WHERE id=%s AND empresa_id=%s',(actor['id'],empresa))
    u=cur.fetchone()
    if not u or not u['activo'] or u['version_acceso']!=actor.get('version_acceso',0):raise HTTPException(401,'La cuenta cambió. Inicia sesión nuevamente.')
    cur.execute('SELECT r.nombre FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id AND r.activo WHERE ur.usuario_id=%s AND ur.activo',(actor['id'],))
    roles={r['nombre'] for r in cur.fetchall()}
    if 'ADMINISTRADOR' in roles:return
    permitidos={'ENCARGADO_SUCURSAL'} if gestionar else {'ENCARGADO_SUCURSAL','CAJERO'}
    if not roles.intersection(permitidos):raise HTTPException(403,'Sólo el administrador o encargado puede gestionar entregas.')
    cur.execute('SELECT id FROM empleado WHERE usuario_id=%s AND empresa_id=%s AND sucursal_id=%s AND activo',(actor['id'],empresa,d['sucursal_id']))
    if not cur.fetchone():raise HTTPException(404,'Entrega no encontrada en tu sucursal.')


def cambiar(actor,did,request,incidencia=False):
    empresa=exigir_empresa_id(actor)
    obs=(request.observacion or '').strip() or None
    with _transaccion() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ACCESOS,))
        cur.execute('SELECT id FROM empresa WHERE id=%s FOR UPDATE',(empresa,))
        cur.execute('SELECT * FROM delivery WHERE id=%s AND empresa_id=%s AND activo FOR UPDATE',(did,empresa))
        fila=cur.fetchone()
        if not fila:raise HTTPException(404,'Entrega no encontrada.')
        d=dict(fila);validar_staff(cur,actor,d)
        if not incidencia and request.estado==d['estado']:return
        if incidencia and request.version==d['version']-1:
            cur.execute('SELECT tipo,observacion,usuario_id FROM delivery_seguimiento WHERE delivery_id=%s AND version=%s',(did,d['version']))
            previo=cur.fetchone()
            if previo and previo['tipo']==('RESOLUCION' if request.resolver else 'INCIDENCIA') and previo['observacion']==obs and previo['usuario_id']==actor['id']:return
        if request.version!=d['version']:raise HTTPException(409,'El seguimiento cambió. Actualiza antes de guardar.')
        anterior=d['estado'];tipo='ESTADO'
        if incidencia:
            if not obs or len(obs)<5:raise HTTPException(422,'Describe la incidencia o su resolución.')
            if request.resolver and not d['incidencia_abierta']:raise HTTPException(409,'No hay una incidencia abierta para resolver.')
            if not request.resolver and d['incidencia_abierta']:raise HTTPException(409,'Ya existe una incidencia abierta; registra su resolución.')
            d['incidencia_abierta']=not request.resolver;tipo='RESOLUCION' if request.resolver else 'INCIDENCIA'
        else:
            if request.estado not in TRANSICIONES.get(anterior,[]):raise HTTPException(409,f'No se permite pasar de {anterior} a {request.estado}.')
            if not pagada(cur,empresa,d['venta_id']):raise HTTPException(409,'El despacho requiere compra y envío pagados con Stripe o QR.')
            if request.estado=='ENTREGADO' and (not request.recepcion_confirmada or d['incidencia_abierta']):
                raise HTTPException(409,'Resuelve la incidencia y confirma la entrega física antes de finalizar.')
            if request.estado=='CANCELADO':
                if not obs or len(obs)<5:raise HTTPException(422,'Indica el motivo de la cancelación.')
                d['incidencia_abierta']=True
            d['estado']=request.estado
        d['version']+=1
        cur.execute('''UPDATE delivery SET estado=%s,version=%s,incidencia_abierta=%s,observacion=COALESCE(%s,observacion),
            fecha_entrega=CASE WHEN %s='ENTREGADO' THEN CURRENT_TIMESTAMP ELSE fecha_entrega END,
            recepcion_confirmada_por=CASE WHEN %s='ENTREGADO' THEN %s ELSE recepcion_confirmada_por END,
            fecha_actualizacion=CURRENT_TIMESTAMP WHERE id=%s AND empresa_id=%s''',
            (d['estado'],d['version'],d['incidencia_abierta'],obs,d['estado'],d['estado'],actor['id'],did,empresa))
        registrar(cur,d,actor['id'],tipo,anterior,obs)
