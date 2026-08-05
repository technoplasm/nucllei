/**
 * 「すべて閉じる」= 表示中チャットの reasoning / tool ブロックを一括で畳む合図。
 *
 * seq を進めるだけの単方向シグナル。各ブロックの `open` はローカル state のままで、
 * seq が変わったときだけ追従する(ReasoningBlock / ToolCallBlock の $effect)ため、
 * 一括操作のあとの個別な手動開閉は殺さない。
 *
 * 状態をモジュールに置くのは、prop で 4 階層(Messages → Message →
 * ResponseMessage → ContentBlocks)引き回さないため。描画されるのは常に 1 チャット
 * ぶんのメッセージ列なので、モジュールスコープ = チャットスコープになる。
 * チャットを切り替えるとブロックは再マウントされ、その時点の seq を初期値として
 * 捉え直すので、古い合図が遅れて発火することもない。
 */
export const collapseAllSignal = $state({ seq: 0, open: false });

/** 表示中のブロックを一括で開く(true)/ 閉じる(false)。 */
export const requestCollapseAll = (open: boolean) => {
	collapseAllSignal.seq += 1;
	collapseAllSignal.open = open;
};
