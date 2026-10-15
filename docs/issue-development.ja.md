# Issue起点のローカル開発

[English](issue-development.md) | 日本語

## 状態と権限

これは旧バックグラウンドスケジューラーとは独立した、明示的に有効化する実装です。
サービスのインストール・有効化、旧タイマーの再開、認証情報のコピー、実ラボ操作、
イメージ公開は行いません。初期状態は無効かつ停止です。
オフラインテスト成功は実際のIssue・PR・CI・マージの通し試験を意味しません。

Codexに目的・許可パス・受け入れ条件を含むIssue作成を依頼します。
課題として保存するだけでは実行を許可しません。明示的に開発開始を依頼した場合に
実行用ラベルを付けられます。Issue作成者と最新の実行ラベル付与者の両方が、
運用者の信頼ユーザー一覧に含まれる必要があります。公開Issueの投稿者、コメント、
モデル出力は実行権限を付与できません。

処理順は、承認されたIssue → ローカルQwen → 隔離検査 → 別レビュー → PR → 必須CI
→ 条件付きマージです。マシンの起動が必要で、停止中の仕事は次回の取得時に処理できます。
ただし中断した開発は明示的な確認・再試行が必要です。Codex標準サブエージェントや
アプリの自動化機能そのものではありません。

## タスクの形式

Issue本文には次の形式のブロックを1つだけ含めます。Codexが代わりに記入できます。

```agent-task
{
  "goal": "Add a focused regression test for an existing input-validation rule.",
  "paths": ["tests/"],
  "acceptance": "Preserve all existing assertions and production behavior; run the unit checks."
}
```

運用者は承認したタスクにだけ `agent:ready` を付与します。ラベル解除、Issueの終了、
タイトル・本文の変更は、公開・マージ前に保留中の仕事を無効化します。
ラベルを解除して再付与すると新しい世代のタスクになります。自動再試行ではありません。
コメントによる作業範囲の拡張はできません。同時に承認する世代は1つにし、古い世代は
次の取得時に取り消します。

ワーカーが編集できるのは指定範囲内の `cmd/`、`internal/`、`tests/`、`docs/` と、
明示的に許可されたREADME・Go依存ファイルです。実行基盤、CI、指示、Makefile、認証情報、
ホスト設定は編集できません。公開一覧に追加できる新規パスは単体テストのみです。
大きな機能追加には、この範囲の別途レビューが必要です。初版は意図的に範囲を限定しています。

## ワーカーと検査

アダプターは既存のローカルOllamaに `qwen3.5:9b-16k` を指定し、`read_file`、
`apply_patch`（1か所だけ一致する文字列の置換）、`run_tests` の3ツールだけを提供します。
モデルにはシェル、GitHubトークン、任意コマンド実行を渡しません。
先の実現性試験で使ったCodex CLI全体の起動ではなく、小さなツール実行ループです。

推論だけで出力上限を使い切って空の完了応答になることを避けるため、アダプターはthinking出力を
無効にします。応答のトークン数・終了理由は私的ログに記録します。
既定値は16K文脈、要求ごとの生成上限4,096トークン、24ターン、600秒です。
入力長、ファイルサイズ、変更量、1日の仕事数、保留中候補数も制限します。
モデルの代替選択や自動ダウンロードは行いません。モデルの成功宣言は合格判定ではありません。

テストはbubblewrap内で、候補の一時コピーと読み取り専用のツールチェーン・キャッシュを使い、
ネットワーク・認証情報なしで実行します。ホストのホーム、Docker/libvirtソケット、ラボ状態は
渡しません。隔離の失敗時に非隔離実行へ切り替えることもありません。
ワーカーの単体テストコマンドは固定です。独立した最終検査では `make check`、`make lint`、
Ansible構文検査、Gitleaksを実行します。依存物と検査ツールは固定済みキャッシュに存在する必要があり、
足りない場合に通信を許可するのではなく失敗させます。公開一覧と翻訳ハッシュは親が機械的に
再生成しますが、それ自体は翻訳内容のレビューではありません。

アダプター自体は運用者ユーザーで実行します。ファイル・ネットワーク隔離の対象は候補コードの
実行であり、そのユーザーの全プロセスではありません。同一アカウントの別の信頼された処理は
設定・状態を変更できます。複数の敵対的利用者向けコード実行サービスとしては使わないでください。
付属ユーザーサービスはメモリ・CPU・プロセス数を制限します。手動試験には時間・プロセス単位の
制限がありますが、その完全なcgroup単位の資源制限はありません。

## 有効化せずに設定する

前提はLinux、リポジトリの開発依存を備えたPython、Git、非特権名前空間が使えるbubblewrap、
固定Go、オフラインのGoモジュール・検査ツールキャッシュ、既存のOllamaとモデルです。
前提が整えば通常操作にroot権限は不要です。

