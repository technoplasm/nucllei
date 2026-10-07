"""Valves 更新の共通処理(tools / functions の `valves/update`)。"""

from typing import Optional


def merge_valves_update(stored: Optional[dict], form_data: dict, Valves) -> dict:
    """保存済みの valve に更新を重ね、保存する dict(明示されたフィールドだけ)を返す。

    - 送られなかったキーは保存値を保つ(部分更新)。以前は送ったキーだけで丸ごと
      置き換えていたため、hint だけを送ると他の valve(url_max_mb 等)が既定に戻った
    - null = UI の「Default」— そのキーの保存値を消してコードの既定値に戻す
    - 保存は明示したフィールドだけ(exclude_unset): 未設定は読み出し側の
      Valves(**保存値) でコードの既定値に埋まるので、コード側の既定変更
      (filter の priority 等)が効き続ける
    - 今の Valves に無いキー(撤去された valve の残骸)は落とす

    管理画面は全フィールドを送る(未設定は null)ので、従来どおり全体の置き換えになる。
    検証に失敗すれば Valves(...) の例外がそのまま上がる。
    """
    merged = {k: v for k, v in (stored or {}).items() if k in Valves.model_fields}
    for k, v in form_data.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    return Valves(**merged).model_dump(exclude_unset=True)
