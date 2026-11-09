"""Respaldos cifrados. Restauración selectiva en la empresa original; global en destino vacío.

No se admite una ruta o conexión proporcionada por el navegador. La configuración
de almacenamiento y del destino de ensayo pertenece al operador de la plataforma.
"""

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID, uuid4
from urllib.parse import urlsplit, unquote
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import parse_dsn
from psycopg2.extras import RealDictCursor
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.core.config import DATABASE_URL
from app.database.connection import get_connection
from app.core.plan_empresa import LOCK_PLANES, _leer_plan, normalizar_politica
from app.modules.administracion.repositories.empresa_saas_repository import auditar, _ajustar_programaciones

STATIC = Path(__file__).resolve().parents[4] / "static"
LOGGER = logging.getLogger(__name__)
PLATAFORMA = {"empresa", "plan", "suscripcion", "suscripcion_stripe", "plan_historial", "evento_plataforma", "respaldo", "respaldo_programacion", "schema_migrations", "solicitud_registro_empresa"}
SOLO_GLOBAL = PLATAFORMA | {'comprobante_pago_envio'}


def configuracion():
    import boto3
    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    bucket = os.getenv("R2_BUCKET_NAME")
    endpoint = os.getenv("R2_ENDPOINT_URL")
    clave = os.getenv("STYLEAR_BACKUP_KEY")
    try:
        dias = int(os.getenv("STYLEAR_BACKUP_RETENTION_DAYS", "30"))
        if not 1 <= dias <= 3650 or not all((account_id, access_key, secret_key, bucket, endpoint, clave)):
            raise ValueError()
        if not endpoint.startswith(f"https://{account_id}.") or not bucket.replace("-", "").isalnum():
            raise ValueError()
        cifrador = Fernet(clave.encode("ascii"))
    except (ValueError, KeyError, UnicodeError) as error:
        raise HTTPException(503, "Configure las variables R2 y STYLEAR_BACKUP_KEY para respaldos privados.") from error
    cliente = boto3.client(
        "s3", endpoint_url=endpoint, region_name=os.getenv("R2_REGION", "auto"),
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
    )
    return cliente, bucket, cifrador, dias


def _politica_plan(cur, empresa_id):
    plan = _leer_plan(cur,empresa_id,validar_vigencia=False)
    politica=normalizar_politica(plan)
    limites,funciones=politica['limites'],politica['funciones']
    return {'plan_codigo':plan['plan_codigo'],'manuales_semana':limites['respaldos_manuales_semana'],
            'retencion_dias':limites['retencion_respaldos_dias'],
            'frecuencia_dias':limites['frecuencia_respaldo_dias'] if funciones['respaldos_automaticos'] else None,
            'horario_editable':funciones['horario_respaldo_configurable'],
            'manuales_habilitados':funciones['respaldos_manuales']}


def respaldo_vencido(programacion, politica, ahora):
    if politica['frecuencia_dias'] is None or not programacion.get('activa',True):
        return False
    zona=ZoneInfo(programacion.get('zona_horaria','America/La_Paz') if politica['horario_editable'] else 'America/La_Paz')
    local=ahora.astimezone(zona)
    hora=str(programacion.get('hora_local','02:00'))[:5] if politica['horario_editable'] else '02:00'
    if local.date()<programacion.get('fecha_inicio',local.date()) or local.strftime('%H:%M')<hora:
        return False
    ultimo=programacion.get('ultimo_respaldo')
    return ultimo is None or (local.date()-ultimo.astimezone(zona).date()).days>=politica['frecuencia_dias']


def _json(valor):
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _versiones(cur):
    cur.execute("SELECT version FROM schema_migrations ORDER BY version")
    return [r["version"] for r in cur.fetchall()]


