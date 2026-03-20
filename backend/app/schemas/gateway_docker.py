"""Schemas for gateway Docker spin-up API payloads."""

from __future__ import annotations

from pydantic import model_validator
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

    @model_validator(mode="after")
    def _require_user_ids_for_channels(self) -> "GatewaySpinUpCreate":
        if self.discord_bot_token and not self.discord_user_ids:
            raise ValueError(
                "discord_user_ids is required when discord_bot_token is provided."
            )
        if self.telegram_bot_token and not self.telegram_user_ids:
            raise ValueError(
                "telegram_user_ids is required when telegram_bot_token is provided."
            )
        return self


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
    unpaired: list[str] = []  # gateway IDs with no main agent
