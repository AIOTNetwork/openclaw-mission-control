"""Discord bot token validation and guild/member listing endpoints."""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import SQLModel

from app.api.deps import require_org_admin

if TYPE_CHECKING:
    from app.services.organizations import OrganizationContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discord", tags=["discord"])
ORG_ADMIN_DEP = Depends(require_org_admin)

DISCORD_API = "https://discord.com/api/v10"
DISCORD_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Permissions: Send Messages | Read Message History | Add Reactions | Use Slash Commands
BOT_PERMISSIONS = 68672


# --- Schemas ---


class TokenInput(SQLModel):
    token: str


class BotInfo(SQLModel):
    valid: bool
    bot_username: str | None = None
    bot_id: str | None = None
    invite_url: str | None = None
    error: str | None = None


class GuildInfo(SQLModel):
    id: str
    name: str
    icon: str | None = None
    member_count: int | None = None


class GuildsResponse(SQLModel):
    guilds: list[GuildInfo]


class GuildIdInput(SQLModel):
    token: str
    guild_id: str


class MemberInfo(SQLModel):
    id: str
    username: str
    display_name: str | None = None
    avatar: str | None = None
    bot: bool = False


class MembersResponse(SQLModel):
    members: list[MemberInfo]


# --- Helpers ---


def _extract_client_id(token: str) -> str | None:
    """Extract the bot's client/application ID from a Discord bot token."""
    try:
        first_segment = token.split(".")[0]
        # Discord tokens use base64 without padding
        padded = first_segment + "=" * (-len(first_segment) % 4)
        decoded = base64.b64decode(padded).decode("utf-8")
        # Validate it's a numeric snowflake
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return None


def _build_invite_url(client_id: str) -> str:
    return (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={client_id}"
        f"&permissions={BOT_PERMISSIONS}"
        f"&scope=bot"
    )


# --- Endpoints ---


@router.post("/validate-token", response_model=BotInfo)
async def validate_discord_token(
    payload: TokenInput,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> BotInfo:
    """Validate a Discord bot token and return bot info + invite URL."""
    token = payload.token.strip()
    if not token:
        return BotInfo(valid=False, error="Token is empty.")

    try:
        async with httpx.AsyncClient(timeout=DISCORD_TIMEOUT) as client:
            resp = await client.get(
                f"{DISCORD_API}/users/@me",
                headers={"Authorization": f"Bot {token}"},
            )
    except httpx.HTTPError as exc:
        return BotInfo(valid=False, error=f"Failed to reach Discord API: {exc}")

    if resp.status_code == 401:
        return BotInfo(valid=False, error="Invalid bot token.")
    if resp.status_code != 200:
        return BotInfo(
            valid=False,
            error=f"Discord API returned {resp.status_code}.",
        )

    data = resp.json()
    bot_id = data.get("id", "")
    client_id = _extract_client_id(token) or bot_id

    return BotInfo(
        valid=True,
        bot_username=data.get("username"),
        bot_id=bot_id,
        invite_url=_build_invite_url(client_id),
    )


@router.post("/guilds", response_model=GuildsResponse)
async def list_bot_guilds(
    payload: TokenInput,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> GuildsResponse:
    """List servers the Discord bot has joined."""
    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required.")

    try:
        async with httpx.AsyncClient(timeout=DISCORD_TIMEOUT) as client:
            resp = await client.get(
                f"{DISCORD_API}/users/@me/guilds",
                headers={"Authorization": f"Bot {token}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to reach Discord API: {exc}",
        ) from exc

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid bot token.")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Discord API returned {resp.status_code}.",
        )

    guilds = [
        GuildInfo(
            id=g["id"],
            name=g["name"],
            icon=g.get("icon"),
            member_count=g.get("approximate_member_count"),
        )
        for g in resp.json()
    ]
    return GuildsResponse(guilds=guilds)


@router.post("/guild-members", response_model=MembersResponse)
async def list_guild_members(
    payload: GuildIdInput,
    ctx: OrganizationContext = ORG_ADMIN_DEP,
) -> MembersResponse:
    """List members of a specific Discord server."""
    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required.")

    try:
        async with httpx.AsyncClient(timeout=DISCORD_TIMEOUT) as client:
            resp = await client.get(
                f"{DISCORD_API}/guilds/{payload.guild_id}/members",
                headers={"Authorization": f"Bot {token}"},
                params={"limit": 100},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to reach Discord API: {exc}",
        ) from exc

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid bot token.")
    if resp.status_code == 403:
        raise HTTPException(
            status_code=403,
            detail="Bot lacks permissions. Enable the Server Members Intent in the Discord Developer Portal.",
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Discord API returned {resp.status_code}.",
        )

    members = []
    for m in resp.json():
        user = m.get("user", {})
        members.append(
            MemberInfo(
                id=user.get("id", ""),
                username=user.get("username", ""),
                display_name=user.get("global_name") or m.get("nick"),
                avatar=user.get("avatar"),
                bot=user.get("bot", False),
            )
        )
    return MembersResponse(members=members)
