<script lang="ts">
	// llens(#48 → #70 A2): Admin を Workspace に統合。Settings をワークスペース配下でホスト
	// (admin ゲートは layout 側)。タブは宣言配列 TABS で定義し、マークアップは {#each} 一箇所。
	// Code Interpreter 設定は撤去(nsjail+IPython 外部サンドボックス固定 — #11)。
	// ツールサーバー接続は独立タブをやめ General に完全統合(保存も共通)。
	import { getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { config } from '$lib/stores';
	import { getBackendConfig } from '$lib/apis';

	import General from './General.svelte';
	import Connections from './Connections.svelte';
	import Models from './Models.svelte';
	import Documents from './Documents.svelte';
	import Interface from './Interface.svelte';
	import Audio from './Audio.svelte';
	import Images from './Images.svelte';
	import Pipelines from './Pipelines.svelte';
	import Database from './Database.svelte';

	const i18n = getContext('i18n');

	const TABS = [
		{ id: 'general', label: 'General', icon: 'i-lucide-settings' },
		{ id: 'connections', label: 'Connections', icon: 'i-lucide-cloud' },
		{ id: 'models', label: 'Models', icon: 'i-lucide-box' },
		{ id: 'documents', label: 'Documents', icon: 'i-lucide-file-search' },
		{ id: 'interface', label: 'Interface', icon: 'i-lucide-monitor' },
		{ id: 'audio', label: 'Audio', icon: 'i-lucide-volume-2' },
		{ id: 'images', label: 'Images', icon: 'i-lucide-image' },
		{ id: 'pipelines', label: 'Pipelines', icon: 'i-lucide-layers' },
		{ id: 'db', label: 'Database', icon: 'i-lucide-database' }
	];

	let selectedTab = $state('general');

	const savedToast = () => {
		toast.success($i18n.t('Settings saved successfully!'));
	};

	// General/Documents はグローバル config に効く設定を持つので、保存後に再取得して反映する
	const savedWithConfigRefresh = async () => {
		savedToast();
		await tick();
		await config.set(await getBackendConfig());
	};
</script>

<div class="flex flex-col lg:flex-row w-full h-full pb-2 lg:space-x-4">
	<div
		class="flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-40 text-content text-sm font-medium text-left scrollbar-none"
		onwheel={(event) => {
			// 横スクロールタブは縦ホイールでも流せるようにする(狭幅時)
			if (event.deltaY !== 0) {
				event.currentTarget.scrollLeft += event.deltaY;
			}
		}}
	>
		{#each TABS as tab (tab.id)}
			<button
				class="px-0.5 py-1 min-w-fit rounded-lg lg:flex-none flex text-left {selectedTab === tab.id
					? ''
					: ' text-content-subtle hover:text-content'}"
				onclick={() => {
					selectedTab = tab.id;
				}}
			>
				<div class=" self-center mr-2">
					<span class="{tab.icon} size-4 block"></span>
				</div>
				<div class=" self-center">{$i18n.t(tab.label)}</div>
			</button>
		{/each}
	</div>

	<div class="flex-1 mt-3 lg:mt-0 overflow-y-scroll pr-1 scrollbar-hidden">
		{#if selectedTab === 'general'}
			<General saveHandler={savedWithConfigRefresh} />
		{:else if selectedTab === 'connections'}
			<Connections onSave={savedToast} />
		{:else if selectedTab === 'models'}
			<Models />
		{:else if selectedTab === 'documents'}
			<Documents onSave={savedWithConfigRefresh} />
		{:else if selectedTab === 'interface'}
			<Interface onSave={savedToast} />
		{:else if selectedTab === 'audio'}
			<Audio saveHandler={savedToast} />
		{:else if selectedTab === 'images'}
			<Images on:save={savedToast} />
		{:else if selectedTab === 'pipelines'}
			<Pipelines saveHandler={savedToast} />
		{:else if selectedTab === 'db'}
			<Database />
		{/if}
	</div>
</div>
