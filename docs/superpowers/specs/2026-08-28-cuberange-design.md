# CubeRange — 設計仕様書

**リポジトリ**: `space-cs-sim`
**プロダクト名**: CubeRange（暫定 / 公表前に商標確認）
**版**: 3 版（2026-08-28）
**状態**: 主要な技術前提を実測で確定。実装計画の作成待ち

---

## 0. この文書の証拠規則

**実行して確かめていない主張は書かない。**

1 版はソースを読んだだけで「検証済み」と称し、Renode を起動した結果 6 件が反証された。
2 版は Codex による独立レビューで 17 件（うち CRITICAL 3 件）の欠陥を指摘された。
3 版は、それらを **13 のエージェントによる実測プローブ**で決着させた結果である。
是正の全記録は §16 にある。

再現手段: `tools/renode-probe/probe.sh`（前提の検証ゲート。CI の最初のジョブ）。
このハーネスは**わざと失敗する自己テスト**を内蔵する — 初版が「出力ディレクトリが無いと全項目 PASS」
という偽の成功を出したため、**失敗できないハーネスは証拠として認めない**。

---

## 1. 目的とスコープ

### 1.1 一文で

**Renode 上で衛星のファームウェアを命令レベルで動かし、地上局・無線リンク・衛星内部バス・
OBC ファームの 4 層すべてに実際に攻撃を通せる、宇宙システム版の RAMN。**

### 1.2 「ビジネスでも使える水準」の定義

これは**成熟度の主張ではなく品質バー**である。適用先はスコープを広げる方向ではなく、狭める方向。

| バー | 具体的に何を満たすか |
| --- | --- |
| 攻撃が本当に通る | 各演習に「攻撃成功」を assert する CI テストがある |
| 防御が本当に効く | 同じ演習に「対策適用後は攻撃が失敗し、正規動作は成功する」テストがある |
| 誠実である | 脆弱版と対策版で**防御機構を弱めない**。差分は境界検査 1 行（§7.3） |
| 再現する | 固定バージョン・決定的実行（§9.2）。特権も GUI も不要 |
| 主張が検証可能 | すべての前提が `probe.sh` で再現でき、CI で継続確認される |
| 現実と地続き | 実標準のサブセット。**独立オラクル**で突き合わせる（§9.3） |
| 実機化を塞がない | 実在 MCU + Zephyr。Renode 固有の抜け道を使わない |
| 商用利用可 | 自作コードは Apache-2.0。コピーレフト / NOSA を取り込まない |

**明示的に名乗らないもの**: 飛行認定、実時間検証、RF 適合、暗号認証、MCU 完全再現。
`ASSURANCE.md` にこの境界を書く（§12）。

### 1.3 非目標（YAGNI）

高精度軌道力学 / 実 SDR / CTF スコアサーバ / 物理基板の製造 / CCSDS フル準拠。

---

## 2. ポジショニング

| 既存 | 忠実度 | ライセンス | 空白 |
| --- | --- | --- | --- |
| **NASA NOS3** (617★) | システム／運用レベル。cFS は Linux でネイティブ実行 | NOSA 1.3 | ファーム内部の攻撃ができない。NOSA は商用障壁 |
| **Hack-A-Sat** QEMU 一式 (97★) | ファームレベル | 混在 | 2020 年で停止。単発 CTF。バス・地上系が非統合 |
| **RAMN** (275★) | 命令レベル | — | 完全に自動車ドメイン |
| **Renode** (2786★) | エミュレータ本体 | **MIT** | 宇宙向けの記述もシナリオも無い |
| **Yamcs / OpenC3** | 運用レベル | AGPL-3.0 / source-available | 攻撃側の道具ではない |

**我々の位置**: 命令レベルの衛星ファーム + 内部バス + 地上系を一本に繋ぎ、
**双方向 CI 検証済みの**セキュリティ演習を同梱したもの。NOS3 とは補完関係。

---

## 3. 実測に基づく技術前提

### 3.1 プラットフォーム

| 決定 | 実測根拠 |
| --- | --- |
| **Renode 1.16.1（リリース版に固定）** | portable-dotnet を導入。version/build を `probe.sh` が assert |
| **ノード MCU = STM32H753**（`platforms/boards/nucleo_h753zi.repl`） | `fdcan1/2: CAN.MCAN`、`crypto: STM32H7_CRYPTO`、`rng`、`watchdog`、`i2c1`、9 本の UART、`ethernet` を `peripherals` 出力で確認 |
| **RTOS = Zephyr** | Antmicro 配布の `nucleo_h743zi--zephyr-*` ELF が 4 ノードで起動し CAN 相互受信 |
| **CSP = libcsp を採用（自作しない）** | libcsp 2.1 は**正式な Zephyr モジュール**（`zephyr/CMakeLists.txt`, `src/drivers/can/can_zephyr.c`, `src/arch/zephyr/*`, Zephyr サンプル）。MIT。このホストでビルド・実行し CFP のゴールデンベクタを生成済み |

**STM32L552 を採らない理由**: 1.16.1 の `stm32l552.repl` に FDCAN が無く（master 限定）、
Zephyr の `nucleo_l552ze_q` も CAN 非対応（`supported:` に `can` 無し、dtsi は `status="disabled"`）。
TrustZone を失うため、セキュアブートは TF-M ではなく MCUboot で行う。

### 3.2 Renode の欠陥登録簿（実測。設計はこれを回避する）

| # | 欠陥 | 影響 | 回避 |
| --- | --- | --- | --- |
| D1 | `Sensors.OB1203 @ i2c1` がコンストラクタで `ArgumentException("Field LED_FLIP intersects with another range")` → **プロセス abort** | 環境光センサが使えない | 太陽センサは ADC で表現（§3.4） |
| D2 | `Sensors.VEML7700` が 1.16.1 に存在しない（E04） | 同上 | 同上 |
| D3 | WebSocket `sensor-set` の `magnetic-flux-density` が `AK0991x` の `RecoverableException` を誘発し **abort** | GUI から磁力計を書けない | `DefaultMagneticFluxDensity{X,Y,Z}` か RESD を使う |
| D4 | `sysbus.usartX BaudRate` の getter が未設定 UART で `DivideByZeroException` → **abort** | 任意のプロパティ取得が全ノードを落としうる | **Monitor には例外隔離が無い**。ホットループで使うコマンド文字列を allowlist 化する |
| D5 | Monitor が科学記法を**無言で破壊**（`1e3`→1、`1.5e-2`→1.5、`0b101`→0）。`.5` は構文エラー | センサ値が静かに壊れる | 送信前に必ず固定小数点整形（`f"{v:.6f}"`） |
| D6 | `-e` 引数は `;` で 1 行に連結され、**最初の失敗で残り全部が中断** | シナリオが黙って部分適用される | 長い `-e` 連鎖を使わない。TCP Monitor に 1 行 1 コマンドで送る |
| D7 | `machine Pause` は**時間ドメイン全体**を凍結。`RunFor` フローでは無効化される | ノード単体を止められない | `cpu IsHalted true` を使う（§5.3） |
| D8 | `machine Reset` は `RunFor` 停止状態で**デッドロック** | 復電が固まる | `machine RequestReset` を使う |
| D9 | `emulation RemoveMachine` 後の `RunFor` が **SIGSEGV**（exit 139、2 回再現） | — | 使わない |
| D10 | `.repl` の `@ none` 形式は**無言で何も生成しない**（診断なし） | インジェクタが存在しないまま動く | `NullRegistrationPoint` に登録する |
| D11 | ハブに繋ぐ `ICAN` が未登録オブジェクトだと例外が配送ループ内で発生し、**以後すべてのノードのフレームがハブごと毒される** | バス全体が沈黙 | 必ずマシンのペリフェラルとして登録 |
| D12 | 不正なハンドシェイクは External Control のリスナーを**恒久破壊** | 制御チャネル喪失 | クライアントを堅牢化。復旧は `CreateExternalControlServer` の再発行 |
| D13 | Monitor へ行末未完のまま切断を繰り返すと 54 回目でリスナーが**恒久的に詰まる** | 制御チャネル喪失 | 切断前に必ず `\n` を送る。connect タイムアウトを検出して Renode を再起動する監視役を置く |
| D14 | 失敗コマンドは Renode を対話 Monitor に落とす。stdin が TTY だと**永久に停止** | CI が固まる | CI は必ず `< /dev/null` と実時間タイムアウトを付ける |
| D15 | `CANTester` / `CANKeywords` が **1.16.1 に存在しない**（型解決が None。`TerminalTester` は解決する） | Robot に CAN キーワードが一切無い | CAN の assert は自作インジェクタ経由か UART 観測で行う |
| D16 | 未マップ番地への命令フェッチ／アクセスで**フォールトが起きない**（0x10000000 で CFSR=0, HFSR=0） | 野良ポインタ検知の演習が成立しない | そのような演習を作らない |
| D17 | `MPU_CTRL.PRIVDEFENA=0` が特権アクセスで無視される（実機なら MemManage） | 「領域未定義＝拒否」に依存できない | 明示的に定義した領域のみに依存する |
| D18 | Renode の CANHub はバス ACK を模擬しない | **CAN エラーフレーム／bus-off の演習は成立しない** | 作らない（§7.5 で明示） |
| D19 | `machine Reset` は RAM をゼロ化しない。清浄状態は `LoadELF` と Zephyr の `.bss` ゼロ化に由来 | 古いヒープが電源断を生き延びる | 復電手順に `LoadELF` を必ず含める |
| D20 | `CANMessageFrame(id, data)` の 2 引数版は**標準 11 bit フレーム**を作る | 拡張 ID でフィルタしている受信側が黙って落とす。フレームの中身は完全に正しいまま届かない | `extendedFormat: true` を明示する |
| D21 | libcsp は `csp_conf.version` の既定が **2** で、ヘッダと CFP の配置を**実行時**に選ぶ | 「CSP v1 を使う」は firmware で設定しない限り真にならない。全ノードが v2 同士なら健全に見え、v1 のツールだけが無言で無視される | `csp_init()` の前に `csp_conf.version = 1` を明示 |

いずれも `probe.sh` の否定アサーションとして固定し、将来の Renode が直したら CI が気付く。

### 3.3 性能（実測）

