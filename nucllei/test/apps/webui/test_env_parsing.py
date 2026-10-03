"""env の読み取り(import 時)の回帰テスト — DB 不要の単体。

- `env_int`: 整数 env の安全な読み取り。配備の設定ファイルの打ち間違い 1 つで起動が
  落ちないこと(非数は warning + 既定、未設定 / 空は既定、下限未満は既定)。
- `WEBUI_SECRET_KEY`: 固定鍵のフォールバックは **ENV=dev 限定**(ken 決定 2026-10-03)。
  dev 以外で鍵が無ければ import 時に止まる(既知の固定鍵でセッションを偽造されないため)。

nucllei.env は import 済みのモジュール状態を持つので、import 時の分岐は
サブプロセスで nucllei.env だけを import して検証する(DB には触らない)。
"""

import os
import subprocess
import sys

import pytest

from nucllei.env import env_int

# ---- env_int -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,default,minimum,expected",
    [
        (None, 25, 0, 25),  # 未設定 → 既定
        ("", 25, 0, 25),  # 空 → 既定
        ("  ", None, 0, None),  # 空白のみ → 既定(None も既定として通る)
        ("40", 25, 0, 40),
        (" 7 ", 1, 1, 7),  # 前後の空白は許す
        ("abc", 25, 0, 25),  # 非数 → 既定(落ちない)
        ("1.5", 25, 0, 25),  # 小数は整数ではない → 既定
        ("-1", 389, 1, 389),  # 下限未満 → 既定
        ("0", 1, 1, 1),  # 下限未満 → 既定
        ("0", 0, 0, 0),  # 下限ちょうどは通る
        ("-5", 0, None, -5),  # 下限無しなら負も通る
    ],
)
def test_env_int(monkeypatch, raw, default, minimum, expected):
    if raw is None:
        monkeypatch.delenv("NUCLLEI_TEST_INT", raising=False)
    else:
        monkeypatch.setenv("NUCLLEI_TEST_INT", raw)
    assert env_int("NUCLLEI_TEST_INT", default, minimum=minimum) == expected


def test_env_int_warns_on_garbage(monkeypatch, caplog):
    monkeypatch.setenv("NUCLLEI_TEST_INT", "twenty")
    with caplog.at_level("WARNING", logger="nucllei.env"):
        assert env_int("NUCLLEI_TEST_INT", 20) == 20
    assert "NUCLLEI_TEST_INT" in caplog.text


# ---- WEBUI_SECRET_KEY --------------------------------------------


def _import_env(**env_overrides):
    """サブプロセスで `nucllei.env` を import し、(returncode, stderr) を返す。"""
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import nucllei.env; print(nucllei.env.WEBUI_SECRET_KEY)",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_secret_key_fixed_fallback_only_in_dev():
    result = _import_env(ENV="dev", WEBUI_SECRET_KEY="")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "t0p-s3cr3t"
    # 固定鍵で動いていることは気付けなければならない
    assert "WEBUI_SECRET_KEY is not set" in result.stderr + result.stdout


def test_secret_key_required_outside_dev():
    result = _import_env(ENV="prod", WEBUI_SECRET_KEY="")
    assert result.returncode != 0
    assert "WEBUI_SECRET_KEY is not set and ENV is not 'dev'" in result.stderr
    assert "nucllei serve" in result.stderr  # 直し方を示す


def test_secret_key_from_env_outside_dev():
    result = _import_env(ENV="prod", WEBUI_SECRET_KEY="deployment-key")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "deployment-key"


def test_serve_style_key_file_satisfies_prod(tmp_path):
    """`nucllei serve` / `create-admin` の経路: env 未設定でも `$DATA_DIR/.webui_secret_key`
    を読んで env に入れてから import するので、ENV=prod でも止まらない。"""
    (tmp_path / ".webui_secret_key").write_text("persisted-key")
    env = {
        **os.environ,
        "ENV": "prod",
        "WEBUI_SECRET_KEY": "",
        "DATA_DIR": str(tmp_path),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import nucllei; nucllei._load_secret_key(); "
            "import nucllei.env; print(nucllei.env.WEBUI_SECRET_KEY)",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "persisted-key"
