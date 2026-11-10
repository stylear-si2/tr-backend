import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.core.contexto_empresa import exigir_empresa_id
from app.core.config import AI_REPORTS_PROVIDER
from app.modules.inteligencia.repositories.reporte_repository import (
    actualizar_estado_reporte_programado,
    consultar_reporte,
    crear_reporte_programado,
    eliminar_reporte_programado,
    listar_reportes_programados,
    listar_sucursales,
    marcar_ejecucion_reporte_programado,
)
from app.modules.inteligencia.services.reporte_periodo import MESES, rango_mes_explicito
from app.modules.inteligencia.services.reporte_vertex_service import interpretar_con_vertex
from app.modules.inteligencia.schemas.reportes.reporte_request import (
    CrearReporteProgramadoRequest,
    ReporteRequest,
)

TITULOS = {
    "VENTAS": "Ventas registradas",
    "VENTAS_POR_SUCURSAL": "Ventas por sucursal y período",
    "INGRESOS_POR_CATEGORIA": "Ingresos por categoría",
    "PRODUCTOS_MAS_VENDIDOS": "Productos más vendidos",
    "INVENTARIO": "Inventario por sucursal",
    "RESERVAS": "Reservas",
    "MOVIMIENTOS": "Movimientos de inventario",
    "TRANSFERENCIAS": "Transferencias de stock",
    "PAGOS": "Historial de pagos",
    "DELIVERIES": "Pedidos con delivery",
    "USUARIOS": "Usuarios registrados",
}
MONETARIOS = {"subtotal", "descuento", "total", "importe"}


def catalogo_reportes(empresa_id: int,usuario=None) -> dict[str, object]:
    from app.modules.inteligencia.repositories.reporte_repository import catalogo_comercial
    sucursal=limitar_reporte(usuario) if usuario is not None else None
    return {"tipos": [{"id": key, "nombre": title} for key, title in TITULOS.items() if not (sucursal and key=='USUARIOS')],
            "sucursales": [s for s in listar_sucursales(empresa_id) if sucursal is None or s['id']==sucursal], **catalogo_comercial(empresa_id)}


def limitar_reporte(usuario,request=None):
    from app.modules.inteligencia.repositories.reporte_repository import sucursal_encargado
    e=exigir_empresa_id(usuario)
    roles=set(usuario.get('roles',[]))
    if 'ADMIN_SAAS' in roles:
        raise HTTPException(403,'Los reportes comerciales requieren una cuenta de empresa.')
    if 'ADMINISTRADOR' in roles:
        return request
    if 'ENCARGADO_SUCURSAL' not in roles:
        raise HTTPException(403,'No tienes acceso a reportes.')
    sucursal=sucursal_encargado(e,int(usuario['id']))
    if sucursal is None:
        raise HTTPException(403,'No tienes una sucursal activa asignada.')
    if request is None:
        return sucursal
    if getattr(request,'tipo',None)=='USUARIOS':
        raise HTTPException(403,'El reporte de usuarios está reservado al administrador.')
    if request.sucursal_id not in (None,sucursal):
        raise HTTPException(403,'Solo puedes consultar tu sucursal.')
    return request.model_copy(update={'sucursal_id':sucursal})


def generar_detalle(request,empresa_id):
    _validar_sucursal_reporte(request,empresa_id)
    datos=consultar_reporte(request,empresa_id,paginacion=(request.pagina,request.tamano))
    r=datos['resumen']
    respuesta = {'tipo':request.tipo,'titulo':TITULOS[request.tipo],
        'filtros_efectivos':request.model_dump(mode='json'),'generado_en':datetime.now(ZoneInfo('America/La_Paz')).isoformat(),
        'pagina':request.pagina,'tamano':request.tamano,'filas':datos['filas'],
        'total_filas':r['total_filas'],'resumen':r,'serie_grafico':datos['serie_grafico'],
        'sin_datos':r['total_filas']==0,
        'columnas':[{'key':k,'label':k.replace('_',' ').title()} for k in datos['filas'][0]] if datos['filas'] else [],
        'nota': ('Los importes corresponden a prendas vendidas, con descuentos proporcionales y sin delivery. Son ventas registradas, no utilidad ni saldo neto de devoluciones.' if request.tipo in {'PRODUCTOS_MAS_VENDIDOS','VENTAS_POR_SUCURSAL','INGRESOS_POR_CATEGORIA'} else 'El filtro de producto o categoría selecciona ventas que contienen esas prendas; los importes incluyen toda la venta.' if request.tipo=='VENTAS' and (request.categoria_id or request.producto_id) else 'Los totales corresponden al resultado completo; el inventario es actual y los importes de órdenes son nominales.')}

    from .reporte_analisis import firma_resultado
    respuesta['firma_resultado']=firma_resultado(respuesta)
    return respuesta



