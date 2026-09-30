# SRv6 MUPのアーキテクチャ

[English](architecture.md) | 日本語

略語は[用語集](glossary.ja.md)、実装の制限と対応する検証証拠は[機能の対応状況](feature-status.ja.md)を参照してください。

両端はともにMUP PEで、**MUP PE（N3／Interwork側）** と **MUP PE（N6／Direct側）** と表記します。Interwork／Directは収容する区間の種類であり、必ず分離すべき2種類の装置を意味しません。1台のMUP PEが両方を収容することもできます。N3／N6との対応はこのラボの構成で、すべてのMUP構成に共通ではありません。[MUP Architecture Draft §3–4.1](https://datatracker.ietf.org/doc/html/draft-ietf-dmm-mup-architecture-02#section-3)を参照してください。互換性のため、設定キー、Compose／APIのID `tpe`／`npe`、VM名 `lab-tpe`／`lab-npe`、既存のコマンド・環境変数名は変更しません。

## 配備プロファイル

以下の制御手順と論理的なパケット経路は、2つの配備プロファイルで共通です。`lab-*`がVM名を表すのは6VM参照構成だけで、単一VM構成では対応する役割を1台のVM内のコンテナーに配置します。どちらも物理ホストのDockerへラボを直接配備する構成ではありません。

| 項目 | 6VM参照構成 | 実験用の単一VMコンパクト構成 |
|---|---|---|
| 配置 | KVM/libvirtゲスト6台。coreゲスト内にfree5GC Compose | KVM/libvirtゲスト1台。core、observer、controller、PE、RAN、DNをComposeで配置 |
| 配備と設定 | Ansible。完全な設定の`config/lab.local.yml` | `./lab`。`config/compact.local.yml`による部分的な上書き |
| PEのデータインターフェース | virtio NIC、driver-mode XDP | veth、generic XDP。性能同等性は主張しない |
| ダッシュボード | ホスト上の収集・Webサービス、ポート`8787` | ゲスト内の収集処理・WebコンテナーとホストのSSHトンネル、ポート`8788` |
| 構築と試験 | [参照構成の運用](operations.ja.md) | [単一VMセットアップ](compact-lab.ja.md)と[ハンズオン](hands-on.ja.md)。初回は`./lab up --build` |

参照構成だけのコマンド、インターフェース名、XDPモードの検査を単一VM構成に適用しないでください。ソースは公開済みですが、構築済みイメージの配布は保留です。

<a id="各ノードの役割"></a>

## 論理的な役割と参照構成のVM名

- `lab-core`: free5GCの制御プレーンと通常のUPF。`pfcp-observer` はDockerブリッジ `br-free5gc` 上の双方向N4通信をパッシブに読み取ります。PFCPを終端せず、SMF/UPF間の交換も変更しません。
- `lab-mupc`: ポリシーエンジン、BGP MUPスピーカー、ルートリフレクター。Connect RPCでセッション全体のスナップショットを受け、T1/T2を生成・撤回し、MUP PE（N3／Interwork側）のISDとMUP PE（N6／Direct側）のDSDを両端へ反映します。
- `lab-tpe`: MUP PE（N3／Interwork側）。Vinberoが選択された上りF-TEIDに一致するGTP-UをSRv6に変換します。ローカルISDは逆方向用の `End.M.GTP4.E` を広告します。
- `lab-npe`: MUP PE（N6／Direct側）。選択された上りSRv6を `End.DT4` で終端し、UE宛ての下り通信をMUP PE（N3／Interwork側）へカプセル化します。ローカルDSDはDirect Segment `65000:1` を `End.DT4` SIDに関連付けます。
- `lab-ran` / `lab-dn`: UERANSIMのgNB+UEと、データネットワークの試験終端です。

## 制御プレーンの順序

1. SMFがUPFへN4 PFCPセッション要求を送ります。
2. オブザーバーは要求を仮保持し、対応する受理応答を得た場合だけ確定します。UEアドレス、DNN、CP/UP SEID、UPF/RAN F-TEID、QFIを再構成します。受理された変更でも情報が不完全なら、安全側に倒して適用を拒否します。
3. 状態変更直後と、その後5秒ごとに全体スナップショットを送ります。世代番号は単調増加です。
4. MUP-Cは初期ポリシー（`DNN=internet`、UEが `10.60.0.0/16` 内）に一致するセッションを選択し、T1/T2をトランザクションとして広告します。同一プロセスのGoBGPが2台のiBGPクライアント間でISD/DSDを反映するため、PE間に別のBGPセッションは不要です。ペア処理には部分的な広告失敗のローカルな巻き戻しを含みますが、両PEへの同時・不可分な適用ではありません。
5. MUP PE（N6／Direct側）はT1（RT `65000:100`）を取り込み、MUP PE（N3／Interwork側）のISDで解決してUEごとの下りheadendを生成します。MUP PE（N3／Interwork側）のEnd.M.GTP4.Eは、設定されたN3 coreアドレス `10.210.32.10` を復元GTP-Uの送信元に使います。
6. MUP PE（N3／Interwork側）はT2を取り込み、MUP PE（N6／Direct側）のDSDでDirect Segment `65000:1` を解決して、上り終端の判定と厳密なF-TEIDエントリーを生成します。
7. PFCP削除、操作による抑止、セッション変更、15秒のオブザーバーリース失効によりペアを撤回します。未知・未選択の通信はVinberoのMUPエントリーに捕捉されず、カーネル/UPF経路へ進みます。

## 選択された上りパケットの流れ

```text
UE -> gNB
   GTP-U {dst=10.210.32.10, TEID=UL}, routed through MUP PE (N3/Interwork side) 10.210.31.12
-> MUP PE (N3/Interwork side) Vinbero T2 match
   SRv6 H.Encaps {segment=fd10:2:0:1::}
-> MUP PE (N6/Direct side) End.DT4, table 100
-> N6 -> DN
```

MUP PE（N3／Interwork側）はN3 core上に `10.210.32.12` も持ちます。厳密に一致するT2がなければ、Vinberoはパケットを通過させ、カーネルが通常のUPF `10.210.32.10` へ転送します。

## 選択された下りパケットの流れ

```text
DN -> route 10.60.0.0/16 via MUP PE (N6/Direct side)
   IPv4 {dst=UE}
-> MUP PE (N6/Direct side) Vinbero T1 match
   SRv6 H.Encaps {segment=ISD SID + RAN IPv4/DL TEID/QFI args}
   source fd10:2::
-> MUP PE (N3/Interwork side) End.M.GTP4.E
   uses fixed GTP-U source 10.210.32.10 and Args.Mob.Session at byte 7
-> GTP-U -> gNB -> UE
```

ここに示した下り送信元は、[レビュー済みキャプチャー](../examples/pcap/one-call/README.ja.md)で観測したロケーター由来のアドレスであり、条件付きの送信元埋め込み用プレフィックスとは異なります。この区別と、復元GTP-Uで使う固定送信元は[アドレス計画](address-plan.ja.md)を参照してください。

T1がなければ、MUP PE（N6／Direct側）のカーネル経路がUEプレフィックスを通常のUPF `10.210.6.10` へ送ります。そのため、PFCPセッションを変更せず、経路撤回だけで元のfree5GCデータ経路に戻ります。

## 障害時の動作

- 受理応答前にPFCP要求を公開しません。
- 古いスナップショット世代や想定外のobserver IDを拒否します。
- T1/T2の片側だけの広告はロールバックします。
- F-TEIDの変更では両方向をトランザクションとして置き換えます。
- オブザーバー喪失から15秒で期限切れとなり、広告中の全セッションを撤回します。
- 操作者による抑止はセッション単位で、MUP-Cプロセスの存続中は新しいスナップショットを受けても保持されます。
- VinberoとMUP-CのAPIはラボまたはループバックのアドレスだけで待ち受け、物理LANにはMUPサービスを公開しません。
- 6VM参照構成は、インターフェース間のredirectを送信デバイスへ確実に送り出すため、virtio NICにXDP driver modeで接続します。単一VM構成はコンテナーのvethでgeneric XDPを使用します。

## 可観測性の構成

どちらの構成でもダッシュボードはMUPの制御経路には含まれません。以下の配置とSSH/systemdによる収集図は**6VM参照構成**のものです。`mup-dashboard`は仮想化ホスト上で動作し、5秒ごとに状態を収集してU-PlaneのICMPプローブを1回送ります。

```text
MUP-C Connect API ── status + controlled PFCP sessions ──┐
lab-core/RAN/PE/DN ── SSH systemd active state ──────────┤
MUP PEs (both sides) ─ SSH Vinbero JSON routes/counters ─┤
UE uesimtun0 ─────── ICMP echo to DN 10.210.6.15 ───────┤
                                                         v
                                               immutable JSON snapshot
                                                         |
                                     loopback + tailscale0 HTTP dashboard
```

**単一VM構成**では、ゲスト内の収集処理がcontroller/PEの状態とコンテナーの稼働状態を読み、JSONスナップショットを保存し、定期的なUEプローブを実行します。別の非特権WebコンテナーがUnixソケットでスナップショットを配信し、ホストは監視付きSSHトンネルで公開します。既定はループバックのみで、Tailnetは任意です。WebコンテナーにはDockerソケットもラボネットワークへのアクセスも与えません。パケット証跡試験では定期プローブを排他制御し、キャプチャーへの混入を防ぎます。[単一VMのダッシュボード運用](compact-lab.ja.md)と[ハンズオンの確認](hands-on.ja.md)を参照してください。

UIがMUP経路を強調するのは、オブザーバーリースが有効で、1件以上の制御対象セッションが広告され、T1/T2ペアが存在する場合だけです。それ以外は通常のUPFフォールバック経路を強調します。プローブ成功時は選択経路に要求・応答を表示してRTTを示し、タイムアウト時はU-Planeを赤くします。

収集処理は `suppress`、`resume`、`reconcile` やVinberoの変更APIを呼びません。古い、または取得不能なスナップショットは監視の失敗であり、ラボの正常性を示しません。参照構成のホスト側収集処理は到達不能なゲストを報告できますが、単一VMのダッシュボードにはゲストとトンネルの稼働が必要です。
