"""CU04: transacciones de administración de acceso, compartidas por AD y AS."""

from contextlib import contextmanager
from functools import wraps
from fastapi import HTTPException
from psycopg2.errors import UniqueViolation
from app.core.security import hashear_password
from app.core.plan_empresa import exigir_cupo_usuarios, exigir_operaciones_empresa
from app.modules.autenticacion.autorizacion import (
    ROLES_BASE,
    CONFIGURABLES,
    permisos_cursor,
)
from app.modules.autenticacion.repositories.perfil_repository import (
    _transaccion,
    invalidar_accesos,
)

LOCK_ACCESOS = 2026102304


def auditar(
    cur,
    actor,
    empresa,
    accion,
    objetivo=None,
    afectada=None,
    modulo="USUARIOS",
    descripcion=None,
):
    cur.execute(
        """INSERT INTO bitacora(usuario_id,empresa_id,accion,modulo,descripcion,resultado,
        objetivo_usuario_id,empresa_afectada_id,direccion_ip,user_agent)
        VALUES(%s,%s,%s,%s,%s,'EXITOSO',%s,%s,%s::inet,%s)""",
        (
            actor["id"],
            empresa,
            accion,
            modulo,
            descripcion or accion.replace("_", " ").capitalize(),
            objetivo,
            afectada,
            actor.get("_ip"),
            actor.get("_ua"),
        ),
    )


def registrar_rechazo(actor, accion):
    from app.modules.autenticacion.repositories.bitacora_repository import (
        registrar_bitacora,
    )

    registrar_bitacora(
        int(actor["id"]),
        accion,
        "CONTROL_ACCESO",
        "BLOQUEADO",
        descripcion="Operación administrativa rechazada.",
        empresa_id=actor.get("empresa_id"),
        direccion_ip=actor.get("_ip"),
        user_agent=actor.get("_ua"),
    )


def controlado(accion):
    def decorar(fn):
        @wraps(fn)
        def ejecutar(actor, *args, **kwargs):
            try:
                return fn(actor, *args, **kwargs)
            except UniqueViolation as error:
                registrar_rechazo(actor, accion)
                raise HTTPException(
                    409, "Ese usuario o correo ya está registrado."
                ) from error
            except HTTPException:
                registrar_rechazo(actor, accion)
                raise

        return ejecutar

    return decorar


def validar_actor(cur, actor, permiso=None):
    cur.execute(
        """SELECT id,activo,empresa_id,version_acceso FROM usuario WHERE id=%s""",
        (actor["id"],),
    )
    actual = cur.fetchone()
    if (
        not actual
        or not actual["activo"]
        or actual["empresa_id"] != actor.get("empresa_id")
        or actual["version_acceso"] != actor.get("version_acceso", 0)
    ):
        raise HTTPException(401, "La cuenta cambió. Inicia sesión nuevamente.")
    cur.execute(
        """SELECT r.nombre FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id AND r.activo
        WHERE ur.usuario_id=%s AND ur.activo""",
        (actor["id"],),
    )
    roles = {r["nombre"] for r in cur.fetchall()}
    if actual["empresa_id"] is None:
        if "ADMIN_SAAS" not in roles:
            raise HTTPException(403, "Acceso exclusivo de AS.")
    elif "ADMINISTRADOR" not in roles or (
        permiso and permiso not in permisos_cursor(cur, actor["id"])
    ):
        raise HTTPException(403, "No tiene el permiso necesario: " + str(permiso))


@contextmanager
def escritura(actor, empresa, permiso):
    with _transaccion() as cur:
        # Serializa administradores/plantillas; empresa antes de usuario para cupos.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ACCESOS,))
        validar_actor(cur, actor, permiso)
        if empresa is not None:
            cur.execute("SELECT id FROM empresa WHERE id=%s FOR UPDATE", (empresa,))
            if not cur.fetchone():
                raise HTTPException(404, "Empresa no encontrada.")
        yield cur


