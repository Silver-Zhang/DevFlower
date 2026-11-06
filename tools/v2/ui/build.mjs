import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const here=path.dirname(fileURLToPath(import.meta.url));
// Reuse the repository's exact, licensed workbench dependency lock.
const deps=process.env.DEVFLOWER_UI_DEPS||path.resolve(here,'../../studio/workbench_ui');
const require=createRequire(path.join(deps,'package.json'));
const {build}=require('esbuild');
await build({entryPoints:[path.join(here,'index.jsx')],bundle:true,minify:true,format:'esm',target:'es2022',
 outfile:path.join(here,'../static/app.js'),nodePaths:[path.join(deps,'node_modules')],legalComments:'eof',
 define:{'process.env.NODE_ENV':'"production"'}});
