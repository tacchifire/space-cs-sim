# CubeRange — 設計仕様書

**リポジトリ**: `space-cs-sim`
**プロダクト名**: CubeRange（暫定 / 公表前に商標確認）
**Python パッケージ名**: `cuberange`
**版**: 2 版（2026-08-28、design-court による是正後）
**状態**: 実測により裏付け済み。実装計画待ち

---

## 0. この文書の証拠規則

**実行して確かめていない主張は書かない。**

本書の Renode に関する能力主張は、すべて `tools/renode-probe/probe.sh` により再現できる。
再現できない主張は本書に載せない。1 版はソースコードを読んだだけで「検証済み」と称しており、
実際に Renode を起動した結果 **6 件の重大な誤り**が判明した。その記録を §16 に残す。

CI は `probe.sh` を実行し、前提が壊れたら落ちる。

```
$ RENODE_DIR=... ./tools/renode-probe/probe.sh
Renode v1.16.1.17033 (build d66b0c2a-202602160923, .NET 8.0.12)

== A. Core topology ==
  PASS  emulation CreateCANHub
  PASS  4x nucleo_h753zi joined to canHub
  PASS  CreateServerSocketTerminal + connect (space link)
  PASS  usart CreateFileBackend (headless console capture)
== B. Peripheral models required by the node design ==
  PASS  temperature  TMP108 @ i2c1
  PASS  power monitor PAC1934 @ i2c1
  PASS  battery gauge MAX77818 @ i2c1
  PASS  IMU          LSM9DS1_IMU @ i2c1
  PASS  magnetometer LSM9DS1_Magnetic @ i2c1
  PASS  gyroscope    LSM330_Gyroscope @ i2c1
  PASS  light/sun    OB1203 @ i2c1
== C. Optional host integrations (must NOT be required) ==
  SKIP  SocketCAN bridge  -- command exists; host vcan0 absent
  SKIP  LogCANTraffic (Wireshark pcap)  -- Wireshark absent; not usable headless
== D. Real firmware: Zephyr + FDCAN across four nodes ==
  PASS  Zephyr booted on 4/4 nodes
  PASS  4/4 nodes received CAN frames via hub
  MEAS  4-node speed: 8 virtual s in 41.1 s wall = .19x real time
```

---

## 1. 目的とスコープ

### 1.1 一文で

**Renode 上で衛星のファームウェアを命令レベルで動かし、地上局・無線リンク・衛星内部バス・OBC ファームの
4 層すべてに実際に攻撃を通せる、宇宙システム版の RAMN。**

### 1.2 背景

Toyota RAMN は「4 個の MCU と CAN バスを 1 枚の基板に載せる」ことで、自動車セキュリティを
*実際に手を動かして* 学べるものにした。本プロジェクトはその宇宙版を作る。物理基板を先に作るのではなく、
Renode によるマルチマシン・エミュレーションで同等の体験を先に成立させる。

### 1.3 利用者と成功条件

一次利用者はプロジェクトオーナー自身（攻撃検証の PoC 基盤）。ただし**同時にハンズオン学習基盤としても
成立している**ことを要件とする。品質水準は「ビジネスでも使えるレベル」— すなわち:

| 成功条件 | 測り方 |
| --- | --- |
| 攻撃が本当に通る | 各演習に「攻撃成功」を assert する CI テストがある |
| 防御が本当に効く | 同じ演習に「対策適用後は攻撃が失敗する」を assert する CI テストがある |
| 誰の手元でも同じに動く | 固定バージョンの Renode で `make demo` が一発で通る。特権も GUI も不要 |
| 主張が検証可能 | 前提はすべて `probe.sh` で再現でき、CI で継続的に確認される |
| 現実と地続き | 実標準（CCSDS / ECSS PUS / CSP）のサブセットに準拠 |
| 実機化の道が塞がっていない | 実在 MCU + 実 HAL(Zephyr)。Renode 固有の抜け道を使わない |
| 商用利用の障害がない | 自作コードは Apache-2.0。コピーレフト / NOSA コードを取り込まない |

### 1.4 非目標（YAGNI）

- 高精度な軌道力学（摂動、詳細熱モデル）— 攻撃の帰結が見える最小限で足りる
- 実 SDR / RF ハードウェア（Phase 3 以降）
- CTF スコアサーバ、ユーザ管理、マルチテナント
- 物理基板の設計・製造（道は塞がないが、作らない）
- CCSDS フル準拠（COP-1、CLTU/BCH、AOS の完全実装）

---

## 2. ポジショニング — なぜ既存物の再発明でないか

