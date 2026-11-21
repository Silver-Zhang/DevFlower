import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const tools = path.dirname(fileURLToPath(import.meta.url));
const studio = path.join(tools, 'studio/workbench_ui');
const workbench = path.join(tools, 'workbench/ui');
const targets = new Map([
  ['workbench', [path.join(workbench, 'index.jsx'), path.join(workbench, '../static/app.js')]],
  ['studio', [path.join(studio, 'index.jsx'), path.join(studio, '../static/workbench.js')]],
]);
const target = targets.get(process.argv[2]);
if (!target || process.argv.length !== 3) {
  console.error('Usage: node tools/build_ui.mjs <workbench|studio>');
  process.exit(2);
}

const [entry, output] = target;
const dependencies = process.env.DEVFLOWER_UI_DEPS || studio;
const require = createRequire(path.join(dependencies, 'package.json'));
const { build } = require('esbuild');

await build({
  entryPoints: [entry],
  outfile: output,
  nodePaths: [path.join(dependencies, 'node_modules')],
  bundle: true,
  minify: true,
  format: 'esm',
  target: 'es2022',
  legalComments: 'eof',
  define: { 'process.env.NODE_ENV': '"production"' },
});
