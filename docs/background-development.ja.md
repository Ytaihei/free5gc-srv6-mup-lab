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
  --codex /absolute/path/to/package/bin/codex --go-root /absolute/path/to/pinned-go
```

3つの専用アカウント、前提パッケージ、root所有の`/opt/srv6-mup-background`、
非公開状態の`/var/lib/srv6-mup-background`を作成します。既存のアカウント・
unit・導入先・状態があれば上書きせず拒否します。部分的な導入も保持するため、
無確認で削除して再実行しないでください。両timerは有効化しません。

Codex本体だけのコピーではなく、Codex 0.154.0 Linux x86-64の完全なstandalone
パッケージを指定します。導入処理は`bin/codex`、`bin/codex-code-mode-host`、
`codex-package.json`、`codex-path/rg`、`codex-resources/bwrap`、
`codex-resources/zsh/bin/zsh`を固定SHA-256で検証します。この固定値は確認した信頼済み
パッケージのバイト列を示し、上流の署名検証ではありません。6ファイルすべてを非公開の
導入マニフェストに記録し、開発前にも検証します。ソースのみを公開する本リポジトリには
追加しません。自動ダウンロード、バージョン更新、モデル変更、認証情報のコピーは行いません。

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

導入確認ではアカウント分離・認証・モデルを介した実際のツール実行・ソース検査を確認します。公開コミットに使用する
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

### 導入確認の失敗と管理処理の修正

導入確認は`worker-auth`、`publisher-auth`、`isolation-probe`、`worker-workspace`、`worker-tools`、`source-checks`の
段階ごとに記録します。各試行は`/var/lib/srv6-mup-background/commissioning/`配下の
非公開ディレクトリへ`result.json`と権限0600のログを保存します。エラーには失敗した
段階とログの絶対パスを表示します。ログはローカルで確認し、認証出力を公開しないで
ください。再確認の前に台帳を停止し、以前の導入確認を無効にするため、失敗したのに
確認済みのまま残ることはありません。

`worker-workspace`段階は、実際の隔離workerでディレクトリへの移動と一時ファイルの
読み書きを確認します。モデル呼び出し、デプロイ、公開は行いません。開発用チェック
アウトと同じ親ディレクトリ配下に、worker所有の検査用ディレクトリを新規作成して
保持し、その場所を`result.json`に記録します。

`worker-tools`段階はパッケージを検証し、その新しい検査用ディレクトリで、同じworkerの
隔離設定の下、設定済みCodexモデルを最大180秒呼び出します。プロセスの正常終了やモデルの
成功宣言だけでなく、新しく生成した検証値を含む非公開ファイルの作成を要求します。
認証済みアカウントのモデル利用枠を消費しますが、開発用チェックアウトの調査、デプロイ、
公開、マージは行いません。補助プログラムの欠如、不正なイベント列・エラー、作成物の欠如・
不一致があれば導入確認は失敗し、キューを停止したままにします。

workerの`200/CHDIR`エラーは、root所有の作業用親ディレクトリが、作成時に`0755`を
指定しても、管理サービスの`UMask=0077`によって`0700`になった場合に発生します。
導入確認と開発処理では、シンボリックリンクをたどらないディレクトリ記述子を使い、
この1ディレクトリだけを明示的に`0755`へ設定します。既存のチェックアウトと未完了の
編集内容は保持します。所有者の不一致、リンク、他ユーザーに書き込みを許す権限は、
自動で引き取らず拒否します。非公開のworkerホームは`0700`のままで、認証情報や
証跡の権限も変更しません。下記のレビュー済み管理処理の更新と導入確認を再実行すると、
既存の親ディレクトリを修復し、timer再開前にアクセスを検証できます。再帰的なchmodや
チェックアウトの削除で回避しないでください。

`source-checks`段階は、`/opt/srv6-mup-background/candidates/`配下に保持した
root所有のスナップショットを検査用アカウントで実行します。検証済みの公開ソース
一覧に含まれるファイルだけをコピーし、導入済みマニフェストのハッシュと照合します。
導入済みツール、非公開設定、証跡は除外します。導入ディレクトリを直接アーカイブ
検査すると、それらの追加ファイルまで対象になってしまうためです。保存先は
`result.json`に記録し、成功・失敗のいずれでも削除しません。スナップショットの
準備に失敗した場合も非公開の段階別ログを保持し、導入確認済みにはしません。

`InaccessiblePaths`はアクセスを拒否しますが、パス自体が見えなくなるとは限りません。
検査は`Path.exists()`ではなく、ディレクトリへのアクセスとUnixソケット接続を確認
します。存在しない・遮断された接続先は合格ですが、接続成功やデーモンの停止だけでは
合格にしません。

`IPAddressDeny`はパケットをフィルタリングします。遮断されたTCP接続は再試行を
続け、Pythonが`EAGAIN`（`network_errno: 11`）を返す場合があり、これだけでは
隔離の証明になりません。プライベート網の検査では代わりに、`127.0.0.2`に自分で
確保した一時ポート宛てにUDPデータグラムを1個送ります。既存のホスト上のデーモン
や稼働ラボには接続しません。`sendto`からの`EPERM`または`EACCES`だけを拒否と
認め、送信成功、接続拒否、経路到達不能、タイムアウト、ソケット準備の失敗は合格に
しません。ログでは検査方式を`udp-self-send`と表示します。ネットワーク許可リストや
sandboxの制約は緩和しません。上流の
[パケットフィルターの説明](https://github.com/systemd/systemd/blob/v255/man/systemd.resource-control.xml)
と[Pythonのタイムアウト処理](https://github.com/python/cpython/blob/v3.12.3/Modules/socketmodule.c)
も参照してください。この検査は隔離された検査サービス内で実行します。通常のホスト
上で実行しても、そのサービスの隔離状態を検証したことにはなりません。このループ
バック検査だけで全アドレス範囲、IPv6経路、実機ラボ操作を検証済みとはしません。

レビュー済みの管理処理修正を既存環境へ適用する場合、両timerを停止・無効化し、
バックグラウンドサービスを停止します。単発試験が稼働中なら、そのサービスも停止し、
中断記録を確認・確認済みにしてから更新してください。更新処理は稼働中の試験サービスを
検出すると拒否します。レビュー済みチェックアウトから実行します。

```bash
sudo python3 scripts/install-background-development.py --apply --update-coordinator
```

この限定更新は導入済みマニフェストの全ハッシュを検証し、管理・実行・導入スクリプト、
その試験、日英の手順書と翻訳ハッシュだけを受け入れます。公開一覧の変更は拒否し、
ポリシー、unit、依存、ツールの版、ラボコードは更新しません。旧ファイルとマニフェストを
非公開の`updates/`配下へ保持し、アカウント・認証・タスクの証跡を維持したまま、
台帳を停止し、導入確認・自動マージを無効にします。再開前に`commission`を再実行
してください。更新が途中で止まった場合もバックアップを保持し、検証を通過できなく
なります。導入先・状態の削除や、マニフェストの編集による迂回はしないでください。

`bin/codex`だけがある旧導入環境では、管理処理の修正と合わせて、固定版の不足する
同梱ファイルの復元を明示的に指定します。

```bash
sudo python3 scripts/install-background-development.py --apply --update-coordinator \
  --codex-package /absolute/path/to/package
