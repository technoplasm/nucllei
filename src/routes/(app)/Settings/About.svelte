<script lang="ts">
	import { APP_VERSION } from '$lib/constants';
	import { WEBUI_NAME, config } from '$lib/stores';
	import { onMount, getContext } from 'svelte';

	import OPEN_WEBUI_LICENSE from '../../../../LICENSE.open-webui?raw';

	const i18n = getContext('i18n');

	type LicensedPackage = { name: string; version: string; license: string };

	// 依存クレジット(static/licenses.json — `uv run task licenses` で再生成)
	let licenses = $state<{ frontend: LicensedPackage[]; backend: LicensedPackage[] } | null>(null);
	// 取得の試行が終わったか(#135)。終わるまでは「読み込めなかった」案内を出さない
	let licensesLoaded = $state(false);

	// llens(2026-07-17 決定): UI で明示するのは「配布物に実体が同梱されるもの」だけ —
	// フォント(OFL-1.1、表記義務あり)のみ。MIT/BSD/Apache 等はソース同梱の各 LICENSE で
	// 充足するため全列挙はしない。licenses.json からの自動抽出なのでフォント入れ替えにも追従する。
	// 判定は前方一致でなく語としての OFL(#135): `SIL OFL 1.1` / `(OFL-1.1 AND MIT)` のような
	// 表記揺れで静かに 0 件になるのを避ける。
	const fontPackages = $derived(
		(licenses?.frontend ?? []).filter((pkg) => /(^|[^A-Za-z])OFL/i.test(pkg.license ?? ''))
	);

	onMount(async () => {
		licenses = await fetch('/licenses.json')
			.then((res) => (res.ok ? res.json() : null))
			.catch(() => null);
		licensesLoaded = true;
	});

	// fork base の BSD-3-Clause 全文。文字列リテラルで埋め込むと本文をカラム 0 に
	// 置く必要がありエディタのインデント検出が壊れるため、リポジトリ直下の
	// LICENSE.open-webui をビルド時に取り込む(?raw — 真実源はファイル 1 本)。
</script>

<div class="flex flex-col h-full justify-between space-y-3 text-sm mb-6">
	<div class=" space-y-3 flex-1 min-h-0 overflow-y-auto">
		<!-- 上段: 配備層(WEBUI_NAME でブランドされたハーネス/エージェント設計。
		     LLENS 等。権利表記は WEBUI_COPYRIGHT env — 無ければ段ごと出さない) -->
		{#if $WEBUI_NAME !== 'Nucllei' || $config?.copyright}
			<div>
				<div class="flex items-baseline gap-2">
					<span class="text-base font-medium">{$WEBUI_NAME}</span>
					{#if $config?.webui_version}
						<span class="text-xs text-content-muted">{$config.webui_version}</span>
					{/if}
				</div>

				{#if $config?.copyright}
					<div class="mt-1 text-xs text-content-subtle leading-relaxed">
						{$config.copyright}
					</div>
				{/if}
			</div>
		{/if}

		<!-- 中段: 本体(Nucllei) -->
		<div>
			<div class="flex items-baseline gap-2">
				<span class="text-base font-medium">Nucllei</span>
				{#if APP_VERSION}
					<span class="text-xs text-content-muted">{APP_VERSION}</span>
				{/if}
			</div>

			<div class="mt-1 text-xs text-content-subtle leading-relaxed">
				Nucllei is a foundation platform for AI agent harnesses, forked from
				<span class="whitespace-nowrap">Open WebUI</span> v0.6.5 (BSD-3-Clause). All modifications and
				additions are Copyright © Technoplasm, Inc., licensed under the PolyForm Noncommercial License
				1.0.0 with additional permissions (see LICENSE in the repository).
			</div>
		</div>

		<hr class=" border-line" />

		<!-- 下段 1: fork 元(Open WebUI)のライセンス全文 -->
		<div>
			<div class="text-sm font-medium">Upstream License</div>

			<div class="mt-2">
				<div class="flex items-baseline gap-2 text-content-muted">
					<a href="https://openwebui.com" target="_blank" class="text-sm"> Open WebUI v0.6.5 </a>
					<span class="text-xs">(fork base, BSD-3-Clause)</span>
				</div>

				<pre class="mt-1 text-xs text-content-subtle whitespace-pre-wrap">{OPEN_WEBUI_LICENSE}</pre>
			</div>
		</div>

		<!-- 下段 2: サードパーティ(配布物に実体が同梱されるもののみ = フォント)。
		     段は常に出す(#135): フォントは表記義務(OFL-1.1)があり実体も同梱されるので、
		     licenses.json が無い / OFL を 1 件も拾えないときは「欠けている」ことを見せる
		     (以前は段ごと黙って消え、本番で落ちていても気づけなかった)。
		     dev サーバでは licenses.json を生成しない限り常に案内側になる(uv run task licenses) -->
		<hr class=" border-line" />

		<div>
			<div class="text-sm font-medium">Third-Party Licenses</div>

			<div class="mt-2">
				<div class="text-xs font-medium text-content-muted">Fonts</div>

				{#if fontPackages.length > 0}
					<div class="mt-1 text-xs text-content-subtle leading-relaxed break-words">
						{fontPackages.map((pkg) => `${pkg.name} ${pkg.version}`).join(', ')} — SIL Open Font License
						1.1
					</div>
				{:else if licensesLoaded}
					<div class="mt-1 text-xs text-red-600 dark:text-red-400 leading-relaxed">
						{$i18n.t('Font license notices could not be loaded.')}
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>
