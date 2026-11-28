# 編集ファイル(.annot)の仕様

Win版(`zumen_annot.pyw`)とWeb版(`web/`)は、同じ編集ファイル(`.annot`)を読み書きします。
この文書は、2つのアプリが守る共通の取り決めです。形式を変えるときは、必ず両方を同時に直し、
この文書の `version` を更新してください。

- 拡張子: `.annot`(中身は UTF-8 の JSON)
- 現在の版: `"version": 2` または `3`(下の「複数の図面・ページの入れ替え」)
- 図面そのもの(PDF・画像)は含まれません。注記と設定だけを保存します

## 全体の形

```json
{
  "version": 2,
  "source": { "path": "sample.png", "rel": "sample.png", "type": "image" },
  "pdf_dpi": 150,
  "current_page": 0,
  "common": { "font_size": 34.0, "length": 100.0, "width": 5.0,
              "c_text": "#000000", "c_circle": "#000000", "c_arrow": "#000000" },
  "next_num": 6,
  "paper": { "size": "A4", "orient": "自動", "margin": "10", "dpi": "200" },
  "pages": { "0": [ /* 注記 */ ] }
}
```

| 項目 | 内容 |
| --- | --- |
| `version` | 形式の版。今は `2`。 |
| `source.path` / `source.rel` | 元の図面の場所。Win版は絶対パスと、編集ファイルからの相対パスを入れる。Web版はファイル名だけを入れる(パスを知らないため)。読み込むときは、`rel` → `path` の順に探し、なければ図面を選んでもらう。 |
| `source.type` | `"pdf"` または `"image"`。 |
| `pdf_dpi` | PDFを何dpiの画素として扱うか。今は `150` 固定。座標の基準になる。 |
| `current_page` | 開いていたページ(0始まり)。 |
| `common` | 共通ステータス。「更新」で確定した値。 |
| `next_num` | 「次の番号」。 |
| `paper` | 用紙設定。`size` は `"図面サイズのまま"` / `"A4"` / `"A3"` / `"B4"` / `"B5"`、`orient` は `"自動"` / `"縦"` / `"横"`、`margin` は mm、`dpi` は `"150"` / `"200"` / `"300"`(どれも文字列)。 |
| `pages` | ページ番号(文字列。0始まり)→ 注記の配列。配列の順が、表の行の順・描く順になる。 |

## 複数の図面・ページの入れ替え(version 3)

「追加」で図面を足したり、ページを入れ替えたりした作業は、`version: 3` で保存します。
**図面が1つで、ページも頭から全部そのままの並びなら、これまでどおり `version: 2` で保存します**(旧版のアプリでも読めます)。

```json
{
  "version": 3,
  "source": { "path": "a.png", "rel": "a.png", "type": "image" },
  "sources": [ { "path": "a.png", "rel": "a.png", "type": "image" },
               { "path": "b.pdf", "rel": "b.pdf", "type": "pdf" } ],
  "page_order": [ [0, 0], [1, 0], [1, 1] ],
  "pages": { "0": [], "1": [], "2": [] }
}
```

| 項目 | 内容 |
| --- | --- |
| `sources` | 使っている図面の一覧(各要素は `source` と同じ形)。 |
| `page_order` | 全ページの並び。要素は `[sources の番号, その図面のページ(0始まり)]`。**`pages` のキーと `current_page` は、この並びの上での位置(0始まり)**。 |
| `source` | `sources` の先頭と同じ。旧版が開いたときに何かが出るよう残している(旧版は並びを知らないので、注記の位置はずれる)。 |

- 読み込むときは、`sources` の図面を `rel` → `path` の順に探し、見つからなければ選んでもらう。`page_order` が欠けている・範囲外のときは、図面を並べて頭から全ページとする(version 2 と同じ)。
- 図面の置き場所は `rel`(編集ファイルからの相対)。複数の図面を使うときは、同じフォルダに置いておくと見つけやすい。

## 座標

- 単位は「図面のピクセル」。原点は図面の左上、x は右、y は下に増える。
- 画像はそのままの画素数。PDFは `pdf_dpi`(150dpi)で描いたときの画素数(A4で約 1240 × 1754)。
- Web版は、図面が大きすぎるときは表示用の画像だけ縮小して持つ。**保存される座標は常に元の大きさ基準**なので、Win版と行き来しても位置はずれない。

## 注記(pages の配列の要素)

```json
{ "x": 330, "y": 330, "num": "3,4", "arrow": true, "angle": 45.0, "common": true,
  "font_size": 34.0, "length": 100.0, "width": 5.0,
  "c_text": "#000000", "c_circle": "#000000", "c_arrow": "#000000",
  "extra": [ { "angle": 160.0, "length": 100.0, "width": 5.0, "c_arrow": "#000000", "common": true } ] }
```

| 項目 | 内容 |
| --- | --- |
| `x`, `y` | 〇の中心。`null` なら「未配置の行」(表にだけある行)。 |
| `num` | 番号の文字。`"3,4"`(複数)や `"3-5"`(範囲)も可。 |
| `arrow` | 1本目の矢印の有無。未配置なら `null`。 |
| `angle` | 1本目の矢印の向き(度)。0=右、90=上、反時計回り。未配置なら `null`。 |
| `common` | `true` のとき、「更新」で共通ステータスが反映される。 |
| `font_size`, `length`, `width` | 文字サイズ・矢印の長さ・太さ(px)。 |
| `c_text`, `c_circle`, `c_arrow` | 数字・〇の枠・矢印の色(`#RRGGBB`)。 |
| `extra` | 2本目以降の矢印。`angle`・`length`・`width`・`c_arrow`・`common` を持つ。 |

## 読み込むときの決まり(両アプリ共通)

- 知らない項目は無視する(将来の項目追加に備える)。
- `common` や注記の項目が欠けていたら、既定値で補う(`font_size` 28、`length` 80、`width` 4、色は黒)。
- 配置済みで `arrow` が欠けていれば `true`、`angle` が欠けていれば 45 とする。未配置なら `arrow`・`angle` は `null`、`extra` は空にする。
- 古い形式(`common` がなく `colors` と `defaults` がある)は、見た目を変えないよう、全注記を `common: false` で開く。

## 描き方・計算の決まり

画面の見た目と書き出しの結果が同じになるよう、次の式を共通にしています。
実装は Win版 `zumen_annot.pyw` の同名の関数と、Web版 `web/core.js` にあります。

- 〇の枠の太さ: `max(1.5, font_size × 0.09)`
- 〇の大きさ: 番号の文字の幅で決まる。1〜2桁は円、それより長い番号(3桁以上・複数番号)は横に伸びる楕円(`circleRadii`)
- 矢印: 〇の縁から出る。矢じりの長さ・幅は太さから決まる(`arrowShape`)
- 用紙の割り付け: 余白の内側に図面を収め、中央に置く(`paperLayout`)

## 互換性の確認

`web/tests/` に、Win版(Python)の計算結果を基準にした照合テストがあります。

```
python3 web/tests/make_pyref.py   # Win版の計算結果(pyref.json)を作り直す
node --test web/tests/core.test.mjs
```

(`make_pyref.py` は `zumen_annot.pyw` を読み込むので、リポジトリの直下から実行します。)
