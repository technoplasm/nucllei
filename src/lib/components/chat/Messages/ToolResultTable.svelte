<script lang="ts">
	// tool 結果の表(kind="table" — tool が Rendered(csv, "table", meta={...}) を返したとき)。
	// 仕様: _memos/tool-arg-render.md「table の再導入」。パース・行上限は tool-render/csv.ts。
	// meta(descriptor から)は表の上に「キー: 値」のラベルで並べる。表は枠付きで高さ上限まで、
	// 越えたら縦スクロール(見出し行は sticky で固定)、列が多ければ横スクロール。セルは
	// 折り返さず、長いセルは省略して全文は title で見せる。数値列の右寄せはしない(推測しない)。
	import { getContext } from 'svelte';

	import type { CsvTable } from '$lib/utils/tool-render';

	const i18n = getContext('i18n');

	let { table }: { table: CsvTable & { meta: [string, string][] } } = $props();
</script>

<!-- not-prose: 応答本文の prose(td / th の余白・行高)を表に効かせない -->
<div class="not-prose">
	{#if table.meta.length > 0}
		<div class="flex flex-wrap gap-1 my-0.5" dir="ltr">
			{#each table.meta as [key, value], k (k)}
				<span
					class="text-xs font-mono rounded-md bg-gray-100 dark:bg-gray-850 px-1.5 py-0.5 max-w-full truncate"
					title={`${key}: ${value}`}
					><span class="text-content-muted">{key}:</span>
					<span class="text-content">{value}</span></span
				>
			{/each}
		</div>
	{/if}

	<div class="max-h-96 overflow-auto rounded-md border border-line my-0.5" dir="ltr">
		<table class="w-max min-w-full text-xs font-mono text-left border-separate border-spacing-0">
			<thead>
				<tr>
					{#each table.columns as column, c (c)}
						<th
							scope="col"
							class="sticky top-0 z-[1] bg-surface-muted font-semibold text-content px-2 py-1 border-b border-line {c >
							0
								? 'border-l'
								: ''}"
						>
							<div class="max-w-xs truncate" title={column}>{column}</div>
						</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each table.rows as row, r (r)}
					<tr class="hover:bg-surface-hover">
						{#each row as cell, c (c)}
							<td
								class="px-2 py-0.5 text-content border-line {r < table.rows.length - 1
									? 'border-b'
									: ''} {c > 0 ? 'border-l' : ''}"
							>
								<div class="max-w-xs truncate" title={cell}>{cell}</div>
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if table.rows.length < table.totalRows}
		<div class="text-xs text-content-muted my-0.5 px-0.5">
			{$i18n.t('Showing first {{COUNT}} of {{TOTAL}} rows', {
				COUNT: table.rows.length,
				TOTAL: table.totalRows
			})}
		</div>
	{/if}
</div>
