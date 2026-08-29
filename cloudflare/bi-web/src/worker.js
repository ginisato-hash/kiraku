// 喜らく 速報BI Worker（表示 + 15分更新スケジューラ）
// - HTML/JS/CSS は ASSETS binding（./public）から配信
// - BIデータ JSON/CSV は R2 bucket kiraku-bi-data の latest/ から読むだけ
// - Beds24 API は呼ばない。Beds24 token/.env は参照しない。
// - scheduled(): 15分毎のCloudflare Cron Triggerから、GitHub Actions
//   workflow_dispatch APIを叩くだけ（GITHUB_ACTIONS_DISPATCH_TOKEN secret）。
//   実際のBeds24取得・BI生成・R2 publishは既存の refresh-bi-r2.yml
//   （refresh-beds24-bi → publish-bi-r2）がそのまま担う。ここでは何も
//   fetch/生成/publishしない — GitHub Actionsの`on.schedule`が信頼できない
//   問題（fc1ffca参照）を、より確実なCloudflare Cron Triggerで置き換える
//   だけの役割。dispatch tokenの値は絶対にログしない。

const R2_PREFIX = "latest/";

// GitHub Actions workflow_dispatch target — matches refresh-bi-r2.yml exactly.
// This Worker never touches Beds24/R2 publish logic itself; it only asks
// GitHub to run the existing workflow, on a schedule GitHub's own
// `on.schedule` cron cannot reliably guarantee (see fc1ffca commit message
// for the original diagnosis; this Cron Trigger replaces that unreliable
// path, it does not duplicate the workflow's own logic).
const GITHUB_OWNER = "ginisato-hash";
const GITHUB_REPO = "kiraku";
const GITHUB_WORKFLOW_FILE = "refresh-bi-r2.yml";
const GITHUB_DISPATCH_REF = "main";
const GITHUB_API_VERSION = "2022-11-28";

