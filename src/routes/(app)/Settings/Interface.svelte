<script lang="ts">
	import { settings } from '$lib/stores';
	import { onMount, getContext } from 'svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');

	let { saveSettings }: { saveSettings: (updated: any) => void | Promise<void> } = $props();

	// 設定行は宣言的に定義して {#each} で展開する(チャット挙動系は Chats タブへ分離)。
	// type: 'switch' | 'select' / init: $settings からの初期値 / save: 既定は { key: 値 } で保存
	type Row = {
		key: string;
		label: string;
		type: 'switch' | 'select';
		options?: { value: any; label: string }[];
		init: () => any;
		save?: (value: any) => void | Promise<void>;
		show?: () => boolean;
	};

	const ROWS: Row[] = [
		{
			key: 'landingPageMode',
			label: 'Landing Page Mode',
			type: 'select',
			options: [
				{ value: '', label: 'Default' },
				{ value: 'chat', label: 'Chat' }
			],
			init: () => $settings.landingPageMode ?? ''
		},
		{
			key: 'chatBubble',
			label: 'Chat Bubble UI',
			type: 'switch',
			init: () => $settings.chatBubble ?? true
		},
		{
			key: 'showUsername',
			label: 'Display the username instead of You in the Chat',
			type: 'switch',
			init: () => $settings.showUsername ?? false,
			show: () => !values.chatBubble
		},
		{
			key: 'widescreenMode',
			label: 'Widescreen Mode',
			type: 'switch',
			init: () => $settings.widescreenMode ?? false
		},
		{
			key: 'chatDirection',
			label: 'Chat direction',
			type: 'select',
			options: [
				{ value: 'auto', label: 'Auto' },
				{ value: 'LTR', label: 'LTR' },
				{ value: 'RTL', label: 'RTL' }
			],
			init: () => $settings.chatDirection ?? 'auto'
		},
		{
			key: 'notificationSound',
			label: 'Notification Sound',
			type: 'switch',
			init: () => $settings.notificationSound ?? true
		}
	];

	let values = $state<Record<string, any>>({});
	let loaded = $state(false);

	const saveRow = (row: Row) => {
		if (row.save) {
			row.save(values[row.key]);
		} else {
			saveSettings({ [row.key]: values[row.key] });
		}
	};

	onMount(() => {
		for (const row of ROWS) {
			values[row.key] = row.init();
		}
		loaded = true;
	});
</script>

<div class="flex flex-col h-full justify-between space-y-3 text-sm">
	<div class=" space-y-3 flex-1 min-h-0 overflow-y-auto">
		{#if loaded}
			<div>
				<div class=" mb-1.5 text-md font-medium space-y-1">{$i18n.t('Interface')}</div>

				{#each ROWS as row (row.key)}
					{#if row.show?.() ?? true}
						<label class="py-1 flex w-full justify-between items-center">
							<div class=" self-center text-xs">{$i18n.t(row.label)}</div>

							{#if row.type === 'switch'}
								<Switch bind:state={values[row.key]} onChange={() => saveRow(row)} />
							{:else if row.type === 'select'}
								<select
									class="bg-surface-muted hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-xs outline-hidden"
									bind:value={values[row.key]}
									onchange={() => saveRow(row)}
								>
									{#each row.options ?? [] as option (option.value)}
										<option value={option.value}>{$i18n.t(option.label)}</option>
									{/each}
								</select>
							{/if}
						</label>
					{/if}
				{/each}
			</div>
		{/if}
	</div>
</div>
