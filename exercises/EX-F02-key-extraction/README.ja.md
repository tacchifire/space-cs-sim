---
id: EX-F02
title: 鍵のもう一つの写し
layer: firmware
difficulty: intermediate
duration: 45-60 min
prerequisite: EX-F01
ttp:
  # 意図的に空にしてある。SPARTA と SPACE-SHIELD への対応付けは、公式の STIX エクスポートに対して
  # ID の実在を確認する作業であり、それをするツールはここに無い。埋めれば、引用の体裁をした推測に
  # なる。何かが検証するようになるまで空のままにする。
  sparta: []
  space_shield: []
---

# EX-F02 — 鍵のもう一つの写し

*English: [README.md](README.md)*

## 状況

あなたはまた宇宙リンク上にいる。
EX-F01 が残した位置である。
正しい空を向いた受信機と、送信機を持っている。

今回は何も溢れない。
この OBC は EX-F01 の長さ検査を ON にしてビルドされている。
送るのは、OBC が意図して実装しているテレコマンドである。
PUS 6,5、メモリダンプ。
メモリを読んで地上へ送る。
それがこのサービスの役目だからである。

OBC は自分の報告に署名する。
EX-D01 の修正である。
署名するには鍵が要る。
`cr_sdls_key`、EX-S01 以来ずっと宇宙リンクを認証してきた32オクテットである。
鍵は `firmware/common/cuberange_keys.h` の `static const` であり、`static const` は `.rodata` に置かれる。
この部品ではフラッシュであり、そのアドレスは image 自身のシンボルテーブルに書いてある。

## 目的

宇宙リンク越しに、鍵を持たずに、OBC から SDLS 鍵を読み出す。

成功条件は、ヘッダにある文字列に見える32オクテットではない。
この射場の鍵は意図的に見分けやすくしてあるので、一致しても何も証明しない。
成功条件は、宇宙機が署名した報告のトレーラを検証できる32オクテットである。

## 動いているもの

```
地上局 --TCP--> COMM.usart2 --CSP/CAN--> OBC --> EPS
   ここにいる                            標的
```

EX-F01 と同じ配置、同じ位置である。
攻撃者マシンも CAN インジェクタも無い。
OBC は別のビルドである。
報告に署名する——それが鍵をここに置く。
そしてテレコマンドを一切検証しないので、ダンプ要求には攻撃者が持っていないものは何も要らない。

## 実行

```bash
make firmware-f02
make exercise EX=EX-F02-key-extraction                       # シナリオを起動したままにする
python3 exercises/EX-F02-key-extraction/solve.py --show      # 送らずに要求だけ表示する
python3 exercises/EX-F02-key-extraction/solve.py             # 模範解答
make verify EX=EX-F02-key-extraction
```

`make exercise` は **range のネットワーク名前空間の中**のシェルに入る。ソルバはそこから走らせる。
名前空間にはループバックしか無く（SAFE_USE.md）、起動ごとに新しく作られるので、別の端末で
起動したソルバは `ConnectionRefusedError` を受け取る——宇宙リンクは外側には存在しない。
`make channel`・`make gs`・`make verify` もそのシェルから動く。

シェルではなく1コマンドだけ走らせるなら:

```bash
make exercise EX=EX-F02-key-extraction RUN='python3 exercises/EX-F02-key-extraction/solve.py'
```

## 始める前に：なぜ鍵がこの計算機にあるのか

この OBC に固有の間違いではない。
`static const` はそれを使うすべての image にコンパイルされる。
だから鍵で署名か検証をする image は、どれも写しを持つ。
COMM はリンクの検証に使い（EX-S01）、テレコマンドを検証する OBC も持ち（EX-S02）、報告に署名する OBC も持つ（EX-D01）。
無線機の写しは無線機の背後にある。
OBC の写しは、OBC が実装するすべてのサービスの背後にある。
そしてその1つはメモリを読む。

EX-F01 のオーバーフローに手を伸ばさないこと。
ここでは両方のビルドで塞がれている。
すでに動いているコードなら同じように鍵を読める——`mitigation.ja.md` ははっきりそう書いている。
だがこの演習は、訊かれれば鍵を差し出す宇宙機についてのものである。

## ヒント

<details><summary>ヒント1 — 鍵はどこにあるか</summary>

```bash
arm-zephyr-eabi-nm -S "$(make -s out)"/build-obc-f02-vuln/zephyr/zephyr.elf | grep cr_sdls_key
```

`-S` はアドレスに加えてサイズも表示する。
アドレスを見ること。
この部品では 0x08000000 から上がフラッシュである。
ビルドのどこにもそれを隠すものは無く、ランダム化するものも無い。
</details>