**測定方法**: 壁時計はプロセス起動（約 4.5 秒）と ELF 取得を含むため過小に出る。
Renode 自身の `emulation GetTimeSourceInfo` が返す `Cumulative load`（仮想 1 秒あたりのホスト秒）を
使い、実時間比は `1/load` とする。

#### 遅さは既定値の産物だった（2 版の中心的前提を撤回）

2 版は「4 ノードは実時間の 0.19〜0.30 倍」を土台に、時間圧縮を含む設計全体を組んでいた。
これは**ワークロードの性質ではなく Renode の既定値 2 つの産物**である:
既定の 100 µs グローバル量子と、**エミュレーションを 1.0× で頭打ちにする実時間スロットル**。

4 ノード・実 Zephyr で自分で測り直した結果:

| 設定 | `Cumulative load` | 実時間比 |
| --- | --- | --- |
| `SetGlobalQuantum "0.0001"`（既定相当） | 3.320 | **0.301×** |
| `SetGlobalQuantum "0.002"` のみ | 0.99995 | **ちょうど 1.000×** ← スロットル上限 |
| `+ SetGlobalAdvanceImmediately true` | 0.427 | **2.34×** |

「ぴったり 1.000×」がスロットルの存在を示している。仮想 60 秒が
**エミュレーション 25.6 秒 / 壁時計 34.4 秒**で終わり、4 ノードすべてが起動して
各 357 メッセージを受信した（ファームの挙動は正常）。

**帰結**: パス単位（60 仮想秒）の体験は**十分に対話的**で、時間圧縮は不要。
時間圧縮が要るのは軌道スケールの現象だけ（90 分軌道 = 5400/2.34 ≈ 38 分）。

#### その他の実測値

| 項目 | 結果 |
| --- | --- |
| 4 ノード、負荷 18.5/14 コア | 0.079× — **負荷下の値は無意味。静穏ホストでのみ assert する** |
| ログ抑制の効果 | **2 版の「約 12 倍」は誤り**。未実装レジスタ警告は**起動時のみ**（約 56 行/ノード）で 0〜9%。12 倍を出したのは `LoggingUartAnalyzer` に UART を通していたためで、警告のせいではない |
| `cpu PerformanceInMips` | **速度に影響しない**（1〜400 MIPS で 1% 未満） |
| ホストのコア数 | **効かない**。Renode の 1 スレッドが 100% に張り付き、合計でも 14 コア中 2.56。**シングルスレッド性能を買う** |
| RSS | 既定 948 MB（4 ノード）/ 調整後 503 MB。ノードあたり +50〜120 MB |
| 5 台目のノード | 壁時計 +12〜26%、RSS +107 MB。**負担にならない** |
| External Control 対 Monitor | **中央値で約 44 倍高速**（GPIO 読み 0.162 ms 対 7.112 ms） |
| Monitor バッチ（`;` 連結） | 未バッチ 437 ms/ステップ（物理 2.3 Hz が上限）→ バッチ 57 ms（17.4 Hz） |

#### 推奨実行設定

```
emulation SetGlobalQuantum "0.002"          # バイト同一を保てる最大の量子
emulation SetGlobalAdvanceImmediately true  # interactive のみ。1.0× 上限を外す
logLevel 3 sysbus / rcc / fdcan1            # 可読性のため。性能効果は 9% 以下
# cpu PerformanceInMips は設定しない（無効）
```

**量子は精度パラメータである。** 2 ms を超えると**エラーも出さずに**ファームのタイミングが狂う:
5 ms で 20 仮想秒あたりカウンタ 1 周期を喪失（約 2.5%）、100 ms で約 28% かつノード間の合意が崩れる。
しかも**誤差は時間依存**で、5 ms は 5 秒と 10 秒では正しく見え 20 秒で初めて破綻した —
**短いスモークテストでは捕まらない**。量子を 2 ms に固定し、
**60 仮想秒以上の走行を 100 µs 基準トレースと比較する CI チェック**を置く。

### 3.4 センサ／アクチュエータ（実測で確定）

| CubeSat 機能 | 機構 | 状態 |
| --- | --- | --- |
| 温度（サブ度精度） | `Sensors.MAX77818.Temperature` または `LSM9DS1_IMU.Temperature` | **TMP108 は整数度に量子化**され [-128,127] にクランプされるため不可 |
| 電池電圧・電流 | `Sensors.MAX77818`（`Current` / `CellVoltage`、書き込み可） | PAC1934 は**設定可能プロパティを一切持たない**ため不可 |
| ジャイロ + 加速度 | `Sensors.LSM9DS1_IMU` | 実測 OK |
| 磁力計 | `Sensors.AK09916`（`IMagneticSensor` 実装）の `DefaultMagneticFluxDensity{X,Y,Z}` | 12000 を書いて `0x2EE0` を読み戻し確認。**WebSocket 経由の set は D3 で落ちる** |
| 太陽センサ（粗） | **ADC**: `adc3 SetDefaultValue <mV> <ch>` / `FeedVoltageSampleToChannel <ch> <mV> <n>` / `FeedVoltageSampleToChannel <ch> @<file>` | 3 形式すべて実測 OK。実 CubeSat の粗太陽センサもフォトダイオード + ADC |
| 太陽電池・母線電圧 | 同上 | 同上 |
| 決定的な時系列注入 | **RESD**（REnode Sensor Data）。`tools/csv2resd/csv2resd.py` が同梱 | CSV → RESD 変換器を確認。CI リプレイの正規経路 |
| ロードスイッチ・磁気トルカ | GPIO。マシン間は `emulation CreateGPIOConnector` | EPS の PB0 が COMM の `gpioPortC` IDR bit7 を駆動することを実測 |

**GPIO コネクタの要件**（実測で判明）: `AttachTo` が**必須**（先に呼ばないと "is not connected"）。
1 コネクタあたり最大 2 ペリフェラル。イベントは `HandleTimeDomainEvent` で遅延配送されるため
**仮想時間が進んでいないと届かない**。極性はピン依存（`stm32h743.repl` の `gpioPortB` には
`invertedAFPins` エントリがある）— ロードスイッチには該当しないピンを選ぶか、assert 前に極性を実測する。

---

## 4. アーキテクチャ

### 4.1 5 つのプレーン

```
┌──────────────────────────────────────────────────────────────────────┐
│ PRESENTATION PLANE            ブラウザ（WSL2 でも Windows 側から到達）  │
│   運用コンソール / 衛星状態 / バスタイムライン / 演習の進行             │
│   Renode の Web ターミナル（/telnet/<id>）も同じ画面に埋め込む          │
└───────────────┬──────────────────────────────┬───────────────────────┘
                │ WebSocket (自前 GS backend)   │ WebSocket (Renode --server-mode)
┌───────────────▼──────────────────────────────┼───────────────────────┐
│ GROUND PLANE                    (ホスト/Python)                       │
│   gs-core … TC 組立 / TM 復号 / PUS / SDLS / sqlite                   │
│   attack-kit … キャプチャ・リプレイ・偽装・注入                        │
└───────────────┬──────────────────────────────┼───────────────────────┘
                │ TCP (frames)                  │
┌───────────────▼──────────────────────────────┼───────────────────────┐
│ CHANNEL PLANE 「真空」          (ホスト/Python)                        │
│   パス窓(AOS/LOS) / 伝搬遅延 / ビット誤り / 断 / tap & inject          │
└───────────────┬──────────────────────────────┼───────────────────────┘
                │ TCP → usart2 socket terminal  │
┌───────────────▼──────────────────────────────▼───────────────────────┐
│ SPACE PLANE              Renode 1.16.1 / STM32H753 / Zephyr           │
│   ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐   ┌─────────────────────────┐  │
│   │ COMM │ │ OBC  │ │ EPS  │ │ ADCS │   │ attacker (空のマシン)    │  │
│   └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘   │ TcpCanInjector (C#, 90行)│  │
│      └────────┴────────┴────────┴───────┴─────────┬───────────────┘  │
│                    canHub (CSP over CAN / libcsp CFP)                 │
│      EPS.gpioPortB[0] ──GPIOConnector──▶ COMM.gpioPortC[7]  (PGOOD)   │
└───────────────┬──────────────────────────────────────────────────────┘
                │ External Control API (binary) + Monitor (batched text)
┌───────────────▼──────────────────────────────────────────────────────┐
│ PHYSICS PLANE                   (ホスト/Python)                       │
│   軌道・日照・可視性 / 姿勢 / 電力。仮想時間で積分                     │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 ホスト制御ループ（CRITICAL 課題の解決）

**唯一のコーディネータ**が Renode の生存と時間を排他所有する。他のどのプレーンも
Renode API を直接叩かない。

| 目的 | 経路 | 根拠 |
| --- | --- | --- |
| 時間の前進・取得、GPIO、`sysbus` 読み書き | **External Control API**（バイナリ、`emulation CreateExternalControlServer`） | Monitor 比 **44 倍高速**。プロトコルを byte-exact に逆解析し、**200 行の純 Python クライアント**を実証 |
| I2C センサ注入、`cpu IsHalted`、`LoadELF` 等 | **Monitor（TCP、`;` バッチ）** | External Control は run_for/time/machine/adc/gpio/sysbus のみ。両者は**同時利用で無デッドロック**を実測 |
| 宇宙リンクのバイト | **usart2 の socket terminal** | §5.2 |
| ブラウザ | **`--server-mode` WebSocket** | §4.4 |

**1 ティックの手順**（順序は固定）:

1. 非ブロッキングで全 UART ソケットを drain（専用スレッド。**Renode API を絶対に呼ばない**）
2. GPIO イベント（`timestamp_us` は**仮想時間**）を取り込む
3. Channel / Physics を t+Δ まで進める
4. センサ値を**1 バッチ**で注入（Monitor 1 行、固定小数点整形、返却フィールド数を検証）
5. `run_for(Δ)` を発行
6. Δ = **100 ms 仮想**（実測の最適点）

**遵守規則**（すべて実測に基づく）:

- **L1** 制御クライアントは 1 個だけ。External Control も Monitor も listener backlog=1
- **L2** `start`（自走）を使わない。自走中は `run_for` が失敗し、**GPIO イベントは失われる（バッファされない）**
- **L3** UART ソケットに対する**ブロッキング recv を制御スレッドで行わない**。実測で仮想時間が 220000 µs に 8 秒間凍結した（唯一のデッドロック。構造的に回避可能）
- **L4** Monitor バッチは**必ず**返却フィールド数を期待値と突き合わせる。中断は無言で、以後の欄がずれる（D6）
- **L5** 送信前に浮動小数を固定小数点へ整形（D5）
- **L6** ホットループのコマンド文字列は allowlist。任意のプロパティ取得は禁止（D4）
- **L7** 再現性が要る注入は**ティックの合間**に行う。ティック中の書き込みはステップ終了後に実行される（実測）
- **L8** 切断前に必ず `\n`（D13）
- **L9** CI は `< /dev/null` と実時間タイムアウト（D14）
- **L10** ハンドシェイク時、先行するイベントフレームを読み飛ばす（D12）
- **L11** GPIO 登録はクライアント切断後も**永久に残り発火し続ける**。長時間運用前に負荷試験

### 4.3 時間モデル

Renode の仮想時間が唯一のマスタークロック。パス窓・タイムアウト・周期 HK はすべて仮想時間で定義する。

**実証済みの決定性**: 2 つの Renode プロセスで、ステップ粒度 143 倍差・実時間 3.3 倍差でも
**GPIO タイムスタンプがビット一致し、UART が同一 SHA-256**。
さらに `RunFor "5"` ≡ 5×`RunFor "1"` がビット完全に一致する（量子が割り切れなくても）。
**したがってホストのティック刻みは自由**で、対話 100 ms / CI 一括のどちらでも同じ結果になる。

**時間圧縮の範囲が縮んだ**（§3.3 の測定による）。60 仮想秒のパスは 26 秒で終わるので、
**パス単位では圧縮しない**。圧縮するのは軌道スケールの現象（日照/日陰、電池の充放電、
軌道位相）だけで、CAN タイミング・PUS タイムアウト・ウォッチドッグ周期は決して圧縮しない。

#### 2 つの実行プロファイル

| プロファイル | 設定 | 速度 | 決定性 |
| --- | --- | --- | --- |
| `interactive` | 量子 2 ms + `SetGlobalAdvanceImmediately true` | **2.34×**（4 ノード実測） | ビット再現しない（`RunFor` 境界で末尾 1 行がぶれる） |
| `ci` | 量子 2 ms、AdvanceImmediately **オフ**、`SetGlobalSerialExecution true`、`SetSeed <固定>` | ちょうど 1.0× | **ビット決定的**（§9.2） |

CI が 1.0× に張り付くのは受容する。60 仮想秒のテストが 60 秒で済み、その代わりに厳密な再現性が買える。
CI で速度が要るなら AdvanceImmediately を使ってよいが、その場合アサーションは
**末尾 1 行のぶれに耐える**書き方（内容・接頭辞で判定し、行数で判定しない）にしなければならない。

### 4.4 プレゼンテーションプレーン（GUI）

**Renode のネイティブ GTK GUI は WSL2 で動かない。** 対照実験で確定した:
tkinter のウィンドウは `xwininfo -root -tree` に現れるが、Renode を GUI モードで起動しても
ウィンドウは 0 個で、エラーも出ない（WSLg・GTK3 は導入済み）。

代わりに **`renode --server-mode --server-mode-port <p>`** を使う。実測で得られた面:

| 種別 | 内容 |
| --- | --- |
| アクション | `status` / `command` / `exec-renode` / `exec-monitor` / `term-resize` / `spawn` / `kill` |
| `exec-renode` サブ | `machines` / `uarts` / `buttons` / `leds` / `button-set` / `sensors` / `sensor-get` / `sensor-set` |
| イベント（push） | `uart-opened` / **`led-state-changed`** / `button-state-changed` / `renode-quitted` |
| Web ターミナル | `http://<host>:<port>/telnet/<id>`（Monitor と各 UART） |
| ファイル | `fs/list` / `fs/mkdir` / `fs/stat` / `fs/dwnl` |

