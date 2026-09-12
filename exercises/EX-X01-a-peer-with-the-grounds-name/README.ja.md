---
id: EX-X01
title: 地上局を名乗る僚機
layer: crosslink
difficulty: intermediate
duration: 45-60 min
prerequisite: EX-G04
ttp:
  # 意図的に空。SPARTA と SPACE-SHIELD への対応付けは各 ID を公式 STIX エクスポートと突き合わせる
  # 作業であり、それを行う道具がこのリポジトリには無い。埋めれば引用の皮をかぶった推測になる。
  sparta: []
  space_shield: []
---

# EX-X01 — 地上局を名乗る僚機

## 状況

宇宙機が2機。そして今回は、互いに話せる。

ここまでの演習には入口がちょうど1つしか無かった。テレコマンドは宇宙リンクに到着し、その機の
COMM がデフレームし、OBC に渡される——その経路である。この range が築いた防御はすべてその上に
載っている。EX-L01 の再送検査、EX-G03 の仮想チャネル別シーケンス番号、EX-G02 の権限表、
EX-G04 の拒否報告。`make constellation` は宇宙機同士が**分離している**ことまで証明している——
`test_the_buses_are_isolated` が、衛星0のバス上のフレームは衛星1のバスに現れないと主張する。

互いに話せないコンステレーションは、コンステレーションではなく衛星4機である。
そこでこのシナリオは、それをコンステレーションにするリンクを足す。各 COMM の fdcan2 を、
1本の共有バスに繋ぐ。これは目的のある実在の機能である——洋上にいてどの地上局とも交信できない
宇宙機に、交信できる隣機を経由して届く。

起動して、動くところを見る:

```bash
make exercise EX=EX-X01-a-peer-with-the-grounds-name
```

`make exercise` は **range のネットワーク名前空間の中**のシェルに入る。ソルバはそこから走らせる。
名前空間にはループバックしか無く（SAFE_USE.md）、起動ごとに新しく作られるので、別の端末で
起動したソルバは `ConnectionRefusedError` を受け取る——宇宙リンクもインジェクタも外側には
存在しない。`make channel`・`make gs`・`make verify` もそのシェルから動き、コマンドは従来のままである。

シェルではなく1コマンドだけ走らせるなら:

```bash
make exercise EX=EX-X01-a-peer-with-the-grounds-name RUN='python3 exercises/EX-X01-a-peer-with-the-grounds-name/solve.py'
```

起動から数秒で、両方の COMM コンソールがそう言う:

```
CUBERANGE: COMM (addr 5) booting
CUBERANGE: COMM crosslink up on can@4000a400, /2 local, default out
COMM: crosslink reached COMM 13 in 1 ms
```

衛星0の COMM はアドレス5、衛星1の COMM は13。別々の内部 CAN バス、別々のマシンに載っており、
その間を CSP パケットが渡った。

これが**機能**である。

## 目的

衛星0の COMM 電源レールを落とす。あなたはクロスリンク上にいて、それ以外のどこにもいない。

前提は、コンステレーションの1機が既にあなたのものだということ。どうやってそこに至ったかは
別の演習の主題であり（EX-F01 は OBC 上でのコード実行で終わる）、この演習はその**後**から始まる。
range が与えるのはクロスリンクバス上のインジェクタで、それが侵害された宇宙機の無線に相当する。

すべて対策済みである。EPS には EX-B01 のトークン検査。OBC には EX-F01 の長さ検査、EX-G02 の
権限表、EX-G04 の検証報告——すべて **ON**。

## 試す順序

それぞれ実行し、指定されたコンソールを読む。次節を読む前に、どれが通ったかを書き留めること。

1. **直接経路。** 衛星0の EPS にレールを落とせと告げる——EX-B01 のコマンドを、ここから送る。

   ```bash
   python3 exercises/EX-X01-a-peer-with-the-grounds-name/solve.py --direct
   ```

2. **OBC に、正直に。** 同じ目的を OBC 経由で。自分の身分を使う——PUS 8 機能1、発信元 id
   `0x0AA`、すなわち衛星1。

   ```bash
   python3 exercises/EX-X01-a-peer-with-the-grounds-name/solve.py --honest
   ```

3. **OBC に、他人として。** 同じコマンドで、2オクテットだけ変える。

   ```bash
   python3 exercises/EX-X01-a-peer-with-the-grounds-name/solve.py
   ```

## 何が起きたか

```
1.  EPS: REJECTED unauthenticated rail command from node 13
2.  OBC: REJECTED PUS 8 function 1 from source 170 - not authorised
3.  OBC: PUS 8 executed - COMM rail OFF
    EPS: COMM rail OFF (commanded)
```

## 結論すべきこと

**直接経路は失敗した。その理由は正確に述べる価値がある。** EPS は送信者が**所持していなければ
ならない**ものを要求する。共有トークンは貧弱な秘密であり——その対策文書自身がそう書き、再送可能で
あり OBC のフラッシュを読める攻撃者には耐えないと書いている——それでも貧弱な秘密は「持っているか
いないか」のどちらかであり、新しい道を通って来ても手には入らない。

**正直な経路も失敗した。** 権限表に衛星1の項目は無く、`source_may_perform` は知らない発信元に
false を返す。フェイルクローズであり、それは正しい。そして、だからこそ3番目は2オクテット先に
ある。

**3番目が通った。** 権限表は送信者が**書き込む**ものを要求する。TC 副ヘッダの発信元 id は
フィールドであり、あなたはそこに `0x0042` を書いた。パケットの中にそれを照合できるものは無い。
EX-G02 は「どの局が何をしてよいか」を決める制御を作り、その点については正しい。「どの局であるか」
を決めることは最初からできておらず、**2つ目の入口ができるまで誰もそれを声に出さなかった**。

**そしてもう1つ、こちらの方が大きい。** この range が築いたリンク層の防御は、1つも経路上に
無かった。すべて COMM ファームウェアの `on_tc_frame` の中にあり、`on_tc_frame` は宇宙リンクから
到着したバイト列に対して走る。あなたのパケットはその近くを通っていない。**防御は資産にではなく
経路に付いている。** 演習4本分のアップリンク対策が守るのはアップリンクである。

**そして冒頭の中継は、攻撃と別物ではない。** クロスリンクは自機宛でないアドレスのパケットを
転送する——それが機能そのものであり、冒頭の ping を運んだのもそれである。外側のバスと内側の
バスの間で経路制御するということは、内部ノードすべてを外部から到達可能にするということである。
運用者は他では届かない宇宙機を手に入れた。あなたも手に入れた。

対策と、それが解決しないことは `mitigation.ja.md` にある。
