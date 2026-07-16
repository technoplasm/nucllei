<script lang="ts">
	import { onDestroy, onMount, type Snippet } from 'svelte';

	let {
		onVisible = undefined,
		children
	}: {
		onVisible?: () => void;
		children?: Snippet;
	} = $props();

	let loaderElement: HTMLElement | null = $state(null);

	let observer: IntersectionObserver;
	let intervalId: ReturnType<typeof setInterval>;

	onMount(() => {
		observer = new IntersectionObserver(
			(entries, observer) => {
				entries.forEach((entry) => {
					if (entry.isIntersecting) {
						intervalId = setInterval(() => {
							onVisible?.();
						}, 100);
					} else {
						clearInterval(intervalId);
					}
				});
			},
			{
				root: null, // viewport
				rootMargin: '0px',
				threshold: 0.1 // When 10% of the loader is visible
			}
		);

		if (loaderElement) {
			observer.observe(loaderElement);
		}
	});

	onDestroy(() => {
		observer.disconnect();

		if (intervalId) {
			clearInterval(intervalId);
		}
	});
</script>

<div bind:this={loaderElement}>
	{@render children?.()}
</div>