封筒: 要求 `{"action","payload","version","id"}`、応答 `{"version","status","id","data","error"}`、
イベント `{"version","event","data"}`。**`payload` は配列ではなく、C# ハンドラの引数名をキーにしたオブジェクト**
（`callArgs[arg] = apiRequest.Payload[param.Name]`）。実測で 15/16 呼び出しが成功。

**注意点**: ペリフェラルは**フルネーム**で指す（`sysbus.gpioPortC.UserButton1`）。
`sensor-set` は `temperature` では動くが `magnetic-flux-density` では **Renode ごと落ちる**（D3）。
UI は D3 の型を呼ばない。

**GUI の構成**: 単一のブラウザアプリ。X 不要・root 不要・Windows のブラウザから `localhost` で到達。

- 左: 衛星の現況（各ノードの起動/リセット/モード、仮想時刻、パス状態、リンク、CAN カウンタ、電池、姿勢、最後に受理した TC）
- 中: 運用コンソール（TC 送信、HK、イベントログ）と**バスタイムライン**（仮想時刻・送受信・復号済みフィールド・生バイト・注入マーカ・拒否理由）
- 右: Renode の Web ターミナル（Monitor と選択した UART）
- 演習ペイン: シナリオ選択・手順・3 段階ヒント・成否判定

### 4.5 観測・制御チャネル（4 本。役割を混ぜない）

**Monitor は `emulation RunFor` の実行中ずっとブロックする**（コマンドのエコーが走行終了まで返らないことを実測）。
したがって**学習者 UI が Monitor から読んではならない**。用途ごとに別チャネルを持つ。

| チャネル | 起動 | 用途 | 再接続 |
| --- | --- | --- | --- |
| **External Control** | `emulation CreateExternalControlServer` | 時間の前進・取得、GPIO、`sysbus` 読み書き | 単一クライアント |
| **Monitor** | `--port <p>` | 制御（センサ注入、`IsHalted`、`LoadELF`）。**バッチ必須** | **1 プロセスに 1 セッション、再接続不可** |
| **logNetwork** | `logNetwork <p>` | **ライブイベント配信**。UI と物理エンジンが読む | 可 |
| **Robot XML-RPC** | `--robot-server-port <p>` | **素の XML-RPC。57 キーワード**（`CreateLedTester` / `AssertLedState` / `AssertLedIsBlinking` / `AssertLedDutyCycle` 等）。**言語非依存・再接続可能** | 可 |
| GDB | `machine StartGdbServer <p> false` | 学習者のデバッグ。ノードごとに 1 ポート、相互干渉なし | 可 |

`--robot-server-port` の発見は「Monitor が 1 セッションしか持てない」制約を実質的に解消する。
また **`CANTester` が存在しない**（D15）ぶんを、LED アサーションが部分的に埋める。

**Python フックの出力先**: フック内の `print()` は Renode 自身の stdout に出て
`--port` の Monitor セッションには**届かない**。CubeRange が同梱するフックは必ず
`cpu.Log(LogLevel.Error, ...)` / `machine.Log(...)` を使い、`logFile` と `logNetwork` に載せる。

#### アクチュエータ観測（EPS のロードスイッチ、磁気トルカ）

- **push が正解**: GPIO ポートごとに `sysbus SetHookBeforePeripheralWrite sysbus.gpioPortX "<python>"` を
  仕掛け、`(offset, value, machine.ElapsedVirtualTime)` を物理エンジンへ送る。
  全書き込みを仮想時刻つきで正確に拾い、変化が無いときは無料。
- **ポート単語で持つ**: 「ピン N を読む」スカラー API は無い。`gpioPortX GetGPIOs`（16 ピン一括、約 11 ms）か
  `sysbus ReadDoubleWord <base+0x14>`（ODR）。base は gpioPortA 0x58020000 から +0x400 刻み。
- **BSRR だけをフックしてはならない**: Zephyr の STM32 GPIO ドライバは **ODR（0x14）**でトグルし、
  BSRR（0x18）は初期化時しか触らない。BSRR にウォッチポイントを置くと**全てのトグルを取りこぼす** —
  まさに旗艦演習が「無線が死んでいるから見えない」と誤認する失敗モードである。
- **read-to-clear レジスタをポーリングしてはならない**: Monitor のレジスタ読みは
  ペリフェラルモデルを通る（`SetHookAfterPeripheralRead` が Monitor の読みで発火することを実測）。
  UART の status、CAN の IR、タイマの SR を物理エンジンが読むと**ファームの状態を壊す**。
  ポーリングは GPIO ODR / `LED.State` / `GetGPIOs` に限る。
- **逐次の Monitor コマンドは原子的スナップショットではない**（`GreenLED=False` を読んだ 6 ms 後に
  ODR が既に 1 だった実例）。一貫した多レール断面が要るなら 1 フック内で取るか、ポート単語を 1 回で読む。

#### 学習者に出す道具（すべて実測で動作確認済み）

| 機構 | コマンド | 用途 |
| --- | --- | --- |
| **ウォッチポイントフック** | `sysbus AddWatchpointHook <addr> <width> <Read\|Write\|ReadAndWrite> "<python>"` | **最も価値の高い採点プリミティブ**。EX-F01 は戻り番地スロットへの Write、EX-F02 は鍵バッファへの **Read**（発火時の PC まで取れる）。*Read フックでは `value` が 0 になる*ので `cpu.PC.RawValue` と `cpu.Bus.ReadDoubleWord(addr)` を使う |
| PC/シンボルフック | `cpu AddHook <addr> "<py>"` / `cpu AddSymbolHook "<sym>" "<py>"` | 到達判定 |
| ペリフェラルアクセスログ | `sysbus LogPeripheralAccess sysbus.i2c1 true` | レジスタ**名**と**アクセス元 PC** まで出る。バス攻撃の学習者向けキャプチャ |
| 実行トレース | `cpu CreateExecutionTracing "<n>" @<file> Disassembly` | 「何が動いたのか」 |
| フレームグラフ | `cpu EnableProfilerCollapsedStack @<file> true` / `cpu LogFunctionNames true` | Zephyr の実関数名つき |
| GDB | `machine StartGdbServer <p> false` | **ReverseStep+/ReverseContinue+ を広告**。生の GDB remote を話すのでブラウザ側フロントエンドから直接扱え、`arm-none-eabi-gdb` は不要 |

