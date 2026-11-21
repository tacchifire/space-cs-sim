# 共通ノード基板 ピンマップ: 確定事実と未決事項（ドラフト）

- 作成日: 2026-11-20（フェーズ1「仕様凍結」準備ドラフト）
- 調査範囲: `firmware/` 配下（各アプリの `boards/nucleo_h753zi.overlay`、`crosslink.overlay`、`prj.conf`、追加 conf、`src/main.c`、共通コード）、`Makefile`（`BOARD ?= nucleo_h753zi`）、`firmware/common/identity.cmake`、設計書 `docs/superpowers/specs/2026-08-28-cuberange-design.md` の関連記述
- 原則: リポジトリ内のファイルに根拠があるものだけを「確定事実」とした。Zephyr のボード定義（`nucleo_h753zi.dts` 等）はリポジトリ外（ツールチェーン側）にあるため、そこから継承している項目は「リポジトリ内では未確認」と明記した。一般知識で補える項目も、根拠がリポジトリ内にないものは確定扱いにしていない。

## 1. 確定事実（リポジトリ内に根拠あり）

### 1.1 プラットフォーム前提

| 項目 | 事実 | 根拠 |
|---|---|---|
| ボード／MCU | Zephyr ボード `nucleo_h753zi`（STM32H753）。全ファームウェアはこのボード向けにビルドされる | `Makefile`: `BOARD ?= nucleo_h753zi`、各 mitigation.md の `west build -b nucleo_h753zi` |
| エミュレーション側モデル | Renode は `platforms/boards/nucleo_h753zi.repl` を使用（中身は STM32H743 系モデル流用。`.repl` 内の gpioPortB `invertedAFPins` 等の記述が各 overlay のコメントで引用されている） | 各 `scenario.resc`、各 overlay のコメント |
| ノード役割と CSP アドレス | 衛星0: OBC=1, EPS=2, ADCS=4, COMM=5、クロスリンク端末=6（base+6）、SCID/APID=0x0A9。衛星 index で +8 ずつ（最大4衛星）。地上局 source id: primary=66 (0x0042), backup=67 (0x0043) | `firmware/common/identity.cmake` |
| 使用ペリフェラルの範囲 | 全アプリの `prj.conf` で有効なのは LIBCSP、CAN、SERIAL/UART（COMM のみ）、GPIO（EPS/ADCS/OBC のみ）、イベント、ヒープのみ。**ADC・I2C・SPI・USB・Ethernet の有効化はリポジトリ内のどの conf にもない** | `firmware/apps/*/prj.conf`、`sdls.conf`、`pus_auth.conf`、`crosslink.conf` |

### 1.2 CAN（FDCAN1: 内部バス）

| 項目 | 事実 | 根拠 |
|---|---|---|
| コントローラ | FDCAN1 を使用。全アプリが `DT_CHOSEN(zephyr_canbus)` 経由で取得（ボード定義側で `zephyr,canbus = &fdcan1` と宣言されている、と csp_ping のコメントが明記） | `firmware/apps/*/src/main.c`、`csp_ping/src/main.c` コメント |
| ビットレート | **1,000,000 bps**（全アプリ共通の `CAN_BITRATE` 定数。adcs/eps は 1000000 表記、他も同値） | 各 `src/main.c` |
| CAN FD | **不使用（classic CAN）**。`CONFIG_CAN_FD_MODE` は off で `CAN_MAX_DLEN` は 8、とクロスリンク conf のコメントが明記 | `firmware/apps/comm/crosslink.conf` コメント |
| FDCAN1 の TX/RX ピン | **リポジトリ内の overlay では指定されていない**（fdcan1 ノード自体に触れているファイルがない）。ピンは Zephyr ボード定義（Nucleo-H753ZI 用 dts）からの継承 | overlay 一式に fdcan1 の pinctrl 記述なし（不在が根拠） |
| カーネルクロック | ボード定義側の &fdcan1 ノードが KERNEL クロック源（PLL2_Q）を追加している（クロスリンク overlay が fdcan2 用に「ボードの &fdcan1 の clocks を複写した」と明記していることからの間接確認） | `crosslink.overlay` コメント |
| トランシーバ | Nucleo-144 には CAN トランシーバが1つ載っており fdcan1 がそれを使う、と overlay が明記。**自作基板側のトランシーバ選定に関する記述はリポジトリ内にない** | `crosslink.overlay` コメント |

### 1.3 CAN（FDCAN2: クロスリンク、対象ビルドのみ）