def generar_dashboard(request,empresa_id):
    _validar_sucursal_reporte(request,empresa_id)
    from app.modules.inteligencia.repositories.reporte_repository import consultar_dashboard
    desde_anterior=request.fecha_desde-timedelta(days=(request.fecha_hasta-request.fecha_desde).days+1)
    anterior=request.model_copy(update={'fecha_desde':desde_anterior,'fecha_hasta':request.fecha_desde-timedelta(days=1),'comparar':False})
    datos=consultar_dashboard(request,empresa_id,anterior if request.comparar else None)
    respuesta={'filtros':request.model_dump(mode='json'),'datos':datos['actual'],
        'generado_en':datetime.now(ZoneInfo('America/La_Paz')).isoformat(),
        'definiciones':{
            'ventas':'Registros activos COMPLETADA, por fecha de venta; WEB se presenta como DIGITAL.',
            'importe_vendido':'Suma de total de las ventas completadas, en BOB; no representa una utilidad.',
            'cobrado':'Órdenes con pago recibido o confirmado, por fecha de pago; caja presencial por saldo no representado en órdenes.',
            'fecha_cobro_legacy':'Si falta fecha de pago se utiliza la actualización de la orden y, en último término, su creación.',
            'neto':'Flujo de cobros registrados menos reembolsos confirmados en el período, separado por moneda; no es ganancia.',
            'reservas':'Estado actual de reservas creadas en el período.',
            'inventario':'Existencias actuales; no se reconstruye ni compara stock histórico.',
            'tipo_venta':'El canal filtra ventas y flujos monetarios; reservas e inventario se limitan por empresa y sucursal.'}}
    if request.comparar:
        comparaciones={}
        for k in ('ventas','ventas_presenciales','ventas_digitales','importe_vendido','reservas_pendientes','reservas_vencidas'):
            comparaciones[k]=comparar_valores(datos['actual'][k],datos['anterior'][k])
        monedas=sorted({f['moneda'] for d in (datos['actual'],datos['anterior']) for f in d['flujos']})
        for moneda in monedas:
            for k in ('cobrado','reembolsado','neto'):
                valores=[next((f[k] for f in d['flujos'] if f['moneda']==moneda),'0') for d in (datos['actual'],datos['anterior'])]
                comparaciones[f'{k}_{moneda}']=comparar_valores(*valores)
        respuesta['comparacion']={'fecha_desde':anterior.fecha_desde,'fecha_hasta':anterior.fecha_hasta,
                                  'datos_anteriores':datos['anterior'],'indicadores':comparaciones}
    return respuesta


def comparar_valores(actual,anterior):
    a,b=Decimal(str(actual)),Decimal(str(anterior))
    return {'actual':str(a),'anterior':str(b),'diferencia':str(a-b),
            'variacion_porcentual':str(((a-b)/abs(b)*100).quantize(Decimal('.01'))) if b!=0 else None,
            'motivo':None if b!=0 else 'BASE_ANTERIOR_CERO'}


def _validar_sucursal_reporte(request,empresa_id):
    from app.modules.inteligencia.repositories.reporte_repository import catalogo_comercial
    if getattr(request,'categoria_id',None) or getattr(request,'producto_id',None):
        catalogo = catalogo_comercial(empresa_id)
        if request.categoria_id and not any(c['id']==request.categoria_id for c in catalogo['categorias']):
            raise HTTPException(422,'La categoría no pertenece a esta empresa o está inactiva.')
        if request.producto_id and not any(p['id']==request.producto_id and (not request.categoria_id or p['categoria_id']==request.categoria_id) for p in catalogo['productos']):
            raise HTTPException(422,'El producto no corresponde a esta empresa y categoría.')
    if request.sucursal_id is not None and not any(s['id']==request.sucursal_id for s in listar_sucursales(empresa_id)):
        raise HTTPException(422,'La sucursal seleccionada no existe o está inactiva.')




