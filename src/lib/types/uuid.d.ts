// uuid@9 は型が @types/uuid 分離(依存を増やさない方針で導入しない)。使うのは v4 だけなので最小宣言。
declare module 'uuid' {
	export function v4(): string;
}