def _esquema(cur):
    cur.execute("""SELECT table_name,column_name,is_nullable,data_type FROM information_schema.columns
                   WHERE table_schema='public' ORDER BY table_name,ordinal_position""")
    tablas = {}
    for r in cur.fetchall():
        tablas.setdefault(r["table_name"], {})[r["column_name"]] = dict(r)
    cur.execute("""SELECT tc.table_name,kcu.column_name,ccu.table_name AS padre,ccu.column_name AS columna_padre
                   FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu USING (constraint_catalog,constraint_schema,constraint_name)
                   JOIN information_schema.constraint_column_usage ccu USING (constraint_catalog,constraint_schema,constraint_name)
                   WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'""")
    fks = [dict(r) for r in cur.fetchall()]
    # Algunas referencias de empresa (p. ej. reporte_programado.creado_por)
    # se validan con triggers y no tienen FK declarada. También condicionan el
    # orden de restauración y deben anularse/reponerse cuando son opcionales.
    cur.execute("""SELECT c.relname AS tabla,t.tgargs AS argumentos FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE n.nspname='public' AND p.proname='validar_empresa_de_padre' AND NOT t.tgisinternal""")
    existentes={(r['table_name'],r['column_name'],r['padre']) for r in fks}
    for r in cur.fetchall():
        args=bytes(r['argumentos']).split(b'\x00')
        if len(args)<2:
            continue
        padre,columna=args[0].decode(),args[1].decode()
        clave=(r['tabla'],columna,padre)
        if r['tabla'] in tablas and padre in tablas and columna in tablas[r['tabla']] and clave not in existentes:
            fks.append({'table_name':r['tabla'],'column_name':columna,'padre':padre,'columna_padre':'id'})
            existentes.add(clave)
    # Un alcance por tabla. Las relaciones restantes se validan también por FK/triggers.
    alcances = {t: 't.empresa_id = %s' for t, cols in tablas.items() if 'empresa_id' in cols and t not in SOLO_GLOBAL}
    while True:
        nuevos = {}
        for r in fks:
            t, padre = r['table_name'], r['padre']
            if t not in alcances and t not in SOLO_GLOBAL and padre in alcances:
                condicion = alcances[padre].replace('t.', 'p.')
                # Los hijos sin empresa de este esquema dependen directamente de usuario.
                if 'empresa_id' not in tablas[padre]:
                    continue
                nuevos[t] = f'EXISTS (SELECT 1 FROM "{padre}" p WHERE p."{r["columna_padre"]}"=t."{r["column_name"]}" AND {condicion})'
        if not nuevos:
            break
        alcances.update(nuevos)
    cur.execute("""SELECT tc.table_name,k.column_name FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage k USING (constraint_catalog,constraint_schema,constraint_name)
                   WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema='public' ORDER BY k.ordinal_position""")
    pks = {}
    for r in cur.fetchall():
        pks.setdefault(r['table_name'], []).append(r['column_name'])
    return tablas, fks, alcances, pks


def _filas(cur, tabla, alcance=None, empresa_id=None):
    query = sql.SQL('SELECT to_jsonb(t) AS fila FROM {} t').format(sql.Identifier(tabla))
    if alcance:
        query += sql.SQL(' WHERE ' + alcance)
    cur.execute(query, (empresa_id,) if alcance else ())
    return sorted([r['fila'] for r in cur.fetchall()], key=_json)


def _archivo_local(valor):
    if not isinstance(valor, str):
        return None
    ruta = unquote(urlsplit(valor).path)
    if '/static/' not in ruta:
        return None
    relativa = ruta.split('/static/', 1)[1]
    archivo = (STATIC / relativa).resolve()
    if STATIC.resolve() not in archivo.parents or archivo.is_symlink():
        raise HTTPException(409, "Referencia de archivo fuera del almacenamiento permitido.")
    if not archivo.is_file():
        raise HTTPException(409, "Falta un archivo local referenciado; no se genera una copia incompleta.")
    return archivo


def _archivos_en_filas(tablas):
    archivos = set()
    def visitar(valor):
        if isinstance(valor, dict):
            for v in valor.values():
                visitar(v)
        elif isinstance(valor, list):
            for v in valor:
                visitar(v)
        elif isinstance(valor, str):
            archivo = _archivo_local(valor)
            if archivo:
                archivos.add(archivo)
    visitar(tablas)
    return archivos


def _pg_env(dsn):
    parametros = parse_dsn(dsn)
    env = os.environ.copy()
    for nombre in ('PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD', 'PGDATABASE', 'PGSERVICE'):
        env.pop(nombre, None)
    for clave, valor in parametros.items():
        env['PG' + ('DATABASE' if clave == 'dbname' else clave.upper())] = valor
    return env