def alcance(actor, empresa):
    if actor.get("empresa_id") is not None and empresa != actor["empresa_id"]:
        raise HTTPException(403, "Empresa no autorizada.")
    if actor.get("empresa_id") is None and "ADMIN_SAAS" not in actor.get("roles", []):
        raise HTTPException(403, "Acceso exclusivo de AS.")


def condicion(empresa, solo_ad=False):
    where = "u.empresa_id IS NOT DISTINCT FROM %s"
    if solo_ad:
        where += " AND EXISTS(SELECT 1 FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id WHERE ur.usuario_id=u.id AND r.nombre='ADMINISTRADOR')"
    return where


SELECT_USUARIO = """SELECT u.id,u.nombre,u.apellido,u.username,u.correo,u.activo,u.empresa_id,
    COALESCE((SELECT array_agg(r.nombre ORDER BY r.nombre) FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id
        WHERE ur.usuario_id=u.id AND ur.activo AND r.activo),ARRAY[]::varchar[]) roles,
    EXISTS(SELECT 1 FROM cliente c WHERE c.usuario_id=u.id AND c.activo) es_cliente,
    EXISTS(SELECT 1 FROM empleado e WHERE e.usuario_id=u.id AND e.activo) es_empleado
    FROM usuario u"""


def usuario_cursor(cur, uid, empresa, solo_ad=False, bloquear=False):
    cur.execute(
        SELECT_USUARIO
        + " WHERE u.id=%s AND "
        + condicion(empresa, solo_ad)
        + (" FOR UPDATE OF u" if bloquear else ""),
        (uid, empresa),
    )
    fila = cur.fetchone()
    if not fila:
        raise HTTPException(404, "Usuario no encontrado.")
    return dict(fila)