| 項目 | 事実 | 根拠 |
|---|---|---|
| 有効化範囲 | クロスリンク系ビルドでのみ `EXTRA_DTC_OVERLAY_FILE` で追加適用。通常ビルドの COMM には含まれない（vuln/hard ペアの Kconfig 比較を壊さないため、と明記） | `crosslink.overlay` 冒頭コメント、`Makefile` の各 firmware-* ターゲット |
| ピン | **PB12 = RX、PB13 = TX**（このパッケージの FDCAN2 ピン配置として明記） | `crosslink.overlay`: `pinctrl-0 = <&fdcan2_rx_pb12 &fdcan2_tx_pb13>` |
| 競合 | PB13 は Ethernet TXD1 と兼用で、Nucleo 実機ではジャンパ JP6/JP7 で解決する（ボード定義の警告を引用）。COMM はネットワークを使わないため mac ドライバがピンを占有することはない、と明記 | `crosslink.overlay` コメント |
| クロック | `clocks` にバス APB1_2 ＋ KERNEL 源 PLL2_Q（`STM32_SRC_PLL2_Q FDCAN_SEL(2)`）の2要素が必須。欠けるとビットレート導出の基準がない | `crosslink.overlay` 本体とコメント |
| メッセージ RAM | fdcan1 と共有（0x4000AC00）、fdcan2 のセクションは `bosch,mram-cfg` オフセット 0x350 | `crosslink.overlay` コメント |
| ビットレート | FDCAN1 と同じ 1 Mbps（`CAN_BITRATE` を XLINK インターフェースにも使用） | `comm/src/main.c`（csp_can_open_and_add_interface の XLINK 側呼び出し） |
| Renode の注意 | Renode はピンマルチプレクスをモデルしないため、上記ピン選択は Renode 上の動作には無関係。「実機で動かないデバイスツリーを事実らしく書かない」ために選定された、と明記されている | `crosslink.overlay` コメント |

### 1.4 UART

| 項目 | 事実 | 根拠 |
|---|---|---|
| スペースリンク | **USART2、PA2 = TX / PA3 = RX、115200 bps**。SoC dtsi では `status = "disabled"` のため overlay で有効化。エイリアス `spacelink` | `comm/boards/nucleo_h753zi.overlay`、`csp_ping/boards/nucleo_h753zi.overlay`（同一内容） |
| コンソール | **USART3** が `zephyr,console` 兼 `zephyr,shell-uart`（ボード定義側の設定、と overlay コメントが明記）。リンクと分ける理由は、共有すると地上局がブートバナー 9239 バイトを受け取るため | 同 overlay コメント、設計書も同旨 |
| USART3 のピン | **リポジトリ内では指定されていない**（ボード定義からの継承。Nucleo の ST-LINK VCP 接続かどうかもリポジトリ内根拠なし） | overlay 一式に usart3 の pinctrl 記述なし |
| ログ | `CONFIG_LOG=n`（全アプリ）。コンソールには自前 printk のみ。USART2 にはリンクプロトコル以外を流さないことが不変条件 | 各 `prj.conf` とコメント |

### 1.5 GPIO（役割表示）

| 信号 | ピン | 極性・バインディング | 根拠 |
|---|---|---|---|
| EPS: COMM レール（負荷スイッチ表示） | **PD5** | `GPIO_ACTIVE_HIGH`、`gpio-leds` バインディング、エイリアス `commrail` | `eps/boards/nucleo_h753zi.overlay` |
| ADCS: タンブル表示 | **PD6** | 同上、エイリアス `tumbleflag` | `adcs/boards/nucleo_h753zi.overlay` |
| OBC: FDIR inhibit 表示 | **PD7** | 同上、エイリアス `fdirinhibit` | `obc/boards/nucleo_h753zi.overlay` |
| ポート D を使う理由 | Renode の stm32h743 モデルでポート B の複数ピン（PB0, PB1, PB6, PB7, PB13, PB14, PB15）が `invertedAFPins` に入り、AF 設定時に ODR と逆に読めるため。ポート D には該当なし。**これは Renode モデル側の事情であり、実機の電気的制約ではない**（overlay コメント自身が Renode の `.repl` を根拠に挙げている） | 各 overlay コメント | 
| 初期値 | EPS レールは起動時 ON（`GPIO_OUTPUT_ACTIVE`）、ADCS/OBC の表示は INACTIVE 開始 | 各 `src/main.c` |

### 1.6 その他（ファームウェアから読み取れる制約）

