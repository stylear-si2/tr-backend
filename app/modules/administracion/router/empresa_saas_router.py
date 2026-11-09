"""Operaciones de plataforma; nunca exponen datos comerciales de empresas."""

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator
from psycopg2 import IntegrityError

from app.core.contexto_empresa import exigir_empresa_id, normalizar_codigo_empresa
from app.core.security import hashear_password
from app.core.plan_empresa import LIMITES_ADMITIDOS, FUNCIONES_ADMITIDAS, catalogo_capacidades, capacidades_empresa
from app.modules.administracion.services import suscripcion_stripe_service
from app.modules.autenticacion.dependencies.usuario_actual import obtener_usuario_actual
from app.modules.autenticacion.dependencies.admin_required import requerir_admin, requerir_admin_saas
from app.modules.administracion.repositories.empresa_saas_repository import (
    asignar_plan_empresa, cambiar_estado_empresa, crear_empresa_con_admin,
    crear_plan, listar_empresas, listar_planes, obtener_plan_empresa, editar_plan, editar_empresa, listar_eventos_plataforma,
    previsualizar_plan, historial_plan, historial_suscripciones, resumen_planes, empresas_por_plan,
)

router = APIRouter(prefix="/api/v1", tags=["Plataforma SaaS"])


class PlanAlta(BaseModel):
    model_config = ConfigDict(extra='forbid')
    codigo: str
    nombre: str = Field(min_length=2, max_length=120)
    limites: dict[str, StrictInt] = Field(default_factory=dict)
    funciones: dict[str, StrictBool] = Field(default_factory=dict)
    publico: bool = False
    descripcion: str = Field(default='', max_length=4000)
    precio: Decimal = Field(default=Decimal('0'), ge=0, max_digits=12, decimal_places=2)
    moneda: str = Field(default='BOB', pattern=r'^[A-Z]{3}$')
    condiciones: str = Field(default='', max_length=10000)
    stripe_price_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode='after')
    def validar_publicacion(self):
        if self.publico and (self.codigo == 'STYLEAR_LEGADO' or self.limites.get('usuarios', 1) < 1):
            raise ValueError('Un plan público debe permitir la cuenta administradora y no puede ser el plan de transición.')
        return self

    @field_validator("codigo")
    @classmethod
    def validar_codigo(cls, valor: str) -> str:
        codigo = valor.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{2,50}", codigo):
            raise ValueError("Codigo de plan invalido.")
        return codigo

    @field_validator("limites")
    @classmethod
    def validar_limites(cls, valor: dict[str, int]) -> dict[str, int]:
        if set(valor) - LIMITES_ADMITIDOS:
            raise ValueError("Límites admitidos: " + ", ".join(sorted(LIMITES_ADMITIDOS)))
        if any(cupo < 0 for cupo in valor.values()):
            raise ValueError("Los limites no pueden ser negativos.")
        return valor


    @field_validator("funciones")
    @classmethod
    def validar_funciones(cls, valor: dict[str, bool]) -> dict[str, bool]:
        if set(valor) - FUNCIONES_ADMITIDAS:
            raise ValueError("Funciones admitidas: " + ", ".join(sorted(FUNCIONES_ADMITIDAS)))
        return valor


class PlanEdicion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    codigo: str | None = None
    nombre: str | None = None
    descripcion: str | None = Field(default=None,max_length=4000)
    precio: Decimal | None = Field(default=None,ge=0,max_digits=12,decimal_places=2)
    moneda: str | None = None
    condiciones: str | None = Field(default=None,max_length=10000)
    stripe_price_id: str | None = Field(default=None, max_length=255)
    limites: dict[str, StrictInt] | None = None
    funciones: dict[str, StrictBool] | None = None
    publico: StrictBool | None = None
    activo: StrictBool | None = None
    token_previsualizacion: str | None = None


class EmpresaEdicion(BaseModel):
    nombre: str = Field(min_length=2, max_length=150)


class PlanRespuesta(PlanAlta):
    id: int
    activo: bool
    revision: int = 1
    model_config = ConfigDict(extra='ignore')


