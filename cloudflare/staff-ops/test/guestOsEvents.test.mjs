import assert from 'node:assert/strict';
import { CleaningLiveState } from '../src/cleaningLiveState.js';
import { RECEIPT_DO, validateGuestOsEvent, eventDigest } from '../src/guestOsEvents.js';
import worker from '../src/worker.js';
import { applyRoomAccessStatus } from '../src/roomAccessState.js';
import { KIRAKU_ROOM_ORDER } from '../src/roomMaster.js';

function fixture() {
  const objects = new Map();
  let failApply = false, loseAck = false, badAck = false;
  const env = { CLEANING_LIVE: { idFromName: x => x, get: name => ({ fetch: async request => {
    const response = await instance(name).fetch(request);
    if (loseAck && new URL(request.url).pathname.endsWith('/apply')) { loseAck = false; throw new Error('TEST_LOST_ACK'); }
    if (badAck && new URL(request.url).pathname.endsWith('/apply')) return Response.json({kind:'delivered',id:1,digest:'WRONG'});
    return response;
  } }) } };
  function instance(name) {
    if (!objects.has(name)) {
      const map = new Map(), sent = [];
      let tail = Promise.resolve();
      const api = target => ({
        get: async key => structuredClone(target.get(key)),
        put: async (key, value) => { if (failApply && key.startsWith('guestos:applied:')) throw new Error('TEST_ATOMIC_FAILURE'); target.set(key, structuredClone(value)); },
        list: async ({ prefix }) => [...target].filter(([k]) => k.startsWith(prefix)),
      });
      const storage = { ...api(map), transaction: fn => {
        const run = tail.then(async () => { const copy = structuredClone(map); const result = await fn(api(copy)); map.clear(); for (const [k,v] of copy) map.set(k,v); return result; });
        tail = run.catch(() => {}); return run;
      } };
      const object = new CleaningLiveState({ storage, getWebSockets: () => [{ send: x => sent.push(JSON.parse(x)) }] }, env);
      objects.set(name, { object, map, sent });
    }
    return objects.get(name).object;
  }
  const call = async event => (await instance(RECEIPT_DO).fetch(new Request('https://cleaning-live/internal/guest-os/receive', { method: 'POST', body: JSON.stringify(event) }))).json();
  return { env, objects, instance, call, fail: v => { failApply = v; }, lose: () => { loseAck = true; }, badAck: () => { badAck = true; } };
}
const event = (id, type = 'STAY_CHECKED_IN', extra = {}) => ({ id, propertyId: 'kiraku', type, subjectRef: '1', canonicalRoomKey: 'kiraku:686763:3', roomNumber: '401', occurredAt: '2026-09-01T06:00:00.000Z', ...(type === 'STAY_CHECKED_OUT' ? { vacatedRoomEmpty: true } : {}), ...extra });
const DAY = '2026-09-01';
let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log('ok - Guest OS:', name); }

