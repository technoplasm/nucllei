<script lang="ts">
	import { settings } from '$lib/stores';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');

	let { saveSettings }: { saveSettings: (updated: any) => void | Promise<void> } = $props();

	// llens(#56): 音声入力の既定値。消費側(VoiceRecording / CallOverlay)と同じ値を持つ
	// — 根拠は _memos/audio.md「逐次文字起こし(疑似ストリーミング)と読み取り猶予」節
	const DEFAULT_VOICE_SILENCE_TIMEOUT = 4000;
	const DEFAULT_VOICE_MIN_CHUNK_DURATION = 300;

	const clamp = (value: number, min: number, max: number, fallback: number) =>
		Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;

	// 設定行は宣言的に定義して {#each} で展開する(旧 Interface タブの Chat/Voice/File
	// セクションをこのタブへ移設)。type: 'switch' | 'select' / init: $settings からの初期値 /
	// save: 既定は { key: 値 } で保存(title の nested 形や権限ゲートは行ごとに上書き)
	type Row = {
		key: string;
		label: string;
		type: 'switch' | 'select' | 'number';
		options?: { value: any; label: string }[];
		// type: 'number' の入力欄の範囲(単位は label に書く)
		number?: { min: number; max: number; step: number };
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
					key: 'ctrlEnterToSend',
					label: 'Enter Key Behavior',
					type: 'select',
					options: [
						{ value: false, label: 'Enter to Send' },
						{ value: true, label: 'Ctrl+Enter to Send' }
					],
					init: () => $settings.ctrlEnterToSend ?? true
				},
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
					key: 'copyFormatted',
					label: 'Copy Formatted Text',
					type: 'switch',
					init: () => $settings.copyFormatted ?? false
				},
				{
					// llens: 軸を「展開」に統一(2026-07-17)。既定は互い違い —
					// 本文コードは読めないと困るので ON、思考・ツール実行は補助情報なので OFF
					key: 'expandCodeBlocks',
					label: 'Expand code blocks in responses',
					type: 'switch',
					init: () => $settings.expandCodeBlocks ?? true
				},
				{
					key: 'expandDetails',
					label: 'Expand reasoning and tool calls',
					type: 'switch',
					init: () => $settings.expandDetails ?? false
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
				}
			]
		},
		{
			title: 'Voice',
			rows: [
				{
					// llens: Call(音声通話)ボタンの表示切り替え(見た目のみ・既定 OFF)
					key: 'callEnabled',
					label: 'Enable Call',
					type: 'switch',
					init: () => $settings.callEnabled ?? false
				},
				{
					key: 'voiceInterruption',
					label: 'Allow Voice Interruption in Call',
					type: 'switch',
					init: () => $settings.voiceInterruption ?? false
				},
				{
					// llens(#56): 読み取り猶予 — 無音がこの長さ続いたら発話の区切りとみなす。
					// 録音バーの逐次文字起こしと Call オーバーレイの両方が読む(既定 4 秒)
					key: 'voiceSilenceTimeout',
					label: 'Silence Before Transcribing (seconds)',
					type: 'number',
					number: { min: 0.5, max: 30, step: 0.5 },
					init: () => ($settings.voiceSilenceTimeout ?? DEFAULT_VOICE_SILENCE_TIMEOUT) / 1000,
					save: (value) => {
						const seconds = clamp(Number(value), 0.5, 30, DEFAULT_VOICE_SILENCE_TIMEOUT / 1000);
						values.voiceSilenceTimeout = seconds;
						saveSettings({ voiceSilenceTimeout: Math.round(seconds * 1000) });
					}
				},
				{
					// llens(#56): 短すぎる区間は文字起こしの精度が落ちるので送らない
					key: 'voiceMinChunkDuration',
					label: 'Minimum Voice Segment Length (seconds)',
					type: 'number',
					number: { min: 0, max: 5, step: 0.1 },
					init: () => ($settings.voiceMinChunkDuration ?? DEFAULT_VOICE_MIN_CHUNK_DURATION) / 1000,
					save: (value) => {
						const seconds = clamp(Number(value), 0, 5, DEFAULT_VOICE_MIN_CHUNK_DURATION / 1000);
						values.voiceMinChunkDuration = seconds;
						saveSettings({ voiceMinChunkDuration: Math.round(seconds * 1000) });
					}
				}
			]
		},
		{
			title: 'File',
			rows: []
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
								{:else if row.type === 'number'}
									<input
										class="bg-surface-muted hover:bg-surface-hover w-16 px-2 py-0.5 rounded-xs text-xs text-right outline-hidden"
										type="number"
										min={row.number?.min}
										max={row.number?.max}
										step={row.number?.step}
										bind:value={values[row.key]}
										onchange={() => saveRow(row)}
									/>
								{/if}
							</label>
						{/if}
					{/each}
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
