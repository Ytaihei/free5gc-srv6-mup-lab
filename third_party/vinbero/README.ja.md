# Vinberoの統合

[English](README.md) | 日本語

このラボは上流Vinberoの `v0.1.1`（`0d7ccf3c798fffa000ccee82e1fd8c9d86525099`）を固定します。Ansibleが `patches/0001-gobgp-v4.8-mup-draft01.patch` を適用し、固定Cソースから埋め込みeBPFオブジェクトを再生成して `go mod tidy` を実行し、デーモンとCLIをソースからビルドします。パッチはVinberoのGoBGP 4.7 forkを上流GoBGP 4.8へ置き換え、v4.8 APIが要求するDirect Segment MUP拡張コミュニティのsubtypeを明示します。

現在のMUP-Cはルートの `go.mod` でGoBGP 4.9.0を選択し、PEは意図的に4.8.0を維持します。この混在ベースラインでは、コントローラーに固定した4.8 UPDATE fixtureだけでなく、実機のMUP/撤回/リース/1call試験が必要です。共通Goコンパイラーは `config/versions.lock.yml` で別に固定します。

ホスト側検証からは、特権が必要なeBPF実行時試験を意図的に除外しています。これらは `CAP_BPF`、`CAP_SYS_ADMIN`、memlock変更が必要で、配備後のMUP PE（N3／Interwork側）/MUP PE（N6／Direct側） VM内で実行します。

VinberoはApache License 2.0で配布されています。このディレクトリのパッチは上記コミットに対する変更です。正式な全文はルートの `LICENSE`、配布範囲は `THIRD_PARTY_NOTICES.md` に記録されています。

パッチの変更対象は `go.mod`、`pkg/bgp/gobgp/advertise_mup.go`、`pkg/bgp/gobgp/decode_mup_test.go` であり、Vinbero全体のソースやコンパイル済みeBPFオブジェクトは同梱しません。2026-09-07のソース確認では、固定ツリーにLICENSEはあり、独立したNOTICEはありませんでした。どのソーススナップショットでも、この変更・出所の記録とルートのライセンス/通知を保持してください。パッチ適用済みの実行時ツリー全体やバイナリは別の配布物であり、別途レビューが必要です。
