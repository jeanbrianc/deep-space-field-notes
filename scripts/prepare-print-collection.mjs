import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const page = await readFile(path.join(root, 'app/page.tsx'), 'utf8');
const files = JSON.parse(`[${page.match(/const imageFiles = \[([\s\S]*?)\n\];/)[1].trim().replace(/,$/, '')}]`);
const names = Object.fromEntries([...page.match(/const commonNames:[\s\S]*?= \{([\s\S]*?)\n\};/)[1].matchAll(/"([^"]+)": "([^"]+)"/g)].map(m => [m[1], m[2]]));
names.Unknown = 'Uncharted Field';
const manifest = JSON.parse(await readFile(path.join(root, 'public/comparisons/manifest.json'), 'utf8'));
const catalogPath = path.join(root, 'print_products/catalog.json');
const catalog = JSON.parse(await readFile(catalogPath, 'utf8'));
const previous = new Map(catalog.products.map(p => [p.captureFile ?? p.object, p]));
const masters = path.join(root, 'print_products/masters/edge-to-edge-v1');
const previews = path.join(root, 'public/prints');
await mkdir(masters, { recursive: true });
await mkdir(previews, { recursive: true });
const width = 3600, height = 5400;
const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const products = [], proof = [], thumbs = [];
for (const file of files) {
  const m = file.match(/^Stacked_(\d+)_(.+)_([\d.]+)s_(LP|IRCUT)_(\d{8})-\d{6}_(cleaned|hand_processed)\.(jpg|png)$/);
  if (!m) throw new Error(`Invalid capture: ${file}`);
  const rawObject = m[2], mosaic = rawObject.startsWith('mosaic_'), object = rawObject.replace(/^mosaic_/, '') + (mosaic ? ' · Mosaic' : '');
  const title = (names[rawObject.replace(/^mosaic_/, '')] ?? object) + (mosaic ? ' · Mosaic' : '');
  const comparison = manifest.captures.find(c => c.baseline.filename === file);
  const nightsky = comparison?.curatedDefault === 'nightskyai';
  const source = nightsky ? path.join(root, 'public/comparisons', comparison.nightSkyAI.alignment?.sourceFilename ?? comparison.nightSkyAI.filename) : path.join(root, 'public/images', file);
  const bytes = await readFile(source), meta = await sharp(bytes).metadata();
  const frames = nightsky ? comparison.nightSkyAI.frames : Number(m[1]);
  const treatment = nightsky ? 'NIGHTSKYAI SELECTED STACK' : 'GALLERY SELECTED EDIT';
  const old = previous.get(file) ?? previous.get(object);
  const id = old?.id ?? `${title}-${object}`.normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  if (products.some(p => p.id === id)) throw new Error(`Duplicate print ID: ${id}`);
  const image = await sharp(bytes).rotate().resize(width, height, { fit:'cover', position:'centre' }).toColourspace('srgb').toBuffer();
  const titleSize = title.length > 25 ? 100 : 126;
  const overlay = Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">
    <rect x="180" y="180" width="2210" height="330" fill="black"/>
    <text x="270" y="295" fill="#d9ad6d" font-family="Courier New,monospace" font-size="54" letter-spacing="6">NORTHERN MICHIGAN</text>
    <text x="270" y="423" fill="#f0eee4" font-family="Georgia,serif" font-size="78" letter-spacing="5">DEEP SPACE FIELD NOTES</text>
    <rect x="180" y="4640" width="2940" height="580" fill="black"/>
    <text x="280" y="4790" fill="#f0eee4" font-family="Georgia,serif" font-size="${titleSize}">${escape(title)}</text>
    <text x="280" y="4900" fill="#d9ad6d" font-family="Courier New,monospace" font-size="${(object+treatment).length > 43 ? 46 : 54}" letter-spacing="4">${escape(object)} · ${frames} FRAMES · ${treatment}</text>
    <line x1="280" y1="4955" x2="510" y2="4955" stroke="#d9ad6d" stroke-width="5"/>
    <text x="280" y="5035" fill="#f0eee4" font-family="Courier New,monospace" font-size="48" letter-spacing="4">CAPTURED UNDER NORTHERN MICHIGAN SKIES</text>
    <text x="280" y="5110" fill="#f0eee4" font-family="Courier New,monospace" font-size="48" letter-spacing="4">ASTROPHOTOGRAPHY BY BRIAN JEAN</text>
  </svg>`);
  const poster = await sharp(image).composite([{ input:overlay }]).withMetadata({density:300}).jpeg({quality:96,chromaSubsampling:'4:4:4'}).toBuffer();
  await writeFile(path.join(masters, `${id}-12x18.jpg`), poster);
  await sharp(poster).resize(960,1440).jpeg({quality:88}).toFile(path.join(previews, `${id}.jpg`));
  const fingerprint = hash(bytes);
  const artworkSha256 = hash(poster);
  const unchanged = old?.sourceSha256 === fingerprint && old?.artworkSha256 === artworkSha256 && old?.layout === 'edge-to-edge-v1';
  products.push({id,object,title,captureFile:file,previewUrl:`/prints/${id}.jpg`,layout:'edge-to-edge-v1',artworkSha256,sourceSha256:fingerprint,sourcePixels:[meta.width,meta.height],nativeDpiAtPrintWidth:Number((meta.width/12).toFixed(1)),checkoutUrl:unchanged ? old.checkoutUrl : null,sampleApproved:unchanged ? old.sampleApproved : false,artworkApproved:unchanged ? old.artworkApproved : false,productPublished:unchanged ? old.productPublished : false,...(old?.fourthwallProductId ? {fourthwallProductId:old.fourthwallProductId} : {})});
  proof.push({id,object,source:path.relative(root,source),sourceSha256:fingerprint,sourcePixels:[meta.width,meta.height],frames,treatment,nativeDpiAtPrintWidth:meta.width/12,outputSha256:hash(poster),printReady:false});
  thumbs.push({input:await sharp(poster).resize(240,360).png().toBuffer(),left:(products.length-1)%6*260+10,top:Math.floor((products.length-1)/6)*400+10});
  console.log(`Prepared ${title}`);
}
catalog.schemaVersion=2;
catalog.storefrontOrigin ??= null;
catalog.product.layout='edge-to-edge-v1';
catalog.products=products;
await writeFile(catalogPath,JSON.stringify(catalog,null,2)+'\n');
await writeFile(path.join(masters,'manifest.json'),JSON.stringify({generatedAt:new Date().toISOString(),products:proof},null,2)+'\n');
const rows=Math.ceil(products.length/6);
const labels=Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="1560" height="${rows*400}">${products.map((p,i)=>`<text x="${i%6*260+10}" y="${Math.floor(i/6)*400+392}" fill="white" font-family="Arial" font-size="12">${escape(p.object+' · '+p.title).slice(0,45)}</text>`).join('')}</svg>`);
await sharp({create:{width:1560,height:rows*400,channels:3,background:'#17191b'}}).composite([...thumbs,{input:labels,left:0,top:0}]).png().toFile(path.join(masters,'collection-review.png'));
console.log(`${products.length} poster concepts prepared. No product or sale approvals granted.`);