### 4.6 攻撃者の生 CAN 注入（ATTACKER ノードは不要になった）

2 版は「特権なしの生 CAN 注入は不可能だから 5 台目の Zephyr マシンが必須」としたが、**これは誤り**。
特権不要の経路が 4 つ実証された（いずれも被害ノードの Rx FIFO 0 まで到達を確認）:

| 経路 | 実証 | 速度 |
| --- | --- | --- |
| Monitor レジスタ直書き | `fdcan1` の TXBC/TXESC/message-RAM/TXBAR へ 8 回の `sysbus WriteDoubleWord` | 約 7 frames/s。コード 0 行の緊急用 |
| `CAN.CANToUART` | `NullRegistrationPoint` に登録すればハブに接続できる（先の E21 は登録ミス） | 1 データバイト/フレーム、TX ID 固定 |
| Monitor の `python` | **IronPython 2.7.12 + 完全な CLR リフレクション**。30 行で `ICAN`+`IPeripheral` | 任意 ID/長 |
| **`include @file.cs`（Roslyn 同梱）** | **90 行の `TcpCanInjector : ICAN, IPeripheral`** を実行時コンパイル。`machine CreateTcpCanInjector "inj" <port>` | 200 発ロスなし、ホスト側 35k frames/s |

**採用**: 4 番目。プラットフォームも ELF も CPU も持たない**空のマシン**（`mach create "attacker"`）に
インジェクタを載せる。ハブが `GetName()`/`GetMachine()` を解決できるようにするためだけの器である。

**制約**（実測）: 注入時にエミュレーションが**動作中**でなければならない（停止中は `HandleTimeDomainEvent`
配送が無言で捨てられる）。D10・D11 も参照。

`vcan0`（SocketCAN）経路は Linux + root がある環境でのみ `--with-vcan` で有効化し、
`candump` / Wireshark / Scapy を**観測側で**使えるようにする。**必須にはしない。**

---

## 5. ノード構成

| ノード | CSP addr | 役割 | 仕込む弱点 | 攻撃が通ったときの物理的帰結 |
| --- | --- | --- | --- | --- |
| **COMM** | 5 | TT&C。TC/TM フレーム、SDLS、CAN⇄宇宙リンク中継 | SDLS 既定無効。有効時も IV 再利用・リプレイ窓なし | なし（入口） |
| **OBC** | 1 | C&DH。PUS、TC スケジュール、FDIR、HK 集約 | PUS Service 8 のパラメータ長を検証しない | 制御フロー乗っ取り（§7.3） |
| **EPS** | 2 | 電力。電池・太陽電池・ロードスイッチ | CAN 上のロードスイッチ指令に認証がない | **COMM の電源断 → 衛星が沈黙** |
| **ADCS** | 4 | 姿勢。IMU・磁力計・太陽センサ・磁気トルカ | トルク指令に範囲検査がない | スピンアップ → 発電不能 → 電池枯渇 |
| **attacker** | — | 空のマシン + `TcpCanInjector`（§4.6） | — | — |

### 5.1 UART 割り当て（新規・必須）

`nucleo_h753zi.dts` は `zephyr,console = &usart3` かつ `zephyr,shell-uart = &usart3` を設定する。
実測: リンクソケットに接続したクライアントは、アプリのバイトより前に
**9239 バイトの Zephyr コンソール出力**を受け取った。

| UART | 用途 | バックエンド |
| --- | --- | --- |
| `usart3` | **Zephyr コンソール専用**（デバッグ） | `CreateFileBackend` |
| `usart2` | **宇宙リンク**（COMM。バイナリ） | `CreateServerSocketTerminal <port> "spacelink" false` |
| `uart4` | 予備（将来のペイロード等） | — |

**実証**: 同じ未改変 ELF で console→usart3 ファイル、link→usart2 ソケットにしたところ、
リンクソケットは 3 仮想秒で**受信 0 バイト**、地上局が最初に見るバイトはアプリが送る
CCSDS ASM `1a cf fc 1d` だった。

**`CreateServerSocketTerminal` の第 3 引数は必須**: 実シグネチャは
`(Int32 port, String name, Boolean telnetMode = True, Boolean flushOnConnect = False)` であり、
**`telnetMode` は既定 true** で 11 バイトの telnet IAC（`ff fd 00 ff fb 01 ff fb 03 ff fc 22`）を前置する。
省略するとバイナリリンクが壊れる。`flushOnConnect` の意味は直感と逆で、
`false` だと遅れて接続したクライアントが**バックログを受け取る**。

SoC dtsi では usart3 と lpuart1 以外すべて `status="disabled"` なので、
`usart2` を使うにはアプリ側の devicetree overlay が要る。
COMM は `CONFIG_UART_CONSOLE` / `CONFIG_LOG_BACKEND_UART` / `CONFIG_SHELL` / `CONFIG_BOOT_BANNER`
がリンク UART に出力しないことを保証する。

**CI ガード**: `renode-test` の Robot スイート（3 テスト、**否定対照を含めて全 PASS 実証済み**）が
「最初の有効フレームまでリンクソケットに 0 バイト」を assert する。

### 5.2 EPS → COMM の電源線

`emulation CreateGPIOConnector "loadSwitchComm"` で EPS の `gpioPortB[0]` を
COMM の `gpioPortC[7]`（PGOOD）に接続する。実測で LED と IDR bit7 の両方が
マシン境界を越えて駆動されることを確認済み。COMM のファームはこの線を「power good」として尊重する。

### 5.3 ノードの電源断機構（実測で確定）

**`cpu IsHalted true` が唯一の正解。**

```
mach set "COMM"
cpu IsHalted true                       # 電源断
# → COMM の命令カウンタがビット完全に凍結、CAN も UART も沈黙
# → OBC/EPS/ADCS と仮想時間は正常に進行

mach set "COMM"                         # 復電（RunFor フロー）
machine RequestReset                    # machine Reset は D8 でデッドロック
emulation RunFor "0.001"
mach set "COMM"
sysbus LoadELF @fw/comm.elf             # 必須。省くと COMM は永久に死ぬ
cpu IsHalted false
```

3 ノードでの実証: OBC の UART が「両ピア生存＝カウンタ二重表示」→「COMM 停止中の 3 秒＝単独表示」
→「復電後＝相手が 0 から再スタートして交互表示」。COMM の UART は起動バナー → 3 秒の完全な沈黙 →
**2 回目の起動バナー**。グローバル仮想時間は 9.101 秒まで進み続けた。

**却下した機構**: `machine Pause`（D7）、`machine Reset`（D8）、`emulation RemoveMachine`（D9）、
`connector Disconnect`（バスだけ切れて実行は続く。再接続が非対称）、別時間ドメイン（**API が存在しない**）。

**注意**: halt 中も `fdcan1` はハブに繋がったままフレームを FIFO に受け続けるため、
裸の unhalt では古いフレームで異常が出る。上記の `RequestReset` + `LoadELF` 手順がこれを解消する。

---

## 6. プロトコルスタック

### 6.1 宇宙リンク

```
CubeRange lab framing:  ASM 0x1ACFFC1D + 固定長
┌─────────────────────────────────────────────────────┐
│ TC / TM Transfer Frame（最小形）                      │
│  ├ Frame Header (SCID, VCID, Frame Seq)              │
│  ├ [SDLS Security Header]        ← Phase 2           │
│  ├ Space Packet → PUS Packet                         │
│  ├ [SDLS Security Trailer (MAC)] ← Phase 2           │
│  └ Frame Error Control (FECF)                        │
└─────────────────────────────────────────────────────┘
```

**外側の区切りは「CubeRange lab framing」であり CCSDS の CLTU ではない**。
CLTU は 231.0-B-4 の規定で開始列は `EB90`。我々は Phase 1 でこれを実装しない。
これを CCSDS チャネル準拠と称してはならない。

| # | 文書番号 | 名称 | 版 | Phase 1 |
| --- | --- | --- | --- | --- |
| 1 | **CCSDS 133.0-B-2** | Space Packet Protocol | 2020-06 | 実装 |
| 2 | **CCSDS 132.0-B-3** | TM Space Data Link Protocol | 2021-10 | 実装（最小形） |
| 3 | **CCSDS 232.0-B-4** | TC Space Data Link Protocol | 2021-10 + Cor.1 | 実装（最小形） |
| 4 | **CCSDS 355.0-B-2** | Space Data Link Security | 2022-07 | Phase 2 |
| 5 | **CCSDS 355.1-B-1** | SDLS Extended Procedures | 2020-02 | Phase 2（範囲限定） |
| 6 | **CCSDS 232.1-B-2** | COP-1 | 2010-09 + Cor.1 | 非実装 |
| 7 | **CCSDS 231.0-B-4** | TC Sync & Channel Coding（**CLTU**） | 2021-07 + TC.1 | 非実装 |
| 8 | **CCSDS 732.0-B-5** | AOS | 2025-10（B-4 は廃止） | 非実装 |

### 6.2 FECF の CRC（一次資料で確定）

**「CRC-16-CCITT」というラベルは使わない。** その名で呼ばれる CRC-16 は 10 種類以上あり、
実装が両側で同じ間違いを選ぶと**ラウンドトリップは完全に成功したまま線上で誤る**。

CCSDS 132.0-B-3 §4.1.6.2.2 を実 PDF から引用して確定した:
`G(X) = X^16 + X^12 + X^5 + 1`、`L(X) = Σ(i=0..15) X^i`、
注記「`X^(n-16)·L(X)` の項はシフトレジスタを全 1 に初期化する効果を持つ」。

| パラメータ | 値 |
| --- | --- |
| 幅 / 多項式 | 16 / `0x1021` |
| 初期値 | `0xFFFF` |
| 入出力反転 | なし |
| XorOut | `0x0000` |
| 通称 | **CRC-16/IBM-3740**（CCITT-FALSE） |
| 被覆 | 一次ヘッダ先頭〜FECF 直前。**ASM は含まない** |
| 検算性 | FECF 込みで計算すると `0x0000` |

6 つの独立実装（crcmod / crc / fastcrc / 手書きビットループ / NASA CryptoLib の
`Crypto_Calc_FECF` を `.so` としてビルドし ctypes 経由 / FSFW の `CRC::crc16ccitt`）が一致。
Yamcs の公表期待値 `0x75FB` も再現。

