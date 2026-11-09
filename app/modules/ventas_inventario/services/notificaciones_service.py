"""Bandeja propia y registro privado de instalaciones FCM."""
import os
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Literal
from app.core.contexto_empresa import exigir_empresa_id
from app.modules.autenticacion.repositories.perfil_repository import _transaccion

Categoria = Literal['GENERAL','COMPRA','PAGO','RESERVA','ENTREGA','CAMBIO','DEVOLUCION','REEMBOLSO']
CATEGORIAS = ('GENERAL','COMPRA','PAGO','RESERVA','ENTREGA','CAMBIO','DEVOLUCION','REEMBOLSO')
CANAL_ANDROID = 'stylear_transacciones_v1'
DESTINOS = {'VENTA':'venta','ORDEN_PAGO':'orden_pago','RESERVA':'reserva','DELIVERY':'delivery',
            'CAMBIO':'devolucion','DEVOLUCION':'devolucion'}


class PreferenciasRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    categorias: dict[Categoria, StrictBool]


def emitir_cliente(cur, empresa_id, cliente_id, clave, titulo, mensaje, categoria, recurso_tipo, recurso_id, orden_id=None):
    cur.execute('SELECT usuario_id FROM cliente WHERE id=%s AND empresa_id=%s AND activo', (cliente_id,empresa_id))
    cliente=cur.fetchone()
    if not cliente:
        return None
    return emitir_usuario(cur, empresa_id, cliente['usuario_id'], clave, titulo, mensaje,
                          categoria, recurso_tipo, recurso_id, orden_id)


def emitir_usuario(cur, empresa_id, uid, clave, titulo, mensaje, categoria, recurso_tipo=None, recurso_id=None, orden_id=None):
    """La bandeja y su salida se insertan en la transacción del hecho comercial."""
    if categoria not in CATEGORIAS or (
        (recurso_tipo is not None or recurso_id is not None)
        and (recurso_tipo not in DESTINOS or int(recurso_id or 0) <= 0)
    ):
        raise ValueError('Categoría o destino de notificación inválido.')
    cur.execute('''INSERT INTO notificacion_usuario(empresa_id,usuario_id,clave,titulo,mensaje,orden_id,
        categoria,recurso_tipo,recurso_id,accion) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(empresa_id,usuario_id,clave) DO NOTHING RETURNING id''',
        (empresa_id,uid,clave,titulo,mensaje,orden_id,categoria,recurso_tipo,recurso_id,
         'VER_DETALLE' if recurso_tipo else None))
    nueva=cur.fetchone()
    if nueva:
        cur.execute('''INSERT INTO salida_push(notificacion_id,dispositivo_id,expira_en)
            SELECT %s,d.id,CURRENT_TIMESTAMP+INTERVAL '24 hours' FROM dispositivo_push d
            WHERE d.usuario_id=%s AND d.empresa_id=%s AND d.activo
            AND NOT EXISTS(SELECT 1 FROM notificacion_preferencia p WHERE p.usuario_id=d.usuario_id
                AND p.empresa_id=d.empresa_id AND p.categoria=%s AND NOT p.push_habilitado)
            ON CONFLICT DO NOTHING''',(nueva['id'],uid,empresa_id,categoria))
        return nueva['id']
    return None


def avisar_reserva(cur,reserva_id,evento):
    cur.execute('SELECT id,empresa_id,cliente_id,estado FROM reserva WHERE id=%s',(reserva_id,))
    reserva=cur.fetchone()
    if reserva:
        emitir_cliente(cur,reserva['empresa_id'],reserva['cliente_id'],f'reserva:{reserva_id}:{evento}',
                       'Actualización de reserva',f"Tu reserva está en estado {reserva['estado']}.",
                       'RESERVA','RESERVA',reserva_id)


