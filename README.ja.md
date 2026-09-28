# free5GC SRv6 MUP ラボ

[English](README.md) | 日本語

このリポジトリは、free5GC、UERANSIM、N4/PFCPのパッシブオブザーバー、GoBGPを内部に組み込んだMUPコントローラー、VinberoのeBPF/XDP SRv6データプレーンを使い、隔離されたIPv4 Direct MUPラボを構築します。ユーザープレーンは物理LANから分離します。実験用のオープン実装であり、本番用5GコアやMUP標準への完全準拠をうたうものではありません。

用語は[用語集](docs/glossary.ja.md)、実装済み・一部実装・未実装の機能と、それとは別に管理する検証証拠は[機能の対応状況](docs/feature-status.ja.md)を入口にしてください。

単一VMでのセットアップと部品単位の改変については、[コンパクト構成の手順](docs/compact-lab.ja.md)と[構築済みイメージ／開発の操作](docs/prebuilt-development.ja.md)を参照してください。ソースからの構築は利用できますが、構築済みイメージの配布は引き続き審査完了が条件です。

## アーキテクチャ

両端はともに **MUP PE** で、**MUP PE（N3／Interwork側）** と **MUP PE（N6／Direct側）** と表記します。互換性のため内部IDは `tpe` と `npe`（VM名は `lab-tpe` と `lab-npe`）のままです。これらのIDはMUP仕様の用語ではありません。

```text
                         BGP MUP SAFI (AFI 1 / SAFI 85)
                   +---------------------------------------+
                   |                                       |
             lab-tpe 192.168.123.12                  lab-npe .13
             MUP PE (Vinbero)                       MUP PE (Vinbero)
             N3/Interwork side                      N6/Direct side
                   |          SRv6 underlay                |
 gNB .11 -- N3a -- +==== 2001:db8:100:10::/64 ============+ -- N6 -- DN .15
                   |                                       |
                   + -- N3 core -- ordinary UPF .10 -------+

 SMF <-- N4/PFCP --> UPF       lab-core, Docker bridge br-free5gc
                         ^ passive AF_PACKET capture
                         |
                  pfcp-observer -- snapshot/lease --> lab-mupc .14
                                                    MUP-C + GoBGP
```

オブザーバーは、受理されたPFCPセッション確立・変更・削除トランザクションだけを状態に反映します。MUP-Cは設定されたDNN/UE/SUPIポリシーを適用し、下り用のT1 Session Transformed経路と上り用のT2 Session Transformed経路を生成します。VinberoはMUP PE（N3／Interwork側）のInterwork Segment Discovery経路とMUP PE（N6／Direct側）のDirect Segment Discovery経路を使って解決し、eBPFマップに設定します。

選択された通信はSRv6 MUPのバイパスを通ります。未一致の通信、経路が撤回された通信、オブザーバー状態の期限が切れた通信は、カーネル/通常UPF経路を使います。フォールバックで疎通するには有効なUPFセッションと経路が必要で、未知TEIDをUPFが受理するとは限りません。TEIDの手入力は不要です。

経路とパケットの流れは[アーキテクチャ](docs/architecture.ja.md)、アドレス・RD・RT・ロケーター・SIDの詳細は[アドレス計画](docs/address-plan.ja.md)を参照してください。

## 実装済みの構成要素

- 自動起動しない6台のlibvirt VMと、隔離されたユーザープレーンネットワーク
- free5GC v4.2.3、gtp5g v0.9.5、UERANSIM v3.3.0
- `br-free5gc` 上でのパッシブなPFCPセッション再構成
- ポリシー駆動のMUP-C、GoBGP v4.9.0、Connect RPC、`mupctl`
- 上流GoBGP v4.8を使う互換パッチを適用したVinbero v0.1.1
- PEのvirtioインターフェース上のdriver-mode XDP。パッチ適用済みeBPFオブジェクトはプロビジョニング時に再生成
- 部分的な失敗をローカルで巻き戻すT1/T2ペア広告と、15秒のオブザーバーリース失効による撤回。両PEへの同時・不可分な適用ではない
- MUP PE（N3／Interwork側）の `End.M.GTP4.E`、MUP PE（N6／Direct側）の `End.DT4`、ISD/DSD解決、直接経路とフォールバック経路
- 撤回時のフォールバックを含む、ベースラインおよびMUPのE2E試験スクリプト
- 新しいPFCP登録を伴う、オブザーバーリースの失効・復旧試験
- トポロジー、PFCPセッション、MUP経路、サービス状態、Vinbero XDPカウンターを表示し、5秒ごとにUE→DNのICMPプローブを行う、設定を変更しないライブダッシュボード

Ansibleは `vinbero_pe` ロールを配備します。既存ゲストの移行時には旧VPP/サイドカーのサービスを無効化しますが、パッケージは削除しません。旧実装のソースは公開配布に含めません。

## ライフサイクル