**CSP の CRC は別物**: CRC-32C/Castagnoli（poly `0x1EDC6F41`、init `0xFFFFFFFF`、反転あり、
xorout `0xFFFFFFFF`）を**ビッグエンディアンで**付加。libcsp の `csp_crc32_memory()` を実行して確認。

### 6.3 PUS

**ECSS-E-ST-70-41C**（2016-04-15）。PUS は Space Packet に載るアプリ層の約束事であり、
RF 変調・フレーム符号化・COP-1・SDLS は規定しない。

| Service | 名称 | セキュリティ上の意味 |
| --- | --- | --- |
| 1 | Request verification | 攻撃の成否が攻撃者にも観測できる |
| 3 | Housekeeping | 偽装 TM の対象 |
| 5 | Event reporting | 検知演習の情報源 |
| 8 | Function management | **★ 境界検査欠如を仕込む場所** |
| 9 | Time management | 時刻ずらし → スケジュール攻撃の前段 |
| 11 | Time-based scheduling | **★ 遅発性の破壊コマンド** |
| 17 | Test | Phase 0 の疎通対象 |

副ヘッダ（2 つの Apache-2.0 実装が bit 単位で一致）:
**TC = 5 バイト** `[0] pus_version<<4|ack_flags, [1] service, [2] subtype, [3:5] source ID u16 BE`。
**TM = 7 バイト + 時刻** `[0] version<<4|sc_time_ref, [1] service, [2] subtype,
[3:5] msg type counter u16 BE, [5:7] dest ID u16 BE`。

**正直な留保**: ECSS-E-ST-70-41C 本文は登録が必要で入手できなかった。
PUS の根拠は一次資料ではなく、**独立した 2 実装の一致**である。

### 6.4 内部バス — libcsp を採用

**自前実装しない。** libcsp 2.1（MIT）を Zephyr モジュールとして組み込む。

CSP v1 ヘッダ（`csp_id.c:29-41` を読み、libcsp を実行して 9 ベクタで確認）:
`pri` bits31-30、**`SRC` bits29-25**、`DST` bits24-20、`dport` bits19-14、`sport` bits13-8、`flags` bits7-0。
（ソースの ASCII 図は SOURCE を先に書いており、コードもそれに一致する。）

CFP v1 の 29 bit CAN 識別子（`csp_if_can.h:77-119`、7 ケース・CAN 24 フレームで確認）:
`SRC` bits28-24、`DST` bits23-19、`TYPE` bit18、`REMAIN` bits17-10、`ID` bits9-0。
BEGIN フレームのペイロード = CSP ヘッダ 4B + 長さ u16 BE 2B + データ最大 2B。
MORE フレーム = 生データ最大 8B。BEGIN の `remain = (length+6-1)/8`。
libcsp v1.6 と master で CFP マクロが同一であることも確認済み。

CSP v2 は 48 bit ヘッダで非互換。Phase 1 は v1 を採る。

### 6.5 SDLS（Phase 2）

CCSDS 355.0-B-2 のサブセット: SPI、IV、AES-256-GCM、アンチリプレイ窓。

**2 版の誤りを撤回する。** 「ハードウェア crypto に置けば鍵が守られる」は**偽**である。
STM32H7 の CRYP は**アクセラレータであって金庫ではない**（鍵はファーム管理のメモリから来る）。
さらに Renode の `STM32H7_CRYPTO` は鍵レジスタ `CRYP_K0LR/K0RR`（0x48021020/24）を
**素の読み書き可能レジスタとして模擬**する（`0xDEADBEEF`/`0xCAFEBABE` を書いて両方そのまま読み戻せた）。
シミュレータ上でも保護として成立しない。

正直な対策は (M1) Service 6 のアドレス allowlist と (M2) 鍵の寿命最小化と明示的ゼロ化であり、
**すでにコード実行を得た攻撃者は止められない**ことを README に明記する。

---

## 7. 演習カタログ

各演習は 5 点セット: `README.md`（シナリオ・SPARTA/SPACE-SHIELD TTP・3 段階ヒント）、
`scenario.resc`、`solve.py`、`mitigation.md`、`verify.robot`（**攻撃成功と対策時失敗の両方**）。

### 7.1 脅威モデルの根拠

Willbold, Schloegel, Vögele, Gerhardt, Holz, Abbasi,
"Space Odyssey: An Experimental Software Security Analysis of Satellites",
IEEE S&P 2023（Distinguished Paper）。ESTCube-1 / ESA OPS-SAT / Flying Laptop の実ファームを解析し、
**TC 認証の欠如または回避可能性**、**暗号はあっても認可がない**、**危険な保守プリミティブ（直接メモリ操作）**、
**パケット申告長への信頼と安全でないパース**、**スタックカナリア等の欠如**を報告している。
本カタログの弱点はこの実測知見に対応させる。

### 7.2 Phase 1 の演習

| ID | 層 | 内容 | 成功条件 |
| --- | --- | --- | --- |
| **EX-L01** | 宇宙リンク | 可視パス中の TC をキャプチャし次パスでリプレイ | 認証なしでコマンドが再実行される |
| **EX-B01** | 内部バス | `TcpCanInjector` から偽 CSP フレームを注入し EPS に COMM の電源を切らせる | COMM が沈黙し TM が途絶える（§5.3） |
| **EX-F01** | ファーム | PUS Service 8 の境界検査欠如で**制御フロー乗っ取り** | §7.3 |
| **EX-G01** | 地上系 | 悪意ある**スケジュールインポート**で TC スケジュール DB を汚染 | 運用者が意図しない TC が送信される |

**EX-G01 の是正**: 2 版は「DB に書ける者が DB を書ける」という同語反復だった。
侵入機構を明示する（スキーマ検証・署名検証のないインポート経路、初期権限は低権限プラグイン）。
対策はスキーマ/署名検証・認可・最小権限・監査証跡。

### 7.3 EX-F01 の誠実性（全面改訂）

**シェルコード注入は不可能であり、そう教えてはならない。** 実 ELF から実測した Zephyr の既定値:

- ON: `ARM_MPU=y`, `HW_STACK_PROTECTION=y`, `MPU_STACK_GUARD=y`, `XIP=y`, `SRAM_REGION_PERMISSIONS=y`
- OFF: `USERSPACE`, `STACK_CANARIES`, `STACK_SENTINEL`, `STACK_POINTER_RANDOM=0`

**(a) 戻り番地は保護されない。** MPU スタックガードはスタックの**下**に置かれる境界領域
（`guard_start = stack_info.start - guard_size`、`K_MEM_PARTITION_P_RO_U_NA`）。
フレーム内 memcpy は**上**へ伸びて保存済み `{r4,r5,lr}` を壊し、ガードに触れない。
Zephyr 自身の文書も「ガードは検出できるがデータ破壊を防げない」と述べている。

**(b) SRAM は execute-never。** 実 ELF の `mpu_regions[]` を復号すると
SRAM @0x24000000/512K `RASR=0x110B0024` → **XN=1**、FLASH `RASR=0x07020028` → XN=0/RO。
**Renode はこれを強制する**: `TranslateAddress 0x24003000 InstructionFetch` は失敗し
Read/Write は成功、SRAM に PC を置くと命令が 0 個しか実行されない。フラッシュ上の同じ NOP 列は動く。
MPU ON でシェルコードを実行すると PC は MemManage ハンドラ、`CFSR=0x00000001`（IACCVIOL）。

→ **Renode は実機と一致する。したがってここで通る exploit は実機でも通る。**

**採用する形**: **ret2win（コード再利用）**。保存 LR / 関数ポインタを壊し、
既にリンクされている関数（セーフモード解除、FDIR インヒビット解除など）へ飛ばす。
フラッシュは RX かつ非ランダム（XIP、ASLR なし）、カナリアなし、CFI なし、権限分離なし —
すべて**実測された既定値**である。

- **EX-F01b** ROP チェーン（同じく誠実。ガジェット探索は Phase 2）
- **EX-F01c** データのみ攻撃（隣接する認可フラグ／コマンド allowlist の破壊。カナリア耐性）
- **EX-S03** 青チーム版: オーバーフローが MPU に捕まる構成で、MemManage フォールト
  （`CFSR=0x00000001` / `0x82`、`MMFAR`）を追跡し、不正な PUS TC と相関させ、
  障害報告テレメトリを生成する

**Kconfig 差分はゼロ。** 脆弱版と対策版は同じ `prj.conf`（既定値を明示的に再宣言して差分を可読にする）
を共有し、違いは自作シンボル `CONFIG_CUBERANGE_PUS8_LENGTH_CHECK` が守る
`if (arg_len > sizeof(args)) reject();` の 1 行だけ。
**CI ゲートが両 ELF の `CONFIG_*` ABS シンボルを diff し、差分が正確に 1 行であることを機械的に証明する。**
これにより「防御を切って作った脆弱性」という藁人形を構造的に排除する。

### 7.4 EX-F02（鍵抽出）

`static const` 鍵は XIP=y のもとで `.rodata`（フラッシュ、実測 `0x0800ba60`）に置かれ、
MPU 上は P_RO_U_RO/XN=0 — 任意のコードからも、境界のない PUS(6) ダンプからも読める。
ソフトウェア AES はさらに同じ SRAM 上に鍵スケジュールを展開する。
対策は §6.5 の M1/M2 と、その限界の明記。

### 7.5 作れない演習（実測により）

- **CAN エラーフレーム / bus-off の診断**: Renode の CANHub はバス ACK を模擬しない（D18）
- **野良ポインタによる BusFault の検知**: 未マップ領域でフォールトが起きない（D16）
- **「MPU 領域未定義だから特権アクセスが拒否される」**: PRIVDEFENA が無視される（D17）

### 7.6 TTP マッピング

SPARTA（Aerospace Corporation）と ESA SPACE-SHIELD はいずれも STIX/JSON を配布している。
`tools/ttp_map.py` が両者を取得し、各演習のフロントマターに書かれた TTP ID の**実在を検証**する
（存在しない ID は CI で落とす）。**ID は推測で書かない。** 両者は独立に採番されており、
フレームワーク名とバージョンと native ID を保存し、相互参照は明示的な crosswalk として持つ。

---

## 8. エラー処理と FDIR

