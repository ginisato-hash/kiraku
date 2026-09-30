import { WorkerEntrypoint } from 'cloudflare:workers';
import { RECEIPT_DO, validateGuestOsEvent } from './guestOsEvents.js';
export { default, CleaningLiveState } from './worker.js';

// Only a named service binding reaches this capability. No public HTTP route,
// shared staff password, Beds24 credential, R2 access or guest data is involved.
export class GuestOsEvents extends WorkerEntrypoint {
  async deliver(raw) {
    const event = validateGuestOsEvent(raw);
    if (!event) return { kind: 'permanent_failure', reason: 'INVALID_EVENT' };
    return this.#call('receive', event);
  }
  async receipt(id) {
    if (!Number.isSafeInteger(id) || id < 1) return { state: 'invalid' };
    return this.#call('receipt', { id });
  }
  async #call(operation, body) {
    const stub = this.env.CLEANING_LIVE.get(this.env.CLEANING_LIVE.idFromName(RECEIPT_DO));
    const response = await stub.fetch(new Request(`https://cleaning-live/internal/guest-os/${operation}`, { method: 'POST', body: JSON.stringify(body) }));
    if (!response.ok) return { kind: 'retryable', reason: 'RECEIVER_UNCONFIRMED' };
    return response.json();
  }
}
