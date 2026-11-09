"""Contratación y renovación mensual de planes en Stripe de prueba."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import stripe

from app.core.plan_empresa import normalizar_politica
from app.modules.administracion.repositories.empresa_saas_repository import _ajustar_programaciones
from app.modules.autenticacion.repositories.perfil_repository import _transaccion


EVENTOS = {
    'checkout.session.completed', 'checkout.session.expired',
    'invoice.paid', 'customer.subscription.deleted',
}
ZONA_EMPRESA = ZoneInfo('America/La_Paz')
MONEDAS_SIN_DECIMALES = {
    'bif', 'clp', 'djf', 'gnf', 'jpy', 'kmf', 'krw', 'mga',
    'pyg', 'rwf', 'vnd', 'vuv', 'xaf', 'xof', 'xpf',
}


def _clave():
    clave = os.getenv('STRIPE_SECRET_KEY', '').strip()
    return clave if clave.startswith('sk_test_') and not clave.startswith('sk_test_placeholder') else ''


def _stripe_configurado():
    return bool(_clave() and os.getenv('STRIPE_WEBHOOK_SECRET', '').strip())


def _frontend():
    return os.getenv('FRONTEND_URL', 'http://localhost:4200').rstrip('/') + '/mi-plan'


def _plan_actual(cur, empresa):
    cur.execute("""SELECT s.id AS suscripcion_id, s.plan_id, s.estado, s.fecha_inicio,
                          p.activo, p.stripe_price_id, p.precio, p.moneda, e.estado AS empresa_estado
                   FROM suscripcion s JOIN plan p ON p.id=s.plan_id
                   JOIN empresa e ON e.id=s.empresa_id
                   WHERE s.empresa_id=%s ORDER BY s.fecha_creacion DESC,s.id DESC LIMIT 1""", (empresa,))
    return cur.fetchone()


def _abierta(cur, empresa):
    cur.execute("""SELECT * FROM suscripcion_stripe
                   WHERE empresa_id=%s AND estado IN ('PENDIENTE','ACTIVA')""", (empresa,))
    return cur.fetchone()


def _empresa_elegible(plan):
    return bool(plan and plan['empresa_estado'] == 'ACTIVA' and plan['estado'] in ('ACTIVA', 'VENCIDA')
                and plan['fecha_inicio'] <= datetime.now(ZONA_EMPRESA).date())


def _precio_contratable(plan):
    return bool(plan and plan['activo'] and plan['stripe_price_id'] and plan['precio'] > 0)


def estado(empresa):
    with _transaccion(isolation_level='REPEATABLE READ', readonly=True) as cur:
        plan = _plan_actual(cur, empresa)
        abierta = _abierta(cur, empresa)
        actual_id = plan['plan_id'] if plan else None
        pendiente_id = None
        if abierta and abierta['estado'] == 'PENDIENTE':
            pendiente_id = abierta['plan_destino_id'] or actual_id
        cur.execute("""SELECT id,codigo,nombre,descripcion,precio,moneda,limites,funciones,activo,stripe_price_id
                       FROM plan WHERE id IN (%s,%s) OR (activo AND codigo <> 'STYLEAR_LEGADO' AND
                           (codigo IN ('BASICO','INTERMEDIO','PREMIUM') OR (precio>0 AND stripe_price_id IS NOT NULL)))
                       ORDER BY CASE codigo WHEN 'BASICO' THEN 0 WHEN 'INTERMEDIO' THEN 1
                                            WHEN 'PREMIUM' THEN 2 ELSE 3 END, id""", (actual_id, pendiente_id))
        opciones = cur.fetchall()
        cur.execute("""SELECT comprobante_url FROM suscripcion_stripe WHERE empresa_id=%s
                       AND comprobante_url IS NOT NULL ORDER BY pagado_hasta DESC LIMIT 1""", (empresa,))
        comprobante = cur.fetchone()
    habilitado = _stripe_configurado() and _empresa_elegible(plan)
    puede_continuar = habilitado and pendiente_id is not None and not abierta['stripe_suscripcion_id']
    planes = []
    for opcion in opciones:
        id_plan = opcion['id']
        puede_elegir = (puede_continuar and id_plan == pendiente_id) if abierta else (
            habilitado and _precio_contratable(opcion)
        )
        planes.append({
            **{clave: opcion[clave] for clave in ('id', 'codigo', 'nombre', 'descripcion', 'moneda')},
            **normalizar_politica(opcion),
            'precio': str(opcion['precio']),
            'es_actual': id_plan == actual_id,
            'puede_elegir': puede_elegir,
        })
    precio = abierta['importe'] if abierta else plan['precio'] if plan else None
    moneda = abierta['moneda'] if abierta else plan['moneda'] if plan else None
    return {
        'estado': abierta['estado'] if abierta else None,
        'importe': str(precio) if precio is not None else None,
        'moneda': moneda,
        'puede_pagar': habilitado and (
            puede_continuar if abierta else _precio_contratable(plan)
        ),
        'puede_gestionar': bool(_clave() and abierta and abierta['estado'] == 'ACTIVA'
                                and abierta['stripe_cliente_id']),
        'planes': planes,
        'plan_pendiente_id': pendiente_id,
        'comprobante_url': comprobante['comprobante_url'] if comprobante else None,
    }


def checkout(empresa, plan_id=None, *, usuario_pagador=None):
    if not _stripe_configurado():
        raise HTTPException(503, 'Configura Stripe de prueba y su webhook para cobrar la suscripción.')
    clave = _clave()
    ahora = datetime.now(timezone.utc)
    with _transaccion() as cur:
        cur.execute('SELECT id FROM empresa WHERE id=%s FOR UPDATE', (empresa,))
        plan = _plan_actual(cur, empresa)
        if not _empresa_elegible(plan):
            raise HTTPException(409, 'La empresa necesita un plan asignado y estar activa para contratar.')
        intento = _abierta(cur, empresa)
        if intento and intento['estado'] == 'ACTIVA':
            raise HTTPException(409, 'Esta empresa ya tiene una suscripción mensual en Stripe.')
        if intento and intento['checkout_expira'] and intento['checkout_expira'] <= ahora:
            # Sólo el webhook de expiración puede cerrar un intento que Stripe pudo cobrar.
            raise HTTPException(409, 'Stripe todavía está confirmando el cierre de la sesión anterior.')
        if intento and intento['stripe_suscripcion_id']:
            raise HTTPException(409, 'Stripe está confirmando el pago de la suscripción.')
        if intento and plan_id is not None and plan_id != (intento['plan_destino_id'] or plan['plan_id']):
            raise HTTPException(409, 'Ya hay un pago pendiente para otro plan.')
        if not intento:
            destino_id = plan_id if plan_id is not None and plan_id != plan['plan_id'] else None
            destino = plan
            if destino_id is not None:
                cur.execute("""SELECT activo,stripe_price_id,precio,moneda FROM plan
                               WHERE id=%s AND codigo <> 'STYLEAR_LEGADO'""", (destino_id,))
                destino = cur.fetchone()
            if not _precio_contratable(destino):
                raise HTTPException(409, 'El plan elegido no está habilitado para cobro mensual.')
            cur.execute("""INSERT INTO suscripcion_stripe
                           (id,empresa_id,suscripcion_id,plan_destino_id,stripe_price_id,importe,moneda,usuario_pagador_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid4()), empresa, plan['suscripcion_id'], destino_id,
                         destino['stripe_price_id'], destino['precio'], destino['moneda'], usuario_pagador))
            intento = cur.fetchone()
    if intento['checkout_url']:
        return {'url': intento['checkout_url']}
    if ahora - intento['creado_en'] > timedelta(hours=23):
        raise HTTPException(409, 'Revisa el intento de cobro pendiente en Stripe antes de volver a pagar.')
    metadata = {'stylear_suscripcion': str(intento['id']), 'empresa_id': str(empresa)}
    try:
        precio = stripe.Price.retrieve(intento['stripe_price_id'], api_key=clave)
        moneda = str(precio.get('currency', '')).lower()
        multiplicador = 1 if moneda in MONEDAS_SIN_DECIMALES else 100
        recurrente = precio.get('recurring') or {}
        if (not precio.get('active') or precio.get('livemode') is not False
                or recurrente.get('interval') != 'month' or recurrente.get('interval_count') != 1
                or recurrente.get('usage_type') != 'licensed'
                or moneda != intento['moneda'].lower()
                or precio.get('unit_amount') != Decimal(intento['importe']) * multiplicador):
            raise HTTPException(422, 'El precio Stripe debe ser mensual y coincidir con el importe y la moneda del plan.')
        sesion = stripe.checkout.Session.create(
            api_key=clave, idempotency_key=f'stylear-plan-{intento["id"]}',
            mode='subscription', payment_method_types=['card'],
            line_items=[{'price': intento['stripe_price_id'], 'quantity': 1}],
            metadata=metadata, subscription_data={'metadata': metadata},
            success_url=_frontend() + '?stripe=pendiente',
            cancel_url=_frontend(),
        )
    except (HTTPException, stripe.InvalidRequestError) as error:
        with _transaccion() as cur:
            cur.execute("""UPDATE suscripcion_stripe SET estado='CERRADA'
                           WHERE id=%s AND checkout_id IS NULL AND stripe_suscripcion_id IS NULL""", (intento['id'],))
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(422, 'Stripe no pudo usar el precio configurado para este plan.') from error
    except stripe.StripeError as error:
        raise HTTPException(503, 'Stripe no respondió. Puedes reintentar la misma operación.') from error
    with _transaccion() as cur:
        cur.execute("""UPDATE suscripcion_stripe SET checkout_id=%s,checkout_url=%s,
                       checkout_expira=to_timestamp(%s)
                       WHERE id=%s AND estado='PENDIENTE' RETURNING id""",
                    (sesion.id, sesion.url, sesion.expires_at, intento['id']))
        if not cur.fetchone():
            raise HTTPException(409, 'La sesión cambió de estado. Actualiza Mi plan.')
    return {'url': sesion.url}