| 層 | 異常 | 挙動 |
| --- | --- | --- |
| Channel | ビット誤り / 断 | 破棄。**生バイトは必ず保存**（鑑識性が最優先） |
| 宇宙リンク | FECF 不一致 / 長さ不正 | 破棄 + カウンタ + PUS 5 |
| 宇宙リンク | SDLS 認証失敗（Phase 2） | 破棄 + イベント + 連続失敗でパス遮断 |
| PUS | 未知の service/subtype | PUS 1 の否定応答 |
| 内部バス | CSP CRC-32C 不一致 | 破棄 + カウンタ |
| ノード | ハング | `watchdog` によるリセット |
| EPS | 電圧低下 | セーフモード（非必須負荷を落とす） |
| 地上局 | デコード不能 | 例外を握り潰さず生フレームを保存して継続 |
| ホストループ | キュー溢れ / 欄数不一致 / 時刻の後退 | **致命扱い**。警告にしない |

---

## 9. 検証戦略

### 9.1 層

| 層 | 手段 | 対象 |
| --- | --- | --- |
| **前提** | `tools/renode-probe/probe.sh` | 本書の Renode 主張すべて + 欠陥登録簿の否定アサーション |
| ユニット | `pytest` + `hypothesis` | コーデックのラウンドトリップ、境界値 |
| **適合** | ゴールデンベクタ（§9.3） | **独立オラクル**との bit 一致 |
| ノード単体 | `renode-test` | 起動と HK |
| 結合 | `renode-test` | 地上局 TC → COMM → CAN → OBC → TM の往復 |
| **演習** | `renode-test` | **攻撃成功 + 対策時失敗 + 対策時に正規動作が成功** |
| 性能 | `probe.sh` の MEAS | 4 ノード速度。**静穏ホストでのみ assert** |
| 供給網 | SBOM / ライセンススキャナ | 商用可否の継続確認 |

### 9.2 決定性の規則（実測。すべて必須）

| # | 規則 | 実測根拠 |
| --- | --- | --- |
| **G1** | 複数マシンのシナリオは**必ず** `emulation SetGlobalSerialExecution true` を出力する | 無しでは 14/14 回すべて内部状態が乖離（`ExecutedInstructions` に約 0.75% のばらつき）。ペリフェラルトレースも観測可能に乖離。有りでは 14/14 一致 |
| **G2** | `emulation SetSeed <固定値>` を必ず出す | 既定シードはプロセスごとにランダム（3 プロセスで 3 値）。G1 とは独立の要件で、互いに代替しない |
| **G3** | 量子（quantum）は**正しさのパラメータ**。シナリオに固定し、**ゴールデンの同一性にハッシュとして含める** | 4 つの量子 → 4 つの異なる（それぞれ再現する）状態。性能目的で量子を変えると全ゴールデンが無効化する |
| **G4** | ホストのティック刻みは自由 | `RunFor "5"` ≡ 5×`RunFor "1"` がビット完全一致（量子が割り切れない場合も） |
| **G5** | CI がハッシュしてよいのは**ゲスト生成のバックエンド捕捉のみ** | Renode の `logFile` は全行にホスト時刻が付く。スナップショット `.dat` は同一状態でも約 8000 バイト異なる（TimeSource の簿記） |
| **G6** | `Save`/`Load` を採用する。ただし **Load は UART バックエンドを復元しない** | Save→別プロセスで Load→続行が、通しの実行とビット一致。捕捉は再アタッチして前後を連結する必要がある。両コマンドは Monitor の `help` に出ないため自前で文書化しバージョンを固定する |
| **G7** | 「トレースが無い/短い」は**再試行可能なインフラ障害**として、「トレース不一致」（本物のテスト失敗）と区別する | 約 79 回に 1 回、出力も エラーも無く終了する事象を観測（再現せず） |
| **G8** | すべての Renode 起動を**実時間ウォッチドッグ + RSS 上限**で包み、超過したら kill して再試行する | **107 回中 14 回（13.1%）がハングし、RSS が 100 秒で 3.7 GB、10 分で 17.3 GB まで漏れた**（VmSize 275 GB）。既定設定でも調整後でも同率に発生し、量子とは無関係。再試行では必ず回復した。**16 GB の CI マシンや学習者のノート PC を OOM させる規模である。ハングした 17 GB のプロセスの上に黙って座り続ける UI を出荷してはならない** |
| **G9** | 量子を変えたら**60 仮想秒以上**の走行を 100 µs 基準トレースと突き合わせる | 5 ms の誤差は 5 秒・10 秒では現れず 20 秒で初めて破綻した（§3.3） |

「バイト単位で再現する」と言えるのは、G1〜G3 のもとで**同一 Renode ビルド・同一プラットフォーム**の
**UART/バックエンド捕捉**についてのみ。スナップショットとログには言えない。
異なる Renode バージョン・異なるホスト CPU・ホスト側 I/O 併用については**未実証**。

### 9.3 適合オラクル（自己検証の禁止）

自作コーデック同士のラウンドトリップは**証明にならない**。両側が同じ誤りを選べば完全に一致する。

| 層 | 独立オラクル | ライセンス | 入手性 |
| --- | --- | --- | --- |
| Space Packet | spacepackets / ccsdspy / gr-satellites / FSFW（4 実装が一致） | Apache-2.0 / BSD-3 / GPL-3 / Apache-2.0 | pip 可 |
| TM/TC フレーム | **Yamcs のテストベクタ**（1115 バイトの実 TM フレーム） | AGPL（外部プロセス／データとしてのみ） | GitHub |
| FECF CRC | 6 実装 + **CCSDS 132.0-B-3 の一次資料** | — | 確定（§6.2） |
| PUS-C | spacepackets + FSFW（bit 一致） | Apache-2.0 | pip / egit |
| CSP v1 / CFP | **libcsp 本体をビルドして実行** | MIT | 実証済み |
| **SDLS** | **正直な空白** | — | ベンダリング可能な bit-exact ゴールデンは**存在しない**。CryptoLib を**外部ランタイムオラクル**として使い（ソースは複製しない）、Yamcs の `SecurityAssociationAes256Gcm128` を第 2 オラクルにする |

**捕まえた実例**: TM フレームの First Header Pointer を独自復号すると 0、Yamcs の期待値は 6。
これは不一致ではなく、Yamcs の `getFirstHeaderPointer()` が `fhp += dataOffset` して
**絶対オフセット**を返すため。まさに本節が防ごうとしている「規約の食い違い」であり、
外部オラクルを使ったからこそ表面化した。

### 9.4 CI

GitHub Actions。**ネットワーク有りの成果物準備ジョブ**と**egress 禁止のテストジョブ**を分離する
（現状の `probe.sh` は ELF を HTTPS で取得するため、egress 禁止の主張と矛盾していた）。
Renode の URL 規約は `-s_<size>-<sha1>` を含むため URL 自体が完全性ピンになる。

---

## 10. リポジトリ構成

```
space-cs-sim/
├── platforms/cubesat/       # .repl : ノード別（UART 割当・GPIO 極性のコメント必須）
├── scripts/                 # .resc : SetGlobalSerialExecution / SetSeed / logLevel を必ず出力
├── attacker/TcpCanInjector.cs   # 実行時コンパイルされる ICAN + TCP インジェクタ (~90 行)
├── firmware/                # Zephyr west workspace（libcsp をモジュールとして取り込む）
│   ├── common/{ccsds,pus,sdls,fdir}/
│   └── apps/{obc,comm,eps,adcs}/
├── src/cuberange/           # Python (Apache-2.0)
│   ├── proto/               #   spacepackets を土台に、frame/SDLS を自作
│   ├── renode/              #   external control クライアント / Monitor バッチクライアント
│   ├── channel/ physics/ gs/ attack/
│   └── web/                 #   GUI backend (WebSocket) + 静的フロントエンド
├── exercises/EX-*/          # 5 点セット
├── tests/{robot,pytest,golden}/
├── tools/{renode-probe,csv2resd-wrappers,ttp_map.py}/
├── docker/
└── docs/{architecture,protocols,threat-model,hardware-path}.md
```

---

## 11. ライセンス方針

自作コードは **Apache-2.0**。
依存 OK: Renode(MIT)、Zephyr(Apache-2.0)、**libcsp(MIT)**、spacepackets(Apache-2.0)、sgp4(MIT)。
**取り込まない**: NASA CryptoLib / NOS3（NOSA 1.3）、Yamcs（AGPL-3.0）、OpenC3（source-available）、
gr-satellites（GPL-3.0）。これらは**外部プロセスまたはテストデータとしてのみ**使う。
CI で SBOM を生成しライセンス逸脱を検出する。

---

## 12. 倫理・公開に伴う責務

シミュレータは完全に合成環境であり、実在の衛星・地上局・周波数・鍵を含まない。
演習は自己完結し、外部ネットワークへ送信しない。各演習には必ず対策編を対にする。

公開前に必要な文書（現状すべて欠落）:

| 文書 | 内容 |
| --- | --- |
| `SECURITY.md` | 対象バージョン、非公開連絡先、トリアージ目標、**意図的な演習用欠陥と本物の脆弱性の区別** |
| `SAFE_USE.md` | 合成標的のみ。実在の識別子・周波数・鍵・エンドポイントを置かない |
| `CONTRIBUTING.md` | 新しい exploit には合成標的・対策・成否テスト・出自・egress ゼロ試験・2 名レビューを要求。不透明なバイナリと実行時ダウンロードを拒否 |
| `ASSURANCE.md` | **本製品が支持しない主張**の明示（飛行認定・実時間検証・RF 適合・暗号認証・MCU 完全再現） |
| `docs/legal/export-control.md` | 責任者と再レビュー契機。**ITAR/EAR の分類は UNVERIFIED**。宇宙をテーマにするだけで ITAR 対象にはならない（22 CFR 120.33/120.34 の公知例外）が、SDLS/AES は別途暗号分類の検討が要る。**専門家の判断が記録されるまで「EAR99」「ITAR 非該当」と書かない** |
| `intentional-vulnerabilities.yml` | ID・ファイル/関数・対象プロファイル・exploit・対策・責任者。CI が対策版に危険シンボルが無いことを検証 |
| `firmware-matrix.yml` | 役割 × {脆弱版, 対策版} の成果物を内容アドレスで固定（ソース commit、west manifest、SDK digest、`.config` digest、ELF SHA-256） |
| `GOVERNANCE.md` / `CODEOWNERS` | exploit・暗号・ワークフロー・リリースの変更には 2 名承認 |