| 項目 | 事実 | 根拠 |
|---|---|---|
| 暗号 | SDLS／PUS認証は mbedTLS（AES-256-GCM、ソフトウェア実装）。H7 の CRYP ハードウェアアクセラレータは使っていない（設計書 6.5: CRYP は金庫ではなくアクセラレータで、鍵はファームウェア管理のメモリに残る、と EX-F02 文書も明記） | `sdls.conf`、`pus_auth.conf`、EX-F02 README |
| スタック | 暗号使用ビルドでは `CONFIG_MAIN_STACK_SIZE=3072` ＋ `CONFIG_HW_STACK_PROTECTION=y` が必須級（GCM コンテキスト 424 バイトによるスタック溢れの実測障害が記録されている） | `sdls.conf`、`pus_auth.conf` コメント |
| ヒープ | クロスリンク（CAN IF×2）では `CONFIG_HEAP_MEM_POOL_SIZE=8192`、通常は 4096 | `crosslink.conf`、`prj.conf` |
| 鍵の配置 | SDLS 鍵は `static const`（`firmware/common/cuberange_keys.h`）＝フラッシュ `.rodata` 常駐。セキュアエレメント等は使わない設計 | `cuberange_keys.h`、EX-F02 README |

## 2. 未決事項（共通ノード基板のピンマップ確定に必要な論点）

### A. CAN まわり

1. **FDCAN1 の TX/RX ピン選定**: 現行ファームはボード定義継承で、リポジトリ内にピン指定がない。自作基板では候補ピン（パッケージの AF 表に基づく選定）から確定が必要。FDCAN2 を PB12/PB13 に置く前提（§1.3）と競合しない組み合わせにすること。あわせて、ファーム側に自作基板用 overlay（pinctrl 明示）を新設するかも決める。
2. **トランシーバ選定と制御ピン**: 型番（3.3V 系か 5V 系か、VIO の扱い）、standby/silent 制御ピンを MCU GPIO に出すか固定するか、サイレントモードを故障注入（バスを黙らせる演習的用途）に使うか。現行ファームにトランシーバ制御の概念はない。
3. **終端抵抗の実装**: ロードマップ決定は「幹線両端のみ・切替は故障注入として分離」。ノード基板側に終端を載せるか（DIP／ジャンパ／未実装パッド）、バックプレーン側に寄せるかは未決。120Ω の実装形態と、誤って全ノード終端になる事故の防ぎ方も含む。
4. **クロスリンク（FDCAN2）を v1 基板に含めるか**: 対応表の X01/S01/S02/D01 の4件が依存。含める場合: 第2トランシーバ、PB13 と Ethernet 兼用問題は自作基板では Ethernet を載せなければ消えるが、**PB13 は Renode モデルの invertedAFPins 対象ピン**であり、実機ピンマップと Renode 検証の対応付けに注意が要る。含めない場合は4件の後段送りを仕様凍結文書に明記する。
5. **CAN クロックの確定**: FDCAN カーネルクロックは PLL2_Q（ボード定義継承＋ crosslink.overlay の明示）。自作基板のクロックツリーで PLL2_Q の周波数をいくつにし、1 Mbps で目標サンプルポイント（Zephyr 既定の算出に任せるか明示するか）を満たすかは未決。ビットレート自体（1 Mbps）はファーム固定で確定済み。

### B. クロック／水晶

6. **システムクロック源と水晶**: リポジトリ内には HSE の周波数・有無（Nucleo の ST-LINK MCO バイパスか水晶か）・SYSCLK 周波数の指定が**一切ない**（全てボード定義継承）。自作基板では水晶の周波数と負荷容量、MCO 入力を採るか、PLL 設定（SYSCLK／PLL2_Q の元）を確定する必要がある。これが決まらないと §A-5 も確定しない。
7. **LSE（32.768 kHz）の要否**: RTC／低電力用途は現行ファームに存在しない。載せるか否かは未決（部品点数と将来の時刻系演習に影響）。

### C. コンソール／USB／デバッグ

