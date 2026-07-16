// file-saver は @types が別パッケージ(依存を増やさない方針で導入しない)。使うのは saveAs だけなので最小宣言。
declare module 'file-saver' {
	const fileSaver: {
		saveAs: (blob: Blob, filename?: string) => void;
	};
	export default fileSaver;
}
