// Private Guest OS event contract. No booking reference, guest data or unlock key.
// The global receipt fixes event identity across dates; the date DO applies rooms
// and its receipt atomically. An unknown response can retry only the same event.
const ROOMS = {
  '685761': ['607', '507'],
  '686762': ['601', '602', '603', '604', '503', '504', '403', '404', '405', '406'],
  '686763': ['501', '502', '401', '402'],
  '686764': ['605', '505'],
};
const TYPES = ['STAY_CHECKED_IN', 'STAY_CHECKED_OUT', 'STAY_ROOM_MOVED', 'ROOM_ACCESS_READY', 'ROOM_CREDENTIAL_ALERT'];
const BASE = ['id', 'propertyId', 'type', 'subjectRef', 'canonicalRoomKey', 'roomNumber', 'occurredAt'];
const fail = reason => ({ kind: 'permanent_failure', reason });
const json = value => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
export const RECEIPT_DO = 'guest-os-receipts:kiraku:v1';
function validRoom(key, number) {
  const parts = typeof key === 'string' ? key.split(':') : [];
  return parts.length === 3 && parts[0] === 'kiraku' && /^[1-9]\d*$/.test(parts[2]) && ROOMS[parts[1]]?.[Number(parts[2]) - 1] === number;
}
export function validateGuestOsEvent(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const moved = raw.type === 'STAY_ROOM_MOVED', vacated = moved || raw.type === 'STAY_CHECKED_OUT';
  const extra = [...(moved ? ['fromCanonicalRoomKey', 'fromRoomNumber', 'roomRevision'] : []),
    ...(vacated ? ['vacatedRoomEmpty'] : []), ...(raw.type === 'ROOM_CREDENTIAL_ALERT' ? ['detail'] : [])];
  if (Object.keys(raw).some(k => ![...BASE, ...extra].includes(k)) || !BASE.every(k => Object.hasOwn(raw, k))) return null;
  if (raw.propertyId !== 'kiraku' || !TYPES.includes(raw.type) || !Number.isSafeInteger(raw.id) || raw.id < 1 ||
      typeof raw.subjectRef !== 'string' || !/^[1-9]\d{0,14}$/.test(raw.subjectRef) ||
      typeof raw.occurredAt !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(raw.occurredAt) ||
      !Number.isFinite(Date.parse(raw.occurredAt)) || new Date(raw.occurredAt).toISOString() !== raw.occurredAt) return null;
  const roomlessAlert = raw.type === 'ROOM_CREDENTIAL_ALERT' && raw.canonicalRoomKey === '' && raw.roomNumber === '';
  if (!roomlessAlert && !validRoom(raw.canonicalRoomKey, raw.roomNumber)) return null;
  if (moved && (!validRoom(raw.fromCanonicalRoomKey, raw.fromRoomNumber) || raw.fromCanonicalRoomKey === raw.canonicalRoomKey ||
      !Number.isSafeInteger(raw.roomRevision) || raw.roomRevision < 2)) return null;
  if (vacated && typeof raw.vacatedRoomEmpty !== 'boolean') return null;
  if (raw.detail !== undefined && (typeof raw.detail !== 'string' || !/^[A-Z][A-Z0-9_]{0,63}$/.test(raw.detail))) return null;
  return Object.fromEntries([...BASE, ...extra].filter(k => raw[k] !== undefined).map(k => [k, raw[k]]));
}
export async function eventDigest(event) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(event)));
  return [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2, '0')).join('');
}
const dateFor = event => new Date(Date.parse(event.occurredAt) + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
function effectsFor(event) {
  if (event.type === 'STAY_CHECKED_IN') return [[event.roomNumber, 'WAITING_CHECKOUT']];
  if (event.type === 'STAY_CHECKED_OUT') return [[event.roomNumber, event.vacatedRoomEmpty ? 'CLEANING_ALLOWED' : 'WAITING_CHECKOUT']];
  if (event.type === 'STAY_ROOM_MOVED') return [[event.fromRoomNumber, event.vacatedRoomEmpty ? 'CLEANING_ALLOWED' : 'WAITING_CHECKOUT'], [event.roomNumber, 'WAITING_CHECKOUT']];
  // A ready door key or a key alert is not proof of physical departure/cleaning.
  return [];
}
export async function receiveGuestOsEvent(instance, raw) {
  const event = validateGuestOsEvent(raw);
  if (!event) return fail('INVALID_EVENT');
  const digest = await eventDigest(event), date = dateFor(event), key = `guestos:receipt:${event.id}`;
  const admission = await instance.state.storage.transaction(async tx => {
    const existing = await tx.get(key);
    if (existing) return existing.digest === digest ? existing.state : 'conflict';
    await tx.put(key, { digest, date, state: 'pending' });
    return 'pending';
  });
  if (admission === 'conflict') return fail('EVENT_ID_CONFLICT');
  if (admission === 'delivered') return { kind: 'duplicate', id: event.id, digest };
  const stub = instance.env.CLEANING_LIVE.get(instance.env.CLEANING_LIVE.idFromName(date));
  const response = await stub.fetch(new Request('https://cleaning-live/internal/guest-os/apply', { method: 'POST', body: JSON.stringify(event) }));
  if (!response.ok) return { kind: 'retryable', reason: 'RECEIVER_UNCONFIRMED' };
  const receipt = await response.json();
  if (!['delivered', 'duplicate'].includes(receipt.kind) || receipt.id !== event.id || receipt.digest !== digest)
    return receipt.reason === 'EVENT_ID_CONFLICT' ? fail('EVENT_ID_CONFLICT') : { kind: 'retryable', reason: 'RECEIVER_UNCONFIRMED' };
  await instance.state.storage.put(key, { digest, date, state: 'delivered' });
  return { kind: 'delivered', id: event.id, digest };
}
export async function applyGuestOsEvent(instance, raw) {
  const event = validateGuestOsEvent(raw);
  if (!event) return fail('INVALID_EVENT');
  const digest = await eventDigest(event), date = dateFor(event), key = `guestos:applied:${event.id}`;
  const result = await instance.state.storage.transaction(async tx => {
    const existing = await tx.get(key);
    if (existing) return { receipt: existing.digest === digest ? { kind: 'duplicate', id: event.id, digest } : fail('EVENT_ID_CONFLICT'), broadcasts: [] };
    const broadcasts = [], history = (await tx.get('history')) || [];
    for (const [roomNumber, status] of effectsFor(event)) {
      const high = (await tx.get(`guestos:room:${roomNumber}`)) || 0;
      const current = await tx.get(`room:${roomNumber}`);
      if (event.id <= high) continue;
      await tx.put(`guestos:room:${roomNumber}`, event.id);
      // A newer staff observation wins over a delayed older event. Updating the
      // high-water still prevents that stale event from applying after a retry.
      if (current?.updatedAt && (Date.parse(current.updatedAt) > Date.parse(event.occurredAt) ||
          (Date.parse(current.updatedAt) === Date.parse(event.occurredAt) && current.guestOsEventId === undefined && status === 'CLEANING_ALLOWED'))) continue;
      await tx.put(`room:${roomNumber}`, { status, updatedAt: event.occurredAt, guestOsEventId: event.id });
      history.push({ roomNumber, from: current?.status || 'WAITING_CHECKOUT', to: status, updatedAt: event.occurredAt, source: 'guest_os' });
      broadcasts.push({ type: 'room_access_status', date, roomNumber, status, updatedAt: event.occurredAt });
    }
    await tx.put('history', history.slice(-100));
    // Store only the fixed protocol receipt, no source payload or personal data.
    await tx.put(key, { digest, type: event.type, roomNumber: event.roomNumber, receivedAt: new Date().toISOString() });
    return { receipt: { kind: 'delivered', id: event.id, digest }, broadcasts };
  });
  for (const message of result.broadcasts) instance.broadcast(message);
  return result.receipt;
}
export async function handleGuestOsInternal(instance, request) {
  const url = new URL(request.url);
  if (request.method !== 'POST') return null;
  if (!['/internal/guest-os/receive', '/internal/guest-os/apply', '/internal/guest-os/receipt'].includes(url.pathname)) return null;
  let body; try { body = await request.json(); } catch { return json(fail('INVALID_EVENT')); }
  if (url.pathname.endsWith('/receipt')) {
    if (!Number.isSafeInteger(body?.id) || body.id < 1) return json(fail('INVALID_EVENT'));
    return json((await instance.state.storage.get(`guestos:receipt:${body.id}`)) || { state: 'absent' });
  }
  return json(await (url.pathname.endsWith('/receive') ? receiveGuestOsEvent(instance, body) : applyGuestOsEvent(instance, body)));
}