---

## 13. フェーズ計画（実測見積りに基づき改訂）

2 版の P0+P1 は **270〜470 人日**と見積もられた。以下は削り込んだ現実的な計画である。

| Phase | 内容 | 完了条件 | 見積 |
| --- | --- | --- | --- |
| **R0 リスク退治** | **完了**。Renode 1.16.1 / Zephyr v4.1.0 / SDK v1.0.1 / libcsp v2.1 を固定。自前ビルドの 2 ノードが CSP over CAN で ping 往復（1〜2 ms）。ホストからの生 CAN 注入と External Control による仮想時間制御・メモリ読みも実証 | `make toolchain` → `make firmware` → `make demo` が PASS。`make probe` 34 PASS / 0 FAIL | 実績 1 日 |
| **P0 最初のデモ** | **完了**。`make demo-p0` が実 PUS 17,1 → COMM → libcsp/CAN → OBC → 17,2 → 地上局 の往復を assert。`make soak-p0` が **30/30 連続成功、インフラ再試行 0、失敗 0**（3分24秒）。特権も GUI も不要 | `make check` 緑 + ソーク 30/30 | 実績 1 日 |
| **P1 セキュリティ縦切り 1 本** | **EX-B01 完了**。攻撃者が偽造 CSP フレームで EPS に COMM の電源を切らせ、衛星が沈黙することを実証。対策版は拒否し、かつ**正規の認証済みコマンドは通る**。両 EPS ビルドの Zephyr `.config` は diff で同一 | `make verify EX=EX-B01-eps-killswitch` が 3/3 | 実績 1 日 |
| **P2 2 本目 + GUI** | EX-L01（固定パス窓でのリプレイと対策）。ブラウザ GUI（§4.4） | 同上 | +18〜28 日 |
| **P3 リリース硬化** | 失敗時の成果物、決定的シード、ウォッチドッグ、キャッシュ、文書、SBOM、性能予算 | §12 の文書一式 | +15〜25 日 |

**合計 84〜129 人日（単独開発で 6〜9 か月）。**

**v1 から外すもの**: ADCS ノードと本格 physics、全画面 TUI、PUS 8/9/11（演習が要求するまで）、
EX-F01/EX-G01（P3 以降）、自前 CSP、自前 Space Packet コーデック、5 ノード常時稼働。

**双方向 CI の約束は P1 から守る。** 2 版は対策編を P2 に先送りしており、
これは §1.2 の成功条件との自己矛盾だった。

---

## 14. リスク登録簿

| # | リスク | 状態 |
| --- | --- | --- |
| R1 | Zephyr + FDCAN が Renode で動くか | **解消**。4/4 ノードで実測 |
| R2 | ホスト制御チャネルの実用性 | **解消**。External Control API（44 倍速）+ Monitor バッチ（17.4 Hz） |
| R3 | SocketCAN 経路 | **任意機能に降格**（root + Linux 必須） |
| R4 | 4 ノードの実行速度 | **解消**。既定値の産物だった。量子 2 ms + AdvanceImmediately で **2.34×**（§3.3） |
| R5 | `CAN.MCAN` モデルの未実装ビット | **残存**。`fdcan1: Unhandled write to offset 0x1C (NTSEG1/NTSEG2)` を観測。ビットタイミングは無視されるが通信は成立。CAN-FD や高度な機能は未検証 |
| R6 | Zephyr ツールチェーンの導入コスト | **解消**。`tools/setup-toolchain.sh` に固定。**Zephyr v4.1.0**（main は Python 3.12 要求。このホストは 3.10 なので cmake 段階で失敗する。`cmake/modules/python.cmake` の実測: main→3.12 / v4.1.0→3.10 / v3.7.0→3.8）、**SDK v1.0.1 の分割アセット 164 MB**（`west sdk install` は Zephyr の cmake を経由するため同じ Python 制約に当たる。公式 Docker イメージ約 10 GB は不要）。ディスク: workspace 6.8 GB + SDK 829 MB。**自前ビルドが Renode で起動することを実測**（`Hello World! nucleo_h753zi/stm32h753xx`） |
| R7 | 脆弱版が保護機構で exploit 不能になる | **解消**。ret2win は既定の保護下で成立（§7.3） |
| R8 | アクチュエータ観測の実用性 | **解消**。GPIO は External Control のコールバック（仮想時刻付き）で取得 |
| R9 | 5 ノード性能 | **解消**。5 台目は壁時計 +12〜26%、RSS +107 MB。6 ノードでも約 2.5× |
| R22 | **Renode が約 13% の確率でハングし RSS を 17 GB まで漏らす** | **回避策を実装。発生条件が変わった。** `src/cuberange/renode/supervisor.py` が実時間ウォッチドッグと RSS 上限でプロセスグループごと終了させ、ハングを「再試行可能なインフラ障害」として本物のテスト失敗と区別する。ウォッチドッグ自体を故意に破る試験で発火を実証済み。**その上で、静穏ホストでの P0 往復 30 連続実行ではハングが 1 度も再現しなかった**（peak RSS は全回 約361 MB で安定）。13% なら 30 回で約 4 回出るはずで、0 回は約 1.5% の確率。**元の 13% は並列実行による競合下の測定であった可能性が高い**が、根本原因は未特定のままなので回避策は外さない |
| R23 | 量子 > 2 ms が無言でタイミングを歪め、短いテストでは検出できない | **規則で回避**（G9）。量子はゴールデンの同一性に含める（G3） |
| R24 | 直列実行と AdvanceImmediately のどちらを CI に使うかで、決定性の強さと速度が相反する | **プロファイル分離で対処**（§4.3）。ただし「UART は並列でも一致するが内部状態は一致しない」という 2 つのプローブの観測差は未統合。CI が UART だけをハッシュする限り実害は無いが、内部状態に依存する assert を書く前に決着させる |
| R10 | SDLS の bit-exact ゴールデンが存在しない | **残存**（§9.3）。CryptoLib/Yamcs を外部オラクルにする |
| R11 | ECSS-E-ST-70-41C 本文が入手できない | **残存**。PUS は 2 実装の一致に依拠 |
| R12 | Renode GUI が WSL2 で動かない | **解消**（回避）。`--server-mode` + ブラウザ |
| R13 | External Control の運用上の危険（単一クライアント、孤児セッション、GPIO 登録リーク、ハンドシェイク破壊） | **規則で回避**（L1/L4/L10/L11）。長時間運用前に負荷試験 |
| R14 | 「ティック中の Monitor 書き込みは非決定的な仮想時刻に着弾する」は推論 | **予防的に受容**（L7）。決着策: 特徴的な値をティック中に 2 回注入し、ファームが読んだ仮想時刻を突き合わせる |
| R15 | 自走中の GPIO イベント喪失の機構が不明 | **受容**。挙動のみ記述。L2 により実害なし |
| R16 | External Control の ADC 経路が本ボードで使えない（`STM32F0_ADC` は `IADC` 未実装） | **受容**。ADC 注入は Monitor 限定（実測 PASS） |
| R17 | halt 中も `fdcan1` が FIFO に受信し続ける | **受容**。規定の復電手順で解消（§5.3） |
| R18 | GPIO 極性がピン依存 | **受容**。`invertedAFPins` を持たないピンを選ぶか実測する。選択を `.repl` にコメントで残す |
| R19 | 本書の性能値の一部が競合下の測定 | **要再測**。静穏ホストでの通し実行を R0 で行う |
| R20 | 証拠基盤の衛生（プローブ同士が互いの Renode を kill した） | **要再実行**。`probe.sh` を**単独・静穏ホストで 1 回通す**。`pkill -f renode` は禁止し、記録した PID のみ終了する |
| R21 | Monitor の `RunFor` と External Control の `run_for` の相互作用が未測定 | **未評価**。L2/L7 により推奨事項には影響しない |

---

## 15. 未解決事項

| # | 事項 | 判断時期 |
| --- | --- | --- |
| Q1 | プロダクト名 `CubeRange` の商標・衝突確認 | 公開前 |
| Q2 | 対応プラットフォーム行列（**WSL2 を Tier 1** とする案）と `probe.sh` の Python 化 | R0 |
| Q3 | Physics を別プロセスにするか | P1 着手時 |
| Q4 | 演習と成果物の互換性ポリシー（`release-manifest.json`） | P0 |

---

## 16. 法廷記録

### 16.1 1 版で是正した誤り（Renode を実行して判明）

1. 「検証済み」と称しながら Renode が未導入だった → 導入し全項目を実行、`probe.sh` 化
2. リリース版に RAMN ボードモデルは存在しない（master 限定）→ 依存しない設計へ
3. STM32L552 は二重に破綻（1.16.1 に FDCAN 無し、Zephyr が CAN 非対応）→ STM32H753 へ
4. センサ 3 種が登録不能 → 実測 PASS したモデルへ置換
5. 「`live` は実時間追従」→ 0.19〜0.30× なので不成立。仮想時間をマスタークロックへ
6. SocketCAN / Wireshark を中核に据えていた → 任意機能へ降格

### 16.2 2 版で是正した誤り（Codex レビュー + 13 プローブ）