await check('exact property, room mapping, shape and data boundary refuse before storage', async () => {
  for (const bad of [event(0),event(1,'OTHER'),event(1,'STAY_CHECKED_IN',{propertyId:'other'}),event(1,'STAY_CHECKED_IN',{canonicalRoomKey:'kiraku:686762:1'}),
    event(1,'STAY_CHECKED_IN',{guestName:'TESTGUEST FORBIDDEN'}),event(1,'STAY_CHECKED_IN',{subjectRef:'BOOKING-REF'}),
    event(1,'STAY_CHECKED_IN',{occurredAt:'2026-02-30T06:00:00.000Z'}), event(1,'STAY_CHECKED_IN',{occurredAt:'2026-09-01T06:00:00'}),
    event(1,'STAY_CHECKED_OUT',{vacatedRoomEmpty:undefined}), event(1,'ROOM_CREDENTIAL_ALERT',{detail:'raw provider body'})]) {
    assert.equal(validateGuestOsEvent(bad),null); const f=fixture(); assert.equal((await f.call(bad)).reason,'INVALID_EVENT'); assert.equal(f.objects.get(RECEIPT_DO).map.size,0);
  }
});
await check('all eighteen contract rooms have exactly one accepted key', () => {
  const map={'685761':['607','507'],'686762':['601','602','603','604','503','504','403','404','405','406'],'686763':['501','502','401','402'],'686764':['605','505']};
  assert.deepEqual(Object.values(map).flat().sort(), [...KIRAKU_ROOM_ORDER].sort());
  for(const [type,rooms] of Object.entries(map))rooms.forEach((room,i)=>assert.ok(validateGuestOsEvent(event(1,'STAY_CHECKED_IN',{canonicalRoomKey:`kiraku:${type}:${i+1}`,roomNumber:room}))));
});
await check('checkout is persisted, broadcast and acknowledged with exact digest', async () => {
  const f=fixture(), e=event(1,'STAY_CHECKED_OUT'), r=await f.call(e);
  assert.deepEqual(r,{kind:'delivered',id:1,digest:await eventDigest(validateGuestOsEvent(e))});
  assert.deepEqual(f.objects.get(DAY).map.get('room:401'),{status:'CLEANING_ALLOWED',updatedAt:e.occurredAt,guestOsEventId:1});
  assert.equal(f.objects.get(DAY).sent.length,1); assert.equal(f.objects.get(RECEIPT_DO).map.get('guestos:receipt:1').state,'delivered');
});
await check('duplicate and cross-date conflicting reuse never apply twice', async () => {
  const f=fixture(), e=event(1,'STAY_CHECKED_OUT'); await f.call(e);
  assert.equal((await f.call(e)).kind,'duplicate'); assert.equal(f.objects.get(DAY).sent.length,1);
  assert.equal((await f.call({...e,occurredAt:'2026-09-02T06:00:00.000Z'})).reason,'EVENT_ID_CONFLICT'); assert.equal(f.objects.has('2026-09-02'),false);
});
await check('per-date receipt independently rejects conflicting replays', async () => {
  const f=fixture();await f.call(event(1,'STAY_CHECKED_OUT'));
  const response=await f.instance(DAY).fetch(new Request('https://cleaning-live/internal/guest-os/apply',{method:'POST',body:JSON.stringify(event(1))}));
  assert.equal((await response.json()).reason,'EVENT_ID_CONFLICT');assert.equal(f.objects.get(DAY).map.get('room:401').status,'CLEANING_ALLOWED');
});
await check('an unknown downstream response resumes from the durable receipt without a second effect', async () => {
  const f=fixture(), e=event(1,'STAY_CHECKED_OUT'); f.lose(); await assert.rejects(()=>f.call(e),/TEST_LOST_ACK/);
  assert.equal(f.objects.get(RECEIPT_DO).map.get('guestos:receipt:1').state,'pending');
  assert.equal((await f.call(e)).kind,'delivered'); assert.equal(f.objects.get(DAY).sent.length,1);
});
await check('a mismatched downstream acknowledgement cannot complete the global receipt', async () => {
  const f=fixture();f.badAck();assert.equal((await f.call(event(1,'STAY_CHECKED_OUT'))).kind,'retryable');
  assert.equal(f.objects.get(RECEIPT_DO).map.get('guestos:receipt:1').state,'pending');
});
await check('event ID ordering prevails even when older source clocks appear later', async () => {
  const f=fixture();await f.call(event(2));await f.call(event(1,'STAY_CHECKED_OUT',{occurredAt:'2026-09-01T07:00:00.000Z'}));
  assert.equal(f.objects.get(DAY).map.get('room:401').status,'WAITING_CHECKOUT');
});
await check('room, history and per-date receipt all roll back on a failed storage write', async () => {
  const f=fixture(); f.fail(true); await assert.rejects(()=>f.call(event(1,'STAY_CHECKED_OUT')),/TEST_ATOMIC_FAILURE/);
  assert.equal(f.objects.get(DAY).map.size,0); assert.equal(f.objects.get(DAY).sent.length,0);
  f.fail(false); assert.equal((await f.call(event(1,'STAY_CHECKED_OUT'))).kind,'delivered');
});
await check('out-of-order checkout cannot release a newer checked-in room', async () => {
  const f=fixture(); await f.call(event(2,'STAY_CHECKED_IN',{occurredAt:'2026-09-01T07:00:00.000Z'})); await f.call(event(1,'STAY_CHECKED_OUT'));
  assert.equal(f.objects.get(DAY).map.get('room:401').status,'WAITING_CHECKOUT'); assert.equal(f.objects.get(DAY).sent.length,1);
});
await check('equal source timestamps still follow event order; uncertain manual tie cannot allow cleaning', async () => {
  const f=fixture(); await f.call(event(1,'STAY_CHECKED_OUT')); await f.call(event(2,'STAY_CHECKED_IN'));
  assert.equal(f.objects.get(DAY).map.get('room:401').status,'WAITING_CHECKOUT');
  await f.instance(DAY).state.storage.put('room:401',{status:'WAITING_CHECKOUT',updatedAt:event(1).occurredAt});
  await f.call(event(3,'STAY_CHECKED_OUT'));assert.equal(f.objects.get(DAY).map.get('room:401').status,'WAITING_CHECKOUT');
  await f.instance(DAY).state.storage.put('room:401',{status:'CLEANING_ALLOWED',updatedAt:event(1).occurredAt});
  await f.call(event(4,'STAY_CHECKED_IN'));assert.equal(f.objects.get(DAY).map.get('room:401').status,'WAITING_CHECKOUT');
});
await check('other recorded occupancy refuses cleaning despite checkout', async () => {
  const f=fixture(); await f.call(event(1,'STAY_CHECKED_OUT',{vacatedRoomEmpty:false})); assert.equal(f.objects.get(DAY).map.get('room:401').status,'WAITING_CHECKOUT');
});
await check('a newer manual observation survives a delayed event', async () => {
  const f=fixture(); const object=f.instance(DAY);
  await object.state.storage.put('room:401',{status:'WAITING_CHECKOUT',updatedAt:'2026-09-01T08:00:00.000Z'});
  await f.call(event(1,'STAY_CHECKED_OUT')); assert.equal(f.objects.get(DAY).map.get('room:401').status,'WAITING_CHECKOUT'); assert.equal(f.objects.get(DAY).sent.length,0);
});
await check('move applies two rooms in one transaction and cannot claim cleaning completion', async () => {
  const f=fixture(); const move=event(1,'STAY_ROOM_MOVED',{canonicalRoomKey:'kiraku:686763:4',roomNumber:'402',fromCanonicalRoomKey:'kiraku:686763:3',fromRoomNumber:'401',roomRevision:2,vacatedRoomEmpty:true});
  assert.equal((await f.call(move)).kind,'delivered'); const state=f.objects.get(DAY);
  assert.equal(state.map.get('room:401').status,'CLEANING_ALLOWED'); assert.equal(state.map.get('room:402').status,'WAITING_CHECKOUT'); assert.equal(state.sent.length,2);
  assert.equal([...state.map.values()].some(v=>JSON.stringify(v).includes('DONE')),false);
});
await check('physical moves remain visible after an upstream snapshot no longer lists a departure', async () => {
  const f=fixture();await f.call(event(1,'STAY_CHECKED_OUT'));
  const records={ '401':f.objects.get(DAY).map.get('room:401') };
  const [row]=applyRoomAccessStatus([{room_number:'401',status:'VACANT'}],records);
  assert.equal(row.roomAccessStatus,'CLEANING_ALLOWED');assert.equal(row.roomAccessReadOnly,true);
  assert.equal(applyRoomAccessStatus([{room_number:'401',status:'VACANT'}],{'401':{status:'CLEANING_ALLOWED',updatedAt:event(1).occurredAt}})[0].roomAccessStatus,null);
});
await check('missing move evidence or same-room moves refuse', () => {
  const move=event(1,'STAY_ROOM_MOVED',{fromCanonicalRoomKey:'kiraku:686763:4',fromRoomNumber:'402',roomRevision:2,vacatedRoomEmpty:true});
  for(const key of ['fromCanonicalRoomKey','fromRoomNumber','roomRevision','vacatedRoomEmpty']){const bad={...move};delete bad[key];assert.equal(validateGuestOsEvent(bad),null);}
  assert.equal(validateGuestOsEvent({...move,fromCanonicalRoomKey:move.canonicalRoomKey,fromRoomNumber:move.roomNumber}),null);
});
await check('access-ready and credential-alert receipts never change cleaning state', async () => {
  const f=fixture(); await f.call(event(1,'ROOM_ACCESS_READY')); await f.call(event(2,'ROOM_CREDENTIAL_ALERT',{detail:'REVOCATION_UNKNOWN'}));
  assert.equal(f.objects.get(DAY).map.has('room:401'),false); assert.equal(f.objects.get(DAY).sent.length,0); assert.equal(f.objects.get(DAY).map.get('guestos:applied:2').type,'ROOM_CREDENTIAL_ALERT');
});
await check('concurrent replay applies once and conflicting replay is fenced', async () => {
  const f=fixture(), e=event(1,'STAY_CHECKED_OUT'); const results=await Promise.all([f.call(e),f.call(e),f.call({...e,vacatedRoomEmpty:false})]);
  assert.equal(results[2].reason,'EVENT_ID_CONFLICT'); assert.equal(f.objects.get(DAY).sent.length,1);
});
await check('JST date and previous day state remain isolated', async () => {
  const f=fixture(); await f.call(event(1,'STAY_CHECKED_OUT',{occurredAt:'2026-09-01T16:00:00.000Z'})); assert.ok(f.objects.has('2026-09-02')); assert.equal(f.objects.has(DAY),false);
});
await check('receipt read-back returns fixed proof, never source payload', async () => {
  const f=fixture(); await f.call(event(1)); const response=await f.instance(RECEIPT_DO).fetch(new Request('https://cleaning-live/internal/guest-os/receipt',{method:'POST',body:'{"id":1}'}));
  const proof=await response.json();assert.deepEqual(Object.keys(proof).sort(),['date','digest','state']);assert.equal(proof.state,'delivered');
});
await check('public HTTP cannot reach the private event ingress', async () => {
  for(const path of ['/internal/guest-os/receive','/api/guest-os/receive']) {
    const response=await worker.fetch(new Request('https://staff.example'+path,{method:'POST',body:JSON.stringify(event(1))}),{});assert.ok([302,401,503].includes(response.status));
  }
});
console.log(`Guest OS receiver: ${passed} passed`);