def portal(empresa):
    with _transaccion() as cur:
        intento = _abierta(cur, empresa)
    clave = _clave()
    if not clave or not intento or not intento['stripe_cliente_id']:
        raise HTTPException(409, 'Esta empresa todavía no tiene una suscripción para gestionar.')
    try:
        sesion = stripe.billing_portal.Session.create(
            api_key=clave, customer=intento['stripe_cliente_id'], return_url=_frontend(),
        )
    except stripe.StripeError as error:
        raise HTTPException(503, 'No se pudo abrir el portal. Revisa su configuración en Stripe de prueba.') from error
    return {'url': sesion.url}


def procesar_evento(tipo, objeto):
    """Devuelve None si el evento no pertenece a la facturación SaaS."""
    if tipo not in EVENTOS:
        return None
    meta = objeto.get('metadata') or {}
    if tipo == 'invoice.paid':
        detalles = (objeto.get('parent') or {}).get('subscription_details') or objeto.get('subscription_details') or {}
        meta = detalles.get('metadata') or {}
        suscripcion = detalles.get('subscription') or objeto.get('subscription')
    elif tipo == 'customer.subscription.deleted':
        suscripcion = objeto.get('id')
    else:
        if objeto.get('mode') != 'subscription':
            return None
        suscripcion = objeto.get('subscription')
    referencia = meta.get('stylear_suscripcion')
    if not referencia:
        return None
    try:
        intento_id = str(UUID(referencia))
        empresa = int(meta['empresa_id'])
    except (TypeError, ValueError, KeyError) as error:
        raise HTTPException(409, 'Referencia de suscripción Stripe inválida.') from error
    with _transaccion() as cur:
        cur.execute('SELECT id FROM empresa WHERE id=%s FOR UPDATE', (empresa,))
        cur.execute('SELECT * FROM suscripcion_stripe WHERE id=%s AND empresa_id=%s FOR UPDATE', (intento_id, empresa))
        intento = cur.fetchone()
        if not intento:
            return {'status': 'ignorado'}
        if suscripcion and intento['stripe_suscripcion_id'] not in (None, suscripcion):
            raise HTTPException(409, 'La suscripción Stripe no coincide con el intento guardado.')
        if tipo.startswith('checkout.session.'):
            if intento['checkout_id'] not in (None, objeto.get('id')):
                raise HTTPException(409, 'La sesión Stripe no coincide con el intento guardado.')
            if tipo == 'checkout.session.expired':
                cur.execute("""UPDATE suscripcion_stripe SET estado='CERRADA'
                               WHERE id=%s AND estado='PENDIENTE' AND stripe_suscripcion_id IS NULL""", (intento_id,))
            elif suscripcion and intento['estado'] != 'CERRADA':
                cur.execute("""UPDATE suscripcion_stripe SET checkout_id=%s,
                               stripe_suscripcion_id=%s,stripe_cliente_id=COALESCE(%s,stripe_cliente_id) WHERE id=%s""",
                            (objeto['id'], suscripcion, objeto.get('customer'), intento_id))
        elif tipo == 'customer.subscription.deleted':
            cur.execute("""UPDATE suscripcion_stripe SET estado='CERRADA',stripe_suscripcion_id=%s
                           WHERE id=%s""", (suscripcion, intento_id))
        elif suscripcion and objeto.get('status') == 'paid':
            _aplicar_periodo(cur, intento, suscripcion, objeto)
    return {'status': 'ok'}


