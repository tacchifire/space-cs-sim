# Glossary

*日本語版: [glossary.ja.md](glossary.ja.md)*

This glossary is the single reference for terminology and notation in this repository. Documents are aligned to the glossary, not the other way round: when a document and this glossary disagree, the document is what changes. Every "not used" form below was found in the repository during the survey of the 68 Japanese documents on 2026-11-21; no variant is listed on suspicion alone.

## Purpose

- One spelling per concept, in both languages, so that a reader meeting a term in one document reads it the same way in every other.
- The Japanese form is chosen by the repository's own majority usage where the documents disagreed; the counts behind each contested choice are in the notes.
- Expansions of acronyms live here, because the documents themselves do not expand them (see the acronym rule below).
- New terms are added here first, with their Japanese form and any variant to avoid, before they spread through the documents.

## Notation rules

### Long vowels in katakana loanwords

The default is to omit the long-vowel mark at the end of loanwords from English words ending in -er, -or or -ar: アダプタ (8 occurrences, アダプター 0), ハンドラ (21, ハンドラー 0), エミュレータ (8, エミュレーター 0), シミュレータ (4, シミュレーター 0), ルータ (3, ルーター 0), ユーザ (1, ユーザー 0), マネージャ (3, マネージャー 0), ドライバ (2, ドライバー 0), パラメータ (2, パラメーター 0), モニタ (2, モニター 0), フィルタ (1, フィルター 0), コントローラ (1, コントローラー 0), メモリ (23, メモリー 0). Three words keep the mark because the repository uses them that way without exception or by majority: サーバー (7, サーバ 0), ポリシー (14, ポリシ 0), ブラウザー (4, ブラウザ 1 in CLAUDE.ja.md). The yomiyasu style skill deliberately leaves long-vowel variation as the source wrote it, so it is not the authority here; this glossary is, and where the skill's usual preference and the repository's majority conflicted, the repository won and the exception is listed above.

### Numbers, units and spacing

Digits are half-width everywhere; the survey found no full-width digit in any Japanese document. A Latin unit takes a space after the number: 2 MiB, 1 Mbps, 5 ms, 5 V, 986 Hz are the forms the documents already use consistently. A Japanese counter suffix takes no space: 30秒, 3件, 2機, 4バイト. The counter rule is the one the documents currently break — 30 秒 appears in the EX-G03 and EX-G04 documents against 30秒 in README.ja.md, and 件, 回, バイト and 機 all appear in both spacings — so counter expressions are a first target for alignment. A percent sign attaches to the number (13%), ranges use ～ (27.67～27.76 C in docs/flatsat.ja.md), and dates are ISO form (2026-09-30).

### Parentheses, quotation and symbols

Japanese prose uses full-width parentheses （） — 454 occurrences against 246 half-width, and the half-width ones are almost all code, identifiers, function calls, paths and links, which keep half-width. Japanese prose quotes use 「」; double quotes are reserved for literal strings and values. Bold, code spans and links follow the surrounding document's existing habits; this glossary fixes words, not layout.

### Acronyms and standards numbers

Acronyms are not expanded at first use in prose. That is the repository's settled convention, not an oversight: CCSDS (31 occurrences in the Japanese documents), PUS (89), CSP (43) and SDLS (43) are used bare from README.ja.md onward, and the one expansion on record is CubeSat Space Protocol in docs/legal/export-control.ja.md. The expansions therefore live in the table below, and a reader who needs one comes here. Where precision matters, a standard is cited with its document number, as in CCSDS 355.0-B-2 and CCSDS 132.0-B-3. Node names — OBC, EPS, ADCS, COMM — stay in Latin capitals in both languages, as do tool and library names such as Renode, Zephyr and CryptoLib.

### Exercise IDs and PUS notation

An exercise ID is EX-Xnn: the letters EX, a hyphen, one family letter and a zero-padded two-digit number. The survey found 754 occurrences and no variant — no space form, no one-digit form. Directory names extend the ID with a hyphenated slug, as in EX-F02-key-extraction. PUS services are written PUS 17,1 for a service and subtype pair — comma, no space after it — and PUS 8 for a service alone. PUS-C is used only when naming that specific standard variant, as ASSURANCE.ja.md does for its subheaders; the project's own protocol is ECSS PUS.

### Style

The Japanese documents are written in the plain style (だ・である) throughout: the survey found plain-style sentence endings in all 68 files and no polite-style (です・ます) endings at all. New Japanese text follows the plain style and does not mix the two. English documents use the plain declarative style of the existing documents. In both languages, a claim about what code does is stated as fact only when it has been run or read; the evidence rule in README.md governs, and the glossary adds nothing to it.

## Terms

### Protocols and standards