| # | 2 版の主張 | 反証 | 是正 |
| --- | --- | --- | --- |
| W1 | EX-B01 の電源断機構が未定義 | `machine Pause` は時間ドメイン全体を凍結 | `cpu IsHalted` + `RequestReset` + `LoadELF` を実証（§5.3） |
| W2 | ホストが `ElapsedVirtualTime` を読めば同期できる | プロトコルも所有権も未定義。Monitor 共有は破綻 | External Control API + 単一コーディネータ + L1〜L11（§4.2） |
| W3 | ATTACKER ノード（Zephyr アプリ）が必須 | **特権不要の注入経路が 4 つ実在** | 空のマシン + C# インジェクタ 90 行（§4.6） |
| W4 | Robot の `Create CAN Tester` を CI に使う | **`CANTester`/`CANKeywords` は 1.16.1 に存在しない** | 自作インジェクタと UART 観測で assert |
| W5 | Physics はテレメトリでアクチュエータを観測 | COMM を殺す演習で観測経路も死ぬ | External Control の GPIO コールバック直読 |
| W6 | EX-F01 は「任意コード実行」 | SRAM は XN、Renode も強制する | **ret2win** へ。Kconfig 差分ゼロ（§7.3） |
| W7 | ハードウェア crypto が鍵を守る | CRYP はアクセラレータ。Renode は鍵レジスタを素の RW として模擬 | 撤回。M1/M2 と限界の明記（§6.5） |
| W8 | `usart3` がコンソール兼宇宙リンク | 起動時に 9239 バイトのコンソール出力 | usart2 へ分離。`telnetMode=false` が必須（§5.1） |
| W9 | 自前 CSP 実装 | libcsp 2.1 は正式な Zephyr モジュール | libcsp 採用 |
| W10 | 「CRC-16-CCITT」 | その名の CRC-16 は 10 種類以上 | 一次資料でパラメータを確定（§6.2） |
| W11 | 自作コーデック同士の検証 | 同じ誤りなら完全に一致してしまう | 独立オラクルとゴールデンベクタ（§9.3） |
| W12 | 決定性は replay モードで得られる | 直列実行なしでは 14/14 回乖離 | G1〜G7（§9.2） |
| W13 | Monitor で 1 コマンド 1 往復 | 物理 2.3 Hz が上限 | `;` バッチで 17.4 Hz（§4.2） |
| W14 | EX-G01 | 同語反復だった | 侵入機構を明示（§7.2） |
| W15 | P1 は攻撃成功のみ assert | §1.2 との自己矛盾 | 双方向 CI を P1 から |
| W16 | CI は egress 禁止 | `probe.sh` が ELF を HTTPS 取得 | 成果物準備ジョブを分離（§9.4） |
| W17 | 「ビジネス水準」を成熟度として主張 | 84〜129 人日の v1 に不相応 | 品質バーとして定義し直し、`ASSURANCE.md` で境界を明示 |
| W18 | R8 を参照しつつ登録簿は R7 まで | 宙吊り参照 | R1〜R21 に再構成 |
| W19 | `probe.sh` が確実に失敗しない | 出力ディレクトリ欠損で全項目 PASS | 自己テスト内蔵。34 PASS / 0 FAIL を実測 |
| **W20** | **「4 ノードは実時間の 0.19〜0.30 倍」を土台に設計全体を組んでいた** | **既定の量子と実時間スロットルの産物だった。2 行の設定で 2.34×**（自分で再測して確認） | §3.3 を全面差し替え。パス単位の時間圧縮を撤回し、対話プロファイルと CI プロファイルに分離（§4.3） |
| W21 | 「ログ抑制しないと約 12 倍遅い」 | 未実装レジスタ警告は起動時のみで 0〜9%。12 倍の原因は `LoggingUartAnalyzer` だった | §3.3 で訂正 |
| W22 | ホストのコア数が効くと暗に想定 | Renode の 1 スレッドが上限。14 コア中 2.56 しか使わない | シングルスレッド性能を買う（§3.3） |

### 16.3 3 版で是正した誤り（ラズパイ移植の調査で判明）

aarch64 への移植可否を調べる過程で、移植とは無関係な瑕疵が出てきた。移植の話ではないので
分けて記録する。

| # | 3 版までの主張 | 反証 | 是正 |
| --- | --- | --- | --- |
| W23 | 演習 README の TTP フロントマターが「`tools/ttp_map.py` が公式 STIX で ID の実在を CI 検証する」 | **`tools/ttp_map.py` は存在しない。CI も存在しない** | 検証機構が無いことを書く形に訂正。§0 の規則そのものの違反だった |
| W24 | `probe.sh --offline` が存在する | `MODE=offline` を代入するだけで、値がどこからも読まれていない。**オフライン指定でも普通にフェッチして成功と報告していた** | キャッシュの有無を確認し、空なら明示的に失敗するよう実装。「ネットワークを遮断はしない」ことも明記 |
| W25 | 設計 §9.2 G8 の supervisor 経由起動は全経路で必須 | **`test_p0_roundtrip.py` だけが生の `Popen`**。ウォッチドッグも RSS 上限も無い。誰もが最初に実行する経路にだけ無かった | supervisor 経由に変更 |
| W26 | 適合性テストが独立オラクルを保証する | `spacepackets` / `crcmod` は `pytest.importorskip` 越し。**未導入なら適合層が丸ごと黙って消え、それでも CHECK PASSED が出る** | `make check` の冒頭で import を要求。`requirements.txt` を追加 |
| W27 | ノードの起動待ちは固定 sleep（3〜4 秒）で足りる | `ping` も電源コマンドも**1 回送信・再送なし**。準備前のフレームは黙って捨てられ、以後変わらない状態をポーリングし続ける。`alive()` は COMM→OBC しか見ておらず EX-B01/L01 の宛先である EPS を見ていない。**実際に EX-L01 が間欠的に落ちた**（単体では通り、通しでのみ再現） | 各ノード自身の ready 行を待つよう変更。固定 sleep は遅いホストほど悪化する |
| W28 | FDIR 復帰待ちの 45 秒は「エミュレーションが 2.3x で走る」ので十分 | 予算は**実時間**、FDIR は**仮想 30 秒**。速度に反比例する。x86-64 で約 13 秒、Pi 5 で約 40 秒、**Pi 4 では約 100 秒で落ちる** | `CUBERANGE_FDIR_WAIT_S` で上書き可能にし、算術をコメントに残した |
| W29 | ディスク実測「zephyrproject 6.8 GB / zephyr-sdk 829 MB」 | 再測定で 5.8 GB / **2.0 GB**。SDK は 2.4 倍の誤り | `setup-toolchain.sh` の記述を訂正。併せて `west init -m` がマニフェストリポジトリを**フルクローン**していたことも判明し（`--narrow --depth=1` が浅くするのは projects だけ）、shallow clone + `west init -l` に変更して 856 MB 削減（実測） |
| W30 | ペアゲートが「この版のファームウェアが 1 フラグだけ違う」ことを証明している | **ゲートは成果物の出所を一度も見ていなかった**。`$OUT` の既定が全チェックアウトで同じ `/tmp/cuberange` だったため、同一マシン上の別のチェックアウトが同じ `build-obc/` に `west build -p always` していた。ゲートは一方の脆弱版ともう一方の緩和版を比較し、**この版のどのファイルにも存在しないフラグ名**（`CUBERANGE_APID` ほか）を差分として報告した。ファームウェアの欠陥に見えるが、実際は他人の成果物だった | `CMAKE_HOME_DIRECTORY` を読む出所検査を**検査 0**（最初）に追加。`$OUT` の既定をチェックアウトごとに一意化（`src/cuberange/paths.py`。Makefile 側の同じ導出と `test_paths.py` が突き合わせる）。自己テストは「拒否されたか」ではなく「**その検査の理由で**拒否されたか」を要求する形に強化した |
| W31 | 各演習は、この版の脆弱版／緩和版ファームウェアを走らせている | **演習が指定していたのは「差し替える 1 つのイメージ」だけ**だった。残りのノードは `.resc` の既定値、すなわち全チェックアウト共有の `/tmp/cuberange` から読まれていた。EX-F01 は自分の OBC をこのチェックアウトから、その周りの COMM と EPS を他人のビルドから読んでいたことになる。誰も報告しない。W30 で `$OUT` を分離した結果、demo-p0 が「ノードが ready を報告しない」で落ちて初めて表面化した | 全 `.resc` がファームウェアと捕捉ファイルのパスを `$out` から導出する形に変更。`$out` に既定値は置かない——未設定なら `No such variable: $out` で止まる。他人のビルドで静かに起動するより、止まる方がよい。Renode 1.16.1 が `$comm?=$out/build-comm/zephyr/zephyr.elf` を解決することは実測で確認した。`test_paths.py` がシナリオの直書きと起動側の渡し忘れの両方で落ちる |

W23・W24・W26 はいずれも同じ形をしている——**存在しない検証機構を、存在するかのように書いていた**。
§0 の規則が禁じているのはまさにこれで、規則を書いた文書自身が 3 回違反していた。

W30 は形が違う。検証機構は存在し、正しい性質を検査していた——**入力が誰のものかを確かめないまま**に。ゲートが読む対象を疑う検査が無ければ、そのゲートが出す PASS も FAIL も、どの版について述べたものか決まらない。今回は FAIL 側に出たので気づけたが、取り違えた 2 つの版がたまたま整合していれば PASS が出ていた。

W31 はその続きで、より重い。ゲートの入力だけでなく**演習そのものの入力**が確かめられていなかった。
「1 フラグだけ違う 2 つのイメージ」を証明しても、演習がそのうち 1 つと他人のビルドを混ぜて走らせるなら、
証明した対象と走らせた対象が別物である。この 2 件は、検証の対象を疑う検査が無いときに何が起きるかの
1 組の例になっている。

---

## 17. 参考

- Renode — https://github.com/renode/renode (MIT)
- Renode External Control API — `tools/external_control_client/README.md`（同梱）
- Renode WebSocket API — `src/Renode/WebSockets/`（`--server-mode`）
- RESD / csv2resd — `tools/csv2resd/`（同梱）
- RAMN — https://github.com/ToyotaInfoTech/RAMN
- NASA NOS3 / CryptoLib — https://github.com/nasa/nos3 , https://github.com/nasa/CryptoLib (NOSA 1.3)
- libcsp — https://github.com/libcsp/libcsp (MIT, v2.1 に Zephyr モジュール)
- spacepackets — https://github.com/us-irs/spacepackets-py (Apache-2.0)
- Yamcs — https://github.com/yamcs/yamcs (AGPL-3.0, 外部オラクルとしてのみ)
- Hack-A-Sat — https://github.com/solar-wine/tools-for-hack-a-sat-2020 , https://github.com/deptofdefense/hack-a-sat-library
- SPARTA — https://sparta.aerospace.org/ / SPACE-SHIELD — https://spaceshield.esa.int/
- Willbold et al., "Space Odyssey", IEEE S&P 2023 — https://mschloegel.me/paper/willbold2023spaceodyssey.pdf
- CCSDS Blue Books — https://ccsds.org/