class AdministradorInicial(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    apellido: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=4, max_length=80)
    correo: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=8)

    @field_validator("username", "correo")
    @classmethod
    def normalizar_identidad(cls, valor: str) -> str:
        return valor.strip().lower()


class EmpresaAlta(BaseModel):
    codigo: str
    nombre: str = Field(min_length=2, max_length=150)
    plan_id: int = Field(gt=0)
    fecha_inicio: date = Field(default_factory=date.today)
    fecha_vencimiento: date | None = None
    administrador: AdministradorInicial

    @field_validator("codigo")
    @classmethod
    def validar_codigo(cls, valor: str) -> str:
        return normalizar_codigo_empresa(valor)

    @model_validator(mode="after")
    def validar_fechas(self) -> "EmpresaAlta":
        if self.fecha_vencimiento and self.fecha_vencimiento < self.fecha_inicio:
            raise ValueError("La fecha de vencimiento precede al inicio.")
        return self


class EmpresaEstado(BaseModel):
    estado: Literal["ACTIVA", "SUSPENDIDA", "CANCELADA"]


class AsignarPlan(BaseModel):
    plan_id: int = Field(gt=0)
    fecha_inicio: date = Field(default_factory=date.today)
    fecha_vencimiento: date | None = None

    @model_validator(mode="after")
    def validar_fechas(self) -> "AsignarPlan":
        if self.fecha_vencimiento and self.fecha_vencimiento < self.fecha_inicio:
            raise ValueError("La fecha de vencimiento precede al inicio.")
        return self


@router.get("/saas/planes", response_model=list[PlanRespuesta])
def listar_planes_endpoint(_: dict[str, object] = Depends(requerir_admin_saas)):
    return listar_planes()


@router.post("/saas/planes", response_model=PlanRespuesta, status_code=201)
def crear_plan_endpoint(request: PlanAlta, actor: dict[str, object] = Depends(requerir_admin_saas)):
    try:
        return crear_plan(request.codigo, request.nombre.strip(), request.limites, request.funciones, int(actor["id"]), request.publico,
                          descripcion=request.descripcion,precio=request.precio,moneda=request.moneda,
                          condiciones=request.condiciones,stripe_price_id=request.stripe_price_id)
    except ValueError as error:
        raise HTTPException(422,str(error)) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="El codigo de plan ya existe.") from error


@router.get("/saas/empresas")
def listar_empresas_endpoint(_: dict[str, object] = Depends(requerir_admin_saas)):
    return listar_empresas()


@router.post("/saas/empresas", status_code=201)
def crear_empresa_endpoint(request: EmpresaAlta, actor: dict[str, object] = Depends(requerir_admin_saas)):
    try:
        password_hash = hashear_password(request.administrador.password)
        datos = {
            "codigo": request.codigo,
            "nombre": request.nombre.strip(),
            "plan_id": request.plan_id,
            "fecha_inicio": request.fecha_inicio,
            "fecha_vencimiento": request.fecha_vencimiento,
            "admin_nombre": request.administrador.nombre.strip(),
            "admin_apellido": request.administrador.apellido.strip(),
            "admin_username": request.administrador.username,
            "admin_correo": request.administrador.correo,
        }
        return crear_empresa_con_admin(datos, password_hash, int(actor["id"]))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Codigo, usuario o correo ya registrado.") from error


@router.patch("/saas/empresas/{empresa_id}/estado")
def cambiar_estado_empresa_endpoint(
    empresa_id: int, request: EmpresaEstado,
    actor: dict[str, object] = Depends(requerir_admin_saas),
):
    try:
        empresa = cambiar_estado_empresa(empresa_id, request.estado, int(actor["id"]))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    return empresa