| Term | Japanese | Not used | Notes |
| --- | --- | --- | --- |
| CCSDS | CCSDS | — | Consultative Committee for Space Data Systems. Cited with its document number where precision matters, for example CCSDS 355.0-B-2 (SDLS) and CCSDS 132.0-B-3 (TM framing). Not expanded in prose. |
| ECSS PUS | ECSS PUS | PUS-C as the project's generic name | European Cooperation for Space Standardization, Packet Utilization Standard. Written PUS after the first mention in a document. Service notation is under Notation rules. |
| CSP | CSP | — | CubeSat Space Protocol. The expansion appears once in the repository, in docs/legal/export-control.ja.md; everywhere else CSP is bare. |
| SDLS | SDLS | — | Space Data Link Security, CCSDS 355.0-B-2. A transfer-frame protocol; it does not cover the crosslink, which carries CSP. |
| CAN | CAN | — | Controller Area Network. The bus between nodes. CAN classic and FDCAN are written as the code names them. |
| TC | テレコマンド | — | Telecommand: the category of commands sent to the spacecraft, as in a TC frame or TC replay (EX-L01). |
| TM | テレメトリ | レポート for a PUS report | Telemetry: the category of data returned from the spacecraft. A PUS TM report is a 報告 in Japanese, never a レポート. |
| APID | APID | — | Application process identifier. Written as a hexadecimal value where the documents give one, for example APID 0x0a9. |
| MAC | MAC | — | Message authentication code, here AES-256-GCM. The MAC proves possession of a key; it does not prove authority, which is EX-U02's subject. |

### Project terms