```

既存のCodex本体が固定パッケージと一致している必要があります。追加できるのは不足している
ハッシュ一致の同梱ファイルだけで、別のツールへの置換や、部分修復で残った未管理ファイルの
引取りは行いません。追加一覧をバックアップとともに記録し、最後に導入マニフェストを更新
します。上記同様にtimer・サービスを停止してから実行し、再開前に導入確認を行ってください。
パッケージ指定なしの`--update-coordinator`が不足ツールを暗黙に導入することはありません。

workerとレビューの標準出力は`worker.jsonl`・`review.jsonl`に、診断出力は別の
`worker.jsonl.stderr.log`・`review.jsonl.stderr.log`に保存します。すべて権限0600の
非公開証跡です。管理処理は、不正・不完全なJSONL、最上位のエラー、失敗したturn、error項目を
拒否します。終了コードが0、またはその後に`turn.completed`があっても同じです。こうした
worker実行エラーでは試験を失敗とし、キューを即時停止します。エラーなく会話が終わっただけで
タスク達成の証明とはしません。旧版の混在ログは変更せず保持し、JSONLとして直接解析しないで
ください。[公式の出力・イベント仕様](https://learn.chatgpt.com/docs/non-interactive-mode)も参照してください。

旧版で変更なしとなった試験の原因を修正した後、運用者はキュー停止中に、その未公開・コードのみの
タスクを正確に指定して再試行できます。

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-admin.py retry-task \
  --task documentation-maintenance --run EXACT_RUN_ID
```

