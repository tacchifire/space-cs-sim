# CubeRange — 設計仕様書

**リポジトリ**: `space-cs-sim`
**プロダクト名**: CubeRange（暫定 / 公表前に商標確認）
**Python パッケージ名**: `cuberange`
**日付**: 2026-08-28
**状態**: ドラフト（design-court レビュー前）

---

## 1. 目的とスコープ

### 1.1 一文で

**Renode 上で衛星のファームウェアを命令レベルで動かし、地上局・無線リンク・衛星内部バス・OBC ファームの
4 層すべてに実際に攻撃を通せる、宇宙システム版の RAMN。**

### 1.2 背景

Toyota RAMN は「4 個の MCU と CAN バスを 1 枚の基板に載せる」ことで、自動車セキュリティを
*実際に手を動かして* 学べるものにした。本プロジェクトはその宇宙版を作る。ただし物理基板を先に作るのではなく、
Renode によるマルチマシン・エミュレーションで同等の体験を先に成立させる。

### 1.3 利用者と成功条件

一次利用者はプロジェクトオーナー自身（攻撃検証の PoC 基盤）。ただし**同時にハンズオン学習基盤としても
成立している**ことを要件とする。加えて品質水準は「ビジネスでも使えるレベル」— すなわち:

| 成功条件 | 測り方 |
| --- | --- |
| 攻撃が本当に通る | 各演習に「攻撃成功」を assert する CI テストがある |
| 防御が本当に効く | 同じ演習に「対策適用後は攻撃が失敗する」を assert する CI テストがある |
| 誰の手元でも同じに動く | Docker + 固定バージョンで `make demo` が一発で通る |
| 現実と地続き | 実標準（CCSDS / ECSS PUS / CSP）のサブセットに準拠し、実物の仕様書と突き合わせられる |
| 実機化の道が塞がっていない | ファームは実在 MCU + 実 HAL 向け。Renode 固有の抜け道を使わない |
| 商用利用の障害がない | 自作コードは Apache-2.0。コピーレフト / NOSA コードを取り込まない |

### 1.4 非目標（YAGNI）

- 軌道力学の高精度シミュレーション（摂動、詳細熱モデル）— 攻撃の「結果」が見える最小限で足りる
- 実 SDR / RF ハードウェア（Phase 3 以降）
- CTF スコアサーバ、ユーザ管理、マルチテナント
- 物理基板の設計・製造（道は塞がないが、作らない）
- CCSDS フル準拠（COP-1、CLTU/BCH、AOS の完全実装）

---

## 2. ポジショニング — なぜ既存物の再発明でないか

事前調査で判明した既存物と、その空白:

| 既存 | 何であるか | 忠実度 | ライセンス | 空白 |
| --- | --- | --- | --- | --- |
| **NASA NOS3** (617★, 活発) | cFS + NOS Engine + 42 + COSMOS/Yamcs を束ねた運用シミュレータ | **システム／運用レベル**。cFS は Linux x86 でネイティブ実行。MCU の命令エミュレーションはしない | **NOSA 1.3** | ファーム内部のメモリ破壊・鍵抽出・フォールト注入ができない。NOSA は商用取り込みの障壁 |
| **Hack-A-Sat** (`solar-wine/tools-for-hack-a-sat-2020`, 97★) | 決勝用に衛星ファームを QEMU でエミュレートした一式 | ファームレベル ✓ | 混在 | 2020 年で更新停止。単発の CTF 用。バスも地上系も統合されていない |
| **RAMN** (275★, 活発) | 4 ECU + CAN の教材基板。Renode に公式ボードモデルあり | 命令レベル ✓ | — | **完全に自動車ドメイン** |
| **Renode** (2786★, MIT) | エミュレータ本体 | — | **MIT** ✓ | 宇宙向けのプラットフォーム記述・シナリオが存在しない |
| **Yamcs / OpenC3 COSMOS** | 実運用実績のある地上管制ソフト | 運用レベル | **AGPL-3.0** | 攻撃側の道具ではない。AGPL は製品組み込みの障壁 |

