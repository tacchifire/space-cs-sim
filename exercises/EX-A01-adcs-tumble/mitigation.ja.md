# EX-A01 の対策 — アクチュエータにできることで指令を縛る

*English: [mitigation.md](mitigation.md)*

## 変更点

ビルドフラグ1個の背後に、検査を1つ。

```c
#if CUBERANGE_ADCS_TORQUE_LIMIT
	if (torque > TORQUE_AUTHORITY_MNM || torque < -TORQUE_AUTHORITY_MNM) {
		printk("ADCS: REJECTED out-of-authority torque %d mNm from node %u (limit +-%d)\n",
		       torque, src, TORQUE_AUTHORITY_MNM);
		return;
	}
#endif
```

両方をビルドする。

```bash
west build -b nucleo_h753zi -d build-adcs-vuln firmware/apps/adcs -- -DCUBERANGE_ADCS_TORQUE_LIMIT=0
west build -b nucleo_h753zi -d build-adcs-hard firmware/apps/adcs -- -DCUBERANGE_ADCS_TORQUE_LIMIT=1
```

## クランプではなく拒否

分かりやすい代案は、値を権限の上限に丸めて処理を続けることである。
そうしてはいけない。

クランプは、運用者が出していない指令を実行することになる。
異常調査が始まったとき、ログは ADCS が 20 mNm のスルーを実施したと言い、運用者は 30000 を指令したと確信している。
その食い違いが1日を持っていく。

拒否すれば、問題の値を含んだイベントが残る。
それが、混乱した異常と診断可能な異常の違いである。

一般化するとこうなる。
物理的な包絡の外にある値は、丸めるべき値ではない。
それを送った側が宇宙機について間違っていたという証拠である。

## 他が同一である理由

2つの ADCS イメージは、ボード、`prj.conf`、ソース、コンパイラオプションを共有する。
EX-B01 と違い、`diff` を自分で走らせる必要はない。
`tools/config_diff_gate.py` が宣言済みの全ペアについて実行し、`make check` の一部になっている。

```bash
python3 tools/config_diff_gate.py
# EX-A01: 836 Kconfig symbols identical
# EX-A01: only CUBERANGE_ADCS_TORQUE_LIMIT differs (0 -> 1)
# EX-A01: ELFs differ
# EX-A01: 2 translation units compiled identically apart from -DCUBERANGE_ADCS_TORQUE_LIMIT
```

最後の行が、EX-B01 の手動 `diff` では得られなかったものである。
Kconfig はコンパイラオプションについて何も語らない。
脆弱側だけ防御を無効にしてビルドしたペアでも、`.config` の比較は完璧に空になりうる。

## これが解決しないこと

はっきり書く。
売り込みすぎた対策は、対策が無いより悪い。

- **1つの指令の1つのフィールドを縛るだけである。** このバス上の他のすべての指令は、ワイヤ形式が許すものを何でも受け付ける。バグは「トルクの検査が抜けていた」ことではない。「指令の包絡をハードウェアから導いた者がいなかった」ことであり、この1行を直してもそれは残る。

- **大きさは縛るが、蓄積は縛らない。** 権限内の指令を20回続ければ、やはり衛星は回る。レート制限は無く、総角運動量を追う仕組みも無い。辛抱強い攻撃者、あるいは符号を間違えた制御ループは、同じ結果をもっとゆっくり得る。

- **試みを検知しない。** 拒否はコンソールに出る。イベントは地上に届かず、ハウスキーピングのカウンタも増えない。誰かがバスを探っていることを運用者は知らない。検知の演習が要るが、この演習環境にはまだ無い。

- **何も認証しない。** この検査は誰が送ったかを気にしないし、気にすべきでもない。ただしそれは、侵害された OBC なら包絡の内側で衛星をどこへでも向けられるということでもある。指向要求を壊すには十分すぎる範囲である。

正直に言えば、これは**ありえない**指令を閉じ、**ありうる**濫用をすべて開いたままにする。

## 実機設計なら何をするか

指令の包絡をハードウェアのデータシートから導き、飛行ソフトウェアと地上のチェッカの両方を1つの源から生成する。
そうすれば2つが食い違えなくなる。

瞬時値の上限だけでなく、予算を持った角運動量の勘定を置く。
拒否はコンソール行ではなくテレメトリのイベントを上げる。

そして姿勢指令はモードマネージャを通す。
現在のモードでそのスルーが許されるかを知っている層である。
実際の ADCS 事故の多くは、個別には合法で、状況に対して誤っていた指令から起きる。
