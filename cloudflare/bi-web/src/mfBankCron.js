// Scheduling metadata only. MF credentials, account IDs and financial payloads
// stay in the existing private Actions workflow. No HTTP route invokes this.
export const MF_BANK_CRON = "17 * * * *";

export function mfBankDispatchInputs(event) {
  const time = new Date(event.scheduledTime);
  if (event.cron !== MF_BANK_CRON || !Number.isFinite(event.scheduledTime)
      || !Number.isFinite(time.getTime()) || time.getUTCMinutes() !== 17) {
    throw new Error("invalid_mf_scheduled_event");
  }
  const scheduledAt = time.toISOString();
  return {
    // Use the intended slot, never Date.now(): a delayed JST03:17 tick must
    // still reconcile the full accounting period after it starts/queues.
    full: time.getUTCHours() === 18 ? "true" : "false",
    scheduled_at: scheduledAt,
    dispatch_id: `mf-${scheduledAt.slice(0, 13).replace(/[-T]/g, "")}`,
  };
}

export async function dispatchMfBankSync(event, env) {
  const inputs = mfBankDispatchInputs(event);
  if (!env.GITHUB_ACTIONS_DISPATCH_TOKEN) throw new Error("mf_dispatch_token_missing");
  let response;
  try {
    response = await fetch("https://api.github.com/repos/ginisato-hash/kiraku/actions/workflows/sync-mf-banks.yml/dispatches", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_ACTIONS_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kiraku-mf-cron-dispatcher",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs }),
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    // Do not propagate request headers, exception messages or response bodies.
    throw new Error("mf_dispatch_network_failed");
  }
  const accepted = response.status === 204;
  console.log(JSON.stringify({
    event: "mf_workflow_dispatch", dispatch_id: inputs.dispatch_id,
    scheduled_at: inputs.scheduled_at, full: inputs.full === "true",
    accepted, status: response.status,
  }));
  if (!accepted) throw new Error(`mf_dispatch_rejected_${response.status}`);
  // Acceptance is not sync success. Operators must follow the matching run's
  // MF_SYNC_COMPLETED + MF_STORAGE_PUBLISHED and the private summary timestamp.
  return inputs;
}
