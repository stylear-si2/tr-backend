"""Instantánea comercial: los cambios futuros del catálogo no reescriben notas."""
import json
from psycopg2.extras import Json, RealDictCursor
from app.database.connection import get_connection


def emitir_nota_cursor(cur, venta_id, empresa_id):
    cur.execute('SELECT id FROM venta WHERE id=%s AND empresa_id=%s FOR UPDATE', (venta_id,empresa_id))
    if not cur.fetchone():
        raise ValueError('Venta no encontrada.')
    cur.execute('SELECT numero,datos FROM nota_venta WHERE venta_id=%s AND empresa_id=%s', (venta_id,empresa_id))
    existente = cur.fetchone()
    if existente:
        return dict(existente)
    cur.execute('''SELECT v.codigo,v.subtotal,v.descuento,v.total,v.metodo_pago,v.fecha_venta,
        v.tipo_entrega,v.costo_delivery,
        e.nombre AS empresa,e.codigo AS empresa_codigo,e.comprobante_config,
        s.nombre AS sucursal,s.direccion,s.telefono,
        concat_ws(' ',u.nombre,u.apellido) AS cliente,u.correo,
        concat_ws(' ',vendedor.nombre,vendedor.apellido) AS vendedor
        FROM venta v JOIN empresa e ON e.id=v.empresa_id
        JOIN sucursal s ON s.id=v.sucursal_id AND s.empresa_id=v.empresa_id
        LEFT JOIN cliente cl ON cl.id=v.cliente_id AND cl.empresa_id=v.empresa_id
        LEFT JOIN usuario u ON u.id=cl.usuario_id AND u.empresa_id=v.empresa_id
        LEFT JOIN usuario vendedor ON vendedor.id=v.usuario_id AND vendedor.empresa_id=v.empresa_id
        WHERE v.id=%s AND v.empresa_id=%s AND v.estado='COMPLETADA' ''', (venta_id,empresa_id))
    row = cur.fetchone()
    if row is None:
        raise ValueError('La nota solo puede emitirse para una venta completada.')
    datos = dict(row)
    cur.execute('''SELECT vd.producto_variante_id,p.nombre AS producto,t.nombre AS talla,
        c.nombre AS color,vd.tipo_compra,vd.paquete_origen_id,vd.descuento_mayorista,vd.cantidad,vd.precio_unitario,vd.descuento,vd.subtotal
        FROM venta_detalle vd
        JOIN producto_variante pv ON pv.id=vd.producto_variante_id AND pv.empresa_id=vd.empresa_id
        JOIN producto p ON p.id=pv.producto_id AND p.empresa_id=vd.empresa_id
        LEFT JOIN talla t ON t.id=pv.talla_id AND t.empresa_id=vd.empresa_id
        LEFT JOIN color c ON c.id=pv.color_id AND c.empresa_id=vd.empresa_id
        WHERE vd.venta_id=%s AND vd.empresa_id=%s ORDER BY vd.id''', (venta_id,empresa_id))
    datos['detalles'] = [dict(d) for d in cur.fetchall()]
    datos = json.loads(json.dumps(datos,default=str))
    cur.execute('''INSERT INTO numeracion_nota_venta(empresa_id,ultimo) VALUES(%s,1)
        ON CONFLICT(empresa_id) DO UPDATE SET ultimo=numeracion_nota_venta.ultimo+1
        RETURNING ultimo''', (empresa_id,))
    numero = int(cur.fetchone()['ultimo'])
    cur.execute('INSERT INTO nota_venta(empresa_id,venta_id,numero,datos) VALUES(%s,%s,%s,%s)',
                (empresa_id,venta_id,numero,Json(datos)))
    return {'numero':numero,'datos':datos}


def obtener_nota(venta_id, empresa_id):
    conn=get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            nota=emitir_nota_cursor(cur,venta_id,empresa_id)
        conn.commit()
        return nota
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
