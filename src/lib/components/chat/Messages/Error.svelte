<script lang="ts">
	let { content = '' }: { content?: unknown } = $props();
</script>

<div class="flex my-2 gap-2.5 border px-4 py-3 border-red-600/10 bg-red-600/10 rounded-lg">
	<div class=" self-start mt-0.5">
		<span class="i-lucide-info size-5 block text-red-700 dark:text-red-400"></span>
	</div>

	<div class=" self-center text-sm">
		{typeof content === 'string' ? content : JSON.stringify(content)}
	</div>
</div>
