import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, type Plugin } from 'vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';

// llens(dev): 開発中のキャッシュを完全無効化する。ブラウザ/中間プロキシが
// 古いモジュールを掴んで HMR/リロードが反映されない事故を防ぐ(dev サーバ限定)。
const devNoCache: Plugin = {
	name: 'llens-dev-no-cache',
	apply: 'serve',
	configureServer(server) {
		server.middlewares.use((_req, res, next) => {
			res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0');
			res.setHeader('Pragma', 'no-cache');
			res.setHeader('Expires', '0');
			next();
		});
	}
};

// /** @type {import('vite').Plugin} */
// const viteServerConfig = {
// 	name: 'log-request-middleware',
// 	configureServer(server) {
// 		server.middlewares.use((req, res, next) => {
// 			res.setHeader('Access-Control-Allow-Origin', '*');
// 			res.setHeader('Access-Control-Allow-Methods', 'GET');
// 			res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
// 			res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
// 			next();
// 		});
// 	}
// };

export default defineConfig({
	plugins: [
		devNoCache,
		sveltekit(),
		viteStaticCopy({
			targets: [
				{
					src: 'node_modules/onnxruntime-web/dist/*.jsep.*',

					dest: 'wasm'
				}
			]
		})
	],
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	build: {
		sourcemap: true
	},
	server: {
		watch: {
			// _links/ 配下の symlink ループ(llens ⇄ cage)を watcher が辿ると
			// ELOOP / OOM で dev サーバが落ちる
			ignored: ['**/_links/**']
		}
	},
	worker: {
		format: 'es'
	},
	test: {
		// _links/ はマシン固有 symlink(現行 llens 等)。辿ると symlink ループや
		// 巨大ツリーの走査で OOM するため探索対象から外す
		exclude: ['**/node_modules/**', '**/dist/**', '**/cypress/**', '**/_links/**']
	}
});
