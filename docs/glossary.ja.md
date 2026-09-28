# ラボの用語集

[English](glossary.md) | 日本語

リポジトリ内の略語を調べる入口です。学習用の短い説明であり、仕様書の代わりではありません。右列はこのラボの使い方で、すべてのMUP構成に当てはまるとは限りません。対応・検証範囲は[機能の対応状況](feature-status.ja.md)、パケットの流れは[アーキテクチャ](architecture.ja.md)を参照してください。確認日: 2026-09-15。固定した標準文書の版は変更していません。

## 5Gのノードと識別子

| 用語 | 意味 | このラボでの使い方 |
|---|---|---|
| UE | User Equipment。モバイル端末 | UERANSIMが1加入者を模擬し、通信インターフェースは `uesimtun0` |
| gNB / RAN | 5G基地局 / 無線アクセス網 | `lab-ran` 内でgNBとUEを模擬し、物理的な無線機は不要 |
| AMF | Access and Mobility Management Function。アクセス・移動管理 | free5GCがUE登録やアクセス・移動の制御信号を処理 |
| SMF | Session Management Function。セッション管理 | free5GCがN4/PFCPで通常UPFを制御 |
| UPF | User Plane Function。ユーザープレーン機能 | PFCPのセッション制御と通常のフォールバック転送用に残す。MUP迂回はUPFの削除ではない |
| DN / DNN | Data Network / その識別名 | `lab-dn` が試験終端、`internet` が設定したDNN。実インターネット接続を意味しない |
| PDU Session | Protocol Data Unit session。UEのデータ接続 | Registrationとデータセッション確立は1call内の別の段階 |
| SUPI / IMSI | 恒久的な加入者識別子 / その形式の一つ | PFCPに識別情報があればSUPIプレフィックス選択が可能。UEのIPアドレスとは別 |
| SUCI | 加入者識別情報を秘匿するための形式 | サンプルはテスト加入者とNULL保護方式を使用。本番の匿名化済みデータとして扱わない |
| PLMN | MCCとMNCで表すモバイルネットワーク識別子 | 公開fixtureの値は模擬網を示し、実際の事業者への接続ではない |
| S-NSSAI | Single Network Slice Selection Assistance Information。スライス識別情報 | fixtureは1スライスを選択。複数スライスの検証ではない |
| UL / DL | 上り / 下り | UE→DN / DN→UE。PEのインターフェースではなくUEを基準にする |

