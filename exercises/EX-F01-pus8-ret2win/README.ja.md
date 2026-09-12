---
id: EX-F01
title: 地上が行けない場所へ復帰させる
layer: firmware
difficulty: intermediate
duration: 60-90 min
prerequisite: EX-L01
ttp:
  # 意図的に空にしてある。SPARTA と SPACE-SHIELD への対応付けは、公式の STIX エクスポートに対して
  # ID の実在を確認する作業であり、それをするツールはここに無い。埋めれば、引用の体裁をした推測に
  # なる。何かが検証するようになるまで空のままにする。
  sparta: []
  space_shield: []
---

# EX-F01 — 地上が行けない場所へ復帰させる

*English: [README.md](README.md)*

## 状況

あなたは宇宙リンク上にいる。
EX-L01 と同じ位置である。
正しい空を向いた受信機と、送信機を持っている。

今回はリプレイではない。
自分で組んだテレコマンドを送る。
OBC はそれを受理する。
長さ以外に、何も問題が無いからである。

OBC の PUS 8 ハンドラは、引数ブロックを解析する前に、スタック上の16オクテットのバッファへコピーする。
コピーする長さはパケットから来る。

## 目的

OBC に `maintenance_inhibit_fdir` を実行させる。
どのテレコマンドからも到達できない関数である。
成功条件は、OBC のコンソールに `OBC: FDIR INHIBITED by maintenance handler` が出ること。
そして `gpioPortD` のピン7が上がること。

## 動いているもの

```
地上局 --TCP--> COMM.usart2 --CSP/CAN--> OBC --> EPS
   ここにいる                            標的
```

今回は攻撃者マシンも CAN インジェクタも無い。
送るものはすべて、地上局が送りえたテレコマンドであり、運用者と同じリンクを通って COMM から入る。
EX-B01 と EX-A01 はバスへの到達を必要とする。
これは無線があればよい。

## 実行

```bash
make firmware-f01
make exercise EX=EX-F01-pus8-ret2win                       # シナリオを起動したままにする
python3 exercises/EX-F01-pus8-ret2win/solve.py --show      # 送らずにペイロードだけ表示する
python3 exercises/EX-F01-pus8-ret2win/solve.py             # 模範解答
make verify EX=EX-F01-pus8-ret2win
```

`make exercise` は **range のネットワーク名前空間の中**のシェルに入る。ソルバはそこから走らせる。
名前空間にはループバックしか無く（SAFE_USE.md）、起動ごとに新しく作られるので、別の端末で
起動したソルバは `ConnectionRefusedError` を受け取る——宇宙リンクもインジェクタも外側には
存在しない。`make channel`・`make gs`・`make verify` もそのシェルから動き、コマンドは従来のままである。

シェルではなく1コマンドだけ走らせるなら:

```bash
make exercise EX=EX-F01-pus8-ret2win RUN='python3 exercises/EX-F01-pus8-ret2win/solve.py'
```

## 始める前に：何が効かないか

シェルコードを書かないこと。
難しいのではない。
そもそも実行できない。
しかもそれはエミュレータの性質ではなく、この部品の性質である。

このボードでの Zephyr の既定値は、SRAM を execute-never ビットの立った MPU 領域に置く。
Renode はそれを強制する。
`probe.sh` の section G が証拠である。
`TranslateAddress 0x24003000 InstructionFetch` は拒否され、フラッシュへの同じ問い合わせは成功する。
バッファにペイロードを置いて飛べば、CPU は `CFSR=0x00000001` の MemManage フォールトを取る。
シリコンがするのと同じである。

だからペイロードは、すでに image の中にあるアドレスになる。
演習のための簡略化ではない。
この種の部品での実際の悪用がとる形である。

作業中に知っておく価値のあること。
Renode 1.16.1 の `TranslateAddress` はアクセス種別**ではなく**アドレスでキャッシュする。
SRAM のあるアドレスについて読み出しを問い合わせてから同じアドレスの命令フェッチを問い合わせると、両方が成功を返す。
その順序で execute-never を確認すると、シェルコードが動くという結論になる。
フェッチを先に問うか、新しいプロセスで問うこと。
これは D22 で、プローブに固定してある。

