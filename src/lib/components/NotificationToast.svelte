<script lang="ts">
	import { settings, playingNotificationSound, isLastActiveTab } from '$lib/stores';
	import DOMPurify from 'dompurify';

	import { render } from '$lib/utils/markdown-it';
	import { onMount } from 'svelte';

	let {
		onClick = () => {},
		title = '',
		content
	}: {
		onClick?: () => void;
		title?: string;
		content: string;
	} = $props();

	onMount(() => {
		if (!navigator.userActivation.hasBeenActive) {
			return;
		}

		if ($settings?.notificationSound ?? true) {
			if (!$playingNotificationSound && $isLastActiveTab) {
				playingNotificationSound.set(true);

				const audio = new Audio(`/audio/notification.mp3`);
				audio.play().finally(() => {
					// Ensure the global state is reset after the sound finishes
					playingNotificationSound.set(false);
				});
			}
		}
	});
</script>

<button
	class="flex gap-2.5 text-left min-w-[var(--width)] w-full dark:bg-gray-850 dark:text-white bg-white text-black border border-gray-100 dark:border-gray-850 rounded-xl px-3.5 py-3.5"
	onclick={() => {
		onClick();
	}}
>
	<div class="shrink-0 self-top -translate-y-0.5">
		<img src={'/static/favicon.png'} alt="favicon" class="size-7 rounded-full" />
	</div>

	<div>
		{#if title}
			<div class=" text-[13px] font-medium mb-0.5 line-clamp-1 capitalize">{title}</div>
		{/if}

		<div class=" line-clamp-2 text-xs self-center text-content font-normal">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- #17 XSS sink 監査済み(サニタイズは各経路) -->
			{@html DOMPurify.sanitize(render(content))}
		</div>
	</div>
</button>
