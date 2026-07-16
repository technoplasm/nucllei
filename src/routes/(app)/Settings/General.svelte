<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { getLanguages, changeLanguage } from '$lib/i18n';

	import { settings } from '$lib/stores';
	import { themeChangeHandler } from '$lib/utils/theme';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');

	let { saveSettings }: { saveSettings: (updated: any) => void | Promise<void> } = $props();

	let languages = $state<Awaited<ReturnType<typeof getLanguages>>>([]);

	// 設定行は宣言的に定義して {#each} で展開する。theme/language は $settings でなく
	// localStorage / i18n が持ち主なので init/save を行ごとに上書きする
	type Row = {
		key: string;
		label: string;
		type: 'switch' | 'select';
		options?: () => { value: any; label: string }[];
		init: () => any;
		save?: (value: any) => void | Promise<void>;
	};

	const ROWS: Row[] = [
		{
			key: 'theme',
			label: 'Theme',
			type: 'select',
			options: () => [
				{ value: 'system', label: `⚙️ ${$i18n.t('System')}` },
				{ value: 'dark', label: `🌑 ${$i18n.t('Dark')}` },
				{ value: 'light', label: `☀️ ${$i18n.t('Light')}` }
			],
			init: () => localStorage.theme ?? 'system',
			save: (value) => themeChangeHandler(value)
		},
		{
			key: 'lang',
			label: 'Language',
			type: 'select',
			options: () =>
				languages.map((language: any) => ({ value: language.code, label: language.title })),
			init: () => $i18n.language,
			save: (value) => changeLanguage(value)
		},
		{
			key: 'notificationEnabled',
			label: 'Notifications',
			type: 'switch',
			init: () => $settings.notificationEnabled ?? false,
			// 有効化はブラウザの通知権限を取ってから確定する
			save: async (value) => {
				if (value) {
					const permission = await Notification.requestPermission();

					if (permission !== 'granted') {
						toast.error(
							$i18n.t(
								'Response notifications cannot be activated as the website permissions have been denied. Please visit your browser settings to grant the necessary access.'
							)
						);
						values.notificationEnabled = false;
						return;
					}
				}

				saveSettings({ notificationEnabled: value });
			}
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

	onMount(async () => {
		languages = await getLanguages();

		for (const row of ROWS) {
			values[row.key] = row.init();
		}
		loaded = true;
	});
</script>

<div class="flex flex-col h-full justify-between text-sm">
	<div class="  flex-1 min-h-0 overflow-y-auto">
		{#if loaded}
			<div class="">
				<div class=" mb-1.5 text-md font-medium">{$i18n.t('WebUI Settings')}</div>

				{#each ROWS as row (row.key)}
					<label class=" py-1 flex w-full justify-between items-center">
						<div class=" self-center text-sm">{$i18n.t(row.label)}</div>

						{#if row.type === 'switch'}
							<Switch bind:state={values[row.key]} onChange={() => saveRow(row)} />
						{:else if row.type === 'select'}
							<select
								class="bg-surface-muted hover:bg-surface-hover w-fit px-2 py-0.5 pr-6 rounded-xs text-sm outline-hidden"
								bind:value={values[row.key]}
								onchange={() => saveRow(row)}
							>
								{#each row.options?.() ?? [] as option (option.value)}
									<option value={option.value}>{option.label}</option>
								{/each}
							</select>
						{/if}
					</label>
				{/each}
			</div>
		{/if}
	</div>
</div>