## ヒント

<details><summary>ヒント1 — 長さはどこから来るか</summary>

`firmware/apps/obc/src/main.c` の `handle_function` を読む。
効いてくる行は2つ。
`arg_len` を計算する行と、コピーする行である。
そのうえで、フラグを落としたビルドで `arg_len` を縛っているものは何かを考える。
</details>

<details><summary>ヒント2 — どこまで書くか</summary>

バッファ先頭から保存された復帰アドレスまでのオフセットが要る。
試行錯誤ではなく、関数のプロローグから読む。

```bash
arm-zephyr-eabi-objdump -d "$(make -s out)"/build-obc/zephyr/zephyr.elf \
  | sed -n '/<handle_function>:/,+8p'
```

命令3つで全部わかる。
何が push されるか、その下にどれだけスタックが確保されるか、`memcpy` の宛先がどこか。
</details>

<details><summary>ヒント3 — アドレスと、その1ビット</summary>

```bash
arm-zephyr-eabi-nm "$(make -s out)"/build-obc/zephyr/zephyr.elf | grep maintenance
```

ビット0を立てること。
Cortex-M では偶数アドレスへの分岐は、ARM 命令を実行しろという要求である。
この部品に ARM モードは無いので、目当ての関数ではなく UsageFault が返ってくる。
`nm` はこのビットを伏せて表示する。
シンボルテーブルでは立っている。
</details>

## 何を結論すべきか

**そのパケットは正当だった。**
サービス8は存在し、機能1も存在し、フレームのチェックサムは正しく、シーケンス番号も順序どおりで、しかも OBC は行くべきでない場所へ行く前にその機能を実行した。
上流に落とす理由を持つものは何も無かった。
テレコマンドの認証機構、つまり EX-B01 の対策も EX-L01 の対策も、これを通していただろう。
権限のある運用者が長さを間違えれば、まったく同じことが起きるからである。

**弾薬はデッドコードだった。**
`maintenance_inhibit_fdir` は機能表の中で enable ビットを落として置かれており、ディスパッチャはそれを忠実に守る。
認可の検査は壊れていない。
あなたが通った経路の上に無かっただけである。
ハンドラを削るほうが、ガードするより良い修正になる。
飛行イメージが必ず抱えている保守用の入口について、これは居心地の悪い教訓である。

**フラッシュは固定で既知である。**
XIP、ASLR 無し、そして読める image の中のシンボルテーブル。
この種の部品では、何かのアドレスは調べれば分かる。
「攻撃者には在り処が分からない」は、ただでは手に入らない防御である。

Willbold らは実在の衛星ファームウェアでこの組み合わせを報告している（*Space Odyssey*, IEEE S&P 2023）。
バッファの寸法決めにパケットの長さフィールドを信頼すること、そしてその結果を捕まえるスタック保護が無いことである。

## そして直す

[mitigation.ja.md](mitigation.ja.md) を見てほしい。
検証は5つを assert する。
いつもの3つに、演習が自分自身を証明してしまわないための2つが加わる。

| テスト | 何を assert するか |
| --- | --- |
| `test_the_target_is_in_the_image_and_unreachable_by_command` | win 関数がリンカを生き延び、フラッシュにあり、表で無効化されている |
| `test_attack_succeeds_against_the_vulnerable_obc` | 機能が実行され、**そのうえで**制御がハンドラへ移った |
| `test_mitigation_rejects_the_over_long_argument_block` | OBC が拒否を記録し、指示器が上がらない |
| `test_the_mitigation_does_not_break_legitimate_pus8` | 寸法の合う PUS 8,1 は今もレールを切り替える |
| `test_the_measured_offset_still_describes_the_build` | 復帰アドレスのオフセットが古びていない |

2番目は乗っ取りだけでなく `PUS 8 executed` も assert する。
これが無いと、win 関数へ誤って流れ込んだパケットと、実行されたうえでそこへ復帰したパケットが同じに見える。
制御フローの乗っ取りは後者だけである。
