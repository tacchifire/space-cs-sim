# ラズパイ移行の引き継ぎ

x86-64 の WSL2 で作った CubeRange を Raspberry Pi に移す。この文書は移行そのものの引き継ぎで、
プロジェクトの作法は `CLAUDE.md` にある。**先に `CLAUDE.md` を読むこと。**

移行に必要な変更は既に入れてある。この文書は「何が入っていて、何が未確認で、Pi に着いたら
最初に何を測るか」を書いたもの。

---

## 1. 結論から

**Pi 5 (8GB 以上) なら動く。Pi 4 は演習体験としては無理。**

- Renode 1.16.1 は公式の aarch64 ビルドがある（`linux-arm64-portable-dotnet`、75 MB）。
  ソースコミットは x86-64 版と同一 `d66b0c2a`。Cortex-M トランスレータ
  `translate-arm-m-le.so` も入っている。ビルドし直す必要はない。
- Zephyr SDK v1.0.1 も aarch64 ホスト用アセットが揃っている。ターゲットは `arm-zephyr-eabi` の
  まま、展開先のパスも同一なので `CROSS_COMPILE` は変更不要。
- **速度が唯一の本質的な問題。** Renode は 1 マシン 1 スレッドに張り付くので、シングルスレッド
  性能がそのまま効く。x86-64 での実測 2.34x に対し、Pi 5 で 0.6〜0.95x、Pi 4 で 0.23〜0.42x の
  **見積もり**（Geekbench 6 シングルコア比からの推定であって、実測ではない）。

Pi 4 の見積もり ~0.3x は、設計 §3.3 が「Renode のデフォルト（100 µs quantum ＋ リアルタイム
スロットル）の欠陥」として退けた 0.301x と数字の上で同じ。つまり Pi 4 では「時間圧縮は不要」
「パスレベルの体験は十分にインタラクティブ」という §16.2 W20 の結論がそのまま無効になる。
Pi 5 では無効にならない（60 仮想秒が実時間 63〜100 秒）。

| 機種 | 判定 |
| --- | --- |
| Pi 5 16GB / 8GB | 推奨 |
| Pi 5 4GB | 動くが窮屈。`CUBERANGE_RSS_CEILING_MB=1024` 必須 |
| Pi 4 全モデル | 演習体験としては非推奨 |
| Pi 3 / Zero 2 W / CM4 | 不可 |

**100.87.110.2 が何なのかは、まだ誰も確かめていない。** このセッションはそのホストに触れて
いない。最初にやるのは機種と RAM とディスクの確認。

---

## 2. 手順

`README.md` の手順がそのまま通るように直してある。アーキテクチャの分岐は自動。

```bash
# 依存。bc は probe.sh が 6 回使う（Pi OS Lite には入っていない）。libicu と libssl3 は
# Renode 同梱の .NET が起動時に dlopen する。C コンパイラは make check が共有コーデックを
# 2 回コンパイルするのと、crcmod がどのアーキ向けにも wheel を出していないため。
sudo apt install -y bc build-essential python3-dev python3-venv libicu-dev libssl3

# PEP 668 があるので venv を作る（--break-system-packages は使わない）
python3 -m venv ~/cuberange-venv && . ~/cuberange-venv/bin/activate
pip install -r requirements.txt

# Renode。README のスニペットが uname -m で aarch64 版を選ぶ
# Zephyr SDK。setup-toolchain.sh が uname -m でホスト側アセットを選ぶ
./tools/setup-toolchain.sh

make check
```

ネットワークが無い環境では、一度オンラインで `./tools/renode-probe/probe.sh --fetch-only` を
実行してキャッシュを温めておけば、以後 `--offline` が使える。

入れてはいけないもの:

- **GTK は不要。** 全ての起動が `--disable-xwt --console --plain` なので UI は構築されない。
  headless の Pi に libgtk を入れる理由はない。
- **システムの .NET は不要。** portable-dotnet バンドルが .NET 8.0.16 を内蔵している。
  x86-64 版は 8.0.12 なので `renode --version` の runtime 行は変わる（何も検証していない行）。

ICU が入らない環境なら `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` で起動はする。

---

## 3. 入れた変更

