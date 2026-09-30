import { cpSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
const cases = [
 ['property boundary',"raw.propertyId !== 'kiraku' || ",''],
 ['global digest',"existing.digest === digest ? existing.state : 'conflict'","existing.state"],
 ['exact downstream ack',"receipt.digest !== digest","false"],
 ['atomic receipts',"instance.state.storage.transaction(async tx => {","(async fn => fn(instance.state.storage))(async tx => {"],
 ['room high-water',"if (event.id <= high) continue;",''],
 ['manual observation',"if (current?.updatedAt && (Date.parse(current.updatedAt)","if (false && (Date.parse(current.updatedAt)"],
 ['vacancy proof',"if (vacated && typeof raw.vacatedRoomEmpty !== 'boolean') return null;",''],
 ['date receipt replay',"if (existing) return { receipt: existing.digest === digest", "if (false) return { receipt: existing.digest === digest"],
];
let passed=0;
for(const [name,find,replace] of cases){
 const dir=mkdtempSync(`${tmpdir()}/kiraku-receiver-mutation-`);
 try{
  cpSync(new URL('../src/',import.meta.url),dir+'/src',{recursive:true});mkdirSync(dir+'/test');
  cpSync(new URL('./guestOsEvents.test.mjs',import.meta.url),dir+'/test/guestOsEvents.test.mjs');
  writeFileSync(dir+'/package.json','{"type":"module"}');const file=dir+'/src/guestOsEvents.js',source=readFileSync(file,'utf8');assert.ok(source.includes(find));writeFileSync(file,source.replaceAll(find,replace));
  let output='';try{execFileSync(process.execPath,[dir+'/test/guestOsEvents.test.mjs'],{encoding:'utf8',stdio:'pipe'});}catch(e){output=String(e.stderr)+String(e.stdout);}
  assert.match(output,/AssertionError|Expected values/,name);passed++;console.log('killed -',name);
 }finally{rmSync(dir,{recursive:true,force:true});}
}
console.log(`Receiver mutations: ${passed} passed`);
