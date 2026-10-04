import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { execSync } from 'node:child_process';

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

// git tag からのバージョン導出(リポジトリ外ビルドや git 不在では null)
const gitDescribe = () => {
	try {
		// バージョンとみなすタグはリリース `vYYYYMMDD-NN` の 1 形式のみ(作業タグの混入防止)
		return execSync('git describe --tags --always --match "v[0-9]*"', {
			stdio: ['ignore', 'pipe', 'ignore']
		})
			.toString()
			.trim();
	} catch {
		return null;
	}
};

export default defineConfig({
	plugins: [
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
		// llens(#30/#25): バージョンの真実源は git tag(例 v20260716-01)。
		// 優先順: APP_VERSION env(docker build が焼き込む)→ git describe(dev / ローカルビルド。
		// tag を切るだけで次回ビルドから反映)→ package.json の OWUI 基点バージョン。
		__APP_VERSION__: JSON.stringify(
			process.env.APP_VERSION || gitDescribe() || process.env.npm_package_version
		)
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
			// llens(#84): app.html の <link rel="manifest"> が叩く BE route(PWA off では {})。
			// proxy に無いと dev だけ 404 が console に出る
			'/manifest.json': BACKEND_PROXY
		},
		watch: {
			// _links/ ・ ,始まりディレクトリ(個人作業スペース、symlink 置き場を含む)配下の
			// symlink ループ(llens ⇄ cage 等)を watcher が辿ると ELOOP / OOM で dev サーバが落ちる。
			// ディレクトリ自体を入口で弾く裸形と配下 glob を併記(入口で弾けば symlink を踏まない)
			// .venv は site-packages が数万ディレクトリあり、監視すると inotify 上限(ENOSPC)で落ちる
			ignored: [
				'**/_links',
				'**/_links/**',
				'**/,*',
				'**/,*/**',
				'**/.venv',
				'**/.venv/**',
				// dev の data/ (uploads・DB) は監視しない — 添付の録音 (mp4/webm) で inotify 上限 (ENOSPC) に当たり vite が落ちる
				'**/data',
				'**/data/**',
				// backend (Python) は FE から import しない。監視すると .py の保存のたびに全体リロードが
				// 走り、tmp ファイル (sed の作業ファイル等) で inotify 上限 (ENOSPC) に当たって落ちる (2026-10-04)
				'**/nucllei/**',
				'**/ext',
				'**/ext/**'
			]
		}
	},
	worker: {
		format: 'es'
	},
	test: {
		// _links/ ・ ,始まりディレクトリはマシン固有 symlink(現行 llens 等)を含む。
		// 辿ると symlink ループや巨大ツリーの走査で OOM するため探索対象から外す
		exclude: ['**/node_modules/**', '**/dist/**', '**/_links/**', '**/,*/**']
	}
});
