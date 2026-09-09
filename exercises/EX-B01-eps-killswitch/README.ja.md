---
id: EX-B01
title: 内部バスから無線を殺す
layer: internal bus
difficulty: introductory
duration: 30-45 min
ttp:
  # 意図的に空にしてある。SPARTA と SPACE-SHIELD への対応付けは、公式の STIX エクスポートに対して
  # ID の実在を確認する作業であり、それをするツールはここに無い。埋めれば、引用の体裁をした推測に
  # なる。何かが検証するようになるまで空のままにする。
  sparta: []
  space_shield: []
---

# EX-B01 — 内部バスから無線を殺す

*English: [README.md](README.md)*

## 状況

あなたは衛星の CAN バス上にいる。
どのサブシステムも所有していない。
できるのは線にフレームを流すことだけである。

侵害されたペイロード、悪意ある部品、あるいはハーネスに触れられる者が置かれる位置である。

EPS がロードスイッチを所有する。
そのうちの1本が COMM、つまり無線に給電している。
このバス上で指令を認証するものは何も無い。

## 目的

衛星を沈黙させる。
成功条件は、地上局がテレメトリを失い、EPS のコンソールが `COMM rail OFF` を報告することである。

## 動いているもの

```
地上局 --TCP--> COMM.usart2 --CSP/CAN--> OBC        (PUS 17 の往復)
                  COMM  OBC  EPS  attacker  ------  CAN ハブ1本
                             |
                  gpioPortD.5 = COMM の電源レール
```

攻撃者は、実行時にコンパイルされる C# の `ICAN` ペリフェラルと TCP リスナを載せた、素の Renode マシンである。
そのポートに `"<hex can id> <hex data>"` を書けば、生の CAN フレームがバスに現れる。
特権も SocketCAN も自前のファームウェアも要らない。

ホスト側の電源ドメインが EPS のレール GPIO を監視し、落ちたら COMM のマシンを halt する。
Renode には給電されていないマシンという概念が無いので、物理的な帰結を外から適用している。

## 実行

```bash
make firmware-p1
make exercise EX=EX-B01-eps-killswitch      # シナリオを起動したままにする
python3 exercises/EX-B01-eps-killswitch/solve.py     # 模範解答
```

## ヒント

<details><summary>ヒント1 — どこを見るか</summary>

EPS は CSP ポート 11 で待ち受ける。
指令は7オクテットで、opcode、レール、状態、そして残り4バイトである。
`firmware/apps/eps/src/main.c` を読み、脆弱版がその7つのうちどれを実際に検査しているか決める。
</details>

<details><summary>ヒント2 — バスにフレームを載せる</summary>

`src/cuberange/proto/csp.py` が CSP v1 パケットを組み立て、CFP over CAN のフレームに断片化する。
インジェクタの行プロトコルは `"<hex can id> <hex data>"` で、1行1フレームである。

静かに何も起きない原因が2つある。
拡張29ビットではなく標準11ビットのフレームを送ること。
そしてエミュレーションが停止している間に注入すること。
どちらもこの演習を作る過程で実際にやった間違いである。
</details>

<details><summary>ヒント3 — 送信元アドレス</summary>

CSP の送信元を 1 にして、OBC を名乗る。
そのうえで、システムのどこかにそれに気付くものがあるかを自問してほしい。
</details>

## 何を結論すべきか

面白いのは EPS にバグがあることではない。
EPS は設計どおりに振る舞っており、その設計には運用者の指令と他人の指令を区別する手段が無い、ということである。

認証の無いバスは、そこにいる最も信用できないノードと同じだけしか、どのノードも信用できなくする。
しかもここでの攻撃者は、そもそもノードですらなかった。

これは Willbold らが実在の衛星ファームウェアで見つけたものと同じ形である（*Space Odyssey*, IEEE S&P 2023）。
テレコマンド認証の欠如または回避可能性、そして独自プロトコルを、不明瞭さがアクセス制御であるかのように扱うことである。

## そして直す

[mitigation.ja.md](mitigation.ja.md) を見て、検証を走らせてほしい。
3方向すべてを assert する。

```bash
make verify EX=EX-B01-eps-killswitch
```

| テスト | 何を assert するか |
| --- | --- |
| `test_attack_succeeds_against_the_vulnerable_eps` | 偽造したフレームが衛星を沈黙させる |
| `test_mitigation_blocks_the_forged_command` | 対策版はそれを拒み、稼働を続ける |
| `test_the_mitigation_does_not_break_legitimate_commands` | 認証された指令は今も通る |

3つ目は前の2つと同じだけ重要である。
本物の運用者まで止める制御は対策ではなく、障害である。