// Fires the existing refresh-bi-r2.yml workflow via workflow_dispatch. Success
// is HTTP 204 (GitHub returns no body for this endpoint). Never logs the
// token value — only the outcome (status code / ok / error text).
async function dispatchBiRefreshWorkflow(env) {
  const token = env.GITHUB_ACTIONS_DISPATCH_TOKEN;
  if (!token) {
    return { ok: false, status: null, error: "GITHUB_ACTIONS_DISPATCH_TOKEN secret is not set" };
  }
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${GITHUB_WORKFLOW_FILE}/dispatches`;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "kiraku-bi-worker-cron-dispatcher",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: GITHUB_DISPATCH_REF }),
    });
    if (res.status === 204) {
      return { ok: true, status: 204, error: null };
    }
    // Read the body for diagnostics on failure (GitHub's error JSON never
    // contains the token), but still never echo request headers.
    const bodyText = await res.text().catch(() => "");
    return { ok: false, status: res.status, error: bodyText.slice(0, 500) };
  } catch (e) {
    return { ok: false, status: null, error: e instanceof Error ? e.message : String(e) };
  }
}
const MONTH_RE = /^\d{4}-\d{2}$/;

// path -> { key: R2 object key (R2_PREFIX付与前), type: Content-Type }
const DATA_ROUTES = {
  "/api/manifest": { key: "manifest.json", type: "application/json; charset=utf-8" },
  "/api/snapshot": { key: "bi_snapshot.json", type: "application/json; charset=utf-8" },
  "/data/manifest.json": { key: "manifest.json", type: "application/json; charset=utf-8" },
  "/data/bi_snapshot.json": { key: "bi_snapshot.json", type: "application/json; charset=utf-8" },
  "/data/bi_daily_timeseries.csv": { key: "bi_daily_timeseries.csv", type: "text/csv; charset=utf-8" },
  "/data/bi_monthly_kpi.csv": { key: "bi_monthly_kpi.csv", type: "text/csv; charset=utf-8" },
  "/data/bi_validation_status.json": { key: "bi_validation_status.json", type: "application/json; charset=utf-8" },
  "/data/bi_exception_summary.json": { key: "bi_exception_summary.json", type: "application/json; charset=utf-8" },
};

// 月別 /data/months/{YYYY-MM}/{filename} のfilename -> Content-Type
const MONTH_FILE_TYPES = {
  "bi_snapshot.json": "application/json; charset=utf-8",
  "bi_daily_timeseries.csv": "text/csv; charset=utf-8",
  "bi_monthly_kpi.csv": "text/csv; charset=utf-8",
  "bi_validation_status.json": "application/json; charset=utf-8",
  "bi_exception_summary.json": "application/json; charset=utf-8",
};

// 日付跨ぎ後もCDN/ブラウザに古いBIデータをキャッシュさせない（重大不具合対応。Phase 7）。
const NO_STORE_HEADERS = {
  "cache-control": "no-store, no-cache, must-revalidate",
  "pragma": "no-cache",
  "expires": "0",
};

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...NO_STORE_HEADERS },
  });
}

// R2オブジェクトをJSONとして読む。実R2Object(.text())とテスト用の簡易mock({body: "..."})の両方に対応する。
async function getR2Json(env, key) {
  const obj = await env.BI_DATA.get(R2_PREFIX + key);
  if (!obj) return null;
  try {
    const text = typeof obj.text === "function" ? await obj.text() : obj.body;
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

async function r2ObjectResponse(env, key, type) {
  const obj = await env.BI_DATA.get(R2_PREFIX + key);
  if (!obj) {
    return jsonResponse({ ok: false, error: "not found", key: R2_PREFIX + key }, 404);
  }
  return new Response(obj.body, {
    status: 200,
    headers: { "content-type": type, ...NO_STORE_HEADERS },
  });
}

async function handleApiMonths(env) {
  const manifest = await getR2Json(env, "manifest.json");
  if (!manifest) {
    return jsonResponse({ ok: false, error: "manifest not found" }, 404);
  }
  return jsonResponse({
    default_month: manifest.default_month ?? null,
    available_months: manifest.available_months || [],
    months_with_any_booking: manifest.months_with_any_booking || [],
    months_with_active_booking: manifest.months_with_active_booking || [],
    today_global_summary: manifest.today_global_summary ?? null,
  });
}

async function handleApiSnapshot(env, url) {
  const monthParam = url.searchParams.get("month");
  if (!monthParam) {
    // month指定なし: manifest.default_month があればそのmonth別snapshot、無ければ従来のlatest/bi_snapshot.json
    const manifest = await getR2Json(env, "manifest.json");
    if (manifest && manifest.default_month) {
      const key = `months/${manifest.default_month}/bi_snapshot.json`;
      const obj = await env.BI_DATA.get(R2_PREFIX + key);
      if (obj) {
        return new Response(obj.body, {
          status: 200,
          headers: { "content-type": "application/json; charset=utf-8", ...NO_STORE_HEADERS },
        });
      }
    }
    return r2ObjectResponse(env, "bi_snapshot.json", "application/json; charset=utf-8");
  }

  if (!MONTH_RE.test(monthParam)) {
    return jsonResponse({ ok: false, error: "invalid month format (expected YYYY-MM)" }, 400);
  }
  const manifest = await getR2Json(env, "manifest.json");
  const available = (manifest && manifest.available_months) || [];
  if (!available.includes(monthParam)) {
    return jsonResponse({ ok: false, error: "month not available", month: monthParam }, 404);
  }
  return r2ObjectResponse(env, `months/${monthParam}/bi_snapshot.json`, "application/json; charset=utf-8");
}

async function handleMonthDataFile(env, month, filename) {
  const type = MONTH_FILE_TYPES[filename];
  if (!type) {
    return jsonResponse({ ok: false, error: "not found" }, 404);
  }
  if (!MONTH_RE.test(month)) {
    return jsonResponse({ ok: false, error: "invalid month format (expected YYYY-MM)" }, 400);
  }
  return r2ObjectResponse(env, `months/${month}/${filename}`, type);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // /health : R2を読まない
    if (path === "/health") {
      return jsonResponse({
        ok: true,
        service: "kiraku-bi",
        data_source: "r2",
        r2_binding: "BI_DATA",
      });
    }

    if (path === "/api/months") {
      return handleApiMonths(env);
    }

    if (path === "/api/snapshot") {
      return handleApiSnapshot(env, url);
    }

    const monthFileMatch = path.match(/^\/data\/months\/([^/]+)\/([^/]+)$/);
    if (monthFileMatch) {
      const [, month, filename] = monthFileMatch;
      return handleMonthDataFile(env, decodeURIComponent(month), filename);
    }

    // BIデータ系ルート : R2(BI_DATA)から返す
    const route = DATA_ROUTES[path];
    if (route) {
      try {
        const obj = await env.BI_DATA.get(R2_PREFIX + route.key);
        if (!obj) {
          return jsonResponse(
            { ok: false, error: "not found", key: R2_PREFIX + route.key }, 404);
        }
        return new Response(obj.body, {
          status: 200,
          headers: { "content-type": route.type, ...NO_STORE_HEADERS },
        });
      } catch (e) {
        return jsonResponse({ ok: false, error: String(e) }, 500);
      }
    }

    // それ以外 : 静的アセット（index.html / app.js 等）
    return env.ASSETS.fetch(request);
  },

  // Cloudflare Cron Trigger (see wrangler.toml [triggers] crons) — fires every
  // 15 minutes. Only dispatches the existing GitHub Actions workflow; does
  // NOT fetch Beds24, does NOT touch R2, does NOT generate BI data itself.
  // ctx.waitUntil keeps the dispatch call alive past the handler's return
  // (Cloudflare Workers may otherwise cancel in-flight fetches once
  // scheduled() returns).
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      dispatchBiRefreshWorkflow(env).then((result) => {
        if (result.ok) {
          console.log(`bi_dispatch_ok cron=${event.cron} scheduled_time=${new Date(event.scheduledTime).toISOString()}`);
        } else {
          console.log(`bi_dispatch_failed cron=${event.cron} status=${result.status} error=${result.error}`);
        }
      })
    );
  },
};

export { dispatchBiRefreshWorkflow };
