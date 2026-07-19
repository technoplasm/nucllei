<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import type { Snippet } from 'svelte';

	// bits-ui 2.x は Content の transition prop が廃止(forceMount + child snippet が公式パターン)、
	// かつ Portal が明示になったため、その定型をここに一本化する(refs #83)。
	// メニューは 0.19 の closeFocus={false} 相当(閉じてもトリガへフォーカスを戻さない)が既定。
	// UserMenu のようにフォーカス復帰させたい場合だけ restoreFocus を立てる。
	let {
		className = '',
		side = 'bottom',
		align = 'start',
		sideOffset = 0,
		alignOffset = 0,
		sub = false,
		restoreFocus = false,
		children
	}: {
		className?: string;
		side?: 'top' | 'bottom' | 'left' | 'right';
		align?: 'start' | 'center' | 'end';
		sideOffset?: number;
		alignOffset?: number;
		/** DropdownMenu.Sub 配下のサブメニューとして描画する */
		sub?: boolean;
		restoreFocus?: boolean;
		children?: Snippet;
	} = $props();
</script>

{#if sub}
	<DropdownMenu.SubContent class={className} {sideOffset} forceMount>
		{#snippet child({ wrapperProps, props, open })}
			{#if open}
				<div {...wrapperProps}>
					<div {...props} transition:flyAndScale>
						{@render children?.()}
					</div>
				</div>
			{/if}
		{/snippet}
	</DropdownMenu.SubContent>
{:else}
	<DropdownMenu.Portal>
		<DropdownMenu.Content
			class={className}
			{side}
			{align}
			{sideOffset}
			{alignOffset}
			forceMount
			onCloseAutoFocus={(e) => {
				if (!restoreFocus) {
					e.preventDefault();
				}
			}}
		>
			{#snippet child({ wrapperProps, props, open })}
				{#if open}
					<div {...wrapperProps}>
						<div {...props} transition:flyAndScale>
							{@render children?.()}
						</div>
					</div>
				{/if}
			{/snippet}
		</DropdownMenu.Content>
	</DropdownMenu.Portal>
{/if}