モバイル網の文脈は [RFC 9433 §2.1](https://www.rfc-editor.org/rfc/rfc9433.html#section-2.1) と、その3GPP参照文書を確認してください。実際のfixtureと保持する項目は[ラボ設定](../config/lab.example.yml)と[セッションモデル](../internal/model/session.go)にあります。

## インターフェースとセッションのプロトコル

| 用語 | 意味 | このラボでの使い方 |
|---|---|---|
| N2 / NGAP / SCTP | gNB–AMFの制御区間、アプリケーションプロトコル、トランスポート | 登録関連信号やPDUリソース設定を運ぶ。N2の例示PCAPで確認可能 |
| N3 / GTP-U | gNB–UPFのユーザー区間 / ユーザーパケットのトンネル | N3 access/coreに分け、IPv4 GTP-U経路にMUP PE（N3／Interwork側）を配置 |
| N4 / PFCP | SMF–UPFの制御区間 / Packet Forwarding Control Protocol | observerが双方向をsnoopする。PFCPを終端せずセッション要求も送らない |
| N6 / N9 | UPF–DN / UPF–UPFのユーザー区間 | N6は配備済み。複数UPFのN9ローミング構成は未提供 |
| TEID / F-TEID | 32ビットのトンネル終端識別子 / 終端を含めたトンネル情報 | GTP終端アドレスと方向を組み合わせる。番号単独は全網で一意ではない |
| SEID / F-SEID | 64ビットのPFCPセッション終端識別子 / そのアドレスを含む形式 | CP/UPのSEIDでPFCPセッションを追跡。UL/DL TEIDとは異なる |
| PDR / PDI | Packet Detection Rule / Packet Detection Information | パケット照合、UEアドレス、DNN、トンネル情報などをobserverが読み取る |
| FAR | Forwarding Action Rule。転送動作の規則 | observerがRAN側の転送終端と下りTEIDを抽出 |
| QER / QFI | QoS Enforcement Rule / QoS Flow Identifier | 選択したQFIをセッションとT1で運ぶ。これだけではQoSのスケジューリングや帯域制御にならない |
| QoS | Quality of Service。通信に応じた品質上の扱い | QFIはフローを識別する値であり、このラボがフローごとの完全なサービス品質を保証するわけではない |
| Snapshot / observer lease | 観測状態全体の報告 / 鮮度の期限 | 変更時と5秒ごとに送信。15秒の失効で経路を撤回し、PFCPセッション自体は削除しない |

PFCPの実装範囲は[パッシブ状態リーダー](../internal/pfcpstate/state.go)が規定します。上流PFCPライブラリが解析できる全フィールドへの対応ではありません。[ノードの役割](architecture.ja.md#各ノードの役割)と[運用手順](operations.ja.md)も参照してください。

## MUPとBGP経路

| 用語 | 意味 | このラボでの使い方 |
|---|---|---|
| MUP / SRv6 MUP | Mobile User Planeアーキテクチャ / SRv6で実現したデータプレーン | セッション状態を経路情報へ変換。対応プロファイルはIPv4 UEとDirect Segment |
| MUP-C | MUPコントローラー | `lab-mupc` がポリシーを適用しセッション経路を生成。observerは `lab-core` に分離配置 |
| MUP PE | MUP対応Provider Edge | 両方のVinberoノードがMUP PE。MUP PE（N3／Interwork側）のIDは `tpe`、MUP PE（N6／Direct側）のIDは `npe` |
| T-PE / N-PE | 旧来のラボ内の別名で、MUP仕様の用語ではない | 説明には上記のMUP PE表記を使う。`tpe`／`npe` と `lab-tpe`／`lab-npe` は設定・API・CLI・VMの互換識別名としてのみ保持 |
| MUP-GW | 初期のMUP Draftにある歴史的なゲートウェイ名称 | N3側の現在の表示名には使わず、MUP PE（N3／Interwork側）とする。別のSMFやPFCP終端ではない |
| Direct / Interwork Segment | ネットワーク・サービスへの直接接続 / モバイル網のユーザープレーンプロトコルとの相互接続 | このラボではN6／DNがDirect、N3／GTP-UがInterwork。InterworkにはN9も収容でき、1台のMUP PEで両種類を扱える。MUPのSegmentはSegment RoutingのSegmentと同じ概念ではない |
| ISD / DSD | Interwork / Direct Segment Discovery経路 | MUP PE（N3／Interwork側）とMUP PE（N6／Direct側）が、セッション経路を解決するための発見情報を広告 |
| T1 / T1ST / ST1 | Type 1 Session Transformed経路 | UEプレフィックス、RAN終端、DL TEID、QFIを運び、MUP PE（N6／Direct側）の下り転送に使う |
| T2 / T2ST / ST2 | Type 2 Session Transformed経路 | UPF終端とUL TEIDを運び、MUP PE（N3／Interwork側）がDirect Segmentを解決して上り転送に使う |
| BGP / iBGP / RR | 経路制御プロトコル / 同一AS内のピアリング / ルートリフレクター | MUP-CはT1/T2生成に加え、両PE間のdiscovery経路を反映 |
| AFI / SAFI | Address Family Identifier / Subsequent Address Family Identifier | セッション経路はAFI 1、SAFI 85。AFI 1でもSRv6アンダーレイはIPv4という意味ではない |
| NLRI | Network Layer Reachability Information。経路到達性の情報 | BGP内の型付き経路情報。セッション経路のUPDATEはユーザーパケットではない |
| RD / RT | Route Distinguisher / Route Target | RDは経路の区別、RTは取り込み範囲の制御。同じ `65000:100` のような表記でも別フィールド |
| EC / TLV | Extended Community / type-length-value形式のフィールド | RTとDirect Segment ECを利用。Draftの追加TLV/ECプロファイルまで自動で対応するわけではない |

表記は [MUP Architecture Draft §2–4.1](https://datatracker.ietf.org/doc/html/draft-ietf-dmm-mup-architecture-02#section-2) に合わせます。[初期DraftのMUP-GW定義](https://datatracker.ietf.org/doc/html/draft-mhkk-dmm-srv6mup-architecture-02#section-2)は過去の資料を読むためだけに残し、現行の別種のPEを意味するものとしては使いません。

次の4種は名称と番号を混同しやすいため注意してください。

| ワイヤー上のRoute Type | 経路 |
|---|---|
| 1 | ISD |
| 2 | DSD |
| 3 | T1 / ST1 |
| 4 | T2 / ST2 |

Architecture Type 1（3GPP-5G）は別フィールドです。T1のRoute Typeは1ではありません。固定した [MUP architecture Draft](https://datatracker.ietf.org/doc/html/draft-ietf-dmm-mup-architecture-02)、[MUP SAFI Draft §3.1](https://datatracker.ietf.org/doc/html/draft-ietf-bess-mup-safi-01#section-3.1)、[multiprotocol BGP](https://www.rfc-editor.org/rfc/rfc4760.html)、[RD/RTの定義](https://www.rfc-editor.org/rfc/rfc4364.html)を参照してください。ラボでの対応は[アドレス計画](address-plan.ja.md)と[経路ビルダー](../internal/bgp/speaker.go)にあります。

## SRv6とパケット転送

| 用語 | 意味 | このラボでの使い方 |
|---|---|---|
| SRv6 / SID | Segment Routing over IPv6 / IPv6アドレスで表すセグメント識別子 | UEの中身がIPv4でも、外側の転送はIPv6 |
| Locator / function / arguments | 経路制御されるSIDプレフィックス / ローカル処理 / その引数 | このプロファイルのSID配置は固定。RANアドレス・TEID・QFIを引数に載せられる |
| SRH | Segment Routing Header。IPv6 Routing Type 4 | SRv6の例示PCAPで確認可能。すべてのSRv6カプセル化が同じヘッダー配置を必要とするわけではない |
| H.Encaps | ヘッドエンドでカプセル化する動作 | 選択された内側通信に外側IPv6/SRv6を付ける |
| End.DT4 / VRF | IPv4の脱カプセル化後に指定テーブルで検索する動作 / 経路制御のコンテキスト | MUP PE（N6／Direct側）は上りを脱カプセル化し、テナントの経路テーブル100へ渡す |
| End.M.GTP4.E | IPv4 GTP-Uを出力するモバイル動作 | MUP PE（N3／Interwork側）がgNBへの下りGTP-Uを再構成。上りのカプセル化動作ではない |
| Args.Mob.Session | SID配置に載せるモバイルセッション引数 | 配備プロファイルのオフセットは固定。任意のSID配置を扱う汎用デコーダーではない |
| eBPF / XDP | カーネル内のプログラム・マップ / 受信経路の早い段階での処理 | PEのvirtioインターフェースでVinberoがdriver-mode XDPを使用 |
| Fallback | 選択MUPエントリーが適用されない場合の通常UPF転送 | 元のUPFセッションとカーネル経路が有効であることが前提。無損失の保証ではない |

ヘッダー、ネットワークプログラミング、モバイル動作の定義は、それぞれ [RFC 8754](https://www.rfc-editor.org/rfc/rfc8754.html)、[RFC 8986](https://www.rfc-editor.org/rfc/rfc8986.html)、[RFC 9433](https://www.rfc-editor.org/rfc/rfc9433.html)を参照してください。実際のパケットの流れと固定配置は[アーキテクチャ](architecture.ja.md)と[アドレス計画](address-plan.ja.md)にあります。

## ツール・試験・配布

| 用語 | 意味 | このラボでの使い方 |
|---|---|---|
| free5gc-compose / UERANSIM | コンテナー化した5Gコア / UE・gNBの模擬実装 | Composeはcore VM内で動作。ラボ全体にはKVM/libvirtとAnsibleも必要 |
| GoBGP / Vinbero | BGPライブラリ / eBPFを使うMUP SRv6実装 | controllerとPEで異なる版を意図的に固定。ライブラリ機能の存在はラボの対応保証ではない |
| Connect RPC / mupctl | 型付きHTTP RPC / controllerのCLI | 状態読み取りと抑止操作は別。信頼する網への待ち受け限定はAPI認証ではない |
| 1call / E2E | 本ラボの登録からデータ疎通までの単一試験 / エンドツーエンド試験 | PDU SessionとICMP/HTTPを含み、音声通話やIMS/VoNR試験ではない |
| RTT / convergence | パケットの往復時間 / 状態・転送が収束するまでの時間 | ダッシュボードのping RTTではPFCP→BGPやBGP→eBPFの収束時間を測れない |
| PCAP / PCAPNG | パケットキャプチャーの形式 | 完全一致でレビューした通常PCAP 7件だけを許可。通常の取得物は非公開 |
| Fixture / golden wire fixture | 固定した試験入力 / 期待するプロトコルのバイト列 | 回帰確認には有用だが、独立した完全なRFC準拠認証ではない |
| CI / SBOM | 継続的インテグレーション / ソフトウェア部品表 | オフライン検査と依存一覧は、権限を要する実機転送試験の代わりではない |
| Idempotence / clean-OS reproduction | 再適用で変更なし / 新しいOSからの再構築 | Ansibleの変更なしrecapと新規インストールは異なる証拠 |
| Pending / unverified | 作業を保留 / 対応する検証証拠がない | どちらも未実装と同義ではない。機能一覧の2列で区別する |

続いて[運用手順](operations.ja.md)、[機能の対応状況](feature-status.ja.md)、[レビュー済みPCAPの解説](../examples/pcap/one-call/README.ja.md)を参照してください。
