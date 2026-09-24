const fs=require('fs'),z=require('zlib');
const files=fs.readdirSync('.').filter(f=>f.endsWith('.json')).sort();
const rows=[];
for(const f of files){const b=fs.readFileSync(f);const r={file:f,disk:b.length};
 for(const q of [1,4,5,6,11]){r['br'+q]=z.brotliCompressSync(b,{params:{[z.constants.BROTLI_PARAM_QUALITY]:q}}).length;}
 r.gzip6=z.gzipSync(b,{level:6}).length; rows.push(r); console.log(JSON.stringify(r));}
fs.writeFileSync(process.argv[2],JSON.stringify(rows,null,1));