def _pg(comando, dsn):
    try:
        subprocess.run(comando, env=_pg_env(dsn), check=True, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as error:
        # La salida de las herramientas puede contener cadenas de conexión.
        raise HTTPException(503, "Falló la herramienta PostgreSQL de respaldo/restauración. Revise el entorno privado.") from error


def crear_respaldo(actor_id: int, empresa_id: int | None = None, origen: str = "MANUAL"):
    identificador = uuid4()
    object_key = f"respaldos/{empresa_id or 'plataforma'}/{identificador}.fernet"
    conn = get_connection()
    subido = False
    bloqueo_empresa = False
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock_shared(%s)',(LOCK_PLANES,))
        conn.commit()
        if empresa_id is not None:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(hashtextextended('cu25-respaldo:' || %s::text, 0))", (empresa_id,))
            conn.commit()
            bloqueo_empresa = True
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            politica = None
            if empresa_id is not None:
                cur.execute("SELECT id FROM empresa WHERE id=%s FOR UPDATE", (empresa_id,))
                if cur.fetchone() is None:
                    raise HTTPException(404, 'Empresa no encontrada.')
                politica = _politica_plan(cur, empresa_id)
                if origen in {'MANUAL','AUTOMATICO'}:
                    _leer_plan(cur,empresa_id,validar_vigencia=True)
                if origen=='AUTOMATICO':
                    cur.execute('SELECT * FROM respaldo_programacion WHERE empresa_id=%s FOR UPDATE',(empresa_id,))
                    programacion=dict(cur.fetchone() or {})
                    if not respaldo_vencido(programacion,politica,datetime.now(timezone.utc)):
                        raise HTTPException(409,'El respaldo automático no está habilitado o aún no corresponde.')
                if origen == "MANUAL":
                    if not politica['manuales_habilitados']:
                        raise HTTPException(403,'El plan no permite crear respaldos manuales.')
                    cur.execute("SELECT count(*) AS total FROM respaldo WHERE empresa_id=%s AND tipo='EMPRESA' AND origen='MANUAL' AND fecha >= CURRENT_TIMESTAMP - INTERVAL '7 days'", (empresa_id,))
                    if int(cur.fetchone()["total"]) >= politica["manuales_semana"]:
                        raise HTTPException(429, detail={"codigo":"CUOTA_RESPALDO_MANUAL", "mensaje":f"El plan permite {politica['manuales_semana']} respaldos manuales cada 7 días."})
            cliente, bucket, cifrador, dias_global = configuracion()
            dias = politica["retencion_dias"] if politica else dias_global
            versiones = _versiones(cur)
            manifiesto = {'formato': 1, 'id': str(identificador), 'tipo': 'EMPRESA' if empresa_id else 'GLOBAL', 'empresa_id': empresa_id, 'origen': origen, 'versiones': versiones}
            buffer = io.BytesIO()
            with ZipFile(buffer, 'w', ZIP_DEFLATED) as z:
                if empresa_id is not None:
                    cur.execute('SELECT codigo FROM empresa WHERE id=%s', (empresa_id,))
                    empresa = cur.fetchone()
                    if not empresa:
                        raise HTTPException(404, 'Empresa no encontrada.')
                    _, _, alcances, _ = _esquema(cur)
                    datos = {t: _filas(cur, t, alcance, empresa_id) for t, alcance in alcances.items()}
                    manifiesto['empresa_codigo'] = empresa['codigo']
                    z.writestr('datos.json', _json(datos))
                    # Referencias globales se verifican, nunca se sobrescriben al restaurar una empresa.
                    globales = {t: _filas(cur, t) for t in ('rol', 'permiso', 'rol_permiso', 'plan')}
                    z.writestr('referencias.json', _json(globales))
                    archivos = _archivos_en_filas(datos)
                    manifiesto['conteos'] = {t: len(filas) for t, filas in datos.items()}
                else:
                    # Una instantánea exportada mantiene el dump y el manifiesto en la misma versión de la base.
                    cur.execute('SELECT pg_export_snapshot() AS snapshot')
                    snapshot = cur.fetchone()['snapshot']
                    with tempfile.TemporaryDirectory(prefix='stylear_dump_') as tmp:
                        dump = Path(tmp) / 'base.dump'
                        _pg(['pg_dump', '--format=custom', '--no-owner', '--no-privileges', '--snapshot=' + snapshot, '--file=' + str(dump)], DATABASE_URL)
                        z.write(dump, 'base.dump')
                    archivos = {p for p in STATIC.rglob('*') if p.is_file() and not p.is_symlink()} if STATIC.exists() else set()
                for p in sorted(archivos):
                    z.write(p, 'archivos/' + p.relative_to(STATIC).as_posix())
                manifiesto['archivos'] = len(archivos)
                z.writestr('manifiesto.json', _json(manifiesto))
            conn.rollback()
            contenido = cifrador.encrypt(buffer.getvalue())
            cliente.put_object(Bucket=bucket, Key=object_key, Body=contenido, ContentType="application/octet-stream")
            subido = True
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""INSERT INTO respaldo (id,empresa_id,tipo,origen,object_key,creado_por,vence_en,sha256,bytes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,tipo,origen,empresa_id,fecha,vence_en,bytes,estado""",
                    (str(identificador),empresa_id,manifiesto['tipo'],origen,object_key,actor_id,datetime.now(timezone.utc)+timedelta(days=dias),hashlib.sha256(contenido).hexdigest(),len(contenido)))
                resultado = dict(cur.fetchone())
                auditar(cur,actor_id,'CREAR_RESPALDO_'+origen,empresa_id,str(identificador))
                if origen=='AUTOMATICO' and empresa_id is not None:
                    cur.execute("""INSERT INTO respaldo_programacion(empresa_id,ultimo_respaldo)
                        VALUES(%s,CURRENT_TIMESTAMP) ON CONFLICT(empresa_id)
                        DO UPDATE SET ultimo_respaldo=CURRENT_TIMESTAMP""",(empresa_id,))
            conn.commit()
            return resultado
    except Exception:
        conn.rollback()
        if subido:
            try:
                cliente.delete_object(Bucket=bucket, Key=object_key)
            except Exception:
                pass
        raise
    finally:
        if bloqueo_empresa:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtextextended('cu25-respaldo:' || %s::text, 0))", (empresa_id,))
            except Exception:
                pass
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock_shared(%s)',(LOCK_PLANES,))
        except Exception:
            pass
        conn.close()


def listar_respaldos(empresa_id: int | None = None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if empresa_id is None:
                cur.execute('SELECT id,tipo,origen,empresa_id,fecha,vence_en,bytes,estado FROM respaldo ORDER BY fecha DESC LIMIT 200')
            else:
                cur.execute('SELECT id,tipo,origen,empresa_id,fecha,vence_en,bytes,estado FROM respaldo WHERE empresa_id=%s ORDER BY fecha DESC LIMIT 200', (empresa_id,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _abrir(cur, identificador: UUID):
    cliente, bucket, cifrador, _ = configuracion()
    cur.execute("SELECT * FROM respaldo WHERE id=%s AND estado='DISPONIBLE' FOR UPDATE", (str(identificador),))
    registro = cur.fetchone()
    if registro is None:
        raise HTTPException(404, 'Respaldo no encontrado.')
    if not registro.get("object_key"):
        raise HTTPException(409, "Este respaldo anterior no está en R2; debe generarse una nueva copia.")
    try:
        contenido = cliente.get_object(Bucket=bucket, Key=registro["object_key"])["Body"].read()
        if hashlib.sha256(contenido).hexdigest() != registro['sha256']:
            raise ValueError()
        z = ZipFile(io.BytesIO(cifrador.decrypt(contenido)))
        manifiesto = json.loads(z.read('manifiesto.json'))
        if manifiesto['id'] != str(identificador) or manifiesto['empresa_id'] != registro['empresa_id'] or manifiesto['tipo'] != registro['tipo']:
            raise ValueError()
        return z, manifiesto
    except (OSError, ValueError, InvalidToken, KeyError, BadZipFile) as error:
        raise HTTPException(409, 'No se pudo verificar o descifrar el respaldo.') from error
    except Exception as error:
        raise HTTPException(503, 'No se pudo acceder al respaldo en R2.') from error


def previsualizar_restauracion(identificador: UUID, actor_id: int, empresa_id: int | None = None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            with _abrir(cur, identificador)[0] as z:
                manifiesto = json.loads(z.read('manifiesto.json'))
            if empresa_id is not None and manifiesto["empresa_id"] != empresa_id:
                raise HTTPException(404, "Respaldo no encontrado.")
            if manifiesto['versiones'] != _versiones(cur):
                raise HTTPException(409, 'El esquema difiere del respaldo; requiere una migración supervisada.')
            resultado = {**manifiesto, 'confirmacion': 'RESTAURAR ' + str(identificador)}
            if manifiesto['empresa_id']:
                _, _, alcances, _ = _esquema(cur)
                resultado['conteos_actuales'] = {t: len(_filas(cur,t,c,manifiesto['empresa_id'])) for t,c in alcances.items()}
                resultado['destino'] = 'Empresa original suspendida; reemplaza sus datos comerciales y cierra sus sesiones.'
            else:
                resultado['destino'] = 'Base y carpeta de ensayo vacías configuradas por el operador; no sobrescribe producción.'
            auditar(cur,actor_id,'PREVISUALIZAR_RESTAURACION',manifiesto['empresa_id'],str(identificador))
        conn.commit()
        return resultado
    finally:
        conn.close()


def _restaurar_archivos(z, destino, nuevos):
    for nombre in z.namelist():
        if not nombre.startswith('archivos/') or nombre.endswith('/'):
            continue
        archivo = (destino / nombre.removeprefix('archivos/')).resolve()
        if destino.resolve() not in archivo.parents or archivo.is_symlink():
            raise HTTPException(409, 'Archivo fuera del destino permitido.')
        contenido = z.read(nombre)
        if archivo.exists():
            if archivo.read_bytes() != contenido:
                raise HTTPException(409, 'Un archivo existente difiere; no se sobrescriben recursos compartidos.')
            continue
        archivo.parent.mkdir(parents=True,exist_ok=True)
        with archivo.open('xb') as f:
            f.write(contenido)
        nuevos.append(archivo)
    return nuevos


def restaurar_respaldo(identificador: UUID, actor_id: int, confirmacion: str, empresa_id: int | None = None):
    if confirmacion != 'RESTAURAR ' + str(identificador):
        raise HTTPException(400, 'Debe confirmar la restauración del respaldo indicado.')
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT empresa_id FROM respaldo WHERE id=%s AND estado='DISPONIBLE'", (str(identificador),))
            row = cur.fetchone()
            if not row or (empresa_id is not None and row['empresa_id'] != empresa_id):
                raise HTTPException(404, 'Respaldo no encontrado.')
            destino_empresa=row['empresa_id']
    finally:
        conn.close()
    if destino_empresa is not None:
        crear_respaldo(actor_id, destino_empresa, "PRE_RESTAURACION")
    conn = get_connection()
    nuevos = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT pg_advisory_xact_lock_shared(%s)',(LOCK_PLANES,))
            z, manifiesto = _abrir(cur,identificador)
            with z:
                if manifiesto['versiones'] != _versiones(cur):
                    raise HTTPException(409, 'El esquema difiere del respaldo.')
                if manifiesto['tipo'] == 'GLOBAL':
                    _restaurar_global(z, nuevos)
                else:
                    _restaurar_empresa(cur,z,manifiesto)
                    _restaurar_archivos(z,STATIC,nuevos)
                auditar(cur,actor_id,'RESTAURAR_RESPALDO',manifiesto['empresa_id'],str(identificador))
        conn.commit()
        return {'estado': 'RESTAURADO', 'destino': 'ENSAYO' if manifiesto['tipo']=='GLOBAL' else 'EMPRESA_ORIGINAL_SUSPENDIDA'}
    except Exception:
        conn.rollback()
        for archivo in nuevos:
            archivo.unlink(missing_ok=True)
        raise
    finally:
        conn.close()


def _restaurar_empresa(cur,z,manifiesto):
    empresa_id = manifiesto['empresa_id']
    cur.execute('SELECT codigo,estado FROM empresa WHERE id=%s FOR UPDATE', (empresa_id,))
    empresa = cur.fetchone()
    if not empresa or empresa['codigo'] != manifiesto['empresa_codigo'] or empresa['estado'] != 'SUSPENDIDA':
        raise HTTPException(409,'Suspenda la empresa original antes de restaurarla.')
    tablas,fks,alcances,pks = _esquema(cur)
    datos = json.loads(z.read('datos.json'))
    if set(datos) != set(alcances):
        raise HTTPException(409,'El inventario de tablas cambió.')
    # Evita escrituras concurrentes durante el reemplazo y permite verificar que B no cambie.
    for t in sorted(alcances):
        cur.execute(sql.SQL('LOCK TABLE {} IN EXCLUSIVE MODE').format(sql.Identifier(t)))
    cur.execute('SET CONSTRAINTS ALL DEFERRED')
    for t,filas in json.loads(z.read('referencias.json')).items():
        if filas != _filas(cur,t):
            raise HTTPException(409,'Cambió una referencia global; revise el respaldo antes de restaurar.')
    antes = {t:_filas(cur,t,'NOT ('+c+')',empresa_id) for t,c in alcances.items()}
    for t,filas in datos.items():
        for fila in filas:
            if 'empresa_id' in tablas[t] and fila['empresa_id'] != empresa_id:
                raise HTTPException(409,'Fila de otra empresa en el respaldo.')
            condicion = sql.SQL(' AND ').join(sql.SQL('{} IS NOT DISTINCT FROM %s').format(sql.Identifier(k)) for k in pks[t])
            cur.execute(sql.SQL('SELECT 1 FROM {} t WHERE ').format(sql.Identifier(t))+condicion+sql.SQL(' AND NOT ('+alcances[t]+')'),tuple(fila[k] for k in pks[t])+(empresa_id,))
            if cur.fetchone():
                raise HTTPException(409,'Un identificador pertenece a otra empresa.')
    # Rompe únicamente vínculos anulables de la empresa restaurada, luego elimina
    # hijos antes que padres, incluso cuando una FK usa ON DELETE RESTRICT.
    pendientes_orden = set(datos)
    orden = []
    while pendientes_orden:
        disponibles = sorted(t for t in pendientes_orden if not any(r['table_name']==t and r['padre'] in pendientes_orden and r['padre']!=t and tablas[t][r['column_name']]['is_nullable']=='NO' for r in fks))
        if not disponibles:
            raise HTTPException(409,'Dependencia circular obligatoria.')
        orden.extend(disponibles)
        pendientes_orden.difference_update(disponibles)
    for t in orden:
        anulables = {r['column_name'] for r in fks if r['table_name']==t and tablas[t][r['column_name']]['is_nullable']=='YES' and r['padre'] in datos and r['column_name'] not in pks[t]}
        if anulables:
            asignaciones = sql.SQL(',').join(sql.SQL('{}=NULL').format(sql.Identifier(k)) for k in sorted(anulables))
            cur.execute(sql.SQL('UPDATE {} t SET ').format(sql.Identifier(t))+asignaciones+sql.SQL(' WHERE '+alcances[t]),(empresa_id,))
    for t in reversed(orden):
        cur.execute(sql.SQL('DELETE FROM {} t WHERE ').format(sql.Identifier(t))+sql.SQL(alcances[t]),(empresa_id,))
    pendientes = set(datos)
    actualizaciones = []
    while pendientes:
        disponibles = sorted(t for t in pendientes if not any(r['table_name']==t and r['padre'] in pendientes and r['padre']!=t and tablas[t][r['column_name']]['is_nullable']=='NO' for r in fks))
        if not disponibles:
            raise HTTPException(409,'Dependencia circular obligatoria; no se restaura parcialmente.')
        for t in disponibles:
            anulables = {r['column_name'] for r in fks if r['table_name']==t and tablas[t][r['column_name']]['is_nullable']=='YES' and r['padre'] in datos}
            for fila in datos[t]:
                inicial = {k:(None if k in anulables else v) for k,v in fila.items()}
                cur.execute(sql.SQL('INSERT INTO {} SELECT (json_populate_record(NULL::{},%s::json)).*').format(sql.Identifier(t),sql.Identifier(t)),(_json(inicial),))
                if anulables:
                    actualizaciones.append((t,fila,anulables))
            pendientes.remove(t)
    for t,fila,columnas in actualizaciones:
        asignaciones = sql.SQL(',').join(sql.SQL('{}=r.{}').format(sql.Identifier(k),sql.Identifier(k)) for k in sorted(columnas))
        condicion = sql.SQL(' AND ').join(sql.SQL('t.{}=r.{}').format(sql.Identifier(k),sql.Identifier(k)) for k in pks[t])
        cur.execute(sql.SQL('UPDATE {} t SET ').format(sql.Identifier(t))+asignaciones+sql.SQL(' FROM json_populate_record(NULL::{},%s::json) r WHERE ').format(sql.Identifier(t))+condicion,(_json(fila),))
    cur.execute('SET CONSTRAINTS ALL IMMEDIATE')
    for t,c in alcances.items():
        if _filas(cur,t,c,empresa_id)!=datos[t] or _filas(cur,t,'NOT ('+c+')',empresa_id)!=antes[t]:
            raise HTTPException(409,'La verificación de datos restaurados o de otras empresas falló.')
    # Tokens guardados en una copia anterior no deben volver a habilitar sesiones.
    cur.execute('UPDATE sesion SET activa=FALSE WHERE empresa_id=%s',(empresa_id,))
    cur.execute('UPDATE token_recuperacion SET utilizado=TRUE WHERE usuario_id IN (SELECT id FROM usuario WHERE empresa_id=%s)',(empresa_id,))
    cur.execute('UPDATE password_reset_token SET usado=TRUE WHERE usuario_id IN (SELECT id FROM usuario WHERE empresa_id=%s)',(empresa_id,))
    cur.execute('UPDATE dispositivo_push SET activo=FALSE WHERE empresa_id=%s',(empresa_id,))
    cur.execute("""UPDATE salida_push SET estado='OMITIDO',ultimo_error='Copia restaurada; aviso histórico no se reenvía.'
        WHERE notificacion_id IN (SELECT id FROM notificacion_usuario WHERE empresa_id=%s)
            AND estado IN ('PENDIENTE','FALLIDO')""",(empresa_id,))
    # Recuperar datos comerciales no permite recuperar un plan superior ni
    # reactivar automatizaciones retiradas por el administrador SaaS.
    politica=normalizar_politica(_leer_plan(cur,empresa_id,validar_vigencia=False))
    _ajustar_programaciones(cur,empresa_id,politica)


def _restaurar_global(z, nuevos):
    dsn = os.getenv('STYLEAR_RESTORE_DATABASE_URL')
    assets = os.getenv('STYLEAR_RESTORE_ASSETS_DIR')
    if not dsn or not assets or parse_dsn(dsn)==parse_dsn(DATABASE_URL):
        raise HTTPException(503,'Configure una base y una carpeta vacías de ensayo distintas de producción.')
    destino = Path(assets).resolve()
    if destino == STATIC.resolve() or STATIC.resolve() in destino.parents or (destino.exists() and any(destino.iterdir())):
        raise HTTPException(409,'El destino de archivos debe estar vacío y separado del activo.')
    ensayo = psycopg2.connect(dsn)
    try:
        with ensayo.cursor() as c:
            c.execute('SELECT current_database(),inet_server_addr()::text,inet_server_port()')
            identidad_destino = c.fetchone()
            activo = get_connection()
            try:
                with activo.cursor() as original:
                    original.execute('SELECT current_database(),inet_server_addr()::text,inet_server_port()')
                    if identidad_destino == original.fetchone():
                        raise HTTPException(409,'El destino es la base activa; use una base de ensayo distinta.')
            finally:
                activo.close()
            c.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' LIMIT 1")
            if c.fetchone():
                raise HTTPException(409,'La base de ensayo debe estar vacía.')
    finally:
        ensayo.close()
    _restaurar_archivos(z,destino,nuevos)
    with tempfile.TemporaryDirectory(prefix='stylear_restore_') as tmp:
        dump = Path(tmp)/'base.dump'
        dump.write_bytes(z.read('base.dump'))
        _pg(['pg_restore','--dbname='+parse_dsn(dsn)['dbname'],'--single-transaction','--exit-on-error','--no-owner','--no-privileges',str(dump)],dsn)
    ensayo = psycopg2.connect(dsn)
    try:
        with ensayo.cursor() as c:
            c.execute('UPDATE sesion SET activa=FALSE')
            c.execute('UPDATE token_recuperacion SET utilizado=TRUE')
            c.execute('UPDATE password_reset_token SET usado=TRUE')
            c.execute('UPDATE dispositivo_push SET activo=FALSE')
            c.execute("UPDATE salida_push SET estado='OMITIDO' WHERE estado IN ('PENDIENTE','FALLIDO')")
            c.execute("SELECT to_regclass('public.comprobante_pago_envio')")
            if c.fetchone()[0] is not None:
                c.execute("UPDATE comprobante_pago_envio SET estado='FALLIDO',pdf=NULL WHERE estado='PENDIENTE'")
        ensayo.commit()
    finally:
        ensayo.close()


def eliminar_respaldo(identificador: UUID,actor_id: int,empresa_id: int | None = None):
    cliente,bucket,_,_ = configuracion()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if empresa_id is None:
                cur.execute("UPDATE respaldo SET estado='ELIMINADO' WHERE id=%s AND estado='DISPONIBLE' RETURNING empresa_id,object_key",(str(identificador),))
            else:
                cur.execute("UPDATE respaldo SET estado='ELIMINADO' WHERE id=%s AND empresa_id=%s AND estado='DISPONIBLE' RETURNING empresa_id,object_key",(str(identificador),empresa_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404,'Respaldo no encontrado.')
            auditar(cur,actor_id,'ELIMINAR_RESPALDO',row[0],str(identificador))
        try:
            if row[1]:
                cliente.delete_object(Bucket=bucket, Key=row[1])
        except Exception as error:
            raise HTTPException(503, "No se pudo eliminar el objeto del almacenamiento R2.") from error
        conn.commit()
        return {'estado':'ELIMINADO'}
    finally:
        conn.close()


def purgar_vencidos(actor_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM respaldo WHERE estado='DISPONIBLE' AND vence_en<CURRENT_TIMESTAMP")
            ids = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    for identificador in ids:
        eliminar_respaldo(identificador,actor_id)
    return {"eliminados":len(ids)}


def obtener_programacion(empresa_id: int):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            politica = _politica_plan(cur, empresa_id)
            cur.execute("""SELECT empresa_id,activa,fecha_inicio,hora_local,zona_horaria,ultimo_respaldo,actualizada_en
                FROM respaldo_programacion WHERE empresa_id=%s""", (empresa_id,))
            fila = cur.fetchone()
            return {"politica": politica, "programacion": dict(fila) if fila else {
                "empresa_id": empresa_id, "activa": politica["frecuencia_dias"] is not None,
                "fecha_inicio": datetime.now(ZoneInfo("America/La_Paz")).date(),
                "hora_local": "02:00:00", "zona_horaria": "America/La_Paz", "ultimo_respaldo": None,
            }}
    finally:
        conn.close()


def guardar_programacion(empresa_id: int, actor_id: int, activa: bool, fecha_inicio, hora_local: str, zona_horaria: str):
    try:
        zona = ZoneInfo(zona_horaria)
        hora = datetime.strptime(hora_local, "%H:%M").time()
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise HTTPException(422, "Indique una zona horaria válida y la hora en formato HH:MM.") from error
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _leer_plan(cur,empresa_id,bloquear=True)
            politica = _politica_plan(cur, empresa_id)
            if politica["frecuencia_dias"] is None and activa:
                raise HTTPException(403, "El plan actual no incluye respaldos automáticos.")
            if not politica["horario_editable"] and hora.strftime("%H:%M") != "02:00":
                raise HTTPException(403, "El plan permite respaldos automáticos, pero mantiene la hora fija de las 02:00.")
            if not politica["horario_editable"] and zona.key != "America/La_Paz":
                raise HTTPException(403, "El plan mantiene el horario de respaldos en la zona de Bolivia.")
            cur.execute("""INSERT INTO respaldo_programacion (empresa_id,activa,fecha_inicio,hora_local,zona_horaria,actualizada_en)
                VALUES (%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT (empresa_id) DO UPDATE SET activa=EXCLUDED.activa,fecha_inicio=EXCLUDED.fecha_inicio,
                hora_local=EXCLUDED.hora_local,zona_horaria=EXCLUDED.zona_horaria,actualizada_en=CURRENT_TIMESTAMP
                RETURNING empresa_id,activa,fecha_inicio,hora_local,zona_horaria,ultimo_respaldo,actualizada_en""",
                (empresa_id, activa, fecha_inicio, hora, zona.key))
            row = dict(cur.fetchone())
            auditar(cur, actor_id, "CONFIGURAR_RESPALDO_AUTOMATICO", empresa_id)
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ejecutar_respaldos_programados():
    """Una sola instancia procesa el calendario; cada copia revalida su plan."""
    conn=get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT pg_try_advisory_lock(250251105) AS adquirido')
            if not cur.fetchone()['adquirido']:
                return 0
            cur.execute("""SELECT e.id,(SELECT u.id FROM usuario u JOIN usuario_rol ur ON ur.usuario_id=u.id
                JOIN rol r ON r.id=ur.rol_id WHERE u.empresa_id=e.id AND r.nombre='ADMINISTRADOR'
                AND u.activo AND ur.activo AND r.activo ORDER BY u.id LIMIT 1) AS actor_id
                FROM empresa e WHERE e.estado='ACTIVA' ORDER BY e.id""")
            empresas=[dict(r) for r in cur.fetchall()]
        total=0
        for empresa in empresas:
            if empresa['actor_id'] is None:
                continue
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    politica=_politica_plan(cur,empresa['id'])
                    cur.execute('SELECT * FROM respaldo_programacion WHERE empresa_id=%s',(empresa['id'],))
                    programacion=dict(cur.fetchone() or {})
                conn.rollback()
                if respaldo_vencido(programacion,politica,datetime.now(timezone.utc)):
                    crear_respaldo(int(empresa['actor_id']),int(empresa['id']),'AUTOMATICO')
                    total+=1
            except HTTPException as error:
                conn.rollback()
                if error.status_code not in (403,409):
                    LOGGER.error('No se confirmó el respaldo automático de empresa %s.',empresa['id'])
            except Exception:
                conn.rollback()
                LOGGER.error('Falló el respaldo automático de empresa %s.',empresa['id'])
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id,creado_por FROM respaldo WHERE estado='DISPONIBLE' AND vence_en<CURRENT_TIMESTAMP")
            expirados=[dict(r) for r in cur.fetchall()]
        for fila in expirados:
            try:
                eliminar_respaldo(fila['id'],int(fila['creado_por']))
            except Exception:
                LOGGER.error('Falló la limpieza del respaldo %s.',fila['id'])
        return total
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(250251105)')
        finally:
            conn.close()
