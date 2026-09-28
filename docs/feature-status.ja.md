# 機能の実装・検証状況

[English](feature-status.md) | 日本語

確認日: 2026-09-15。管理対象のラボ構成と記録済みの証拠を人が確認した一覧であり、ダッシュボードの現在値ではありません。この文書追加のために、権限を要する転送試験を再実行したわけではありません。過去の実行は[検証サマリー](validation-summary.ja.md)、用語は[用語集](glossary.ja.md)を参照してください。

## 2種類の状況の読み方

実装と検証は独立した軸です。

| 実装状況 | 意味 |
|---|---|
| 実装済み | 行に記載した制限の範囲で、現在のラボがその機能を提供する |
| 一部実装 | 基礎となる実装はあるが、記載した機能全体は提供していない |
| 未実装 | このラボではその機能・プロファイルを提供しない。上流ライブラリすべてに実装がないという意味ではない |

| 検証状況 | 意味 |
|---|---|
| 実機記録あり | 対応する過去の実行・パケット取得の記録がある。記載した条件にだけ適用する |
| オフライン試験のみ | 対応するソース・単体・配布検査はあるが、この行について実機での主張はしない |
| 未検証 | 記載した条件全体に対応する証拠がない。コードが存在しないことと同義ではない |

対応ベースラインは、Ubuntu 24.04の1ホスト上で動くx86-64 KVMゲスト6台、IPv4 UE 1台、N3 Interwork接続、N6 Direct Segmentです。セッション経路はAFI 1 / SAFI 85、32ビットTEIDの完全一致、固定のSID引数配置を使います。Draftの正確な版と実証の水準は[研究範囲](research-scope.ja.md)にあります。[研究バックログ](research-backlog.ja.md)の拡張は引き続きpendingであり、この表は着手や納期を約束するものではありません。

## 制御プレーン

| ID | 機能 | 実装状況 | 検証状況 | 証拠と制限 |
|---|---|---|---|---|
| CP-01 | 受理PFCPセッションのパッシブなライフサイクル追跡 | 実装済み | 実機記録あり | [状態リーダー](../internal/pfcpstate/state.go)、[1callトレース](../examples/pcap/one-call/README.ja.md)。確立・変更・削除を応答確認後に反映。PFCPを終端・注入しない |
| CP-02 | 拒否・不完全PFCPと不正スナップショットの処理 | 実装済み | オフライン試験のみ | [PFCP試験](../internal/pfcpstate/state_test.go)、[コントローラー試験](../internal/controller/controller_test.go)。変更のトランザクション処理、不完全な受理変更による撤回、不正フィールド、古い世代を含む。網羅的な実機障害試験ではない |
| CP-03 | DNN/UEプレフィックス選択と既定の通常UPF経路 | 実装済み | 実機記録あり | [ポリシー試験](../internal/policy/policy_test.go)、[ベースライン](../scripts/test-baseline.sh)、[MUP試験](../scripts/test-mup.sh)、[実行記録](validation-summary.ja.md)。実機証拠は既定の1 UEと抑止・撤回。選択・非選択UE群の同時試験ではない |
| CP-04 | SUPIプレフィックスによるポリシー選択 | 実装済み | 未検証 | [ポリシー評価](../internal/policy/policy.go)はobserverが抽出した識別情報を利用可能。対応するPFCP User IDが必要で、SUPI選択専用の実行記録はない |
| CP-05 | IPv4 T1/T2の生成・置換・撤回 | 実装済み | 実機記録あり | [スピーカー](../internal/bgp/speaker.go)、[wire fixture](../internal/bgp/wire_compat_test.go)、[実行記録](validation-summary.ja.md)。T1はUE/RAN/DL TEID/QFI、T2はUPF/UL TEIDとDirect Segment ECを運ぶ。片側の広告失敗はローカルで巻き戻すが、両PEへの同時・不可分な適用ではない。固定4.8 fixtureで4.9コントローラーを確認するが、全MUP形式を網羅しない |
| CP-06 | ISD/DSDの反映とPEでのセッション経路解決 | 実装済み | 実機記録あり | [アーキテクチャ](architecture.ja.md)、[PE配備](../ansible/roles/vinbero_pe/tasks/main.yml)、[MUP試験](../scripts/test-mup.sh)、[実行記録](validation-summary.ja.md)。この構成ではT1をISD、T2のDirect Segment ECをDSDで解決 |
| CP-07 | 追加ST2 TLV・ECと既定以外の経路プロファイル | 未実装 | 未検証 | [経路生成](../internal/bgp/speaker.go)、[構成制約](../scripts/lab-config.py)。追加のDraft表現や任意のInterwork/Direct選択を統合していない。上流パーサーの対応はラボのE2E対応ではない |
| CP-08 | TEIDプレフィックスの集約 | 未実装 | 未検証 | [設定検証](../scripts/lab-config.py)で `mup.teid_prefix_length` を32に固定。[wire境界値試験](../internal/bgp/wire_compat_test.go)はデコーダーの長さを確認するもので、集約転送や重複プレフィックスの動作確認ではない |

## データプレーンと配備プロファイル

