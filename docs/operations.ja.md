# 運用手順

[English](operations.md) | 日本語

## 初回インストールと再適用

インストール前に[移植可能な設定](portable-configuration.ja.md)を読み、ローカル設定を準備してください。以下のゲスト名・ユーザー・アドレスは既定のサンプルトポロジーに対応します。

ホスト依存パッケージのインストールには管理者権限が必要です。

```bash
cd free5gc-srv6-mup-lab
sudo LAB_HOST_USER="$USER" ./scripts/install-host-deps.sh
./scripts/install-go-toolchain.sh
make preflight
```

`libvirt` と `kvm` グループを有効にするため、一度ログアウトして再ログインします。その後 `make preflight` を再実行してください。これは読み取り専用で、不足コマンド、グループ所属、KVMアクセス、メモリー、ディスク容量の警告を報告します。続いて隔離ラボを構築または再適用します。

```bash
make check
make networks
./infra/libvirt/create-vms.sh
ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml
```

`create-vms.sh` は既存ゲストを破壊しません。再適用ではMUP PE（N3／Interwork側）にN3 coreを追加し、MUP PE（N6／Direct側）のデータ側NICをN3 coreからN6へ移します。構築済みトポロジーを変更する場合は、先に `virsh domiflist` を確認してください。

再適用でfree5GCが再起動した場合、AnsibleはパッシブPFCPオブザーバーの準備後にだけgNBとUEを再起動します。これにより、UERANSIM単独ではAMF再起動をまたいで復元できないSCTP/PFCP状態を更新します。オブザーバー再起動やバイナリ更新でも、キャプチャー開始後に既存RANを再接続してPFCP状態を再取得します。変更のない実行ではRANを再起動しません。新規構築では未インストールのRANユニットをスキップし、後段のUERANSIM playで起動します。

## 検証

最初に従来のUPF経路を試験します。

```bash
make test-baseline
```

PFCP由来のUEセッションを待ち、MUP経路を抑止して、通常UPF経由のUE–DN間ICMP/HTTPを試験します。終了時には、それまでのセッション選択可能状態を復元します。

経路駆動のバイパス試験は明示的に有効化します。

```bash
MUP_ENABLE=1 make test-mup
```

観測済みセッションを再開し、T1/T2ペアを待ち、VinberoのMUP PE（N6／Direct側）下り/MUP PE（N3／Interwork側）上りマップを検査します。UE通信の試験後にペアを撤回し、同じPFCPセッションがUPFへフォールバックすることを確認してからMUP状態を復元します。

通信に影響するオブザーバーリース試験も明示的に実行します。

```bash
MUP_ENABLE=1 make test-lease
```

オブザーバーを停止し、15秒後のリース失効とMUP撤回を確認します。通常UPF経路の利用可能性を確認した後、オブザーバーとUEを再起動し、新しいPFCP状態でバイパスを復旧します。UEアドレスが変わる場合があります。

診断コマンドの例です。

```bash
ssh ubuntu@192.168.123.14 sudo mupctl status
ssh ubuntu@192.168.123.14 sudo mupctl sessions
ssh ubuntu@192.168.123.12 sudo systemctl status vinbero vinbero-bootstrap
ssh ubuntu@192.168.123.13 sudo systemctl status vinbero vinbero-bootstrap
ssh ubuntu@192.168.123.10 sudo systemctl status free5gc-lab pfcp-observer
```

## ライブダッシュボード

ホスト側ダッシュボードをビルド・インストール・有効化・再起動します。

```bash
cd free5gc-srv6-mup-lab
make dashboard-install
systemctl --user status srv6-mup-dashboard
```

接続先は次のとおりです。

```text
http://127.0.0.1:8787/
http://<tailscale-hostname>:8787/
```

後者はTailnetからのみ接続できます。Tailscaleの転送はWireGuardで暗号化されますが、アプリケーション自体は平文HTTPで、物理LANではなく現在の `tailscale0` IPv4アドレスだけにバインドします。サービス再起動ごとにアドレスを解決するため、将来Tailscaleアドレスが変わっても再起動時に追従します。