**空白＝我々の位置**: *命令レベルで動く衛星ファーム*と*内部バス*と*地上系*を一本に繋ぎ、
セキュリティ演習を CI で検証済みの形で同梱したもの。NOS3 が「運用レベル」なら、CubeRange は
「シリコン／ファームレベル」であり、両者は競合ではなく補完関係にある。

---

## 3. 検証済みの技術前提

以下はすべて Renode の公式リポジトリ／ドキュメントで実物を確認済み（推測ではない）。

### 3.1 Renode でできること（確認済み）

| 能力 | 確認した実物 |
| --- | --- |
| マルチマシン + CAN ハブ | `emulation CreateCANHub "canHub"` / `connector Connect sysbus.fdcan1 canHub`（`scripts/multi-node/ramn.resc`） |
| ホスト SocketCAN への橋渡し | `machine CreateSocketCANBridge "socketcan" "vcan0"` + `connector Connect socketcan canHub`（Antmicro 公式ブログ, 2024-11） |
| CAN トラフィックの pcap 取得 | `emulation LogCANTraffic` → Wireshark |
| UART をホスト TCP に露出 | `emulation CreateServerSocketTerminal $port "name" false` + `connector Connect sysbus.uart1 name`（`scripts/complex/hci_uart/hci_uart.resc`） |
| UART を PTY に露出 | `emulation CreateUartPtyTerminal "tmp" "${link}" true`（`tests/unit-tests/host-uart.robot`） |
| マシン間 UART 接続 | `emulation CreateUARTHub "uartHub"` |
| 時間量子の調整 | `emulation SetGlobalQuantum "0.00001"` |
| `.repl` 内に Python 疑似ペリフェラルを直書き | `Python.PythonPeripheral`（`platforms/boards/ramn.repl` の `pwr` / `otp`） |
| Robot Framework で CAN を assert | `Create CAN Tester canHub 1`（`tests/platforms/ramn.robot`） |
| Robot でログ / LED を assert | `Create Log Tester`, `Create LED Tester` |
| ウォッチドッグでリセット | `Timers.STM32_IndependentWatchdog`（`iwdg`） |

さらに RAMN の Robot テストには **UDS 経由で Cortex-M33 シェルコードを実行させる exploit テスト**
（`0xDEADBEEF` を `0x20000000` に書く）が含まれている。すなわち
「エミュレータ上で exploit の成功を CI で assert する」という本プロジェクトの中核パターンは、
上流で既に実証済みである。

### 3.2 ノード MCU の選定: STM32L552（Cortex-M33）

Renode の `platforms/cpus/stm32l552.repl` を実際に読んで確認した内容:

```
cpu: CPU.CortexM          cpuType: "cortex-m33"
fdcan1: CAN.STM32_FDCAN @ sysbus 0x4000A400   + fdcanRAM
rng: Miscellaneous.STM32_RNG
adc1: Analog.STM32L5_ADC
i2c1: I2C.STM32F1_I2C      spi1/2/3
usart1-3, uart4-5, lpuart1  (計 6 本)
gpioPortA..H
iwdg: Timers.STM32_IndependentWatchdog
flashController: MTD.STM32WBA_FlashController
```

選定理由:

1. **RAMN と同一チップ**。Renode の `platforms/boards/ramn.repl` が既に STM32L552 ベースであり、
   モデルの実績がある。将来、実際の RAMN 基板に本ファームを焼くことすら理屈上は可能。
2. **Cortex-M33 = TrustZone-M**。セキュアブート / セキュア世界の演習（Phase 2）が実機同様に扱える。
3. **FDCAN が実装済み**。内部バスに必要。
4. 80 MHz 級なので 4 マシン同時実行が現実的（RAMN が 4 ECU で実証済み）。
5. 実基板が安価に入手可能（NUCLEO-L552ZE-Q 等）。