| 既存 | 何であるか | 忠実度 | ライセンス | 空白 |
| --- | --- | --- | --- | --- |
| **NASA NOS3** (617★, 活発) | cFS + NOS Engine + 42 + COSMOS/Yamcs を束ねた運用シミュレータ | **システム／運用レベル**。cFS は Linux 上でネイティブ実行。MCU の命令エミュレーションはしない | **NOSA 1.3** | ファーム内部のメモリ破壊・鍵抽出・フォールト注入ができない。NOSA は商用取り込みの障壁 |
| **Hack-A-Sat** (`solar-wine/tools-for-hack-a-sat-2020`, 97★) | 決勝用に衛星ファームを QEMU でエミュレートした一式 | ファームレベル ✓ | 混在 | 2020 年で更新停止。単発の CTF 用。バスも地上系も統合されていない |
| **RAMN** (275★, 活発) | 4 ECU + CAN の教材基板 | 命令レベル ✓ | — | **完全に自動車ドメイン** |
| **Renode** (2786★, MIT) | エミュレータ本体 | — | **MIT** ✓ | 宇宙向けのプラットフォーム記述・シナリオが存在しない |
| **Yamcs / OpenC3 COSMOS** | 実運用実績のある地上管制ソフト | 運用レベル | **AGPL-3.0** | 攻撃側の道具ではない。AGPL は製品組み込みの障壁 |

**空白＝我々の位置**: *命令レベルで動く衛星ファーム*と*内部バス*と*地上系*を一本に繋ぎ、
セキュリティ演習を CI で検証済みの形で同梱したもの。NOS3 が「運用レベル」なら CubeRange は
「シリコン／ファームレベル」であり、両者は競合ではなく補完関係にある。

---

## 3. 実測に基づく技術前提

### 3.1 プラットフォーム決定: Renode 1.16.1 + STM32H753 + Zephyr

| 決定 | 実測による根拠 |
| --- | --- |
| **Renode 1.16.1（リリース版に固定）** | 再現性のため master には依存しない。1.16.1 portable を実際に導入し全機能を確認 |
| **ノード MCU = STM32H753**（`platforms/boards/nucleo_h753zi.repl`） | 1.16.1 の `stm32h743.repl` に `fdcan1/fdcan2: CAN.MCAN @ 0x4000A000/0x4000A400` が実在。4 機同時起動を実測 |
| **RTOS = Zephyr** | Antmicro 配布の `nucleo_h743zi--zephyr-samples-drivers-can-counter.elf` を 4 ノードで実行し、**4/4 が起動、4/4 が CAN ハブ経由で相互受信**することを実測（`Booting Zephyr OS build v3.5.0-rc2` / `Counter received`） |
| **実基板 = NUCLEO-H753ZI** | Renode に同名ボード記述があり、実在の入手可能な基板 |

### 3.2 STM32L552 を採らなかった理由（1 版からの変更）

1 版は「RAMN と同じ STM32L552」を中核に据えていた。実測の結果、**二重に成立しない**:

- **Renode 1.16.1 の `platforms/cpus/stm32l552.repl` に FDCAN が存在しない**
  （`grep -n 'fdcan\|CAN\.' stm32l552.repl` → 0 件）。FDCAN は master にのみ追加されている。
  リリース版固定という business 要件と両立しない。
- **Zephyr の `nucleo_l552ze_q` は CAN 非対応**。`nucleo_l552ze_q.yaml` の `supported:` に `can` が無く、
  `dts/arm/st/l5/stm32l5.dtsi` の `fdcan1` は `status = "disabled"`。Renode 用の `.resc` も無い
  （`boards/st/nucleo_l552ze_q/support/` は `openocd.cfg` のみ）。

**失うもの**: Cortex-M33 の TrustZone-M。セキュアブート演習は TF-M ではなく **MCUboot** で行う
（CubeSat の実装としてもこちらが一般的）。

**残す選択肢**: Renode master が安定リリースに入った時点で L552 ノードを追加オプションとして再評価する。
その場合「RAMN 実基板にそのまま焼ける」という利点が復活する。

### 3.3 確認済みの Renode 機構（すべて `probe.sh` で再現可能）

| 能力 | コマンド | 状態 |
| --- | --- | --- |
| マルチマシン + CAN ハブ | `emulation CreateCANHub "canHub"` / `connector Connect sysbus.fdcan1 canHub` | **PASS** |
| 宇宙リンク（UART→ホスト TCP） | `emulation CreateServerSocketTerminal 5020 "spacelink" false` + `connector Connect sysbus.usart3 spacelink` | **PASS** |
| ヘッドレスなコンソール捕捉 | `sysbus.usart3 CreateFileBackend @<path> true` | **PASS** |
| Monitor を TCP 公開（センサ注入経路） | `renode --port 1234` | **存在確認済み**（`--help`） |
| ホスト SocketCAN 橋渡し | `machine CreateSocketCANBridge "scb" "vcan0"` | **条件付き** — コマンドは 1.16.1 に実在。`vcan0` 作成に **root が必要**（`sudo modprobe vcan` 等）。Linux 限定 |
| CAN を pcap で観測 | `emulation LogCANTraffic` | **条件付き** — **Wireshark 必須**。未導入だと "Wireshark is not installed" で失敗し、しかも `.resc` 全体が中断する。ヘッドレス CI では使わない |
| 時間量子 | `emulation SetGlobalQuantum "0.0001"` | **PASS** |
| ウォッチドッグ | `Timers.STM32_IndependentWatchdog`（`watchdog`） | 実在確認済み |

