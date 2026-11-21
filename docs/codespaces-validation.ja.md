# Codespaces 土台検証（PR-1）

## 目的と状態

2026-11-21、`ba38253` を基点とする `feat/devcontainer-foundation` で準備。
これは Codespace 実機測定の準備であり、Codespaces が動くという証拠ではない。
push・PR 作成・PHASE-CONT-REPORT.md への記録はコーディネーターが担当する。今回の準備には push・PR 作成を含めない。
範囲はコーディネーターの `PUBLISHING-SURVEY.md` §9 の PR-1 に従う。本人が実験を起動できるよう、最小 devcontainer JSON を先行して含める。一般向け導線は後続作業とする。

## 方式と理由

[validation イメージ](../containers/validation/Dockerfile) から派生した Dockerfile を使い、[setup-toolchain.sh](../tools/setup-toolchain.sh) は変更しない。依存関係の入力ファイルだけをコピーし、リポジトリは Codespaces がマウントする。CI エントリーポイントやオフラインゲートは引き継がない。

| 選択 | 理由と制限 |
| --- | --- |
| postCreate ではなくビルド時導入 | 再ビルドで依存層を再利用でき、ソース編集でツールチェーンを再導入しない。初回のクリーンビルドは全取得・ビルドが必要で、起動速度は未実測。 |
| 既存の固定版 | Ubuntu 24.04、Renode 1.16.1、Zephyr v4.1.0、SDK v1.0.1、libcsp v2.1。Python 依存は既存の非固定方針を維持。 |
| 非 root developer＋venv | HOME 相対のセットアップと Makefile のパスを一致させ、システム Python を変更しない。セットアップは既存の hello_world ビルドも行う。 |
| 独立した派生 Dockerfile | validation ゲートを変更しない。apt・パッケージ・版の変更時は両定義を確認する。依存宣言の重複は保守コストとなる。 |
| 最小ポート転送 | GUI 3987（`ports.gui()`）のみ宣言。ポート自動検出は無効。拡張・privileged・隔離回避なし。ポート公開範囲は private に保つ。 |
| x86_64 限定 | 選択した Renode portable アーカイブと SDK host-tools PATH は x86_64 向け。他のアーキテクチャでは明示的にビルドを失敗させる。 |

## 検証チェックリスト

`tools/codespace-verify.sh` はルートの `codespace-verify-result.log` に前回分を上書きする。各項目に BEGIN・出力・RESULT、PASS/FAIL/SKIP、失敗時の終了コード、経過秒を記録する。失敗は非ゼロ終了し、依存項目の SKIP を成功としない。`--env-only` は summary が PASS でも部分確認である。タイムアウト後には強制終了までさらに10秒の猶予がある。

| 項目 | 判定条件／上限 | Codespace 実機結果 |
| --- | --- | --- |
| ツール | python3・west・bwrap の版（各20秒）、Renode の版（30秒） | 未確認 |
| 隔離 | 既存 bubblewrap プローブ：lo だけが存在し、ループバック接続が成功（70秒） | 未確認 |
| Renode 起動 | 隔離内でヘッドレスの machine 作成と正常終了（60秒） | 未確認 |
| ファームウェア | `make firmware-p1`：COMM/OBC と脆弱・対策済み EPS（1200秒） | 未確認 |
| 代表演習 | EX-B01 を `make exercise` と solver で実行。終了0かつ `injected` 出力（300秒）。test_documented_workflow.py と同じ確認 | 未確認 |
| GUI | 127.0.0.1:3987 の一時サーバー。/ の HTTP 200 と正しい /api/config を確認し終了（40秒） | 未確認 |

EX-B01 は既存の文書手順テストが選ぶ、3台構成の軽量な例である。起動と注入を検証し、脆弱・対策済みの全挙動テストではない。`make check` は実行しない。隔離失敗時は Renode・演習を止め、隔離回避を有効にしない。GUI 確認は独立して行い、Renode は起動しない。

## 本人の操作

コーディネーターが `feat/devcontainer-foundation` を push した後、リポジトリの **Code → Codespaces → New with options** から同ブランチの新規 Codespace を作る。x86_64 マシンとリポジトリの devcontainer を選ぶ。作成開始とターミナル使用可能時刻を UTC で記録し、イメージ失敗時には作成・ビルドログを保存する。転送ポートは private に保つ。

ターミナルが使用可能になったら、リポジトリルートで次を1回実行する。

```bash
bash tools/codespace-verify.sh
```

`codespace-verify-result.log` 全文に、作成開始・使用可能時刻、CPU/RAM、ビルド失敗時のログを添えてコーディネーターへ貼り戻す。ビルド失敗ならスクリプトは未実行であり、その旨を報告する。スクリプトは一時 GUI サーバーを終了する。証拠取得後は Codespace を停止・削除する。

## 結果記入欄

| 欄 | 値 |
| --- | --- |
| コミット／UTC 実行日 | 未記入 |
| マシン CPU／RAM | 未記入 |
| 作成開始／使用可能時刻／経過時間 | 未記入 |
| イメージビルド結果／ログ | 未記入 |
| スクリプトログ／終了コード | 未記入 |
| private 転送 GUI のブラウザ観察 | スクリプトでは未確認 |
| 判断／残る阻害要因 | 実機の証拠待ち |

## ローカルの証拠と未確認範囲

構文、環境不足の分岐、GUI 応答、ホスト pytest、文書 parity のローカル結果は完了報告へ記載する。この準備セッションではコンテナランタイムによるビルドも Codespace 実機実行も行っていない。ホストの結果では Codespaces の名前空間権限、イメージビルド成功、起動時間、演習成功は確定しない。

現行 GUI は Host/Origin をローカルアドレスと照合し、/api/config は localhost の Renode URL を返す。転送経由のブラウザアクセスとブラウザから Renode への接続は未確認で、別対応が必要な可能性がある。本変更ではその安全性・接続仕様を変えない。コンテナ内 HTTP 成功はブラウザ体験の成立を証明しない。validation Dockerfile 自体は未ビルドのままであり、この派生イメージを検証しても CI エントリーポイントやオフラインゲートの検証にはならない。

2026-11-21 のローカル実行：構文・JSON・ポート対応は成功、doc parity は **280 passed**。指定のホストスイートは71.69秒で **1231 passed / 135 failed / 17 errors**。このサンドボックスではソケット作成が `Operation not permitted` となり、bwrap・west・Renode がなく、名前空間・プロセス情報の参照も制限される。想定の失敗1件という基準は満たしていない。検証スクリプトは不足ツールで終了1となり、隔離依存の段階を SKIP した。GUI 起動もソケット制限で失敗しており、ローカル GUI 応答成功とはしていない。

ローカルコミットは blocked：`git add` がメインリポジトリの読み取り専用 `.git/worktrees/space-cs-sim-wt-devcontainer` 内に `index.lock` を作成できない。ファイルは作業ツリーに残し、コーディネーターが書き込み可能なセッションでコミットする。

同じサンドボックスで基点 `ba38253` の別コピーを実行した結果は67.96秒で **1227 passed / 135 failed / 17 errors**。失敗・error のテスト ID は完全に一致し、追加の成功4件は新規文書ペアによる。この比較では追加失敗はないが、ホストスイート成功を意味しない。
