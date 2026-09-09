# EX-B01 の対策 — 電源指令を認証する

*English: [mitigation.md](mitigation.md)*

## 変更点

ビルドフラグ1個の背後に、検査を1つ。

```c
#if CUBERANGE_EPS_REQUIRE_AUTH
	if (memcmp(&data[3], POWER_TOKEN, sizeof(POWER_TOKEN)) != 0) {
		printk("EPS: REJECTED unauthenticated rail command from node %u\n", src);
		return;
	}
#endif
```

両方をビルドする。

```bash
west build -b nucleo_h753zi -d build-eps-vuln firmware/apps/eps -- -DCUBERANGE_EPS_REQUIRE_AUTH=0
west build -b nucleo_h753zi -d build-eps-hard firmware/apps/eps -- -DCUBERANGE_EPS_REQUIRE_AUTH=1
```

## 他が同一である理由

「脆弱性」がプラットフォームの防御を切ることから来る演習は、真でないことを教える。

2つの EPS イメージはボード、`prj.conf`、ソース、コンパイラフラグを共有する。
以前はこれを手で確かめるよう求めていた。
今は `tools/config_diff_gate.py` が機械的に証明し、`make check` の一部になっている。

```bash
python3 tools/config_diff_gate.py
# EX-B01: 836 Kconfig symbols identical
# EX-B01: only CUBERANGE_EPS_REQUIRE_AUTH differs (0 -> 1)
# EX-B01: ELFs differ
# EX-B01: 2 translation units compiled identically apart from -DCUBERANGE_EPS_REQUIRE_AUTH
```

変わるのは検査1つだけである。
攻撃は普通に構成された衛星に対して通り、対策はアプリケーションコードの1行であって、コンパイラフラグではない。

## これが解決しないこと

はっきり書く。
売り込みすぎた対策は、対策が無いより悪い。

- **リプレイできる。** トークンは固定文字列である。有効な指令を一度でも見た攻撃者は、それを永久に繰り返せる。実機設計にはカウンタかチャレンジが要る。それが EX-L01 の主題である。

- **コード実行を生き延びない。** トークンは `static const` で `.rodata`、つまりフラッシュにあり、そのノード上で動く何からでも読める。EX-F02 を参照。

- **送信元を認証しない。** 送信者が秘密を知っていたことは証明するが、それが OBC であることは証明しない。電源を正当に指令するノードはすべて同じ秘密を持つ必要があるので、どれか1つを侵害すれば足りる。

- **1つのサービスしか守らない。** このバス上の他のすべての認証されない指令は、いまも開いている。

正直に言えば、これはコストを「線にフレームを流す」から「まず秘密を手に入れる」に引き上げる。
実在する改善であり、小さい。

## 実機設計なら何をするか

単調増加カウンタを伴う指令ごとの認証。
共有ではなくノードごとに配備する鍵。
そして重要なレールのロードスイッチは、バス指令1つでは解除できないインヒビットの背後に置く。

宇宙リンクは CCSDS SDLS（355.0-B-2）が扱う。
内部バスには独自の答えが要る。
CSP はそれを提供しない。