### 3.4 実測性能とその設計上の帰結（重要）

| 構成 | 実測 |
| --- | --- |
| 4 ノード、NOP ループのみ（非代表的） | 仮想 10 s / 実時間 7.4 s = **1.3× 実時間** |
| 2 ノード、実 Zephyr、**ログ抑制なし** | 仮想 6 s / 実時間 74 s = **0.08× 実時間** |
| 2 ノード、実 Zephyr、ログ抑制あり | 仮想 6 s / 実時間 9.95 s = **0.60× 実時間** |
| 4 ノード、実 Zephyr、ログ抑制あり | 仮想 8 s / 実時間 27〜41 s = **0.19〜0.30× 実時間**（RSS 448 MB） |

ここから 3 つの設計要件が導かれる:

1. **ログ抑制は性能要件である。** 未実装レジスタへのアクセス警告が性能を **約 12 倍**悪化させる。
   全 `.resc` は `logLevel 3 sysbus` / `rcc` / `fdcan1` を既定で設定する。
   演習でログが必要なときのみ明示的に引き上げる。
2. **壁時計に追従する `live` モードは成立しない。** 4 ノードでは実時間の 0.2〜0.3 倍しか進まない。
   1 版が書いていた「実時間追従」は撤回する（§4.4 参照）。
3. **ノード数は 4 が上限。** 攻撃者ノードを常設で足すと 5 ノードとなり更に遅くなるため、
   攻撃者ノードは必要な演習でのみ起動する。

### 3.5 センサ／アクチュエータのモデル（実測で確定）

1 版の表は 3 件が誤りだった。以下は **`probe.sh` で PASS を確認済み**のもののみ。

| CubeSat 機能 | Renode モデル | バス | 用途 |
| --- | --- | --- | --- |
| 電源電圧・電流・電力 | `Sensors.PAC1934` | i2c1 | EPS の電力監視（実 CubeSat EPS も同種の IC を使う） |
| 電池残量 | `Sensors.MAX77818` | i2c1 | EPS のバッテリ・フューエルゲージ |
| ジャイロ + 加速度 | `Sensors.LSM9DS1_IMU` | i2c1 | ADCS の姿勢推定 |
| 磁力計 | `Sensors.LSM9DS1_Magnetic` | i2c1 | ADCS の磁場観測（磁気トルカ制御の入力） |
| ジャイロ（代替） | `Sensors.LSM330_Gyroscope` | i2c1 | |
| 太陽センサ（照度で代用） | `Sensors.OB1203` | i2c1 | 日照判定・粗い太陽方向 |
| 温度 | `Sensors.TMP108` | i2c1 | 熱・FDIR |
| 磁気トルカ／ロードスイッチ | GPIO + CSP メッセージ | fdcan1 | Physics 側が観測（§4.3） |

**使用不可と実測で判明したもの**（1 版の誤り）:

| 1 版の記載 | 実測結果 |
| --- | --- |
| `Sensors.VEML7700`（太陽センサ） | `Error E04: Could not resolve type` — 1.16.1 に存在しない |
| `Analog.Potentiometer`（電池電圧） | `Error E04: Could not resolve type` — 1.16.1 に存在しない |
| `Sensors.LSM6DSO_IMU @ i2c1` | `Error E05: Register 'i2c1' ... does not provide an interface` — I2C 登録不可 |

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
                                │ TCP :5020 → CreateServerSocketTerminal
┌───────────────────────────────▼──────────────────────────────────────┐
│ SPACE PLANE                (Renode 1.16.1 / 4 machines, STM32H753)    │
│                                                                       │
│   ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐                     │
│   │  COMM  │  │  OBC   │  │  EPS   │  │  ADCS  │   Zephyr            │
│   └───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘                     │
│       └───────────┴───────────┴───────────┘                          │
│                    canHub  (CSP over CAN)                            │
│                       │                                              │
│                       ├──► [任意] SocketCANBridge ──► vcan0           │
│                       │        （Linux + root のときのみ）            │
│                       └──► [既定] ATTACKER ノード（演習時のみ起動）    │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  Renode 仮想時間が全体のマスタークロック
┌───────────────────────────────▼──────────────────────────────────────┐
│ PHYSICS PLANE                                   (ホスト / Python)     │
│   orbit (SGP4) → 可視性・日照 / attitude (剛体+磁気トルク) / power     │
│   「攻撃の結果」を可視化: スピン・電池枯渇・通信途絶                    │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 プレーン境界の設計原則