ホストとネットワークの設定は `config/lab.example.yml` に集約されています。別マシンでは、Git管理対象外の `config/lab.local.yml` にコピーしてください。既存トポロジーを変更する前に[移植可能な設定](docs/portable-configuration.ja.md)を確認してください。設定ファイルは稼働環境の自動移行機構ではありません。

同じ物理ホスト上の独立したクリーンOSによる復旧試験は[ネストKVMでの再現](docs/clean-room-reproduction.ja.md)を参照してください。元のラボを保持する任意の試験ですが、一時停止が必要です。[検証サマリー](docs/validation-summary.ja.md)には、非公開のホスト情報や作業履歴を除き、検証条件と適用範囲の限界を記載しています。

```bash
cd free5gc-srv6-mup-lab
./scripts/install-go-toolchain.sh
make preflight
make check
make bootstrap
make test-baseline
MUP_ENABLE=1 make test-mup
MUP_ENABLE=1 make test-lease
ONE_CALL_ENABLE=1 make test-one-call
```

次のコントローラー操作は `lab-mupc` 上で実行します。

```bash
mupctl status
mupctl sessions
mupctl suppress <session-key>
mupctl resume <session-key>
mupctl reconcile
```

## ライブダッシュボード

ダッシュボードはユーザーサービスとしてインストールされ、5秒ごとにラボの状態を更新します。ローカルのほか、`tailscale0` が存在する場合は暗号化されたTailscaleネットワークから利用できます。

- ローカル: `http://127.0.0.1:8787/`
- Tailnet: `http://<tailscale-hostname>:8787/`

`make dashboard-install` でインストール・更新します。待ち受け先はループバックと `tailscale0` に割り当てられたIPv4アドレスだけで、物理LANやワイルドカードアドレスにはバインドしません。追加のローカルツール向けに `/api/state` でJSONスナップショットを提供します。

5秒ごとの収集時に、`uesimtun0` から `10.210.6.15` へICMP echoを1回送信します。トポロジー上では有効なMUPまたはフォールバック経路に要求・応答をアニメーション表示し、RTTを表示します。タイムアウト時はU-Planeのリンクが赤くなります。これがダッシュボードによる唯一の能動監視通信です。コントローラー、BGP、PFCP、Vinberoへのアクセスはすべて読み取り専用です。

UEの一連の動作を見るには、画面を開いたまま `ONE_CALL_ENABLE=1 make test-one-call` を実行します。現在のUEを切断し、画面確認用に一時停止した後、新しいInitial RegistrationとPDU Session確立を行い、PFCP由来のT1/T2設定を待ちます。その後、両PEのXDP redirectカウンター増加とともにICMP/HTTP疎通を確認します。既定8秒の観察用待機は `OBSERVE_SECONDS` で変更できます。

オフラインで確認するには[レビュー済み1call PCAP例示](examples/pcap/one-call/README.ja.md)を開いてください。N2/N4/BGP/N3/SRv6/N6の個別トレースと統合版を用意し、残しているラボ識別情報、プライバシーレビュー、デコーダーの制約を日英で説明しています。

インストール・復旧・検証手順は[運用手順](docs/operations.ja.md)、実証済みの範囲と残る研究試験は[研究範囲](docs/research-scope.ja.md)を参照してください。今後のMUP拡張は[研究バックログ](docs/research-backlog.ja.md)でpendingとしています。CIの対象、ローカルスキャナー、SBOMと制約は[サプライチェーン検査](docs/supply-chain.ja.md)に記載しています。

## ライセンスと配布

セットアップ簡素化のため、実験的な[単一VM Compose構成](docs/compact-lab.ja.md)を検証しています。まだ標準構成やビルド済み配布版ではなく、上記の参照手順は引き続き6VM構成です。

このリポジトリ独自のコードと設定はApache License 2.0です。プロビジョニング時に取得するソフトウェアには、それぞれのライセンスが適用されます。[第三者ソフトウェアの注意事項](THIRD_PARTY_NOTICES.ja.md)を確認してください。配布マイルストーンが完了するまではソースのみを配布し、VMイメージ、コンテナーアーカイブ、コンパイル済み第三者バイナリは含めません。

[ソース配布手順](docs/source-distribution.ja.md)に、明示的な公開対象一覧、プライバシー検査、履歴を含めない書き出しツールを説明しています。

正式なライセンスは[英文LICENSE](LICENSE)です。[非公式の日本語参考訳](LICENSE.ja.md)も用意しています。

## 連絡先

保守者：[Ytaihei](https://github.com/Ytaihei)。連絡先：
[taihei@sfc.wide.ad.jp](mailto:taihei@sfc.wide.ad.jp)。

通常の不具合報告には[GitHub Issues](https://github.com/Ytaihei/free5gc-srv6-mup-lab/issues)を利用してください。
機微なセキュリティ報告はメールまたはGitHubの非公開脆弱性報告機能で送ってください。
詳細は[SECURITY.ja.md](SECURITY.ja.md)を参照してください。
公開Issueに認証情報、非公開のラボ情報、未レビューのキャプチャを含めないでください。