def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _interpretar_por_reglas(texto: str, empresa_id: int, sucursal_permitida=None) -> dict[str, object]:
    normal = _normalizar(texto)
    hoy = datetime.now(ZoneInfo("America/La_Paz")).date()
    inicio, fin = hoy.replace(day=1), hoy
    if "semana pasada" in normal:
        fin = hoy - timedelta(days=hoy.weekday() + 1)
        inicio = fin - timedelta(days=6)
    elif "esta semana" in normal:
        inicio = hoy - timedelta(days=hoy.weekday())
    elif "este ano" in normal:
        inicio = hoy.replace(month=1, day=1)
    elif "mes pasado" in normal:
        fin = hoy.replace(day=1) - timedelta(days=1)
        inicio = fin.replace(day=1)
    elif "principio de mes" in normal or "inicio de mes" in normal or "comienzo de mes" in normal or "este mes" in normal:
        inicio = hoy.replace(day=1)
    elif "hoy" in normal:
        inicio = fin = hoy
    fechas = re.findall(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", normal)
    if len(fechas) == 2:
        try:
            inicio, fin = (date(int(a[2]), int(a[1]), int(a[0])) for a in fechas)
        except ValueError:
            return {"interpretado": False, "advertencias": ["Las fechas dictadas no son válidas."], "filtros": None}
    rango_texto = re.search(r"\bdel?\s+(\d{1,2})\s+al\s+(\d{1,2})\s+de\s+(\w+)(?:\s+de\s+(\d{4}))?", normal)
    mes_completo = None
    if rango_texto and rango_texto.group(3) in MESES:
        try:
            ano = int(rango_texto.group(4) or hoy.year)
            mes = MESES[rango_texto.group(3)]
            inicio = date(ano, mes, int(rango_texto.group(1)))
            fin = date(ano, mes, int(rango_texto.group(2)))
        except ValueError:
            return {"interpretado": False, "advertencias": ["El rango dictado no es válido."], "filtros": None}
    elif not fechas:
        mes_completo = rango_mes_explicito(texto, hoy)
        if mes_completo:
            inicio, fin = mes_completo
    palabras = [
        ("PRODUCTOS_MAS_VENDIDOS", ("mas vendido", "mas vendidos", "mejor vendido", "mejor vendidos", "top producto", "top productos")),
        ("VENTAS", ("venta", "ventas", "ingreso", "ingresos")),
        ("INVENTARIO", ("inventario", "inventarios", "bajo stock", "existencia", "existencias")),
        ("RESERVAS", ("reserva", "reservas")),
        ("PAGOS", ("pago", "pagos", "stripe", "tarjeta")),
        ("DELIVERIES", ("delivery", "deliveries", "envio", "envios", "entrega", "domicilio")),
        ("USUARIOS", ("usuario", "usuarios", "cuenta", "cuentas", "cliente", "clientes", "cliente registrado", "clientes registrados")),
        ("MOVIMIENTOS", ("movimiento", "movimientos", "entrada", "entradas", "salida", "salidas", "ajuste", "ajustes")),
        ("TRANSFERENCIAS", ("transferencia", "transferencias", "traslado", "traslados")),
    ]
    tipo = next((key for key, opciones in palabras if any(re.search(r"\b" + re.escape(p) + r"\b", normal) for p in opciones)), None)
    if not tipo:
        return {"interpretado": False, "advertencias": ["No se reconoció un tipo de reporte. Prueba con ventas, inventario, reservas, movimientos o transferencias."], "filtros": None}
    tiene_fecha_explicita = bool(
        fechas
        or rango_texto
        or mes_completo
        or any(p in normal for p in ("hoy", "esta semana", "semana pasada", "este mes", "mes pasado", "este ano", "este año"))
    )
    if tipo == "USUARIOS" and not tiene_fecha_explicita:
        inicio = date(2020, 1, 1)
        fin = hoy
    sucursal_id = None
    sucursales = [s for s in listar_sucursales(empresa_id) if sucursal_permitida is None or s['id']==sucursal_permitida]
    for sucursal in sucursales:
        nombre = _normalizar(str(sucursal["nombre"]))
        if nombre in normal:
            sucursal_id = sucursal["id"]
            break
    if "sucursal" in normal and sucursal_id is None and not any(p in normal for p in ("todas", "por sucursal", "las sucursales", "entre sucursales")):
        return {"interpretado": False, "advertencias": ["No se reconoció la sucursal. Selecciónala en los filtros."], "filtros": None}
    estado = None
    if "pendiente" in normal:
        estado = "PENDIENTE" if tipo in {"PAGOS", "DELIVERIES"} else "PENDIENTE_PAGO" if tipo == "VENTAS" else "PENDIENTE"
    elif "pagado" in normal or "pagadas" in normal or "pagados" in normal:
        estado = "PAGADO" if tipo == "PAGOS" else "COMPLETADA"
    elif "rechaz" in normal:
        estado = "RECHAZADO" if tipo == "PAGOS" else "RECHAZADA"
    elif "cancel" in normal or "anulad" in normal:
        estado = "CANCELADO" if tipo in {"PAGOS", "DELIVERIES"} else "ANULADA"
    elif "entregado" in normal or "entregados" in normal:
        estado = "ENTREGADO"
    elif "camino" in normal:
        estado = "EN_CAMINO"
    elif "preparacion" in normal or "preparación" in normal:
        estado = "EN_PREPARACION"
    tipo_entrega = "DELIVERY" if any(p in normal for p in ("delivery", "domicilio", "envio", "envio a domicilio")) else None
    metodo_pago = "TARJETA" if "tarjeta" in normal else None
    proveedor_pago = "STRIPE" if "stripe" in normal else None
    rol = None
    if "administrador" in normal or "administradores" in normal:
        rol = "ADMINISTRADOR"
    elif "encargado" in normal or "encargados" in normal:
        rol = "ENCARGADO_SUCURSAL"
    elif "cliente" in normal or "clientes" in normal:
        rol = "CLIENTE"
    activo = False if "inactivo" in normal or "inactivos" in normal else True if "activo" in normal or "activos" in normal else None
    try:
        filtros = ReporteRequest(
            tipo=tipo,
            fecha_desde=inicio,
            fecha_hasta=fin,
            sucursal_id=sucursal_id,
            solo_bajo_stock="bajo stock" in normal,
            estado=estado,
            metodo_pago=metodo_pago,
            proveedor_pago=proveedor_pago,
            tipo_entrega=tipo_entrega,
            rol=rol,
            activo=activo,
        )
    except ValueError:
        return {"interpretado": False, "advertencias": ["El rango de fechas no es válido o supera 366 días."], "filtros": None}
    return {"interpretado": True, "advertencias": [], "texto_normalizado": normal, "filtros": filtros.model_dump(mode="json"), "motor": "reglas"}


def _interpretar_base(texto: str, empresa_id: int, sucursal_permitida=None) -> dict[str, object]:
    hoy = datetime.now(ZoneInfo("America/La_Paz")).date()
    if AI_REPORTS_PROVIDER in {"VERTEX", "GEMINI"}:
        try:
            from app.modules.inteligencia.repositories.reporte_repository import catalogo_comercial
            return interpretar_con_vertex(texto, hoy, [s for s in listar_sucursales(empresa_id) if sucursal_permitida is None or s['id']==sucursal_permitida], catalogo_comercial(empresa_id))
        except Exception as exc:
            resultado = _interpretar_por_reglas(texto, empresa_id, sucursal_permitida)
            advertencias = list(resultado.get("advertencias") or [])
            advertencias.append(f"{AI_REPORTS_PROVIDER.title()} no respondió; se usó interpretación local.")
            resultado["advertencias"] = advertencias
            resultado["motor"] = "reglas_fallback"
            return resultado
    return _interpretar_por_reglas(texto, empresa_id, sucursal_permitida)


def interpretar(texto: str, empresa_id: int, sucursal_permitida=None) -> dict[str, object]:
    if any(x in _normalizar(texto) for x in ('ganancia','utilidad','rentabilidad','salario','impuesto')):
        return dict(interpretado=False,filtros=None,advertencias=['Ese cálculo no está disponible. Puedes consultar ventas, ingresos, inventario, reservas o pagos.'])
    resultado = _interpretar_base(texto, empresa_id, sucursal_permitida)
    normal = _normalizar(texto)
    # Intenciones que el contrato representa de forma explícita, incluso en el respaldo local.
    tipo = ('INGRESOS_POR_CATEGORIA' if 'categoria' in normal and any(x in normal for x in ('ingreso','generaron','recaud','por categoria'))
            else 'VENTAS_POR_SUCURSAL' if 'sucursal' in normal and any(x in normal for x in ('agrupad','compar','por sucursal')) else None)
    if tipo and not resultado.get('interpretado'):
        resultado = _interpretar_por_reglas('ventas ' + texto, empresa_id, sucursal_permitida)
    if not resultado.get('interpretado') or not resultado.get('filtros'):
        return resultado
    f = dict(resultado['filtros'])
    if tipo:
        if f.get('estado') not in (None,'COMPLETADA','PAGADO'):
            return dict(interpretado=False,filtros=None,advertencias=['Los reportes agrupados de ingresos y ventas por sucursal incluyen ventas completadas. Para otros estados usa el detalle de ventas.'])
        f['tipo'] = tipo
        f['estado'] = None
        if tipo=='VENTAS_POR_SUCURSAL': f['agrupacion']='MES'
    if any(x in normal for x in ('por dia','diario','cada dia')):
        f['agrupacion'] = 'DIA'
    elif 'seman' in normal and any(x in normal for x in ('por semana','semanal','cada semana')):
        f['agrupacion'] = 'SEMANA'
    elif any(x in normal for x in ('por mes','mensual','cada mes','ultimos tres meses','ultimos 3 meses')):
        f['agrupacion'] = 'MES'
    if any(x in normal for x in ('ultimos tres meses','ultimos 3 meses')):
        hoy = datetime.now(ZoneInfo('America/La_Paz')).date()
        mes = hoy.year*12+hoy.month-1-2
        f['fecha_desde'] = date(mes//12,mes%12+1,1).isoformat()
        f['fecha_hasta'] = hoy.isoformat()
    top = re.search(r'\b(?:top|primeros|los)\s+(\d+|diez|cinco|veinte)\s*(?:productos)?',normal)
    if f['tipo']=='PRODUCTOS_MAS_VENDIDOS' and top:
        n = top.group(1)
        f['limite'] = int(n) if n.isdigit() else {'diez':10,'cinco':5,'veinte':20}[n]
    if any(x in normal for x in ('digital','web')): f['tipo_venta']='DIGITAL'
    elif 'presencial' in normal: f['tipo_venta']='PRESENCIAL'
    if re.search(r'\bqr\b',normal): f['metodo_pago']='QR'
    elif 'efectivo' in normal: f['metodo_pago']='EFECTIVO'
    if f['tipo']=='INVENTARIO' and any(x in normal for x in ('menor existencia','menores existencias','poco stock','stock bajo')):
        f['solo_bajo_stock'] = any(x in normal for x in ('poco stock','stock bajo'))
    # Resolver únicamente nombres de entidades de la empresa, nunca IDs inventados por el modelo.
    entidades = [('categoria_id','categoria'),('producto_id','producto')]
    for campo, palabra in entidades:
        nombre = re.search(r'\b(?:de la |de |del |en la |en el )?'+palabra+r'\s+["\']?(.+?)(?:["\']|\s+(?:entre|desde|durante|este|en septiembre|en octubre|en noviembre|del mes|de este mes)|$)', normal)
        if nombre or f.get(campo):
            from app.modules.inteligencia.repositories.reporte_repository import catalogo_comercial
            catalogo = catalogo_comercial(empresa_id)
            opciones = catalogo['categorias' if palabra=='categoria' else 'productos']
            if nombre:
                buscado = nombre.group(1).strip(' .')
                coincidencias = [v for v in opciones if _normalizar(v['nombre'])==buscado]
                if len(coincidencias)!=1:
                    return dict(interpretado=False,filtros=None,advertencias=[f'No se pudo identificar una única {palabra}: {buscado}. Selecciónala en los filtros.'])
                f[campo] = coincidencias[0]['id']
    try:
        validado = ReporteRequest(**f)
        if validado.categoria_id or validado.producto_id:
            _validar_sucursal_reporte(validado,empresa_id)
    except (ValueError,HTTPException):
        return dict(interpretado=False,filtros=None,advertencias=['Los criterios solicitados no son compatibles o no pertenecen a tu empresa. Revisa los filtros.'])
    if not re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{4}', normal) and not any(x in normal for x in (*MESES, 'hoy','semana','mes','ano')):
        resultado['advertencias'] = [*resultado.get('advertencias', []), 'No se indicó un período: se usa el mes actual hasta hoy. Puedes cambiar las fechas antes de generar.']
    resultado['filtros'] = validado.model_dump(mode='json')
    return resultado


def _presentar(valor: object) -> object:
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, datetime):
        return valor.astimezone(ZoneInfo("America/La_Paz")).isoformat()
    return valor


def generar_reporte(request: ReporteRequest, empresa_id: int) -> dict[str, object]:
    _validar_sucursal_reporte(request,empresa_id)
    sucursales = listar_sucursales(empresa_id)
    if request.sucursal_id is not None and not any(s["id"] == request.sucursal_id for s in sucursales):
        raise HTTPException(status_code=422, detail="La sucursal seleccionada no existe o está inactiva.")
    rows = consultar_reporte(request, empresa_id)
    if len(rows) > 5000:
        raise HTTPException(status_code=422, detail="La consulta completa supera 5000 filas. Usa el detalle paginado o reduce el período para exportar.")
    filas = [{key: _presentar(value) for key, value in row.items()} for row in rows]
    medibles=[r for r in rows if request.tipo!='VENTAS' or r.get('estado','COMPLETADA')=='COMPLETADA']
    total = sum((Decimal(str(row.get("total", row.get("monto_total", row.get("importe",0))) or 0)) for row in medibles), Decimal(0))
    por_moneda=defaultdict(Decimal)
    for row in medibles:
        por_moneda[str(row.get('moneda') or 'BOB')]+=Decimal(str(row.get('total',row.get('monto_total',row.get('importe',0))) or 0))
    if not por_moneda:
        por_moneda['BOB']=Decimal(0)
    unidades = sum(int(row.get("real", row.get("unidades", row.get("cantidad", 0))) or 0) for row in medibles)
    bajo_stock = sum(1 for row in rows if row.get("estado") == "BAJO STOCK") if request.tipo == "INVENTARIO" else 0
    serie: dict[str, float] = defaultdict(float)
    if request.tipo in {"VENTAS", "RESERVAS", "MOVIMIENTOS", "TRANSFERENCIAS", "PAGOS", "DELIVERIES", "USUARIOS"}:
        for row in rows:
            if request.tipo=='VENTAS' and row.get('estado','COMPLETADA')!='COMPLETADA':
                continue
            fecha = row["fecha"].astimezone(ZoneInfo("America/La_Paz"))
            key = fecha.strftime({'MES':'%Y-%m','SEMANA':'%G-W%V','DIA':'%Y-%m-%d'}[request.agrupacion])
            if request.tipo=='PAGOS':
                key+=' · '+str(row.get('moneda') or 'BOB')
            serie[key] += float(row.get("costo_delivery") or 0) if request.tipo=="DELIVERIES" else float(row.get("total", row.get("monto_total", 1)) or (0 if request.tipo in {"VENTAS", "RESERVAS", "PAGOS"} else 1))
    elif request.tipo == "PRODUCTOS_MAS_VENDIDOS":
        serie = {row['producto']: int(row['unidades']) for row in rows}
    elif request.tipo in {'VENTAS_POR_SUCURSAL','INGRESOS_POR_CATEGORIA'}:
        serie = {str(row['periodo'])+' · '+row['sucursal'] if request.tipo=='VENTAS_POR_SUCURSAL' else row['categoria']: float(row['importe']) for row in rows}
    else:
        serie = {"Bajo stock": bajo_stock, "Stock OK": len(rows) - bajo_stock}
    return {
        "tipo": request.tipo,
        "titulo": TITULOS[request.tipo],
        "importes_por_moneda": [{'moneda':m,'importe':str(v)} for m,v in sorted(por_moneda.items())],
        "nota": "Importes de prendas vendidas con descuentos proporcionales y sin delivery; no equivalen a utilidad ni descuentan devoluciones posteriores." if request.tipo in {"PRODUCTOS_MAS_VENDIDOS","VENTAS_POR_SUCURSAL","INGRESOS_POR_CATEGORIA"} else "El inventario muestra el estado actual; no existe historial de snapshots para reconstruirlo por fechas." if request.tipo == "INVENTARIO" else "El valor nominal incluye reservas de todos los estados; no equivale a dinero cobrado." if request.tipo == "RESERVAS" else "Órdenes por fecha de creación, con importes nominales; el flujo cobrado se consulta en el dashboard." if request.tipo=='PAGOS' else "Los indicadores de ventas incluyen solo registros completados." if request.tipo=='VENTAS' else None,
        "filtros_efectivos": {**request.model_dump(mode="json"), "sucursal": next((str(s["nombre"]) for s in sucursales if s["id"] == request.sucursal_id), "Todas")},
        "generado_en": datetime.now(ZoneInfo("America/La_Paz")).isoformat(),
        "indicadores": ([{'label':'Unidades vendidas','valor':unidades},{'label':'Importe vendido (Bs)','valor':float(total)}] if request.tipo in {'VENTAS_POR_SUCURSAL','INGRESOS_POR_CATEGORIA'} else [{"label": "Ventas completadas", "valor": len(medibles)}, {"label": "Unidades vendidas", "valor": unidades}, {"label": "Total ventas (Bs)", "valor": float(total)}] if request.tipo == "VENTAS" else
                        [{"label": "Reservas", "valor": len(rows)}, {"label": "Unidades solicitadas", "valor": unidades}, {"label": "Valor nominal (Bs)", "valor": float(total)}] if request.tipo == "RESERVAS" else
                        [{"label": "Órdenes", "valor": len(rows)}, *[{'label':f'Importe nominal de órdenes ({m})','valor':float(v)} for m,v in sorted(por_moneda.items())]] if request.tipo == "PAGOS" else
                        [{"label": "Deliveries", "valor": len(rows)}, {"label": "Costo delivery (Bs)", "valor": float(sum((Decimal(str(row.get("costo_delivery", 0) or 0)) for row in rows), Decimal(0)))}] if request.tipo == "DELIVERIES" else
                        [{"label": "Usuarios", "valor": len(rows)}, {"label": "Activos", "valor": sum(1 for row in rows if row.get("activo") is True)}, {"label": "Inactivos", "valor": sum(1 for row in rows if row.get("activo") is False)}] if request.tipo == "USUARIOS" else
                        [{"label": "Variantes", "valor": len(rows)}, {"label": "Stock real", "valor": unidades}, {"label": "Bajo stock", "valor": bajo_stock}] if request.tipo == "INVENTARIO" else
                        [{"label": "Registros", "valor": len(rows)}, {"label": "Unidades", "valor": unidades}] if request.tipo in {"PRODUCTOS_MAS_VENDIDOS", "TRANSFERENCIAS"} else
                        [{"label": "Movimientos", "valor": len(rows)}]),
        "serie_grafico": [{"label": key, "valor": value} for key, value in (serie.items() if request.tipo in {'PRODUCTOS_MAS_VENDIDOS','INGRESOS_POR_CATEGORIA','VENTAS_POR_SUCURSAL'} else sorted(serie.items()))],
        "columnas": [{"key": key, "label": key.replace("_", " ").title()} for key in filas[0]] if filas else [],
        "filas": filas,
        "total_filas": len(filas),
        "sin_datos": not filas,
    }


def _presentar_programado(item: dict[str, object]) -> dict[str, object]:
    return {
        **item,
        "creado_en": item["creado_en"].astimezone(ZoneInfo("America/La_Paz")).isoformat() if item.get("creado_en") else None,
        "ultima_ejecucion": item["ultima_ejecucion"].astimezone(ZoneInfo("America/La_Paz")).isoformat() if item.get("ultima_ejecucion") else None,
        "proxima_ejecucion": item["proxima_ejecucion"].astimezone(ZoneInfo("America/La_Paz")).isoformat() if item.get("proxima_ejecucion") else None,
    }


def listar_programados_service(usuario_actual: dict[str, object]) -> list[dict[str, object]]:
    return [_presentar_programado(item) for item in listar_reportes_programados(exigir_empresa_id(usuario_actual))]


def crear_programado_service(request: CrearReporteProgramadoRequest, usuario_actual: dict[str, object]) -> dict[str, object]:
    empresa_id = exigir_empresa_id(usuario_actual)
    if request.sucursal_id is not None and not any(s["id"] == request.sucursal_id for s in listar_sucursales(empresa_id)):
        raise HTTPException(status_code=422, detail="La sucursal seleccionada no pertenece a la empresa.")
    usuario_id = int(usuario_actual["id"]) if "id" in usuario_actual else None
    item = crear_reporte_programado(request.model_dump(), empresa_id, usuario_id)
    return _presentar_programado(item)


def editar_programado_service(programado_id,request,usuario):
    from app.modules.inteligencia.repositories.reporte_repository import editar_reporte_programado
    return _presentar_programado(editar_reporte_programado(programado_id,exigir_empresa_id(usuario),request.revision,request.cambios))


def actualizar_estado_programado_service(programado_id: int, activo: bool, usuario_actual: dict[str, object]) -> dict[str, object]:
    item = actualizar_estado_reporte_programado(programado_id, activo, exigir_empresa_id(usuario_actual))
    if not item:
        raise HTTPException(status_code=404, detail="Programación de reporte no encontrada.")
    return _presentar_programado(item)


def eliminar_programado_service(programado_id: int, usuario_actual: dict[str, object]) -> dict[str, str]:
    ok = eliminar_reporte_programado(programado_id, exigir_empresa_id(usuario_actual))
    if not ok:
        raise HTTPException(status_code=404, detail="Programación de reporte no encontrada.")
    return {"status": "ok", "mensaje": "Programación eliminada exitosamente."}



def ejecutar_programado_service(programado_id: int, usuario_actual: dict[str, object]) -> dict[str, object]:
    return _ejecutar_programacion(programado_id,exigir_empresa_id(usuario_actual),manual=True)


def _ejecutar_programacion(programado_id, empresa_id, manual=False):
    from datetime import timezone
    from psycopg2.extras import RealDictCursor
    from app.database.connection import get_connection
    from app.core.plan_empresa import _leer_plan,normalizar_politica
    from app.core.email import enviar_correo_con_adjunto
    from app.modules.inteligencia.services.programacion_reporte import proxima_ejecucion,periodo_reporte
    from app.modules.inteligencia.exports.reporte_pdf import crear_pdf
    from app.modules.inteligencia.exports.reporte_excel import crear_excel
    conn=get_connection()
    ejecucion_id=None
    item=None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended('cu26-reporte:' || %s::text,0)) AS adquirido",(programado_id,))
            if not cur.fetchone()['adquirido']:
                raise HTTPException(409,'El reporte ya se está ejecutando.')
        conn.commit()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            plan=_leer_plan(cur,empresa_id,bloquear=True,validar_vigencia=False)
            cur.execute('SELECT * FROM reporte_programado WHERE id=%s AND empresa_id=%s FOR UPDATE',(programado_id,empresa_id))
            row=cur.fetchone()
            if not row:
                raise HTTPException(404,'Programación de reporte no encontrada.')
            item=dict(row)
            _validar_envio_programado(cur,item,plan,manual)
            ahora=datetime.now(timezone.utc)
            ocurrencia=ahora if manual else item['proxima_ejecucion']
            if not manual and (ocurrencia is None or ocurrencia>ahora):
                return {'estado':'NO_VENCIDO'}
            revision_intentada=item['revision']
            cur.execute("""INSERT INTO reporte_ejecucion(reporte_id,ocurrencia,estado,reporte_revision)
                VALUES(%s,%s,'INICIADO',%s) ON CONFLICT(reporte_id,ocurrencia) DO NOTHING RETURNING id""",
                (programado_id,ocurrencia,revision_intentada))
            run=cur.fetchone()
            if not run:
                # Un intento previo puede haber enviado antes de perder la conexión.
                # No se reenvía automáticamente una ocurrencia ya reclamada.
                cur.execute('UPDATE reporte_programado SET proxima_ejecucion=%s WHERE id=%s AND empresa_id=%s',
                            (proxima_ejecucion(item,ahora),programado_id,empresa_id))
                conn.commit()
                return {'estado':'YA_INTENTADO','mensaje':'Consulta el historial antes de reintentar manualmente.'}
            ejecucion_id=run['id']
        conn.commit()
        # Revalidar bajo el cerrojo del plan después de persistir el intento.
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            plan=_leer_plan(cur,empresa_id,bloquear=True,validar_vigencia=False)
            cur.execute('SELECT * FROM reporte_programado WHERE id=%s AND empresa_id=%s FOR UPDATE',(programado_id,empresa_id))
            item=dict(cur.fetchone())
            if item['revision']!=revision_intentada:
                raise HTTPException(409,'La programación cambió después de iniciar el intento; no se envió.')
            _validar_envio_programado(cur,item,plan,manual)
            desde,hasta=periodo_reporte(item,ocurrencia)
            request=ReporteRequest(tipo=item['tipo'],fecha_desde=desde,fecha_hasta=hasta,
                sucursal_id=item.get('sucursal_id'),solo_bajo_stock=item['solo_bajo_stock'],**(item.get('filtros') or {}))
            reporte=generar_reporte(request,empresa_id)
            pdf=item['formato']=='PDF'
            contenido=crear_pdf(reporte) if pdf else crear_excel(reporte)
            extension='pdf' if pdf else 'xlsx'
            enviar_correo_con_adjunto(item['destinatario_email'],item['titulo'],
                f"Reporte del {desde} al {hasta}.",contenido,f"reporte-{programado_id}.{extension}", 'StyleAR')
            cur.execute("""UPDATE reporte_ejecucion SET estado='ENVIADO',terminado_en=CURRENT_TIMESTAMP
                WHERE id=%s""",(ejecucion_id,))
            cur.execute("""UPDATE reporte_programado SET ultima_ejecucion=CURRENT_TIMESTAMP,proxima_ejecucion=%s
                WHERE id=%s AND empresa_id=%s RETURNING *""",(proxima_ejecucion(item),programado_id,empresa_id))
            actualizado=dict(cur.fetchone())
        conn.commit()
        return {'estado':'ENVIADO','programado':_presentar_programado(actualizado),'reporte':reporte,
                'mensaje':'Reporte generado y enviado mediante el transporte de correo configurado.'}
    except Exception:
        conn.rollback()
        if ejecucion_id is not None:
            with conn.cursor() as cur:
                cur.execute("""UPDATE reporte_ejecucion SET estado='FALLIDO',terminado_en=CURRENT_TIMESTAMP,
                    detalle='No se confirmó el envío. Revise antes de reintentar para evitar duplicados.' WHERE id=%s""",(ejecucion_id,))
                if item is not None and not manual:
                    cur.execute('UPDATE reporte_programado SET proxima_ejecucion=%s WHERE id=%s AND empresa_id=%s',
                                (proxima_ejecucion(item),programado_id,empresa_id))
            conn.commit()
        raise
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtextextended('cu26-reporte:' || %s::text,0))",(programado_id,))
        finally:
            conn.close()


