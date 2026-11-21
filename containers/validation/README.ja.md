# 検証コンテナ

CI が回すゲートのためのコンテナ定義。ツールチェーンを1か所に固定し、CI と
手元で共有する。ワークフローのキャッシュキーとしてのみ存在する状態をやめる
ためのものである。

## 中身

| ファイル | 内容 |
| --- | --- |
| `Dockerfile` | Ubuntu 24.04 にワークフローのパッケージ、Renode 1.16.1、`tools/setup-toolchain.sh` が入れる Zephyr ツールチェーンを収めたもの |

## CI との等価性

- 入口は同じ: `make check` を回す `tools/ci.sh`。
- ピン留めも同じ: Zephyr v4.1.0、SDK v1.0.1、Renode 1.16.1、libcsp v2.1。
- 分割も同じ: 取得とビルドはネットワークを使ってよいが、ゲートの実行は
  使ってはならない。ワークフローでは2つのジョブ、ここでは1つのイメージの
  2回の実行とし、2回目はネットワークを切る。

## 使い方

```sh
docker build -t cuberange-validation -f containers/validation/Dockerfile .
docker run --rm cuberange-validation --env-only
docker run --rm --network none cuberange-validation
```

## 実行時の要件

- 非特権ユーザ名前空間が動作すること。でなければ隔離バックエンドがどれも
  動かず、ゲートは開始を拒否する。Ubuntu 24.04 ホストでは
  `kernel.apparmor_restrict_unprivileged_userns` のノブか、コンテナを特権
  実行することが必要になる。
- ディスク: ツールチェーンだけで約7 GB。イメージが大きいのは設計どおり。
- ベースイメージはタグで固定し、digest ではまだ固定していない。digest
  固定は、イメージを実際にビルドする最初の機会に確定する未決事項である。

## 状態

- **未ビルド・未実行。** この定義を書いたマシンにはコンテナランタイムが
  なく、`docker`、`podman`、`buildah` のいずれも存在しないため、ここでは
  ビルドを試みること自体ができなかった。この定義は未検証である。
- ビルドされ、中でのゲート実行が観測されるまでは、等価な検証は既存 CI
  （`.github/workflows/check.yml`、同じ `tools/ci.sh` を `ubuntu-latest` で
  回す）が担う。ブランチが検証済みになるのは、そのプルリクエストを開いて
  実行ログをコミット SHA と照合したときであり、このファイルが存在する
  ことではない。