すべて push 済み。x86-64 側で `make check` が通ることは確認してある。

| 変更 | 理由 |
| --- | --- |
| `probe.sh` のビルドピンを arch 別に | Antmicro は 2 アーキを 1 リリース内の別ジョブで 10 分差でビルドしていて、同じコミット `d66b0c2a` でも x86-64 は 09:23、arm64 は 09:33 |
| `probe.sh` の `PERF_FLOOR` を env 上書き可能に | 1.5x は x86-64 の数字。どの Pi にも到達できない |
| `setup-toolchain.sh` に `SDK_HOST` 分岐 | アセット名のホスト側だけが変わる |
| `setup-toolchain.sh` の hosttools インストーラを **ハード失敗**に | 下記 |
| `setup-toolchain.sh` に PEP 668 の案内 | 下記 |
| `setup-toolchain.sh` を shallow clone ＋ `west init -l` に | 下記 |
| `supervisor.py` の既定値を env 上書き可能に | 3 箇所にハードコードされていた |
| `test_p0_roundtrip.py` を supervisor 経由に | 下記 |
| **固定 sleep での起動待ちを、ノード自身の ready 行の待機に置換**（3 ファイル） | 下記。**Pi で一番効く修正** |
| `probe.sh --offline` を実装 | パースするだけで値が読まれておらず、オフライン指定でも普通にフェッチして成功と報告していた |
| `requirements.txt` ＋ `make check` でオラクルの import を要求 | 下記 |
| `README.md` に `bc` / `libicu` / `build-essential` を明記 | `probe.sh` は `bc` を 6 回使う。Pi OS Lite には入っていない |
| `README.md` の Renode URL を arch 別に | |

**起動待ちの置換 — Pi で一番効く。** 3 ファイルすべてが `time.sleep(3.0〜4.0)` で「起動したはず」
としていた。ところが `GroundStation.ping` も電源コマンドも**1 回送って終わり**で再送がない。
準備前に送ったフレームは黙って捨てられ、その後は変わらないレールを 15 秒ポーリングし続ける。
`alive()` は COMM→OBC を ping するだけで、コマンドの宛先である EPS を一切見ていない。

実際にこれで `make check` の EX-L01 が間欠的に落ちた（単体では 3/3 通り、`verify-all` 単体でも
6/6 通る。通しでのみ再現）。**固定 sleep は遅いホストほど悪化する**ので、Pi では確実に踏む。
各ノードが自分で出す ready 行（`COMM ready` / `OBC ready` / `EPS listening`）を待つよう変更した。
上限は `CUBERANGE_BOOT_TIMEOUT_S`（既定 30 秒）で伸ばせる。

**オラクルの import 要求。** `spacepackets` と `crcmod` は `pytest.importorskip` で守られていた。
つまり**入っていなければ適合性テストの層がまるごと黙って消え、それでも CHECK PASSED が出る**。
自分の証拠を静かに捨てるゲートはゲートではないので、`make check` の先頭で import を要求する
ようにした（両方向で発火を確認済み）。`requirements.txt` を追加。オラクルはこちらと独立に動く
ことが価値なので、**意図的にバージョンを固定していない**。

**hosttools インストーラ。** 名前が `zephyr-sdk-x86_64-hosttools-standalone-0.10.sh` で
ハードコードされていた。aarch64 では `zephyr-sdk-aarch64-...` なので `if [ -f ... ]` が単に偽に
なり、ホストツールをダウンロードして展開して**インストールせずに exit 0**していた。何も表示
されない。しかも直前の gcc 存在チェックが通ってしまうので、再実行しても直らない。
今はファイルが無ければ止まる。

**PEP 668。** Debian 12 以降（＝現行の Raspberry Pi OS）は system Python を
externally-managed としていて pip が一切入らない。`set -e` なので**スクリプトの最初の
install で落ちる**。venv を使えという案内を出すようにした。`--break-system-packages` は
使わない — OS 側の Python に対して名前の通りのことをする。

**shallow clone。** `west init -m <url> --mr <tag>` はマニフェストリポジトリを**フルクローン**
する。`west update --narrow -o=--depth=1` が浅くするのは *projects* だけ。実測で
`zephyr/.git` が 952 MB → 96 MB、**856 MB の削減**。`west list` がマニフェストを解決できる
ことは確認済み。

