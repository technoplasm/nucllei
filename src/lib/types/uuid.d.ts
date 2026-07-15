// uuid@9 は型が @types/uuid 分離(air-gapped で依存を増やさない)。使うのは v4 だけなので最小宣言。
declare module 'uuid' {
	export function v4(): string;
}
