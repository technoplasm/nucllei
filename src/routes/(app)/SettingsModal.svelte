<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { models, settings } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';
	import { getModels } from '$lib/apis';

	import Modal from '$lib/components/common/Modal.svelte';
	import Account from './Settings/Account.svelte';
	import About from './Settings/About.svelte';
	import General from './Settings/General.svelte';
	import Interface from './Settings/Interface.svelte';
	import Chats from './Settings/Chats.svelte';
	import Data from './Settings/Data.svelte';

	const i18n = getContext('i18n');

	let { show = $bindable(false) }: { show?: boolean } = $props();

	// llens(#70 A2): タブ横断の設定検索(searchData/keywords)は撤去 — タブ 6 個に対して過剰。
	// Interface = UI / Chats = チャット挙動(旧 Interface の Chat/Voice/File)/
	// Data = import/export・アーカイブ・全削除(旧 Chats)
	const TABS = [
		{ id: 'general', label: 'General', icon: 'i-lucide-settings' },
		{ id: 'interface', label: 'Interface', icon: 'i-lucide-monitor' },
		{ id: 'chats', label: 'Chats', icon: 'i-lucide-messages-square' },
		{ id: 'data', label: 'Data', icon: 'i-lucide-database' },
		{ id: 'account', label: 'Account', icon: 'i-lucide-circle-user-round' },
		{ id: 'about', label: 'License', icon: 'i-lucide-info' }
	];

	let selectedTab = $state('general');

	const saveSettings = async (updated: any) => {
		await settings.set({ ...$settings, ...updated });
		await models.set(await getModels());
		await updateUserSettings({ ui: $settings });
	};
</script>

<!-- llens(#34): ユーザー設定モーダルは細めに(xl=896px → md=672px) -->
<Modal size="md" bind:show>
	<div class="text-content">
		<div class=" flex justify-between text-content px-5 pt-4 pb-1">
			<div class=" text-lg font-medium self-center">{$i18n.t('Settings')}</div>
			<button
				class="self-center"
				type="button"
				aria-label={$i18n.t('Close')}
				onclick={() => {
					show = false;
				}}
			>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-4 pt-1 pb-4 md:space-x-4">
			<div
				class="flex flex-row overflow-x-auto gap-2.5 md:gap-1 md:flex-col flex-1 md:flex-none md:w-40 text-content text-sm font-medium text-left mb-1 md:mb-0 scrollbar-none"
				onwheel={(event) => {
					// 横スクロールタブは縦ホイールでも流せるようにする(狭幅時)
					if (event.deltaY !== 0) {
						event.currentTarget.scrollLeft += event.deltaY;
					}
				}}
			>
				{#each TABS as tab (tab.id)}
					<button
						class="px-0.5 py-1 min-w-fit rounded-lg md:flex-none flex text-left {selectedTab ===
						tab.id
							? ''
							: ' text-content-subtle hover:text-content'}"
						type="button"
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
			<!-- 高さはブレークポイントに依らず固定(タブ切替・狭幅で伸縮させない)。
			     枠からあふれる分は Modal の背面スクロールが受ける。
			     flex-1 は md+(横並び)の幅取り用 — 狭幅の縦積みでは高さ方向に効いて
			     h-[34rem] を潰すので md: 限定 -->
			<div class="md:flex-1 h-[34rem]">
				{#if selectedTab === 'general'}
					<General {saveSettings} />
				{:else if selectedTab === 'interface'}
					<Interface {saveSettings} />
				{:else if selectedTab === 'chats'}
					<Chats {saveSettings} />
				{:else if selectedTab === 'data'}
					<Data />
				{:else if selectedTab === 'account'}
					<Account
						{saveSettings}
						saveHandler={() => {
							toast.success($i18n.t('Settings saved successfully!'));
						}}
					/>
				{:else if selectedTab === 'about'}
					<About />
				{/if}
			</div>
		</div>
	</div>
</Modal>
