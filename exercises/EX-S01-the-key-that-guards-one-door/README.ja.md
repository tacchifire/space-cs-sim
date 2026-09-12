---
id: EX-S01
title: 一つの扉だけを守る鍵
layer: space link
difficulty: intermediate
duration: 30-45 min
prerequisite: EX-X01
ttp:
  # 意図的に空。SPARTA と SPACE-SHIELD への対応付けは各 ID を公式 STIX エクスポートと突き合わせる
  # 作業であり、それを行う道具がこのリポジトリには無い。埋めれば引用の皮をかぶった推測になる。
  sparta: []
  space_shield: []
---

# EX-S01 — 一つの扉だけを守る鍵

## 状況

暗号が入った。

EX-X01 は「送信者を認証するには SDLS が要る、そしてこの range は実装していない」で終わった。
実装した——宇宙リンクに。COMM はアップリンクの全フレームについて CCSDS 355.0-B-2 の認証 MAC
（AES-256-GCM）を、**他の何かがそのフレームを読む前に**検証する。再送カウンタも、攻撃者が書く
ヘッダフィールドではなく**署名される範囲の内側**にある。

これは小さなことではない。効かないものを見る前に、効くところを見る価値がある。
コンソールは起動時と毎フレームでそう言う:

```
COMM: SDLS self-test passed (NIST AES-256-GCM vector)
COMM: authenticated frame, SPI 9 seq 1, 11 octets of payload
```

`make sdls` が 4 つを実測する——ターゲットが公表 NIST タグを計算すること、認証済みフレームが
実行されること、ペイロード 1 オクテットを反転して **FECF を修復した**フレームが拒否されること、
バイト同一の再送が拒否されること。**EX-L01 の攻撃は死んだ。** EX-G02 の偽装の半分も死んだ——
権限表が読む発信元 id が MAC で覆われたので、アップリンクでは他局の身分を名乗れない。
「どの局が何をしてよいか」を決める制御に、初めて「どの局であるか」の裏付けが付いた。

## 目的

衛星0の COMM 電源レールを落とす。

あなたはクロスリンクにいる。EX-X01 が置いていった場所そのままである。
コンステレーションの1機はあなたのもの。**鍵は持っていない。**

```bash
make exercise EX=EX-S01-the-key-that-guards-one-door          # range の中のシェルに入る
python3 exercises/EX-S01-the-key-that-guards-one-door/solve.py   # そのシェルのプロンプトで
```

`make exercise` は **range のネットワーク名前空間の中**のシェルに入る。ソルバはそこから走らせる。
名前空間にはループバックしか無く（SAFE_USE.md）、起動ごとに新しく作られるので、別の端末で
起動したソルバは `ConnectionRefusedError` を受け取る——宇宙リンクもインジェクタも外側には存在しない。

シェルではなく1コマンドだけ走らせるなら:

```bash
make exercise EX=EX-S01-the-key-that-guards-one-door RUN='python3 exercises/EX-S01-the-key-that-guards-one-door/solve.py'
```

## 何が起きたか

```
COMM: authenticated frame, SPI 9 seq 1, 11 octets of payload
COMM: uplink frame seq=0 carrying 11 octets -> OBC
OBC:  APID 0x0a9 PUS 17,1 from source 66
OBC:  PUS 17,2 report sent to COMM (counter 0)

OBC:  accepted a connection from 13 on port 10
OBC:  APID 0x0a9 PUS 8,1 from source 66
OBC:  PUS 8 executed - COMM rail OFF
EPS:  COMM rail OFF (commanded)
```

前半は運用者、認証済み。後半はあなた、クロスリンクから、EX-X01 と**同じコマンドと同じ2オクテット
の偽装**で。**何も変わっていない。**

## 結論すべきこと

SDLS は主張どおりのことをすべてやり、主張していないことは何もやらなかった。

これは**トランスファフレーム**のセキュリティプロトコルである。セキュリティヘッダは TC 主ヘッダと
ペイロードの間に座り、MAC はそのフレームを覆う。クロスリンクはトランスファフレームを運ばない——
CSP を運ぶ。機間バスが運ぶのはそれである——ので、SDLS が守るフレームは存在せず、パケットの中に
セキュリティヘッダを置く場所も無い。あなたのパケットは検査に落ちたのではない。
**検査に出会わなかった。**

コンソールを見ること。`accepted a connection from 13`——OBC は、パケットが実際に到着した CSP
アドレスを、その後 source 66 を名指しするのと同じログに書いている。**2つの数字はずっと線の上に
あり、片方は主張である。**

**持ち帰るべき失敗様態は技術的なものではない。** この配備のあと誰かが文書に
「アップリンクは認証されている」と書き、その文は**真**であり、そして
「宇宙機は認証された指令しか受け付けない」と読まれる——こちらは偽である。
制御の適用範囲は、それが座っている経路である。EX-X01 は「防御は資産にではなく経路に付いている」
と言った。これは同じ文をもう一度、**この range で最も強い制御**について言っている。
そしてそれが、見落としやすい版である。

そして EX-X01 の2つの教訓のうち、SDLS がどちらを直したかに注目すること。
権限表は送信者が**書く**ものを要求していた。MAC はそれを送信者が**所持していなければならない**
ものに変える——**アップリンクでは**。EPS のトークンは最初から所持ベースで、助けは要らなかった。
暗号は「どの制御が新しい経路を生き延びるか」を変えていない。**1つの制御が、1つの経路で、
どちらの分類に入るかを変えた**だけである。

対策と、その代価は `mitigation.ja.md` にある。