8. **コンソール（USART3）の物理インタフェース**: ロードマップの目標像は「USB-C コンソール」を含むが、現行ファームに USB デバイス機能はない。選択肢 — (a) USB-UART ブリッジ IC を載せて USART3 に接続、(b) STM32H753 ネイティブ USB（OTG_FS）で CDC を実装（ファーム変更が要る）、(c) USB は給電のみでコンソールはピンヘッダ UART — のどれにするか未決。あわせて USART3 の TX/RX ピン自体の確定（現行はボード継承で未指定）。
9. **スペースリンク（USART2）の基板上インタフェース**: PA2/PA3 は確定事実。ただし基板からどう出すか（バックプレーン経由か、COMM ノードのみコネクタ直出し か）と、GS ブリッジとの接続形態（レベルは 3.3V TTL 前提でよいか）は未決。COMM 以外のノードにも USART2 を配るか（現行ファームで USART2 を使うのは COMM と csp_ping のみ）。
10. **SWD の引き出し方**: リポジトリ内に SWD に関する記述は**一切ない**。コネクタ形式（Tag-Connect／10ピン Cortex Debug／ピンヘッダ）、SWDIO/SWCLK（PA13/PA14 はパッケージ固定だがリポジトリ内根拠はなし＝要データシート確認として扱う）、SWO を出すか、バックプレーン経由で4ノード分を集約できるか（信号品質的に個別が無難か）は未決。全演習のイメージ切替え手段に直結する最重要論点の1つ。
11. **NRST/BOOT の外部制御**: ロードマップの設計要件（演習後の確実な初期化）に含まれるが、ファーム・リポジトリ側に具体化はない。BOOT0 ピンの引き出し（DFU ブートローダ経由の再書込みを予備経路にするか）、NRST をバックプレーン／制御ノードから駆動できるようにするか、その制御 GPIO の割り当ては未決。

### D. 電源／計測

12. **EPS レール信号（PD5）と実負荷スイッチの関係**: 現行ファームの PD5 は「表示用 GPIO」であり、Renode ではホストが代行して電源断を模擬した。自作基板では PD5 が直接ロードスイッチ IC の EN を駆動するのか、別 GPIO を割り当てるのか、スイッチは EPS ノード基板側に置くのかバックプレーン側に置くのかが未決。PD5 のままにすればファーム変更なしで実スイッチ化できる（最有力だが未決定）。
13. **ノード別電源スイッチの制御主体**: ロードマップは「ノード別電源スイッチ＋電流計測」を共通基板の機能とするが、誰がスイッチを制御するか（EPS ファームの GPIO か、バックプレーン側の常時給電ドメインの制御回路か、物理スイッチ併用か）は未決。EPS 自身の電源を誰が切るかも含む。
14. **電流計測の方式と ADC ピン**: 現行ファームに ADC 使用は皆無。計測 IC（I2C 出力の電流モニタ等）か、シャント＋MCU ADC かで必要ピンが変わる。後者なら ADC チャンネルのピン割り当てと、ファームへの計測値取り込み（現行プロトコルに電流値を載せるフィールドはない＝ファーム変更の要否）までが論点。I2C を使う場合は I2C バスのピン（現行未使用）も新規確定が要る。
15. **電源入力とレギュレーション**: EPS 単点受電は USB-C 5V（ロードマップ）。ノード基板側の 3.3V 生成（LDO／DC-DC 選定）、STM32H753 の内部レギュレータ方式（LDO/SMPS 選択はピンの結線に影響）、VCAP ピンの扱い等、電源構成に伴うピン確定事項はリポジトリ外の論点として未決。
16. **役割表示 GPIO（PD5/PD6/PD7）の扱い**: ポート D 選定の理由は Renode モデルの都合であり実機制約ではない。自作基板でも PD5/6/7 を踏襲すればファーム無変更で済むが、役割ごとに同一基板を使う設計では「表示 LED は全基板に載せ、意味は役割ファームが決める」形になる。LED の実装（全基板共通で3つ載せるか）と、PD5 のスイッチ兼用（論点12）との整理が要る。

### E. 役割別 I/O・その他

17. **役割別 I/O の範囲**: ロードマップは「役割別 I/O」を共通基板の差分要素とするが、現行ファームが要求する役割別信号は表示 GPIO 3種のみ。ADCS の実アクチュエータ（マグネトルカ駆動回路等）や EPS の追加レールを v1 でどこまで基板に載せるかは未決（対応表上は A01/B01 とも表示レベルの等価実装で成立する）。
18. **未使用ピンの処理と基板テストポイント**: 浮いたピンの処理、量産・立ち上げ確認用のテストポイント（電源・SWD・UART・CAN の最低限）の配置方針は未決。
19. **基板識別（役割／個体）**: 同一基板を4枚使うため、役割や衛星 index はビルド時定数（identity.cmake）で決まる。基板側に ID 用のストラップピン／DIP を持たせてファームが読む形にするか否かは未決。

## 3. 完全性についての注記

このドラフトの元になった調査では未決事項は20件挙がっていたが、20件目の文面と19件目の末尾は、レビュー前に書き込みの切り捨てで失われた。ここでは再構成せず、欠落として記録する。再導出はフェーズ1の作業である。
