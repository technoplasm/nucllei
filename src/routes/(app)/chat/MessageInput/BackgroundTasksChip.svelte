<script lang="ts">
	// 入力欄下部行(floating トークンゲージと同じ行)の左側に出す
	// 「実行中バックグラウンド tool」チップ(#110)。走行中の background subagent が
	// あるときだけ描く。供給は Chat.svelte の常時検知ポーリング + SubagentSessionBlock の
	// live done(background-execs store 参照)。クリックで一覧をポップオーバー表示し、
	// 各 session の「開く」(/c/ 観測モードへ)と「停止」(SubagentSessionBlock の
	// stopSession と同じ合成: サーバが知る task ∪ 消費中ストリームの task)を提供する。
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	import { chatId, mobile } from '$lib/stores';
	import { getBackgroundExecs, removeBackgroundExec } from '$lib/stores/background-execs.svelte';
	import { chatChildrenState } from '$lib/stores/chat-children.svelte';
	import { getTaskIdsByChatId, stopTask } from '$lib/apis';
	import { getActiveTaskIds } from '$lib/utils/completion-streams';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownContent from '$lib/components/common/DropdownContent.svelte';

	const i18n = getContext('i18n');

	const entries = $derived(getBackgroundExecs($chatId));

	let show = $state(false);
	let stoppingIds = $state<Record<string, boolean>>({});

	// タイトルはサイドバー tree 用の chat-children store から引く(表示中 chat の
	// children を保持している)。無ければ sessionId 短縮で示す
	const labelFor = (sessionId: string): string =>
		chatChildrenState.entries.find((child) => child.id === sessionId)?.title ||
		sessionId.slice(0, 8);

	const openSession = (sessionId: string) => {
		show = false;
		goto(`/c/${sessionId}`);
	};

	// 停止: SubagentSessionBlock の stopSession と同じ合成。表示は即時に落とす —
	// 完走扱いの後始末(resume enqueue 等)は既存のポーリング検知に委ねる
	const stopSession = async (sessionId: string) => {
		stoppingIds[sessionId] = true;
		try {
			const taskRes = await getTaskIdsByChatId(sessionId).catch(() => null);
			const taskIds = [...new Set([...(taskRes?.task_ids ?? []), ...getActiveTaskIds(sessionId)])];
			for (const taskId of taskIds) {
				await stopTask(taskId).catch((error) => {
					toast.error(`${error}`);
					return null;
				});
			}
			removeBackgroundExec(sessionId);
		} finally {
			delete stoppingIds[sessionId];
		}
	};
</script>

{#if entries.length > 0}
	<Dropdown bind:show>
		<button
			class="flex items-center gap-1.5 bg-surface-raised border border-line rounded-full px-2.5 py-1 text-xs text-content-muted tabular-nums pointer-events-auto cursor-pointer hover:text-content transition"
			aria-label={$i18n.t('Background tasks')}
		>
			<span class="i-lucide-loader-circle size-3.5 block shrink-0 animate-spin"></span>
			<span>{entries.length}</span>
			{#if !$mobile}
				<span>{$i18n.t('Background')}</span>
			{/if}
		</button>

		{#snippet content()}
			<DropdownContent
				className="w-max min-w-[220px] max-w-[calc(100vw-2rem)] rounded-xl px-1 py-1 border border-line z-50 bg-surface-raised text-content shadow-sm"
				sideOffset={6}
				side="top"
				align="start"
			>
				{#each entries as entry (entry.sessionId)}
					<div class="flex items-center gap-2 px-3 py-2 text-sm min-w-0">
						<span class="i-lucide-bot size-4 block shrink-0 text-content-muted"></span>
						<div class="line-clamp-1 flex-1 min-w-0 font-medium">{labelFor(entry.sessionId)}</div>
						<button
							class="p-1 rounded-md text-content-muted hover:text-content hover:bg-surface-hover transition disabled:opacity-50"
							title={$i18n.t('Stop')}
							aria-label={$i18n.t('Stop')}
							disabled={stoppingIds[entry.sessionId] ?? false}
							onclick={() => stopSession(entry.sessionId)}
						>
							<span class="i-lucide-circle-stop size-4 block"></span>
						</button>
						<button
							class="p-1 rounded-md text-content-muted hover:text-content hover:bg-surface-hover transition"
							title={$i18n.t('Open')}
							aria-label={$i18n.t('Open')}
							onclick={() => openSession(entry.sessionId)}
						>
							<span class="i-lucide-arrow-up-right size-4 block"></span>
						</button>
					</div>
				{/each}
			</DropdownContent>
		{/snippet}
	</Dropdown>
{/if}