**注**: Renode の L552 記述には AES ペリフェラルがない。したがって SDLS の暗号はソフトウェア実装
（Zephyr PSA Crypto / mbedTLS）になる。これは欠点ではなく、**鍵がフラッシュ上に平文で存在する**という
現実的な脆弱性を演習に使えるという意味で好都合。

### 3.3 採用しない選択肢とその理由

- **GR712RC / GR716（LEON3FT, 実際の宇宙用 SPARC）**: Renode に `.repl` はあるが、SpaceWire (GRSPW2)、
  CAN、GRTM/GRTC、MIL-STD-1553 はすべて **`Tag`（未実装スタブ）**。実物の中身を確認済み。
  「本物の宇宙 CPU」という訴求力は高いが、肝心の宇宙用ペリフェラルが動かないため土台にできない。
  Phase 3 で「別プラットフォームの選択肢」として再検討する。
- **UT32M0R500（CAES 製ラドハード Cortex-M0+, `CAN.UT32_CAN` 実装済み）**: 実在の耐放射線 MCU で
  CAN も動く。魅力的だが Cortex-M0+ かつ Zephyr のボードサポートが薄く、TrustZone もない。
  Phase 3 で「ラドハード版ノード」の選択肢として残す。
- **STM32H753**: Antmicro の CAN デモで使われており crypto ペリフェラルもあるが、
  Cortex-M7 480 MHz × 4 は重い。L552 を採る。

### 3.4 未検証で Phase 0 で潰すべき前提（リスク）

| # | 前提 | 潰し方 |
| --- | --- | --- |
| R1 | Zephyr の `nucleo_l552ze_q` で FDCAN ドライバが Renode 上で動く | Phase 0 の最初のタスク。動かない場合は devicetree overlay を書く / 最悪 `CAN.STM32_FDCAN` モデルに PR |
| R2 | Renode Monitor の TCP ポート経由で 5 Hz × 4 マシンのセンサ更新が破綻しない | Phase 0 でスパイク。破綻するなら `external-control` API（`tests/external-control/` にある）へ切替 |
| R3 | `SocketCANBridge` が双方向で、ホストからの注入も反映される | Phase 0 で `cansend vcan0` → ノードが受信することを確認 |
| R4 | 4 マシン同時実行の実時間比が実用範囲（目標: 実時間の 0.2 倍以上） | Phase 0 で計測。足りなければノード数を減らす / quantum を調整 |

---

## 4. アーキテクチャ

### 4.1 4 つのプレーン

