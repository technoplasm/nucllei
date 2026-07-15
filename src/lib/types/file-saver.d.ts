// file-saver は @types が別パッケージ(air-gapped で増やさない)。使うのは saveAs だけなので最小宣言。
declare module 'file-saver' {
	const fileSaver: {
		saveAs: (blob: Blob, filename?: string) => void;
	};
	export default fileSaver;
}
