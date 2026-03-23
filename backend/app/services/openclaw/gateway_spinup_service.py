"""Orchestrator for spinning up managed OpenClaw gateway containers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from app.core.auth import AuthContext
from app.core.config import settings
from app.core.time import utcnow
from app.db import crud
from app.models.gateways import Gateway
from app.schemas.gateway_docker import GatewaySpinUpCreate, GatewaySpinUpResponse
from app.schemas.gateways import GatewayRead
from app.services.openclaw.admin_service import GatewayAdminLifecycleService
from app.services.openclaw.docker_service import DockerError, OpenClawDockerService

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_ROOT = "~/.openclaw"


class GatewaySpinUpService:
    """Orchestrates the full spin-up flow for a managed gateway."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.docker = OpenClawDockerService()

    async def spin_up(
        self,
        payload: GatewaySpinUpCreate,
        *,
        organization_id: UUID,
        auth: AuthContext,
    ) -> GatewaySpinUpResponse:
        """Spin up a new managed OpenClaw gateway container end-to-end."""
        org_id = organization_id
        name = payload.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Gateway name is required.",
            )

        # 1. Feature flag check
        if not settings.openclaw_docker_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Docker management not available. Set OPENCLAW_DOCKER_ENABLED=true.",
            )

        # 2. Docker availability
        if not self.docker.check_docker_available():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Docker daemon is not reachable.",
            )

        # 3. Image check / build
        if not self.docker.check_image_exists():
            try:
                self.docker.build_image(settings.openclaw_repo_path)
            except DockerError as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to build OpenClaw image: {exc}",
                ) from exc

        # 4. Port allocation
        gateway_port = payload.gateway_port
        if gateway_port is None:
            # Collect ports already assigned to existing gateways (including
            # stopped containers) so we don't hand out a conflicting port.
            import re

            existing_gateways = await Gateway.objects.filter_by(
                organization_id=org_id,
            ).all(self.session)
            reserved_ports: set[int] = set()
            for gw in existing_gateways:
                if gw.url:
                    m = re.search(r":(\d+)$", gw.url)
                    if m:
                        gw_port = int(m.group(1))
                        reserved_ports.add(gw_port)
                        reserved_ports.add(gw_port + 100)  # bridge port

            try:
                gateway_port = self.docker.find_available_port(
                    reserved_ports=reserved_ports,
                )
            except DockerError as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=str(exc),
                ) from exc
        bridge_port = gateway_port + 100

        # 5. Auth token
        auth_token = OpenClawDockerService.generate_auth_token()

        # 6. Config directory
        # container_config_dir: where the backend container writes files
        # host_config_dir: the same directory as seen by the Docker host
        #                  (used for docker compose --env-file and stored in DB)
        container_config_dir = str(Path(settings.openclaw_config_base_dir) / name)
        host_config_dir = str(Path(settings.openclaw_config_host_dir) / name)

        if Path(container_config_dir).exists():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Gateway name '{name}' already in use (config directory exists).",
            )

        # 7. Generate config files (write to container-visible path)
        try:
            self.docker.generate_config_files(
                container_config_dir,
                name=name,
                anthropic_api_key=payload.anthropic_api_key,
                gateway_port=gateway_port,
                bridge_port=bridge_port,
                auth_token=auth_token,
                max_concurrent_agents=payload.max_concurrent_agents,
                discord_bot_token=payload.discord_bot_token,
                telegram_bot_token=payload.telegram_bot_token,
                discord_user_ids=payload.discord_user_ids,
                telegram_user_ids=payload.telegram_user_ids,
                host_config_dir=host_config_dir,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate config files: {exc}",
            ) from exc

        # 8. Start container
        # docker compose runs inside this container, so use container paths
        # for env-file and compose file. But the .env *contents* reference
        # host paths (for volume mounts that Docker daemon resolves on the host).
        try:
            project_name = self.docker.start_container(
                name=name,
                host_config_dir=container_config_dir,
                gateway_port=gateway_port,
                bridge_port=bridge_port,
                auth_token=auth_token,
                repo_path=settings.openclaw_repo_path,
            )
        except DockerError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to start container: {exc}",
            ) from exc

        # 9. Wait for gateway ready
        # From inside the MC container, the host's ports are reachable via
        # host.docker.internal (Docker Desktop) or the host gateway IP.
        gateway_url = f"ws://host.docker.internal:{gateway_port}"
        ready = self.docker.wait_for_ready(gateway_url, auth_token, timeout=60)

        if not ready:
            # Leave container running for debug, but report timeout
            logger.warning("Gateway %s did not become ready within timeout", name)

        # 10. Create DB record
        gateway_id = uuid4()
        now = utcnow()
        gateway = await crud.create(
            self.session,
            Gateway,
            id=gateway_id,
            organization_id=org_id,
            name=name,
            url=gateway_url,
            token=auth_token,
            disable_device_pairing=True,
            workspace_root=DEFAULT_WORKSPACE_ROOT,
            allow_insecure_tls=False,
            managed=True,
            docker_project_name=project_name,
            config_dir=host_config_dir,
            created_at=now,
            updated_at=now,
        )

        # 11. Provision main agent (best-effort if gateway not ready)
        result_status = "ready" if ready else "error"
        message = None
        if ready:
            try:
                lifecycle = GatewayAdminLifecycleService(self.session)
                await lifecycle.ensure_main_agent(gateway, auth, action="provision")
                # Run a full template sync with rotate_tokens to ensure
                # the agent gets a proper auth token registered in MC.
                from app.services.openclaw.session_service import GatewayTemplateSyncQuery

                await lifecycle.sync_templates(
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
                result_status = "ready_unpaired"
                message = "Gateway is running but main agent provisioning failed."
                logger.warning("Main agent provisioning failed for %s", name)
        else:
            message = "Gateway container started but did not become responsive within 60s."

        gateway_read = GatewayRead.model_validate(gateway)
        return GatewaySpinUpResponse(
            gateway=gateway_read,
            status=result_status,
            message=message,
        )