各プレーンはプロセス境界と明示的なワイヤフォーマットで分かれる。

- 攻撃者は現実でも「線の上」にしか居ない。プロセス境界＝攻撃面であり、そこが観測点になる。
- 各プレーンを単体で差し替え・テストできる。
- Phase 3 で Channel プレーンを GNU Radio に差し替える際、他プレーンを触らずに済む。

### 4.3 ホスト⇄Renode の双方向インタフェース

| 方向 | 既定の手段（特権不要） | 代替 |
| --- | --- | --- |
| **宇宙リンク** | COMM の `usart3` を `CreateServerSocketTerminal` で TCP 露出。**PASS 済み** | — |
| **ホスト → Renode**（センサ値注入） | Renode Monitor TCP（`renode --port`）に `i2c1.imu ...` 等を発行 | `external-control` API |
| **Renode → ホスト**（アクチュエータ観測） | **ATTACKER/TAP ノードは使わず**、EPS/ADCS が自ノードの状態を PUS HK として downlink し、Physics は地上系の TM を購読する | Linux + root があれば `vcan0` を直接購読するほうが素直 |
| **攻撃者のバス注入** | 演習時に **ATTACKER ノード**（5 台目の Renode マシン）を canHub に接続し、そのUARTソケット経由でホストからフレームを送る | Linux + root があれば `cansend vcan0` |

**設計判断**: `vcan0` 経路は「あれば嬉しい」であって**必須にしない**。
実測で `modprobe vcan` に root が必要であり、Linux 限定であることが確認されたため。
既定経路は特権なしで完結する。`vcan0` が使える環境では `--with-vcan` で有効化し、
`candump` / Wireshark / Scapy という RAMN と同じ手触りが得られる。

### 4.4 時間モデル（1 版から全面変更）

実測（§3.4）より、壁時計追従は不可能。**Renode の仮想時間を唯一のマスタークロックとする。**

- Channel / Physics / Ground の各プレーンは、壁時計ではなく **Renode の仮想時刻**を参照して進む。
  タイムアウト・パス窓・周期 HK はすべて仮想時間で定義する。
- 実時間の 0.2 倍しか進まないことは、**仮想時間で設計されている限り問題にならない**。
- 体感を良くするため **時間圧縮**を設ける: 1 パス = 実軌道の 600 秒ではなく、
  既定で **60 仮想秒**にスケールする（`--pass-duration` で変更可）。

| モード | 用途 | センサ入力 | 時間 |
| --- | --- | --- | --- |
| `interactive` | 対話的な攻撃検証・ハンズオン | Physics が仮想時刻に同期して駆動 | Renode 仮想時間（壁時計比 0.2〜0.3×） |
| `replay` | **CI / 再現検証** | 収録済み `sensor-trace.jsonl` を決定的に注入 | `emulation RunFor` でステップ実行 |

CI は必ず `replay` で走る。これにより「テストが時々落ちる」を構造的に排除する。

---

## 5. ノード構成

| ノード | CSP addr | 役割 | 主なペリフェラル | 仕込む弱点 | 攻撃が通ったときの物理的帰結 |
| --- | --- | --- | --- | --- | --- |
| **COMM** | 5 | TT&C。TC/TM フレーム処理、SDLS、CAN⇄宇宙リンク中継 | `usart3`(宇宙リンク), `fdcan1`, `rng` | SDLS が既定で無効。有効時も IV 再利用・リプレイ窓なし | なし（入口） |
| **OBC** | 1 | C&DH。PUS サービス、TC スケジュール、FDIR、HK 集約 | `fdcan1`, flash, `watchdog` | PUS Service 8 のパラメータ長を検証しないスタックオーバーフロー | 任意コード実行 → 全ノード掌握 |
| **EPS** | 2 | 電力。電池・太陽電池・ロードスイッチ | `fdcan1`, `PAC1934`, `MAX77818`, GPIO | CAN 上のロードスイッチ指令に認証がない | **COMM の電源を落とす → 衛星が沈黙** |
| **ADCS** | 4 | 姿勢。IMU・磁力計・太陽センサ・磁気トルカ | `fdcan1`, `LSM9DS1_IMU`, `LSM9DS1_Magnetic`, `OB1203`, GPIO | トルク指令に範囲検査がない | **スピンアップ → 発電不能 → 電池枯渇** |
| **ATTACKER** | 任意 | 演習時のみ起動する 5 台目。侵害済み subsystem を演じる | `fdcan1`, `usart3`(ホスト制御) | — | 特権なしでバス注入を可能にする |

