<script lang="ts">
	// llens(#110): システムが自動挿入した通知メッセージの表示。
	//
	// 非同期 tool(background)の完了を親ターンへ伝える定型メッセージは、モデルに
	// ターンを起こさせるため wire 上は user ロールで送る必要がある。しかし UI で
	// ユーザー発言の吹き出しとして出すと「勝手にチャットを送った」ように見えるため、
	// 明示的にシステム挿入と分かる体裁(バッジ + 抑えた配色 + 編集/削除なし)で描く。
	import { getContext } from 'svelte';
	import type { ChatHistory } from '$lib/types/chat';

	let { history, messageId }: { history: ChatHistory; messageId: string } = $props();

	const i18n = getContext('i18n');
	const message = $derived(history.messages[messageId]);
</script>

<div class="flex w-full" id="message-{message?.id}">
	<div class="w-full py-1">
		<div
			class="w-full rounded-lg border border-line bg-surface-muted px-3 py-2 text-sm text-content-muted"
		>
			<div class="mb-1 flex items-center gap-1.5 text-xs font-medium text-content-subtle">
				<span class="i-lucide-cpu size-3.5 block"></span>
				<span>{$i18n.t('Inserted by the system')}</span>
			</div>
			<div class="whitespace-pre-wrap break-words">{message?.content ?? ''}</div>
		</div>
	</div>
</div>
