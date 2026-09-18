#!/usr/bin/env node
// S13: precompile the methodology page scripts. The pages used to load @babel/standalone
// (3.1 MB) and compile their JSX in every visitor's browser; they now load these compiled
// files. Output is exactly what @babel/standalone 7.29.0 produced in the browser for
// <script type="text/babel"> (presets react+env and its three default plugins), minus the
// inline source map.
//   Setup once:  npm i --prefix /tmp/methodology-build @babel/standalone@7.29.0
//   Build:       NODE_PATH=/tmp/methodology-build/node_modules node tools/build_methodology.js
// Edit the .jsx files (Report.jsx, tweaks-panel.jsx, methodology-boot.jsx), never the .js files.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const Babel = require('@babel/standalone');
if (Babel.version !== '7.29.0') {
  console.error(`REFUSE: @babel/standalone ${Babel.version}, need 7.29.0`);
  process.exit(1);
}
const dir = path.join(__dirname, '..', 'static', 'methodology');
const boot = path.join(dir, 'methodology-boot.jsx');
if (!fs.existsSync(boot)) {
  const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
  const m = html.match(/<script type="text\/babel">([\s\S]*?)<\/script>/);
  if (!m) {
    console.error('REFUSE: no methodology-boot.jsx and no inline text/babel block in index.html');
    process.exit(1);
  }
  fs.writeFileSync(boot, m[1]);
  console.log('extracted methodology-boot.jsx from index.html');
}
const opts = (filename) => ({
  filename, presets: ['react', 'env'],
  plugins: ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'],
  targets: { browsers: undefined }, sourceMaps: false,
});
for (const name of ['tweaks-panel', 'Report', 'methodology-boot']) {
  const src = name + '.jsx';
  const code = Babel.transform(fs.readFileSync(path.join(dir, src), 'utf8'), opts(src)).code;
  const out = path.join(dir, name + '.js');
  fs.writeFileSync(out, `/* Compiled from ${src} by tools/build_methodology.js (@babel/standalone 7.29.0). Do not edit. */\n` + code + '\n');
  console.log(`${name}.js`.padEnd(22), crypto.createHash('md5').update(fs.readFileSync(out)).digest('hex'));
}
