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

<!-- bits-ui 2.x: closeFocus 相当は DropdownContent(onCloseAutoFocus)へ移管。typeahead prop は廃止 -->

<DropdownMenu.Root
	bind:open={show}
	onOpenChange={(state) => {
		onChange?.(state);
	}}
>
	<DropdownMenu.Trigger>
		{@render children?.()}
	</DropdownMenu.Trigger>

	{@render content?.()}
</DropdownMenu.Root>