物理的帰結の連鎖（EPS 攻撃 → 通信途絶、ADCS 攻撃 → 電力枯渇）は意図的である。
RAMN で「車が変な挙動をする」ことが学習効果の核であるのと同じく、
**攻撃が抽象的なフラグではなく衛星の死につながる**ことが本教材の核。

---

## 6. プロトコルスタック

すべて**実標準のサブセットを自前実装**する。理由:
(a) 脆弱性を意図的に仕込む必要がある、(b) NOSA / AGPL 依存を避ける、(c) 実装が読める大きさに収まる。

### 6.1 宇宙リンク（COMM ⇄ 地上）

```
ASM 0x1ACFFC1D
┌─────────────────────────────────────────────────────┐
│ TC / TM Transfer Frame（最小形）                      │
│  ├ Frame Header (SCID, VCID, Frame Seq)              │
│  ├ [SDLS Security Header]  ← Phase 2 で有効化可能      │
│  ├ Space Packet                                      │
│  │   └ PUS Packet                                    │
│  ├ [SDLS Security Trailer (MAC)]                     │
│  └ Frame Error Control (CRC-16-CCITT)                │
└─────────────────────────────────────────────────────┘
```

参照する標準（`ccsds.org` の実 PDF で番号を確認したもの）:

| 対象 | 文書番号 | 確認状況 |
| --- | --- | --- |
| TM Space Data Link Protocol | **CCSDS 132.0-B-3**（2021-10） | ccsds.org/Pubs/132x0b3.pdf を確認 |
| TC Space Data Link Protocol | **CCSDS 232.0-B-4**（2021-10） | 確認 |
| Space Data Link Security (SDLS) | **CCSDS 355.0-B-2** | ccsds.org/Pubs/355x0b2.pdf を確認 |
| SDLS — Extended Procedures | **CCSDS 355.1-B-1**（2020-02） | ccsds.org/Pubs/355x1b1.pdf を確認 |
| Space Packet Protocol | CCSDS 133.0-B（issue 要確認） | **未確定** — 実装前に ccsds.org で issue を確定する |
| AOS Space Data Link | CCSDS 732.0-B-5（2025-10, 732.0-B-4 は廃止） | 二次情報。実装対象外のため参考 |
| COP-1 / TC Sync & Channel Coding (CLTU) | CCSDS 232.1-B / 231.0-B（issue 要確認） | **未確定** — Phase 1 では実装しないため保留 |

Phase 1 では COP-1、CLTU/BCH 符号化、AOS を実装しない。フレーム同期は ASM + 固定長で行う。

### 6.2 PUS サービス（Phase 1 の範囲）

参照標準は **ECSS-E-ST-70-41C**（サービス番号と名称は実装前に規格本文で照合する）。

| Service | 用途 | セキュリティ上の意味 |
| --- | --- | --- |
| 1 | TC 受理/実行の成否報告 | 攻撃の成否が観測できる |
| 3 | 周期ハウスキーピング TM | 偽装 TM の対象 |
| 5 | イベント報告 | 検知演習の情報源 |
| 8 | 関数管理 | **★ スタックオーバーフローを仕込む場所** |
| 9 | 時刻管理 | 時刻ずらし → スケジュール攻撃の前段 |
| 11 | 時刻ベースのスケジューリング | **★ 遅発性の破壊コマンドを仕込める** |
| 17 | 疎通確認 | Phase 0 の疎通対象 |

Service 6（メモリ管理）は Phase 2 で追加し、鍵抽出演習に使う。

### 6.3 内部バス（CSP over CAN）

CubeSat Space Protocol の**バージョン 1**（32 bit ヘッダ）のサブセットを自前実装する。
`libcsp`(MIT) と**ワイヤ互換**とし、ホスト側テストでは `libcsp` を参照実装として突き合わせる。
CSP v2 は 48 bit ヘッダで非互換だが、既存 CubeSat の現場・既存ツールは依然 v1 が主流であるため
Phase 1 は v1 を採る（Phase 2 で v2 対応を検討）。

CAN 上に流れるため、`vcan0` が使える環境では `candump` / Wireshark / Scapy から素で観測・注入できる。

### 6.4 SDLS（Phase 2）

CCSDS 355.0-B-2 相当のサブセット: SPI、IV、AES-256-GCM による認証暗号、アンチリプレイ窓。
Zephyr PSA Crypto を使用（STM32H753 の crypto アクセラレータは Renode 1.16.1 の
`stm32h743.repl` には含まれないため**ソフトウェア実装**とする — これは鍵がフラッシュ上に
存在するという現実的な脆弱性を演習に使えるため好都合）。
NASA CryptoLib は**テストベクタの突き合わせのみ**に使い、コードは取り込まない（NOSA 回避）。