`config/issue-development.example.json` を私的な運用設定の場所へコピーし、状態・ツール・
キャッシュの絶対パスと公開コミット用の名義を設定します。設定・認証情報はリポジトリや候補の
ディレクトリ外に置きます。状態ディレクトリは運用者所有でモード `0700` にします。

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json plan
python3 scripts/issue-development.py --config /absolute/private/operator.json status
```

`plan` はリモート通信もモデル実行も行いません。`status` は状態がなければ私的な保存先を
初期化しますが、開発を開始しません。設定の `enabled`、`publish`、`automatic_merge` は
独立したスイッチです。どれかを有効にしても、Issueにラベルを付けても、他は有効になりません。

対象リポジトリに限定したGitHubトークンを運用者所有の `0600` 通常ファイルに保存し、
`github_token_file` に指定します。必要なIssue・コード・PR操作とチェック結果の読み取り、
ブランチルール確認に必要な読み取りだけを許可し、保護の回避権限を与えないでください。
トークンはコマンド引数やワーカー環境に入れません。公開時は検査の外側にある信頼された
Git認証アダプターを使います。

独立レビューはOpenAI Responses APIを使い、ツールなし、固定スキーマ、候補の正確なコミット・
差分・変更ファイルを入力とします。`review_model` と `review_api_key_file` を明示設定します。
キーも同じ私的ファイルの条件を満たす必要があります。未設定なら `awaiting-review` で止まり、
Qwenの自己レビューやChatGPTログインの流用で代替しません。API利用には課金が発生します。
既定上限はUTC日付ごとに1回、入力80KB、出力3,000トークンで、失敗を含め送信前に回数を予約します。
これは金額上限ではないため、提供側プロジェクトの支出制御も設定してください。
plan/statusとローカル試験ではキーを要求・読み込みしません。

不確かな指摘や上流互換性の根拠不足は承認を止めます。初版のレビューモデルは上流資料の閲覧や
追加ツールを使えないため、静的な変更範囲の判定を通る依存更新でも人の判断が必要になる場合があります。
[OpenAIの自動化ガイド](https://learn.chatgpt.com/docs/non-interactive-mode)と
[構造化出力](https://developers.openai.com/api/docs/guides/structured-outputs)も参照してください。

## 試験・実行・停止

最初に、上記ブロックと同じ形式のJSONタスクを使い、ローカルの不変コミットで試験します。
Qwenと実検査を使いますが、GitHubやレビューAPIは呼びません。

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json trial \
  --source /absolute/path/to/checkout --revision FULL_40_CHARACTER_COMMIT \
  --task /absolute/private/task.json
```

失敗時も証跡は私的な状態ディレクトリに残ります。成功しても終了状態はマージ済みではなく
`awaiting-review` です。差分と検査ログを確認してください。実際のIssue処理には認証情報を
用意し、`enabled` を明示設定してから次を実行します。

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json resume
python3 scripts/issue-development.py --config /absolute/private/operator.json once
python3 scripts/issue-development.py --config /absolute/private/operator.json status
```

単発サイクルを確認した後でのみ、`configs/systemd/srv6-mup-issues.service` に記載した場所へ
信頼されたソースを配置し、対応する私的な設定ファイルを用意して、テンプレートをユーザーサービス
として設置します。カレンダー型の開発課題ではなく、承認済みIssueだけを確認します。

```bash
systemctl --user daemon-reload
systemctl --user enable --now srv6-mup-issues.service
systemctl --user stop srv6-mup-issues.service
python3 scripts/issue-development.py --config /absolute/private/operator.json pause
```

サービスのソースは生成された作業場所の外に置く、レビュー済みコピーにしてください。
PRから自己更新はしません。これらのコマンド自体はソースやunitの設置、ログインに依存しない
ユーザーサービスの稼働設定を行いません。
旧 `srv6-mup-background.timer` と `srv6-mup-background-watchdog.timer` は利用も有効化もしません。

実行中の作業を中断するにはサービスを停止します。`pause` は状態ロックを取得してから次回以降を
止める操作であり、実行中のワーカーを終了させるものではありません。失敗するとキューを停止して
証跡を残します。未公開の失敗・中断・レビュー却下を確認し、古い実行を保持したまま1回だけ再試行するには:

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json retry JOB_ID
python3 scripts/issue-development.py --config /absolute/private/operator.json resume
```

1世代あたり最大2回です。再試行は運用者の明示操作が必要で、勝手には予約しません。
レビュー指摘は次の試行に渡せますが、以前の検査・レビュー承認は引き継ぎません。
公開途中の再開では固定したブランチと既存PRを再利用し、ワーカーを再実行しません。
失敗した仕事を清掃で削除することもありません。状態保存先の変更にはサービスの停止・再起動が必要です。

## 公開・マージ条件

公開は初期状態で無効であり、最終検査成功と、不変のheadに結び付く独立承認が必要です。
公開通知には固定の状態とPR番号だけを含め、モデル要約、生の例外、私的ログは載せません。
通知失敗時は再送待ちフラグを保持します。GitHub不通時はローカル状態を確認してください。

自動マージには、明示的な有効化、既存の保守的な変更分類、有効な独立レビュー、変更されていないIssue、
同じbase/head、そのheadに対するGitHub Actionsの `test`、`gitleaks`、`source-and-binaries` 成功が
さらに必要です。厳密な必須ブランチチェックを設定しておく必要があり、実行基盤は保護設定を変更せず、
管理者による回避も要求しません。マージには期待するhead SHAを指定します。
実行コード・ポリシー・基盤の変更、不確かな依存更新、実機証跡不足は、自動マージではなく判断待ちです。

無人公開・マージの有効化前に、実際のIssue → PR → CI → マージ、本文変更・ラベル解除による取り消し、
通知、重複防止、時間切れ・再起動の復旧、対象ホストの資源隔離を検証してください。
オフラインのモックやローカルQwen試験は、それらの受け入れ試験の代わりではありません。