def _activar_plan_destino(cur, intento, vigencia):
    empresa = intento['empresa_id']
    actual = _plan_actual(cur, empresa)
    cur.execute('SELECT id,activo,limites,funciones,codigo FROM plan WHERE id=%s',
                (intento['plan_destino_id'],))
    destino = cur.fetchone()
    hoy = datetime.now(ZONA_EMPRESA).date()
    if (not actual or actual['suscripcion_id'] != intento['suscripcion_id']
            or actual['estado'] not in ('ACTIVA', 'VENCIDA') or not destino or not destino['activo']
            or vigencia < hoy):
        raise HTTPException(409, 'El pago no coincide con el cambio de plan pendiente.')
    cur.execute("UPDATE suscripcion SET estado='CANCELADA' WHERE id=%s", (actual['suscripcion_id'],))
    cur.execute("""INSERT INTO suscripcion (empresa_id,plan_id,fecha_inicio,fecha_vencimiento,estado)
                   VALUES (%s,%s,%s,%s,'ACTIVA') RETURNING id""",
                (empresa, destino['id'], hoy, vigencia))
    nueva = cur.fetchone()['id']
    _ajustar_programaciones(cur, empresa, normalizar_politica(destino))
    return nueva


def _comprobante(factura):
    url = factura.get('hosted_invoice_url')
    if isinstance(url, str):
        partes = urlsplit(url)
        if partes.scheme == 'https' and partes.netloc in ('invoice.stripe.com', 'pay.stripe.com'):
            return url
    return None