---

## 7. 演習カタログ

各演習 (`exercises/EX-xxx/`) は 5 点セットで構成する。**この形式が本プロジェクトの製品価値である。**

```
exercises/EX-B01-eps-killswitch/
├── README.md          … シナリオ、学習目標、SPARTA TTP、所要時間、ヒント3段階
├── scenario.resc      … 演習用 Renode 起動スクリプト（脆弱版ファーム、ログ抑制込み）
├── solve.py           … 模範解答となる攻撃スクリプト
├── mitigation.md      … 対策の設計と、対策版ファームの config フラグ
└── verify.robot       … ★ 攻撃成功を assert / 対策適用後は失敗を assert
```

### 7.1 Phase 1 の演習（4 層に 1 つずつ）

| ID | 層 | 内容 | 成功条件 |
| --- | --- | --- | --- |
| **EX-L01** | 宇宙リンク | 可視パス中の TC をキャプチャし、次のパスでリプレイ | 認証なしでコマンドが再実行される |
| **EX-B01** | 内部バス | ATTACKER ノード（または `vcan0`）から偽 CSP フレームを注入し、EPS に COMM の電源を切らせる | 衛星が沈黙し TM が途絶える |
| **EX-F01** | ファーム | PUS Service 8 のパラメータ長検証欠如を突き、OBC で任意コード実行 | OBC RAM の所定アドレスにマーカーが書かれる |
| **EX-G01** | 地上系 | 地上局の TC スケジュール DB を汚染し、次パスで破壊的コマンドを送らせる | 運用者が意図しない TC が送信される |

### 7.2 Phase 2 の演習

- **EX-L02** TM 偽装 → 運用者コンソールに嘘の HK を表示
- **EX-B02** CAN バス飽和 → FDIR 誤作動
- **EX-A01** ADCS トルク範囲検査欠如 → スピンアップ → 電力枯渇
- **EX-F02** PUS Service 6 のメモリ読み出しで SDLS 鍵をフラッシュから抽出
- **EX-S01〜** 防御ラボ: SDLS 有効化 / MCUboot セキュアブート / バス IDS / TC 認証

### 7.3 SPARTA / SPACE-SHIELD へのマッピング

SPARTA（Aerospace Corporation）と ESA SPACE-SHIELD はいずれも STIX/JSON を配布している。
`tools/sparta_map.py` が両者をダウンロードし、各演習 `README.md` のフロントマターに書かれた
TTP ID の**実在を検証**する（存在しない ID は CI で落とす）。
**本書では TTP ID を推測で書かない。** 割当は実装時に公式データから機械的に行う。

---

## 8. エラー処理と FDIR

| 層 | 異常 | 挙動 |
| --- | --- | --- |
| Channel | ビット誤り / 断 | フレーム破棄。**生バイトは必ず保存**（鑑識性を最優先） |
| 宇宙リンク | CRC 不一致 / 長さ不正 | 破棄 + カウンタ + PUS 5 イベント |
| 宇宙リンク | SDLS 認証失敗（Phase 2） | 破棄 + イベント + 連続失敗でパス遮断 |
| PUS | 未知の service/subtype | PUS 1 の否定応答 |
| 内部バス | CSP CRC32 不一致 | 破棄 + カウンタ |
| ノード | ハング | `watchdog`(STM32_IndependentWatchdog) によるリセット |
| EPS | 電圧低下 | セーフモード遷移（非必須負荷を落とす） |
| 地上局 | デコード不能 | 例外を握り潰さず、生フレームを DB に保存して継続 |

---

## 9. 検証戦略

| 層 | 手段 | 対象 |
| --- | --- | --- |
| **前提** | **`tools/renode-probe/probe.sh`** | **本書の Renode 能力主張すべて。CI の最初のジョブ** |
| ユニット | `pytest` + `hypothesis` | CCSDS/PUS/CSP/SDLS コーデックのラウンドトリップ、境界値 |
| 相互運用 | `pytest` | 自前 CSP 実装 ⇄ `libcsp`(MIT) のワイヤ互換 |
| ノード単体 | `renode-test` (Robot) | 各ノードが起動し HK を出す |
| 結合 | `renode-test` (Robot) | 地上局 TC → COMM → CAN → OBC → TM 応答の往復 |
| **演習** | `renode-test` (Robot) | **攻撃成功の assert + 対策適用時の失敗 assert（両方向）** |
| 性能 | `probe.sh` の MEAS 出力 | 4 ノード速度。0.15× を下回ったら CI 警告 |
| 供給網 | `pip-audit` / SBOM | 商用利用可否の継続確認 |

