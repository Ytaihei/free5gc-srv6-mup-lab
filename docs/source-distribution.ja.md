# ソースのみの配布

[English](source-distribution.md) | 日本語

公開対象は隔離されたIPv4 Direct SRv6 MUP実験ラボです。ソース公開は、稼働するコントローラー、ダッシュボード、free5GC WebUI、加入者DBのインターネット公開を許可するものではありません。

## 明示的な配布範囲

`config/public-source.json` は公開ファイルを1件ずつ指定します。新たに管理するファイルやignore対象外のファイルは、書き出し前に分類が必要です。ポリシーはソースと同じ不変コミットから取得するため、未コミットのポリシー変更で既存コミットの候補を変えることはできません。公開パスと非公開パスの重複も禁止します。

有効なGo実装、Vinberoパッチ、Ansible/libvirt構築、サンプルYAML、試験、CI、再現手順、LICENSE、THIRD_PARTY_NOTICESを残し、次を除外します。

- ホスト固有のゲーム、ディスク復旧、ファイアウォール、バックアップ操作。
- 無効なVPP/Pythonサイドカー実装と専用試験。
- 非公開の工数見積もり、承認記録、PR/CIリンク、詳細実行ログ。
- Git履歴/メタデータ、実行時ディスク・イメージ・バイナリ、未レビューのキャプチャー、生成cloud-init、ローカル設定、認証情報。

除外した復旧ファイルは非公開で保持し、削除しません。汎用ラボアドレス、公開UEサンプル、意図した上流/著者の帰属表示は残しますが、本番用認証情報ではありません。[セキュリティ方針](../SECURITY.ja.md)と[第三者ソフトウェアの通知](../THIRD_PARTY_NOTICES.ja.md)を確認してください。この手順は梱包/プライバシー検査であり、全面的な脆弱性監査や法的認証ではありません。バイナリ・イメージ・サービス提供には別途レビューが必要です。

唯一のバイナリ例外は[レビュー済みPCAP例示](../examples/pcap/one-call/README.ja.md)です。`config/public-pcaps.json` で許可パス、SHA256、サイズ、パケット数を固定し、履歴検査も含めてソースと同じコミットから読みます。ツリー・書き出し・履歴検査は、欠落、未登録、差し替え、不正構造、切り詰められたキャプチャーを拒否します。対応はサイズ制限付きの通常Ethernet PCAPのみで、PCAPNGは許可しません。バイナリの私用情報検査は生文字列とIPバイナリ表現を調べますが、全プロトコルのデコード項目までは調べません。一覧更新前にはオフラインでパケット単位のレビューが必要で、一致するハッシュはレビュー対象を固定するだけです。公開承認ではありません。通常の実行時キャプチャーや私用取得ツールは配布しません。

## 検査と書き出し

```bash
make check-public-source
# Keep real identifiers in a private JSON string array outside the public tree.
python3 scripts/export-source.py --check-tree . \
  --denylist /private/path/publication-identities.json
python3 scripts/export-source.py --revision HEAD \
  --denylist /private/path/publication-identities.json \
  --output artifacts/source/candidate-unique.tar.gz
```

リリース候補の作成前にソースをレビューしてコミットします。書き出しはコミット済みblobだけを使い、未コミットの変更を含めません。アーカイブの上書き、refのpush、公開設定変更、公開承認も行いません。JSON出力にはリビジョン、ファイル数、SHA256、`publication_approved=false` を記録します。tarの時刻/UID/GIDは0、所有者名は空にし、GitコミットのPAXヘッダーを省きます。選択ソースとモードが同じなら、コミット識別情報にかかわらず同じアーカイブバイト列になります。

非公開の禁止リストには、既知のホスト名/ユーザー名、Tailnet識別子、その他秘匿すべき個人情報を含めます。移植可能なCIでは任意ですが、操作者の最終プライバシーレビューでは必須です。検査対象はテストとWeb素材を含むすべての選択ファイルです。大小文字を区別せず照合し、Pythonの定数文字列結合も調べます。エラーにはパスだけを出し、一致値は表示しません。実際の禁止リストを公開テストへ埋め込まないでください。未知・符号化された全識別情報を検出できるわけではなく、手動確認と秘密情報スキャンも必要です。

新しい空ディレクトリへ展開し、`free5gc-srv6-mup-lab` に入って、他のツールがファイルを作る**前に**一覧とプライバシーを検査します。

```bash
python3 scripts/export-source.py --check-tree . \
  --denylist /private/path/publication-identities.json
./scripts/verification-tool.sh gitleaks dir . --redact --config .gitleaks.toml
make check
make lint
ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml --syntax-check
```

公開テスト鍵の完全一致パス例外が一致するよう、Gitleaksは展開したプロジェクト内で実行してください。実際の秘密鍵・トークンは許可しません。展開一覧の検査は、非公開対象パスも含め、余分なファイルを拒否します。ダイジェストと秘匿情報を除いた検査結果は、展開ソースの外へ保存してください。

## 公開は別途承認する

履歴のない新規リポジトリまたは承認済みソースアーカイブを使います。過去のホスト情報を含む復旧用リポジトリを公開へ切り替えたり、古いrefをpushしたりしないでください。Issue、PR、Actionsログ、成果物、リリース、スクリーンショットは別の公開面であり、このツールで秘匿化されません。

公開前に候補そのものと公開先を承認し、Go module/importパス、リポジトリリンク、CODEOWNERS、報告先を公開先に合わせます。非公開脆弱性報告窓口と必須CIを有効化・確認してください。既存所有者の帰属表示は意図的なもので、匿名性の約束ではありません。公開リリースと実行時成果物の配布には、ローカル検査の成功とは別の明示的承認が必要です。

## Git履歴全体を別途確認する

書き出したツリーの検査成功は、復旧用Gitリポジトリの公開を承認するものではありません。次の読み取り専用検査は、現在のブランチからファイルを削除済みでも、到達可能な履歴に非公開ファイルがあるリポジトリでは意図的に失敗します。

```bash
python3 scripts/export-source.py --check-history . \
  --denylist /private/path/publication-identities.json
```

ローカルのrefsとHEAD、全到達コミットのツリーと各版の公開対象一覧、注釈付きタグの連鎖、コミット・ref・タグのメタデータ、禁止ファイル形式を調べます。識別情報はパスとPythonの定数文字列結合も対象です。shallow、grafts、replace refsは拒否します。出力は問題の分類・件数だけで、秘匿値を表示しません。失敗時は非0で終了しますが、ブランチ削除や履歴書き換えはしません。refsの取得、reflog・到達不能オブジェクト、GitHub上の記録やキャッシュされたPR refsの検査は行いません。Gitleaksも別途必要です。

非公開の復旧リポジトリでは失敗することが正常です。CIは書き出した公開ツリーから隔離リポジトリを作り、架空の検査用作成者で成功側を確認します。これは実際のリリースや承認済み作成者情報ではありません。公開先、ブランチ運用、リモート上の記録、最終承認は[公開チェックリスト](publication-checklist.ja.md)で確認します。
