<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import type { Snippet } from 'svelte';

	let {
		show = $bindable(false),
		onChange = undefined,
		children,
		content
	}: {
		show?: boolean;
		onChange?: (state: boolean) => void;
		children?: Snippet;
		content?: Snippet;
	} = $props();
</script>

<DropdownMenu.Root
	bind:open={show}
	closeFocus={false}
	onOpenChange={(state) => {
		onChange?.(state);
	}}
	typeahead={false}
>
	<DropdownMenu.Trigger>
		{@render children?.()}
	</DropdownMenu.Trigger>

	{@render content?.()}
</DropdownMenu.Root>