| ID | 機能 | 実装状況 | 検証状況 | 証拠と制限 |
|---|---|---|---|---|
| DP-01 | 選択したIPv4 UE通信の双方向SRv6転送 | 実装済み | 実機記録あり | [パケットの流れ](architecture.ja.md)、[MUP試験](../scripts/test-mup.sh)、[例示パケット](../examples/pcap/one-call/README.ja.md)。Vinbero driver-mode XDP、上りのGTP4相互接続とEnd.DT4、下りのH.EncapsとEnd.M.GTP4.Eを[固定SID配置](address-plan.ja.md)で使用 |
| DP-02 | 抑止・撤回後の通常UPF転送 | 実装済み | 実機記録あり | [ベースライン](../scripts/test-baseline.sh)、[MUP試験](../scripts/test-mup.sh)、[実行記録](validation-summary.ja.md)。元のUPFセッションとカーネル経路が有効であることが必要。撤回はPFCP状態を再生成せず、無損失も保証しない |
| DP-03 | 障害注入時の未知TEIDの動作 | 一部実装 | 未検証 | [フォールバック設計](architecture.ja.md)では未一致の通信をカーネル/UPFへ渡す。未知TEIDを注入する専用の受け入れ試験表はなく、不正TEIDをUPFが受理するとは限らない |
| DP-04 | 複数QFI、QFI変更とQoSのパケット処理 | 一部実装 | 未検証 | [セッションモデル](../internal/model/session.go)と[経路生成](../internal/bgp/speaker.go)は単一のQFIを運び、[サンプル](../examples/pcap/one-call/README.ja.md)で既定QFIを確認可能。それだけでは複数フローの選択、変更処理、スケジューリング・帯域制御の証明にならない |
| DP-05 | IPv6 UE・IPv4v6 PDU、AFI 2、mobile GTP6構成 | 未実装 | 未検証 | [セッション検証](../internal/model/session.go)はUEとトンネル終端にIPv4を要求し、[BGPファミリー](../internal/bgp/speaker.go)はAFI 1。SRv6アンダーレイのIPv6はIPv6 UE対応を意味しない |
| DP-06 | 複数UE・スライス、移動と加入者間の分離 | 一部実装 | 未検証 | [コントローラー](../internal/controller/controller.go)は複数セッションキーを保持できるが、[fixture](../config/lab.example.yml)は1加入者・1スライス。単一UEの再登録は実施済み。同時ポリシー群やgNB/UPF終端変更時の分離は未検証 |
| DP-07 | 複数セグメント、collapsed PE、home-routed N9構成 | 未実装 | 未検証 | [トポロジー](architecture.ja.md)と[設定](../config/lab.example.yml)は別々のPEでInterwork/Direct接続を一つずつ提供。これらの追加構成は含まない |

## 試験と可観測性

| ID | 機能 | 実装状況 | 検証状況 | 証拠と制限 |
|---|---|---|---|---|
| V-01 | 1callの切断→登録→PDU・データ疎通試験 | 実装済み | 実機記録あり | [1callスクリプト](../scripts/test-one-call.sh)、[実行記録](validation-summary.ja.md)、[サンプル](../examples/pcap/one-call/README.ja.md)。PFCP由来の経路・マップ、ICMP 8応答、期待するHTTP本文、両PEのXDPカウンター増加を確認。音声通話ではない |
| V-02 | observerリース失効・撤回・復旧 | 実装済み | 実機記録あり | [リース試験](../scripts/test-observer-lease.sh)、[実行記録](validation-summary.ja.md)。observer停止後の撤回・フォールバックを確認し、新しいUE/PFCP登録で復旧。コントローラーHAや時間上限付きの無損失保証ではない |
| V-03 | PEのネットワーク再設定・近隣エントリー復旧 | 実装済み | 実機記録あり | [ネットワーク復旧試験](../scripts/test-network-recovery.sh)、[実行記録](validation-summary.ja.md)。netplan apply、systemd-networkd再起動、MUP PE（N6／Direct側）のDN近隣情報更新と、その後の1callを確認。通信に影響する明示的な有効化が必要な試験で、Vinbero/MUP-Cデーモンの再起動ではない |
| V-04 | MUP-C・各PEデーモンの独立再起動と損失評価 | 一部実装 | 未検証 | [サービスと配備処理](../ansible/site.yml)、observerの定期スナップショットなど復旧の土台はある。[pendingの試験](research-backlog.ja.md)では独立再起動、復元状態の比較、損失測定が残る |
| V-05 | PFCP→BGP、BGP→eBPF、最初のパケットの個別収束測定 | 未実装 | 未検証 | [研究範囲](research-scope.ja.md)で個別測定を要求。ポーリング、ping RTT、統合キャプチャーの時刻だけでは、校正済みの収束時間測定にならない |
| V-06 | 配布レビュー済みの1call PCAP解説 | 実装済み | 実機記録あり | [7件のキャプチャーと確認事項](../examples/pcap/one-call/README.ja.md)、[ハッシュ一覧](../config/public-pcaps.json)。N2/N4/BGP/N3/SRv6/N6と統合版で、ラボ識別情報・認証関連の文脈を残す。完全一致の配布検査はオフライン検査であり、別の実機1callではない |
| V-07 | 選択・非選択群とフォールバックの網羅的な比較キャプチャー | 一部実装 | 未検証 | [1call例示](../examples/pcap/one-call/README.ja.md)と[フォールバック試験](../scripts/test-mup.sh)で一部を別々に確認。同時に動く複数UE群と比較用の全経路キャプチャーを網羅した組み合わせは未提供 |
| V-08 | ライブのトポロジー・状態と定期UE→DN ping表示 | 実装済み | 実機記録あり | [ダッシュボード実装](../internal/dashboard/collector.go)、[運用](operations.ja.md)、[検証記録](validation-summary.ja.md)。5秒ごとの状態収集とICMPプローブ1回。経路表示は収集状態からの推定で、各パケットの通過証拠ではない。過去にローカルと別Tailnet端末からのアクセスを確認済み。この文書が監視するわけではない |

