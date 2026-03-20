"use client";

export const dynamic = "force-dynamic";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";

import { useAuth } from "@/auth/clerk";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { GatewaysTable } from "@/components/gateways/GatewaysTable";
import { DashboardPageLayout } from "@/components/templates/DashboardPageLayout";
import { buttonVariants } from "@/components/ui/button";
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog";

import { ApiError, customFetch } from "@/api/mutator";
import {
  type listGatewaysApiV1GatewaysGetResponse,
  getListGatewaysApiV1GatewaysGetQueryKey,
  useDeleteGatewayApiV1GatewaysGatewayIdDelete,
  useListGatewaysApiV1GatewaysGet,
} from "@/api/generated/gateways/gateways";
import { createOptimisticListDeleteMutation } from "@/lib/list-delete";
import { useOrganizationMembership } from "@/lib/use-organization-membership";
import type { GatewayRead } from "@/api/generated/model";
import { useUrlSorting } from "@/lib/use-url-sorting";

type BatchContainerStatusResponse = {
  statuses: Record<string, boolean>;
  unpaired: string[];
};

const GATEWAY_SORTABLE_COLUMNS = ["name", "workspace_root", "updated_at"];

export default function GatewaysPage() {
  const { isSignedIn } = useAuth();
  const queryClient = useQueryClient();
  const { sorting, onSortingChange } = useUrlSorting({
    allowedColumnIds: GATEWAY_SORTABLE_COLUMNS,
    defaultSorting: [{ id: "name", desc: false }],
    paramPrefix: "gateways",
  });

  const { isAdmin } = useOrganizationMembership(isSignedIn);
  const [deleteTarget, setDeleteTarget] = useState<GatewayRead | null>(null);

  const gatewaysKey = getListGatewaysApiV1GatewaysGetQueryKey();
  const gatewaysQuery = useListGatewaysApiV1GatewaysGet<
    listGatewaysApiV1GatewaysGetResponse,
    ApiError
  >(undefined, {
    query: {
      enabled: Boolean(isSignedIn && isAdmin),
      refetchInterval: 30_000,
      refetchOnMount: "always",
    },
  });

  const gateways = useMemo(
    () =>
      gatewaysQuery.data?.status === 200
        ? (gatewaysQuery.data.data.items ?? [])
        : [],
    [gatewaysQuery.data],
  );

  const hasManagedGateways = useMemo(
    () => gateways.some((gw) => (gw as GatewayRead & { managed?: boolean }).managed),
    [gateways],
  );

  const containerStatusesKey = ["containerStatuses"];
  const containerStatusesQuery = useQuery<
    { data: BatchContainerStatusResponse; status: number },
    ApiError
  >({
    queryKey: containerStatusesKey,
    queryFn: () =>
      customFetch<{ data: BatchContainerStatusResponse; status: number }>(
        "/api/v1/gateways/docker/container-statuses",
        { method: "GET" },
      ),
    enabled: Boolean(isSignedIn && isAdmin && hasManagedGateways),
    refetchInterval: 10_000,
  });

  const containerStatuses = containerStatusesQuery.data?.data?.statuses;
  const unpairedGatewayIds = containerStatusesQuery.data?.data?.unpaired;

  const [stopTarget, setStopTarget] = useState<GatewayRead | null>(null);

  const stopMutation = useMutation<unknown, ApiError, { gatewayId: string }>({
    mutationFn: ({ gatewayId }) =>
      customFetch(`/api/v1/gateways/${gatewayId}/docker/stop`, {
        method: "POST",
      }),
    onSuccess: () => {
      setStopTarget(null);
      void queryClient.invalidateQueries({ queryKey: containerStatusesKey });
    },
  });

  const startMutation = useMutation<unknown, ApiError, { gatewayId: string }>({
    mutationFn: ({ gatewayId }) =>
      customFetch(`/api/v1/gateways/${gatewayId}/docker/start`, {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: containerStatusesKey });
    },
  });

  const handleStop = useCallback(() => {
    if (!stopTarget) return;
    stopMutation.mutate({ gatewayId: stopTarget.id });
  }, [stopTarget, stopMutation]);

  const handleStart = useCallback(
    (gateway: GatewayRead) => {
      startMutation.mutate({ gatewayId: gateway.id });
    },
    [startMutation],
  );

  const provisionMutation = useMutation<unknown, ApiError, { gatewayId: string }>({
    mutationFn: ({ gatewayId }) =>
      customFetch(`/api/v1/gateways/${gatewayId}/docker/provision`, {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: containerStatusesKey });
      void queryClient.invalidateQueries({ queryKey: gatewaysKey });
    },
  });

  const handleProvision = useCallback(
    (gateway: GatewayRead) => {
      provisionMutation.mutate({ gatewayId: gateway.id });
    },
    [provisionMutation],
  );

  const deleteMutation = useDeleteGatewayApiV1GatewaysGatewayIdDelete<
    ApiError,
    { previous?: listGatewaysApiV1GatewaysGetResponse }
  >(
    {
      mutation: createOptimisticListDeleteMutation<
        GatewayRead,
        listGatewaysApiV1GatewaysGetResponse,
        { gatewayId: string }
      >({
        queryClient,
        queryKey: gatewaysKey,
        getItemId: (gateway) => gateway.id,
        getDeleteId: ({ gatewayId }) => gatewayId,
        onSuccess: () => {
          setDeleteTarget(null);
        },
        invalidateQueryKeys: [gatewaysKey],
      }),
    },
    queryClient,
  );

  const handleDelete = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate({ gatewayId: deleteTarget.id });
  };

  return (
    <>
      <DashboardPageLayout
        signedOut={{
          message: "Sign in to view gateways.",
          forceRedirectUrl: "/gateways",
        }}
        title="Gateways"
        description="Manage OpenClaw gateway connections used by boards"
        headerActions={
          isAdmin && gateways.length > 0 ? (
            <Link
              href="/gateways/new"
              className={buttonVariants({
                size: "md",
                variant: "primary",
              })}
            >
              Create gateway
            </Link>
          ) : null
        }
        isAdmin={isAdmin}
        adminOnlyMessage="Only organization owners and admins can access gateways."
        stickyHeader
      >
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <GatewaysTable
            gateways={gateways}
            isLoading={gatewaysQuery.isLoading}
            sorting={sorting}
            onSortingChange={onSortingChange}
            showActions
            stickyHeader
            onDelete={setDeleteTarget}
            onStop={setStopTarget}
            onStart={handleStart}
            onProvision={handleProvision}
            containerStatuses={containerStatuses}
            unpairedGatewayIds={unpairedGatewayIds}
            emptyState={{
              title: "No gateways yet",
              description:
                "Create your first gateway to connect boards and start managing your OpenClaw connections.",
              actionHref: "/gateways/new",
              actionLabel: "Create your first gateway",
            }}
          />
        </div>

        {gatewaysQuery.error ? (
          <p className="mt-4 text-sm text-red-500">
            {gatewaysQuery.error.message}
          </p>
        ) : null}
      </DashboardPageLayout>

      <ConfirmActionDialog
        open={Boolean(deleteTarget)}
        onOpenChange={() => setDeleteTarget(null)}
        title="Delete gateway?"
        description={
          <>
            This removes the gateway connection from Mission Control. Boards
            using it will need a new gateway assigned.
          </>
        }
        errorMessage={deleteMutation.error?.message}
        errorStyle="text"
        cancelVariant="ghost"
        onConfirm={handleDelete}
        isConfirming={deleteMutation.isPending}
      />

      <ConfirmActionDialog
        open={Boolean(stopTarget)}
        onOpenChange={() => setStopTarget(null)}
        title="Stop gateway container?"
        description={
          <>
            This will stop the Docker container for{" "}
            <strong>{stopTarget?.name}</strong>. The gateway will go offline
            until you start it again.
          </>
        }
        confirmLabel="Stop"
        confirmingLabel="Stopping…"
        errorMessage={stopMutation.error?.message}
        errorStyle="text"
        cancelVariant="ghost"
        onConfirm={handleStop}
        isConfirming={stopMutation.isPending}
      />
    </>
  );
}
