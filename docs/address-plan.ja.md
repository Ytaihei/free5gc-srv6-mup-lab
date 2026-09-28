# アドレスとMUP識別子の計画

[English](address-plan.md) | 日本語

## ネットワーク

| セグメント | プレフィックス | 接続先 |
|---|---|---|
| 管理/BGP | `192.168.123.0/24` | core `.10`、RAN `.11`、MUP PE（N3／Interwork側） `.12`、MUP PE（N6／Direct側） `.13`、MUP-C `.14`、DN `.15` |
| N2 | `10.210.2.0/24` | AMF `.10`、gNB `.11` |
| N3 access | `10.210.31.0/24` | gNB `.11`、MUP PE（N3／Interwork側） `.12` |
| N3 core | `10.210.32.0/24` | UPF `.10`、MUP PE（N3／Interwork側） `.12` |
| SRv6アンダーレイ | `2001:db8:100:10::/64` | MUP PE（N3／Interwork側） `::12`、MUP PE（N6／Direct側） `::13` |
| N6 | `10.210.6.0/24` | UPF `.10`、MUP PE（N6／Direct側） `.13`、DN `.15` |
| UEプール | `10.60.0.0/16` | 最初の試験UE `10.60.0.1` |

すべてのユーザープレーンブリッジを物理LANから分離します。ホストのネットワーク、core VM外のDocker既定ネットワーク、Tailscaleのアドレスは流用しません。

## SRv6とBGP MUPの値

| 項目 | 値 |
|---|---|
| MUP PE（N3／Interwork側）ロケーター | `fd10:1::/48` |
| MUP PE（N3／Interwork側） Interwork Segment SID | `fd10:1:0:1::` |
| MUP PE（N3／Interwork側） `End.M.GTP4.E` トリガー | `fd10:1::/56` |
| Args.Mob.Sessionオフセット | バイト `7` |
| IPv4送信元の抽出位置 | ビット `64` |
| MUP PE（N3／Interwork側） ISD RD / RT | `65000:12` / `65000:100` |
| MUP PE（N6／Direct側）ロケーター | `fd10:2::/48` |
| MUP PE（N6／Direct側） Direct Segment SID | `fd10:2:0:1::/128`（`End.DT4`） |
| MUP PE（N6／Direct側） DSD originator | `10.210.255.13/32` |
| MUP PE（N6／Direct側） DSD RD / RT | `65000:13` / `65000:200` |
| Direct Segment拡張コミュニティ | `65000:1` |
| 下り外側送信元プレフィックス | `fd10:2:100::/64` |
| セッションRD | `65000:10` |
| T1ルートターゲット | `65000:100` |
| T2ルートターゲット | `65000:200` |
| T2終端アドレス長 | `64` ビット（IPv4終端 + 厳密な32ビットTEID） |

T1はUEの `/32`、RAN終端、下りTEID、QFIを運びます。T2はUPF終端、上りTEID、Direct Segment拡張コミュニティを運びます。T1/T2には固定のサービスSIDを意図的に含めません。各PEが、一致するルートターゲットの範囲内のISD/DSD経路から適切なSIDを解決します。
