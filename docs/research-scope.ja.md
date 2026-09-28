# 研究範囲と主張できる範囲

[English](research-scope.md) | 日本語

制御プレーンの成功をE2EのMUP転送成功と混同しないよう、実証の水準を分けます。

| 水準 | 主張 | 証拠 |
|---|---|---|
| L0 | 通常のfree5GC PFCP/GTP-UセッションでUPF経由のUE通信が可能 | セッション抑止状態の `make test-baseline` |
| L1 | パッシブN4オブザーバーがTEID手入力なしで受理PFCP状態を再構成 | `mupctl sessions`、オブザーバー試験、実際のPFCP値 |
| L2 | 選択したPFCP状態がT1/T2 MUP SAFIペアの広告・撤回になる | Go試験、`mupctl`、VinberoのMUP経路状態 |
| L3 | 受信経路がVinbero eBPFに反映され、選択UL/DLがUPFを迂回 | `MUP_ENABLE=1 make test-mup` とVinberoマップ/状態検査 |
| L4 | 障害、移動、時間特性を再現可能に評価 | `MUP_ENABLE=1 make test-lease` と残る障害試験・キャプチャー |

L0–L3を実装しています。ただし配備報告で各水準を主張できるのは、報告するVM群で実際に該当試験を通した場合だけです。ローカルの単体試験や構文検査だけではPFCPキャプチャーも転送も証明できません。

機能ごとの実装と検証の2軸は[機能の対応状況](feature-status.ja.md)、用語は[用語集](glossary.ja.md)を参照してください。

次の研究段階はL4です。

1. PFCP→BGP、BGP→eBPF、最初のパケットの収束時間を別々に測る。
2. 完了: `make test-lease` によるリース失効・撤回・フォールバック・復旧の自動化。
3. MUP-Cと各PEのデーモンを独立に再起動し、損失と状態再構成を記録する。完了済みのPEネットワーク再設定・近隣情報復旧試験は、このデーモン障害の組み合わせとは別。[実行記録](validation-summary.ja.md)を参照する。
4. 未知TEID、拒否PFCP応答、不完全な変更、QFI変更、UE再登録、gNB/UPF終端変更を試験する。単一UEの再登録は[1callスクリプト](../scripts/test-one-call.sh)で確認済み。拒否・不完全PFCPには単体試験があるが、網羅的な実機障害試験ではない。
5. 2台目の選択UEと、明示的に選択しないポリシー群を追加する。
6. 選択通信はUPFを迂回し、フォールバック通信は迂回しないことを示す比較キャプチャーを完成させる。[7件の1call PCAP例示](../examples/pcap/one-call/README.ja.md)には選択経路のN3 access・SRv6・N6を含むが、選択・非選択UE群とフォールバックの全組み合わせを網羅してはいない。

規範文書と実装の基準は次のとおりです。

- `draft-ietf-dmm-mup-architecture-02`
- `draft-ietf-bess-mup-safi-01`
- RFC 9433のSRv6 mobile user-plane behavior
- MUP-CのGoBGP v4.9.0と両Vinbero PEのv4.8.0（意図的なMUP AFI/SAFI混在相互接続ベースライン）
- Vinbero v0.1.1と `third_party/vinbero` の管理対象互換パッチ

MUPの2文書はInternet-Draftです。各実験報告に、文書の正確な版、GoBGP、Vinberoコミット、カーネル、試験スクリプトのリビジョンを記録してください。

コントローラー版は `go.mod`、PE版はVinberoパッチで指定します。旧 `gobgp_version` Ansible変数は公開ラボの有効なロールでは未使用です。コントローラーのType 1/2 UPDATEバイト列は `internal/bgp/wire_compat_test.go` の固定4.8.0 fixtureと照合します。変更後は実機のMUP、撤回、リース復旧、1call試験も必要であり、単体fixtureだけではPE転送の相互接続性を示せません。

今後の実装分野と受け入れ基準は[研究バックログ](research-backlog.ja.md)にあります。実機試験に合格するまでは計画であり、上記の実証済み水準を拡張しません。
