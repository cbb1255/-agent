const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = filename => fs.readFileSync(path.join(__dirname, filename), 'utf8').replace(/\r\n/g, '\n');
const scriptSafe = source => source.replace(/<\/script/gi, '<\\/script');
const art = JSON.parse(read('assets/art.json'));
const sections = {
  EMBED_FONTS: read('assets/font-faces.css'),
  EMBED_STYLES: read('demo.css'),
  EMBED_DATA: scriptSafe('window.DEMO_ART = ' + JSON.stringify(art) + ';'),
  EMBED_API: scriptSafe(read('api-client.js')),
  EMBED_HISTORY: scriptSafe(read('chat-history.js')),
  EMBED_WORKBENCH: scriptSafe(read('workbench.js')),
  EMBED_SHELF: scriptSafe(read('shelf.js'))
};

let html = read('template.html');
for (const [marker, content] of Object.entries(sections)) {
  const placeholder = '/* ' + marker + ' */';
  if (!html.includes(placeholder)) throw new Error('Missing template placeholder ' + marker);
  html = html.replace(placeholder, () => content);
}
if (/\/\* EMBED_/.test(html)) throw new Error('An embed placeholder remains');
for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) {
  new vm.Script(script[1]);
}
fs.writeFileSync(path.join(__dirname, 'index.html'), html);
console.log('Built frontend/index.html');