```
┌──────────────────────────────────────────────────────────────────────┐
│ GROUND PLANE                                    (ホスト / Python)     │
│   gs-console  … 運用者 TUI（HK 表示・コマンド送信・イベントログ）        │
│   gs-core     … TC 組立 / TM 復号 / PUS / SDLS / テレメトリ DB(sqlite) │
│   attack-kit  … 攻撃者ツール（キャプチャ・リプレイ・偽装・ファジング）    │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ TCP :5010 (frames)
┌───────────────────────────────▼──────────────────────────────────────┐
│ CHANNEL PLANE  「真空」                          (ホスト / Python)     │
│   pass scheduler … 軌道→可視ウィンドウ (AOS/LOS)                       │
│   impairments    … 伝搬遅延 / ビット誤り / 断                          │
│   tap & inject   … 攻撃者の観測点かつ注入点（★ 攻撃の主戦場）           │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ TCP :5020 → Renode ServerSocketTerminal
┌───────────────────────────────▼──────────────────────────────────────┐
│ SPACE PLANE                              (Renode / 4 machines)        │
│                                                                       │
│   ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐                     │
│   │  COMM  │  │  OBC   │  │  EPS   │  │  ADCS  │  各 STM32L552 +Zephyr│
│   └───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘                     │
│       └───────────┴───────────┴───────────┘                          │
│                    canHub  (CSP over CAN)                            │
│                       │                                              │
│                       └──► SocketCANBridge ──► vcan0                 │
│                             （Wireshark / can-utils / Scapy）★       │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
        ┌───────────────────────┴────────────────────────┐
        │ vcan0 を購読（アクチュエータ指令を読む）           │
        │                        Renode Monitor TCP :3456 │
        │                        （センサ値を書く）        │
┌───────▼─────────────────────────────────────────────────▼────────────┐
│ PHYSICS PLANE                                   (ホスト / Python)     │
│   orbit (SGP4) → 可視性・日照 / attitude (剛体+磁気トルク) / power     │
│   「攻撃の結果」を可視化する層: スピン・電池枯渇・通信途絶              │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 プレーン境界の設計原則

各プレーンは**プロセス境界と明示的なワイヤフォーマット**で分かれる。理由:

- 攻撃者は現実でも「線の上」にしか居ない。プロセス境界＝攻撃面であり、教材としてそこが観測点になる。
- 各プレーンを単体で差し替え・テストできる（Physics を止めても Space/Ground は動く）。
- Phase 3 で Channel プレーンを GNU Radio に差し替える際、他プレーンを触らずに済む。

### 4.3 ホスト⇄Renode の双方向インタフェース（重要な設計判断）

| 方向 | 手段 | 根拠 |
| --- | --- | --- |
| **Renode → ホスト**（アクチュエータ指令、バス観測） | `SocketCANBridge` → `vcan0` を Physics/攻撃者が購読 | Renode 側 API を一切使わない。ホスト側は普通の SocketCAN ソケット。攻撃者ツールと Physics が同じ観測点を共有できる |
| **ホスト → Renode**（センサ値注入） | Renode Monitor TCP ポートに `adc1.battery Voltage 3.72` 等を発行 | 追加モデル不要。ファームは本物の ADC/I2C 読み出しを行う |
| **ホスト ⇄ Renode**（宇宙リンク） | COMM ノードの `usart2` を `CreateServerSocketTerminal` で TCP 露出 | 確認済みの標準手段 |

R2 が破綻した場合の代替は Renode `external-control` API。

### 4.4 決定性の二モード（ビジネス品質の要）

| モード | 用途 | センサ入力 | 時間 |
| --- | --- | --- | --- |
| `live` | 対話的な攻撃検証・ハンズオン | Physics プレーンが実時間で駆動 | 実時間追従 |
| `replay` | **CI / 再現検証** | 事前収録した `sensor-trace.jsonl` を決定的に注入 | `emulation RunFor` でステップ実行、実時間非依存 |

CI は必ず `replay` モードで走る。これにより「テストが時々落ちる」を構造的に排除する。
攻撃の再現手順も `replay` トレース + 攻撃スクリプトの組で完全に固定される。

---

## 5. ノード構成

RAMN の 4 ECU に対応する 4 ノード。各ノードは**役割・弱点・物理的帰結**をひとつずつ持つ。

| ノード | CSP addr | 役割 | 主なペリフェラル | 仕込む弱点 | 攻撃が通ったときの物理的帰結 |
| --- | --- | --- | --- | --- | --- |
| **COMM** | 5 | TT&C。TC/TM フレーム処理、SDLS、CAN⇄宇宙リンクの中継 | `usart2`(宇宙リンク), `fdcan1`, `rng` | SDLS が既定で無効。有効時も IV 再利用・リプレイ窓なし | なし（入口） |
| **OBC** | 1 | C&DH。PUS サービス、TC スケジュール、FDIR、HK 集約 | `fdcan1`, `flash`, `iwdg` | PUS Service 8 のパラメータ長を検証しないスタックオーバーフロー | 任意コード実行 → 全ノード掌握 |
| **EPS** | 2 | 電力。電池・太陽電池・ロードスイッチ | `fdcan1`, `adc1`(電圧/電流), `gpioPortB`(スイッチ) | CAN 上のロードスイッチ指令に認証がない | **COMM の電源を落とす → 衛星が沈黙（復旧不能）** |
| **ADCS** | 4 | 姿勢。IMU・太陽センサ・磁気トルカ | `fdcan1`, `i2c1`(IMU/照度), `gpioPortC` | トルク指令に範囲検査がない | **スピンアップ → 太陽電池が発電しない → 電池枯渇** |

物理的帰結の連鎖（EPS 攻撃 → 通信途絶、ADCS 攻撃 → 電力枯渇）は意図的な設計である。
RAMN で「車が変な挙動をする」ことが学習効果の核であるのと同じく、
**攻撃が抽象的なフラグではなく衛星の死につながる**ことが本教材の核。

### 5.1 センサ／アクチュエータのモデル化

| 対象 | Renode モデル | 駆動元 |
| --- | --- | --- |
| 電池電圧・電流 | `Analog.Potentiometer @ adc1` | Physics → Monitor |
| 太陽電池発電 | `Analog.Potentiometer @ adc1` | Physics（日照計算） |
| 太陽センサ | `Sensors.VEML7700`（照度センサ）@ `i2c1` | Physics（太陽方向） |
| IMU（ジャイロ/加速度） | `Sensors.LSM6DSO_IMU` @ `i2c1` | Physics（姿勢積分） |
| 磁力計 | `Sensors.LSM9DS1_Magnetic` @ `i2c1` | Physics（IGRF 簡易） |
| 温度 | `Sensors.TMP108` @ `i2c1` | Physics（簡易熱） |
| 磁気トルカ指令 | GPIO / CSP メッセージ | Physics が vcan0 で観測 |
| ロードスイッチ | `gpioPortB` | Physics が vcan0 で観測 |

いずれも Renode に既存のモデルであり、実在の部品である（実機化の道を塞がない）。

---

## 6. プロトコルスタック

すべて**実標準のサブセットを自前実装**する。理由は 3 つ:
(a) 脆弱性を意図的に仕込む必要がある、(b) NOSA / AGPL 依存を避ける、(c) 実装が読める大きさに収まる。

### 6.1 宇宙リンク（COMM ⇄ 地上）

```
┌─────────────────────────────────────────────────────┐
│ TC/TM Transfer Frame  (CCSDS 232.0-B / 132.0-B 最小形) │
│  ├ Frame Header (SCID, VCID, Frame Seq)              │
│  ├ [SDLS Security Header]  ← Phase 2 で有効化可能      │
│  ├ Space Packet (CCSDS 133.0-B)                      │
│  │   └ PUS Packet (ECSS-E-ST-70-41C サブセット)       │
│  ├ [SDLS Security Trailer (MAC)]                     │
│  └ Frame Error Control (CRC-16-CCITT)                │
└─────────────────────────────────────────────────────┘
```

Phase 1 では COP-1 (FARM/FOP)、CLTU/BCH 符号化、AOS は実装しない。
フレーム同期は ASM（Attached Sync Marker `0x1ACFFC1D`）+ 固定長で行う。

### 6.2 PUS サービス（Phase 1 で実装する範囲）

| Service | 名称 | 用途 | セキュリティ上の意味 |
| --- | --- | --- | --- |
| 1 | Request Verification | TC 受理/実行の成否報告 | 攻撃の成否が観測できる＝攻撃者にも有用 |
| 3 | Housekeeping | 周期 TM | 偽装 TM の対象 |
| 5 | Event Reporting | 異常イベント | 検知演習の情報源 |
| 8 | Function Management | 任意関数呼び出し | **★ スタックオーバーフローを仕込む場所** |
| 9 | Time Management | 時刻同期 | 時刻ずらし → スケジュール攻撃の前段 |
| 11 | Time-based Scheduling | 未来時刻の TC 予約 | **★ 遅発性の破壊コマンドを仕込める** |
| 17 | Test | 疎通確認 | Phase 0 の疎通対象 |

Service 6 (Memory Management) は Phase 2 で追加し、鍵抽出演習に使う。

### 6.3 内部バス（CSP over CAN）

CubeSat Space Protocol v1 のサブセットを自前実装（`libcsp` と**ワイヤ互換**、ただし依存しない）。
32 bit ヘッダ（priority / src / dst / dport / sport / flags）と CFP フラグメンテーションを実装。
ホスト側では `libcsp`(MIT) を参照実装として相互運用テストに使う。

CAN 上に流れるため、`vcan0` 経由で `candump` / Wireshark / Scapy から素で観測・注入できる。
**これが RAMN と同じ「手触り」を与える核心**。

### 6.4 SDLS（Phase 2）

CCSDS 355.0-B 相当のサブセット: SPI(Security Parameter Index)、IV、AES-256-GCM による
認証暗号、アンチリプレイ窓。Zephyr PSA Crypto を使用。
NASA CryptoLib は**参照用にテストベクタのみ突き合わせ**、コードは取り込まない（NOSA 回避）。

---

## 7. 演習カタログ

各演習 (`exercises/EX-xxx/`) は次の 5 点セットで構成する。**この形式が本プロジェクトの製品価値である。**

```
exercises/EX-B01-eps-killswitch/
├── README.md          … シナリオ、学習目標、SPARTA TTP、想定所要時間、ヒント3段階
├── scenario.resc      … この演習用の Renode 起動スクリプト（脆弱版ファームを載せる）
├── solve.py           … 模範解答となる攻撃スクリプト
├── mitigation.md      … 対策の設計と、対策版ファームの config フラグ
└── verify.robot       … ★ 攻撃成功を assert / 対策適用後は失敗を assert
```

### 7.1 Phase 1 の演習（4 層に 1 つずつ）

| ID | 層 | 内容 | 成功条件 |
| --- | --- | --- | --- |
| **EX-L01** | 宇宙リンク | 可視パス中の TC をキャプチャし、次のパスでリプレイする | 認証なしでコマンドが再実行される |
| **EX-B01** | 内部バス | `vcan0` に偽の CSP フレームを注入し、EPS に COMM の電源を切らせる | 衛星が沈黙し TM が途絶える |
| **EX-F01** | ファーム | PUS Service 8 のパラメータ長検証欠如を突き、OBC で任意コードを実行する | OBC RAM の所定アドレスにマーカーが書かれる |
| **EX-G01** | 地上系 | 地上局の TC スケジュール DB を汚染し、次パスで破壊的コマンドを送らせる | 運用者が意図しない TC が送信される |

### 7.2 Phase 2 の演習

- **EX-L02** TM 偽装 → 運用者コンソールに嘘の HK を表示させる
- **EX-B02** CAN バス飽和 → FDIR 誤作動
- **EX-A01** ADCS トルク指令の範囲検査欠如 → スピンアップ → 電力枯渇
- **EX-F02** PUS Service 6 のメモリ読み出しで SDLS 鍵をフラッシュから抽出
- **EX-S01〜** 防御ラボ: SDLS 有効化 / セキュアブート / バス IDS / TC 認証 を入れて上記が失敗することを確認

### 7.3 SPARTA へのマッピング

sparta.aerospace.org が STIX2 JSON を配布していることを確認済み。
`tools/sparta_map.py` で STIX2 をダウンロードし、各演習の `README.md` フロントマターに書かれた
TTP ID の**実在を検証**する（存在しない ID は CI で落とす）。
具体的な TTP ID の割当は実装時に公式データから機械的に行う — **本仕様書では ID を推測で書かない**。

---

## 8. エラー処理と FDIR

| 層 | 異常 | 挙動 |
| --- | --- | --- |
| Channel | ビット誤り / 断 | フレーム破棄。**生バイトは必ず保存**（セキュリティ道具として鑑識性を最優先） |
| 宇宙リンク | CRC 不一致 / フレーム長不正 | 破棄 + カウンタ加算 + PUS 5 イベント |
| 宇宙リンク | SDLS 認証失敗（Phase 2） | 破棄 + イベント + 連続失敗でパス遮断 |
| PUS | 未知の service/subtype | PUS 1 の否定応答 |
| 内部バス | CSP CRC32 不一致 | 破棄 + カウンタ |
| ノード | ハング | `iwdg` によるリセット（Renode で実証済みの機構） |
| EPS | 電圧低下 | セーフモード遷移（非必須負荷を落とす） |
| 地上局 | デコード不能 | 例外を握り潰さず、生フレームを DB に保存して継続 |

---

## 9. 検証戦略

| 層 | 手段 | 対象 |
| --- | --- | --- |
| ユニット | `pytest` + `hypothesis` | CCSDS/PUS/CSP/SDLS コーデックのラウンドトリップ、境界値 |
| 相互運用 | `pytest` | 自前 CSP 実装 ⇄ `libcsp`(MIT) のワイヤ互換 |
| ノード単体 | `renode-test` (Robot) | 各ノードが起動し HK を出す |
| 結合 | `renode-test` (Robot) | 地上局 TC → COMM → CAN → OBC → TM 応答の往復 |
| **演習** | `renode-test` (Robot) | **攻撃成功の assert + 対策適用時の失敗 assert（両方向）** |
| 性能 | ベンチスクリプト | 4 マシンの実時間比。閾値割れで CI 警告 |
| 供給網 | `pip-audit` / SBOM | 商用利用可否の継続確認 |

CI: GitHub Actions。Renode と Zephyr SDK をバージョン固定した Docker イメージを使用。
`antmicro/renode-test-action` が利用可能。

---

## 10. リポジトリ構成

```
space-cs-sim/
├── platforms/cubesat/            # .repl : ノード別プラットフォーム記述
│   ├── node-common.repl          #   STM32L552 + 共通ペリフェラル
│   ├── OBC.repl  COMM.repl  EPS.repl  ADCS.repl
├── scripts/                      # .resc
│   ├── single-node/cubesat.resc
│   ├── multi-node/cubesat.resc   #   canHub + 4 ノード + SocketCAN ブリッジ
├── firmware/                     # Zephyr アプリ (west workspace)
│   ├── common/
│   │   ├── ccsds/  pus/  csp/  sdls/     # プロトコル実装（脆弱版/対策版を config で切替）
│   │   └── fdir/
│   └── apps/{obc,comm,eps,adcs}/
├── src/cuberange/                # Python パッケージ (Apache-2.0)
│   ├── proto/                    #   ccsds, pus, csp, sdls コーデック
│   ├── channel/                  #   遅延・誤り・パス窓・tap/inject
│   ├── physics/                  #   orbit(SGP4), attitude, power, thermal
│   ├── gs/                       #   地上局コア + TUI コンソール
│   ├── attack/                   #   攻撃者ツールキット
│   └── bridge/                   #   Renode Monitor クライアント, vcan I/F
├── exercises/EX-*/               # 演習 5 点セット
├── tests/{robot,pytest}/
├── tools/                        # sparta_map.py, sensor-trace 収録, ベンチ
├── docker/                       # Renode + Zephyr SDK 固定イメージ
├── docs/
│   ├── architecture.md  protocols.md  threat-model.md  hardware-path.md
│   └── superpowers/specs/
└── Makefile                      # make demo / make test / make exercise EX=...
```

**言語方針**: リポジトリの公開ドキュメントは英語を正とし、`docs/ja/` に日本語版を置く。
本設計書のみ日本語（意思決定の記録として）。

---

## 11. ライセンス方針

| 対象 | 方針 |
| --- | --- |
| 本プロジェクトのコード | **Apache-2.0** |
| Renode (MIT) | 依存 OK |
| Zephyr (Apache-2.0) | 依存 OK |
| libcsp (MIT) | テスト時のみ依存 OK |
| **NASA CryptoLib / NOS3 (NOSA 1.3)** | **コードを取り込まない。** 仕様とテストベクタの参照のみ |
| **Yamcs / OpenC3 COSMOS (AGPL-3.0)** | **コアに組み込まない。** 別パッケージの任意アダプタとしてのみ提供 |
| gr-satellites (GPL-3.0) | Phase 3 で任意依存として検討（コアには入れない） |

CI で SBOM を生成し、ライセンス逸脱を検出する。

---

## 12. 倫理・安全性

- シミュレータは完全に合成環境であり、実在の衛星・地上局・周波数・鍵を一切含まない。
- 演習は自己完結型で、外部ネットワークへの送信を行わない（CI でも egress を禁止）。
- 攻撃ツールは本シミュレータのプロトコル実装に対してのみ動作する（実運用機材への転用可能性を最小化）。
- 各演習には必ず対策編を対にする。攻撃のみを教えない。

---

## 13. フェーズ計画

| Phase | 目標 | 完了条件 |
| --- | --- | --- |
| **P0 歩く骨格** | 2 ノード (COMM+OBC) で地上局→リンク→CAN→OBC→TM の往復が通る。R1〜R4 のリスクを潰す | `make demo` が動き、往復を assert する Robot テストが CI で緑 |
| **P1 最小有用** | 4 ノード、PUS 1/3/5/8/9/11/17、CSP over CAN、Physics（軌道・電力・姿勢）、演習 4 本 | 4 演習すべてが「攻撃成功」を CI で assert。`vcan0` で Wireshark 観測ができる |
| **P2 防御ラボ** | SDLS、セキュアブート(TF-M)、FDIR 強化、検知演習、SPARTA マッピング、Yamcs/OpenC3 アダプタ | 各攻撃演習に対応する「対策適用で失敗する」テストが CI で緑 |
| **P3 拡張** | GNU Radio ベースバンド、実 SDR、実基板ブリングアップ、ラドハード MCU ノード | 別途スコープ |

**本仕様書は P0 と P1 を対象とする。** P2/P3 は方向性の記載に留め、別途 spec を書く。

---

## 14. 未解決事項

| # | 事項 | 判断時期 |
| --- | --- | --- |
| Q1 | プロダクト名 `CubeRange` の商標・既存プロジェクト衝突確認 | 公開前 |
| Q2 | Zephyr の重さ（SDK 数 GB）が受け入れ可能か。代替は FreeRTOS + STM32 HAL（RAMN 方式） | P0 の R1 検証と同時 |
| Q3 | 地上局 TUI に `textual` を使うか `rich` 単体で足りるか | P1 着手時 |
| Q4 | Physics を別プロセスにするか gs-core 内スレッドにするか | P1 着手時 |

---

## 15. 参考

- Renode — https://github.com/renode/renode (MIT)
- Renode CAN + SocketCAN — https://antmicro.com/blog/2024/11/demonstrating-can-support-in-renode
- RAMN — https://github.com/ToyotaInfoTech/RAMN
- NASA NOS3 — https://github.com/nasa/nos3 (NOSA 1.3)
- NASA CryptoLib — https://github.com/nasa/CryptoLib (NOSA 1.3)
- libcsp — https://github.com/libcsp/libcsp (MIT)
- Hack-A-Sat QEMU tooling — https://github.com/solar-wine/tools-for-hack-a-sat-2020
- Hack-A-Sat library — https://github.com/deptofdefense/hack-a-sat-library
- SPARTA — https://sparta.aerospace.org/ (STIX2 JSON 配布あり)
