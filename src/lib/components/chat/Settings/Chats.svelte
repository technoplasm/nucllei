<script lang="ts">
	import { settings } from '$lib/stores';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');

	let { saveSettings }: { saveSettings: (updated: any) => void | Promise<void> } = $props();

	// 設定行は宣言的に定義して {#each} で展開する(旧 Interface タブの Chat/Voice/File
	// セクションをこのタブへ移設)。type: 'switch' | 'select' / init: $settings からの初期値 /
	// save: 既定は { key: 値 } で保存(title の nested 形や権限ゲートは行ごとに上書き)
	type Row = {
		key: string;
		label: string;
		type: 'switch' | 'select';
		options?: { value: any; label: string }[];
		init: () => any;
		save?: (value: any) => void | Promise<void>;
		show?: () => boolean;
	};

	type Section = {
		title: string;
		rows: Row[];
	};

	const SECTIONS: Section[] = [
		{
			title: 'Chat',
			rows: [
				{
					key: 'titleAutoGenerate',
					label: 'Title Auto-Generation',
					type: 'switch',
					init: () => $settings?.title?.auto ?? true,
					save: (value) => {
						saveSettings({
							title: {
								...$settings.title,
								auto: value
							}
						});
					}
				},
				{
					key: 'responseAutoCopy',
					label: 'Auto-Copy Response to Clipboard',
					type: 'switch',
					init: () => $settings.responseAutoCopy ?? false,
					// 有効化はクリップボード権限を確認してから確定する
					save: async (value) => {
						if (value) {
							const permission = await navigator.clipboard
								.readText()
								.then(() => 'granted')
								.catch(() => '');

							if (permission !== 'granted') {
								toast.error(
									$i18n.t(
										'Clipboard write permission denied. Please check your browser settings to grant the necessary access.'
									)
								);
								values.responseAutoCopy = false;
								return;
							}
						}
						saveSettings({ responseAutoCopy: value });
					}
				},
				{
					key: 'richTextInput',
					label: 'Rich Text Input for Chat',
					type: 'switch',
					init: () => $settings.richTextInput ?? true
				},
				{
					key: 'largeTextAsFile',
					label: 'Paste Large Text as File',
					type: 'switch',
					init: () => $settings.largeTextAsFile ?? false
				},
				{
					key: 'copyFormatted',
					label: 'Copy Formatted Text',
					type: 'switch',
					init: () => $settings.copyFormatted ?? false
				},
				{
					key: 'collapseCodeBlocks',
					label: 'Always Collapse Code Blocks',
					type: 'switch',
					init: () => $settings.collapseCodeBlocks ?? false
				},
				{
					key: 'expandDetails',
					label: 'Always Expand Details',
					type: 'switch',
					init: () => $settings.expandDetails ?? false
				},
				{
					key: 'ctrlEnterToSend',
					label: 'Enter Key Behavior',
					type: 'select',
					options: [
						{ value: false, label: 'Enter to Send' },
						{ value: true, label: 'Ctrl+Enter to Send' }
					],
					init: () => $settings.ctrlEnterToSend ?? false
				},
				{
					key: 'scrollOnBranchChange',
					label: 'Scroll to bottom when switching between branches',
					type: 'switch',
					init: () => $settings.scrollOnBranchChange ?? true
				},
				{
					key: 'hapticFeedback',
					label: 'Haptic Feedback',
					type: 'switch',
					init: () => $settings.hapticFeedback ?? false
				},
				{
					key: 'iframeSandboxAllowSameOrigin',
					label: 'iframe Sandbox Allow Same Origin',
					type: 'switch',
					init: () => $settings.iframeSandboxAllowSameOrigin ?? false
				},
				{
					key: 'iframeSandboxAllowForms',
					label: 'iframe Sandbox Allow Forms',
					type: 'switch',
					init: () => $settings.iframeSandboxAllowForms ?? false
				}
			]
		},
		{
			title: 'Voice',
			rows: [
				{
					key: 'voiceInterruption',
					label: 'Allow Voice Interruption in Call',
					type: 'switch',
					init: () => $settings.voiceInterruption ?? false
				}
			]
		},
		{
			title: 'File',
			rows: [
				{
					key: 'imageCompression',
					label: 'Image Compression',
					type: 'switch',
					init: () => $settings.imageCompression ?? false
				}
			]
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

	// 画像圧縮の上限サイズ(Image Compression 有効時のみ表示)
	let imageCompressionSize = $state<{ width: string; height: string }>({
		width: '',
		height: ''
	});

	// チャット背景画像(最下部の独立ボタン)
	let backgroundImageUrl = $state<string | null>(null);
	let inputFiles = $state<FileList | null>(null);
	let filesInputElement = $state<HTMLInputElement | null>(null);

	onMount(() => {
		for (const section of SECTIONS) {
			for (const row of section.rows) {
				values[row.key] = row.init();
			}
		}

		imageCompressionSize = $settings.imageCompressionSize ?? { width: '', height: '' };
		backgroundImageUrl = $settings.backgroundImageUrl ?? null;

		loaded = true;
	});
</script>

<div class="flex flex-col h-full justify-between space-y-3 text-sm">
	<input
		bind:this={filesInputElement}
		bind:files={inputFiles}
		type="file"
		hidden
		accept="image/*"
		onchange={() => {
			let reader = new FileReader();
			reader.onload = (event) => {
				backgroundImageUrl = `${event.target?.result}`;
				saveSettings({ backgroundImageUrl });
			};

			if (
				inputFiles &&
				inputFiles.length > 0 &&
				['image/gif', 'image/webp', 'image/jpeg', 'image/png'].includes(inputFiles[0]['type'])
			) {
				reader.readAsDataURL(inputFiles[0]);
			} else {
				toast.error($i18n.t('Unsupported file type.'));
				inputFiles = null;
			}
		}}
	/>

	<div class=" space-y-3 flex-1 min-h-0 overflow-y-auto">
		{#if loaded}
			{#each SECTIONS as section (section.title)}
				<div>
					<div class=" mb-1.5 text-md font-medium">{$i18n.t(section.title)}</div>

					{#each section.rows as row (row.key)}
						{#if row.show?.() ?? true}
							<label class=" py-1 my-0.5 flex w-full justify-between items-center">
								<div class=" self-center text-xs">{$i18n.t(row.label)}</div>

								{#if row.type === 'switch'}
									<Switch bind:state={values[row.key]} on:change={() => saveRow(row)} />
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

					{#if section.title === 'File' && values.imageCompression}
						<div class=" py-1 flex w-full justify-between items-center text-xs">
							<div class=" self-center text-sm">{$i18n.t('Image Max Compression Size')}</div>

							<div class="flex items-center gap-1">
								<input
									bind:value={imageCompressionSize.width}
									onchange={() => saveSettings({ imageCompressionSize })}
									type="number"
									class="w-20 px-2 py-0.5 bg-surface-muted rounded-xs outline-hidden text-center"
									min="0"
									placeholder="Width"
								/>x
								<input
									bind:value={imageCompressionSize.height}
									onchange={() => saveSettings({ imageCompressionSize })}
									type="number"
									class="w-20 px-2 py-0.5 bg-surface-muted rounded-xs outline-hidden text-center"
									min="0"
									placeholder="Height"
								/>
							</div>
						</div>
					{/if}
				</div>
			{/each}

			<hr class=" border-line my-2" />

			<div class=" py-1 flex w-full justify-between items-center">
				<div class=" self-center text-xs">{$i18n.t('Chat Background Image')}</div>

				<button
					class="text-xs px-3 py-0.5 rounded-full bg-secondary hover:bg-secondary-hover text-secondary-content"
					type="button"
					onclick={() => {
						if (backgroundImageUrl !== null) {
							backgroundImageUrl = null;
							saveSettings({ backgroundImageUrl });
						} else {
							filesInputElement?.click();
						}
					}}
				>
					{#if backgroundImageUrl !== null}
						{$i18n.t('Reset')}
					{:else}
						{$i18n.t('Upload')}
					{/if}
				</button>
			</div>
		{/if}
	</div>
</div>