**テストハーネス自身の健全性**: `probe.sh` は初版で「出力ディレクトリが無いと全項目が PASS になる」
という欠陥があった（ログ欠損時に `grep` が不一致を返すため）。修正済みで、
**ログが存在しない probe は必ず FAIL** とする。テストが黙って成功することを許さない。

CI: GitHub Actions。Renode 1.16.1 portable をバージョン固定で取得。特権も GUI も不要。

---

## 10. リポジトリ構成

```
space-cs-sim/
├── platforms/cubesat/            # .repl : ノード別プラットフォーム記述
│   ├── node-common.repl          #   nucleo_h753zi + 共通ペリフェラル
│   ├── OBC.repl  COMM.repl  EPS.repl  ADCS.repl  ATTACKER.repl
├── scripts/                      # .resc（すべて logLevel 抑制込み）
│   ├── single-node/cubesat.resc
│   ├── multi-node/cubesat.resc
├── firmware/                     # Zephyr アプリ (west workspace)
│   ├── common/
│   │   ├── ccsds/  pus/  csp/  sdls/   # 脆弱版/対策版を Kconfig で切替
│   │   └── fdir/
│   └── apps/{obc,comm,eps,adcs,attacker}/
├── src/cuberange/                # Python パッケージ (Apache-2.0)
│   ├── proto/                    #   ccsds, pus, csp, sdls コーデック
│   ├── channel/                  #   遅延・誤り・パス窓・tap/inject
│   ├── physics/                  #   orbit(SGP4), attitude, power, thermal
│   ├── gs/                       #   地上局コア + TUI コンソール
│   ├── attack/                   #   攻撃者ツールキット
│   └── bridge/                   #   Renode Monitor クライアント, 仮想時刻同期
├── exercises/EX-*/               # 演習 5 点セット
├── tests/{robot,pytest}/
├── tools/
│   ├── renode-probe/probe.sh     # ★ 前提の検証ゲート
│   └── sparta_map.py
├── docker/                       # Renode 1.16.1 固定イメージ
├── docs/
└── Makefile                      # make probe / make demo / make test / make exercise EX=...
```

**言語方針**: 公開ドキュメントは英語を正とし、`docs/ja/` に日本語版を置く。
本設計書のみ日本語（意思決定の記録として）。

---

## 11. ライセンス方針

| 対象 | 方針 |
| --- | --- |
| 本プロジェクトのコード | **Apache-2.0** |
| Renode (**MIT**, LICENSE 実物で確認) | 依存 OK |
| Zephyr (Apache-2.0) | 依存 OK |
| libcsp (**MIT**, 確認済み) | テスト時のみ依存 OK |
| **NASA CryptoLib / NOS3 (NOSA 1.3**, LICENSE 実物で確認**)** | **コードを取り込まない。** 仕様・テストベクタの参照のみ |
| **Yamcs (AGPL-3.0**, 確認済み**) / OpenC3 COSMOS** | **コアに組み込まない。** 別パッケージの任意アダプタとしてのみ |
| gr-satellites (GPL-3.0) | Phase 3 で任意依存として検討 |

CI で SBOM を生成し、ライセンス逸脱を検出する。

---

## 12. 倫理・安全性

- シミュレータは完全に合成環境であり、実在の衛星・地上局・周波数・鍵を一切含まない。
- 演習は自己完結型で、外部ネットワークへの送信を行わない（CI でも egress を禁止）。
- 攻撃ツールは本シミュレータのプロトコル実装に対してのみ動作する。
- 各演習には必ず対策編を対にする。攻撃のみを教えない。

---

## 13. フェーズ計画

| Phase | 目標 | 完了条件 |
| --- | --- | --- |
| **P0 歩く骨格** | 2 ノード (COMM+OBC) で地上局→リンク→CAN→OBC→TM の往復が通る | `make probe` と `make demo` が通り、往復を assert する Robot テストが CI で緑 |
| **P1 最小有用** | 4 ノード、PUS 1/3/5/8/9/11/17、CSP over CAN、Physics、演習 4 本 | 4 演習すべてが「攻撃成功」を CI で assert |
| **P2 防御ラボ** | SDLS、MCUboot セキュアブート、FDIR 強化、検知演習、SPARTA マッピング、Yamcs/OpenC3 アダプタ | 各攻撃に対応する「対策適用で失敗する」テストが CI で緑 |
| **P3 拡張** | GNU Radio ベースバンド、実 SDR、実基板ブリングアップ、L552/ラドハード MCU ノード | 別途スコープ |

**本書は P0 と P1 を対象とする。**

---

## 14. 残存リスク