保持された該当試験の結果が`needs-decision`で、PRや公開処理の記録がないことを要求します。
チェックアウト、基準コミット、編集内容、元のログ・結果を維持し、再試行を記録して既存作業を
優先します。失敗回数のリセットやキューの再開は行いません。稼働中の作業、実機タスク、
公開済み候補、証跡の不一致、同じ試験IDの再使用は拒否します。導入確認とこの明示的な再試行
指定が成功してから`resume`・`test-once`を使い、新しい証跡で開発の一巡を確認してください。

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

通常サービスを手動起動しても合意した時間帯を検査します。後述の明示的なコードのみの
単発試験は別のモードで、どちらも昼間の実機配備を許可しません。
開発担当は1タスクを受け、固定候補を別の検査・読取り専用
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

## 即時の単発試験（コードのみ）

導入・導入確認・`resume`の後、運用者は月水金のtimerやポリシーを変更せずに、
キューの1タスクを即時に試験できます。

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-admin.py test-once
sudo journalctl -u srv6-mup-background-test.service -f
```

これはdry-runではなく実際の開発実行です。設定済みモデルのアカウントを使用し、
公開PRの作成と固定GitHubステータスIssueの更新が発生し得ます。時間帯の条件だけを
実行開始から40分の期限に置き換えます。一時サービス全体の実行上限は45分、停止猶予は
60秒です。導入済みソースの検証、導入確認、認証、リソース制限、連続失敗上限、共有ロック、
認証情報を持たない検査、独立レビュー、公開時の検査は維持します。台帳が停止中、中断記録が
存在、実行可能タスクなし、選択タスクが実機プロファイルを要求、自動化PRが3件以上の
いずれかで試験を拒否します。PR上限は既存作業の再開にも適用します。自動マージは
運用者の設定を書き換えず、この実行のメモリー上で強制的に無効化し、実機操作も無効のままです。

起動コマンドはサービスの起動後に戻ります。**試験の成功・完了を意味しません**。
試験サービス名は固定で、重複起動で稼働中の試験を置き換えません。定期開発と試験は同じ
ロックを使用します。timer、永続的な例外設定、キューの優先順位、モデルは変更しません。
`_execute-test-once`はサービス内部の入口で、運用者向けコマンドではありません。

ログに`/var/lib/srv6-mup-background/trials/RUN_ID/result.json`配下の非公開結果パスを
表示します。表示された正確なファイルをsudoでローカル確認してください。開始・終了時刻、
固定期限、最終工程、タスクの結果、存在する場合のPR番号、最終通知の到達を記録します。
`completed`は選択した処理が終了し、最後のステータスIssue更新が成功したことを示します。
非同期のGitHub CI、マージ、実機ラボ検証の完了は意味しません。保持した公開処理の再開では
ワーカーを再実行しない場合があり、変更なしの場合はタスク・試験ともにPRを作らず
`needs-decision`となり、`completed`にはしません。過去の結果を遡って書き換えることはありません。
これらは新しい候補作成・検査・レビュー・PR作成の一巡の証明ではありません。一巡の受け入れ
前に、保持した実行証跡と該当PRの検査結果を確認してください。

最後のステータス報告は週単位の重複抑止を適用せず、非公開ログではなく固定タスクID・状態
だけを公開します。通知失敗時は試験を失敗・通知待ちとして記録し、作成済みPRは保持します。
稼働中の試験を停止する場合:

```bash
sudo systemctl stop srv6-mup-background-test.service
```

停止・時間切れ時には監視処理を呼びます。中断した実行記録を保持し、運用者の確認と
正確なIDによる確認済み操作までキューを停止します。急な停止では結果ファイルが
`status: running`のまま残る場合があります。これは不完全な証跡であり、成功ではありません。
子ジョブにも既存の実行上限が適用されます。再試行のために証跡を削除したり、ロックを
強制解除したりしないでください。ログと結果は非公開の運用証跡で、公開資料ではありません。
このモードのオフライン試験は、導入済みホスト上の実際の隔離試験の代わりにはなりません。

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
