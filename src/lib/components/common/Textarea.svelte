<script lang="ts">
	import { onMount, tick } from 'svelte';

	export let value = '';
	export let placeholder = '';
	export let rows = 1;
	export let required = false;
	// label の for と紐づけるための id(未指定なら属性ごと出ない)
	export let id: string | undefined = undefined;
	export let className =
		'w-full rounded-lg px-3 py-2 text-sm text-content bg-gray-50 dark:bg-gray-850 outline-hidden  h-full';

	let textareaElement: HTMLTextAreaElement | undefined;

	// Adjust height on mount and after setting the element.
	onMount(async () => {
		await tick();
		resize();

		requestAnimationFrame(() => {
			// setInterveal to cehck until textareaElement is set
			const interval = setInterval(() => {
				if (textareaElement) {
					clearInterval(interval);
					resize();
				}
			}, 100);
		});
	});

	const resize = () => {
		if (textareaElement) {
			textareaElement.style.height = '';
			textareaElement.style.height = `${textareaElement.scrollHeight}px`;
		}
	};
</script>

<textarea
	bind:this={textareaElement}
	bind:value
	{id}
	{placeholder}
	class={className}
	style="field-sizing: content;"
	{rows}
	{required}
	on:input={() => {
		resize();
	}}
	on:focus={() => {
		resize();
	}}
></textarea>
