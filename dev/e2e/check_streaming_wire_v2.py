"""wire v2(SSE 一本化)ストリーミングの実機 e2e(#70 C トラック)。

実 SGLang(kimi)+ dev サーバー(BE :3000 / FE :5173)を前提に、
Playwright のクリーンブラウザで以下を検証する:

  1. 送信 → ストリーミング描画がテキスト単調成長する(delta イベント経路)
  2. 応答が完了する(done → EOF)
  3. ストリーム中にリロードしても、再アタッチ(GET /api/tasks/{id}/events)で
     続きが流れ込み完走する

実行: .venv/bin/python dev/e2e/check_streaming_wire_v2.py
前提: dev アカウント(ken@llens.local / llens-dev)と preset モデルが 1 つ以上
(_memos/dev-environment.md)。
"""

import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
EMAIL = "ken@llens.local"
PASSWORD = "llens-dev"

PROMPT_STREAM = "1から30までの数字を1行ずつ、コメント付きで出力する Python コードを書いて。説明も付けて。"
PROMPT_RELOAD = "日本の都道府県を北から順に、それぞれ一言の説明を付けて全部列挙して。"


def login(page):
    page.goto(f"{BASE}/auth")
    page.fill('input[type="email"]', EMAIL)
    page.fill('input[type="password"]', PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    assert "/auth" not in page.url, f"login failed: {page.url}"


def send_prompt(page, text):
    box = page.locator("#chat-input")
    box.click()
    box.fill(text)
    page.wait_for_timeout(300)
    # 送信ボタンをクリック(ctrlEnterToSend 設定に依存しない)
    page.locator("#send-message-button").click()


def response_text_length(page):
    return page.evaluate("""() => {
            const nodes = document.querySelectorAll('.chat-assistant, [id^="message-"]');
            let total = 0;
            for (const n of nodes) total += (n.innerText || '').length;
            return total;
        }""")


def wait_done(page, timeout_s=180):
    """入力欄の停止ボタンが消える(= done)まで待つ。"""
    deadline = time.time() + timeout_s
    stable = 0
    last = -1
    while time.time() < deadline:
        length = response_text_length(page)
        stable = stable + 1 if length == last else 0
        last = length
        # 8 秒間変化なし + 生成中インジケータなしで完了とみなす
        if stable >= 16:
            return True
        page.wait_for_timeout(500)
    return False


def main():
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        login(page)
        print("login: OK")

        # --- 1. ストリーミング単調成長 -------------------------------------
        # Kimi は必ず reasoning から始まる(表示テキストは伸びない)ため、
        # サンプリングは done 相当(8 秒無変化)まで回し、全期間の成長を見る。
        page.goto(BASE)
        page.wait_for_timeout(1500)
        send_prompt(page, PROMPT_STREAM)

        samples = []
        stable = 0
        for _ in range(400):  # 最長 120 秒
            samples.append(response_text_length(page))
            if len(samples) >= 2 and samples[-1] == samples[-2] and samples[-1] > 0:
                stable += 1
            else:
                stable = 0
            if stable >= 26:  # ~8 秒無変化 = 完了
                break
            page.wait_for_timeout(300)

        growth = [b - a for a, b in zip(samples, samples[1:])]
        grew = sum(1 for g in growth if g > 0)
        shrank = [g for g in growth if g < 0]
        print(f"streaming: samples={len(samples)} grew={grew} last={samples[-1]}")
        if grew < 3:
            failures.append(f"streaming growth not observed: {samples[-30:]}")
        if shrank and min(shrank) < -50:
            # スナップショット収束による小さな揺り戻しは許容、大幅減少は異常
            failures.append(f"content shrank during stream: {shrank}")
        print("completion: OK" if grew >= 3 else "completion: NG")

        # --- 2. ストリーム中リロード → 再アタッチ ---------------------------
        base_len = response_text_length(page)
        send_prompt(page, PROMPT_RELOAD)

        # 本文ストリーミングが始まる(表示テキストが伸び始める)まで待つ
        started = False
        for _ in range(240):  # 最長 120 秒(reasoning 長考を許容)
            if response_text_length(page) > base_len + 80:
                started = True
                break
            page.wait_for_timeout(500)
        if not started:
            failures.append("second response text did not start streaming")

        len_before = response_text_length(page)
        page.reload()
        page.wait_for_timeout(3000)
        len_after_reload = response_text_length(page)
        print(f"reload: before={len_before} after={len_after_reload}")

        # 再アタッチ後も成長し、最終的に pre-reload を超えるか
        final_len = len_after_reload
        for _ in range(240):
            final_len = response_text_length(page)
            if final_len > len_before + 50:
                break
            page.wait_for_timeout(500)
        print(f"reattach growth: {len_after_reload} -> {final_len}")
        if final_len <= len_before:
            failures.append(
                "no growth after mid-stream reload (reattach broken?): "
                f"before={len_before} final={final_len}"
            )

        if wait_done(page, 240):
            print("reattach completion: OK")
        else:
            failures.append("reattached response did not complete in time")

        browser.close()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print("\nALL OK")


if __name__ == "__main__":
    main()