def _aplicar_periodo(cur, intento, suscripcion, factura):
    lineas = (factura.get('lines') or {}).get('data', [])
    periodos = []
    for linea in lineas:
        precio = (linea.get('price') or {}).get('id')
        precio = precio or ((linea.get('pricing') or {}).get('price_details') or {}).get('price')
        detalles = (linea.get('parent') or {}).get('subscription_item_details') or {}
        periodo = linea.get('period') or {}
        fin = periodo.get('end')
        if (precio == intento['stripe_price_id'] and not linea.get('proration')
                and not detalles.get('proration') and isinstance(fin, int)):
            periodos.append(periodo)
    if not periodos:
        raise HTTPException(409, 'La factura no incluye el periodo del precio contratado.')
    periodo = max(periodos, key=lambda p: p['end'])
    hasta = datetime.fromtimestamp(periodo['end'], timezone.utc)
    anterior = intento['pagado_hasta']
    if anterior and hasta <= anterior:
        return
    if intento['estado'] == 'CERRADA' and not intento['stripe_suscripcion_id']:
        return
    cur.execute("""SELECT id FROM suscripcion_stripe WHERE empresa_id=%s
                   ORDER BY creado_en DESC,id DESC LIMIT 1""", (intento['empresa_id'],))
    if cur.fetchone()['id'] != intento['id']:
        return
    vigencia = (hasta - timedelta(microseconds=1)).astimezone(ZONA_EMPRESA).date()
    suscripcion_id = intento['suscripcion_id']
    if intento['plan_destino_id'] and not anterior:
        moneda = intento['moneda'].lower()
        multiplicador = 1 if moneda in MONEDAS_SIN_DECIMALES else 100
        pagado = factura.get('amount_paid')
        if (factura.get('currency') != moneda or not isinstance(pagado, int)
                or pagado < Decimal(intento['importe']) * multiplicador):
            raise HTTPException(409, 'El importe o la moneda de la factura no corresponde al plan contratado.')
        suscripcion_id = _activar_plan_destino(cur, intento, vigencia)
    else:
        cur.execute("""UPDATE suscripcion SET estado='ACTIVA', fecha_vencimiento=%s
                       WHERE id=%s AND empresa_id=%s AND estado IN ('ACTIVA','VENCIDA')
                         AND fecha_inicio<=%s""",
                    (vigencia, suscripcion_id, intento['empresa_id'], vigencia))
        if not cur.rowcount:
            raise HTTPException(409, 'El periodo pagado no corresponde a la suscripción asignada.')
    cur.execute("""UPDATE suscripcion_stripe SET stripe_suscripcion_id=%s,stripe_cliente_id=COALESCE(%s,stripe_cliente_id),
                   suscripcion_id=%s,pagado_hasta=%s,comprobante_url=COALESCE(%s,comprobante_url),
                   estado=CASE WHEN estado='CERRADA' THEN estado ELSE 'ACTIVA' END
                   WHERE id=%s""", (suscripcion, factura.get('customer'), suscripcion_id, hasta,
                                    _comprobante(factura), intento['id']))
    _encolar_pago_plan(cur, intento, factura, suscripcion_id, periodo, vigencia)


