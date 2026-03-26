"use client";

import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type GatewaySpinUpFormProps = {
  onSuccess: (gatewayId: string) => void;
  onCancel: () => void;
};

type SpinUpStatus =
  | "idle"
  | "checking-docker"
  | "building-image"
  | "starting-container"
  | "waiting-for-gateway"
  | "provisioning"
  | "error";

type BotInfo = {
  valid: boolean;
  bot_username: string | null;
  bot_id: string | null;
  invite_url: string | null;
  error: string | null;
};

type GuildInfo = {
  id: string;
  name: string;
  icon: string | null;
  member_count: number | null;
};

type MemberInfo = {
  id: string;
  username: string;
  display_name: string | null;
  avatar: string | null;
  bot: boolean;
};

export function GatewaySpinUpForm({
  onSuccess,
  onCancel,
}: GatewaySpinUpFormProps) {
  const [name, setName] = useState("");
  const [anthropicApiKey, setAnthropicApiKey] = useState("");
  const [gatewayPort, setGatewayPort] = useState("");
  const [maxConcurrentAgents, setMaxConcurrentAgents] = useState("4");
  const [discordBotToken, setDiscordBotToken] = useState("");
  const [telegramBotToken, setTelegramBotToken] = useState("");
  const [discordUserIds, setDiscordUserIds] = useState("");
  const [telegramUserIds, setTelegramUserIds] = useState("");

  const [spinUpStatus, setSpinUpStatus] = useState<SpinUpStatus>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Discord wizard state
  const [botInfo, setBotInfo] = useState<BotInfo | null>(null);
  const [validatingToken, setValidatingToken] = useState(false);
  const [guilds, setGuilds] = useState<GuildInfo[]>([]);
  const [loadingGuilds, setLoadingGuilds] = useState(false);
  const [members, setMembers] = useState<MemberInfo[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [selectedGuildId, setSelectedGuildId] = useState<string>("");
  const [selectedUserIds, setSelectedUserIds] = useState<Set<string>>(
    new Set(),
  );

  const isLoading = spinUpStatus !== "idle" && spinUpStatus !== "error";
  const canSubmit = Boolean(name.trim()) && Boolean(anthropicApiKey.trim());

  const statusMessages: Record<SpinUpStatus, string> = {
    idle: "",
    "checking-docker": "Checking Docker availability...",
    "building-image": "Building OpenClaw image (this may take a few minutes)...",
    "starting-container": "Starting container...",
    "waiting-for-gateway": "Waiting for gateway to become ready...",
    provisioning: "Provisioning main agent...",
    error: "",
  };

  const handleValidateToken = async () => {
    const token = discordBotToken.trim();
    if (!token) return;

    setValidatingToken(true);
    setBotInfo(null);
    setGuilds([]);
    setMembers([]);
    setSelectedGuildId("");
    setSelectedUserIds(new Set());
    setErrorMessage(null);

    try {
      const { customFetch } = await import("@/api/mutator");
      const result = await customFetch<{ data: BotInfo; status: number }>(
        "/api/v1/discord/validate-token",
        {
          method: "POST",
          body: JSON.stringify({ token }),
        },
      );
      setBotInfo(result.data);
      if (result.data.valid) {
        // Auto-fetch guilds
        await handleFetchGuilds(token);
      }
    } catch (err: unknown) {
      setBotInfo({
        valid: false,
        bot_username: null,
        bot_id: null,
        invite_url: null,
        error: err instanceof Error ? err.message : "Validation failed.",
      });
    } finally {
      setValidatingToken(false);
    }
  };

  const handleFetchGuilds = async (token?: string) => {
    const t = token || discordBotToken.trim();
    if (!t) return;

    setLoadingGuilds(true);
    try {
      const { customFetch } = await import("@/api/mutator");
      const result = await customFetch<{
        data: { guilds: GuildInfo[] };
        status: number;
      }>("/api/v1/discord/guilds", {
        method: "POST",
        body: JSON.stringify({ token: t }),
      });
      setGuilds(result.data.guilds);
    } catch {
      setGuilds([]);
    } finally {
      setLoadingGuilds(false);
    }
  };

  const handleFetchMembers = async (guildId: string) => {
    const token = discordBotToken.trim();
    if (!token || !guildId) return;

    setLoadingMembers(true);
    setSelectedGuildId(guildId);
    setMembers([]);
    try {
      const { customFetch } = await import("@/api/mutator");
      const result = await customFetch<{
        data: { members: MemberInfo[] };
        status: number;
      }>("/api/v1/discord/guild-members", {
        method: "POST",
        body: JSON.stringify({ token, guild_id: guildId }),
      });
      setMembers(result.data.members.filter((m) => !m.bot));
    } catch (err: unknown) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : "Failed to fetch members. Make sure the Server Members Intent is enabled.",
      );
    } finally {
      setLoadingMembers(false);
    }
  };

  const toggleMember = (userId: string) => {
    setSelectedUserIds((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) {
        next.delete(userId);
      } else {
        next.add(userId);
      }
      return next;
    });
  };

  // Sync selected user IDs back to the text field
  const applySelectedUsers = () => {
    if (selectedUserIds.size > 0) {
      setDiscordUserIds(Array.from(selectedUserIds).join(", "));
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);

    // Apply any selected users from the picker
    let finalDiscordUserIds = discordUserIds;
    if (selectedUserIds.size > 0) {
      finalDiscordUserIds = Array.from(selectedUserIds).join(", ");
      setDiscordUserIds(finalDiscordUserIds);
    }

    if (discordBotToken.trim() && !finalDiscordUserIds.trim()) {
      setErrorMessage(
        "Select authorized users from the member list, or enter Discord user IDs manually.",
      );
      return;
    }
    if (telegramBotToken.trim() && !telegramUserIds.trim()) {
      setErrorMessage(
        "Telegram user IDs are required when a Telegram bot token is provided.",
      );
      return;
    }

    setSpinUpStatus("starting-container");

    try {
      const { customFetch } = await import("@/api/mutator");
      const body: Record<string, unknown> = {
        name: name.trim(),
        anthropic_api_key: anthropicApiKey.trim(),
        max_concurrent_agents: parseInt(maxConcurrentAgents, 10) || 4,
      };
      if (gatewayPort.trim()) {
        body.gateway_port = parseInt(gatewayPort.trim(), 10);
      }
      if (discordBotToken.trim()) {
        body.discord_bot_token = discordBotToken.trim();
      }
      if (telegramBotToken.trim()) {
        body.telegram_bot_token = telegramBotToken.trim();
      }
      if (finalDiscordUserIds.trim()) {
        body.discord_user_ids = finalDiscordUserIds
          .split(",")
          .map((id) => id.trim())
          .filter(Boolean);
      }
      if (selectedGuildId) {
        body.discord_guild_ids = [selectedGuildId];
      }
      if (telegramUserIds.trim()) {
        body.telegram_user_ids = telegramUserIds
          .split(",")
          .map((id) => id.trim())
          .filter(Boolean);
      }

      const result = await customFetch<{
        data: {
          gateway: { id: string };
          status: string;
          message: string | null;
        };
        status: number;
      }>("/api/v1/gateways/spin-up", {
        method: "POST",
        body: JSON.stringify(body),
      });

      if (result.data.status === "error") {
        if (result.data.gateway?.id) {
          onSuccess(result.data.gateway.id);
          return;
        }
        setSpinUpStatus("error");
        setErrorMessage(result.data.message || "Gateway failed to start.");
        return;
      }

      onSuccess(result.data.gateway.id);
    } catch (err: unknown) {
      setSpinUpStatus("error");
      const message =
        err instanceof Error ? err.message : "Something went wrong.";
      setErrorMessage(message);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
    >
      {/* --- Core settings --- */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-slate-900">
          Gateway name <span className="text-red-500">*</span>
        </label>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-gateway"
          disabled={isLoading}
        />
      </div>

      <div className="space-y-2">
        <label className="text-sm font-medium text-slate-900">
          Anthropic API key <span className="text-red-500">*</span>
        </label>
        <Input
          type="password"
          value={anthropicApiKey}
          onChange={(e) => setAnthropicApiKey(e.target.value)}
          placeholder="sk-ant-..."
          disabled={isLoading}
        />
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-900">
            Gateway port
          </label>
          <Input
            type="number"
            value={gatewayPort}
            onChange={(e) => setGatewayPort(e.target.value)}
            placeholder="Auto-assigned"
            disabled={isLoading}
          />
          <p className="text-xs text-slate-500">
            Leave empty for auto-assignment.
          </p>
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-900">
            Max concurrent agents
          </label>
          <Input
            type="number"
            value={maxConcurrentAgents}
            onChange={(e) => setMaxConcurrentAgents(e.target.value)}
            placeholder="4"
            disabled={isLoading}
          />
        </div>
      </div>

      {/* --- Discord setup wizard --- */}
      <div className="space-y-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
        <h3 className="text-sm font-semibold text-slate-900">
          Discord Bot Setup
        </h3>

        {/* Step 1: Create bot instructions */}
        {!botInfo?.valid ? (
          <div className="rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-700">
            <p className="font-medium">
              Need a bot?{" "}
              <a
                href="https://discord.com/developers/applications"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                Create one in the Discord Developer Portal
              </a>
            </p>
            <ol className="mt-1 ml-4 list-decimal space-y-0.5">
              <li>Click &quot;New Application&quot; and give it a name</li>
              <li>
                Go to <strong>Bot</strong> tab → click &quot;Reset Token&quot;
                to get the bot token
              </li>
              <li>Paste the token below</li>
            </ol>
            <div className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <p className="font-bold">
                ⚠ REQUIRED: Enable &quot;Message Content Intent&quot;
              </p>
              <p className="mt-0.5">
                In the Discord Developer Portal → <strong>Bot</strong> tab →
                scroll to <strong>Privileged Gateway Intents</strong> → toggle on{" "}
                <strong>Message Content Intent</strong> and save. Without this,
                the bot will fail to connect with error 4014.
              </p>
            </div>
          </div>
        ) : null}

        {/* Step 2: Token input + validate */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-900">
            Discord bot token
          </label>
          <div className="flex gap-2">
            <Input
              type="password"
              value={discordBotToken}
              onChange={(e) => {
                setDiscordBotToken(e.target.value);
                // Reset wizard state on token change
                if (botInfo) {
                  setBotInfo(null);
                  setGuilds([]);
                  setMembers([]);
                  setSelectedGuildId("");
                  setSelectedUserIds(new Set());
                }
              }}
              placeholder="Paste bot token here"
              disabled={isLoading}
              className="flex-1"
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!discordBotToken.trim() || validatingToken || isLoading}
              onClick={handleValidateToken}
            >
              {validatingToken ? "Checking..." : "Verify"}
            </Button>
          </div>
        </div>

        {/* Token validation result */}
        {botInfo ? (
          botInfo.valid ? (
            <div className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">
              Bot verified: <strong>{botInfo.bot_username}</strong>
            </div>
          ) : (
            <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
              {botInfo.error || "Invalid token."}
            </div>
          )
        ) : null}

        {/* Step 3: Invite URL */}
        {botInfo?.valid && botInfo.invite_url ? (
          <div className="space-y-2">
            <p className="text-xs text-slate-600">
              Invite the bot to your server with the correct permissions:
            </p>
            <a
              href={botInfo.invite_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
            >
              Invite {botInfo.bot_username} to Server
              <svg
                className="h-3 w-3"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                />
              </svg>
            </a>
            <p className="text-xs text-slate-500">
              After inviting, click &quot;Refresh servers&quot; below to select
              users.
            </p>
          </div>
        ) : null}

        {/* Step 4: Server selection + member picker */}
        {botInfo?.valid ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <label className="text-sm font-medium text-slate-900">
                Select server
              </label>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={loadingGuilds || isLoading}
                onClick={() => handleFetchGuilds()}
                className="text-xs"
              >
                {loadingGuilds ? "Loading..." : "Refresh servers"}
              </Button>
            </div>

            {guilds.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {guilds.map((guild) => (
                  <button
                    key={guild.id}
                    type="button"
                    onClick={() => handleFetchMembers(guild.id)}
                    className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                      selectedGuildId === guild.id
                        ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                    }`}
                  >
                    {guild.name}
                  </button>
                ))}
              </div>
            ) : !loadingGuilds ? (
              <p className="text-xs text-slate-500">
                No servers found. Invite the bot first, then refresh.
              </p>
            ) : null}

            {/* Member list */}
            {loadingMembers ? (
              <p className="text-xs text-slate-500">Loading members...</p>
            ) : members.length > 0 ? (
              <div className="space-y-2">
                <label className="text-sm font-medium text-slate-900">
                  Select authorized users{" "}
                  <span className="text-red-500">*</span>
                </label>
                <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-slate-200 bg-white p-2">
                  {members.map((member) => (
                    <label
                      key={member.id}
                      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-slate-50"
                    >
                      <input
                        type="checkbox"
                        checked={selectedUserIds.has(member.id)}
                        onChange={() => toggleMember(member.id)}
                        className="h-3.5 w-3.5 rounded border-slate-300"
                      />
                      <span className="text-sm text-slate-700">
                        {member.display_name || member.username}
                      </span>
                      <span className="text-xs text-slate-400">
                        @{member.username}
                      </span>
                      <span className="text-xs text-slate-300">
                        {member.id}
                      </span>
                    </label>
                  ))}
                </div>
                {selectedUserIds.size > 0 ? (
                  <p className="text-xs text-green-600">
                    {selectedUserIds.size} user
                    {selectedUserIds.size > 1 ? "s" : ""} selected
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Fallback: manual user ID input */}
        {botInfo?.valid ? (
          <div className="space-y-1">
            <details className="text-xs">
              <summary className="cursor-pointer text-slate-500 hover:text-slate-700">
                Or enter user IDs manually
              </summary>
              <div className="mt-2">
                <Input
                  value={discordUserIds}
                  onChange={(e) => setDiscordUserIds(e.target.value)}
                  placeholder="Comma-separated Discord user IDs"
                  disabled={isLoading}
                />
              </div>
            </details>
          </div>
        ) : null}
      </div>

      {/* --- Telegram (unchanged) --- */}
      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-900">
            Telegram bot token
          </label>
          <Input
            type="password"
            value={telegramBotToken}
            onChange={(e) => setTelegramBotToken(e.target.value)}
            placeholder="Optional"
            disabled={isLoading}
          />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-900">
            Telegram user IDs{" "}
            {telegramBotToken.trim() ? (
              <span className="text-red-500">*</span>
            ) : null}
          </label>
          <Input
            value={telegramUserIds}
            onChange={(e) => setTelegramUserIds(e.target.value)}
            placeholder="Comma-separated"
            disabled={isLoading}
          />
          <p className="text-xs text-slate-500">
            Required when Telegram bot token is set.
          </p>
        </div>
      </div>

      {/* --- Status / errors --- */}
      {isLoading ? (
        <div className="flex items-center gap-2 rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-700">
          <svg
            className="h-4 w-4 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          {statusMessages[spinUpStatus]}
        </div>
      ) : null}

      {errorMessage ? (
        <p className="text-sm text-red-500">{errorMessage}</p>
      ) : null}

      <div className="flex justify-end gap-3">
        <Button
          type="button"
          variant="ghost"
          onClick={onCancel}
          disabled={isLoading}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={isLoading || !canSubmit}>
          {isLoading ? "Spinning up..." : "Spin up gateway"}
        </Button>
      </div>
    </form>
  );
}