| # | リスク | 状態 |
| --- | --- | --- |
| R1 | Zephyr + FDCAN が Renode で動くか | **解消**。4/4 ノードで実測 PASS |
| R2 | Monitor TCP 経由のセンサ注入が実用速度か | **未検証**。`--port` の存在のみ確認。P0 でスパイク。破綻時は `external-control` API |
| R3 | SocketCAN 経路 | **条件付き成立**。root + Linux 必須のため任意機能に降格済み |
| R4 | 4 ノードの実行速度 | **測定済み** 0.19〜0.30×。仮想時間設計により受容 |
| R5 | 我々が書く Zephyr ファームが `CAN.MCAN` モデルの未実装ビットに当たる | **新規**。実測で `fdcan1: Unhandled write to offset 0x1C (NTSEG1/NTSEG2)` を観測済み。ビットタイミングは無視されるが通信は成立する。CAN-FD や高度な機能を使うと壊れる可能性 |
| R6 | Zephyr SDK の導入コスト（数 GB） | **未評価**。P0 で Docker 化して評価。代替は FreeRTOS + STM32 HAL |
| R7 | 演習の脆弱ファームが Zephyr の MPU/スタックガードで exploit 不能になる | **未評価**。脆弱版は保護機構を Kconfig で無効化する前提。P1 で確認 |

---

## 15. 未解決事項

| # | 事項 | 判断時期 |
| --- | --- | --- |
| Q1 | プロダクト名 `CubeRange` の商標・既存プロジェクト衝突確認 | 公開前 |
| Q2 | CCSDS 133.0-B / 232.1-B / 231.0-B の issue 番号確定 | 実装前 |
| Q3 | ECSS-E-ST-70-41C のサービス名称・番号の規格本文照合 | 実装前 |
| Q4 | 地上局 TUI に `textual` を使うか `rich` 単体で足りるか | P1 着手時 |
| Q5 | Physics を別プロセスにするか gs-core 内スレッドにするか | P1 着手時 |

---

## 16. 法廷記録 — 1 版で是正した誤り

design-court により、1 版の以下が**実行によって**反証された。

| # | 1 版の主張 | 反証 | 是正 |
| --- | --- | --- | --- |
| 1 | 「検証済みの技術前提」 | 環境に Renode が未導入だった（`which renode` → not found）。ソースを読んだだけだった | Renode 1.16.1 を導入し全項目を実行。`probe.sh` として再現可能化 |
| 2 | Renode に RAMN ボードモデルが同梱され実績がある | **リリース版 1.16.1 に `ramn.repl` / `ramn.resc` は存在しない**（master のみ） | RAMN モデルに依存しない設計へ。参考事例としてのみ言及 |
| 3 | ノード MCU は STM32L552（FDCAN あり） | **1.16.1 の `stm32l552.repl` に FDCAN が無い**。加えて Zephyr の `nucleo_l552ze_q` は CAN 非対応 | STM32H753 / `nucleo_h753zi` に変更。実 Zephyr ファームで実証 |
| 4 | センサは VEML7700 / Potentiometer / LSM6DSO_IMU | 3 件とも 1.16.1 で登録失敗（E04 × 2、E05 × 1） | 実測 PASS した 7 モデルに置換 |
| 5 | `live` モードは実時間追従 | 4 ノードで**実時間の 0.19〜0.30 倍**しか進まない | 仮想時間をマスタークロックとする設計へ全面変更。時間圧縮を導入 |
| 6 | SocketCAN / Wireshark を中核経路として記載 | `modprobe vcan` に root 必要、`LogCANTraffic` は Wireshark 必須でヘッドレス不可 | 任意機能へ降格。特権不要の ATTACKER ノード経路を既定に |

加えて、**ログ抑制が性能を約 12 倍左右する**という設計要件が実測から新たに判明した（§3.4）。

---

## 17. 参考

- Renode — https://github.com/renode/renode (MIT)
- Renode CAN + SocketCAN — https://antmicro.com/blog/2024/11/demonstrating-can-support-in-renode
- Renode `tests/peripherals/MCAN.robot`（Zephyr CAN 用ビルド済み ELF の入手元）
- RAMN — https://github.com/ToyotaInfoTech/RAMN
- NASA NOS3 — https://github.com/nasa/nos3 (NOSA 1.3)
- NASA CryptoLib — https://github.com/nasa/CryptoLib (NOSA 1.3)
- libcsp — https://github.com/libcsp/libcsp (MIT)
- Hack-A-Sat QEMU tooling — https://github.com/solar-wine/tools-for-hack-a-sat-2020
- Hack-A-Sat library — https://github.com/deptofdefense/hack-a-sat-library
- SPARTA — https://sparta.aerospace.org/ (STIX2 JSON 配布あり)
- CCSDS Blue Books — https://ccsds.org/
