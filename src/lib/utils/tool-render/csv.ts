// tool 結果の表(kind="table")の純ロジック。仕様: _memos/tool-arg-render.md「table の再導入」。
// 表になるのは tool が `Rendered(csv, "table", meta={...})` を返したときだけ —
// ここは中身から表かどうかを推測しない。本文は CSV(RFC 4180、1 行目が列名)で、
// descriptor に meta があるときだけ先頭に nucllei が組み立てた `# k=v …` の 1 行が付く
// (その行は飛ばし、meta そのものは descriptor から描く)。

// 描画する行の上限(モデルへの text は変えない — 表示だけ先頭で切る)
export const TABLE_MAX_ROWS = 1000;

// RFC 4180 の CSV を行 × セルに読む。区切りはカンマ、レコード区切りは CRLF / LF / CR。
// 引用符で囲んだフィールドはカンマ・改行を含めてよく、"" は " 1 個。
// 壊れた入力(閉じない引用符・引用符の後に続く文字・引用符で始まらないフィールド中の ")は
// null — 呼び手は text 表示に戻す。末尾の改行 1 つは空レコードにしない。
export const parseCsv = (text: string): string[][] | null => {
	const rows: string[][] = [];
	let row: string[] = [];
	let field = '';
	// 今のフィールドが引用符で始まったか / 引用符の内側か / 閉じ引用符の直後か
	let quoted = false;
	let inQuotes = false;
	let afterQuote = false;
	let i = 0;
	const n = text.length;

	const endField = () => {
		row.push(field);
		field = '';
		quoted = false;
		afterQuote = false;
	};
	const endRow = () => {
		endField();
		rows.push(row);
		row = [];
	};

	while (i < n) {
		const c = text[i];
		if (inQuotes) {
			if (c === '"') {
				if (text[i + 1] === '"') {
					field += '"';
					i += 2;
					continue;
				}
				inQuotes = false;
				afterQuote = true;
				i += 1;
				continue;
			}
			field += c;
			i += 1;
			continue;
		}
		if (c === ',') {
			endField();
			i += 1;
			continue;
		}
		if (c === '\r' || c === '\n') {
			endRow();
			i += c === '\r' && text[i + 1] === '\n' ? 2 : 1;
			continue;
		}
		// 閉じ引用符の後は区切りか改行しか来ない
		if (afterQuote) return null;
		if (c === '"') {
			// 引用符はフィールドの先頭でだけ開く
			if (field !== '' || quoted) return null;
			quoted = true;
			inQuotes = true;
			i += 1;
			continue;
		}
		field += c;
		i += 1;
	}
	if (inQuotes) return null;
	// 末尾の改行で終わっていれば最後のレコードは確定済み(空レコードを作らない)
	if (field !== '' || quoted || row.length > 0) {
		endRow();
	}
	return rows;
};

export type CsvTable = {
	columns: string[];
	// 描画する行(先頭 TABLE_MAX_ROWS 行まで)。各行は列数に揃えてある
	rows: string[][];
	// CSV のデータ行の総数(見出しを除く)。rows.length < totalRows なら切って出している
	totalRows: number;
};

// 本文を CSV の表として読む。skipMetaLine は descriptor に meta があるとき(本文の先頭に
// nucllei が組み立てた meta 行がある)— 先頭 1 行を飛ばす。中身の `#` は見ない。
// 表として出せないとき(CSV が壊れている・見出しが無い・meta 行しか無い)は null。
// 列数の揃わない行は許容する — 列数は全行の最大に合わせ、足りないセルは空、
// 見出しが足りない列は空の見出しにする。
export const parseCsvTable = (
	text: string,
	skipMetaLine = false,
	maxRows = TABLE_MAX_ROWS
): CsvTable | null => {
	let rest = text;
	if (skipMetaLine) {
		const nl = rest.indexOf('\n');
		if (nl === -1) return null;
		rest = rest.slice(nl + 1);
	}

	const records = parseCsv(rest);
	if (records === null || records.length === 0) return null;

	const [header, ...body] = records;
	const width = records.reduce((max, record) => Math.max(max, record.length), 0);
	const pad = (record: string[]) =>
		record.length < width ? [...record, ...Array(width - record.length).fill('')] : record;

	return {
		columns: pad(header),
		rows: body.slice(0, Math.max(0, maxRows)).map(pad),
		totalRows: body.length
	};
};

// descriptor の meta(スカラーだけ — BE が検証済み)を「キー: 値」のラベル用に文字列へ。
// bool は true / false、null は空、数値は String()。object 以外・非スカラーの値は捨てる
// (型に忠実 — 宣言外の形を推測して描かない)
export const formatTableMeta = (meta: unknown): [string, string][] => {
	if (meta === null || typeof meta !== 'object' || Array.isArray(meta)) return [];
	const entries: [string, string][] = [];
	for (const [key, value] of Object.entries(meta as Record<string, unknown>)) {
		if (value === null) entries.push([key, '']);
		else if (typeof value === 'boolean') entries.push([key, value ? 'true' : 'false']);
		else if (typeof value === 'number' || typeof value === 'string')
			entries.push([key, String(value)]);
	}
	return entries;
};