<details><summary>ヒント2 — どう頼むか</summary>

`firmware/apps/obc/src/main.c` の `handle_memory_dump` を読む。
アプリケーションデータは7オクテット、ビッグエンディアンである。
メモリ ID、開始アドレス、長さ。
メモリ ID 0 はアドレス空間全体で、実装されているのはそれだけである。
1つの報告が運べるのは最大128オクテット、鍵4本分である。
`GroundStation.send_pus` が送ってくれる。
</details>

<details><summary>ヒント3 — 頼んでいない20オクテット</summary>

TM 6,6 は7オクテットを繰り返し、ダンプを運び、そのあとにさらに20オクテットを付ける。
PUS 認証トレーラ、つまりシーケンス番号と MAC である。
この OBC は署名するからである。
Space Packet の長さフィールドはそれを覆うので、署名を検査しないパーサにはアプリケーションデータの末尾に見える。
ダンプがどこで終わるかは、ダンプ自身の長さが教えてくれる。
`cuberange.proto.pus_auth.verify(packet, key=...)` がトレーラを検査する。
いま読み出したオクテットを鍵として試すこと。
</details>

## 何を結論すべきか

**サービスは正しく動いた。**
書式の誤りも、オーバーフローも、迂回された検査も無い。
PUS 6 がメモリを読んだのは、メモリを読むことがその仕事だからである。
そして宇宙機は、送ったものを認証していた——送っているまさにその鍵で。

**鍵は1つ、image は複数。**
宇宙リンクを守る鍵は無線機の中だけにあるのではない。
それで署名か検証をする image はどれも写しを持ち、それは image が存在する限りフラッシュにあり、そのアドレスはシンボルテーブルに書いてある。
EX-S01 はそれを1つの扉に付けた。
鍵は、それを持つすべてのノードのすべての扉の背後にある。
EX-B01 の電源トークンも同じ形で置かれている——`POWER_TOKEN` も OBC のソースでは `static const` であり、EX-B01 の対策文書はここを指している。

**所持が証明のすべてである。**
SDLS（EX-S01）、テレコマンドのトレーラ（EX-S02）、報告のトレーラ（EX-D01）は、どれもこの32オクテットの所持を証明し、それ以外は何も証明しない。
読み出されてしまえば、それらの制御はどれも、読んだ者を運用者として扱う。
EX-U02 は「誰かがあなたの鍵を持っている」から始まった。
これは、誰かがそれを手に入れる方法の1つである。

**ハードウェアでも隠せなかった。**
設計書（6.5 節）は、STM32H7 の CRYP は金庫ではなくアクセラレータだと記録している。
鍵は結局ファームウェアが管理するメモリから来る。
そして Renode のこの部品のモデルは、鍵レジスタをそのまま読み返す。

Willbold らは、解析した実在の衛星ファームウェアに危険な保守プリミティブがあったと報告している（*Space Odyssey*, IEEE S&P 2023）。
直接のメモリ操作はその1つである。

## そして直す

[mitigation.ja.md](mitigation.ja.md) を見てほしい。
検証は5つを assert する。
いつもの3つに、演習が自分自身を証明してしまわないための2つが加わる。

| テスト | 何を assert するか |
| --- | --- |
| `test_the_key_is_in_both_images_in_flash` | 前提を ELF だけから確かめる。両方の半分が鍵をフラッシュに持ち、それがこの射場の鍵である |
| `test_the_dump_reads_the_key_and_the_key_verifies_the_spacecrafts_reports` | 脆弱な OBC が鍵を返し、そのオクテットが同じ実行の署名済み報告を検証し、1ビット違う鍵は検証しない |
| `test_the_mitigated_obc_refuses_to_dump_the_key` | OBC が拒否を記録し、ダンプ報告は届かず、宇宙機は今も応答する |
| `test_the_mitigation_does_not_break_the_dump_service` | 公開ブロックのダンプは返され、マジックで始まり、署名されている |
| `test_a_dump_that_starts_in_the_block_and_runs_past_it_is_refused` | 開始アドレスだけを見る検査なら通してしまう要求 |

2番目は鍵を**使って**確かめる。
5番目があるのは、ダンプの開始アドレスだけを見る検査が、4番目の要求を正しい検査とまったく同じように通すからである。

**まだ走らせていない。**
この演習は、Zephyr のツールチェーンも Renode も無いマシンで書かれた。
`make verify EX=EX-F02-key-extraction` がどこかで通るまで、上の表はテストが何を確かめるかを言っているのであって、それが成り立つことを言っているのではない。