def _validar_envio_programado(cur,item,plan,manual):
    from app.core.plan_empresa import normalizar_politica
    if not item['activo']:
        raise HTTPException(409,'La programación está pausada.')
    if not plan['operaciones_habilitadas']:
        raise HTTPException(403,'La suscripción no está vigente para enviar reportes programados.')
    funciones=normalizar_politica(plan)['funciones']
    formato='reportes_pdf' if item['formato']=='PDF' else 'reportes_excel'
    if not funciones['reportes_programacion'] or not funciones[formato]:
        raise HTTPException(403,'La programación o su formato ya no están incluidos en el plan.')
    if not manual:
        cur.execute("""SELECT 1 FROM usuario u JOIN usuario_rol ur ON ur.usuario_id=u.id AND ur.activo
            JOIN rol r ON r.id=ur.rol_id AND r.activo
            WHERE u.id=%s AND u.empresa_id=%s AND u.activo AND r.nombre='ADMINISTRADOR' LIMIT 1""",
            (item['creado_por'],item['empresa_id']))
        if not cur.fetchone():
            raise HTTPException(403,'El responsable del reporte ya no es administrador activo de esta empresa.')


def ejecutar_reportes_programados():
    import logging
    from datetime import timezone
    from psycopg2.extras import RealDictCursor
    from app.database.connection import get_connection
    from app.modules.inteligencia.services.programacion_reporte import proxima_ejecucion
    conn=get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT pg_try_advisory_lock(2026110527) AS adquirido')
            if not cur.fetchone()['adquirido']:
                return
            cur.execute("""SELECT rp.* FROM reporte_programado rp JOIN empresa e ON e.id=rp.empresa_id
                WHERE rp.activo AND e.estado='ACTIVA' ORDER BY rp.id""")
            filas=[dict(r) for r in cur.fetchall()]
        for item in filas:
            try:
                if item['proxima_ejecucion'] is None:
                    with conn.cursor() as cur:
                        cur.execute("""UPDATE reporte_programado SET proxima_ejecucion=%s
                            WHERE id=%s AND proxima_ejecucion IS NULL""",(proxima_ejecucion(item),item['id']))
                    conn.commit()
                elif item['proxima_ejecucion'] <= datetime.now(timezone.utc):
                    _ejecutar_programacion(item['id'],item['empresa_id'])
            except HTTPException:
                # Restricción vigente: se conserva la programación y no se envía.
                conn.rollback()
            except (ValueError,KeyError):
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("""UPDATE reporte_programado SET activo=FALSE,pausa_motivo='CONFIGURACION_INVALIDA'
                        WHERE id=%s""",(item['id'],))
                conn.commit()
            except Exception:
                conn.rollback()
                logging.getLogger(__name__).error('No se confirmó el reporte programado %s; consulte su historial.',item['id'])
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(2026110527)')
        finally:
            conn.close()
