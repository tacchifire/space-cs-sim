---
id: EX-G02
title: 宇宙機が確かめない権限
layer: ground segment
difficulty: intermediate
duration: 30-45 min
prerequisite: EX-G01
ttp:
  # 意図的に空にしてある。SPARTA と SPACE-SHIELD への対応付けは、公式の STIX エクスポートで
  # 各 ID の実在を確認する作業であり、それを行うツールはここに無い。埋めた一覧は、引用の形を
  # した推測になる。検証する仕組みができるまで空のままにする。
  sparta: []
  space_shield: []
---

# EX-G02 — 宇宙機が確かめない権限

## 状況

衛星 0 と話す地上局が 2 つある。主局は指令を出す。副局は**見るために**建てられた。テレメトリを
受け、パスの確認のために ping を打つ。指令は出さないことになっている。レンジの認可マトリクスは
まさにそう書いてある（`src/cuberange/gs/authority.py`）。

| 局 | ping | power | observe |
| --- | --- | --- | --- |
| primary | 可 | 可 | 可 |
| backup | 可 | **不可** | 可 |

このマトリクスは**地上にある**。運用者コンソールが何を提示するか、地上系が自発的に何を送るかを
決める。宇宙機はこれを一度も見たことがない。

そしてテレコマンドには、誰が送ったかが既に載っている。PUS 副ヘッダには 16 ビットの source id が
あり、地上局はそこを埋めている。OBC はその欄を読み、コンソールに出力し、返す報告の宛先にそのまま
echo している。**判断には一度も使っていない。**

## 目的

副局から、衛星 0 の COMM 電源線を切ること。

主局の識別子を騙るのではない。副局として、副局だと名乗る source id のまま、正直にやること。
面白い結果は「騙れる」ことではない。**騙る必要が無い**ことである。

## 何が動いているか

CAN ハブ上の COMM・OBC・EPS と、宇宙リンク。このシナリオには意図的な点が 2 つある。

- **EPS は硬化版である。** バス上に電源指令を直接偽造するのは EX-B01 であり、このイメージでは
  既に塞がれている。電源線を動かす唯一の道は、トークンを持つ OBC に頼むことである。
- **OBC は両方のビルドで EX-F01 の長さ検査が ON である。** PUS 8 の引数コピーには境界がある。
  この演習の主題は「メモリ安全なサービス 8 が、それでも誰にでも実行されること」であり、
  オーバーフローを残しておくと、それが問題だったと読者に思わせてしまう。

攻撃者マシンも CAN インジェクタも無い。ここに注入すべきものは何も無い。

## 実行する

```bash
make firmware-g02
make exercise EX=EX-G02-unauthorized-authority

# あるいは手で、`make exercise` が出すプロンプトで
python3 exercises/EX-G02-unauthorized-authority/solve.py --station backup
```

`make exercise` は **range のネットワーク名前空間の中**のシェルに入る。ソルバはそこから走らせる。
名前空間にはループバックしか無く（SAFE_USE.md）、起動ごとに新しく作られるので、別の端末で
起動したソルバは `ConnectionRefusedError` を受け取る——宇宙リンクもインジェクタも外側には
存在しない。`make channel`・`make gs`・`make verify` もそのシェルから動き、コマンドは従来のままである。

シェルではなく1コマンドだけ走らせるなら:

```bash
make exercise EX=EX-G02-unauthorized-authority RUN='python3 exercises/EX-G02-unauthorized-authority/solve.py --station backup'
```

`solve.py` は送るフレームを表示する。正規の方も表示させて、並べて見てほしい。

```bash
python3 -c "
import importlib.util, sys
sys.path.insert(0, 'src')
s = importlib.util.spec_from_file_location('s', 'exercises/EX-G02-unauthorized-authority/solve.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.build_command('primary', 0).hex().upper())
print(m.build_command('backup',  0).hex().upper())"
```

## ヒント

<details>
<summary>source id はどこに入っているか</summary>

PUS TC 副ヘッダの 3・4 オクテット目（ECSS-E-ST-70-41C）。`src/cuberange/proto/pus.py` が詰め、
`firmware/apps/obc/src/main.c` が `handle_space_packet` の冒頭で `source_id` に読み出し、
出力し、そして**意味のあるどこにも渡していない**。
</details>

<details>
<summary>何も返ってこない。効いたのか</summary>

どちらにせよ何も返らない。脆弱版で不正な指令に拒否応答が出ないのは、不正だと誰も気づいていない
からである。成功した指令に確認応答が出ないのは、ここでの PUS 8,1 に報告が無いからである。
OBC コンソール（`$(make -s out)/obc.uart`）か、電源線のピンを見ること。正直な証拠は電源線の方で、
`gpioPortD` のピン 5、`PowerDomain` が読んでいるものである。
</details>

<details>
<summary>電源線が落ちてリンクが死んだ</summary>

そのとおり。それが発見であって、設定の失敗ではない。機能 1 は無線を切る。権限なしにそれを使った
局は、切った無線を戻す指令が届く経路も同時に消したことになる。シナリオを起動し直すこと。
</details>

## 何を結論すべきか

**地上の認可マトリクスは帳簿である。** 持つ価値はある——ありふれた失敗、つまりパス終盤の疲れた
運用者が違うコンソールに向かう事故は止まる。しかし第二の地上系、放置されたコンソール、侵害された
サイト、リンクに到達した誰かに対しては何も止めない。どれもそのマトリクスを参照しない。

**識別子は最初からワイヤに載っていた。** ここは腰を据えて考える価値がある。宇宙機に情報が
不足していたのではない。すべてのテレコマンドで source id を受け取り、記録し、報告として地上へ
返していた。対策の実装に必要だったのは、新しい欄でも新しいプロトコルでも鍵交換でもなく、
**既にそこにあるものを読むという決断**だけだった。

**攻撃フレームは正規のものと 1 オクテットしか違わない。** 不正形式でもなく、過大でもなく、
再送でもなく、順序外でもない。テレコマンド列の異常を探す侵入検知規則は、ここで何も見つけない。
異常が無いからである。実在の局から届いた、ただ送る権限が無いだけの、正しい指令である。

**EX-G01 がこれを予告していた。** その mitigation.md は、取り込みポリシーが「運用者に触れない」
こと、「運用者コンソールは今でも何でもスケジュールできる——それは次の演習であって、これではない」
ことを書いている。そして「送信に印が付かない」とも書いている。source id がその印であり、
使われていなかった。

## そして直す

[mitigation.ja.md](mitigation.ja.md) を参照。
