# root の無いホストで CubeRange をビルドする

*English: [host-setup-without-root.md](host-setup-without-root.md)*

[README.ja.md](../README.ja.md) の手順は `sudo apt install build-essential` と `pip install` から始まる。
権限の絞られたワークステーションでは、そのどちらも使えない。

以下は、実際にそういうマシンで通した道筋である。
Ubuntu 26.04、`sudo` なし、`gcc` なし、`pip` なし、`make` なし、Docker なし。
記録するのは、たどるより考え出すほうがずっと時間がかかったからである。

以下はすべて `$HOME` の下に入る。
パッケージマネージャは要らない。

## そのマシンに何があったか

先に確認する価値がある。
このうち2つは、無いと思い込みやすくて実は在る。

```bash
for c in bc busybox git curl python3 ld as ar; do printf '%-10s' "$c"; command -v $c || echo '--'; done
ldconfig -p | grep -cE 'libicu|libssl'      # Renode 同梱の .NET が起動時に両方 dlopen する
```

`bc` は省略できない。
`probe.sh` が6回呼ぶ。
`libicu` と OpenSSL 3 は Renode が同梱する .NET ランタイムが dlopen する。
この3つが無くて導入もできないなら、ここで止まったほうがよい。
この先は助けにならない。

## 1. Python

システムの Python が、Zephyr v4.1.0 の想定より新しいことがある。
実測したホストでは 3.14 だった。
Zephyr v4.1.0 が固定されているのはまさに Python の下限のためで、経緯は `tools/setup-toolchain.sh` の冒頭にある。

`uv` は独立したインタプリタをダウンロードするので、コンパイラは要らない。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv python install 3.12
uv venv --seed --python 3.12 ~/.venvs/cuberange
uv pip install --python ~/.venvs/cuberange/bin/python \
    pytest spacepackets crcmod cmake ninja
```

`--seed` は飾りではない。
これを付けないと venv に `pip` モジュールが入らず、`setup-toolchain.sh` は最初のインストールで `No module named pip` と言って死ぬ。

`cmake` と `ninja` は PyPI の wheel から入れる。
`setup-toolchain.sh` はこれらを入れないので、無いと最後のスモークテストが `CMake is not installed or cannot be found` で落ちる。

## 2. C コンパイラ

`tests/native` と `make check` はホストのコンパイラを要求する。
Zig が単一の静的な tarball として1つ配っている。

```bash
ZIG=0.16.0
curl -sSL -o /tmp/zig.tar.xz "https://ziglang.org/download/$ZIG/zig-x86_64-linux-$ZIG.tar.xz"
mkdir -p ~/.local/opt && tar xf /tmp/zig.tar.xz -C ~/.local/opt
ln -sf ~/.local/opt/zig-x86_64-linux-$ZIG/zig ~/.local/bin/zig

printf '#!/bin/sh\nexec %s/.local/bin/zig cc "$@"\n' "$HOME" > ~/.local/bin/cc
chmod +x ~/.local/bin/cc
```

`zig cc` は sysroot を同梱した clang である。
コーデックのテスト一式も、GNU Make 自身も、文句なくコンパイルできた。

## 3. GNU Make

このリポジトリはすべて `make` で駆動する。
手順2のコンパイラでビルドする。

```bash
curl -sSL -o /tmp/make.tar.gz https://ftp.gnu.org/gnu/make/make-4.4.1.tar.gz
tar xzf /tmp/make.tar.gz -C /tmp && cd /tmp/make-4.4.1
CC="$HOME/.local/bin/zig cc" ./configure --prefix="$HOME/.local" --disable-dependency-tracking
sh ./build.sh && ./make install
```

`build.sh` は make 自身のブートストラップスクリプトで、まさにこの状況のためにある。

## 4. Renode、Zephyr、libcsp

README と同じである。
違うのは `setup-toolchain.sh` を venv の中から走らせることだけ。

```bash
case "$(uname -m)" in
  x86_64)  ASSET=renode-1.16.1.linux-portable-dotnet.tar.gz ;;
  aarch64) ASSET=renode-1.16.1.linux-arm64-portable-dotnet.tar.gz ;;
esac
curl -L -o /tmp/renode.tar.gz \
  "https://github.com/renode/renode/releases/download/v1.16.1/$ASSET"
mkdir -p ~/tools && tar xzf /tmp/renode.tar.gz -C ~/tools

. ~/.venvs/cuberange/bin/activate
./tools/setup-toolchain.sh
git clone --depth 1 --branch v2.1 https://github.com/libcsp/libcsp ~/libcsp
```

## 5. 読み込む1ファイル

```bash
cat > ~/cuberange-host-env.sh <<'EOF'
export PATH="$HOME/.local/bin:$PATH"
. "$HOME/.venvs/cuberange/bin/activate"
. "$HOME/cuberange-env.sh"
export PATH="$HOME/zephyr-sdk/sysroots/x86_64-pokysdk-linux/usr/bin:$PATH"
export RENODE_DIR="$HOME/tools/renode_1.16.1-dotnet_portable"
export LIBCSP="$HOME/libcsp"
EOF
```

Zephyr SDK の `sysroots/.../usr/bin` に `dtc` などビルドが必要とするホストツールが入っている。

あとは次のとおり。

```bash
. ~/cuberange-host-env.sh
make probe
make check
```

## この道筋での実測値

後日の実行が、本物の退行と単に遅いマシンを区別できるように記録する。

| | |
| --- | --- |
| `make probe` | 40 pass / 0 fail / 3 skip、静穏なホストで約6分 |
| 4ノードの実行速度 | 実時間の 4.50 倍（8コア x86-64。設計書の 2.34 倍は14コア機での値） |
| `make check` | 静穏なホストで 11分26秒。プローブ、コーデック、native、往復、決定性、演習14アサーション、ファームウェア4ペア |
| コンステレーション | 1エミュレーションで8ノード。4アサーションが17秒、最大 RSS 688 MB、仮想20秒がエミュレーション4.16秒。設計書の4ノード 2.34 倍とは比較できない。ここではノードが起動後アイドルで、ホスト負荷も 8.9 だった |
| ディスク | Zephyr ワークスペース 4.5 GB、SDK 2.0 GB、Renode 200 MB |

**プローブの実行中に他の Renode 作業を走らせないこと。**
同じプローブが、Renode の作業を2つ並走させた状態では35分かかった。
設計書の R22 は、報告されている13%のハング率自体が競合の産物ではないかと疑っている。
この観測はそれと整合する。

## ここでもまだ動かないもの

- **SocketCAN と Wireshark の統合。** どちらも root が要る。`probe.sh` は失敗ではなく SKIP として正しく報告する。設計上どちらも任意機能である。

- **CRC の4つ目のオラクルとしての NASA CryptoLib。** `tools/gen_golden.py` は `./cryptolib/build/libcryptolib.so` を期待するが、これはベンダリングされておらず、このホストではビルドしていない。
