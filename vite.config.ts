import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, type Plugin } from 'vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';

// llens(dev): dev フロントが叩く BE の origin。既定はローカル BE(:3000)。
// BACKEND_ORIGIN で上書きすれば別ホストの BE を指せる。
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || 'http://localhost:3000';
const BACKEND_PROXY = {
	target: BACKEND_ORIGIN,
	changeOrigin: true,
	// llens(#70 B3): BE 不達時は vite 既定の 500 ではなく 502 を返し、本番リバプロ
	// (Caddy)と挙動を揃える — フロントの apiFetch が 502-504 を「不達」と判定する。
	configure: (proxy: any) => {
		proxy.on('error', (_err: unknown, _req: unknown, res: any) => {
			if (res && !res.headersSent && res.writeHead) {
				res.writeHead(502, { 'Content-Type': 'application/json' });
			}
			if (res && res.end) {
				res.end(JSON.stringify({ detail: 'Bad Gateway (backend unreachable)' }));
			}
		});
	}
};

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
		// llens(#30/#25): deploy tag(例 prod20260708_01)を APP_VERSION で焼き込む。
		// 未設定なら package.json の OWUI 基点バージョンにフォールバック。
		APP_VERSION: JSON.stringify(process.env.APP_VERSION || process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	build: {
		sourcemap: true
	},
	server: {
		// llens(dev): LAN 上のホスト名(enda-personal 等)からの dev アクセスを許可する。
		// dev サーバ限定なので全ホスト許可でよい(0.0.0.0 listen と合わせて実機確認用)。
		allowedHosts: true,
		// llens(dev): BE を叩く経路を本番(docker 単一オリジン)に合わせて proxy に集約する。
		// フロントは相対 URL(constants.ts で WEBUI_BASE_URL='')で叩き、下記プレフィックスだけ
		// BE(既定 http://localhost:3000、BACKEND_ORIGIN で上書き可)へ流す。
		// tailscale 越しでも {frontend-host}/api → BE:3000/api で素通しになる。
		proxy: {
			'/api': BACKEND_PROXY,
			'/openai': BACKEND_PROXY,
			'/ollama': BACKEND_PROXY,
			'/oauth': BACKEND_PROXY,
			// llens(#70 B3): 不達復帰ポーリング(apiFetch)が叩く BE の health endpoint
			'/health': BACKEND_PROXY,
			// socket.io(path /ws/socket.io)は websocket アップグレードが要る
			'/ws': { ...BACKEND_PROXY, ws: true }
		},
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