def listar(actor, empresa, estado="ACTIVOS", busqueda="", pagina=1, por_pagina=25, rol=None):
    alcance(actor, empresa)
    solo_ad = actor.get("empresa_id") is None and empresa is not None
    where = condicion(empresa, solo_ad)
    params = [empresa]
    if estado != "TODOS":
        where += " AND u.activo=%s"
        params.append(estado == "ACTIVOS")
    if busqueda:
        where += " AND concat_ws(' ',u.nombre,u.apellido,u.username,u.correo) ILIKE %s"
        params.append("%" + busqueda + "%")
    if rol:
        existe = "EXISTS(SELECT 1 FROM usuario_rol ur JOIN rol r ON r.id=ur.rol_id WHERE ur.usuario_id=u.id AND ur.activo AND r.activo"
        if rol == 'SIN_ROL':
            where += ' AND NOT ' + existe + ')'
        else:
            where += ' AND ' + existe + ' AND r.nombre=%s)'
            params.append(rol)
    with _transaccion(isolation_level="REPEATABLE READ", readonly=True) as cur:
        validar_actor(cur, actor, "usuarios:ver")
        if empresa is not None:
            cur.execute("SELECT id FROM empresa WHERE id=%s", (empresa,))
            if not cur.fetchone():
                raise HTTPException(404, "Empresa no encontrada.")
        cur.execute("SELECT count(*) total FROM usuario u WHERE " + where, params)
        total = cur.fetchone()["total"]
        cur.execute(
            SELECT_USUARIO
            + " WHERE "
            + where
            + " ORDER BY u.id DESC LIMIT %s OFFSET %s",
            params + [por_pagina, (pagina - 1) * por_pagina],
        )
        return {
            "registros": [dict(r) for r in cur.fetchall()],
            "total": total,
            "pagina": pagina,
            "por_pagina": por_pagina,
            "total_paginas": max(1, (total + por_pagina - 1) // por_pagina),
        }


def detalle(actor, empresa, uid):
    alcance(actor, empresa)
    with _transaccion() as cur:
        validar_actor(cur, actor, "usuarios:ver")
        return usuario_cursor(
            cur, uid, empresa, actor.get("empresa_id") is None and empresa is not None
        )


def rol_cursor(cur, rid, empresa):
    cur.execute("SELECT id,nombre,activo FROM rol WHERE id=%s", (rid,))
    r = cur.fetchone()
    if not r or not r["activo"]:
        raise HTTPException(404, "Rol no encontrado.")
    if r["nombre"] not in ROLES_BASE:
        raise HTTPException(
            409, "Este rol adicional es de consulta hasta definir sus capacidades."
        )
    if (empresa is None) != (r["nombre"] == "ADMIN_SAAS"):
        raise HTTPException(403, "Ese rol no pertenece al alcance de la cuenta.")
    return r


def validar_vinculo(cur, uid, empresa, nombre):
    if nombre in {"CAJERO", "ENCARGADO_SUCURSAL"}:
        cur.execute(
            """SELECT e.id FROM empleado e JOIN sucursal s ON s.id=e.sucursal_id AND s.empresa_id=e.empresa_id
            WHERE e.usuario_id=%s AND e.empresa_id=%s AND e.activo AND s.activo""",
            (uid, empresa),
        )
        if not cur.fetchone():
            raise HTTPException(
                409,
                "Primero vincula la cuenta a un empleado y sucursal desde Empleados (CU07).",
            )
    if nombre == "PROVEEDOR":
        cur.execute(
            """SELECT pu.usuario_id FROM proveedor_usuario pu JOIN proveedor p ON p.id=pu.proveedor_id AND p.empresa_id=pu.empresa_id
            WHERE pu.usuario_id=%s AND pu.empresa_id=%s AND pu.activo AND p.activo""",
            (uid, empresa),
        )
        if not cur.fetchone():
            raise HTTPException(
                409, "Primero vincula la cuenta desde Proveedores (CU08)."
            )
    if nombre == "CLIENTE":
        cur.execute(
            "SELECT id FROM cliente WHERE usuario_id=%s AND empresa_id=%s AND activo",
            (uid, empresa),
        )
        if not cur.fetchone():
            raise HTTPException(409, "La cuenta no tiene un cliente asociado.")


def proteger_ultimo(cur, actor, fila, rol):
    if fila["id"] == actor["id"]:
        raise HTTPException(
            409,
            "No puedes quitar tu propio acceso administrativo. Usa Perfil para tu cuenta.",
        )
    if rol in fila["roles"] and fila["activo"]:
        cur.execute(
            """SELECT count(DISTINCT u.id) total FROM usuario u JOIN usuario_rol ur ON ur.usuario_id=u.id AND ur.activo
            JOIN rol r ON r.id=ur.rol_id AND r.activo WHERE u.empresa_id IS NOT DISTINCT FROM %s AND u.activo AND r.nombre=%s""",
            (fila["empresa_id"], rol),
        )
        if cur.fetchone()["total"] <= 1:
            raise HTTPException(
                409, "Debe permanecer al menos un administrador activo."
            )


@controlado("CREAR_USUARIO_RECHAZADO")
def crear(actor, empresa, datos):
    alcance(actor, empresa)
    try:
        password = hashear_password(datos.password)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    with escritura(actor, empresa, "usuarios:crear") as cur:
        r = rol_cursor(cur, datos.rol_id, empresa)
        if (
            actor.get("empresa_id") is None
            and empresa is not None
            and r["nombre"] != "ADMINISTRADOR"
        ):
            raise HTTPException(
                403, "AS sólo administra administradores de empresa en esta vista."
            )
        if r["nombre"] in {"CAJERO", "ENCARGADO_SUCURSAL", "PROVEEDOR"}:
            raise HTTPException(
                409,
                "Crea esta cuenta mediante Empleados (CU07) o Proveedores (CU08) para conservar sus vínculos.",
            )
        if empresa is not None:
            exigir_cupo_usuarios(cur, empresa)
        cur.execute(
            """INSERT INTO usuario(nombre,apellido,username,correo,password_hash,empresa_id)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
            (
                datos.nombre,
                datos.apellido,
                datos.username,
                datos.correo,
                password,
                empresa,
            ),
        )
        uid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO usuario_rol(usuario_id,rol_id) VALUES(%s,%s)", (uid, r["id"])
        )
        if r["nombre"] == "CLIENTE":
            cur.execute(
                "INSERT INTO cliente(usuario_id,empresa_id,telefono) VALUES(%s,%s,%s)",
                (uid, empresa, datos.telefono),
            )
        auditar(cur, actor, actor.get("empresa_id"), "CREAR_USUARIO", uid, empresa)
        return usuario_cursor(cur, uid, empresa)


@controlado("EDITAR_USUARIO_RECHAZADO")
def editar(actor, empresa, uid, datos):
    alcance(actor, empresa)
    with escritura(actor, empresa, "usuarios:editar") as cur:
        fila = usuario_cursor(
            cur,
            uid,
            empresa,
            actor.get("empresa_id") is None and empresa is not None,
            True,
        )
        cambia = datos.username != fila["username"] or datos.correo != fila["correo"]
        if cambia and uid == actor["id"]:
            raise HTTPException(
                409,
                "Cambia tus datos de acceso desde Perfil, confirmando tu contraseña.",
            )
        valores = (datos.nombre, datos.apellido, datos.username, datos.correo)
        if valores != (
            fila["nombre"],
            fila["apellido"],
            fila["username"],
            fila["correo"],
        ):
            cur.execute(
                "UPDATE usuario SET nombre=%s,apellido=%s,username=%s,correo=%s WHERE id=%s",
                valores + (uid,),
            )
            if cambia:
                invalidar_accesos(cur, uid, empresa)
            auditar(cur, actor, actor.get("empresa_id"), "EDITAR_USUARIO", uid, empresa)
        return usuario_cursor(cur, uid, empresa)


@controlado("CAMBIAR_ESTADO_USUARIO_RECHAZADO")
def estado_usuario(actor, empresa, uid, activo):
    alcance(actor, empresa)
    permiso = "usuarios:activar" if activo else "usuarios:desactivar"
    with escritura(actor, empresa, permiso) as cur:
        fila = usuario_cursor(
            cur,
            uid,
            empresa,
            actor.get("empresa_id") is None and empresa is not None,
            True,
        )
        if fila["activo"] == activo:
            return {"mensaje": "La cuenta ya tiene ese estado."}
        if activo and empresa is not None:
            exigir_cupo_usuarios(cur, empresa, uid)
        if not activo:
            proteger_ultimo(
                cur, actor, fila, "ADMIN_SAAS" if empresa is None else "ADMINISTRADOR"
            )
        else:
            if not fila["roles"]:
                raise HTTPException(409, "Asigna un rol antes de reactivar la cuenta.")
            for nombre in fila["roles"]:
                validar_vinculo(cur, uid, empresa, nombre)
        cur.execute("UPDATE usuario SET activo=%s WHERE id=%s", (activo, uid))
        invalidar_accesos(cur, uid, empresa)
        auditar(
            cur,
            actor,
            actor.get("empresa_id"),
            "ACTIVAR_USUARIO" if activo else "DESACTIVAR_USUARIO",
            uid,
            empresa,
        )
        return {
            "mensaje": (
                "Cuenta reactivada."
                if activo
                else "Cuenta desactivada. Su historial se conserva."
            )
        }


@controlado("CAMBIAR_ROL_RECHAZADO")
def rol_usuario(actor, empresa, uid, rid, activo):
    alcance(actor, empresa)
    with escritura(
        actor, empresa, "roles:asignar" if activo else "roles:retirar"
    ) as cur:
        fila = usuario_cursor(
            cur,
            uid,
            empresa,
            actor.get("empresa_id") is None and empresa is not None,
            True,
        )
        r = rol_cursor(cur, rid, empresa)
        if (
            actor.get("empresa_id") is None
            and empresa is not None
            and r["nombre"] != "ADMINISTRADOR"
        ):
            raise HTTPException(403, "AS sólo gestiona el acceso administrador.")
        cur.execute(
            "SELECT activo FROM usuario_rol WHERE usuario_id=%s AND rol_id=%s",
            (uid, rid),
        )
        relacion = cur.fetchone()
        if bool(relacion and relacion["activo"]) == activo:
            return {"mensaje": "El rol ya tiene ese estado."}
        if activo:
            if empresa is not None:
                exigir_operaciones_empresa(empresa, cur)
            validar_vinculo(cur, uid, empresa, r["nombre"])
        else:
            if r["nombre"] in {"ADMINISTRADOR", "ADMIN_SAAS"}:
                proteger_ultimo(cur, actor, fila, r["nombre"])
            if len(fila["roles"]) <= 1:
                raise HTTPException(
                    409,
                    "No puedes dejar una cuenta sin roles. Desactívala si corresponde.",
                )
        cur.execute(
            """INSERT INTO usuario_rol(usuario_id,rol_id,activo) VALUES(%s,%s,%s)
            ON CONFLICT(usuario_id,rol_id) DO UPDATE SET activo=EXCLUDED.activo""",
            (uid, rid, activo),
        )
        invalidar_accesos(cur, uid, empresa)
        auditar(
            cur,
            actor,
            actor.get("empresa_id"),
            "ASIGNAR_ROL" if activo else "RETIRAR_ROL",
            uid,
            empresa,
        )
        return {"mensaje": "Rol actualizado. Se cerraron las sesiones afectadas."}


def catalogos(actor, estado="ACTIVOS"):
    with _transaccion() as cur:
        validar_actor(cur, actor, "roles:ver")
        condicion_estado = (
            ""
            if estado == "TODOS"
            else " WHERE activo=" + ("TRUE" if estado == "ACTIVOS" else "FALSE")
        )
        cur.execute(
            "SELECT id,nombre,descripcion,activo FROM rol"
            + condicion_estado
            + " ORDER BY id"
        )
        roles = []
        for fila in cur.fetchall():
            r = dict(fila)
            if actor.get("empresa_id") is not None and r["nombre"] == "ADMIN_SAAS":
                continue
            cur.execute(
                "SELECT permiso_id FROM rol_permiso WHERE rol_id=%s AND activo ORDER BY permiso_id",
                (r["id"],),
            )
            r.update(
                permiso_ids=[p["permiso_id"] for p in cur.fetchall()],
                protegido=r["nombre"] in ROLES_BASE,
                configurable=r["nombre"] == "ADMINISTRADOR",
                cantidad_permisos=0,
            )
            r["cantidad_permisos"] = len(r["permiso_ids"])
            roles.append(r)
        cur.execute(
            "SELECT id,nombre,modulo,accion,descripcion,activo FROM permiso ORDER BY modulo,accion,id"
        )
        permisos = []
        for fila in cur.fetchall():
            p = dict(fila)
            p["configurable"] = p["accion"] in CONFIGURABLES
            p["motivo"] = (
                "Configuración efectiva en CU04/CU05 para AD."
                if p["configurable"]
                else "Protegido: mantiene la política actual de su módulo."
            )
            if actor.get("empresa_id") is None or p["modulo"] != "SAAS":
                permisos.append(p)
        return {"roles": roles, "permisos": permisos}


@controlado("CAMBIAR_PLANTILLA_RECHAZADO")
def actualizar_plantilla(actor, rid, datos):
    if actor.get("empresa_id") is not None:
        raise HTTPException(403, "Sólo AS mantiene plantillas globales.")
    revoco = False
    with escritura(actor, None, None) as cur:
        cur.execute("SELECT * FROM rol WHERE id=%s FOR UPDATE", (rid,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "Rol no encontrado.")
        if datos.permiso_ids is None:
            if datos.nombre is not None and datos.nombre != r["nombre"]:
                raise HTTPException(
                    409,
                    "Los nombres y capacidades de roles base o heredados se conservan.",
                )
            if (
                datos.activo is not None
                and datos.activo != r["activo"]
                and r["nombre"] in ROLES_BASE
            ):
                raise HTTPException(409, "Los roles base están protegidos.")
            nuevo_activo = r["activo"] if datos.activo is None else datos.activo
            nueva_desc = (
                r["descripcion"] if datos.descripcion is None else datos.descripcion
            )
            if nuevo_activo != r["activo"] and not datos.confirmar_global:
                raise HTTPException(
                    400, "Confirma el impacto global de cambiar el estado del rol."
                )
            if nuevo_activo != r["activo"] or nueva_desc != r["descripcion"]:
                cur.execute(
                    "UPDATE rol SET activo=%s,descripcion=%s WHERE id=%s",
                    (nuevo_activo, nueva_desc, rid),
                )
                if nuevo_activo != r["activo"]:
                    _revocar_rol(cur, rid)
                    revoco = True
                auditar(
                    cur,
                    actor,
                    None,
                    "EDITAR_PLANTILLA",
                    modulo="ROLES",
                    descripcion=f'Plantilla {r["nombre"]} (ID {rid}): metadatos o estado actualizados.',
                )
        else:
            if r["nombre"] != "ADMINISTRADOR" or not r["activo"]:
                raise HTTPException(
                    409,
                    "Sólo la plantilla AD permite configurar las capacidades señaladas.",
                )
            if not datos.confirmar_global:
                raise HTTPException(
                    400, "Confirma que el cambio afecta a todas las empresas."
                )
            nuevos = set(datos.permiso_ids)
            cur.execute(
                "SELECT permiso_id FROM rol_permiso WHERE rol_id=%s AND activo", (rid,)
            )
            actuales = {p["permiso_id"] for p in cur.fetchall()}
            cur.execute(
                "SELECT id FROM permiso WHERE accion=ANY(%s) AND activo",
                (list(CONFIGURABLES),),
            )
            editables = {p["id"] for p in cur.fetchall()}
            if (nuevos ^ actuales) - editables:
                raise HTTPException(
                    409, "No se pueden cambiar permisos protegidos o desconocidos."
                )
            if nuevos != actuales:
                cur.execute(
                    "UPDATE rol_permiso SET activo=FALSE WHERE rol_id=%s AND permiso_id=ANY(%s)",
                    (rid, list(editables)),
                )
                for pid in nuevos & editables:
                    cur.execute(
                        """INSERT INTO rol_permiso(rol_id,permiso_id,activo) VALUES(%s,%s,TRUE)
                        ON CONFLICT(rol_id,permiso_id) DO UPDATE SET activo=TRUE""",
                        (rid, pid),
                    )
                _revocar_rol(cur, rid)
                revoco = True
                auditar(
                    cur,
                    actor,
                    None,
                    "CAMBIAR_PERMISOS_PLANTILLA",
                    modulo="ROLES",
                    descripcion=f'Plantilla {r["nombre"]} (ID {rid}): permisos configurables actualizados.',
                )
        return {
            "mensaje": "Plantilla actualizada."
            + (" Las cuentas afectadas deben ingresar nuevamente." if revoco else "")
        }


def _revocar_rol(cur, rid):
    cur.execute(
        """SELECT u.id,u.empresa_id FROM usuario u JOIN usuario_rol ur ON ur.usuario_id=u.id AND ur.activo
        WHERE ur.rol_id=%s ORDER BY u.id FOR UPDATE OF u""",
        (rid,),
    )
    usuarios = [dict(u) for u in cur.fetchall()]
    for u in usuarios:
        invalidar_accesos(cur, u["id"], u["empresa_id"])
