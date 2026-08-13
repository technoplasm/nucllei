<script lang="ts">
	// 親 tool ブロック内の子 agent session インライン表示(#107 P3)。
	// run_subagent の結果(render.result.kind="subagent" → ResultDisplay 'subagent')から
	// 子 chat を遅延 fetch し、transcript を読み取り専用で描く。ToolCallBlock の
	// {#if open} の内側に置かれるため「開くまで fetch しない」が自然に効く。
	//
	// live 追従: 子 chat_id で completion-streams に独立 handler を追加登録する
	// (chatHandlers は chatId → Set<handler> なので、親 Chat の handler とも
	// 同一 session を指す他ブロックの handler とも衝突しない)。
	// 子の chat:usage / tool:progress はグローバル store に書かず捨てる — 親の
	// floating ゲージへの混線防止(#107 P3 の全体決定)。
	//
	// 深さは 1 で切る: 子 transcript 内の subagent ブロックは展開せず「開く」リンク
	// のみ。深さは context で伝搬する(途中の ContentBlocks / ToolCallBlock に
	// prop を足さない)。
	import { getContext, setContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	import { chatId, chats, currentChatPage, models } from '$lib/stores';
	import { requestSubagentResume } from '$lib/stores/subagent-resume.svelte';
	import { getChatById, cloneChatById, getChatList } from '$lib/apis/chats';
	import { getTaskIdsByChatId, stopTask } from '$lib/apis';
	import {
		attachToTask,
		getActiveTaskIds,
		registerChatHandler,
		unregisterChatHandler,
		type CompletionEnvelope
	} from '$lib/utils/completion-streams';
	import { applyCompletionData } from '$lib/utils/chat-events';
	import { createMessagesList } from '$lib/utils';
	import { formatDurationSeconds } from '$lib/utils/duration';
	import type { ChatHistory } from '$lib/types/chat';
	import type { ResultDisplay } from '$lib/utils/tool-render';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import ContentBlocks from './ContentBlocks.svelte';

	const i18n = getContext('i18n');

	type SubagentDisplay = Extract<ResultDisplay, { kind: 'subagent' }>;

	let {
		id = '',
		display
	}: {
		id?: string;
		display: SubagentDisplay;
	} = $props();

	// 結果 JSON は確定済みで変異しない — 初期値で固定してよい(意図的な初期値キャプチャ)
	// svelte-ignore state_referenced_locally
	const sessionId = display.sessionId;

	// #109: background spawn の即返し結果(status: "running")を表示中か。
	// svelte-ignore state_referenced_locally
	const spawnRunning = display.status === 'running';

	// #109: spawn 即返し結果の表示中に子の done を検知したら、親ターンの自動再開を
	// resume store へ要求する。enqueue は session 単位 once(store 側)。
	// 「親 chat 表示中 ∧ turn 非走行」ガード・処理済み drop・自動送信は Chat.svelte が持つ
	// (観測モード /c/ では Chat 側ガードにより送信されない)。
	// 自動送信された turn の await_subagent 結果は answer 形(status 無し)なので、
	// ここの検知対象に戻らない — 再開ループは構造的に閉じない。
	const requestResumeIfSpawnRunning = () => {
		if (!spawnRunning) {
			return;
		}
		const parentChatId = $chatId;
		if (parentChatId) {
			requestSubagentResume(parentChatId, sessionId);
		}
	};

	// 入れ子深さ(表示は深さ 1 で切る)。setContext は init 時のみ有効なので
	// ここで固定する。
	const depth = getContext<number | undefined>('nucllei:subagent-depth') ?? 0;
	setContext('nucllei:subagent-depth', depth + 1);
	const expandable = depth < 1;

	let status = $state<'loading' | 'ready' | 'error'>('loading');
	let running = $state(false);
	let stopping = $state(false);
	let history = $state<ChatHistory>({ messages: {}, currentId: null });
	let presetId = $state('');
	let title = $state('');

	const presetName = $derived($models.find((m) => m.id === presetId)?.name ?? presetId);
	const messagesList = $derived(createMessagesList(history, history.currentId));

	// usage 要点(display.usage — 完走時に BE が結果 JSON へ同梱した persisted 値)
	const usageLabel = $derived.by(() => {
		const usage = display.usage;
		if (!usage) return '';
		const parts: string[] = [];
		if (usage.prompt_tokens != null) parts.push(`↑${usage.prompt_tokens}`);
		if (usage.completion_tokens != null) parts.push(`↓${usage.completion_tokens}`);
		if (parts.length > 0) parts.push('tok');
		if (usage.duration != null) {
			parts.push(
				`· ${$i18n.t('{{DURATION}} seconds', { DURATION: formatDurationSeconds(usage.duration) })}`
			);
		}
		return parts.join(' ');
	});

	const fetchChat = async (): Promise<boolean> => {
		const res = await getChatById(sessionId).catch(() => null);
		if (!res?.chat) {
			return false;
		}
		title = res.chat.title ?? '';
		presetId = (res.chat.models ?? [])[0] ?? '';
		history = res.chat.history ?? { messages: {}, currentId: null };
		return true;
	};

	// 生きた task が無いのに done が立っていない assistant は中断残骸 —
	// スピナーを止める(Chat.svelte loadChat と同じ規律)
	const markSettled = () => {
		for (const message of Object.values(history.messages)) {
			if (message.role === 'assistant') {
				message.done = true;
			}
		}
	};

	// 継続ターンで fetch 後に増えたメッセージ(BE が子 history へ直接積む)への追従:
	// 未知の message_id の snapshot が来たら読み直す(多重発火ガード付き。読み直し中に
	// 落ちたイベントは後続の間引きスナップショット到着で収束する)
	// アタッチ済み(fetch 未解決の間を含む)task の二重アタッチ防止(Chat.svelte の
	// attachedTaskIds と同じパターン)。二重アタッチは delta の二重適用になる
	const attachedTaskIds = new Set<string>();

	let refetching = false;
	const refetchForUnknownMessage = () => {
		if (refetching) return;
		refetching = true;
		fetchChat().finally(() => {
			refetching = false;
		});
	};

	const settle = async () => {
		unregisterChatHandler(sessionId, envelopeHandler);
		running = false;
		stopping = false;
		// 確定状態(persisted usage / done)へ置換
		await fetchChat();
		markSettled();
		// #109: live 追従中の完走検知 — spawn 即返し結果なら親ターンの再開を要求
		requestResumeIfSpawnRunning();
	};

	const envelopeHandler = (envelope: CompletionEnvelope): void | Promise<void> => {
		const messageId = envelope.message_id;
		if (envelope.chat_id !== sessionId || !messageId) {
			return;
		}
		// chat:completion のみ適用する。chat:usage / tool:progress はグローバル store に
		// 書かず捨てる(全体決定)。confirmation / input(__event_call__)には応答しない —
		// 観測面から子ターンへ介入しない(サーバ側タイムアウトに委ねる)。
		if (envelope.data?.type !== 'chat:completion') {
			return;
		}
		const message = history.messages[messageId];
		if (!message) {
			refetchForUnknownMessage();
			return;
		}
		const { done } = applyCompletionData(message, envelope.data?.data ?? {});
		history.messages[messageId] = message;
		if (done) {
			return settle();
		}
	};

	$effect(() => {
		if (!expandable) {
			return;
		}
		let disposed = false;
		(async () => {
			const [fetched, taskRes] = await Promise.all([
				fetchChat(),
				getTaskIdsByChatId(sessionId).catch(() => null)
			]);
			if (disposed) {
				return;
			}
			if (!fetched) {
				status = 'error';
				return;
			}
			const taskIds: string[] = taskRes?.task_ids ?? [];
			if (taskIds.length > 0) {
				// 走行中(後続の run_subagent がこの session を継続している等)— live 購読。
				// 既にこのモジュールが消費中のストリーム・このブロックがアタッチ要求済み
				// (fetch 未解決含む)の task には重複アタッチしない(Chat.svelte の
				// 再アタッチと同じ二段ガード — 二重アタッチは delta の二重適用になる)
				running = true;
				registerChatHandler(sessionId, envelopeHandler);
				const activeTaskIds = getActiveTaskIds(sessionId);
				for (const taskId of taskIds) {
					if (!activeTaskIds.includes(taskId) && !attachedTaskIds.has(taskId)) {
						attachedTaskIds.add(taskId);
						attachToTask(sessionId, taskId).catch((error) => {
							console.error('subagent session attach failed', taskId, error);
						});
					}
				}
			} else {
				markSettled();
				// #109: マウント時点で既に完走済み(折りたたみ中に子が終わっていた等)。
				// spawn 即返し結果なら done 検知として親ターンの再開を要求する — 回収済みの
				// 古い履歴での再検知は Chat.svelte が isSubagentResumeHandled で drop する
				requestResumeIfSpawnRunning();
			}
			status = 'ready';
		})();
		return () => {
			disposed = true;
			// unmount 時は必ず解除(リーク禁止)。handler 参照一致で消すので、
			// 同一 session を指す別ブロックが後から登録した handler は消さない
			unregisterChatHandler(sessionId, envelopeHandler);
		};
	});

	const openSession = () => {
		goto(`/c/${sessionId}`);
	};

	// fork = clone(BE が kind=user へ落とす — 「介入したいときは fork」)。
	// ChatItem.svelte の clone 導線と同じ後処理(サイドバー一覧の追従)
	const forkSession = async () => {
		const res = await cloneChatById(
			sessionId,
			$i18n.t('Clone of {{TITLE}}', { TITLE: title || sessionId })
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (res) {
			goto(`/c/${res.id}`);
			currentChatPage.set(1);
			await chats.set(await getChatList($currentChatPage));
		}
	};

	// 停止: 消費中ストリームの task ∪ サーバが知る task(send.ts の stop と同じ合成)。
	// 停止後の畳み(settle)は SSE の done 到着に委ねる
	const stopSession = async () => {
		stopping = true;
		const taskRes = await getTaskIdsByChatId(sessionId).catch(() => null);
		const taskIds = [...new Set([...(taskRes?.task_ids ?? []), ...getActiveTaskIds(sessionId)])];
		for (const taskId of taskIds) {
			await stopTask(taskId).catch((error) => {
				toast.error(`${error}`);
				return null;
			});
		}
	};
</script>

<div {id} class="w-full border border-line rounded-lg">
	<!-- ヘッダ: preset 名 + 状態 + usage 要点 + アクション -->
	<div class="flex items-center gap-2 px-3 py-1.5 text-sm min-w-0">
		<span class="i-lucide-bot size-4 block shrink-0 text-content-muted"></span>
		<span class="font-medium truncate">
			{(expandable ? presetName : '') || $i18n.t('Agent session')}
		</span>

		{#if expandable}
			{#if status === 'loading'}
				<Spinner className="size-3.5" />
			{:else if running}
				<span class="flex items-center gap-1.5 text-xs text-content-muted shimmer shrink-0">
					<Spinner className="size-3.5" />
					{$i18n.t('Running...')}
				</span>
			{:else if display.error}
				<span class="i-lucide-circle-alert size-3.5 block shrink-0 text-red-600 dark:text-red-400"
				></span>
			{:else if display.ok}
				<span
					class="i-lucide-circle-check size-3.5 block shrink-0 text-green-500 dark:text-green-400"
				></span>
			{/if}
		{/if}

		{#if usageLabel}
			<span class="text-xs text-content-subtle tabular-nums truncate">{usageLabel}</span>
		{/if}

		<div class="grow"></div>

		{#if expandable && running}
			<button
				class="p-1 rounded-md text-content-muted hover:text-content hover:bg-gray-100 dark:hover:bg-gray-850 transition disabled:opacity-50"
				title={$i18n.t('Stop')}
				aria-label={$i18n.t('Stop')}
				disabled={stopping}
				onclick={stopSession}
			>
				<span class="i-lucide-circle-stop size-4 block"></span>
			</button>
		{/if}
		{#if expandable}
			<button
				class="p-1 rounded-md text-content-muted hover:text-content hover:bg-gray-100 dark:hover:bg-gray-850 transition"
				title={$i18n.t('Fork')}
				aria-label={$i18n.t('Fork')}
				onclick={forkSession}
			>
				<span class="i-lucide-git-branch size-4 block"></span>
			</button>
		{/if}
		<button
			class="p-1 rounded-md text-content-muted hover:text-content hover:bg-gray-100 dark:hover:bg-gray-850 transition"
			title={$i18n.t('Open')}
			aria-label={$i18n.t('Open')}
			onclick={openSession}
		>
			<span class="i-lucide-arrow-up-right size-4 block"></span>
		</button>
	</div>

	{#if expandable}
		{#if status === 'loading'}
			<div class="flex justify-center border-t border-line py-4">
				<Spinner className="size-4" />
			</div>
		{:else if status === 'error'}
			<div class="border-t border-line px-3 py-2 text-sm text-content-muted">
				{$i18n.t('Failed to load agent session')}
			</div>
		{:else}
			<div class="border-t border-line px-3 py-2 space-y-3">
				{#if display.error}
					<div class="text-sm text-red-600 dark:text-red-400">{display.error}</div>
				{/if}
				{#each messagesList as message (message.id)}
					{#if message.role === 'user'}
						<!-- 子への指示(instruction)。モデル間のやり取りなので素のテキストで -->
						<div class="flex gap-2 text-sm">
							<span
								class="i-lucide-corner-down-right size-3.5 block shrink-0 mt-0.5 text-content-muted"
							></span>
							<div class="whitespace-pre-wrap font-medium min-w-0" dir="auto">
								{message.content}
							</div>
						</div>
					{:else}
						<!-- 子 transcript は既存の content_blocks レンダラを再利用(読み取り専用) -->
						<ContentBlocks
							id={message.id}
							blocks={message.content_blocks ?? []}
							done={message.done ?? false}
							{history}
						/>
					{/if}
				{/each}
				{#if display.truncated}
					<div class="text-xs text-content-subtle italic">
						{$i18n.t('Answer truncated for the parent chat')}
					</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>
