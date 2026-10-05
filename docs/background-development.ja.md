# 任意のバックグラウンド開発

[English](background-development.md) | 日本語

## 状態と境界

停止状態で導入する任意の管理処理、タスク台帳、開発・検査・公開担当の分離、
固定PRの証跡、保守的なマージ判定を提供します。アプリのScheduledではなく、
サーバー側のsystemdジョブです。
[Codexの非対話実行](https://learn.chatgpt.com/docs/non-interactive-mode)を使います。
実装があることは、導入・無人実行・復旧リハーサルの完了を意味しません。
実際の状態は非公開の運用記録で確認してください。

合意した実行時間は日本時間の月・水・金03:00〜04:00で、新規開発は03:40まで
です。逃した回の追いかけ実行はしません。同時実行は1件だけで、失敗時も
ソースと証跡を保持します。3回連続失敗で停止し、中断記録が残る場合は確認
するまで再開を拒否します。

**初期の管理処理では、両構成への実機候補配備と自動復旧リハーサルを
有効化していません。** 台帳に必要な実機構成を残し、未実施を合格に
しません。導入確認処理は実機構成の有効化を拒否します。これらは残る実装・
受け入れ作業であり、timerを停止しておけば完成したという意味ではありません。
インストーラーはラボ認証情報を配置しません。

## 導入せず確認する

```bash
python3 scripts/background-development.py plan
python3 scripts/background-development.py status
python3 scripts/background-development.py run --dry-run
python3 scripts/install-background-development.py
```

これらは状態初期化、Codex実行、GitHub通信、サービス導入、ラボ操作を行いません。
通常のチェックアウトから特権管理処理を実行・再開できません。既定のローカル
状態は利用者の非公開状態ディレクトリで、導入済みスケジューラーの状態とは別です。

## 導入と動作確認

sudoと、既存の信頼するCodexおよび固定版Goの導入先が必要です。プレビュー後、
それぞれの絶対パスを指定します。

```bash
sudo python3 scripts/install-background-development.py --apply \
  --codex /absolute/path/to/codex --go-root /absolute/path/to/pinned-go
```

3つの専用アカウント、前提パッケージ、root所有の`/opt/srv6-mup-background`、
非公開状態の`/var/lib/srv6-mup-background`を作成します。既存のアカウント・
unit・導入先・状態があれば上書きせず拒否します。部分的な導入も保持するため、
無確認で削除して再実行しないでください。両timerは有効化しません。

**導入前に**unitファイルだけをコピーしてtimerを有効化した場合、timer一覧に
表示されても開発処理が動く証拠にはなりません。`/opt`の実行ファイルが存在しない
可能性があります。インストーラーはパッケージ導入・アカウント・状態作成より前に
unit競合を確認します。この状態からの復旧に限り、上記の導入コマンドへ
`--adopt-existing-units`を追加できます。内容が完全一致し、root所有で、グループ・
他ユーザーの書込み権限、シンボリックリンク、ハードリンク、systemdの上書き設定が
ないテンプレートだけを受け入れます。稼働中のサービスと別ディレクトリのunitは
拒否します。一致するtimerは導入前に無効化し、認証・導入確認・再開が成功するまで
無効のままにします。独自unitや部分導入は別途確認が必要です。再試行のために
無確認で削除しないでください。

新しいアカウントでローカルに認証します。既存の認証ファイルをコピーせず、
トークンをコマンド引数・チャット・Issue・公開ワークフローへ貼らないでください。

```bash
sudo -u mup-bg-worker -H /opt/srv6-mup-background/bin/codex login --device-auth
sudo -u mup-bg-publisher -H gh auth login --hostname github.com --git-protocol https
```

公開担当の認証は、このリポジトリの内容・PR・Issue操作と検査結果の読取りに
限定します。管理者、ワークフロー編集、パッケージ、保護迂回の権限を付与しないで
ください。開発担当には公開認証、sudo、Docker/libvirtソケット、ラボSSH鍵を
渡しません。試験は両方の認証を持たない3つ目のアカウントで実行します。
モデル実行は認証したCodexアカウントの利用枠を消費し、API課金への自動切り替えはありません。

導入確認ではアカウント分離・認証・ソース検査を確認します。公開コミットに使用する
作成者情報を明示してください。

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-admin.py commission \
  --author-name 'Your Name' --author-email 'you@example.org'
```

この処理後もスケジューラーは停止、自動マージは無効です。初期モデルは配備環境で
選択済みだったCodexモデルを非公開の運用者ファイルに固定します。利用できなければ
失敗し、暗黙に切り替えません。実際の分離PR・検査・レビューを確認するまで自動
マージを有効にしないでください。その受け入れ後に、運用者が非公開の
`automatic_merge`を明示的に設定します。モデルはこのファイルを書き換えられません。

## 実行・停止・確認

導入確認後、運用者は**コードのみ**の開発を再開し、両timerを有効化できます。
これで未有効化の実機部分まで完了するわけではありません。

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-development.py \
  --state /var/lib/srv6-mup-background resume
sudo systemctl enable --now srv6-mup-background.timer srv6-mup-background-watchdog.timer
sudo systemctl list-timers srv6-mup-background.timer
```

両timerに加え、実際の導入状態も確認します。

```bash
test -x /opt/srv6-mup-background/venv/bin/python3
getent passwd mup-bg-worker mup-bg-checks mup-bg-publisher
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-development.py \
  --state /var/lib/srv6-mup-background status
systemctl is-active srv6-mup-background.timer srv6-mup-background-watchdog.timer
sudo journalctl -u srv6-mup-background.service -u srv6-mup-background-watchdog.service
```

通常のチェックアウトのstatusは、別の初期停止状態を参照するため、導入確認の証拠
にはなりません。初回待ちのtimer、実行履歴のないサービス、オフライン試験の成功は
無人開発の一巡を実証しません。実際の分離候補・検査・レビュー・PR・通知を確認する
まで、自動マージは無効に保ってください。

サービスを手動起動しても合意した時間帯を検査します。昼間の配備を許可する
迂回機能はありません。開発担当は1タスクを受け、固定候補を別の検査・読取り専用
レビューで確認します。公開担当は秘匿値を含めない固定本文のPRを作成します。
公開中断後の再試行は記録したブランチ・コミットを使い、PRの重複を避けます。
自動化PRは最大3件で、無関係のDependabotや投稿者のPRを自動マージしません。

```bash
sudo systemctl disable --now srv6-mup-background.timer
sudo systemctl stop srv6-mup-background.service
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-development.py \
  --state /var/lib/srv6-mup-background pause
sudo journalctl -u srv6-mup-background.service
```

監視処理は中断記録を保持し、後続作業を停止します。保持した作業を確認し、
ワーカーが止まっていることを確認してから、正確なIDを指定して確認済みにします。

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-admin.py acknowledge --run EXACT_RUN_ID
```

確認済みにしてもソースと証跡を保持し、スケジュールは停止したままです。ログは
ローカルで確認し、そのまま公開できる資料とは扱わないでください。GitHubの1件の
Issueに、固定タスクID・状態だけの週報と緊急失敗通知を記録します。通知到達は
受け入れ前で、GitHub障害時は通知待ちをローカルに保持します。自動清掃、メール、
アプリ受信箱通知はありません。到達確認まではサービス状態とPRを手動で確認してください。

## マージ条件と残る受け入れ

初期の自動判定対象は、小さなREADME・用語集の説明文修正と、既存動作への純粋な
追加単体試験だけです。その他の文書、試験の編集・削除、実行コード、依存、
ポリシー、ライセンス、実行例、標準準拠・検証成功の主張は人の判断が必要です。
公開一覧の追加は新しい試験ファイルだけに限定します。日英文書のハッシュは
機械的に更新しますが、意味のレビューの代用にしません。

固定候補への別工程のレビュー、全ローカル検査、同じコミットに対するGitHub
Actionsの`test`・`gitleaks`・`source-and-binaries`成功を要求します。mainは記録した
基準コミットと一致する必要があります。期待するhead SHAを指定し、squashと
既存の保護を使ってマージします。管理者迂回は禁止です。基準が古い、レビューが
不明、headが変わった場合は自動マージを止めます。

残る受け入れは、実際のroot導入とsandbox・ネットワーク確認、専用認証、実際の
PR・CI・マージの一巡、通知到達、両実機アダプターと時間制限付き復旧リハーサルです。
それらが成功するまでは、検査したコード開発基盤であって、**自律ラボ開発の運用
全体が完了した状態ではありません**。イメージ配布、OS/DB更新、VM削除、生の
キャプチャー公開は対象外のままです。
