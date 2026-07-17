<script lang="ts">
	import { getContext, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';

	import dayjs from 'dayjs';

	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { user } from '$lib/stores';
	import { formatFileSize } from '$lib/utils';
	import { getFileList } from '$lib/apis/files';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	// llens(#69): チャット入力からの「保存済みファイル」picker。
	// 自分の file を新しい順に一覧し(検索付き・複数選択)、onSelect(FileModel[]) を返す。
	// processFileById(再添付フック発火)は呼び出し側(MessageInput)の責務。
	const i18n = getContext('i18n');

	let {
		show = $bindable(false),
		onSelect = () => {}
	}: {
		show?: boolean;
		onSelect?: Function;
	} = $props();

	const limit = 30;

	let files = $state<any[]>([]);
	let total = $state(0);
	let loading = $state(false);

	let query = $state('');
	let queryDebounceTimeout: ReturnType<typeof setTimeout> | null = null;

	let selectedIds = $state<string[]>([]);

	// show 依存の初期化(#55 の変化検知ガードを踏襲)。fetchFiles 内の状態読みで
	// effect が再発火しないよう untrack で包む。
	let lastShow = false;
	$effect(() => {
		if (show !== lastShow) {
			lastShow = show;
			if (show) {
				untrack(() => {
					query = '';
					selectedIds = [];
					files = [];
					total = 0;
					fetchFiles(false);
				});
			}
		}
	});

	const fetchFiles = async (append = false) => {
		loading = true;

		const res = await getFileList({
			limit,
			offset: append ? files.length : 0,
			order_by: 'created_at',
			direction: 'desc',
			filename: query !== '' ? query : undefined,
			// admin の無指定は全ユーザー分になるため、自分の file に明示的に絞る
			user_ids: $user?.role === 'admin' ? [$user?.id] : undefined
		}).catch((err) => {
			toast.error(`${err}`);
			return null;
		});

		if (res) {
			// append の合間に自分のアップロードで並びが変わりうる — 同一 id は keyed each を
			// 壊すので除外(Files.svelte と同じガード)
			files = append
				? [...files, ...res.items.filter((item) => !files.some((f) => f.id === item.id))]
				: res.items;
			total = res.total;
		}

		loading = false;
	};

	const onQueryInput = () => {
		if (queryDebounceTimeout) {
			clearTimeout(queryDebounceTimeout);
		}
		queryDebounceTimeout = setTimeout(() => {
			fetchFiles(false);
		}, 300);
	};

	const toggle = (id) => {
		if (selectedIds.includes(id)) {
			selectedIds = selectedIds.filter((i) => i !== id);
		} else {
			selectedIds = [...selectedIds, id];
		}
	};

	const isImage = (file) => (file?.meta?.content_type ?? '').startsWith('image/');
	const formatTimestamp = (ts) => dayjs((ts ?? 0) * 1000).format('YYYY-MM-DD HH:mm');

	const submitHandler = () => {
		const selected = files.filter((f) => selectedIds.includes(f.id));
		show = false;
		onSelect(selected);
	};
</script>

<Modal size="md" bind:show>
	<div class="font-primary px-5 py-4 text-content flex flex-col max-h-[80vh]">
		<div class="flex justify-between items-center pb-2">
			<div class="text-lg font-medium self-center">{$i18n.t('Saved Files')}</div>
			<button class="self-center" type="button" onclick={() => (show = false)}>
				<span class="i-lucide-x size-5 block"></span>
			</button>
		</div>

		<div class="flex w-full mt-0.5 mb-2">
			<div class="self-center ml-1 mr-3">
				<span class="i-lucide-search size-3.5 block"></span>
			</div>
			<input
				class="w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
				bind:value={query}
				oninput={onQueryInput}
				placeholder={$i18n.t('Search Files')}
			/>
		</div>

		<div class="flex-1 overflow-y-auto min-h-40 -mx-1 px-1">
			{#if files.length === 0}
				<div class="w-full flex justify-center items-center py-10 text-sm text-content-subtle">
					{#if loading}
						<Spinner className="size-4" />
					{:else}
						{$i18n.t('No files found')}
					{/if}
				</div>
			{:else}
				<div class="flex flex-col">
					{#each files as file (file.id)}
						<button
							class="flex items-center gap-3 w-full px-2 py-1.5 rounded-xl text-left transition {selectedIds.includes(
								file.id
							)
								? 'bg-gray-100 dark:bg-gray-850'
								: 'hover:bg-black/5 dark:hover:bg-white/5'}"
							type="button"
							onclick={() => toggle(file.id)}
						>
							{#if isImage(file)}
								<img
									src={`${WEBUI_API_BASE_URL}/files/${file.id}/content`}
									alt={file?.meta?.name ?? file.filename}
									loading="lazy"
									class="size-9 shrink-0 rounded-lg object-cover bg-black/5 dark:bg-white/5"
								/>
							{:else}
								<div
									class="size-9 shrink-0 rounded-lg bg-black/5 dark:bg-white/5 flex items-center justify-center text-content-muted"
								>
									<span class="i-lucide-file-text size-4 block"></span>
								</div>
							{/if}

							<div class="flex-1 min-w-0 self-center">
								<div class="text-sm font-medium line-clamp-1">
									{file?.meta?.name ?? file.filename}
								</div>
								<div class="text-xs text-content-muted line-clamp-1 mt-0.5">
									{#if file?.meta?.size}
										{formatFileSize(file.meta.size)} •
									{/if}
									{formatTimestamp(file?.created_at)}
								</div>
							</div>

							<div class="shrink-0 self-center {selectedIds.includes(file.id) ? '' : 'invisible'}">
								<span class="i-lucide-check size-4 block"></span>
							</div>
						</button>
					{/each}
				</div>

				{#if files.length < total}
					<div class="w-full flex justify-center py-1.5">
						<button
							class="px-3.5 py-1 text-xs font-medium rounded-xl hover:bg-black/5 dark:hover:bg-white/5 text-content dark:hover:text-white transition flex items-center gap-2"
							type="button"
							disabled={loading}
							onclick={() => fetchFiles(true)}
						>
							{#if loading}
								<Spinner className="size-3" />
							{/if}
							{$i18n.t('Load More')}
						</button>
					</div>
				{/if}
			{/if}
		</div>

		<div class="flex justify-end pt-3 text-sm font-medium gap-1.5">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-transparent hover:bg-gray-100 text-content dark:hover:bg-gray-850 transition rounded-full"
				type="button"
				onclick={() => (show = false)}
			>
				{$i18n.t('Cancel')}
			</button>
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50 disabled:cursor-not-allowed"
				type="button"
				disabled={selectedIds.length === 0}
				onclick={submitHandler}
			>
				{$i18n.t('Attach')}{selectedIds.length > 0 ? ` (${selectedIds.length})` : ''}
			</button>
		</div>
	</div>
</Modal>
