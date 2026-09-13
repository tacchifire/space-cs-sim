---
id: EX-S02
title: 道ではなく要求を認証する
layer: application
difficulty: advanced
duration: 45-60 min
prerequisite: EX-S01
ttp:
  # 意図的に空。SPARTA と SPACE-SHIELD への対応付けは各 ID を公式 STIX エクスポートと突き合わせる
  # 作業であり、それを行う道具がこのリポジトリには無い。埋めれば引用の皮をかぶった推測になる。
  sparta: []
  space_shield: []
---

# EX-S02 — 道ではなく要求を認証する

## 状況

EX-S01 の対策文書は、示した穴を塞ぐ道を3つ挙げ、3番目が実際に答えるものだと書いた:

> アプリケーション層で認証する。PUS パケット自身が MAC を持つ。すると**どのリンクから来たかが
> 問題にならなくなる**——上の2つが持っていない性質である。

これがそれである。テレコマンドは Space Packet の内側にトレーラを持ち、
パケット自身の長さフィールドがそれを覆う:

```
primary(6) | PUS TC secondary(5) | application data | SEQ(4) | MAC(16)
```

MAC までの全体を AES-256-GCM で覆う——**発信元 id とシーケンス番号を含めて**。
OBC はディスパッチの前に検証し、その下の何もそれを知らない——パーサが見るのは
**署名されたオクテットそのもの**である。

**このトレーラはミッション定義であり、外部オラクルを持たない。** ECSS-E-ST-70-41C は TC パケットに
認証フィールドを定義しておらず、CCSDS はセキュリティをトランスファフレーム層に置く——
そしてそれが、これが依存をやめるために存在する層である。`tests/golden/pus_auth.json` は
`oracles` の欄にそう書いてある。似て見えるだけの何かを並べるのではなく。
外部から検査されるのはプリミティブの側で、libsodium と NIST ベクタが担う。

## 目的

衛星0の COMM 電源レールを落とす。

EX-X01、EX-S01 と同じ位置——クロスリンク、侵害された僚機、鍵は無い。

```bash
make exercise EX=EX-S02-authenticate-the-request          # range の中のシェルに入る
python3 exercises/EX-S02-authenticate-the-request/solve.py   # そのシェルのプロンプトで
```

`make exercise` は **range のネットワーク名前空間の中**のシェルに入る。ソルバはそこから走らせる。
名前空間にはループバックしか無く（SAFE_USE.md）、起動ごとに新しく作られるので、
別の端末で起動したソルバは `ConnectionRefusedError` を受け取る。

シェルではなく1コマンドだけ走らせるなら:

```bash
make exercise EX=EX-S02-authenticate-the-request RUN='python3 exercises/EX-S02-authenticate-the-request/solve.py'
```

## 何が起きたか

```
OBC: accepted a connection from 5 on port 10
OBC: APID 0x0a9 PUS 17,1 from source 66
OBC: PUS 17,2 report sent to COMM (counter 0)

OBC: accepted a connection from 13 on port 10
OBC: REJECTED an unauthenticated telecommand from node 13
```

**レールは点いたままである。** SDLS がアップリンクに入っても触れられなかった攻撃——
SDLS はトランスファフレームのプロトコルで、クロスリンクは CSP を運ぶため——が、
ここでは拒否される。理由は、制御がリンクを離れて**要求の上に移った**ことである。

**何が変わらなかったかに注目すること。** ソルバは EX-X01 と EX-S01 が使うのと同じファイルである。
クロスリンクは同じバスである。OBC の権限表も、拒否報告も、長さ検査も、すべて ON のままで、
すべてそれまでどおりのことをしている。**フラグ1つ、制御1つ、そして経路が問題でなくなった。**

代価も線の上に見える。11 オクテットのペイロードだった同じ PUS 17,1 が、いま 31 オクテットである。
**全テレコマンドに20オクテット、永久に。**

## 結論すべきこと

**制御の適用範囲は、それが束縛されている対象である。** リンクに束縛すればそのリンクを覆う——
それが EX-S01 で、この range で最も強いリンク層の制御についてそうだった。
要求に束縛すれば要求を覆う、その要求がどこへ行っても。
これは暗号についての主張ではない。**EPS のトークンにも同じ文が当てはまる**——
だからあの制御は作者が考えもしなかった経路を生き延び、権限表は生き延びなかった。

**そして何が未解決のままかに注目すること。毎回同じものだからである。**
認証は**誰か**に答える。**何を**には一度も答えていない。
EX-G02 の権限表は今も「認証された局がレールを落としてよいか」を決めており、
その表が間違っていれば、**完璧に認証された指令が間違ったことをする**。
これを配備して止まった range には、暗号が1つも無かった頃とまったく同じように EX-G02 が残る。

代価と、これが直さないことは `mitigation.ja.md` にある。
