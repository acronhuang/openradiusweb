"""HTTP layer for the certificates feature.

Thin handlers — parse → call service → wrap. The download handler is
the only one that produces a non-JSON response (PEM bytes).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.auth import get_current_user, require_admin
from orw_common.database import get_db
from orw_common.models.certificate import (
    GenerateCARequest,
    GenerateServerRequest,
    ImportCertRequest,
)

from . import service

router = APIRouter(prefix="/certificates")


@router.get("")
async def list_certificates(
    cert_type: str | None = None,
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.list_certs(
        db,
        tenant_id=user["tenant_id"],
        cert_type=cert_type, enabled=enabled,
        page=page, page_size=page_size,
    )


@router.get("/{cert_id}")
async def get_certificate(
    cert_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.get_cert_detail(
        db, tenant_id=user["tenant_id"], cert_id=cert_id,
    )


@router.post("/generate-ca", status_code=201)
async def generate_ca_cert(
    req: GenerateCARequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    return await service.generate_ca(
        db, user, req=req, client_ip=_client_ip(request),
    )


@router.post("/generate-server", status_code=201)
async def generate_server_cert(
    req: GenerateServerRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    return await service.generate_server(
        db, user, req=req, client_ip=_client_ip(request),
    )


@router.post("/import", status_code=201)
async def import_certificate(
    req: ImportCertRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    return await service.import_cert(
        db, user, req=req, client_ip=_client_ip(request),
    )


@router.put("/{cert_id}/activate")
async def activate_certificate(
    cert_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    return await service.activate_cert(
        db, user, cert_id=cert_id, client_ip=_client_ip(request),
    )


@router.delete("/{cert_id}", status_code=204)
async def delete_certificate(
    cert_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    await service.delete_cert(
        db, user, cert_id=cert_id, client_ip=_client_ip(request),
    )


@router.get("/{cert_id}/download")
async def download_certificate(
    cert_id: UUID,
    include_key: bool = Query(False, description="Include private key in download"),
    include_chain: bool = Query(False, description="Include certificate chain"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    out = await service.download_cert(
        db,
        tenant_id=user["tenant_id"], cert_id=cert_id,
        include_key=include_key, include_chain=include_chain,
    )
    return Response(
        content=out["pem"],
        media_type="application/x-pem-file",
        headers={
            "Content-Disposition": f"attachment; filename={out['filename']}",
        },
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
