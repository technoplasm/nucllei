import DOMPurify from 'dompurify';

/**
 * mermaid が描いた SVG のサニタイズ(GHSA-r29h-37fj-x2w6 / GHSA-v8qj-hxv7-mgvv の多層防御)。
 *
 * mermaid のノード・辺のラベルは `<foreignObject>` の中の HTML(div / span / p / br)で描かれる。
 * DOMPurify 既定は `<foreignObject>` を許さず、SVG の中の HTML 要素も落とすのでラベルが消える。
 * そこで `<foreignObject>` だけを足し、その中を HTML の統合点として扱う(mermaid が
 * securityLevel 'strict' で自分に掛けるのと同じ設定)。foreignObject の中の HTML には
 * チャット本文の HTML と同じ既定 allowlist が効く — script・イベントハンドラ(on*)・
 * javascript: URL は除去される。
 */
export const DIAGRAM_SVG_SANITIZE_CONFIG = {
	ADD_TAGS: ['foreignobject'],
	ADD_ATTR: ['dominant-baseline'],
	HTML_INTEGRATION_POINTS: { foreignobject: true }
};

/** mermaid の SVG を `{@html ...}` に渡せる文字列にする。 */
export const sanitizeDiagramSvg = (svg: string): string =>
	DOMPurify.sanitize(svg ?? '', DIAGRAM_SVG_SANITIZE_CONFIG);
