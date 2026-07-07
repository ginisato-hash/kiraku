// 喜らく 速報BI Worker（表示専用）
// - HTML/JS/CSS は ASSETS binding（./public）から配信
// - BIデータ JSON/CSV は R2 bucket kiraku-bi-data の latest/ から読むだけ
// - Beds24 API は呼ばない。token/.env は参照しない。

const R2_PREFIX = "latest/";

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

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
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
          headers: { "content-type": route.type, "cache-control": "no-store" },
        });
      } catch (e) {
        return jsonResponse({ ok: false, error: String(e) }, 500);
      }
    }

    // それ以外 : 静的アセット（index.html / app.js 等）
    return env.ASSETS.fetch(request);
  },
};