def bandeja(actor,pagina=1,tamano=20,leida=None,categoria=None):
    if pagina<1 or not 1<=tamano<=100 or (categoria is not None and categoria not in CATEGORIAS):
        raise HTTPException(422,'Filtros de bandeja inválidos.')
    empresa=exigir_empresa_id(actor)
    with _transaccion() as cur:
        cur.execute('''WITH propias AS MATERIALIZED (
            SELECT * FROM notificacion_usuario WHERE empresa_id=%s AND usuario_id=%s),
            filtradas AS MATERIALIZED (SELECT * FROM propias WHERE (%s::boolean IS NULL OR leida=%s)
                AND (%s::text IS NULL OR categoria=%s))
            SELECT (SELECT count(*) FROM filtradas) AS total,
                (SELECT count(*) FROM propias WHERE NOT leida) AS no_leidas,
                coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.id DESC) FROM
                    (SELECT * FROM filtradas ORDER BY id DESC LIMIT %s OFFSET %s) p),'[]'::jsonb) AS avisos''',
            (empresa,actor['id'],leida,leida,categoria,categoria,tamano,(pagina-1)*tamano))
        return {**dict(cur.fetchone()),'pagina':pagina,'tamano':tamano}


def resumen(actor):
    with _transaccion() as cur:
        cur.execute('''SELECT count(*) AS total,count(*) FILTER(WHERE NOT leida) AS no_leidas
            FROM notificacion_usuario WHERE empresa_id=%s AND usuario_id=%s''',(exigir_empresa_id(actor),actor['id']))
        return dict(cur.fetchone())


def trazabilidad(actor,nid):
    with _transaccion() as cur:
        cur.execute('''SELECT n.id,n.leida,n.leida_en,n.creado_en,
            count(s.id) FILTER(WHERE s.estado='ENVIADO') AS aceptadas_fcm,
            count(s.id) FILTER(WHERE s.estado='PENDIENTE') AS pendientes,
            count(s.id) FILTER(WHERE s.estado='FALLIDO') AS fallidas,
            count(s.id) FILTER(WHERE s.estado='OMITIDO') AS omitidas,
            max(s.aceptado_en) AS ultima_aceptacion
            FROM notificacion_usuario n LEFT JOIN salida_push s ON s.notificacion_id=n.id
            WHERE n.id=%s AND n.empresa_id=%s AND n.usuario_id=%s GROUP BY n.id''',
            (nid,exigir_empresa_id(actor),actor['id']))
        fila=cur.fetchone()
        if not fila:
            raise HTTPException(404,'Notificación no encontrada.')
        return {**dict(fila),'visualizacion_sistema':'NO_ACREDITADA_POR_FCM'}


def leer_todas(actor):
    with _transaccion() as cur:
        cur.execute('''UPDATE notificacion_usuario SET leida=TRUE,leida_en=coalesce(leida_en,CURRENT_TIMESTAMP)
            WHERE empresa_id=%s AND usuario_id=%s AND NOT leida''',(exigir_empresa_id(actor),actor['id']))
        return {'actualizadas':cur.rowcount}


def preferencias(actor,datos=None):
    empresa=exigir_empresa_id(actor)
    with _transaccion() as cur:
        if datos is not None:
            for categoria,habilitada in datos.categorias.items():
                cur.execute('''INSERT INTO notificacion_preferencia(empresa_id,usuario_id,categoria,push_habilitado)
                    VALUES(%s,%s,%s,%s) ON CONFLICT(empresa_id,usuario_id,categoria)
                    DO UPDATE SET push_habilitado=EXCLUDED.push_habilitado,actualizado_en=CURRENT_TIMESTAMP''',
                    (empresa,actor['id'],categoria,habilitada))
        cur.execute('SELECT categoria,push_habilitado FROM notificacion_preferencia WHERE empresa_id=%s AND usuario_id=%s',
                    (empresa,actor['id']))
        valores={r['categoria']:r['push_habilitado'] for r in cur.fetchall()}
        return {'categorias':{c:valores.get(c,True) for c in CATEGORIAS},'bandeja_transaccional':True}


