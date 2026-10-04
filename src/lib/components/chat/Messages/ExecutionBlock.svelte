<script lang="ts">
	// background 昇格された tool 呼び出し(バックグラウンドタスク)の spawn 表示(#110)。
	// 親メッセージの tool result は spawn 時の running 形のまま不変(append-only)で、
	// 完了の真実は exec 行 — 現在の状態は executions store(5 秒ポーリング)から引く
	// 1 行ステータス。store に行が無い(未 fetch・他 chat の履歴)間は result の
	// running 形を信じてスピナーを出す。結果は BE の配達通知(SystemNoticeMessage)
	// または await_task の render_descriptor パススルーで描かれる。
	import { getContext } from 'svelte';

	import { getExecutionEntry } from '$lib/stores/executions.svelte';
	import { subscribeTick, tickNow } from '$lib/utils/tick.svelte';
	import { executionErrorKind, TOOL_ERROR_LOOKS, type ResultDisplay } from '$lib/utils/tool-render';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import ToolErrorLabel from './ToolErrorLabel.svelte';

	const i18n = getContext('i18n');

	type ExecutionDisplay = Extract<ResultDisplay, { kind: 'execution' }>;

	let {
		id = '',
		display
	}: {
		id?: string;
		display: ExecutionDisplay;
	} = $props();

	const entry = $derived(getExecutionEntry(display.execId));
	// store に行が無い間は running 扱い(result の running 形が最後に知られた真実)
	const status = $derived(entry?.status ?? 'running');
	const toolName = $derived(entry?.tool_name ?? display.toolName ?? '');
	// 終端のエラー文言は畳まずに見せる(tooltip だけだと気づけない)
	const errorText = $derived(entry?.error ?? '');
	// tool のエラー種別(サーバの旗 — 本文は解釈しない)。見た目は ToolCallBlock と同じ表
	// (TOOL_ERROR_LOOKS)。outcome は status done で届くが「該当なし」(amber)で出す
	const errorKind = $derived(executionErrorKind(status, entry?.error_kind));
	const errorLook = $derived(errorKind ? TOOL_ERROR_LOOKS[errorKind] : null);

	const statusLabel = $derived.by(() => {
		if (status === 'done') return $i18n.t('Completed');
		if (status === 'stopped') return $i18n.t('Stopped');
		if (status === 'orphaned') return $i18n.t('Lost on server restart');
		return $i18n.t('Running...');
	});

	// 実行中の経過時間(exec 行の created_at 起点・共有 1 秒クロック)。終端は duration
	const running = $derived(status === 'running');
	$effect(() => {
		if (!running || !entry) return;
		return subscribeTick();
	});
	const timeLabel = $derived.by(() => {
		if (running) {
			if (!entry) return '';
			const seconds = Math.max(0, Math.round(tickNow() / 1000 - entry.created_at));
			return $i18n.t('{{DURATION}} seconds', { DURATION: seconds });
		}
		if (entry?.duration != null) {
			return $i18n.t('{{DURATION}} seconds', { DURATION: Math.round(entry.duration) });
		}
		return '';
	});
</script>

<div {id} class="flex items-center gap-2 text-sm min-w-0 px-0.5 my-0.5" title={errorText}>
	{#if running}
		<Spinner className="size-3.5 shrink-0" />
	{:else if errorLook}
		<span class="{errorLook.icon} size-4 block shrink-0 {errorLook.iconClass}"></span>
	{:else if status === 'done'}
		<span class="i-lucide-circle-check size-4 block shrink-0 text-green-500 dark:text-green-400"
		></span>
	{:else if status === 'stopped'}
		<span class="i-lucide-circle-stop size-4 block shrink-0 text-content-muted"></span>
	{:else}
		<!-- orphaned(サーバ再起動で失われた)— バグではないので赤にしない -->
		<span class="i-lucide-circle-alert size-4 block shrink-0 text-amber-500 dark:text-amber-400"
		></span>
	{/if}

	{#if toolName}
		<span class="font-medium font-mono truncate">{toolName}</span>
	{/if}
	{#if errorKind}
		<ToolErrorLabel kind={errorKind} class="text-xs shrink-0" />
	{:else}
		<span class="text-xs text-content-muted shrink-0 {running ? 'shimmer' : ''}">{statusLabel}</span
		>
	{/if}
	{#if timeLabel}
		<span class="text-xs text-content-subtle tabular-nums shrink-0">{timeLabel}</span>
	{/if}
	<span class="text-xs text-content-subtle font-mono truncate" dir="ltr">{display.execId}</span>
</div>
{#if !running && errorText}
	<div class="text-xs text-content-muted whitespace-pre-wrap px-0.5 pb-0.5">{errorText}</div>
{/if}
