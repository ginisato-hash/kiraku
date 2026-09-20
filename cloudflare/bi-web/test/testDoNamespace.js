// testDoNamespace.js — a minimal in-memory stand-in for a Durable Object
// namespace binding, backed by REAL BiRefreshCoordinator instances (not a
// spy) so Worker-level tests (cron gating, webhook routing, callback
// routing) exercise the actual state machine end-to-end without needing
// Miniflare. One JS object instance per distinct `idFromName` name, each
// with its own isolated in-memory storage Map — matching how separate DO
// ids in production never share storage.
import { BiRefreshCoordinator } from "../src/biRefreshCoordinator.js";

function makeStorage() {
  const map = new Map();
  return {
    async get(key) { return map.has(key) ? map.get(key) : undefined; },
    async put(key, value) { map.set(key, value); },
  };
}

export function makeCoordinatorNamespace(env = {}) {
  const instances = new Map();
  return {
    idFromName(name) { return name; },
    get(name) {
      if (!instances.has(name)) {
        instances.set(name, new BiRefreshCoordinator({ storage: makeStorage() }, env));
      }
      const coordinator = instances.get(name);
      return { fetch: (request) => coordinator.fetch(request) };
    },
  };
}
