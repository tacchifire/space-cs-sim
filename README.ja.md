# CubeRange

**宇宙システムのセキュリティ**を手を動かして学ぶための演習環境である。
衛星のファームウェアを [Renode](https://renode.io) 上で命令レベルで動かし、CubeSat の内部 CAN バス、無線リンク、地上局までを一本に繋ぐ。
攻撃は実際に通り、対策が本当にそれを止めることも検証されている。

自動車分野で Toyota の [RAMN](https://github.com/ToyotaInfoTech/RAMN) が果たした役割の、宇宙版にあたる。
RAMN は4つの MCU と CAN バスを1枚の基板に載せることで、自動車セキュリティを学習可能なものにした。

**現状は演習3本、衛星ノード4つ。**
地上局が実際の ECSS PUS 17,1 を送り、CCSDS フレーミングとエミュレートされた UART リンク、そして STM32H753 ノード間の CSP over CAN を通って、実際の 17,2 が返ってくる。
演習は連鎖している。
どの演習も、直前の演習の対策が「これは解決しない」と認めた限界を突く。

- **EX-B01**：バスにフレームを流せるだけの攻撃者が衛星を沈黙させる。電源指令に認証を入れて塞ぐが、その対策文書はリプレイ可能だと明記している。
- **EX-L01**：その対策が、宇宙リンクで録音したフレームの再送で破られる。鍵も、パースも、フィールドの意味の理解も要らない。
- **EX-A01**：欠けていた制御は認証ではなかった。正真正銘で、書式も正しく、物理的に実行不可能なトルク指令が衛星をスピンアップさせる。その間、どのサブシステムも正常を報告し続ける。

この先どこへ向かうかは[設計仕様書](docs/superpowers/specs/2026-08-28-cuberange-design.md)にある。

```
$ make demo-p0
COMM: uplink frame seq=0 carrying 11 octets -> OBC
COMM: forwarded 11 octets to OBC on port 10
OBC:  accepted a connection from 5 on port 10
OBC:  APID 0x0a9 PUS 17,1 from source 66
OBC:  PUS 17,2 report sent to COMM (counter 0)
COMM: downlink 17 octets from node 1
```

## NOS3 や CTF ではなく、これを作った理由

NASA の [NOS3](https://github.com/nasa/nos3) は**運用**レベルのシミュレータである。
cFS は Linux 上でネイティブに動き、バスはミドルウェアで表現される。
Hack-A-Sat の一式はファームウェアをエミュレートしていたが、単発の競技向けで 2020 年から更新が止まっている。

CubeRange が置かれているのは**ファームウェアとシリコン**のレベルであり、そこから動かない。
実物の Zephyr イメージが実在 MCU のモデル上で動き、CAN バスもリンクも本物として存在する。
だからこそ、メモリ破壊や鍵抽出、バス注入、フォールト注入といった演習が実測として成立する。
台本を用意する必要がない。

## はじめかた

Linux（WSL2 でよい）、Python 3.10 以上、ディスク約 8 GB が要る。
root も Docker も不要である。

最小構成のイメージでは、先に次を入れる。
`bc` は省略できない。プローブの算術は6箇所とも `bc` を呼ぶ。
`libicu` と OpenSSL 3 は Renode 同梱の .NET が起動時に dlopen する。
C コンパイラが要るのは、`make check` が共有コーデックをコンパイルするためである。

```bash
sudo apt install -y bc build-essential python3-dev libicu-dev libssl3
pip install -r requirements.txt
```

**root がない場合**は [docs/host-setup-without-root.ja.md](docs/host-setup-without-root.ja.md) を見てほしい。
`sudo` も `gcc` も `pip` も `make` も無いホストで実際に通した手順と、そこで出た測定値を記録してある。
このリポジトリのどこにもパッケージマネージャは要らない。

GTK は要らない。
すべての起動が `--disable-xwt` を渡すので、Renode が UI を組み立てることはない。

```bash
# 1. Renode 1.16.1 portable を ~/tools/renode_1.16.1-dotnet_portable に展開する
#    どちらのアーキテクチャでも展開先のディレクトリ名は同じになる。
case "$(uname -m)" in
  x86_64)  RENODE_ASSET=renode-1.16.1.linux-portable-dotnet.tar.gz ;;
  aarch64) RENODE_ASSET=renode-1.16.1.linux-arm64-portable-dotnet.tar.gz ;;
esac
curl -L -o /tmp/renode.tar.gz \
  "https://github.com/renode/renode/releases/download/v1.16.1/$RENODE_ASSET"
mkdir -p ~/tools && tar xzf /tmp/renode.tar.gz -C ~/tools

# 2. Zephyr v4.1.0 と SDK（このバージョンでなければならない理由は tools/setup-toolchain.sh の冒頭にある）
./tools/setup-toolchain.sh
git clone --depth 1 --branch v2.1 https://github.com/libcsp/libcsp ~/libcsp

# 3. 足元を確かめてから動かす
make probe
make demo-p0
```

| ターゲット | 内容 |
| --- | --- |
| `make probe` | 設計が依存する Renode の能力をすべて検証する。40項目、うち5件は意図的に失敗させる自己テスト |
| `make firmware-p0` | COMM と OBC のイメージを作る |
| `make firmware-a01` | 全ノードのイメージ。COMM、OBC、EPS 両版、ADCS 両版 |
| `make demo-p0` | PUS の往復を走らせて assert する |
| `make demo` | R0 のスモークテスト。2ノードが CAN 上で CSP ping を交換する |
| `make determinism` | 同一シナリオを CI プロファイルで3回走らせ、ゲスト出力のバイト一致を要求する |
| `make pair-gate` | 脆弱版と対策版の各ペアが、宣言したビルドフラグ1個だけで違うことを証明する |
| `make spike` | 特権なしで Python から生 CAN フレームを注入し、External Control API で仮想時間を進める |
| `make verify-all` | 全演習を両方向で検証する。攻撃が通り、対策が止め、正規機能が生き残る |
| `make soak-p0` | ウォッチドッグ下で30回連続の往復 |
| `make check` | 上記すべてとコーデック一式 |

`make probe` はホストの負荷に敏感である。
静穏なホストで約6分のところ、Renode の作業を2つ並走させた状態では35分かかった。
プローブが遅いことを退行と読む前に、まず負荷を確認してほしい。

## 全体の組み立て

```
地上局 (Python)  --TCP-->  COMM.usart2  --CSP/CAN-->  OBC
  CCSDS + PUS コーデック       中継              PUS ディスパッチ
```

`usart3` は全ノードで Zephyr のコンソールである。
宇宙リンクを `usart2` に分けてあるのは、コンソールと共有したリンクが、アプリケーションの最初の1バイトより前に 9239 バイトの起動バナーを送りつけるからである。

攻撃者は、プラットフォームも ELF も持たない空の Renode マシンに載せた C# ペリフェラルを通じて生 CAN フレームを注入する。
このペリフェラルは実行時にコンパイルされる。
特権も SocketCAN も攻撃者側ファームウェアも要らない。

## 証拠規則

**実行して確かめていない主張は書かない。**

設計書のどの主張にも、それを再現するコマンドが対応する。
`tools/renode-probe/probe.sh` が常設の検証ゲートであり、このハーネスは**失敗できることが要件**である。
既知の不正入力を自分自身に食わせる自己テストを内蔵するのは、初版が出力ディレクトリの欠損時に全項目 PASS を報告したためである。

この規則は元を取っている。
設計書の最初の2版は、Renode に触れた途端に成立しなくなる能力を「検証済み」と称していた。
MCU の選定もその一つである。
性能に関する中心的な前提は8倍の誤りで、原因はワークロードの性質ではなく Renode の既定値2つだった。
是正の全記録は設計書の16節にある。

道中で見つかった Renode とライブラリの欠陥22件を記録し、assert できるものは `probe.sh` の否定テストとして固定してある。
将来のリリースがどれかを直せば、プローブが落ちて気付く。
挙動だけが静かに変わることはない。
うち2件は丸1日を要した。
構造的には完全なのに、拡張 ID ではなく標準 ID で送られたために届かなかった CAN フレームと、libcsp が実行時に選ぶプロトコルバージョンである。
後者のせいで「CSP v1 を使う」は設計では真、ファームウェアでは偽という状態になっていた。
22件目は 2026-09-09 に見つかったもので、形は同じである。
`cpu TranslateAddress` がアクセス種別ではなくアドレスでキャッシュするため、命令フェッチより先に読み出しを問い合わせると、実行不可のはずの SRAM が実行可能だと報告される。

**CI は存在しない。**
ゲートは `make check` であり、人間が打つ必要がある。
この中の複数の文書が、これらのゲートを CI が強制すると書いていた時期がある。
`.github` ディレクトリは一度も存在したことがない。
そう書くほうが安上がりである。
設計書の以前の版は TTP 検証ツール、オフラインモード、オラクルの必須化を主張しており、3つとも存在しなかった。

## 使う前に

このリポジトリには、宇宙機のプロトコルに対して実際に通る攻撃が入っている。
標的は合成物であり、そのままでなければならない。

| 文書 | 何を定めるか |
| --- | --- |
| [SAFE_USE.ja.md](SAFE_USE.ja.md) | ここで攻撃される対象はすべてエミュレーションである。実在するものに向けないこと、実在の識別子を持ち込まないこと |
| [SECURITY.ja.md](SECURITY.ja.md) | どの弱点が意図的で、どれが報告に値するか、そしてその方法 |
| [ASSURANCE.ja.md](ASSURANCE.ja.md) | ここでの結果が何を支持し、何を支持しないか。結果を引用する前に読むこと |
| [CONTRIBUTING.ja.md](CONTRIBUTING.ja.md) | 証拠規則と、演習が証明しなければならないこと |
| [docs/legal/export-control.ja.md](docs/legal/export-control.ja.md) | 何が分かっていて、何が分かっていないか。そして何も主張していないこと |

## ライセンス

Apache-2.0（[LICENSE](LICENSE) を参照）。
依存は意図的に寛容なものだけに保っている。
Renode は MIT、Zephyr は Apache-2.0、libcsp は MIT である。
NASA CryptoLib と NOS3（NOSA 1.3）、Yamcs（AGPL-3.0）、OpenC3 は、使う場合でも外部オラクルか任意のアダプタとしてのみ扱い、取り込むことはしない。