**supervisor 経由への変更。** `test_p0_roundtrip.py` — `make demo-p0` と `make check` が走らせ、
つまり誰もが最初に実行するパス — が生の `subprocess.Popen` で、ウォッチドッグも RSS 上限も
無かった。設計 §9.2 G8 が必須と書いているものが、一番よく走る経路にだけ無かった。
x86-64 では遅いテストで済むが、swap が 100〜200 MB しかない Pi では 37 MB/s のリークは
OOM kill になる。しかも OOM killer が Renode を選ぶとは限らない（supervisor 自身の Python、
sshd、pytest の方が先に殺される可能性がある）。

### この変更セットの検証記録（x86-64、2026-08-30）

`make check` を 2 回連続、両方とも `MAKE_CHECK_EXIT=0` / `CHECK PASSED`。

```
run 1: probe 34 pass / 0 fail / 3 skip | codecs 83 passed | native ok | demo-p0 2 passed 8.25s | 演習 6 passed 114.86s
run 2: probe 34 pass / 0 fail / 3 skip | codecs 83 passed | native ok | demo-p0 2 passed 7.53s | 演習 6 passed 114.76s
```

変更前の同じ区間は `demo-p0` 13.80s / 演習 131〜146s。起動待ちを固定 sleep から ready 行の
待機に変えた分がそのまま速くなっている。**遅くなっていないことではなく、速くなったことが
証拠**——固定 sleep は常に最悪ケース分だけ待っていた。

なお最初の通し実行は `MAKE_CHECK_EXIT=2` で落ちた。バックグラウンド実行の通知は
「exit code 0」と表示したが、それはラッパーの終了コードだった。**`make check` の終了コードは
自分で取ること。** これも `CHECK PASSED` バナーが存在する理由と同じ話。

---

## 4. Pi に着いたら最初に測ること

**測る前に数字を書き換えないこと。** このプロジェクトの唯一の規則。

### 4-1. Renode のビルド文字列

```bash
~/tools/renode_1.16.1-dotnet_portable/renode --version
```

`probe.sh` の aarch64 側のピン `d66b0c2a-202602160933` は、リリースアセットに埋まっている
バージョンリソースから読んだ値で、**arm64 ホストで実行して確認したものではない**。
違っていれば probe が明確に落ちる（それが正しい振る舞い）。落ちたら観測した文字列に直して、
コミットメッセージにそう書く。

### 4-2. 4 ノードの速度

```bash
PERF_FLOOR=0.1 make probe          # まず通して数字を見る
```

probe が出す ratio を記録する。それが Pi の実力値。記録したら `PERF_FLOOR` の既定値を
その値から余裕をとって設定し、**どのホストで測った数字かをコメントに書く**。
見積もりは Pi 5 で 0.6〜0.95x、Pi 4 で 0.23〜0.42x。大きく外れたら見積もりの方を疑う
（Geekbench は SIMD/crypto に重みがあり、Renode の DBT ループとは性格が違う）。

### 4-3. `ci` プロファイルとの関係

x86-64 では `ci` プロファイル（`AdvanceImmediately` 無し）がきっかり 1.000x になる。これは
リアルタイムスロットルが律速だから。**Pi ではハードウェアが律速になるので、
`AdvanceImmediately` は何も買わない。** つまり Pi では決定論的な `ci` プロファイルが速度を
一切犠牲にしない。Pi では全部 `ci` で走らせるのが正しい。

その代わり、壁時計の予算は全部引き直しになる。60 仮想秒が Pi 5 で実時間 80 秒前後。

### 4-4. メモリ上限

RAM に応じて。既定は 2048 MB。

| RAM | `CUBERANGE_RSS_CEILING_MB` | 備考 |
| --- | --- | --- |
| 16GB | 2048（既定のまま） | |
| 8GB | 1536 | 4 ノードのピーク 503 MB の 3 倍 |
| 4GB | 1024 | ＋ `CUBERANGE_SUPERVISOR_POLL_S=0.25` |
| 4GB 未満 | 非対応 | 収まる上限は誤検知を生む水準になる |

