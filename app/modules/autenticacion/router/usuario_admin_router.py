from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Literal
from app.core.contexto_empresa import exigir_empresa_id
from app.modules.autenticacion.autorizacion import exigir_permiso
from app.modules.autenticacion.dependencies.admin_required import requerir_admin_saas
from app.modules.autenticacion.schemas.usuario_admin.usuario_admin_request import (
    ActualizarUsuarioRequest,
    CrearUsuarioAdminRequest,
)
from app.modules.autenticacion.schemas.usuario_admin.usuario_admin_response import (
    UsuarioAdminResponse,
)
from app.modules.autenticacion.repositories import (
    administracion_acceso_repository as acceso,
)

router = APIRouter(prefix="/api/v1", tags=["Usuarios"])


def admin(permiso):
    def validar(actor=Depends(exigir_permiso(permiso))):
        exigir_empresa_id(actor)
        return actor

    return validar


@router.get("/usuarios/paginados")
def pagina(
    estado: Literal["ACTIVOS", "INACTIVOS", "TODOS"] = "ACTIVOS",
    busqueda: str = Query("", max_length=160),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(25, ge=1, le=200),
    rol: str | None = Query(None, max_length=80),
    actor=Depends(admin("usuarios:ver")),
):
    return acceso.listar(
        actor, actor["empresa_id"], estado, busqueda, pagina, por_pagina, rol
    )


@router.get("/usuarios", response_model=list[UsuarioAdminResponse])
def lista(actor=Depends(admin("usuarios:ver"))):
    return acceso.listar(actor, actor["empresa_id"], por_pagina=1000000)["registros"]


@router.post("/usuarios", response_model=UsuarioAdminResponse, status_code=201)
def crear(datos: CrearUsuarioAdminRequest, actor=Depends(admin("usuarios:crear"))):
    return acceso.crear(actor, actor["empresa_id"], datos)


@router.get("/usuarios/{uid}", response_model=UsuarioAdminResponse)
def detalle(uid: int, actor=Depends(admin("usuarios:ver"))):
    return acceso.detalle(actor, actor["empresa_id"], uid)


@router.put("/usuarios/{uid}", response_model=UsuarioAdminResponse)
def editar(
    uid: int, datos: ActualizarUsuarioRequest, actor=Depends(admin("usuarios:editar"))
):
    return acceso.editar(actor, actor["empresa_id"], uid, datos)


@router.patch("/usuarios/{uid}/desactivar")
def desactivar(uid: int, actor=Depends(admin("usuarios:desactivar"))):
    return acceso.estado_usuario(actor, actor["empresa_id"], uid, False)


@router.patch("/usuarios/{uid}/activar")
def activar(uid: int, actor=Depends(admin("usuarios:activar"))):
    return acceso.estado_usuario(actor, actor["empresa_id"], uid, True)


@router.post("/usuarios/{uid}/roles/{rid}")
@router.patch("/usuarios/{uid}/roles/{rid}/activar")
def asignar(uid: int, rid: int, actor=Depends(admin("roles:asignar"))):
    return acceso.rol_usuario(actor, actor["empresa_id"], uid, rid, True)


@router.patch("/usuarios/{uid}/roles/{rid}/desactivar")
def retirar(uid: int, rid: int, actor=Depends(admin("roles:retirar"))):
    return acceso.rol_usuario(actor, actor["empresa_id"], uid, rid, False)


# AS manages only access accounts, through a separate explicit namespace.
@router.get("/saas/usuarios")
def saas_lista(
    empresa_id: int | None = Query(None, gt=0),
    estado: Literal["ACTIVOS", "INACTIVOS", "TODOS"] = "ACTIVOS",
    busqueda: str = Query("", max_length=160),
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(25, ge=1, le=200),
    rol: str | None = Query(None, max_length=80),
    actor=Depends(requerir_admin_saas),
):
    return acceso.listar(actor, empresa_id, estado, busqueda, pagina, por_pagina, rol)


@router.post("/saas/usuarios", status_code=201, response_model=UsuarioAdminResponse)
def saas_crear(
    datos: CrearUsuarioAdminRequest,
    empresa_id: int | None = Query(None, gt=0),
    actor=Depends(requerir_admin_saas),
):
    return acceso.crear(actor, empresa_id, datos)


@router.get("/saas/usuarios/{uid}", response_model=UsuarioAdminResponse)
def saas_detalle(
    uid: int,
    empresa_id: int | None = Query(None, gt=0),
    actor=Depends(requerir_admin_saas),
):
    return acceso.detalle(actor, empresa_id, uid)


@router.put("/saas/usuarios/{uid}", response_model=UsuarioAdminResponse)
def saas_editar(
    uid: int,
    datos: ActualizarUsuarioRequest,
    empresa_id: int | None = Query(None, gt=0),
    actor=Depends(requerir_admin_saas),
):
    return acceso.editar(actor, empresa_id, uid, datos)


@router.patch("/saas/usuarios/{uid}/desactivar")
def saas_desactivar(
    uid: int,
    empresa_id: int | None = Query(None, gt=0),
    actor=Depends(requerir_admin_saas),
):
    return acceso.estado_usuario(actor, empresa_id, uid, False)


@router.patch("/saas/usuarios/{uid}/activar")
def saas_activar(
    uid: int,
    empresa_id: int | None = Query(None, gt=0),
    actor=Depends(requerir_admin_saas),
):
    return acceso.estado_usuario(actor, empresa_id, uid, True)


@router.post("/saas/usuarios/{uid}/roles/{rid}")
@router.patch("/saas/usuarios/{uid}/roles/{rid}/activar")
def saas_asignar(
    uid: int,
    rid: int,
    empresa_id: int | None = Query(None, gt=0),
    actor=Depends(requerir_admin_saas),
):
    return acceso.rol_usuario(actor, empresa_id, uid, rid, True)


@router.patch("/saas/usuarios/{uid}/roles/{rid}/desactivar")
def saas_retirar(
    uid: int,
    rid: int,
    empresa_id: int | None = Query(None, gt=0),
    actor=Depends(requerir_admin_saas),
):
    return acceso.rol_usuario(actor, empresa_id, uid, rid, False)