## 再現性と運用

| ID | 機能 | 実装状況 | 検証状況 | 証拠と制限 |
|---|---|---|---|---|
| O-01 | 6 VMの配備と冪等な再適用 | 実装済み | 実機記録あり | [移植可能な設定](portable-configuration.ja.md)、[Ansible入口](../ansible/site.yml)、[検証サマリー](validation-summary.ja.md)。Composeはcore VM内で動作し、外側のKVM/libvirtトポロジーを代替しない |
| O-02 | 同じ物理x86-64ホスト上の独立したクリーンOS | 実装済み | 実機記録あり | [ネストKVM手順](clean-room-reproduction.ja.md)、[検証記録](validation-summary.ja.md)。クリーンOSでベースライン・MUP・リース・1callと変更なし再適用に合格。元ラボの一時停止を伴った |
| O-03 | 別の物理ホストでの完全な転送再現 | 一部実装 | 未検証 | [移植設定](portable-configuration.ja.md)と[preflight](../scripts/preflight.sh)は存在。[検証記録](validation-summary.ja.md)は2台目の物理マシンでの完全な転送結果を証明しない |
| O-04 | macOS・Apple Silicon ARM64のネイティブ構成 | 未実装 | 未検証 | [preflight](../scripts/preflight.sh)と[VM配備](../infra/libvirt/create-vms.sh)はLinux x86-64/KVMが必要。Macからのダッシュボード閲覧はネイティブなラボ再現ではない |
| O-05 | OS・カーネル・パッケージまで含む完全オフライン・固定入力の再現 | 一部実装 | 未検証 | [ロック](../config/versions.lock.yml)、[サプライチェーンの限界](supply-chain.ja.md)、[共通ロール](../ansible/roles/common/tasks/main.yml)。ソース・ツール・イメージ入力は固定するが、配備時に依存やOSパッケージを取得し、apt/カーネル環境全体を凍結するものではない |
| O-06 | コントローラーAPIの認証・認可とmTLS | 未実装 | 未検証 | [HTTPサーバー](../cmd/mup-controller/main.go)、[コントローラー検査](../internal/controller/controller.go)。信頼するアドレスへのバインドとobserver IDの一致検査は、暗号学的認証やRBACではない。隔離された信頼ネットワーク内で使用する |
| O-07 | セッション・抑止状態の永続化と複製型コントローラーHA | 未実装 | 未検証 | [コントローラーの状態](../internal/controller/controller.go)はメモリー内。操作による抑止をスナップショット受信後も保持するのは同じプロセスの存続中だけ。新しい観測は永続化・状態複製ではない |
| O-08 | 包括的な資源上限と運用障害への強化 | 一部実装 | 未検証 | [サーバー設定](../cmd/mup-controller/main.go)にはヘッダータイムアウトなどの基礎対策がある。[pendingの強化](research-backlog.ja.md)は、クォータ・過負荷・配備全体のセキュリティ検証が完了したという意味ではない |
| O-09 | 公開ソース一覧・日英文書・レビュー済みキャプチャーの検査 | 実装済み | オフライン試験のみ | [書き出し検査](../scripts/export-source.py)、[文書試験](../tests/test_documentation.py)、[キャプチャー試験](../tests/test_reviewed_pcaps.py)、[配布手順](source-distribution.ja.md)。ソース候補を検査するもので、公開リリース、依存全体の安全性、実際のパケット転送の証明ではない |

## この文書の更新方法

- 行IDを維持し、英語・日本語を同時に更新します。いずれの状況を変更するときも、実コードと正確な試験条件を確認してください。
- 新しい実機での主張には、観測日、リビジョン、ロック、カーネル、コマンド、結果を記録します。[検証サマリー](validation-summary.ja.md)には秘匿情報を除いた要約を追加し、生のホスト識別情報・運用ログ・未レビューのキャプチャーは非公開に保ちます。
- 単体・構文・文書ハッシュ・配布検査だけで実機検証済みに変更しません。過去の合格は、別ホストや変更後のランタイムで現在も合格する保証ではありません。
- 受け入れ基準は[pendingのバックログ](research-backlog.ja.md)で管理します。ベースラインの1ケースが通っただけで研究分野全体を完了にしません。
