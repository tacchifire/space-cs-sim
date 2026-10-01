# FlatSat の USB 診断

*English: [flatsat.md](flatsat.md)*

`tools/flatsat.py` は、所有者が操作する教材用の Electronic Cats FlatSat v1.0 RP2040 に対して、USB の一覧表示、受信記録、ローカルシェルへの照会、センサ値の連続記録を行う。
Renode の演習とは別に動作し、演習用の STM32 ファームウェアは RP2040 に移植していない。

## 対応する USB 構成

初期版は USB 識別子 `1209:babc` を対象とし、`ttyACM` の番号ではなく USB 記述子からポートの役割を判別する。
この判別は、固定した版の [Electronic Cats のシリアル管理モジュール](https://github.com/ElectronicCats/flatsat-ground-station/blob/3a2d7b9500520015232cd9d6fc7b0d1916ee866c/modules/core/serial_manager.py) に従う。
2026-09-30 に接続した基板では、次のインタフェースを確認した。

| USB インタフェース | CLI の役割名 | 確認した Linux パス |
| --- | --- | --- |
| `Cat-Radio0` | `radio0` | `/dev/ttyACM0` |
| `Cat-Radio1` | `radio1` | `/dev/ttyACM1` |
| `Cat-Shell` | `shell` | `/dev/ttyACM2` |

接続し直すとパスが変わる場合がある。
同日に USB の認識と `Cat-Shell` への照会を確認したが、無線インタフェースのアクセス権限はまだ不足しており、無線の受信記録は未確認である。
`fw_version` は `dev-f81af0b-dirty`、ビルド時刻は `2026-08-11T20:38:22Z` と応答した。
`status` は `RadioManager LISTENING` と応答し、`sensors` は加速度と環境の計測値を返した。
シェルを1秒間受信する操作ではポートを開けたが、自発的な出力は0バイトであり、テレメトリ受信の確認にはならない。
シェルの `help` 応答には、ローカルで CCSDS パケットを注入するコマンドはなかった。
参照したソースの版 `3a2d7b9` はパケット形式の参照元であり、この基板の搭載ファームウェアとは異なる。
この形式のパケットを基板が受理するかは未確認である。

## 環境構築とアクセス権限

リポジトリのルートで実行する。
追加の依存パッケージは `pyserial==3.5` であり、Renode のシミュレーションには不要である。
リポジトリの検証がソースファイルを再帰的に走査するため、仮想環境はチェックアウトの外に作る。

```bash
python3 -m venv ~/cuberange-flatsat-venv
. ~/cuberange-flatsat-venv/bin/activate
python3 -m pip install -r requirements-flatsat.txt
python3 tools/flatsat.py devices
```

`devices` は記述子を読むだけで、シリアルポートを開かず、確認用のデータも送信しない。
`permission required` と表示された場合、Linux の管理者は、これらのデバイスパスに一時的なアクセス権限を付与できる。

```bash
sudo setfacl -m "u:$(id -un):rw" /dev/ttyACM0 /dev/ttyACM1 /dev/ttyACM2
python3 tools/flatsat.py devices
```

基板を抜くと ACL が消える場合があるため、再度権限を付与する際は `devices` でパスを確認し直す。

## 受信記録とローカル情報の照会

ローカルシェルに照会し、その後にシェルの自発的な出力を10秒間記録する。
`monitor` はシリアルデータを送信せず、例ではアクセス可能なシェルを選ぶ。
`--port` を省略すると選択した基板の全インタフェースを開くため、すべてのポートへの権限が必要になる。

```bash
python3 tools/flatsat.py info --output /tmp/flatsat-info.jsonl
python3 tools/flatsat.py info --query help --output /tmp/flatsat-help.jsonl
python3 tools/flatsat.py monitor --port shell --duration 10 --output /tmp/flatsat-monitor.jsonl
```

`info` が既定で `Cat-Shell` に書き込むのは `fw_version`、`status`、`sensors` の3コマンドだけである。
`--query` はこの3つか `help` を個別に指定するオプションで、複数回指定できる。
これらのローカル照会は、固定した版の [ファームウェア検証ツール](https://github.com/ElectronicCats/flatsat-ground-station/blob/3a2d7b9500520015232cd9d6fc7b0d1916ee866c/modules/firmware/verify.py) に記載されている。
タイムアウトや未知のコマンドへの応答は照会失敗として扱い、ファームウェアの版を確認したとは見なさない。

受信対象の役割は `--port shell`、`--port radio0`、`--port radio1` で指定でき、デバイスのパスも直接指定できる。
複数の基板を接続している場合は、`devices` が表示した基板 ID を `--serial` に指定して1台を選ぶ。
JSONL の記録には UTC 時刻、接続時の記述子、受信したバイト列の各断片を `raw_hex` で保存し、`info` では送信した照会コマンドも記録する。
USB の読み取り単位とパケットの境界は一致しないため、`monitor` は断片を自動でパケットとして解析しない。
既存の出力ファイルは上書きせず、実行ごとに新しいパスを指定する。
`--output` を省略すると、このチェックアウト固有の `out_dir()/flatsat/` 配下に保存し、`OUT` を設定した場合は `$OUT/flatsat/` 配下に保存する。

Makefile には `make flatsat-devices`、`make flatsat-monitor`、`make flatsat-info`、`make flatsat-watch`、`make flatsat-summary`、`make flatsat-report`、`make flatsat-compare`、`make flatsat-ping`、`make flatsat-check` を用意している。
`FLATSAT_PYTHON` でインタプリタを選び（既定は `python3`）、`FLATSAT_ARGS` で CLI の追加オプションを渡す。
`flatsat-summary` と `flatsat-report` には、記録ファイルのパスを `FLATSAT_ARGS` で渡す。
`make flatsat-check` は `tests/pytest/test_flatsat*.py` のオフラインテストを実行する。

## センサ値の連続記録

確認したシェルは照会に応じてセンサ値を返したが、1秒間の受信監視では自発的な出力はなかった。
時間に沿った変化を記録するには `watch` を使い、`Cat-Shell` への接続を一つ開いて、`sensors` だけを繰り返し照会する。

```bash
python3 tools/flatsat.py watch --duration 10 --interval 1 --output /tmp/flatsat-watch.jsonl
python3 tools/flatsat.py summary /tmp/flatsat-watch.jsonl --json
python3 tools/flatsat.py report /tmp/flatsat-watch.jsonl --output /tmp/flatsat-report.html
```

既定値は `--duration 10`、`--interval 1`、`--timeout 2` で、単位はいずれも秒である。
指定時間を上限に照会するが、USB の読み取りが待機中の場合は、約50 msのタイムアウト単位の分だけ遅れて終了する場合がある。
応答が遅れても遅れた分の照会をまとめて送信しない。
実行ごとに新しい出力パスを指定する。
2026-09-30（JST）に10秒間記録した際は、10サンプルすべてが有効で失敗はなく、出力された温度の範囲は27.67～27.76 Cだった。

JSONL には送信した照会と受信したバイト列を保存し、各照会の結果を構造化した `sensor_sample` として追加する。
`watch` は、シェルが送り返した `sensors` のコマンド行全体を新しい応答の開始として使い、その前に受信したバイト列も元の記録に残す。
確認したファームウェアは、このコマンド行を送り返す。
完全で有効なサンプルには、シェルが出力する単位付きの有限な値が6項目すべて必要になる。

| 計測項目 | 必要な単位 |
| --- | --- |
| X 軸の加速度 | `mg` |
| Y 軸の加速度 | `mg` |
| Z 軸の加速度 | `mg` |
| 温度 | `C` |
| 気圧 | `Pa` |
| 相対湿度 | `%` |

ここで `mg` は標準重力加速度の1,000分の1を表し、加速度の値には重力も含まれる。
各サンプルのホスト時刻は照会開始時点を表し、センサが提供した計測時刻ではない。
記録同士を比較する際は単位をそろえ、たとえば気圧の `Pa` と `hPa` では値が100倍異なる点に注意する。

欠測値は JSON の `null` として保存し、ゼロには置き換えない。
応答の途中でタイムアウトした場合もサンプルは失敗として扱い、`watch` を停止する。
同じシェル照会には要求 ID がないため、遅れた応答を再試行に対応付けると、どの照会への応答か判断できなくなる。
応答後の無出力時間によって応答の終わりを確認できたものの項目が不足している場合もサンプルは失敗だが、照会は続けられる。
集計には完全で有効なサンプルだけを使い、失敗数は別に数える。
サンプルが一つでも失敗すると `watch` はゼロ以外の終了コードを返し、確認用の記録を残す。

`summary` と `report` は保存した記録を読み、USB ポートを開かない。
HTML レポートは表示する計測項目を選べるほか、失敗したサンプルをグラフの空白として示し、欠測をゼロや連続した線として表示しない。
外部のリソースを使わず、オフラインで開ける。

## センサ値のしきい値とイベント

複数回指定できる `--limit metric:min:max` で、境界値を含む範囲を設定する。
片側だけを制限する場合はもう片側を空欄にし、少なくとも一つの境界を指定する。
各計測項目に指定できる範囲は一つで、境界は有限の数値を小さい順に指定する。
例の範囲は操作者が決める実験条件であり、基板の動作保証範囲ではない。

```bash
python3 tools/flatsat.py watch --duration 3 --limit temperature_c:20:30 --limit humidity_percent::65 --label limits-demo --output /tmp/flatsat-limits.jsonl
python3 tools/flatsat.py alerts /tmp/flatsat-limits.jsonl --json
python3 tools/flatsat.py report /tmp/flatsat-limits.jsonl --output /tmp/flatsat-limits.html
python3 tools/flatsat.py alerts /tmp/flatsat-baseline.jsonl --limit temperature_c:20:30 --fail-on-alert
```

計測項目の名前は `temperature_c`、`pressure_pa`、`humidity_percent`、`accel_x_mg`、`accel_y_mg`、`accel_z_mg`、`acceleration_norm_mg` である。
単位は上の表に従い、加速度の大きさには重力も含まれる。
最初に範囲外の値を観測した時、または別の範囲外の状態に移った時は `triggered` を記録し、範囲外から範囲内に戻った時は `recovered` を記録する。
同じ状態が続く間はイベントを重複して記録しない。
欠測、無効な応答、タイムアウトでは、設定した全項目を判定不能の `unknown` とし、この状態に入った時に `unavailable` を記録する。
その後、範囲内の有効な値を観測した時は `resumed` を記録し、観測の再開と欠測を挟まない回復を区別する。
セッションには判定範囲を保存し、各 `alert` イベントにはサンプル番号、照会開始時刻、経過時間、値、境界、状態の変化を保存する。

`alerts` は検証した保存済みサンプルをオフラインで再判定し、記録した範囲を使うか、明示した `--limit` で全範囲を置き換える。
HTML の `report` も同じイベントの時系列を表示し、同じ方法で範囲を置き換えられる。
再判定では、記録された `alert` を信用せず、サンプルから状態の変化を計算し直す。
範囲を設定しない場合、`watch` は従来どおり動作し、レポートはイベント欄を表示しない。
既定では範囲外の状態も記録を続け、記録が成功すれば終了コード `0` を返す。
`--fail-on-alert` を指定した `watch` または `alerts` は、範囲外への遷移が一度でもあれば終了コード `3` を返し、記録やデータのエラーは終了コード `1` を返す。
終了コードで判定するオフラインの再判定では、空の記録、失敗したサンプルを含む記録、未完了の記録も終了コード `1` を返す。
`watch --fail-on-alert` には少なくとも一つの範囲を指定する。
CLI の構文が不正な場合は終了コード `2`、記録を中断した場合は終了コード `130` を返す。
`make flatsat-alerts FLATSAT_ARGS=...` でオフラインの再判定を実行できる。

## 二つの実験の比較

`--label` で実験名を、複数回指定できる `--note` で実験条件を記録する。
ログと HTML レポートには、計測値と一緒にこれらの情報を残す。

```bash
python3 tools/flatsat.py watch --duration 3 --label baseline --note 'interval=1s' --output /tmp/flatsat-baseline.jsonl
python3 tools/flatsat.py watch --duration 3 --interval 0.1 --timeout 0.1 --label candidate --note 'interval=0.1s' --output /tmp/flatsat-candidate.jsonl
python3 tools/flatsat.py compare /tmp/flatsat-baseline.jsonl /tmp/flatsat-candidate.jsonl --output /tmp/flatsat-comparison.html
python3 tools/flatsat.py compare /tmp/flatsat-baseline.jsonl /tmp/flatsat-candidate.jsonl --json
```

`compare` は保存した記録を読み、各項目について比較記録の平均から基準記録の平均を引いた差を返す。
HTML レポートは共通の軸で二つの記録を重ね、記録ごとの周期と欠測を保ち、それぞれ最初のサンプルを時刻の基準にする。
サンプル数が異なっていても比較でき、各サンプルを一対一に対応付けず、記録ごとに集計する。
基板 ID を照合し、ID が不明な場合や異なる基板の場合は、その状態を表示する。
平均値がない場合や数値で表せない差は欠測のままにする。
差は二つの記録の特徴を示すものであり、その原因を判断するには実験条件の記録が必要になる。

`summary` には標本標準偏差、最初と最後の値、その差、値の範囲も含まれる。
標準偏差には有効なサンプルが2件以上必要であり、算出できない統計値は `null` と理由を残す。
時刻の集計には、ホストで記録した周期、照会処理時間、記録の終了状態を含める。
ホストの時刻が不足したり、前の時刻以下になったりした場合、記録周期は算出しない。
ホストで記録した周期は、センサ内部の計測周期とは異なる。

照会コマンドが一致し、6項目すべてが正常なセンサ応答では、確認したファームウェアが返す末尾の空行で照会を終了する。
その他の応答は無出力時間で終わりを確認し、項目が不足した応答は空行だけで成功とは判定しない。
既定の照会間隔は1秒のままである。
2026-10-01（JST）に0.1秒間隔で3秒間照会した際は、28件すべてが正常で、失敗したサンプルはなかった。
その記録のホスト側の記録頻度は約9.986 Hz、平均照会処理時間は約5.718 msだった。

## OODA に沿った実験

基板の向きを変えると3軸の加速度がどう変わるか、といった具体的な問いを短い記録で確かめる。
**OODA ループ**に沿って、観察、解釈、判断、行動の順に実験を進める。

| 段階 | 実際の作業 |
| --- | --- |
| Observe（観察） | `watch` で基準となる記録を取り、基板の位置と記録ファイルのパスを控える。 |
| Orient（解釈） | `summary` と `report` で値と変化を確認し、失敗したサンプルも確認する。 |
| Decide（判断） | 変える条件を一つ選び、基準の記録と比較する計測項目を決める。 |
| Act（行動） | 決めた条件を変えて短い記録をもう一度取り、二つの記録を比較する。 |

条件を変えるのは操作者であり、これらのコマンドは実験を記録して確認するために使い、将来の操作を予約しない。
出力値の刻み幅によって同じ値が続く場合もあるため、同じ値が繰り返されただけではセンサの停止とは判断できない。
グラフが平らな理由をハードウェアの問題と解釈する前に、サンプルの失敗と元の応答を確認する。

## オフラインでのパケット確認

現在の PING はパケットの組み立てと確認だけに対応し、USB ポートを開かず、送信もしない。

```bash
python3 tools/flatsat.py ping --dry-run
python3 tools/flatsat.py decode 1820c0010006000000001022d9
```

例のバイト列は作成した平文 PING であり、APID は `0x020`、シーケンスは `1`、時刻は `0`、オペコードは `0x10`、CRC は正常である。
この構成は、6バイトの CCSDS 主ヘッダ、副ヘッダフラグがある場合の4バイトのミッション経過時刻、2バイトのパケット CRC を使い、固定した版の [Electronic Cats のパケットコーデック](https://github.com/ElectronicCats/flatsat-ground-station/blob/3a2d7b9500520015232cd9d6fc7b0d1916ee866c/modules/core/ccsds.py) に従う。
デコーダはヘッダの各項目、ペイロードのバイト列、CRC の検証結果を表示し、センサ値の形式を推測せず、ペイロードを復号しない。

この構成は CubeRange の PUS と転送フレームの形式、および USB データを `AA55` で包む2 CDC の Pwnsat 構成（`239a:cafe`）と異なる。
この基板でのローカルなパケット注入方法は未確認であり、無線送信インタフェースへの書き込みは電波を送信しうるため、実機への PING 送信は無効にしている。
このツールはファームウェアを書き換えず、演習を実機に接続しない。
教材用基板への適用範囲は [SAFE_USE.ja.md](../SAFE_USE.ja.md) を参照する。
