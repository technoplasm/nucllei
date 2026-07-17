<script lang="ts">
	import DOMPurify from 'dompurify';

	import { onDestroy, type Snippet } from 'svelte';

	import tippy, { type Instance, type Placement } from 'tippy.js';

	let {
		placement = 'top',
		content = '',
		touch = true,
		className = 'flex',
		theme = '',
		offset = [0, 4],
		allowHTML = true,
		tippyOptions = {},
		children
	}: {
		placement?: Placement;
		content?: string;
		touch?: boolean;
		className?: string;
		theme?: string;
		offset?: [number, number];
		allowHTML?: boolean;
		tippyOptions?: Record<string, unknown>;
		children?: Snippet;
	} = $props();

	let tooltipElement: HTMLDivElement | undefined = $state();
	let tooltipInstance: Instance | null = null;

	$effect(() => {
		if (tooltipElement && content) {
			if (tooltipInstance) {
				tooltipInstance.setContent(DOMPurify.sanitize(content));
			} else {
				tooltipInstance = tippy(tooltipElement, {
					content: DOMPurify.sanitize(content),
					placement: placement,
					allowHTML: allowHTML,
					touch: touch,
					...(theme !== '' ? { theme } : { theme: 'dark' }),
					arrow: false,
					offset: offset,
					...tippyOptions
				});
			}
		} else if (tooltipInstance && content === '') {
			tooltipInstance.destroy();
			tooltipInstance = null;
		}
	});

	onDestroy(() => {
		if (tooltipInstance) {
			tooltipInstance.destroy();
		}
	});
</script>

<div bind:this={tooltipElement} aria-label={DOMPurify.sanitize(content)} class={className}>
	{@render children?.()}
</div>
