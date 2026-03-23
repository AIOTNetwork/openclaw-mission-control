"""Thin API wrappers for gateway CRUD and template synchronization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from sqlmodel import col

from app.api.deps import require_org_admin
from app.core.auth import AuthContext, get_auth_context
from app.db import crud
from app.db.pagination import paginate
from app.db.session import get_session
from app.models.agents import Agent
from app.models.gateways import Gateway
from app.models.skills import GatewayInstalledSkill
from app.schemas.common import OkResponse
from app.schemas.gateway_docker import (
    BatchContainerStatusResponse,
    DockerStatusResponse,
    GatewaySpinUpCreate,
    GatewaySpinUpResponse,
)
from app.schemas.gateways import (
    GatewayCreate,
    GatewayRead,
    GatewayTemplatesSyncResult,
    GatewayUpdate,
)
from app.schemas.pagination import DefaultLimitOffsetPage
from app.services.openclaw.admin_service import GatewayAdminLifecycleService
from app.services.openclaw.docker_service import OpenClawDockerService
from app.services.openclaw.gateway_spinup_service import GatewaySpinUpService
from app.services.openclaw.session_service import GatewayTemplateSyncQuery

if TYPE_CHECKING:
    from fastapi_pagination.limit_offset import LimitOffsetPage
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.services.organizations import OrganizationContext

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/gateways", tags=["gateways"])
SESSION_DEP = Depends(get_session)
AUTH_DEP = Depends(get_auth_context)
ORG_ADMIN_DEP = Depends(require_org_admin)
INCLUDE_MAIN_QUERY = Query(default=True)
RESET_SESSIONS_QUERY = Query(default=False)
ROTATE_TOKENS_QUERY = Query(default=False)
FORCE_BOOTSTRAP_QUERY = Query(default=False)
OVERWRITE_QUERY = Query(default=False)
LEAD_ONLY_QUERY = Query(default=False)
BOARD_ID_QUERY = Query(default=None)
_RUNTIME_TYPE_REFERENCES = (UUID,)


def _template_sync_query(
    *,
    include_main: bool = INCLUDE_MAIN_QUERY,
    lead_only: bool = LEAD_ONLY_QUERY,
    reset_sessions: bool = RESET_SESSIONS_QUERY,
    rotate_tokens: bool = ROTATE_TOKENS_QUERY,
    force_bootstrap: bool = FORCE_BOOTSTRAP_QUERY,
    overwrite: bool = OVERWRITE_QUERY,
    board_id: UUID | None = BOARD_ID_QUERY,
) -> GatewayTemplateSyncQuery:
    return GatewayTemplateSyncQuery(
        include_main=include_main,
        lead_only=lead_only,
        reset_sessions=reset_sessions,
        rotate_tokens=rotate_tokens,
        force_bootstrap=force_bootstrap,
        overwrite=overwrite,
        board_id=board_id,
    )


SYNC_QUERY_DEP = Depends(_template_sync_query)


@router.get("", response_model=DefaultLimitOffsetPage[GatewayRead])
async def list_gateways(
    session: AsyncSession = SESSION_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> LimitOffsetPage[GatewayRead]:
    """List gateways for the caller's organization."""
    statement = (
        Gateway.objects.filter_by(organization_id=ctx.organization.id)
        .order_by(col(Gateway.created_at).desc())
        .statement
    )

    return await paginate(session, statement)


