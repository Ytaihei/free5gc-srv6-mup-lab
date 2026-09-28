# ネストKVMによるクリーンOSからの再現

[English](clean-room-reproduction.md) | 日本語

元のx86-64物理マシン上にクリーンなUbuntu L1ホストを作り、L2に6台のラボゲストを再構築する任意の試験です。レビュー済みソースからクリーンOSへ復旧できることを示しますが、別の物理CPUやARM/macOSへの移植性を証明するものではありません。元ゲストのディスクは保持し、試験環境へcloneしません。ソース・コンテナー・ツールチェーンを固定しますが、aptパッケージは完全に閉じた固定スナップショットではありません。

元ラボの一時停止には操作者の承認が必要です。32 GiBホストでは、24 GiBの試験ホストと17 GiBの元ゲスト群を同時起動できません。L2が動作したままL1をsuspend/saveしたり、試験中に移行したりしないでください。[カーネルのネストKVM文書](https://docs.kernel.org/virt/kvm/x86/running-nested-guests.html)も参照してください。

## 外側のホスト

`config/repro-host.yml` は、自動起動しない12 vCPU・24 GiB RAM・280 GiB sparse diskの専用VMと、独立したNAT管理ネットワークを定義します。環境に合うサブネットか確認してください。ヘルパーは空きRAM、元ゲスト停止、名前/サブネットの衝突、空きディスク、ネストKVM、未変更の固定NobleイメージのSHA256を確認します。既存のドメイン・ネットワーク・ディスクの上書きを拒否し、自動削除は行いません。

```bash
make down
python3 scripts/create-repro-host.py          # read-only safety checks
python3 scripts/create-repro-host.py --create
```

ベースは設定先に置かれたチェックサム検証済みの配布イメージであり、元ラボのゲストディスクではありません。導入するのは操作者のSSH**公開鍵だけ**です。GitHubトークンや秘密鍵はコピーしません。ホストスナップショットや生の試験ログは管理対象外の `artifacts/repro/` に保存してください。

## 新しいソースとbootstrap

クリーンなコミット済みリビジョンを固定し、[配布検査](source-distribution.ja.md)に従って履歴なし候補を書き出します。そのレビュー済みソースアーカイブだけを転送し、復旧用Git履歴やリモート認証情報はコピーしません。外側ホストでチェックサムを記録します。

```bash
python3 scripts/export-source.py --revision HEAD \
  --output artifacts/repro/source-candidate.tar.gz
sha256sum artifacts/repro/source-candidate.tar.gz
scp artifacts/repro/source-candidate.tar.gz labadmin@192.168.124.10:source-candidate.tar.gz
ssh labadmin@192.168.124.10
sudo cloud-init status --wait
sha256sum source-candidate.tar.gz           # must match the recorded outer-host digest
tar -xzf source-candidate.tar.gz            # on this fresh host, with no existing target directory
cd free5gc-srv6-mup-lab
python3 scripts/export-source.py --check-tree .
sudo env LAB_HOST_USER=labadmin ./scripts/install-host-deps.sh
./scripts/install-go-toolchain.sh
exit
```

libvirt/kvmグループを有効にするため、**新しいSSHセッション**で再接続します。`/dev/kvm` へのアクセスとVMX/SVM CPUフラグを確認し、無断でQEMUソフトウェアエミュレーションへ切り替えないでください。管理対象の既定ラボ設定を使います。L2管理サブネットはL1内で隔離されているため、元ラボと同じ値でも構いません。

```bash
cd free5gc-srv6-mup-lab
make preflight
make bootstrap
make dashboard-install                      # no Tailscale: loopback only
make test-baseline
MUP_ENABLE=1 make test-mup
MUP_ENABLE=1 make test-lease
ONE_CALL_ENABLE=1 OBSERVE_SECONDS=2 make test-one-call
NETWORK_RECOVERY_ENABLE=1 make test-network-recovery
ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml
```

正確なソースSHA、cloud imageのチェックサム、L1/L2のOS/カーネル、KVMドメイン種別、Ansible recap、E2E結果、ダッシュボード状態、変更なしの再適用を記録します。新規構築の不具合があればソースを修正し、固定リビジョンを更新して該当箇所を再実行してください。再試行を記録し、ゲストを手修正した後の成功を「初回のクリーン成功」と表現しないでください。

2回目の完全に新しいL1/L2試験では、先のL2群とL1を停止し、管理対象外の完全なYAMLプロファイルを `create-repro-host.py --config` に渡します。VM名、ネットワーク名、ブリッジ、サブネット、MACを新しくします。既存ディスクは保持し、以前の実行を上書きしません。新たにレビューしたソースアーカイブを取得し、ホスト準備とbootstrapを繰り返します。

SRロケーターの静的経路とMUP PE（N6／Direct側）のtenant VRF/N6所属は、起動スクリプトだけでなくNetplanに宣言しています。[Netplan VRF仕様](https://netplan.readthedocs.io/en/stable/netplan-yaml/#properties-for-device-type-vrfs)を参照してください。復旧試験は `netplan apply`、`systemd-networkd` 再起動、設定されたDNのMUP PE（N6／Direct側）近隣エントリーだけの削除を行い、各操作後に完全なUE callとXDPカウンターを確認します。

N6接続経路をtenant table 100へ明示し、フォールバックゲートウェイをon-linkとしているため、既存インターフェースをVRFへ移す際に自動接続経路が残ることへ依存しません。アドレスを持たない `mup-dn` masterはnetwork-online判定では任意です。N6アドレスはメンバー側にあり、masterのアドレスを待つと転送可能でもwait-onlineがタイムアウトするためです。復旧試験ではwait-onlineを10秒のタイムアウトで確認します。修正前の失敗状態が残るMUP PE（N6／Direct側）では、playbook適用後に準備状態を検証してユニットを再実行し、失敗フラグだけを消さないでください。

```bash
source scripts/lab-lib.sh
lab_ssh "$LAB_NPE" '/lib/systemd/systemd-networkd-wait-online --timeout=10 && sudo systemctl restart systemd-networkd-wait-online'
```

固定したVinberoのEnd.DT4 XDP転送にはDNの近隣解決が必要です。起動時に1回だけプローブしても、後からキャッシュが消えれば不十分です。MUP PE（N6／Direct側）だけで動く `vinbero-neighbor-refresh.timer` が、`mup-dn` 内の設定DNへ15秒ごとにMUP PE（N6／Direct側）発のICMPを送り、タイムアウトを2秒とします。有効化直後も実行し、DN構築済みを前提にしません。これはDNとN6が正常な場合の復旧時間を制限するもので、任意のtenant宛先に対する汎用解決策ではありません。UE通信を送らず、MACを恒久登録せず、ダッシュボードのUEプローブも置き換えません。近隣削除試験は手動ARPの事前投入なしで自動解決を待ち、実UEのICMP/HTTPと両PEカウンターを検証します。

## 元のラボを復元する

L1内で `make down` を実行してSSHを終了し、物理ホストで次を実行します。

```bash
virsh -c qemu:///system shutdown srv6-mup-repro-host
virsh -c qemu:///system domstate srv6-mup-repro-host
# Wait until the state is "shut off" before continuing.
make up
ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml
make test-baseline
```

元のダッシュボードとMUP経路を確認してからマシンを引き渡してください。通常の停止に失敗したら調査し、どちらのラボも強制削除しないでください。試験ディスクとネットワークは自動起動なしで保持します。公開、PRマージ、ゲストディスク/バイナリ配布には別のレビュー・承認が必要です。

<a id="recovery-after-an-original-guest-kernel-update"></a>

### 元ゲストのカーネル更新後の復旧

ゲストのapt/カーネルは完全固定ではありません。新しいcoreカーネルには、旧ABI向けにビルドしたgtp5gがない場合があります。最初にAnsibleを実行し、動作中カーネル向けにモジュールをビルド・導入してください。起動時にUPFが失敗し、なおSMFがUPFを選べなければ、core全体、パッシブオブザーバー、RANの順に再起動します。これは稼働中セッションをリセットする明示的復旧であり、通常の変更なし再適用ではありません。

```bash
source scripts/lab-lib.sh
lab_ssh "$LAB_RAN" 'sudo systemctl stop ueransim-ue ueransim-gnb'
lab_ssh "$LAB_CORE" 'sudo modprobe gtp5g && sudo systemctl restart free5gc-lab && sudo systemctl restart pfcp-observer'
lab_ssh "$LAB_RAN" 'sudo systemctl start ueransim-gnb && sudo systemctl start ueransim-ue'
make test-baseline
MUP_ENABLE=1 make test-mup
MUP_ENABLE=1 make test-lease
ONE_CALL_ENABLE=1 make test-one-call
```

任意のカーネル/apt更新をまたぐ自動復旧は、固定イメージからのクリーン再現という主張には含めません。差異を隠すためにイメージのダイジェストを変えたり、失敗する検査を抑止したりしないでください。