def destino(actor,nid):
    from psycopg2 import sql
    empresa=exigir_empresa_id(actor)
    with _transaccion() as cur:
        cur.execute('SELECT * FROM notificacion_usuario WHERE id=%s AND empresa_id=%s AND usuario_id=%s',
                    (nid,empresa,actor['id']))
        n=cur.fetchone()
        if not n:
            raise HTTPException(404,'Notificación no encontrada.')
        tabla=DESTINOS.get(n['recurso_tipo'])
        if not tabla:
            return {'disponible':False,'accion':'ABRIR_BANDEJA'}
        cur.execute(sql.SQL('''SELECT 1 FROM {} r JOIN cliente c ON c.id=r.cliente_id AND c.empresa_id=r.empresa_id
            WHERE r.id=%s AND r.empresa_id=%s AND c.usuario_id=%s AND c.activo
            AND (to_jsonb(r)->>'activo')::boolean IS DISTINCT FROM FALSE''').format(sql.Identifier(tabla)),
            (n['recurso_id'],empresa,actor['id']))
        if not cur.fetchone():
            return {'disponible':False,'accion':'ABRIR_BANDEJA'}
        if n['recurso_tipo']=='CAMBIO':
            cur.execute('SELECT 1 FROM cambio_prenda WHERE id=%s',(n['recurso_id'],))
            if not cur.fetchone():
                return {'disponible':False,'accion':'ABRIR_BANDEJA'}
        return {'disponible':True,'accion':'VER_DETALLE','recurso_tipo':n['recurso_tipo'],'recurso_id':n['recurso_id']}


class DispositivoRequest(BaseModel):
    instalacion_id: UUID
    token: str = Field(min_length=20,max_length=4096)
    plataforma: Literal['android','ios','web']


def listar(actor):
    with _transaccion() as cur:
        cur.execute('''SELECT n.* FROM notificacion_usuario n WHERE empresa_id=%s AND usuario_id=%s ORDER BY id DESC LIMIT 100''',
                    (exigir_empresa_id(actor),actor['id']))
        return [dict(f) for f in cur.fetchall()]


def leer(actor,nid):
    with _transaccion() as cur:
        cur.execute('UPDATE notificacion_usuario SET leida=TRUE,leida_en=coalesce(leida_en,CURRENT_TIMESTAMP) WHERE id=%s AND empresa_id=%s AND usuario_id=%s RETURNING id',
                    (nid,exigir_empresa_id(actor),actor['id']))
        if not cur.fetchone():raise HTTPException(404,'Notificación no encontrada.')
    return {'mensaje':'Notificación leída.'}


def registrar(actor,datos):
    with _transaccion() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('push:'+str(datos.instalacion_id),))
        # Una instalación vuelve a vincularse al cambiar de cuenta. El UUID permanece privado en el dispositivo.
        cur.execute('SELECT id,instalacion_id FROM dispositivo_push WHERE token=%s FOR UPDATE',(datos.token,))
        previo = cur.fetchone()
        if previo and str(previo['instalacion_id'])!=str(datos.instalacion_id):
            raise HTTPException(409,'El token ya pertenece a otra instalación.')
        cur.execute('''INSERT INTO dispositivo_push(empresa_id,usuario_id,instalacion_id,token,plataforma,version_acceso,sesion_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(instalacion_id) DO UPDATE SET empresa_id=EXCLUDED.empresa_id,
            usuario_id=EXCLUDED.usuario_id,token=EXCLUDED.token,plataforma=EXCLUDED.plataforma,
            version_acceso=EXCLUDED.version_acceso,sesion_id=EXCLUDED.sesion_id,activo=TRUE,actualizado_en=CURRENT_TIMESTAMP''',
            (exigir_empresa_id(actor),actor['id'],str(datos.instalacion_id),datos.token,datos.plataforma,actor.get('version_acceso',0),actor['_sesion_id']))
    return {'mensaje':'Instalación vinculada a la sesión actual.'}


def retirar(actor,instalacion):
    with _transaccion() as cur:
        cur.execute('UPDATE dispositivo_push SET activo=FALSE WHERE instalacion_id=%s AND empresa_id=%s AND usuario_id=%s',
                    (str(instalacion),exigir_empresa_id(actor),actor['id']))
    return {'mensaje':'Instalación desvinculada.'}


def estado():
    return {'push_configurado':bool(os.getenv('STYLEAR_FIREBASE_PROJECT_ID') and os.getenv('STYLEAR_FIREBASE_CREDENTIALS')),
            'bandeja_disponible':True,'android_canal_id':CANAL_ANDROID,
            'aceptacion_fcm_no_equivale_a_visualizacion':True}
