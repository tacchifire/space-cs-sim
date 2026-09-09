# EX-F01 の対策 — コピーの寸法をパケットではなくバッファから取る

*English: [mitigation.md](mitigation.md)*

## 変更点

ビルドフラグ1個の背後に、検査を1つ。

```c
#if CUBERANGE_OBC_PUS8_LENGTH_CHECK
	if (arg_len > sizeof(args)) {
		printk("OBC: REJECTED PUS 8 argument block of %u octets (buffer is %u)\n",
		       (unsigned int)arg_len, (unsigned int)sizeof(args));
		return;
	}
#endif
	memcpy(args, &app_data[2], arg_len);
```

両方をビルドする。

```bash
west build -b nucleo_h753zi -d build-obc      firmware/apps/obc -- -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=0
west build -b nucleo_h753zi -d build-obc-hard firmware/apps/obc -- -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1
```

リテラルの16ではなく `sizeof(args)` を使う。
誰かがバッファの寸法を変えた瞬間に両者はずれる。
バッファから寸法を読む書き方だけが、そのあとも正しく動く。

## 他が同一である理由

反・藁人形の証明が最も効くのはこの演習である。
スタックオーバーフローの実演には「プラットフォームの防御を切ったのだろう」という疑いが必ず付く。

そうではない。
`tools/config_diff_gate.py` が、信じてくれと頼む代わりに機械的にそう言う。

```
EX-F01: 836 Kconfig symbols identical
EX-F01: only CUBERANGE_OBC_PUS8_LENGTH_CHECK differs (0 -> 1)
EX-F01: ELFs differ
EX-F01: 2 translation units compiled identically apart from -DCUBERANGE_OBC_PUS8_LENGTH_CHECK
```

最後の行は `.config` の diff では得られない。
Kconfig はコンパイラオプションについて何も語らないので、脆弱側だけコマンドラインで防御を外したペアでも、Kconfig の比較は通ってしまう。

両方のビルドで **ON** であり、それでもこれを止めなかった防御。
`ARM_MPU`、`HW_STACK_PROTECTION`、`MPU_STACK_GUARD`、`SRAM_REGION_PERMISSIONS`、`XIP`。

両方で **OFF** であり、それがこのボードでの Zephyr の既定値であるもの。
`STACK_CANARIES`、`STACK_SENTINEL`、`USERSPACE`、`STACK_POINTER_RANDOM=0`。

MPU のスタックガードには一文割く価値がある。
名前からは、これが捕まえるべきだったように見えるからである。
実際には、スレッドのスタックの**下**に置かれる領域であり、下方向に伸びすぎたスタックを捕まえる。
現在のフレームの中を上へ走るコピーは、そこに到達しない。
Zephyr 自身の文書も、ガードはオーバーフローを検出はするがデータ破壊は防がないと述べている。
この区別が具体的にどう見えるかが、これである。

## これが解決しないこと

はっきり書く。
売り込みすぎた対策は、対策が無いより悪い。

- **1つのコピーを直すだけである。** このファームウェアでワイヤから来る他のすべての長さは、いまも信頼されている。バグは「この memcpy に境界が無かった」ことではない。境界の無い memcpy をビルドが可視化しないことであり、この1行を直してもそれは残る。

- **フレームを安全にはしない。** カナリアは無いままなので、他のどのハンドラにある次の無境界書き込みも、同じ保存済み復帰アドレスに同じ容易さで届く。`STACK_CANARIES` を有効にすれば、1件ではなくこの分類全体のコストが上がる。代償は全関数の復帰時の検査であり、これは予算の判断であって、ただで手に入る勝ちではない。

- **保守ハンドラを image に残したままである。** `maintenance_inhibit_fdir` はいまも既知のアドレスにあり、enable ビットが落ちているだけである。認可の検査は働いている。攻撃者が通る経路の上に無いだけである。特権を持つデッドコードは、ガードするより取り除くほうが良い答えであり、この対策はそれをしていない。

- **検知について何も言わない。** 拒否はコンソールに出る。PUS 5 のイベントは地上に届かないので、過大なパケットでコマンドハンドラを探っている者がいても運用者は知らない。それはまさに、この攻撃の前段に来る偵察である。

正直に言えば、これは1つのオーバーフローを閉じ、それを悪用可能にしていた条件をそのまま残す。

## 実機設計なら何をするか

コマンドの定義、つまりサービス、サブタイプ、機能、引数の配置とその長さを、機械可読な1つの源から生成する。
飛行側のパーサと地上側のチェッカを両方そこから生成すれば、長さが誤りうる場所は1つになり、両端が気付く。

アプリケーション層が縛らなければならないものは、フレーミング層で拒否する。
スタックカナリアを有効にして、そのサイクルを払う。
そして保守用の入口は、無効化ではなく飛行イメージから削る。
無効化された関数も、ガジェットではあり続けるからである。

CCSDS SDLS（355.0-B-2）はリンクを認証するが、ここでは助けにならなかっただろう。
パケットは書式として正しく、署名も通っていたはずである。
この演習を EX-L01 の後に置いた理由がそこにある。