上限を超えてから kill が完了するまで最悪 6 秒、約 220 MB の行き過ぎがある。上限＋300 MB の
余裕を見ること。`CUBERANGE_SUPERVISOR_TIMEOUT_S` も上げる必要がある（既定 180 秒は x86-64 の
速度前提。遅い board では正当に遅い実行がハングとして再試行されてしまう）。

### 4-5. ディスク

実測（x86-64、今日）: 合計 8.3 GB。内訳は設計書の記述と合っていない。

| | 実測 | 設計書の記述 |
| --- | --- | --- |
| zephyrproject | 5.8 GB（shallow 化で ~5.0 GB） | 6.8 GB |
| zephyr-sdk | 2.0 GB | 829 MB |
| Renode | 218 MB（arm64 は 223 MB） | ~500 MB |

合計はたまたま近いが、内訳は全部違う。設計書 §の数字は直す価値がある。

**さらに削れる余地（未実装）。** `firmware/apps/comm` のクリーンビルドが実際にコンパイルする
のは 1110 翻訳単位で、内訳は picolibc 956 / zephyr 104 / libcsp 39 / hal_stm32 7 / app 2 /
生成 2。`hal/nxp` 1.3 GB、`lib/gui` 358 MB、`hal/nordic` 274 MB ほか約 55 モジュールからは
**1 つもコンパイルされていない**。実際に要る作業ツリーは ~1.10 GB で、`modules/` の 4.5 GB に
対して大幅に少ない。west 1.5.0 の `manifest.project-filter`（`+regex`/`-regex` のカンマ区切り、
最後にマッチしたものが勝つ）で落とせるはず。**未検証** — やるなら別コミットで、
ビルドが通ることを確認してから。

---

## 5. 触ってはいけないもの

`CLAUDE.md` のピン表が理由付きの一覧。移行で特に効くのは 2 つ。

- **Renode を 1.16.1 から下げない。** v1.16.1 のリリースノートの Fixed に
  "crash on older arm64 hosts (e.g. Raspberry Pi 4)" がある。ピンしているバージョンが
  その修正を含む最初のリリース。
- **ビルドピンをコミット接頭辞だけに緩めない。** タイムスタンプが 2 つのアーティファクトを
  区別する唯一の情報で、正しい方が入っている証拠そのもの。arch 別に分けるのが正しい。

Zephyr ビルドを Pi でやるかは判断が要る。7.8 GB のツールチェーンを置いて `make check` 1 回に
推定 25〜90 分かけて、6.4 MB の ELF を作ることになる。**x86-64 でクロスビルドした ELF を
持ち込んで Pi は実行専用にする**方が筋がいい。ELF はターゲット（Cortex-M）用なので
ホストのアーキテクチャとは無関係。ただし演習を書き換えながら学ぶ用途では Pi 上でビルド
できた方がよく、そこはトレードオフ。

---

## 6. 未確認・未完のもの

正直に列挙する。「たぶん大丈夫」は書かない。

**確認していないこと**

- 100.87.110.2 の機種・RAM・ディスク・OS。何も見ていない。
- arm64 Renode の実行そのもの。バイナリの中身は検査したが、arm64 上で走らせていない
  （このホストに qemu-user-static が無く、Pi に触る許可も無かった）。
- Pi での速度。全部見積もり。
- `manifest.project-filter` によるモジュール削減。

**設計書とツリーの乖離**（移行とは独立だが、Pi 側でも同じものを見ることになる）

- 設計 §9.2 の G1/G2 は全マルチマシンシナリオで `SetGlobalSerialExecution true` と
  `SetSeed <固定値>` を必須としているが、**4 つの `.resc` のどれにも入っていない**。
  §4.3 の `ci` プロファイルもツリーに存在しない。Pi では `ci` が速度を犠牲にしないので、
  ここを埋めるのは Pi 上でやる方が自然。
- 設計 §9.1 と §7 は `renode-test` と `verify.robot` を演習の必須構成要素としているが、
  **`.robot` ファイルは 1 つも無い**。全部 pytest。どちらかに寄せる必要がある。
- G9（quantum を変えたら 100 µs 参照トレースと 60 仮想秒以上を比較する CI チェック）は
  存在しない。CI 自体が無い。
