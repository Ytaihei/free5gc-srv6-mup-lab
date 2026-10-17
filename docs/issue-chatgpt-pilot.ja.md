# ChatGPT契約枠の接続試験

[English](issue-chatgpt-pilot.md) | 日本語

Issueレビューの契約枠接続を検証するため、公式OSS向けSign in with ChatGPTを
手動で試します。**コーディネーターには未接続**です。Issue、Qwen作業、コードレビュー、
PR、マージ、タイマー、ラボ操作は開始しません。コーディネーターは無効のままとし、
コーディネーターもPlatform APIキーを受け付けなくなっています。

## 前提と支出の制約

Python 3.10以上と、導入済みのPyJWT・cryptographyを使います。通常の開発依存ではなく
試験専用の任意依存です。不足時はログインを拒否し、自動インストールはしません。
対象となるChatGPTアカウントの本人が認証します。既存Codex認証のコピー、トークンの
共有、root実行は避けてください。

ログインだけでは推論しません。`smoke`の前にChatGPTの設定→Usageでこのアプリを確認し、
アプリのクレジット消費とクレジットの自動購入が無効であることを確認してください。
**その条件を確立できない場合、確認フラグを指定せず停止してください**。
フラグは運用者の確認表明であり、API側の支出上限ではありません。CLIから設定を
取得・変更することはできません。設定によってクレジットを消費するため、OAuth認証
だけで月額に含まれる枠のみの利用を保証することはできません。

Platform APIキーの読み込み、購入、モデル切り替え、推論の自動再試行はありません。
失敗した要求も試験の1回として記録します。クライアント側の時間・サイズ制限は、
サーバー側の生成トークン数や費用の厳密な上限を保証しません。

## ログインと試験

Linuxホストの信頼済みチェックアウトで実行します。sudoは不要です。

```bash
python3 scripts/issue_chatgpt_pilot.py \
  --state "$HOME/.local/state/srv6-mup-chatgpt-pilot" login
```

待受は`127.0.0.1:1465`のみで、最大10分です。表示された公式URLを開き、意図した
アカウント・アプリだけを承認します。別PCのブラウザを使う場合、先にそのPCから
SSHトンネルを開きます。接続先は実際のホストに置き換えてください。

```bash
ssh -N -L 1465:127.0.0.1:1465 operator@linux-host
```

ログイン端末に検証完了が表示されるまで両コマンドを維持します。Tailscale Serveや
全インターフェース待受でコールバックを公開しないでください。コールバックURLや
私的状態は共有しないでください。ディレクトリは`0700`、ファイルは`0600`です。
PKCE、state、RS256署名、発行者、対象クライアント、有効期限、nonce、契約枠の許可を
検証します。発行されたクライアントIDと固定ホストIDは次のログインにも使います。

```bash
python3 scripts/issue_chatgpt_pilot.py \
  --state "$HOME/.local/state/srv6-mup-chatgpt-pilot" models
```

このアカウントの一覧にある正確な`slug`を選びます。前述の支出条件を確認できた場合に
限り、そのslugで以下を実行します。

```bash
python3 scripts/issue_chatgpt_pilot.py \
  --state "$HOME/.local/state/srv6-mup-chatgpt-pilot" smoke \
  --model ACCOUNT_MODEL_SLUG --confirm-included-only
```

送るのは`MUP_PILOT_OK`と答える固定文だけで、ソースやツールは渡しません。
正常な`response.completed`と文字列一致を要求します。途中の応答、上限・認証エラー、
不正な応答は失敗です。配列入力、`store:false`、`stream:true`を使い、未対応の
`max_output_tokens`は送りません。送信前に記録を作り、失敗時も残します。
検査せず記録を消して強制的に再試行しないでください。
完了した応答は、不一致の場合も私的な`smoke-response.json`に保存します。
保存するのは最終・ストリーミング応答文、完了・一致状態、時刻だけで、認証情報や
HTTPヘッダーは含めません。旧版で失われた応答は復元できません。
ストリーミング本文は正常な完了イベントを受けた場合のみ採用し、最終本文も存在する
場合は両者の一致を必要とします。不一致は引き続き失敗です。

失敗記録を検査し、1回の再試験を明示的に承認した場合、同じフラグ・モデルで
`smoke`の代わりに`retry-smoke`を使います。初回記録は保存したまま、別の
`smoke-retry.json`と`smoke-retry-response.json`に記録します。再試験は1回だけです。
初回成功、モデル変更、再試験記録の存在時は拒否します。自動再試行は有効化しません。

```bash
python3 scripts/issue_chatgpt_pilot.py \
  --state "$HOME/.local/state/srv6-mup-chatgpt-pilot" status
python3 -m unittest discover -s tests -p test_issue_chatgpt_pilot.py -v
```

statusはファイルの有無であり、認証の有効性ではありません。オフラインテストは
合成トークン・模擬応答を使い、実アカウントには接続しません。暗号検証テストは
任意のログイン依存が不足すると明示的にスキップします。状態ディレクトリごとに
1アカウントを扱います。自動更新、アカウント切り替え、ログアウトCLIは未実装です。
期限切れ時は保存済み登録でログインし直し、終了時はChatGPT設定で接続を解除します。

## 残る統合

[Issueレビュー処理](issue-development.ja.md)には、この専用OAuthを使う排他制御付き
トークン更新、コミットSHAに紐づく構造化レビュー、上限時の待機・復旧、1日の回数制限、
回帰テストを実装しています。smoke用CLI自体は引き続きトークンを自動更新しません。
`review-local`を検証し、その後、承認済みIssueをQwen→隔離検査→レビュー→PR/CIまで通してから自動マージを
検討します。smoke成功はこれらの完了を意味しません。旧タイマーは無効のままです。

公式資料:

- [登録とログイン](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [アカウントと利用設定](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [モデルと推論](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [プレビューの制約](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