@router.post("/saas/empresas/{empresa_id}/suscripcion")
def asignar_plan_empresa_endpoint(
    empresa_id: int, request: AsignarPlan,
    actor: dict[str, object] = Depends(requerir_admin_saas),
):
    try:
        suscripcion = asignar_plan_empresa(
            empresa_id, request.plan_id, request.fecha_inicio, request.fecha_vencimiento, int(actor["id"])
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if suscripcion is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    return suscripcion


@router.get("/mi-empresa/plan")
def consultar_mi_plan(usuario: dict[str, object] = Depends(requerir_admin)):
    plan = obtener_plan_empresa(exigir_empresa_id(usuario))
    if plan is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    return plan


@router.get('/mi-empresa/suscripcion/stripe')
def consultar_suscripcion_stripe(usuario=Depends(requerir_admin)):
    return suscripcion_stripe_service.estado(exigir_empresa_id(usuario))


class CheckoutPlanRequest(BaseModel):
    plan_id: int | None = Field(default=None, gt=0)


@router.post('/mi-empresa/suscripcion/stripe/checkout')
def checkout_suscripcion_stripe(datos: CheckoutPlanRequest | None = None, usuario=Depends(requerir_admin)):
    return suscripcion_stripe_service.checkout(exigir_empresa_id(usuario), datos.plan_id if datos else None,
                                                usuario_pagador=usuario['id'])


@router.post('/mi-empresa/suscripcion/stripe/portal')
def portal_suscripcion_stripe(usuario=Depends(requerir_admin)):
    return suscripcion_stripe_service.portal(exigir_empresa_id(usuario))


@router.put("/saas/planes/{plan_id}", response_model=PlanRespuesta)
def editar_plan_endpoint(plan_id: int, request: PlanEdicion, actor: dict = Depends(requerir_admin_saas)):
    try:
        plan = editar_plan(plan_id, request.model_dump(exclude_unset=True), int(actor["id"]))
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="El código de plan ya existe.") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan no encontrado.")
    return plan


@router.patch("/saas/empresas/{empresa_id}")
def editar_empresa_endpoint(empresa_id: int, request: EmpresaEdicion, actor: dict = Depends(requerir_admin_saas)):
    empresa = editar_empresa(empresa_id, request.nombre.strip(), int(actor["id"]))
    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    return empresa


@router.get("/saas/eventos")
def eventos_plataforma_endpoint(_: dict = Depends(requerir_admin_saas)):
    return listar_eventos_plataforma()


@router.get('/saas/planes/catalogo')
def catalogo_planes(_: dict = Depends(requerir_admin_saas)):
    return catalogo_capacidades()


@router.get('/saas/planes/resumen')
def resumen_planes_endpoint(_: dict = Depends(requerir_admin_saas)):
    return resumen_planes()


@router.get('/saas/planes/comparacion')
def comparar_planes(_: dict = Depends(requerir_admin_saas)):
    return {'catalogo': catalogo_capacidades(), 'planes': listar_planes()}


@router.post('/saas/planes/{plan_id}/previsualizar')
def previsualizar_plan_endpoint(plan_id: int, request: PlanEdicion, _: dict = Depends(requerir_admin_saas)):
    try:
        return previsualizar_plan(plan_id,request.model_dump(exclude_unset=True))
    except ValueError as error:
        raise HTTPException(422,str(error)) from error


@router.get('/saas/planes/{plan_id}/historial')
def historial_plan_endpoint(plan_id: int, _: dict = Depends(requerir_admin_saas)):
    return historial_plan(plan_id)


@router.get('/saas/planes/empresas')
def empresas_planes_endpoint(plan_id: int | None = Query(default=None,gt=0),
        estado: Literal['ACTIVA','SUSPENDIDA','PENDIENTE','CANCELADA'] | None = None,
        buscar: str = Query(default='',max_length=150),pagina: int = Query(default=1,ge=1),
        tamano: int = Query(default=20,ge=1,le=100), _: dict = Depends(requerir_admin_saas)):
    return empresas_por_plan(plan_id,estado,buscar,pagina,tamano)


@router.get('/saas/empresas/{empresa_id}/suscripciones')
def suscripciones_empresa_endpoint(empresa_id: int, _: dict = Depends(requerir_admin_saas)):
    return historial_suscripciones(empresa_id)


@router.get('/saas/empresas/{empresa_id}/plan')
def plan_empresa_saas(empresa_id: int, _: dict = Depends(requerir_admin_saas)):
    return capacidades_empresa(empresa_id)


@router.get('/mi-empresa/capacidades')
def capacidades_mi_empresa(usuario: dict = Depends(obtener_usuario_actual)):
    return capacidades_empresa(exigir_empresa_id(usuario))