- `tests/golden/csp.json` は**このリポジトリからは再生成できない**。`tools/gen_golden.py` が
  `./csp_oracle` と `./cryptolib/build/libcryptolib.so` を呼ぶが、どちらもソースも
  ビルド手順も追跡されていない。「自作コーデック同士で検証していない」という論拠を
  支えているファイルが、クローンしただけでは不透明なバイナリ成果物になっている。
- 演習 README の TTP フロントマターは、存在しない `tools/ttp_map.py` が
  「CI で ID の実在を検証する」と書いていた。**この文書と同じコミットで直した** —
  検証する仕組みが無いことを正直に書く形にした。

**時間予算が全部 x86-64 前提**

固定の秒数がコードに散らばっている。Pi では全部引き直しになる。env で上書きできるように
したものは上書きし、できないものは測ってから直すこと。

| 定数 | 既定 | 上書き |
| --- | --- | --- |
| ノード起動待ちの上限 | 30 s | `CUBERANGE_BOOT_TIMEOUT_S` |
| supervisor のウォールクロック | 180 s | `CUBERANGE_SUPERVISOR_TIMEOUT_S` |
| supervisor の RSS 上限 | 2048 MB | `CUBERANGE_RSS_CEILING_MB` |
| supervisor のポーリング間隔 | 0.5 s | `CUBERANGE_SUPERVISOR_POLL_S` |
| probe の速度床 | 1.5x | `PERF_FLOOR` |
| FDIR の復帰待ち | 45 s | `CUBERANGE_FDIR_WAIT_S` |
| `wait_rail(..., seconds=15/20)` | | **上書き不可**。ハードコード |

**FDIR の 45 秒は Pi 4 では確実に落ちる。** EPS はレール遮断の**仮想 30 秒後**に復帰させるが、
待ち側の予算は**実時間**なので、ホストのエミュレーション速度で必要な秒数が変わる。

| ホスト | 速度 | 仮想 30 秒に要する実時間 | 45 秒予算 |
| --- | --- | --- | --- |
| x86-64 | 2.34x（実測） | 約 13 秒 | 十分 |
| Pi 5 | ~0.75x（推定） | 約 40 秒 | **際どい** |
| Pi 4 | ~0.30x（推定） | 約 100 秒 | **落ちる** |

`make probe` で実測した比を使って `CUBERANGE_FDIR_WAIT_S ≒ 30 / 比 + 余裕` に設定すること。
この手の「仮想時間の定数を実時間の予算で待つ」箇所は他にもあるかもしれない。探すこと。

**未着手**（Pi 移行前からの残り）

- CI (GitHub Actions)
- EX-F01（ret2win のメモリ破壊演習）。SRAM は XN で **Renode はそれを実際に強制する**ことを
  実測済みなので、shellcode ではなく code reuse でなければならない。Zephyr のオプション差分
  ゼロで成立することも確認済み。
- SDLS / Phase 2
- GUI（設計のみ。Renode のネイティブ GTK GUI は WSL2 では window が開かないことを実測済み。
  `--server-mode` の WebSocket 経由がブラウザ GUI の道）

---

## 7. 公開済みであることの帰結

リポジトリは **public**。`SAFE_USE.md` / `SECURITY.md` / `ASSURANCE.md` /
`docs/legal/export-control.md` がその前提で書いてある。

- 輸出管理は **UNVERIFIED**。"EAR99" や "not ITAR" と書かない。担当者も未割当。
- 実在の識別子・周波数・鍵・エンドポイントを足さない。
- 攻撃コードが動く状態で公開されている。演習を足すときは `CONTRIBUTING.md` の 3 条件
  （攻撃が通る / 緩和が止める / **緩和後も正規操作が通る**）を必ず満たす。

**コミットの著者情報を確認すること。** 28 個のコミットが `tachibana@nri-secure.co.jp` を
author/committer に持ったまま public リポジトリに push されている（初回コミットだけは
GitHub の noreply アドレス）。意図的ならそのままでよい。意図的でないなら、履歴の書き換えが
必要で、push 済みなので破壊的な操作になる。**判断はユーザーのもの。勝手に書き換えない。**