@router.post("", response_model=GatewayRead)
async def create_gateway(
    payload: GatewayCreate,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> Gateway:
    """Create a gateway and provision or refresh its main agent."""
    service = GatewayAdminLifecycleService(session)
    await service.assert_gateway_runtime_compatible(
        url=payload.url,
        token=payload.token,
        allow_insecure_tls=payload.allow_insecure_tls,
        disable_device_pairing=payload.disable_device_pairing,
    )
    data = payload.model_dump()
    gateway_id = uuid4()
    data["id"] = gateway_id
    data["organization_id"] = ctx.organization.id
    gateway = await crud.create(session, Gateway, **data)
    await service.ensure_main_agent(gateway, auth, action="provision")
    return gateway


@router.get("/docker/status", response_model=DockerStatusResponse)
async def docker_status(
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> DockerStatusResponse:
    """Check Docker daemon availability and OpenClaw image status."""
    docker = OpenClawDockerService()
    available = docker.check_docker_available()
    image_exists = docker.check_image_exists() if available else False
    return DockerStatusResponse(docker_available=available, image_exists=image_exists)


@router.post("/docker/build-image", response_model=OkResponse)
async def build_image(
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Build the openclaw:local Docker image from the mounted repo."""
    from fastapi import HTTPException

    from app.core.config import settings
    from app.services.openclaw.docker_service import DockerError

    docker = OpenClawDockerService()
    if not docker.check_docker_available():
        raise HTTPException(
            status_code=503,
            detail="Docker daemon is not reachable.",
        )
    try:
        docker.build_image(settings.openclaw_repo_path)
    except DockerError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Image build failed: {exc}",
        ) from exc
    return OkResponse()


@router.post("/spin-up", response_model=GatewaySpinUpResponse)
async def spin_up_gateway(
    payload: GatewaySpinUpCreate,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewaySpinUpResponse:
    """Spin up a new managed OpenClaw gateway container."""
    service = GatewaySpinUpService(session)
    return await service.spin_up(
        payload,
        organization_id=ctx.organization.id,
        auth=auth,
    )


@router.get("/docker/container-statuses", response_model=BatchContainerStatusResponse)
async def container_statuses(
    session: AsyncSession = SESSION_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> BatchContainerStatusResponse:
    """Return running status and unpaired list for all managed gateway containers."""
    managed_gateways = await Gateway.objects.filter_by(
        organization_id=ctx.organization.id,
        managed=True,
    ).all(session)
    docker = OpenClawDockerService()
    statuses: dict[str, bool] = {}
    unpaired: list[str] = []
    lifecycle = GatewayAdminLifecycleService(session)
    for gw in managed_gateways:
        if gw.docker_project_name:
            statuses[str(gw.id)] = docker.check_container_running(gw.docker_project_name)
        main_agent = await lifecycle.find_main_agent(gw)
        if main_agent is None:
            unpaired.append(str(gw.id))
    return BatchContainerStatusResponse(statuses=statuses, unpaired=unpaired)


@router.post("/{gateway_id}/docker/stop", response_model=OkResponse)
async def stop_gateway_container(
    gateway_id: UUID,
    session: AsyncSession = SESSION_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Stop a managed gateway container."""
    from fastapi import HTTPException

    service = GatewayAdminLifecycleService(session)
    gateway = await service.require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    if not gateway.managed or not gateway.docker_project_name:
        raise HTTPException(
            status_code=400,
            detail="Gateway is not a managed Docker container.",
        )
    docker = OpenClawDockerService()
    docker.pause_container(gateway.docker_project_name)
    # Mark all agents on this gateway as offline since the container is stopped.
    agents = await Agent.objects.filter_by(gateway_id=gateway.id).all(session)
    for agent in agents:
        if agent.status == "online":
            agent.status = "offline"
            session.add(agent)
    await session.commit()
    return OkResponse()


@router.post("/{gateway_id}/docker/start", response_model=OkResponse)
async def start_gateway_container(
    gateway_id: UUID,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Start a stopped managed gateway container and re-provision agents."""
    from fastapi import HTTPException

    from app.services.openclaw.docker_service import DockerError

    service = GatewayAdminLifecycleService(session)
    gateway = await service.require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    if not gateway.managed or not gateway.docker_project_name:
        raise HTTPException(
            status_code=400,
            detail="Gateway is not a managed Docker container.",
        )
    docker = OpenClawDockerService()
    # Try `docker compose start` first (resumes a stopped container).
    # If the container was removed (e.g. after a system restart), fall back
    # to `restart_container` which does `docker compose up -d`.
    if not docker.resume_container(gateway.docker_project_name):
        if not gateway.config_dir:
            raise HTTPException(
                status_code=400,
                detail="Gateway is missing config directory.",
            )
        from app.core.config import settings

        if not settings.openclaw_docker_enabled:
            raise HTTPException(
                status_code=503,
                detail="Docker management is not enabled on this server.",
            )
        container_config_dir = gateway.config_dir.replace(
            settings.openclaw_config_host_dir,
            settings.openclaw_config_base_dir,
            1,
        )
        try:
            docker.restart_container(
                gateway.docker_project_name,
                container_config_dir,
                settings.openclaw_repo_path,
            )
        except DockerError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start container: {exc}",
            ) from exc

    # Re-provision agents so they check in and flip back to online.
    # The stop endpoint marks agents offline; this is the reverse path.
    ready = docker.wait_for_ready(
        gateway.url, gateway.token or "", timeout=30,
    )
    if ready:
        try:
            await service.ensure_main_agent(gateway, auth, action="provision")
            await service.sync_templates(
                gateway,
                query=GatewayTemplateSyncQuery(
                    include_main=True,
                    lead_only=False,
                    reset_sessions=False,
                    rotate_tokens=True,
                    force_bootstrap=True,
                    overwrite=False,
                    board_id=None,
                ),
                auth=auth,
            )
        except Exception:
            logger.warning(
                "Post-start provisioning failed for gateway %s; "
                "agents may remain offline until manual re-provision.",
                gateway_id,
                exc_info=True,
            )
    return OkResponse()


@router.post("/{gateway_id}/docker/provision", response_model=OkResponse)
async def provision_gateway(
    gateway_id: UUID,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Retry agent provisioning for a managed gateway whose container is running."""
    from fastapi import HTTPException

    service = GatewayAdminLifecycleService(session)
    gateway = await service.require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    if not gateway.managed or not gateway.docker_project_name:
        raise HTTPException(
            status_code=400,
            detail="Gateway is not a managed Docker container.",
        )
    docker = OpenClawDockerService()
    if not docker.check_container_running(gateway.docker_project_name):
        raise HTTPException(
            status_code=409,
            detail="Container is not running. Start it first.",
        )
    ready = docker.wait_for_ready(
        gateway.url, gateway.token or "", timeout=30,
    )
    if not ready:
        raise HTTPException(
            status_code=503,
            detail="Gateway is not responsive yet. Try again shortly.",
        )
    try:
        await service.ensure_main_agent(gateway, auth, action="provision")
        from app.services.openclaw.session_service import GatewayTemplateSyncQuery

        await service.sync_templates(
            gateway,
            query=GatewayTemplateSyncQuery(
                include_main=True,
                lead_only=False,
                reset_sessions=False,
                rotate_tokens=True,
                force_bootstrap=True,
                overwrite=False,
                board_id=None,
            ),
            auth=auth,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Provisioning failed: {exc}",
        ) from exc
    return OkResponse()


@router.get("/{gateway_id}", response_model=GatewayRead)
async def get_gateway(
    gateway_id: UUID,
    session: AsyncSession = SESSION_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> Gateway:
    """Return one gateway by id for the caller's organization."""
    service = GatewayAdminLifecycleService(session)
    gateway = await service.require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    return gateway


@router.patch("/{gateway_id}", response_model=GatewayRead)
async def update_gateway(
    gateway_id: UUID,
    payload: GatewayUpdate,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> Gateway:
    """Patch a gateway and refresh the main-agent provisioning state."""
    service = GatewayAdminLifecycleService(session)
    gateway = await service.require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    updates = payload.model_dump(exclude_unset=True)
    if (
        "url" in updates
        or "token" in updates
        or "allow_insecure_tls" in updates
        or "disable_device_pairing" in updates
    ):
        raw_next_url = updates.get("url", gateway.url)
        next_url = raw_next_url.strip() if isinstance(raw_next_url, str) else ""
        next_token = updates.get("token", gateway.token)
        next_allow_insecure_tls = bool(
            updates.get("allow_insecure_tls", gateway.allow_insecure_tls),
        )
        next_disable_device_pairing = bool(
            updates.get("disable_device_pairing", gateway.disable_device_pairing),
        )
        if next_url:
            await service.assert_gateway_runtime_compatible(
                url=next_url,
                token=next_token,
                allow_insecure_tls=next_allow_insecure_tls,
                disable_device_pairing=next_disable_device_pairing,
            )
    await crud.patch(session, gateway, updates)
    await service.ensure_main_agent(gateway, auth, action="update")
    return gateway


@router.post("/{gateway_id}/templates/sync", response_model=GatewayTemplatesSyncResult)
async def sync_gateway_templates(
    gateway_id: UUID,
    sync_query: GatewayTemplateSyncQuery = SYNC_QUERY_DEP,
    session: AsyncSession = SESSION_DEP,
    auth: AuthContext = AUTH_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GatewayTemplatesSyncResult:
    """Sync templates for a gateway and optionally rotate runtime settings."""
    service = GatewayAdminLifecycleService(session)
    gateway = await service.require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    return await service.sync_templates(gateway, query=sync_query, auth=auth)


@router.delete("/{gateway_id}", response_model=OkResponse)
async def delete_gateway(
    gateway_id: UUID,
    session: AsyncSession = SESSION_DEP,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> OkResponse:
    """Delete a gateway in the caller's organization."""
    service = GatewayAdminLifecycleService(session)
    gateway = await service.require_gateway(
        gateway_id=gateway_id,
        organization_id=ctx.organization.id,
    )
    main_agent = await service.find_main_agent(gateway)
    if main_agent is not None:
        await service.clear_agent_foreign_keys(agent_id=main_agent.id)
        await session.delete(main_agent)

    duplicate_main_agents = await Agent.objects.filter_by(
        gateway_id=gateway.id,
        board_id=None,
    ).all(session)
    for agent in duplicate_main_agents:
        if main_agent is not None and agent.id == main_agent.id:
            continue
        await service.clear_agent_foreign_keys(agent_id=agent.id)
        await session.delete(agent)

    # NOTE: The migration declares `ondelete="CASCADE"` for gateway_installed_skills.gateway_id,
    # but some backends/test environments (e.g. SQLite without FK pragma) may not
    # enforce cascades. Delete rows explicitly to guarantee cleanup semantics.
    installed_skills = await GatewayInstalledSkill.objects.filter_by(
        gateway_id=gateway.id,
    ).all(session)
    for installed_skill in installed_skills:
        await session.delete(installed_skill)

    # If managed, stop and remove the Docker container.
    if gateway.managed and gateway.docker_project_name:
        docker = OpenClawDockerService()
        try:
            docker.remove_container(gateway.docker_project_name)
        except Exception:
            logger.warning(
                "Failed to remove Docker container for managed gateway %s",
                gateway.id,
            )

    await session.delete(gateway)
    await session.commit()
    return OkResponse()