def _encolar_pago_plan(cur, intento, factura, suscripcion_id, periodo, vigencia):
    from app.modules.ventas_inventario.services.comprobante_pago_service import encolar
    from app.modules.ventas_inventario.services.notificaciones_service import emitir_usuario

    pagado = factura.get('amount_paid')
    if not isinstance(pagado, int) or pagado <= 0:
        return
    empresa = intento['empresa_id']
    cur.execute('''
        SELECT e.nombre AS empresa, p.nombre AS plan, s.fecha_inicio
        FROM suscripcion s JOIN empresa e ON e.id=s.empresa_id
        JOIN plan p ON p.id=s.plan_id WHERE s.id=%s AND s.empresa_id=%s
    ''', (suscripcion_id, empresa))
    datos_plan = cur.fetchone()
    cur.execute('''
        SELECT u.id, u.correo FROM usuario u
        WHERE u.empresa_id=%s AND u.activo AND EXISTS (
            SELECT 1 FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id
            WHERE ur.usuario_id=u.id AND ur.activo AND r.activo AND r.nombre='ADMINISTRADOR'
        )
        ORDER BY (u.id=%s) DESC NULLS LAST, u.id LIMIT 1
    ''', (empresa, intento.get('usuario_pagador_id')))
    admin = cur.fetchone()
    moneda = str(factura.get('currency') or intento['moneda']).lower()
    total = Decimal(pagado) / (1 if moneda in MONEDAS_SIN_DECIMALES else 100)
    inicio = periodo.get('start')
    desde = (datetime.fromtimestamp(inicio, timezone.utc).astimezone(ZONA_EMPRESA).date()
             if isinstance(inicio, int) else datos_plan['fecha_inicio'])
    fecha_pago = (factura.get('status_transitions') or {}).get('paid_at')
    fecha = (datetime.fromtimestamp(fecha_pago, timezone.utc) if isinstance(fecha_pago, int)
             else datetime.now(timezone.utc))
    referencia = factura.get('number') or factura.get('id') or f'PLAN-{suscripcion_id}'
    clave = f"plan:{intento['id']}:{periodo['end']}"
    encolar(cur, empresa, clave, admin['correo'] if admin else None, {
        'titulo': 'Pago del plan de empresa', 'referencia': referencia,
        'empresa': datos_plan['empresa'], 'emisor': 'StyleAR',
        'fecha': fecha, 'moneda': moneda.upper(), 'total': total, 'metodo': 'Tarjeta · Stripe',
        'referencias': [('Período cubierto', f'{desde:%d/%m/%Y} al {vigencia:%d/%m/%Y}')],
        'lineas': [(f"Plan {datos_plan['plan']}", 1, total)],
    })
    if admin:
        emitir_usuario(cur, empresa, admin['id'], clave, 'Pago del plan confirmado',
                       f"Se confirmó el pago del plan {datos_plan['plan']} hasta el {vigencia:%d/%m/%Y}.",
                       'PAGO')
