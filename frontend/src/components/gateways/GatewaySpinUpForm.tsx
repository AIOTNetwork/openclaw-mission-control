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

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);

    if (discordBotToken.trim() && !discordUserIds.trim()) {
      setErrorMessage("Discord user IDs are required when a Discord bot token is provided.");
      return;
    }
    if (telegramBotToken.trim() && !telegramUserIds.trim()) {
      setErrorMessage("Telegram user IDs are required when a Telegram bot token is provided.");
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
      if (discordUserIds.trim()) {
        body.discord_user_ids = discordUserIds
          .split(",")
          .map((id) => id.trim())
          .filter(Boolean);
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
        // The gateway DB record exists even on timeout — redirect so the
        // user can retry provisioning from the gateway list once the
        // container finishes starting up.
        if (result.data.gateway?.id) {
          onSuccess(result.data.gateway.id);
          return;
        }
        setSpinUpStatus("error");
        setErrorMessage(
          result.data.message || "Gateway failed to start.",
        );
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

      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-900">
            Discord bot token
          </label>
          <Input
            type="password"
            value={discordBotToken}
            onChange={(e) => setDiscordBotToken(e.target.value)}
            placeholder="Optional"
            disabled={isLoading}
          />
        </div>
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
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-900">
            Discord user IDs{" "}
            {discordBotToken.trim() ? (
              <span className="text-red-500">*</span>
            ) : null}
          </label>
          <Input
            value={discordUserIds}
            onChange={(e) => setDiscordUserIds(e.target.value)}
            placeholder="Comma-separated"
            disabled={isLoading}
          />
          <p className="text-xs text-slate-500">
            Required when Discord bot token is set. Pre-authorized user IDs.
          </p>
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
            Required when Telegram bot token is set. Pre-authorized user IDs.
          </p>
        </div>
      </div>

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
