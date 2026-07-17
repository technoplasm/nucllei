<script lang="ts">
	import { Pagination } from 'bits-ui';

	let {
		page = $bindable(0),
		count = 0,
		perPage = 20
	}: {
		page?: number;
		count?: number;
		perPage?: number;
	} = $props();
</script>

<div class="flex justify-center">
	<Pagination.Root bind:page {count} {perPage} let:pages>
		<div class="my-2 flex items-center">
			<Pagination.PrevButton
				class="mr-[25px] inline-flex size-8 items-center justify-center rounded-[9px] bg-transparent hover:bg-gray-50 dark:hover:bg-gray-850 active:scale-98 disabled:cursor-not-allowed disabled:text-content-subtle hover:disabled:bg-transparent dark:hover:disabled:bg-transparent"
			>
				<span class="i-lucide-chevron-left size-4 block"></span>
			</Pagination.PrevButton>
			<div class="flex items-center gap-2.5">
				{#each pages as page (page.key)}
					{#if page.type === 'ellipsis'}
						<div class="text-sm font-medium text-foreground-alt">...</div>
					{:else}
						<Pagination.Page
							{page}
							class="inline-flex size-8 items-center justify-center rounded-[9px] bg-transparent hover:bg-gray-50 dark:hover:bg-gray-850 text-sm font-medium hover:bg-dark-10 active:scale-98 disabled:cursor-not-allowed disabled:opacity-50 hover:disabled:bg-transparent data-selected:bg-gray-50 data-selected:text-content data-selected:hover:bg-gray-100 dark:data-selected:bg-gray-850 dark:data-selected:hover:bg-gray-800 transition"
						>
							{page.value}
						</Pagination.Page>
					{/if}
				{/each}
			</div>
			<Pagination.NextButton
				class="ml-[25px]  inline-flex size-8 items-center justify-center rounded-[9px] bg-transparent hover:bg-gray-50 dark:hover:bg-gray-850 active:scale-98 disabled:cursor-not-allowed disabled:text-content-subtle hover:disabled:bg-transparent dark:hover:disabled:bg-transparent"
			>
				<span class="i-lucide-chevron-right size-4 block"></span>
			</Pagination.NextButton>
		</div>
	</Pagination.Root>
</div>
