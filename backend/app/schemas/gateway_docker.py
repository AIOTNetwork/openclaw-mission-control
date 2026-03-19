"""Schemas for gateway Docker spin-up API payloads."""

from __future__ import annotations

from sqlmodel import SQLModel

from app.schemas.gateways import GatewayRead


class GatewaySpinUpCreate(SQLModel):
    """Payload for spinning up a new managed gateway container."""

    name: str
    anthropic_api_key: str
    gateway_port: int | None = None
    max_concurrent_agents: int = 4
    discord_bot_token: str | None = None
    telegram_bot_token: str | None = None
    discord_user_ids: list[str] | None = None
    telegram_user_ids: list[str] | None = None


class GatewaySpinUpResponse(SQLModel):
    """Response from a gateway spin-up operation."""

    gateway: GatewayRead
    status: str  # "ready" | "ready_unpaired" | "error"
    message: str | None = None


class DockerStatusResponse(SQLModel):
    """Docker availability and image status."""

    docker_available: bool
    image_exists: bool


class ContainerStatusResponse(SQLModel):
    """Whether a single gateway container is running."""

    running: bool


class BatchContainerStatusResponse(SQLModel):
    """Running status for all managed gateway containers."""

    statuses: dict[str, bool]
