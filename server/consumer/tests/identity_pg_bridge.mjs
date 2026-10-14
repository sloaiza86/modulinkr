import { createInterface } from 'node:readline';
import { readFile, readdir } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const { PGlite } = await import(pathToFileURL(path.join(process.env.MODULINKR_TEST_PGLITE, 'dist/index.js')).href);
const pg=await PGlite.create();
await pg.exec('CREATE ROLE modulinkr LOGIN; CREATE ROLE modulinkr_ro LOGIN;');
const directory=process.argv[2];
for(const file of (await readdir(directory)).filter(n=>n.endsWith('.sql')).sort()) {
  await pg.exec(await readFile(path.join(directory,file),'utf8'));
}
await pg.exec('GRANT USAGE ON SCHEMA public TO modulinkr; GRANT ALL ON ALL TABLES IN SCHEMA public TO modulinkr; GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO modulinkr; SET SESSION AUTHORIZATION modulinkr;');
process.stdout.write(JSON.stringify({ready:true})+'\n');
for await(const line of createInterface({input:process.stdin})) {
  try {
    const request=JSON.parse(line);
    if(request.close) {await pg.close(); process.stdin.destroy(); break;}
    const result=await pg.query(request.sql,(request.params || []).map(v=>v && typeof v==='object' && 'bytes' in v ? Buffer.from(v.bytes,'hex') : v),{rowMode:'array'});
    process.stdout.write(JSON.stringify(result,(_,v)=>typeof v==='bigint'?String(v):v instanceof Uint8Array?{bytes:Buffer.from(v).toString('hex')}:v)+'\n');
  } catch(error) {
    process.stdout.write(JSON.stringify({error:error.message})+'\n');
  }
}
