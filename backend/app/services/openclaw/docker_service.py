"""Docker operations for managed OpenClaw gateway containers."""

from __future__ import annotations

import json
import logging
import secrets
import socket
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

OPENCLAW_IMAGE = "openclaw:local"


class DockerError(Exception):
    """Raised when a Docker operation fails."""


class OpenClawDockerService:
    """Pure Docker + config-file operations (no DB access)."""

    def check_docker_available(self) -> bool:
        """Return True if the Docker daemon is reachable."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def check_image_exists(self, image: str = OPENCLAW_IMAGE) -> bool:
        """Return True if the given Docker image exists locally."""
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def build_image(self, repo_path: str, image: str = OPENCLAW_IMAGE) -> None:
        """Build the OpenClaw Docker image from the repo."""
        logger.info("Building Docker image %s from %s", image, repo_path)
        result = subprocess.run(
            ["docker", "build", "-t", image, "-f", "Dockerfile", "."],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise DockerError(f"Image build failed: {result.stderr}")
        logger.info("Docker image %s built successfully", image)

    def find_available_port(self, start: int = 48780) -> int:
        """Find the next available TCP port starting from *start*.

        Checks Docker host port bindings via ``docker ps`` since this code
        runs inside a container and cannot probe host ports via sockets.
        """
        used_ports = self._get_docker_used_ports()
        for port in range(start, 65536):
            bridge_port = port + 100
            if bridge_port > 65535:
                continue
            if port not in used_ports and bridge_port not in used_ports:
                return port
        raise DockerError("No available ports found")

    def _get_docker_used_ports(self) -> set[int]:
        """Return set of host ports currently bound by Docker containers."""
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Ports}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return set()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return set()

        ports: set[int] = set()
        import re
        # Match patterns like "0.0.0.0:48780->18789/tcp" or ":::48780->18789/tcp"
        for match in re.finditer(r":(\d+)->", result.stdout):
            ports.add(int(match.group(1)))
        return ports

    def generate_config_files(
        self,
        config_dir: str,
        *,
        name: str,
        anthropic_api_key: str,
        gateway_port: int,
        bridge_port: int,
        auth_token: str,
        max_concurrent_agents: int = 4,
        discord_bot_token: str | None = None,
        telegram_bot_token: str | None = None,
        discord_user_ids: list[str] | None = None,
        telegram_user_ids: list[str] | None = None,
        host_config_dir: str | None = None,
    ) -> None:
        """Generate all OpenClaw config files in *config_dir*.

        *config_dir* is the writable path (inside the MC container).
        *host_config_dir*, if given, is the same directory as seen by the
        Docker host — used inside the generated ``.env`` for volume mounts
        that the Docker daemon resolves on the host.
        """
        env_dir = host_config_dir or config_dir
        base = Path(config_dir)
        config_path = base / "config"
        config_path.mkdir(parents=True, exist_ok=True)
        (base / "workspace").mkdir(parents=True, exist_ok=True)

        # --- Channels & plugins ---
        channels: dict[str, object] = {}
        plugins: dict[str, object] = {}
        dm_policy = "allowlist" if (discord_user_ids or telegram_user_ids) else "pairing"

        if telegram_bot_token:
            channels["telegram"] = {
                "enabled": True,
                "dmPolicy": dm_policy,
                "botToken": telegram_bot_token,
                "groupPolicy": "allowlist",
                "streamMode": "partial",
            }
            plugins["telegram"] = {"enabled": True}

        if discord_bot_token:
            channels["discord"] = {
                "enabled": True,
                "token": discord_bot_token,
                "dm": {"policy": dm_policy},
            }
            plugins["discord"] = {"enabled": True}

        # --- openclaw.json ---
        openclaw_config = {
            "meta": {
                "lastTouchedVersion": "2026.2.6",
                "lastTouchedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            },
            "wizard": {
                "lastRunAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                "lastRunVersion": "2026.2.6",
                "lastRunCommand": "configure",
                "lastRunMode": "local",
            },
            "auth": {
                "profiles": {
                    "anthropic:default": {
                        "provider": "anthropic",
                        "mode": "token",
                    },
                },
            },
            "agents": {
                "defaults": {
                    "workspace": "/home/node/.openclaw/workspace",
                    "compaction": {"mode": "safeguard"},
                    "maxConcurrent": max_concurrent_agents,
                    "subagents": {"maxConcurrent": max_concurrent_agents * 2},
                },
            },
            "messages": {"ackReactionScope": "group-mentions"},
            "commands": {"native": "auto", "nativeSkills": "auto"},
            "channels": channels,
            "gateway": {
                "port": 18789,
                "mode": "local",
                "bind": "loopback",
                "controlUi": {"allowInsecureAuth": True},
                "auth": {
                    "mode": "token",
                    "token": auth_token,
                },
                "tailscale": {"mode": "off", "resetOnExit": False},
            },
            "plugins": {"entries": plugins},
        }
        (config_path / "openclaw.json").write_text(
            json.dumps(openclaw_config, indent=2),
        )

        # --- auth-profiles.json ---
        auth_dir = config_path / "agents" / "main" / "agent"
        auth_dir.mkdir(parents=True, exist_ok=True)
        timestamp_ms = int(time.time() * 1000)
        auth_profiles = {
            "version": 1,
            "profiles": {
                "anthropic:default": {
                    "type": "token",
                    "provider": "anthropic",
                    "token": anthropic_api_key,
                },
            },
            "lastGood": {"anthropic": "anthropic:default"},
            "usageStats": {
                "anthropic:default": {
                    "lastUsed": timestamp_ms,
                    "errorCount": 0,
                },
            },
        }
        (auth_dir / "auth-profiles.json").write_text(
            json.dumps(auth_profiles, indent=2),
        )

        # --- .env ---
        # Paths in .env must be host paths since Docker daemon resolves
        # volume mounts on the host, not inside the MC container.
        env_content = (
            f"OPENCLAW_CONFIG_DIR={env_dir}/config/\n"
            f"OPENCLAW_WORKSPACE_DIR={env_dir}/workspace/\n"
            f"OPENCLAW_GATEWAY_PORT={gateway_port}\n"
            f"OPENCLAW_BRIDGE_PORT={bridge_port}\n"
            f"OPENCLAW_GATEWAY_TOKEN={auth_token}\n"
            # Suppress docker compose warnings for unused variables
            # referenced in the openclaw docker-compose.yml.
            "CLAUDE_AI_SESSION_KEY=\n"
            "CLAUDE_WEB_SESSION_KEY=\n"
            "CLAUDE_WEB_COOKIE=\n"
        )
        (base / ".env").write_text(env_content)

        # --- allowFrom files ---
        if discord_user_ids or telegram_user_ids:
            creds_dir = config_path / "credentials"
            creds_dir.mkdir(parents=True, exist_ok=True)

            if discord_user_ids:
                (creds_dir / "discord-allowFrom.json").write_text(
                    json.dumps({"version": 1, "allowFrom": discord_user_ids}, indent=2),
                )
            if telegram_user_ids:
                (creds_dir / "telegram-allowFrom.json").write_text(
                    json.dumps({"version": 1, "allowFrom": telegram_user_ids}, indent=2),
                )

        logger.info("Config files generated in %s", config_dir)

    def start_container(
        self,
        name: str,
        host_config_dir: str,
        gateway_port: int,
        bridge_port: int,
        auth_token: str,
        repo_path: str,
    ) -> str:
        """Start an OpenClaw gateway via docker compose. Returns project name."""
        project_name = f"openclaw-{name}"
        env_file = f"{host_config_dir}/.env"
        compose_file = f"{repo_path}/docker-compose.yml"

        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                compose_file,
                "--env-file",
                env_file,
                "-p",
                project_name,
                "up",
                "-d",
                "openclaw-gateway",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            # Filter out variable-not-set warnings to surface the real error
            error_lines = [
                line
                for line in result.stderr.splitlines()
                if "variable is not set" not in line
            ]
            raise DockerError(
                f"Container start failed: {chr(10).join(error_lines)}"
            )
        logger.info("Container %s started", project_name)
        return project_name

    def check_container_running(self, project_name: str) -> bool:
        """Return True if any service in the compose project is running."""
        try:
            result = subprocess.run(
                [
                    "docker", "compose", "-p", project_name,
                    "ps", "--format", "json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False
            for line in result.stdout.strip().splitlines():
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("State") == "running":
                    return True
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def restart_container(
        self,
        project_name: str,
        config_dir: str,
        repo_path: str,
    ) -> None:
        """Start (or restart) an existing compose project from its saved .env."""
        env_file = f"{config_dir}/.env"
        compose_file = f"{repo_path}/docker-compose.yml"
        result = subprocess.run(
            [
                "docker", "compose",
                "-f", compose_file,
                "--env-file", env_file,
                "-p", project_name,
                "up", "-d", "openclaw-gateway",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            error_lines = [
                line
                for line in result.stderr.splitlines()
                if "variable is not set" not in line
            ]
            raise DockerError(
                f"Container restart failed: {chr(10).join(error_lines)}"
            )
        logger.info("Container %s restarted", project_name)

    def resume_container(self, project_name: str) -> bool:
        """Resume a stopped (but not removed) compose project.

        Returns True if the container was successfully started, False if
        there is no existing container to resume (e.g. it was removed).
        """
        # Check if there are any containers (stopped or running) for this project
        check = subprocess.run(
            ["docker", "compose", "-p", project_name, "ps", "-a", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if check.returncode != 0 or not check.stdout.strip():
            return False

        result = subprocess.run(
            ["docker", "compose", "-p", project_name, "start"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("Failed to resume container %s: %s", project_name, result.stderr)
            return False
        logger.info("Container %s resumed", project_name)
        return True

    def pause_container(self, project_name: str) -> None:
        """Stop a compose project's containers without removing them."""
        result = subprocess.run(
            ["docker", "compose", "-p", project_name, "stop"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("Failed to pause container %s: %s", project_name, result.stderr)
        else:
            logger.info("Container %s paused", project_name)

    def stop_container(self, project_name: str) -> None:
        """Stop and remove a docker compose project."""
        result = subprocess.run(
            ["docker", "compose", "-p", project_name, "down"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("Failed to stop container %s: %s", project_name, result.stderr)
        else:
            logger.info("Container %s stopped", project_name)

    def remove_container(self, project_name: str) -> None:
        """Stop and remove a docker compose project with volumes."""
        result = subprocess.run(
            ["docker", "compose", "-p", project_name, "down", "-v"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("Failed to remove container %s: %s", project_name, result.stderr)
        else:
            logger.info("Container %s removed", project_name)

    def wait_for_ready(
        self,
        url: str,
        token: str,
        *,
        timeout: int = 60,
    ) -> bool:
        """Poll the gateway HTTP endpoint until it responds or timeout."""
        import urllib.request
        import urllib.error

        # Convert ws:// to http:// for health check
        http_url = url.replace("ws://", "http://").replace("wss://", "https://")
        if not http_url.endswith("/"):
            http_url += "/"
        health_url = f"{http_url}overview"

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                req = urllib.request.Request(health_url)
                if token:
                    req.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return True
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            time.sleep(2)
        return False

    @staticmethod
    def generate_auth_token() -> str:
        """Generate a secure gateway auth token."""
        return secrets.token_hex(24)