状態確認には次を使います。

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/api/state | jq .
ss -ltnp | grep ':8787'
journalctl --user -u srv6-mup-dashboard -f
```

コントローラーとPEの収集は読み取り専用です。MUP-C Connect APIと、systemd状態・Vinbero JSONカウンター/経路を取得する固定SSHコマンドを使います。さらに5秒ごとにUEの `uesimtun0` からDN `10.210.6.15` へICMP echoを1回送ります。成功時はトポロジーに要求/応答とRTTを表示し、失敗時はアニメーションを止めてU-Planeを赤くします。SSHは既存の `~/.ssh/id_ed25519` によるラボアクセスを使い、60秒間多重化します。

Tailnet所有者がServeを有効にすると、Tailscale Serveでノード名の証明書とHTTPSを提供できます。有効化後もループバック待ち受けを維持して、次を実行します。

```bash
tailscale serve --bg --yes 8787
tailscale serve status
```

直接の `tailscale0:8787` 待ち受けは、管理設定変更が不要な代替手段として残ります。

### UEの1callを観察する

画面を開き、ラボUEを一時切断する試験を明示的に実行します。

```bash
cd free5gc-srv6-mup-lab
ONE_CALL_ENABLE=1 make test-one-call
```

画面では次の順序を期待します。

1. UEのswitch-off deregistrationで `uesimtun0` が消え、PFCPセッションとMUP経路が0になり、`lab-ran` がdegradedになります。
2. 新しいInitial RegistrationとPDU SessionによりUEアドレス/F-TEID状態を生成します。オブザーバーが選択し、MUP-CがT1/T2をペアで広告します。
3. MUP経路が有効になり、ICMP/HTTP通信でMUP PE（N3／Interwork側）とMUP PE（N6／Direct側）の `REDIRECT` カウンターが増えます。

各観察状態の既定待機は、画面更新間隔5秒より長い8秒です。次のように変更または無効化できます。

```bash
OBSERVE_SECONDS=15 ONE_CALL_ENABLE=1 make test-one-call
OBSERVE_SECONDS=0 ONE_CALL_ENABLE=1 make test-one-call
```

中断やアサーション失敗でも、スクリプトは必ず `ueransim-ue` の再起動を試みます。成功時は登録済みUEとMUP経路を有効なまま残します。

Vinbero CLIはループバック限定APIを使用します。

```bash
sudo env VINBERO_SERVER=http://127.0.0.1:8080 vinbero mup list
sudo env VINBERO_SERVER=http://127.0.0.1:8080 vinbero headend-v4 list
sudo env VINBERO_SERVER=http://127.0.0.1:8080 vinbero sid list
```

両PEのデータインターフェースには `xdpgeneric` ではなく `xdp`（driver mode）が表示される必要があります。

```bash
ip -details link show enp2s0
```

## コントローラー操作

`lab-mupc` 上で実行します。

```bash
mupctl status
mupctl sessions
mupctl suppress <session-key>  # withdraw T1/T2, leave PFCP session intact
mupctl resume <session-key>    # re-evaluate policy and advertise
mupctl reconcile
```

スナップショットはPFCP変更の受理直後と5秒ごとに到着します。15秒間届かなければMUP-Cは広告中のセッション経路を撤回します。オブザーバーを再起動しても、キャプチャー開始前に確立したセッションは再構成できません。UEを再登録し、新しい完全なPFCP確立トランザクションを生成してください。

## 復旧と移行

- VMは自動起動しません。`make up` と `make down` で管理します。
- `vinbero_pe` の移行ガードは既存ゲストの旧VPP/GoBGPサイドカーサービスを無効化します。パッケージは自動削除せず、旧実装は公開対象一覧から除外します。
- 未レビューの過去のfree5GC研究パッチは、固定した上流ベースラインへ適用せず、公開ソースにも含めません。
- 無関係なホスト上の用途とマシン固有のバックアップはラボの前提条件ではなく、公開配布の対象外です。
