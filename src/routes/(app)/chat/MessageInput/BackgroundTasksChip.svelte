<script lang="ts">
	// 入力欄下部行(floating トークンゲージと同じ行)の左側に出す
	// 「バックグラウンドタスク」チップ(#110)。データ源は exec 行
	// (executions store — 5 秒ポーリング + 境界の refresh)。
	// running + 未配達 terminal(terminal ∧ !delivered)の件数バッジを出し、
	// 0 件なら非表示。クリックで一覧をポップオーバー表示し、tool 名・経過・
	// 個別停止を提供する。連鎖上限で BE の自動再開(配達)が保留中
	// (一覧 API の resume_suspended)もここに表示する。
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { chatId, mobile } from '$lib/stores';
	import {
		executionsState,
		isUndeliveredTerminal,
		refreshExecutions,
		type ExecutionEntry
	} from '$lib/stores/executions.svelte';
	import { stopChatExecution } from '$lib/apis/chats';
	import { subscribeTick, tickNow } from '$lib/utils/tick.svelte';
	import { executionErrorKind, TOOL_ERROR_LOOKS } from '$lib/utils/tool-render';

	import ToolErrorLabel from '$lib/components/chat/Messages/ToolErrorLabel.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownContent from '$lib/components/common/DropdownContent.svelte';

	const i18n = getContext('i18n');

	// 表示対象 = running + 未配達 terminal(chatId スコープ一致のときだけ)
	const entries = $derived(
		$chatId && executionsState.chatId === $chatId
			? executionsState.entries.filter(
					(entry) => entry.status === 'running' || isUndeliveredTerminal(entry)
				)
			: []
	);
	const runningCount = $derived(entries.filter((entry) => entry.status === 'running').length);
	const resumeSuspended = $derived(
		!!$chatId && executionsState.chatId === $chatId && executionsState.resumeSuspended
	);

	let show = $state(false);
	let stoppingIds = $state<Record<string, boolean>>({});

	// 実行中の経過時間(exec 行の created_at 起点)。running がある間だけ共有クロック購読
	$effect(() => {
		if (runningCount === 0) return;
		return subscribeTick();
	});
	const timeLabel = (entry: ExecutionEntry): string => {
		if (entry.status === 'running') {
			const seconds = Math.max(0, Math.round(tickNow() / 1000 - entry.created_at));
			return $i18n.t('{{DURATION}} seconds', { DURATION: seconds });
		}
		if (entry.duration != null) {
			return $i18n.t('{{DURATION}} seconds', { DURATION: Math.round(entry.duration) });
		}
		return '';
	};

	// #145: tool のエラー種別(サーバの旗)。見た目とラベルは ExecutionBlock と同じ表
	// (TOOL_ERROR_LOOKS / ToolErrorLabel)— 赤は violation / fatal だけ
	const errorKindOf = (entry: ExecutionEntry) => executionErrorKind(entry.status, entry.error_kind);

	const statusLabel = (entry: ExecutionEntry): string => {
		if (entry.status === 'done') return $i18n.t('Completed');
		if (entry.status === 'failed') return $i18n.t('Failed');
		if (entry.status === 'stopped') return $i18n.t('Stopped');
		if (entry.status === 'orphaned') return $i18n.t('Lost on server restart');
		return $i18n.t('Running...');
	};

	// 個別停止(D6: 親ターン stop は exec に伝播しない — 止めたければここ)。
	// 停止後は refresh で stopped を即取り込む(配達はサーバ駆動 — FE は表示のみ)
	const stopExecution = async (execId: string) => {
		const targetChatId = $chatId;
		if (!targetChatId) {
			return;
		}
		stoppingIds[execId] = true;
		try {
			await stopChatExecution(targetChatId, execId).catch((error) => {
				toast.error(`${error}`);
				return null;
			});
			await refreshExecutions(targetChatId);
		} finally {
			delete stoppingIds[execId];
		}
	};
</script>

{#if entries.length > 0 || resumeSuspended}
	<Dropdown bind:show>
		<button
			class="flex items-center gap-1.5 bg-surface-raised border border-line rounded-full px-2.5 py-1 text-xs text-content-muted tabular-nums pointer-events-auto cursor-pointer hover:text-content transition"
			aria-label={$i18n.t('Background tasks')}
		>
			{#if runningCount > 0}
				<span class="i-lucide-loader-circle size-3.5 block shrink-0 animate-spin"></span>
			{:else}
				<span class="i-lucide-circle-ellipsis size-3.5 block shrink-0"></span>
			{/if}
			<span>{entries.length}</span>
			{#if !$mobile}
				<span>{$i18n.t('Background tasks')}</span>
			{/if}
		</button>

		{#snippet content()}
			<DropdownContent
				className="w-max min-w-[240px] max-w-[calc(100vw-2rem)] rounded-xl px-1 py-1 border border-line z-50 bg-surface-raised text-content shadow-sm"
				sideOffset={6}
				side="top"
				align="start"
			>
				{#each entries as entry (entry.id)}
					{@const errorKind = errorKindOf(entry)}
					<div class="flex items-center gap-2 px-3 py-2 text-sm min-w-0">
						{#if errorKind}
							<span
								class="{TOOL_ERROR_LOOKS[errorKind].icon} size-4 block shrink-0 {TOOL_ERROR_LOOKS[
									errorKind
								].iconClass}"
							></span>
						{:else}
							<span class="i-lucide-terminal size-4 block shrink-0 text-content-muted"></span>
						{/if}
						<div class="flex-1 min-w-0">
							<div class="line-clamp-1 font-medium font-mono">{entry.tool_name}</div>
							<div class="text-xs text-content-muted tabular-nums line-clamp-1">
								{#if errorKind}
									<ToolErrorLabel kind={errorKind} />
								{:else}
									{statusLabel(entry)}
								{/if}
								{#if timeLabel(entry)}
									· {timeLabel(entry)}
								{/if}
							</div>
						</div>
						{#if entry.status === 'running'}
							<button
								class="p-1 rounded-md text-content-muted hover:text-content hover:bg-surface-hover transition disabled:opacity-50"
								title={$i18n.t('Stop')}
								aria-label={$i18n.t('Stop')}
								disabled={stoppingIds[entry.id] ?? false}
								onclick={() => stopExecution(entry.id)}
							>
								<span class="i-lucide-circle-stop size-4 block"></span>
							</button>
						{/if}
					</div>
				{/each}
				{#if resumeSuspended}
					<!-- 連鎖上限(契約 §M3): 無人連続ターンが上限に達し、BE が配達を保留中。
					     次のユーザー発話で連鎖カウントが切れて自動再開が再度有効になる -->
					<div
						class="flex items-center gap-2 px-3 py-2 text-xs text-content-muted border-t border-line min-w-0"
					>
						<span class="i-lucide-pause-circle size-3.5 block shrink-0"></span>
						<span class="line-clamp-2">{$i18n.t('Auto-resume paused (limit reached)')}</span>
					</div>
				{/if}
			</DropdownContent>
		{/snippet}
	</Dropdown>
{/if}
