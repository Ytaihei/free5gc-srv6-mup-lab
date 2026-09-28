# 第三者ソフトウェアと再配布に関する注意事項

[English](THIRD_PARTY_NOTICES.md) | 日本語

このリポジトリはラボ自動化、設定、独自ソースを含みます。以下の実行時プロジェクトの完全なソース、コンテナー/VMイメージ、コンパイル済みバイナリは同梱しません。プロビジョニング時に各上流から取得します。

| 構成要素 | 固定ベースライン | 使用方法 | 上流ライセンス |
|---|---|---|---|
| Vinbero | v0.1.1 / 0d7ccf3c798fffa000ccee82e1fd8c9d86525099 | PE構築時にcloneしてパッチ適用 | [Apache License 2.0](https://github.com/takehaya/Vinbero/blob/0d7ccf3c798fffa000ccee82e1fd8c9d86525099/LICENSE) |
| free5gc-compose | v4.2.3 | core構築時にclone | [Apache License 2.0](https://github.com/free5gc/free5gc-compose/blob/e4e2acebad6d6a8c49cfb03a97d9a09dd40c55c7/LICENSE.txt) |
| gtp5g | v0.9.5 | clone・ビルドし、カーネルモジュールとして導入 | [GNU GPL version 2](https://github.com/free5gc/gtp5g/blob/973d001b25832c5a8e8d34f6381eb0c705fb523d/LICENSE) |
| UERANSIM | v3.3.0 | RAN VM上でclone・ビルド | [GNU Affero GPL version 3](https://github.com/aligungr/UERANSIM/blob/6bf5a1a96aaef6ae8778b9d8b477ac6e2bbf8156/LICENSE) |
| MongoDB Community Server | 参照構成: 上流の4.4、新規compact: 固定した8.0.32 | コンテナーイメージとして取得。既存データの移行は明示的に実施 | [Server Side Public License](https://www.mongodb.com/legal/licensing/server-side-public-license) |

third_party/vinberoの互換パッチは、固定したApache-2.0ソースへの変更です。出所と目的はthird_party/vinbero/README.md、日本語では[Vinbero統合](third_party/vinbero/README.ja.md)を参照してください。Apache-2.0の正式な全文はLICENSEにあります。

上記の固定ソースのライセンスは2026-09-07に確認しました。そのVinberoツリーに独立したNOTICEはありません。どのソース候補でも帰属表示、パッチの変更記録、Apacheライセンスを保持してください。UERANSIMの[固定README](https://github.com/aligungr/UERANSIM/blob/6bf5a1a96aaef6ae8778b9d8b477ac6e2bbf8156/README.md)には商用ライセンスも案内されていますが、このラボはAGPLの選択肢を記録しており、商用ライセンスの権利を主張しません。

Go依存関係はgo.mod/go.sumのバージョンとチェックサムで解決します。それらのソースやライセンスファイル自体は、このリポジトリへコピーしていません。コンパイル済みGoバイナリを公開する前には、全依存関係のライセンス一覧を生成し、必要な通知を配布物へ含めてください。

## リリース方針

コンパクト構成のイメージレビューは、専用のライセンス分類と非公開資料収集を使用します。依存更新、公開されている既定鍵・テスト用鍵の由来、必要なソース・通知、証跡の不足については[イメージ配布レビュー](docs/image-distribution.ja.md)を参照してください。GPL/LGPLを分類できること自体は、イメージの配布承認ではありません。

配布マイルストーン完了まではソースのみを公開します。qcow2、取得したUbuntuイメージ、Dockerイメージアーカイブ、パッチ済みVinberoバイナリ、UERANSIMバイナリ、gtp5gモジュールをリリースに添付しないでください。

ソースのみの梱包は、取得した実行時ソフトウェアの再配布や、第三者ソフトウェアのサービス提供の承認ではありません。特にAGPL/SSPLにはネットワーク・サービスに関する規定があります。第三者に公開する前に実際の配備形態を評価してください。今回の確認はリポジトリのソーススナップショットに限られ、アプライアンス全体の確認や法的な適合認証ではありません。[ソース配布](docs/source-distribution.ja.md)を参照してください。

今後バイナリやイメージを配布する場合は、次を含めます。

- 当該配布物そのもののSBOM
- 含まれるソフトウェアで必要な全ライセンス・通知
- 必要な場合の対応するソースまたは要件を満たすソース提供の申し出
- ビルドに使用したバージョン/ダイジェストのロックマニフェスト

この一覧は技術上の記録であり、法的助言ではありません。バイナリ配布や第三者ソフトウェアのサービス提供前に、適用される義務を確認してください。
