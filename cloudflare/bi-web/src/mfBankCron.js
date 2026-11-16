// Scheduling metadata only. MF credentials, account IDs and financial payloads
// stay in the existing private Actions workflow. No HTTP route invokes this.
export const MF_BANK_CRON = "17 * * * *";

export function mfBankDispatchInputs(event, env = {}) {
  const time = new Date(event.scheduledTime);
  if (event.cron !== MF_BANK_CRON || !Number.isFinite(event.scheduledTime)
      || !Number.isFinite(time.getTime()) || time.getUTCMinutes() !== 17) {
    throw new Error("invalid_mf_scheduled_event");
  }
  const scheduledAt = time.toISOString();
  const jstHour = (time.getUTCHours() + 9) % 24;
  // MF's temporary daily 01:00-06:00 JST maintenance blocks accounting API
  // reads. The first 06:17 tick catches up the SAME day's planned 03:17 full.
  // This is derived only from the scheduled slot, not handler/runner wall time.
  const catchUp = env.MF_NIGHTLY_MAINTENANCE === "true" && jstHour >= 6;
  const fullTime = catchUp
    ? new Date(event.scheduledTime - (jstHour - 3) * 60 * 60 * 1000) : new Date(time);
  fullTime.setUTCSeconds(0, 0);
  const full = fullTime.getUTCHours() === 18;
  return {
    // Use the intended slot, never Date.now(): a delayed JST03:17 tick must
    // still reconcile the full accounting period after it starts/queues.
    full: full ? "true" : "false",
    scheduled_at: scheduledAt,
    dispatch_id: `mf-${scheduledAt.slice(0, 13).replace(/[-T]/g, "")}`,
    full_planned_at: full ? fullTime.toISOString() : "",
  };
}

function githubHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_ACTIONS_DISPATCH_TOKEN}`,
    Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "kiraku-mf-cron-dispatcher", "Content-Type": "application/json",
  };
}

async function confirmedDailyFull(env, plannedAt) {
  const base = "https://api.github.com/repos/ginisato-hash/kiraku/actions";
  async function get(path) {
    const response = await fetch(base + path, {
      headers: githubHeaders(env), signal: AbortSignal.timeout(15000),
    });
    if (response.status !== 200) throw new Error("mf_full_status_unavailable");
    return response.json();
  }
  const history = await get("/workflows/sync-mf-banks.yml/runs?event=workflow_dispatch&status=success&per_page=50");
  if (!Array.isArray(history.workflow_runs) || history.workflow_runs.length > 50) throw new Error("mf_full_status_invalid");
  const candidates = history.workflow_runs.filter(run =>
    run.head_branch === "main" && run.conclusion === "success"
    && typeof run.display_title === "string" && run.display_title.endsWith(`full@${plannedAt}`)
    && Number.isSafeInteger(run.id) && run.id > 0).slice(0, 3);
  for (const run of candidates) {
    const result = await get(`/runs/${run.id}/jobs?per_page=100`);
    if (!Array.isArray(result.jobs)) throw new Error("mf_full_jobs_invalid");
    if (result.jobs.some(job => job.name === "sync-mf-banks" && job.conclusion === "success"
      && ["Restore private ledger", "Fetch bank records from MF", "Commit private ledger generation"]
        .every(name => job.steps?.some(step => step.name === name && step.conclusion === "success")))) return true;
  }
  return false;
}

export async function dispatchMfBankSync(event, env) {
  const inputs = mfBankDispatchInputs(event, env);
  const jstHour = (new Date(event.scheduledTime).getUTCHours() + 9) % 24;
  if (env.MF_NIGHTLY_MAINTENANCE === "true" && jstHour >= 1 && jstHour < 6) {
    console.log(JSON.stringify({
      event: "mf_maintenance_deferred", scheduled_at: inputs.scheduled_at,
      dispatch_id: inputs.dispatch_id, full_planned_at: inputs.full_planned_at,
    }));
    return { ...inputs, deferred: true };
  }
  if (!env.GITHUB_ACTIONS_DISPATCH_TOKEN) throw new Error("mf_dispatch_token_missing");
  if (env.MF_NIGHTLY_MAINTENANCE === "true" && jstHour >= 6) {
    try {
      if (await confirmedDailyFull(env, inputs.full_planned_at)) {
        inputs.full = "false";
        inputs.full_planned_at = "";
      }
    } catch {
      // If GitHub metadata cannot be read, reconcile fully rather than lose
      // today's pending full. Never treat HTTP dispatch acceptance or a
      // secret-gated green/skip run as proof that the full actually completed.
      console.log(JSON.stringify({event: "mf_full_confirmation_unavailable", scheduled_at: inputs.scheduled_at}));
    }
  }
  let response;
  try {
    response = await fetch("https://api.github.com/repos/ginisato-hash/kiraku/actions/workflows/sync-mf-banks.yml/dispatches", {
      method: "POST",
      headers: githubHeaders(env),
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
    full_planned_at: inputs.full_planned_at,
    accepted, status: response.status,
  }));
  if (!accepted) throw new Error(`mf_dispatch_rejected_${response.status}`);
  // Acceptance is not sync success. Operators must follow the matching run's
  // MF_SYNC_COMPLETED + MF_STORAGE_PUBLISHED and the private summary timestamp.
  return inputs;
}
