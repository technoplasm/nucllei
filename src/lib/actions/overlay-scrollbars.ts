import { OverlayScrollbars } from 'overlayscrollbars';
import type { PartialOptions } from 'overlayscrollbars';

// llens(#34): スクロールホストに OverlayScrollbars を張る Svelte action。
// childViewport を立てると「最初の子要素」を viewport として渡す —
// 既存のスクロール要素(bind:this / onscroll / id 参照)を DOM 再構成なしで
// そのまま生かすための v2 の初期化形。scroll ロジック側は無改修で済む。
// 見た目は app.css の .os-theme-app(--scrollbar-thumb トークン参照)。
export function overlayScrollbar(
	node: HTMLElement,
	params: { childViewport?: boolean; options?: PartialOptions } = {}
) {
	const viewport = params.childViewport ? (node.firstElementChild as HTMLElement | null) : null;

	const instance = OverlayScrollbars(viewport ? { target: node, elements: { viewport } } : node, {
		scrollbars: {
			theme: 'os-theme-app',
			// サイドバーの旧挙動の全域化(2026-07-20 ユーザー指定):
			// 領域に乗っている間だけ表示し、離れたら退場
			autoHide: 'leave',
			autoHideDelay: 300
		},
		...(params.options ?? {})
	});

	return {
		destroy() {
			instance.destroy();
		}
	};
}