| Term | Japanese | Not used | Notes |
| --- | --- | --- | --- |
| CubeRange | CubeRange | キューブレンジ | The project name. Latin script in both languages (16 occurrences in the Japanese documents, the katakana form none). |
| exercise | 演習 | エクササイズ | One EX-Xnn unit of the range. 演習 222 occurrences in the Japanese documents, エクササイズ none. |
| range | レンジ | 射場 | The range as a whole. レンジ is the majority form (CLAUDE.ja.md's own first use included); 射場 appears only in CLAUDE.ja.md, three times. |
| ground station | 地上局 | グラウンドステーション | One station: an endpoint that transmits and receives. 地上局 70 occurrences in 27 files; the katakana form none. English ground station 69 occurrences. |
| ground segment | 地上セグメント | — | The ground side as a whole: stations, bridges and operator tooling. Japanese often writes 地上側 (20 occurrences) when contrasting with the space side; use 地上セグメント when the segment itself is the subject. |
| spacecraft | 宇宙機 | — | The generic concept: the system, its class, its behaviour. 宇宙機 195 occurrences in 45 files; English spacecraft 224. |
| satellite | 衛星 | — | A concrete, countable instance: 衛星0, 2衛星構成, 実在の衛星. 衛星 66 occurrences; English satellite 58. Not a free variant of 宇宙機 — the choice says whether an instance is meant. |
| node | ノード | — | One computer on the bus. The node names OBC, EPS, ADCS and COMM are identifiers, not terms to translate. |
| uplink | アップリンク | — | The ground-to-space link as a named channel. For direction alone, Japanese uses the shorthand 上り (30 occurrences), which is legitimate and not a variant to fix. |
| downlink | ダウンリンク | — | The space-to-ground link as a named channel. Direction shorthand is 下り (42 occurrences), on the same terms as 上り. |
| crosslink | クロスリンク | — | The link between spacecraft, carrying CSP. クロスリンク 51 occurrences in 10 files. |
| space link | 宇宙リンク | スペースリンク | The ground–space link. 宇宙リンク 38 occurrences in 18 files; スペースリンク 7, all of them in docs/testbed/. English is uniformly space link (74). |
| fault injection | 故障注入 | フォルトインジェクション | Deliberately causing a fault to observe the response, for example silencing a bus. 故障注入 11 occurrences in 5 files. |
| testbed | テストベッド | テストベンチ | The hardware testbed under docs/testbed/ and hardware/. テストベッド 11 occurrences. |
| FlatSat | FlatSat | フラットサット | The Electronic Cats teaching board and its documents under docs/flatsat.ja.md. Latin script in Japanese text too (10 occurrences, the katakana form none). |
| harness | ハーネス | — | The test harness that runs an exercise and asserts the outcome. |
| oracle | オラクル | oracle in prose | An independent implementation or published reference that a codec is checked against. Paths and file names keep oracle, as in test_oracle_pus.py. |
| golden vector | ゴールデンベクタ | golden ベクタ | A committed reference vector from an independent source. Japanese joins ゴールデン to the noun with no space (ゴールデンファイル, ゴールデンオラクル); golden ベクタ appears once, in the EX-G02 mitigation. Paths keep golden, as in tests/golden/. |
| mitigation | 対策 | 緩和策 | The document paired with each exercise README, and the fix it describes. 対策 140 occurrences in 48 files; the file name stays mitigation.md. |
| defect | 欠陥 | 不具合 | The deliberate flaw an exercise is built around. 欠陥 26 occurrences; 不具合 once. 脆弱性 is kept for vulnerability in the generic security sense, as in SECURITY.ja.md. |
| key (cryptographic) | 鍵 | キー for a cryptographic key | 鍵 276 occurrences. キー is reserved for identifiers and cache keys, as in キャッシュキー in containers/validation/README.ja.md. Most apparent キー occurrences are inside ハウスキーピング and are not this term. |
| allowlist | allowlist | 許可リスト | The list of permitted services, addresses or senders. allowlist 11 occurrences; 許可リスト 2 (SECURITY.ja.md and the EX-G01 mitigation). |
| channel | チャネル | チャンネル | A virtual channel, a link channel, a channel of the bus. チャネル 28 occurrences in 20 files; チャンネル 24 in 7, concentrated in the EX-G03 pair (17) and the EX-G04 pair (3). |
| sensor | センサ | センサー | センサ 9 occurrences, all in docs/flatsat.ja.md; センサー 7, all in docs/flatsat-web.ja.md. The two FlatSat documents disagree with each other, and センサ follows the long-vowel rule. |
| interface | インタフェース | インターフェース | インタフェース 16 occurrences in 7 files; インターフェース 2 (docs/flatsat-web.ja.md and docs/testbed/pinmap.ja.md). |
| browser | ブラウザー | ブラウザ | One of the three long-vowel exceptions. ブラウザー 4 occurrences; ブラウザ once, in CLAUDE.ja.md. |
| server | サーバー | サーバ | One of the three long-vowel exceptions. サーバー 7 occurrences, all in docs/flatsat-web.ja.md; the bare form none. |
| policy | ポリシー | ポリシ | One of the three long-vowel exceptions. ポリシー 14 occurrences in 5 files; the bare form none. Applies to import policies and host policies alike. |
| operator | 運用者 | — | The human running the range in the scenario: the role the exercises address and sometimes attack (EX-D01). 運用者 227 occurrences in 48 files. |
| attacker | 攻撃者 | — | The adversary in an exercise, wherever they sit: on the bus, on the link, at another station. 攻撃者 124 occurrences in 42 files. |
| command | 指令 | — | A command as an operational act sent to the spacecraft is a 指令 (139 occurrences). コマンド (154) is for shell and CLI commands and code-level commands, and テレコマンド names the TC category. The three are kept apart. |
| report | 報告 | レポート for a PUS report | A PUS TM report: 報告 206 occurrences, including 検証報告 and 報告カウンタ. レポート is used only for the generated HTMLレポート artifact in the FlatSat documents. |
| radio | 無線 | — | The radio as a physical thing and its link: 無線を落とす, 無線機. 60 occurrences in 27 files. Not interchangeable with リンク, which names the logical channel. |
| replay | リプレイ | 再生攻撃 | The replay attack or event: EX-L01 のリプレイ, リプレイ可能, アンチリプレイ. The mechanism words are 再送 — 再送カウンタ, 再送防御 — and a frame refused as a replay is refused as 「再送」. |
| install, set up | インストール, 構築 | セットアップ in prose | セットアップ never appears in Japanese prose (0 occurrences). Packages are インストール, an environment is 構築, and setup survives in identifiers: tools/setup-toolchain.sh, docs/host-setup-without-root.ja.md. |

### Verification words

| Term | Japanese | Not used | Notes |
| --- | --- | --- | --- |
| verify | 検証 | — | To establish that a property or claim holds: a MAC is 検証される, a claim is 検証済み only when it has been run or read. 検証 111 occurrences in 39 files. |
| cross-check | 照合 | — | To compare against an independent reference: NIST vectors, a ledger of what was sent, a byte-for-byte comparison with the other implementation. 照合 28 occurrences in 11 files. |
| confirm | 確認 | — | To look and establish a state or fact: 確認したシェルは, 権限が不足していることを確認する. 確認 79 occurrences in 23 files. Weaker than 検証 — it asserts what was seen, not what was proved. |
| inspect | 検査 | — | To check an item at a boundary against rules: パケットの検査, トークン検査, 長さ検査. The packet in EX-S01 was not failed by inspection; it never met one. |
| test (software) | テスト | — | A software test and its run: the pytest suites, golden tests. テスト 63 occurrences in 30 files. |
| test (formal scheme) | 試験 | — | A formal test or certification regime only: ASSURANCE.ja.md's point that conformance is a 試験制度. Not a synonym for テスト. |

## Keeping this glossary current

A term enters this glossary when a second document needs it, or when two documents are found spelling the same concept differently — whichever comes first. The entry records the count behind a contested choice, so a later reader can tell a measured majority from a guess. When a document is aligned to the glossary, only the spelling changes; a term's meaning is never edited in passing. This file and glossary.ja.md are a pair under test_doc_parity.py, so a section, row or bullet added to one is added to the other in the same commit.
