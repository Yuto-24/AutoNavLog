# AutoNavLog UI改善 要求仕様書

- 版: 2.9.0
- 日付: 2026-08-17
- 対象: AutoNavLog 1.1.0 / jma-msm-wind 0.2.1 / Docker Web service + Cloudflare Tunnel
- 実装担当: 別エージェント

## v2.9.0 RJFM北行き UMK/RCA例外（本節を最優先）

本節は、宮崎から大分方面へ `UMK → OVER FIELD → OMARU` のNewta CENTER Routeを
使用し、UMKを5,500 ft MSLで通過する計画だけに適用する。通常経路の距離・風三角・
燃料・到着計算、および既存の転記可否判定は、ここで明示した差分以外変更しない。

### D-43 座標トリガーと主経路

- FROMがRJFMで、KMLの最初の中間点が規則パックのUMKまたはOMARUから1.0 NM以内の
  場合だけ自動適用する。点名は判定に使わない。
- UMKを先に通るKMLでは、既存の後続OMARUをその位置のまま使う。後続OMARUがなければ
  UMK直後へ規則パック座標のOMARUを1点だけ挿入する。再読込・再計算で重複挿入しない。
- OMARUを最初に通るKMLでは、主経路へUMKを挿入せず、RJFMから基準UMKまでの距離を
  仮想RCA距離として保持する。
- KML内でUMKまたはOMARUに一致する座標はKMLを優先する。OVER FIELDと欠けた点だけを
  規則パックから補う。UMKからOMARUまでは5,500 ft MSLとする。
- OMARU自動挿入で分割される手入力距離は測地線距離比で2 Legへ配分して合計を保存する。
  端点が変わる手入力Courseは例外適用中に流用せず、解除時に元の経路・Phase・高度とともに
  復元する。規則改訂で制御区間が短くなったLegにも旧5,500 ft設定を残さない。

### D-44 UMK/RCAのPOH時間例外

- RCA位置は風で得た直線距離ではなく、物理UMKではその物理Legの採用終端、仮想UMKでは
  RJFM→OMARU親Legの採用距離を測地線距離比で換算した主経路位置へ固定する。
- CLIMBのETEと燃料は、採用温度を反映したPOHの5,500 ft到達値を採用する。
- 子区間のDIST、TC、VAR、MC、WIND、WCA、MH、GSはKMLの直線Legについて通常どおり
  表示する。この例外のCLIMB子行だけは、意図的に `ETE != DIST / GS` となる。
- 旋回案内の経路長、旋回時間、風偏位を主NAVLOGのTTL DIST・TTL TIME・燃料へ加えない。
- Route GraphではUMKとOMARUを独立点のまま保持する。NAV LOG主表では、RJFMから
  OMARU到着までの既存Sectionを1つの`RJFM→OMARU`親Legへ集約し、UMK/RCAを子区間境界として
  内包する。親DIST・ETE・燃料は子区間の未丸め合計、親TC・VAR・MCはRJFMからOMARUへの
  WGS84直行測地線値とする。親WIND・WCA・MH・GSは複数の実区間を単一値で表せないため空欄とし、
  子区間には各Sectionの実計算値を残す。
- RJFM→UMKは`CLIMB`、UMK→OMARUは`CRUISE`、いずれも5,500 ft MSLに固定する。
  経路表とNAV LOG編集表ではALT・Phaseを読取専用にし、OMARU出発Legから通常編集へ戻す。

### D-45 RWY別の診断案内

- RWY09とRWY27を毎回同じ採用風、TAS、POH高度時間で解く。RWY09の延長旋回は左、
  RWY27の初期旋回と延長旋回は右とする。20°固定バンクの空気塊旋回を
  優先し、解がなければ全方位の最大地上旋回半径を満たす20°以下の調整円を試す。
- 延長直線と最終UMK直線のMC範囲、PCA、UMK位置・5,500 ft・接線残差をhard制約とする。
  最終延長旋回開始点のMZE 4.0 DME以上はwarningのみとする。
- hard不適合経路は編集地図へ赤い診断線として残すが、転記補助へ経路を出さない。
  `NO_SOLUTION`と入力不足は算出不可理由を保存する。
- この診断の不成立・warningは`Issue`、ProjectStatus、転記可否へ加えない。ATC指示、地形、
  障害物、未定義の他空域は保証対象外である。

### D-46 規則パックと保存状態

- RJFM規則パックはmanifestとpayload SHA-256を検証して起動時に読む。UMK、OVER FIELD、
  OMARUは添付図の未検証デジタイズとして推定誤差を保持し、AIP公開ミラー・国交省告示・
  添付訓練要領・利用者Policyを同一出典として扱わない。
- 内部UI状態を`state_schema_version=5`とし、正規化済み計画と両RWYの診断結果を保存する。
  v4/v3は凍結modelで検証してv5へ移行する。診断結果は計算入力fingerprintの対象外だが、
  計画、規則版、payload SHA-256、主経路、気象・性能入力が変われば再計算で置き換える。
  保存計画が現在の経路・制御Leg・RCA距離・規則パックと一致しない場合は例外計算へ渡さず、
  Webでは旧診断線を非表示、直接計算ではBlockerとする。

### D-47 Web配置

- RJFM NORTHBOUND EXCEPTIONカードは、NAV LOG主表とFUEL表を含む横スクロール領域の直下へ
  表示する。経路線、CENTER Route、凡例は経路MAP内へ残す。計算入力がcurrentでない場合は
  カードと診断線を表示しない。
- 画面幅1,240 px以下は、入力、経路・MAP、準備状況、NAV LOG、RJFM案内の順に1列表示する。
  1,240 pxを超える3列表示では入力・経路・準備状況の上端を揃え、操作のために上下往復を
  要求する中間配置を作らない。

### v2.9.0受入基準

- **W-17**: 点名が誤っていても座標でUMK/OMARUを判定し、既存点の移動・重複挿入をしない。
- **W-18**: UMK/RCAまでのCLIMB ETE/FUELがPOH値と一致し、直線GSとDISTは変わらない。
- **W-19**: RWY09/27の成立・注意・不成立・算出不可を同じDTOで保存し、診断不成立だけでは
  ProjectStatusと転記可否が変わらない。
- **W-20**: Web編集地図、転記補助、保存再読込、schema、出典文書で同じ規則版を扱う。
- **W-21**: UMK物理／仮想の両経路で、NAV LOGは`RJFM→OMARU`を1親Legとして表示し、
  UMK/RCAを子境界に保持する。固定区間のALT・Phaseは編集できず、OMARU出発Legは編集できる。
- **W-22**: RWY09は左、RWY27は右の延長旋回を固定バンク／調整円の両モデルで生成する。
- **W-23**: 1,100 px前後のWeb画面で入力から結果までの表示順が下方向に単調である。

## v2.7.5 Issue #43 Golden NAVLOG表示（本節を最優先）

本節はIssue #43の後発コメントで確定した表示・計算Policyであり、v2.7.4以前の
目的地TAF採用、最終行、正常空欄、Physical Leg表示に関する矛盾する記述を置き換える。

### D-40 方位計算

- VARは東偏差を正とし、`MC = (TC + VAR) mod 360`を使用する。
- WCAは右修正を正とし、`MH = (MC + WCA) mod 360`を使用する。
- 計算DTOには未丸めMHを保持する。転記表示のMHは1°へhalf-upした表示MCと表示WCAを
  加算・正規化し、`291 + (-4) = 287`のように同一行の転記値間で式が成立するようにする。
- altitude guidanceを含む派生MCも同じ規則を使用する。

### D-41 計算結果と表示投影

- `CalculationOutcome.sections`は重複しないCalculation Zoneであり、距離・時間・燃料の
  唯一の集計元とする。
- `CalculationOutcome.display_rows`は表示専用とし、`PHYSICAL_LEG_SUMMARY`、
  `CALCULATION_ZONE`、`DESTINATION_INFO`、`LEG_SEPARATOR`を持つ。display rowから
  TTL DIST、TTL TIME、Fuelを再集計しない。
- 最終VISUAL_ARRIVAL以外の全Physical Legに親小計行と最低1つの内訳行を作る。親は
  FROM/TO、未丸めZone小計、Leg終点CUMを表示し、子はFROM/CUMを空欄にする。
- 子行の親または直前行と同じ計算値は`INHERIT`、継承しない意図的な空欄は`BLANK`とする。
  どちらも完全な空欄であり、`未取得`、`未確定`、`—`を表示しない。本来必要な値の取得・
  算出失敗だけを`UNAVAILABLE`として`未取得`、太字、赤系背景で表示する。
- PAは数値と表示状態を分け、`↗`、`↘`、`(<推定通過高度>)`を表現する。
- Check Point、RCA、EOC、物理終点は未丸めalong-route distance順に並べる。EOCの
  0.5 NM未満snapは物理変針点だけに適用し、Check Pointへ拡張しない。

### D-42 最終Legと目的空港情報

- VREPから目的空港までの計算親行はCAS 121 kt、CALM、WCA=0、GS=TASで計算する。
  目的地TAF風をWCA、MH、GS、ETE、燃料へ使用しない。
- 次行の`DESTINATION_INFO`はFROM空欄、TO=目的空港、空港標高、空港予想気温、TAF風
  だけを表示する。その他の航法・距離・時間・燃料セルは`BLANK`とする。
- WebとA4転記補助HTMLは同じ`NavLogDisplayCell.text`を表示し、別々に値を再計算しない。

### v2.7.5受入基準

- **W-13**: Python、Web、altitude guidance、schema、文書が`MC = TC + VAR`で一致する。
- **W-14**: 全通常Legの親/子、継承空欄、PA記号、Check Point/EOC順序、Leg間空行が
  Issue #43 Golden fixtureと一致する。
- **W-15**: 到着計算親行がCALMで、独立した目的空港情報行に空港諸元だけを表示する。
- **W-16**: 旧Snapshotのdisplay field欠損を読め、保存Projectを切り替えて再選択しても
  Project入力から同じ最新display projectionを再生成する。

## v2.7.4 表示整理・目的地TAF風・更新確認

### D-37 NAV LOG表示

- 自動計算値のセルへ「自動」を付けない。手入力値の「手入力」と未取得表示は残す。
- 採用QNHはinHgを主表示とし、同じ値のhPaを横へ併記する。
- `ESTIMATED_QNH_NOT_OFFICIAL` と `VERIFY_WITH_OFFICIAL_AERODROME_QNH` は、
  QNHの説明と画面末尾の確認文に重複するため生成・表示しない。QNH推定値を公式値と
  扱わない制約、取得不能時の手入力要求、公式気象との照合は維持する。

### D-38 目的地TAF風（Issue #26）

- 到着予定時刻は、Projectの出発予定時刻へ計算結果末尾の累積ETEを加えて求める。
- 目的空港ICAOと到着予定時刻をAviationWeather.govのTAF APIへ渡し、該当時刻の卓越風を
  取得する。`TEMPO` と `PROB` は単一の卓越風として採用しない。
- 風向、風速、ガスト、TAF発表時刻、有効期間、変化区分、TAF原文を参考欄へ表示する。
- 取得した卓越風は独立した`DESTINATION_INFO`行と参考欄だけに表示する。到着区間は
  CAS 121 kt、CALM、WCA=0、GS=TAS、燃料流量12 GPHを維持する。
- 通信失敗、TAF欠測、有効期間外は`DESTINATION_INFO`のWINDを真の`UNAVAILABLE`として
  `未取得`と表示する。到着区間のCALM計算はTAF取得成否に依存しない。
  同一空港のTAF応答は5分間cacheする。

### D-39 更新とcache

- AutoNavLogの版はPython package、Web package、旧Colab成果物、Notebook、検査コードで揃える。
- HTML応答は `Cache-Control: no-cache`、API応答は `Cache-Control: no-store` とする。
- 画面左上へアプリ版を表示し、`/healthz` の版と照合できるようにする。
- 開発・本番の更新はimageを `--no-cache` で再構築し、containerを
  `--force-recreate` で交換する。named volumeは削除しない。

### v2.7.4受入基準

- **W-9**: 自動計算されたVARと風の表示に「自動」がなく、手入力値の表示は残る。
- **W-10**: 廃止した2つのQNH警告が気象結果、Web UI、転記補助へ出ない。
- **W-11**: ETAがTAF有効期間内なら目的地風を`DESTINATION_INFO`行へ表示し、到着区間の
  CALM計算から分離する。
- **W-12**: HTMLとAPIのcache header、画面版、`/healthz` 版が更新手順どおり確認できる。

## v2.7.3 Docker Web service・目的空港場周高度・別添8-1整合

本節は利用者決定「ColabではなくWeb公開し、`.venv`ではなくDocker serviceで動かす」
に基づく配布方式の差分仕様である。
本節と旧本文のNotebook／Colab／Google Drive配布に関する記述が矛盾する場合は、
**本節を優先する**。航法計算、Issue、参照データ、保存、フェイルクローズ、
転記補助HTMLの契約はv2.6.0を維持する。

### D-30 主UIと実行形態

- 主UIは `web/` のReact + TypeScript + Vite SPAとする。
- APIと静的ファイルは `autonavlog.web` のFastAPIアプリが同一オリジンで配信する。
- Node buildとPython runtimeを分離したmulti-stage `Dockerfile` でSPAをpackageへ組み込み、
  `docker compose up -d --build` でserviceとして起動できること。hostの`.venv`へ依存しない。
- container内では `0.0.0.0:8000` をlistenし、Composeのpublished portはhostの
  `127.0.0.1:8123` だけへbindする。LANやInternetへ直接portを公開しない。
- runtimeは非root、read-only root filesystem、全Linux capability削除、
  `no-new-privileges` で動作し、health checkとrestart policyを持つ。
- Colab Notebookは旧Projectの確認・移行用として残してよいが、主配布物・主UIではない。

### D-31 Cloudflare公開境界

- 公開経路は既存のCloudflare Tunnel connectorから `http://localhost:8123` へ接続する。
- 恒常公開はremotely-managed tunnelのPublished applicationを用いる。
  Quick Tunnelは開発確認に限り、正式公開には用いない。
- 外部共有時はCloudflare Accessのself-hosted applicationとAllow policyを必須とする。
  originはCloudflareが付与する `Cf-Access-Authenticated-User-Email` を認証済みidentityとして
  受け取り、sessionと保存Projectをそのidentityへ拘束する。異なるidentityには存在自体を返さない。
- originはloopbackのままとし、Cloudflareを迂回する受信ポートを開けない。
- Tunnel token、Access credential、API tokenをリポジトリ、Project、ログ、ブラウザへ保存しない。

### D-32 Web状態・保存

- session tokenはJavaScriptから参照できない `HttpOnly; Secure; SameSite=Strict` Cookieで
  同一オリジンへ送る。logout時にサーバー側sessionを無効化し、再起動時に失効してよい。
- サーバーの作業sessionはidentity所有者付きLRUで最大128件とし、操作lockはsession単位とする。
  Project本体は既存 `LocalProjectRepository` のrevision付き原子的JSON保存を用い、
  保存metadataのownerとAccess identityが一致するProjectだけを一覧・読込対象にする。
- Docker serviceの保存rootは `/var/lib/autonavlog` とし、named volume
  `autonavlog-data` をmountする。参照データ、Project、MSM cacheを同root配下へ分離する。
- API応答は `Cache-Control: no-store` とし、CSP、frame拒否、
  MIME sniffing拒否、権限policyを付与する。
- KML/KMZは既存のbounded parserへ渡し、Web境界ではbase64文字数と展開前10 MiB上限を課す。

### D-33 Web画面

- 3段階の進行表示（経路／飛行計画／確認・出力）、入力rail、地図・Leg表、
  準備状況rail、NAV LOG結果を1画面に配置する。
- desktopは1240 px超で3列、tabletは821〜1240 pxで2列、820 px以下は1列とし、
  表は領域内横scrollを許可する。3列表示の地図は320〜900 pxで高さを変更でき、
  pointerとkeyboardの双方で操作できる。tablet/mobileは固定高を維持する。
- KML/KMZのdrop・file選択・KML XML貼付、飛行経路候補の選択、Polygon確認、
  FROM/TO、DATE/ETD、FUEL（既定90 gal）/VAR/QNH（hPa・inHg自動変換）/TGL、
  Leg計画高度/Phase、確認事項、保存・読込、計算、転記補助HTMLをcode-nativeなcontrolで提供する。
  PILOT/SHIPは入力させず、KMLの名称は区切り名またはPoint名を優先し、WPはfallbackに限る。
- drop・file選択・KML XML貼付・KMZ内で選択されたKMLのすべてにFR-20の連結候補生成を
  適用する。複数候補時は未選択から開始し、候補名、Leg数、全長を確認して
  1件を明示選択させる。確定前の地図には選択した連結経路全体を表示する。
- 連結候補を採用したProjectは、直近コンテナのpathを
  `Project.metadata["web_import_container_path"]`、構成LineString名を
  `Project.metadata["web_import_segment_names"]` に保存する。Project／Snapshotの
  `schema_version` は変更しない。
- CLIMB / CRUISE / DESCENT LegはMC 0〜179°で3,500 ftから奇数千+500、
  180〜359°で4,500 ftから偶数千+500の候補を示す。ALTはCLIMBでは上昇先の
  巡航高度、CRUISEではそのLegの巡航高度、DESCENTでは降下開始時の巡航高度を表す。
  任意高度も許可するが候補外は赤い要確認表示とし、
  航空法第82条の900 m閾値と地表高未判定の制約を同時表示する。
  法令根拠は[e-Gov 航空法第82条](https://laws.e-gov.go.jp/law/327AC0000000231?occasion_date=20260423)と
  [e-Gov 航空法施行規則第177条](https://laws.e-gov.go.jp/law/327M50000800056?occasion_date=20260316)
  を参照する。
- 計算成功後はNAV LOGへscrollしfocusを移す。画面上で「WARNING／警告」を見出しに使わず、
  利用者向けには「確認事項」と表示する。
- 日本語fontはWeb assetへ同梱し、実行OSのfont有無に依存しない。
- OpenStreetMap tileは地図背景だけに用い、tile取得失敗でも入力・Issue・表を隠さない。
- 地図確認、Polygon確認、経路確定は地図直下へまとめる。mobileでは飛行計画入力、地図、
  確認、確定の順に下方向だけで完了できるDOM順を維持する。

### D-34 気象・出力の安全ゲート

- 既定の `--weather fake` は画面と計算の開発確認専用とし、
  `DEVELOPMENT_WEATHER_PROVIDER`（BLOCKER）を必ず追加して転記出力を止める。
- 標準の実運用modeは `--weather msm-metar-trend` とし、
  既存の欠損フォールバック禁止とForecast固定契約を維持する。
- 利用者がProject単位でFTDモードを選んだ場合だけ、地上風・5,000 ft風のベクトル補間と
  ISA気温を返す専用WeatherProviderへ切り替える。FTDは開発用fakeと区別し、画面と
  転記補助表へFTD固定気象であることを明示する。
- RJFM/RJFOのmaster場周経路高度はいずれも **1,000 ft MSL** とする。VREPの
  標準高度はProjectで確定した採用場周高度を基準にし、5 NMでは+500 ftとする。
  大分東側1,000 ftなら1,500 ft、西側1,300 ftなら1,800 ftであり、東西を
  滑走路方向だけから自動決定しない。
- 場周経路高度は利用者提供資料の明示値を優先し、全資料で確認できない空港は
  第6.6節の式フォールバック値を正解として `VERIFIED` にできる。今回の同梱14空港は
  この手順による出典・revisionを持つ。
- 転記補助の主表は別添8-1の19列（FROM〜SECT/REM FUEL）に揃え、上段9欄と
  下段INFO・TIME/FUEL欄を持つ。ETO/ATO/ATEは機上実績欄として空欄を維持し、
  表示時にhalf-upで速度1 kt、方位1°（3桁ゼロ埋め）、距離0.5 NM、時間0.5分、
  燃料0.1 galへ丸める。独自PHASE/ALT列やIssue一覧を混在させない。

### D-36 計算済みNAV LOGの安全な入力編集（Issue #20）

- 計算済みNAV LOGでは、既存Project入力へ逆写像できる計画高度、手動気温、手動TAS、
  手動風向・風速だけを編集可能にする。風向・風速は常に一組として検証し、空欄は
  自動値への復帰を意味する。VISUAL_ARRIVALの高度・風・TASは到着規則または固定規則を
  優先するため読み取り専用とする。
- TC、VAR、MC、CAS、WCA、MH、距離、GS、ETE、燃料などの計算値を結果上で上書きしない。
  読み取り専用であることを画面上に明示し、性能・気象のautomatic value、metadata、warning、
  `adopted_source`を維持する。
- 編集値はクライアントで範囲・必須・pairを検証し、入力停止後700 msを目安に自動再計算する。
  更新と計算はsession lock内の単一API操作とし、deep copyしたProjectへの計算が成功した時だけ
  Project、Outcome、Readinessを同時に置換する。検証・通信・計算エラー時は直前の正常な結果を
  表示し続け、失敗したdraftを採用済み結果として扱わない。
- 連続編集はクライアントで直列化し、新しいdraftがあるときは古い予約・応答を表示へ反映しない。
  同じ物理LegがRCA/EOC等で複数segmentへ分割表示される場合も、各欄は同じ`section_id`の
  Project入力を更新する。



### D-35 目的空港・採用場周高度と既定値確認UI
- 経路確定前のTOは端点照合用の候補とし、経路確定後に目的空港を最終確認する。
  確定後だけ100 ft単位の「今回採用する場周経路高度（ft MSL）」Inputを表示し、
  master値を初期表示する。〔目的空港・場周高度を確定〕で `ArrivalPlan` の
  `selected_pattern_altitude_ft_msl` と `selected_pattern_altitude_source` を保存する。
  master一致は `AUTOMATIC`、編集値は `MANUAL` とする。
- 採用場周高度を確定するまでNAV LOG計算を許可しない。目的空港またはInputを変更したら
  再確定を要求する。保存Project読込時は確定値と採用元を復元する。
- 「ALT・Phase・FUEL・VAR・TGLを原資料と照合しました」および同義の既定値一括確認
  Checkbox／記録ButtonはWeb・Colabの双方から削除する。各入力値は画面で直接確認し、
  変更時の再計算は計算入力fingerprintで保証する。`defaults_review_fingerprint` と
  `DEFAULTS_NOT_REVIEWED` は旧Project/Snapshot読込互換のためモデルに残してよいが、
  主UIのreadiness gateでは生成しない。

### Web受け入れ基準

- **W-1**: clean checkoutから `docker compose up -d --build` が成功してserviceがhealthyとなり、
  hostのloopback経由で `/healthz` と `/` が200を返す。Access headerまたは明示した
  trusted local identityなしの `/api/session` は401、認証済みでは200を返す。
- **W-2**: Chromium 1440×1000でKML貼付→飛行経路候補選択→連結経路全体の地図確認→
  経路確定→計算→NAV LOG表示・focusが動作し、JavaScript例外と開発overlayがない。
  複数の連結候補がある場合は未選択から始まり、選択した候補だけが地図・FROM/TO・
  確定後のRouteNodeへ反映される。
- **W-3**: 390×844で計算後の主要操作とNAV LOGが存在し、document bodyに水平overflowがない。
- **W-4**: 未確定の採用場周高度は `PATTERN_ALTITUDE_REQUIRED` で計算を止める。
  確定後は同Issueを解消し、開発用気象のBlockerによりA4転記補助HTMLを止める。
- **W-5**: Cloudflare Published applicationのService URLを
  `http://localhost:8123` としたとき、同一オリジンのSPA/APIとして動作する。
- **W-6**: Docker image build、Compose config、Python統合テスト、ruff、mypy、
  TypeScript typecheck、Vite build、Playwright desktop/mobile試験がすべて成功する。
- **W-7**: 同梱masterはRJFC/RJFE/RJFG/RJFK/RJFM/RJFO/RJFS/RJFT/RJFU/RJOA/
  RJOB/RJOK/RJOM/RJOTの14空港を持ち、全行が `VERIFIED` かつ固有出典・revision付きである。
- **W-8**: 大分で1,300 ftを編集確定すると採用元が `MANUAL`、5 NM基準高度が
  1,800 ftとなる。指定された既定値一括照合CheckboxはDOMにも存在しない。
- **根拠ソース（基準）**: 次の2点で**固定**する（v1.8で改訂。再レビュー指摘: v1.7の「作業ツリーを正とする」は、作業ツリーが変化し続けるため第三者が同じ状態を復元できず、基準として再現不能だった）。
  1. commit `10ea6ec307e88ce5a683b3c6c0a5f83a7218071a`（`Implement NAV2 MVP`）
  2. **基準アーカイブ** `docs/baseline/design-baseline-20260802.tar.gz`（SHA-256: `5ee8a608d6044bdb64e087aaf45fc83517a80a6072ab97a76de1ce5c6af10c86`）。2026-08-02時点の作業ツリーの設計対象ファイル一式（`pyproject.toml` / `README.md` / `src/` / `data/` / `scripts/` / `tests/` / `notebooks/` / `docs/`。本書自身と `docs/baseline/` を除く）を決定論的tar（`--sort=name --mtime=2026-08-02T00:00Z --owner=0 --group=0` + `gzip -n`）で固定したもの
  - **基準の優先順位（v1.9で訂正。再レビュー指摘2: v1.8の「本文と実装が食い違えばアーカイブを正」は逆で、本改修で意図した差分までアーカイブ側へ戻す読み方を許していた）**:
    - **改修前の現状に関する記述**（「現行実装は〜である」「既存の〜が存在する」等の事実記述）が実態と食い違う場合 → **基準アーカイブが正**（本文の記述を訂正する）
    - **改修後のあるべき姿**（FR・D決定・受け入れ基準）と実装が食い違う場合 → **本書（FR/D/受け入れ基準）が正**（実装を本書へ合わせる）
  - **成果物条件（v2.6.0で完了）**: 基準アーカイブと `.sha256` は commit `24058c1`（`Add frozen design baseline archive`）でリポジトリへ固定した。SHA-256は上記記載値と一致する
  - 基準アーカイブは基準commitから次の点で進んでいる（**情報としての変更履歴。規範は上記アーカイブ自体**）: `importers/kml.py` のPolygon解析・保持（第0.5節・FR-15・D-10）／ `presentation/colab.py` の確認付きPolygon Route化・`kml_text` 貼付欄ほか一式のUI（第0.5節）／ `calculation_service.py` のSEA参照と `SAFE_ENROUTE_ALTITUDE_REQUIRED` / `PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE` 生成（第0.2節・A.7）／空港seed 2行と性能データ、性能manifest `VERIFIED`（空港の場周経路高度はv2.5検証未合格。第0.1節）
- 変更履歴:
  - **v2.7.3は、14空港masterと経路確定後の採用場周高度InputをWeb/Colabへ統合した版**。利用者提供7 PDFの明示値を優先し、確認できない空港は第6.6節の式を正解として `VERIFIED` にした。大分はmaster 1,000 ftを初期表示し、西側等では1,300 ftへ編集してProject単位に`MANUAL`保存できる。VREP規則を `CAC_REV19_8_4_9_V4` とし、指定されたALT・Phase・FUEL・VAR・TGLの一括照合Checkboxを削除した
  - **v2.6.2は、訓練利用空港masterをRJFMを含む14空港へ拡張した版**。RJFM/RJFS/RJFTは資料明示値、残る11空港は資料確認後の式フォールバック値を採用した
  - **v2.6.1は、目的空港の場周高度を利用者提供資料優先・確認不能時は式フォールバックの順で決定する規則を確定した版**
  - **v2.7.2はPR #2レビューを反映した版**。Cloudflare Access identity所有権、HttpOnly Secure Cookie、session単位lock、KML名称保持、VFR高度候補、QNH単位変換、別添8-1の19列・無丸め出力、計算後focus、Docker build成果物分離をD-31〜D-34とW-1〜W-6へ追加した
  - **v2.7.1は、Web版の主実行方式をhost `.venv` からDocker serviceへ変更した版**。multi-stage build、非root runtime、read-only root、loopback限定port、named volume、health check、restart policyをD-30〜D-32とW-1/W-6へ追加した
  - **v2.7.0は、主配布をColabからローカルWeb版へ変更した版**。React/Vite + FastAPIの同一オリジン構成、loopback bind、Cloudflare Tunnel + Access、Web session、ローカルProject保存、desktop/mobile受け入れ試験をD-30〜D-34とW-1〜W-6に定義した。航法計算とフェイルクローズ契約はv2.6.0を維持する
  - **v2.6.0は、SEAおよび陸域マスクを現バージョンの対象から除外した版**。DEM10B取得、SEA自動算出・手入力・確認、ALTとの比較、SEA列・清書出力、陸域マスク、DEMのDrive二次cache、関連fixture・実測ゲート、`numpy` / `Pillow` の追加を実装しない。既存schemaの `safe_enroute_altitude_ft_msl` は読込互換のため残してよいが、新規Projectでは未設定とし、航法計算・fingerprint・Issue・ProjectStatus・転記補助HTMLへ使用しない。将来SEAを別表として追加する場合は、現NAV LOG計算から独立した新しい要求・データ契約として再設計する。`docs/baseline/` は commit `24058c1` で固定済み。内部UI状態は `state_schema_version=4`、Project/Snapshot本体は `schema_version=1` を維持する
  - **v2.5.3は、VREP基準高度の最終訂正を反映した版**。(1) 目的空港の `elevation_ft_msl` を100 ft単位でhalf-upする（490 ft→500 ft、19 ft→0 ft）、(2) 丸めた空港標高へ1,000 ftを加えて教範4-3の場周経路高度（AGL+1,000 ft）をMSLへ展開し、さらに教範4-4・8-4-9(2)どおり500 ftを加えて5 NM VREP高度を得る、(3) 5 NM境界±1 mおよび5 NM超過距離の整数NM half-up×200 ft/NMは維持する、(4) masterの場周経路高度は事前参照情報として別に保持・表示する。したがって本式を「教範と異なる」とするv2.5.2の記述は撤回する。`ARRIVAL_ALTITUDE_RULE_VERSION` は `CAC_REV19_8_4_9_V3` とする。内部UI状態は `state_schema_version=3`、Project/Snapshot本体は `schema_version=1` を維持する
  - **v2.5.2は、VREP基準高度に関する途中訂正を反映した版（**1,000 ft単位丸めと教範差異の解釈はv2.5.3で撤回**）**。(1) 距離は引き続きVREPから目的空港ARP座標までのWGS84距離を用いる、(2) 高度基準は場周経路高度ではなく、目的空港の `elevation_ft_msl`（利用者のいうARPの空港標高）を1,000 ft単位でhalf-upし、+500 ftする、(3) 5 NM境界±1 mおよび5 NM超過距離の整数NM half-up×200 ft/NMは維持する、(4) 場周経路高度は必須の参照情報として保持するが自動VREP高度の算式には使用しない、(5) 教範8-4-9(2)の場周経路高度基準とは異なる利用者指定のアプリ規則であることを画面・転記補助HTMLへ明示する。`ARRIVAL_ALTITUDE_RULE_VERSION` は `AUTONAVLOG_AIRPORT_ELEVATION_V3` とする。内部UI状態は `state_schema_version=3`、Project/Snapshot本体は `schema_version=1` を維持する
  - **v2.5.1は、VREP端数規則とLoss Timeの利用者決定を反映した版**。(1) VREPは目的空港ARPまでのWGS84距離を用い、5 NM境界±1 mを5 NM扱いとする。高度の基準はARP座標や空港標高ではなく、目的空港に保持した場周経路高度を100 ft単位でhalf-upし、+500 ftする（**この基準高度案はv2.5.2で廃止**）。5 NM超過距離を1 NM単位でhalf-upして整数化し、200 ft/NMを加える規則へ確定した（第6.6節・D-26）、(2) Loss Timeは地上計画値ではなく、飛行中に実Time Checkと実測値を使って事前計算結果を修正する値と確定した。地上計画のForecast・ETE・燃料・fingerprintへLossを一切入れず、入力UIも廃止する（第6.5節・D-25）。内部UI状態は `state_schema_version=3`、Project/Snapshot本体は `schema_version=1` を維持する
  - **v2.5.0は、第8章 NAV2（宮崎課程）との照合結果と利用者決定を反映した版**。(1) 成果物の目的を「KMLからNAV LOGの**地上準備**を完了する」に限定し、ATO/ATE等の実績欄および実発動時刻を基準にするETOは機上記入のため空欄とした。本文中の「印刷可能」は完成NAV LOGの適合宣言ではなく、別添8-1へ書き写す転記補助HTMLを出力できる意味へ統一した（第1章・D-23）、(2) DEM由来SEAを障害物未考慮の**参考値**として使う方針を明記し、確認・印刷ゲートも規定SEAの保証や障害物照合の証明ではないことを明確化した（第0.2節・第6.2節・第12章・D-24）、(3) 教範のETO/Loss Timeを「実Time Checkを基準とし、通常経路ETEとは別に発生した遅延を加える」と定義し、ZONE/CUM ETE・TTL TIMEはLossを除外、時刻timelineだけにLossを加える二系列へ分離した（**この案はv2.5.1で廃止**。第6.5節・A.2・D-25）、(4) VREP通過高度を空港ARPからのWGS84距離と場周経路高度から自動算出し、5 nm付近は+500 ft、5 nm以遠は1 nm当たり+200 ftとした。端数はWGS84実距離から連続計算後に100 ft切上げる案を仮置きし、Direct Base等は理由付き手動overrideとする（第6.6節・FR-40・D-26。この仮置き式はv2.5.1で廃止）、(5) 経路外CPをRouteNodeへ混入させず、有限Leg上のWGS84最近点をabeam点としてZONE/CUM DISTを分割する契約を追加した（第6.7節・FR-41・D-27）、(6) 空港・地点・CPをmanifest付き参照データパックとして分離し、差し替え・CRUD・版戻しを可能にした。選択行はProjectへsnapshotし、active packの変更を既存Projectへ自動反映しない（第6.8節・FR-42・D-28）、(7) NAV2宮崎課程では「風を予想しない」モードを提供せず、風欠損をBlockerとする現行方針を維持した（D-29）。内部UI状態は `state_schema_version=3` とし、Project/Snapshot本体の `schema_version=1` は維持する
  - **v2.4.1は v2.4 への再レビュー指摘6件を反映した版**。(1)[critical] `PersistedSeaState.adopted_source` に既存 `AdoptedSource` を用い、自動・手入力の確認を実際の `safe_enroute_altitude_ft_msl` と採用元へ結び付け、採用元切替を反対側確認の解除と同一トランザクションにした（第3.2節・第10.3節・第10.5節・C-62）、(2)[major] 404を含む提案へ最短 `negative_cache_expires_at_utc` を保存し、期限到来を `proposal_stale` の時間依存条件へ追加した（第8.7節・第8.9節・第10.5節・C-63）、(3)[major] C-52/C-58/C-60/C-61を `pack.zip`＋`ready.json` の世代ディレクトリ方式へ全面更新し、live読込の4者一致・S-4の3者一致、pack identity、token最終tie-breakを規定した、(4)[major/security] DEMパック専用の数値上限・許可path・固定ZIP属性・`ZIP_STORED`限定・manifest/index schema・bounded central-directory preflight・dirfd/`O_NOFOLLOW`読込・GC容量式をDP-1〜DP-5として新設し、KML用K-3〜K-5の誤参照を除去した（第8.9節・C-64）、(5)[major] `derive_project_status()` を `effective_issues` と複合 `ack_key` に統一し、Project/Outcome/Snapshotのstatusを同じmaterialized projectionとした（第6.3節・E-26）、(6)[medium/schema] `SeaProposal` と構成型の完全なPydantic/JSON契約、およびplain canonical fingerprint payloadを定義した（第8.7節・第10.5節・C-65）。内部UI状態は `state_schema_version=2` とし、Project/Snapshot本体の `schema_version=1` は維持する。既知ゲートはA.10・B.1・基準アーカイブ固定に加え、B.2aのDrive FUSE primitive実測を実装handoff条件へ明示した
  - **v2.4は、レビュー指摘への対応ではなく「不要な複雑性の除去」として行った版**（v2.3〜v2.3.2 で第8.9節の公開モデルが構造的に安定したのを機に、他の箇所にも同じ観点を適用した）。`unknown_mask_fingerprint` を `SeaProposal` と `sea_proposal_fingerprint` から**削除**した（第8.7節・第10.5節・C-59）。`unknown` 分類は第8.4.3節の判定規則により（画素ごとのDEM有効/欠損、陸域マスクの true/false/uncertain、マスク利用可否）の純関数であり、その入力はすべて `tile_content_fingerprint`・`land_mask_version`・`geometry_fingerprint`・`corridor_nm`・`failsafe_reasons` として**既に `sea_proposal_fingerprint` に含まれていた**。したがってこの項は確認keyとしての識別力を一切足さず、Legあたり最大 65,536 B × 1,200 タイル ≒ 78 MB の追加hashと、実装間で一致させるバイト水準契約（dtype・C-order・framing・固定ベクトル）のみを要していた。10.5節のバイト規約と固定テストベクトル、C-59(a)(b) を対応分だけ縮小し、削除しても識別力が落ちないことの確認をC-59(b2)として追加した。`unknown_pixels`（カウント）と `unknown_regions`（利用者への提示用）は維持する。
  - **v2.3.2は v2.3.1 への再レビュー指摘5件を反映した版**。(1)[major] no-clobberを**真に原子的な primitive** へ置換。`os.path.lexists` + `os.rename` は check-then-act であり、確認とrenameの狭間に宛先が現れればPOSIX renameが上書きするため契約を満たせなかった。`renameat2(RENAME_NOREPLACE)` はFUSE上の可用性が保証できないため、**本節のロック機構が既に前提としている `os.mkdir` の原子性**へ一本化し、パックの単位を「世代ファイル」から**世代ディレクトリ** `{pack名}.g{generation}.{token}/`（`mkdir` で予約 → 中に `pack.zip` → 完成marker `ready.json`）へ変更した。readers・S-4は `ready.json` を持つディレクトリのみを候補とする。`mkdir` 原子性への依存を**脅威境界として明示**し、B.2の実測項目に加えた（第8.9節・C-61(b)）、(2)[major] 2 GB上限の契約を**正直な表現へ訂正**。並行writerが同じ空きを観測すれば超過しうるため、無条件のhard limitは保証できない。「単一writerにはhard limit、並行writerにはbest-effort」とし、一時超過の上限（並行writer数 × 1パック最大サイズ）と収束経路（publish後GC・次回publish前GC・起動時GC）を明記。あわせて容量判定を**完成ZIPをローカルに構築した後**へ移した（圧縮後サイズは作るまで不明のため。第8.9節G1〜G6・C-60）、(3)[major/schema] `size_bytes` を index entry・`ready.json`・P6・S-4・Q1のschema検証へ追加。Q2が「記録サイズとの不一致でcache miss」とする一方、index entryの定義に `size_bytes` が無かった（第8.9節・C-58・C-61）、(4)[minor] 同一 `generation` の優劣を**liveはlast-wins、S-4再構築はcontent hash辞書順の決定的選択**と書き分けた（liveのP6はentryを無条件差替えするため、hash tie-breakが効くのは走査から作り直すS-4だけ。どちらも安全性には影響しない。第8.9節）、(5)[minor/security] Q1のディレクトリ名regexで z10座標を **0..1023** に限定（負数・無制限桁を排除）、root配下判定を文字列prefixから `commonpath` へ、Q2のローカルtempにも同じサイズ上限を適用し成功・失敗いずれの経路でも `finally` で削除、と明記（第8.9節・C-61(d)(e)）。既知ゲート（基準アーカイブのcommit・A.10・B.1）は未変更。
  - **v2.3.1は v2.3 への再レビュー指摘5件を反映した版**（公開モデルの骨格は維持し、その内側の穴を塞いだ）。(1)[major] `generation` の再採番を廃止。v2.3はP1で仮採番しP3で実値へrenameする規定だったため、ZIP内 `manifest.generation` が仮値のままファイル名とindexが実値になり三者不一致となり、書き換えれば「P2後は不変」契約に違反した。generationはadvisoryなのでP1で確定した値をファイル名・manifest・index entryへ通し、**採番し直さない**。同世代の並行publishは content hash の tie-break で決まる。ファイル名・manifest・index entryの**三者一致**を世代ファイルの自己整合性条件としQ3・S-4で検証（第8.9節・C-58）、(2)[major/security] 読みのTOCTOUを閉塞。Drive上ファイルは外部から差替え可能と本文自身が定めているため、path検証後に再openする形では未検証ZIPを展開できた。Q2でサイズ上限つきに**一度だけローカルtempへコピー**し、以後の whole-file hash・K-3〜K-5・manifest hash・展開を**すべて同一bytes**に対して行う。index entryのファイル名もbasename・厳密regex・pack座標一致・UUID/hash形式・realpathのroot配下としてschema検証する（第8.9節・C-61）、(3)[major] 「古い正当世代＝cache miss」は**正のエントリにしか成立しない**ことを是正。30日TTLの404負キャッシュは、索引が古い世代へ巻き戻ると期限切れの404を有効なhitとして再利用しえた。`.missing` へ `fetched_at_utc` / `expires_at_utc` をmanifest hash対象として持たせ、Q3・Q4で期限切れを常に「エントリ無し」扱いにする。正のエントリは `dem_cache_version` 一致を必須化（第8.9節「negative entryの期限」・C-25・C-52(b)）、(4)[major] 1時間猶予と2 GB上限の優先順位を確定。**2 GBをhard limitとし猶予に優先**。P0で容量を確保し、通常GCで足りなければ猶予前の未参照世代も緊急GCで削除、それでも確保できなければDriveへのpublishをスキップして一次キャッシュのみで継続する（G1〜G4。第8.9節・C-60）、(5)[minor] P1のパック構成を「索引が指す検証済み世代をbaseに一次キャッシュの新規・更新を重ねた全体（同一エントリは一次キャッシュ優先、期限切れ `.missing` は除外）」と確定しsubset publishを禁止、P2の rename を**no-clobber契約**（宛先が既存なら一切触れず新tokenで再試行）として明記（第8.9節・C-61）。既知ゲート（基準アーカイブのcommit・A.10・B.1）は未変更。
  - **v2.3は v2.2 への再レビュー指摘5件を受け、第8.9節のDriveキャッシュ公開モデルを「固定名canonicalの上書き＋fencing token＋conflict marker」から【不変世代ファイル＋索引ポインタ】へ全面置換した版**。指摘(1)(2)により、v1.8以降の漸進的補強では次の2点が**原理的に解けない**ことが確定したため（(1) 手順7と7bの間に隔離されると、旧writerは後継writerのlockへ根拠を書いてしまい、検査を増やしても窓が移動するだけ。(2) 「stale writerは後から `os.replace` しうるので証跡を時間で無視するな」と「resolver・起動時掃除が証跡を除去する」は両立せず、強制終了できないプロセスはfencingできない）、**上書きされる可変オブジェクトそのものを無くす**方針へ切り替えた（指摘(2)が提示した選択肢のうち後者「immutable世代ファイル＋現ownerだけが更新できる選択情報」を採用）。パックは `{pack名}.g{generation}.{token}.zip` の一意名で一度だけ rename 出現し以後不変、可変なのは `index.json` のポインタ1つだけ。これにより**あらゆる競合の最悪結果が「古いが正当な世代の選択」＝cache missへ収束**し、破損が構造的に発生しない。結果として次をすべて削除した: 4点比較／pack単位ロック／replace前後のfencing token確認（手順7・9）／`publishing.json`（手順7b）／`{pack名}.conflicted`・`index.conflicted` と reader fail-close（seqlock読み）／marker解決手順R1〜R8／エントリ単位merge／`_conflict_`・`_locktimeout_` 別名ZIPと `pending.json` pending契約。`index.lock` は**advisory**（直列化による無駄な上書き合戦の抑制のみ。取得失敗でもpublishを中止してよい）へ格下げし、`generation` の単調増分もadvisoryとした。指摘(3)(4)(5)は対象機構の消滅により解消（pending昇格の迂回・`pending.json` の一時的な上限破れ・r1/r3/r5のbaseline更新は、いずれも該当機構が存在しない）。受け入れ基準はC-52を(a)〜(e)へ全面改訂、C-58を索引の一貫性と再構築へ改訂、C-60を世代ファイルのGC・容量の有界性へ改訂。既知ゲート（基準アーカイブのcommit・A.10・B.1）は未変更。
  - **v2.2は v2.1 への再レビュー指摘5件（critical 2・major 3）を反映した版**。(1)[critical] token喪失検知後の復旧を**merge廃止・fail-safe破棄**へ全面改訂（v2.1の「本名をconflict版へ複製して次回mergeする」は、複製できるのが自分の上書き版のみで、上書きで失われた版を含む復旧集合を構成できず、A+Aを「正常merge」としてmarkerを消せた）。手順9はmarker作成のみを行い複製せず、解決はcanonicalとpending候補の破棄→再取得とする（第8.9節手順9・「次回起動時の解決」・C-52(c)）、(2)[critical] readerのfail-closeを**常時成立**へ強化。手順7と手順8の間のpublish窓を、writer自身が `os.replace` の**前に**書く `lock/publishing.json` として可視化し（手順7b）、隔離renameがこれを窓ごと捕捉するため「手順7bから手順9正常完了まで、そのwriterの `publishing.json` が `.lock/` か `.lock.stale.*/` に必ず存在する」という不変条件が成立する。stale replaceは必ずこの区間の内側で起こるため、fail-closeの根拠が常にreplaceより先に存在する（外部の観測者が競走に勝つ必要がない）。読み側は `.lock.stale.*/` を含むpublish窓集合と `{pack名}.conflicted` を展開の前後で確認する（seqlock相当）（第8.9節手順4c・7b・「conflict markerとreaderのfail-close」・C-52(d)）、(3)[major] marker除去のABA競合を解消。固定path markerの無条件除去を禁止し、resolver専用pathへのrename→内容再確認→自分が移したものだけ削除、という解決手順R1〜R8をpack・index両方へ規定（第8.9節「markerの解決手順」・C-52(e)・C-58）、(4)[major] 別名ZIPの回収・容量契約を新設。`pending.json` へのdurable登録＋registryに依らないpattern走査、canonical不在時のみの昇格（merge禁止）、TTL 7日・pack当たり3件・全体50件/512 MB、2 GB容量上限への算入を規定（第8.9節「別名ZIPのpending契約」・C-60）、(5)[major] Route上限超過の「全Leg `NOT_COMPUTED`」を**今回の試行のセッション表示**と明示し、`PersistedSeaState` を上書き・無効化しないことを確定（第8.8節・C-56）。既知ゲート（基準アーカイブのcommit・A.10・B.1）は未変更。
  - v1.6は v1.5 へのレビュー（16件の設計矛盾・14件の追加修正・15件の受け入れ基準不足・未決事項6件の推奨処理）を反映した版。「実装ゲート」と「リリースゲート」を分離し、fingerprintベースの陳腐化判定・統合Issue判定・手入力SEA全経路対応・海岸線安全側処理・長Leg投影誤差対策・非同期ジョブ世代管理を新規に規定した。
  - さらにv1.6への再レビュー指摘5件を反映済み: (1) SEA fingerprintを「入力fingerprint」「提案fingerprint」「手入力確認fingerprint」の3種へ分離（第10.5節）、(2) 警告承認の保存先を `acknowledged_warning_codes` へ単一化し三重化を解消（第3.2節・第6.3節）、(3) Snapshotは `CalculationOutcome` 取得後のみ可能と既存契約に合わせて修正（第6.4節）、(4) fingerprintの共通規約（canonical JSON＋SHA-256）を新設し組み込み `hash()` の不安定性を解消（第10.1a節）、(5) FR-16を入力Section一覧と結果テーブルに分離しB1導線との矛盾を解消。
  - **v1.7は v1.6 への再レビュー指摘11件を反映した版**。(1) 手入力SEA確認を `manual_value_ft` を含む `sea_manual_confirmation` fingerprintへ紐づけ（第10.5節）、(2) Issueの同一性・承認キーを `issue_cause_fingerprint` / `issue_ack_key` として単一定義し `dedupe()` と `acknowledged_warning_codes` で共用（第6.3節）、(3) 計算入力fingerprintの対象payloadを全列挙（第10.2節）、(4) `normalize()` を実装可能な水準まで規定し固定テストベクトルを掲載（第10.1a節）、(5) `validation_status` の許容集合と印刷可否を確定（第0.1節・A.3a）、(6) `corridor_nm` / `margin_ft` をSEA policy定数へ統一（第8.1節・第10.2節・第10.3節）、(7) `SeaJobContext` を `SeaJobState` から分離しメインスレッド単一処理を規定（第8.7節）、(8) 海岸線バッファのpolygon抽出bboxを `d_coast_m` 拡張（第8.4.2節）、(9) HTTP timeout / job deadline を数値契約化（第8.10節）、(10) 根拠ソースを基準commit＋作業ツリーとして固定しPolygon契約を現行機能維持へ更新（本節冒頭・第0.5節・FR-15・D-10）、(11) 第0.1節のデータ状態を作業ツリーの実測値へ更新し欠落時要件を一般則へ書き分け。
  - **v1.7で新たに判明した基準ソースのずれ**（再レビュー指摘10の派生。レビュー指摘外だが同種の欠陥のため併せて修正）: 作業ツリーの `calculation_service.py` は既にSEAを参照しBlockerを2件生成している。v1.6までの「SEAは計算で参照されていない」（第0.2節・A.7）は基準commitに対しては真だが作業ツリーに対しては偽であり、新規コード `PLANNED_ALTITUDE_BELOW_SEA` は既存 `PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE` の重複定義になっていた（第0.3節「重複定義の禁止」違反）。第0.2節・第0.4節・FR-34・A.7・A.9を修正した。
  - **v1.8は v1.7 への再レビュー指摘13件を反映した版**。(1) 第2.1節のKMLスコープをFR-15と整合（エラー対象はskipされた未対応surfaceのみ）、(2) 根拠ソースを基準アーカイブ＋SHA-256で再現可能に固定、(3) KML貼付欄を既存 `kml_text` の再配置に統一し二重Textareaを禁止（FR-1・FR-14）、(4) 陸域マスク利用不可時もDEM有効値は `land`（第8.4.3節）、(5) `IssueContext` を型定義し `dedupe(effective, ...)` / `issue_ack_key(issue, ctx)` へ統一、計算Issueは `calculated_against_fingerprint` に固定（第6.3節）、(6) ETDの解釈規則を単一化しfingerprintへdatetimeオブジェクトを渡す規約に（第10.3節・A.1）、(7) `sea_proposal_fingerprint(section, proposal)` へ署名修正（第10.5節・第6.3節）、(8) DriveパックのTOCTOU対策としてロックプロトコルを新設（第8.9節）、(9) Polygon穴のラスタ化を「Polygon個別マスク→OR合成」へ修正（第8.4.3節・C-39）、(10) タイル取得を共有deadline方式へ修正し試行回数の意味を固定（第8.10節）、(11) `PERFORMANCE_DATA_UNVERIFIED` を新規コードとして統一（第0.1節・A.3a）、(12) 性能manifestの計算影響フィールドを含む `performance_fingerprint` と `airports.csv` の行内容hash `airport_data_fingerprint` を導入（第10.2節）、(13) Polygon外周の選択後座標上限 `max_coordinates_in_selected_polygon_outer` を新設（K-10・FR-15）。受け入れ基準 C-51〜C-53・E-22・F-16・K-12・A-7 を追加し、C-39・E-21 を改訂した。
  - **v2.1は v2.0 への再レビュー指摘6件＋補足2件を反映した版**。(1)[critical] pack書き戻し手順9のtoken喪失検知後の契約を「conflict版への複製（複製元は読み戻し検証済みの本名ファイル）＋persistent conflict marker `{pack名}.conflicted` の作成＋当該packのindex更新禁止」へ強化し、readerのfail-close（marker存在時はcanonical不使用・cache miss扱い）と次回起動時のエントリ単位決定論的merge／破棄・再構築を新設（第8.9節・C-52(c)・C-58）、(2) `tile_content_fingerprint` / `unknown_mask_fingerprint` のバイト水準の算出規約（hashアルゴリズム・タイルの決定的順序・404/fetch_failed marker・maskのshape/dtype/C-order・framing）と固定ベクトルを確定し、`sea_proposal_fingerprint` へ `failsafe_reasons` / `warnings` を追加、`SeaProposal.fingerprint` を「算出完了時に評価した保存値（同値契約・判定には不使用）」と定義（第10.5節・第8.7節・C-59）、(3) 拡張陸域ポリゴン（`d_coast_m` buffer・差分）はA.10のoffline生成手順で事前生成し同梱すると明確化し（実行時はPILラスタ化のみ）、生成ツールの版固定（Shapely/GDAL等。アプリ実行時依存には加えない）・CRSとbuffer方式・座標丸め・穴/MultiPolygonの扱い・固定test vectorをA.10生成手順の確定必須項目へ追加（第8.4.2節・A.10）、(4) `SeaJobContext.excluded_section_ids: frozenset[UUID]` を型定義し、除外Legの guard／preflight／表示／印刷判定（過去の有効な確認は保持、stale・未算出は手入力SEAまたは除外解除）を確定（第8.7節・第8.8節・C-56）、(5) F-14を「dedupe検証（Blocker例: 1件に畳まれ原因解消まで残る）」と「承認共通性検証（要承認Warningの二重producer fixture）」へ分離。v2.0のBLOCKER承認例は不成立だった（第14章）、(6) `normalize()` のDecimal対応を除去しTypeErrorへ（float経由の丸めで精度を失っていた。現行入力にDecimalは無い。第10.1a節・E-20）。補足対応: lock関連一時path（stale隔離・release・locktimeout・conflict）のtokenをfull UUIDへ統一、`.lock.release.*` を起動時掃除・容量制限対象外へ明記（第8.9節）。
  - **v2.0は v1.9 への再レビュー指摘11件（CodeRabbit由来6件＋手動照合5件）を反映した版**。(1) pack lockの解放を「owner token確認→自token専用release pathへのrename→再確認→削除」の所有権付き解放へ全面改訂し、token喪失時は現行lockへ一切触れない契約とした（第8.9節・C-52）、(2) `index.json` 更新へpack lockと同じfencing token手順（取得〜所有権付き解放）を適用し、`manifest.json` の `revision`・`index.json` の `generation` のschema契約（型・初期値・単調増分・期待値取得時点・破損時初期化）を新設（第8.9節・C-58）、(3) lock取得timeout時の動作（本名を上書きせず `_locktimeout_` 別名へ退避・警告・索引不更新）を本文へ明記しC-52と整合（第8.9節）、(4) `IssueContext` を再帰的に不変な値（凍結identity＋正準JSON文字列＋構築時確定のキー）へ改め、`create_effective_issue()` を唯一のfactoryとした（第6.3節・F-17）、(5) Route上限超過からの復帰を「SEA算出対象からの除外（Route・NavSection非変更）＋再preflight」として確定（第8.8節・C-56）、(6) 無効化表へSEA確定値変更・`dem_cache_version`・タイル上限の行を追加（第10.3節・C-57）、(7) E-23(a)を「Snapshot読込」ケースへ、E-23(c)/F-16を「入力fingerprintまたはcause metadataが変わった新outcomeの場合のみ再承認」へ訂正（第14章）、(8) `cause` 構成の個別規則をcode別・producer非依存へ改めF-14のcross-source dedupeを成立させた（第6.3節）、(9) S-9/E-24の「未算出」を「一度も確定結果がないLegのみ」に限定（第13.2節・第14章）、(10) performance実測digestを起動時・明示再読込時は必ず再hashする契約へ強化（第10.2節・E-25）、(11) 実装開始条件の表現を「文書内容の未決はA.10/B.1、実装handoffには加えて基準アーカイブの固定が必須」へ2箇所とも訂正（本節・第15章）。
  - **v1.9は v1.8 への再レビュー指摘15件を反映した版**。(1) pack lockのstale回収をquarantine rename＋fencing token方式へ全面改訂（第8.9節）、(2) 基準の優先順位を「現状記述はアーカイブが正・あるべき姿は本書が正」へ訂正（本節）、(3) `index.json` 更新を専用lock内の不可分操作へ（第8.9節）、(4) SEAジョブ結果のguard順を短絡評価列へ固定し、1件の例外でqueue処理を止めない（第8.7節）、(5) `IssueContext.cause` を生成時にnormalize＋deep freezeし指紋を生成時に確定（第6.3節）、(6) `dedupe` の集約規則を完全定義（第6.3節）、(7) outcome由来ctxの再起動・Snapshot再構築契約を新設（第6.3節・E-23）、(8) S-9の正規化対象を永続モデル上で定義（第13.2節・E-24）、(9) `sea_input_fingerprint` へ `dem_cache_version`・実効タイル上限を追加（第10.5節）、(10) S-3の読込側拒否を `parse_constant` 方式で明記（第13.2節）、(11) DEMデコードのdtype契約を追加（第8.3節）、(12) C-7を `unknown_pixels == 0` に限定しC-38と整合、(13) datetimeのaware判定へ `utcoffset() is not None` を追加（第10.1a節）、(14) `performance_fingerprint` の `table_sha256` を実CSV bytesからの実測digestへ変更（第10.2節）、(15) 基準アーカイブのcommitを成果物条件として明記（本節）。

> **現行規範の優先順位:** 方位計算、Golden表示、目的地TAF風はv2.7.5の
> D-40〜D-42／W-13〜W-16を最優先する。更新確認はv2.7.4のD-39／W-12、配布方式と
> UI実行形態はv2.7.1のD-30〜D-34／W-1〜W-6を適用する。航法計算・データ・安全ゲートは
> v2.6.0本文を維持する。v2.5.3以前のSEA・DEM・陸域マスク関連記述は履歴・現状説明としてのみ残す。

## 本書の位置づけと実装開始条件

本版は、KML/KMZからNAV LOGの地上準備用転記補助HTMLを作る改修だけを対象とする。SEA、DEM、陸域マスクおよびそれらの保存・確認・キャッシュ機構は対象外である。

```
実装開始条件

1. 改修対象UI（D-30）が `autonavlog.web` と `web/` の同一オリジンWebアプリに確定している
2. VREP高度・Loss Time・CP abeam・参照データ方式（D-25〜D-29）が確定している
3. 基準アーカイブと `.sha256` がリポジトリへ固定されている
4. 本書 `DESIGN.md` がcommitまたは不変artifactとして実装者に固定提供されている
```

1〜4は充足済みである。基準アーカイブは commit `24058c1` に固定し、本書は実装変更と同じcommit／PRで固定提供する。旧版で未完了だった陸域マスク（A.10/D-16）、SEA fixture（B.1/D-15）、Drive FUSE primitive実測（B.2a）は、依存するSEA・DEM二次cache自体を本版から除外したため実装開始条件ではない。

```
転記補助出力のリリース条件（実装開始条件とは別）

1. 選択経路に必要な空港・地点・CP参照データを整備し、目的空港の実運用場周経路高度を一次資料で検証する
2. 性能データのmanifestが `validation_status == "VERIFIED"` で、記録hashと実CSVが一致する
3. 非SEA機能の受け入れ基準（第14章）を満たす
4. アプリバージョンを `1.1.0` として `pyproject.toml` に反映する
```

`Project.schema_version` と `CalculationSnapshot.schema_version` は既存の `1` を維持する。既存のSEA関連fieldは読込互換のため残してよいが、本版の計算・状態・表示・出力には使用しない。

---

## 0. 現行実装の前提と重大な制約

### 0.1 同梱データの現状（v1.7で更新）

**v1.6までの本節は「同梱データが空である」を前提としていたが、これは基準commit時点の状態であり作業ツリーとは一致しない**（再レビュー指摘11）。v2.7.3作業ツリー（2026-08-12）の実測値は次のとおりである。

| ファイル | 実測値（2026-08-12 作業ツリー） |
| --- | --- |
| `data/reference/default/airports.csv` | **14件**（RJFC/RJFE/RJFG/RJFK/RJFM/RJFO/RJFS/RJFT/RJFU/RJOA/RJOB/RJOK/RJOM/RJOT）。全行にAIP由来ARP座標・標高、場周高度、固有出典・revision、`VERIFIED` を記録済み |
| `data/performance/climb_time_fuel_distance.csv` | **36行**（原表19節点を500 ft刻みに線形補間、ヘッダ除く） |
| `data/performance/cruise_performance.csv` | **159行**（ヘッダ除く） |
| `data/performance/manifest.json` | `validation_status: "VERIFIED"`、上昇温度・巡航3軸補間Policy、Issue #15添付3件のURL/SHA-256、両CSVの `sha256` 記載済みで実ファイルと**一致**、`source_page` 記入済み |

したがって「同梱データが空で転記補助出力へ到達できない」という v1.6 の前提はもはや成立しない。場周経路高度は、提供資料の明示値または第6.6節の式フォールバックで検証したmaster値を初期表示し、Projectで確定した採用値を自動VREP高度算式へ使う。**リリース条件（第1章）と `PERFORMANCE_DATA_UNVERIFIED` の要件は削除しない。** 配布物・別環境・データ差し替え時には再び未整備・未検証となりうるため、以下は**データ状態に依存しない一般的なフェイルセーフ要件**として規定する。

> **一般則**: UIは同梱データの充足状況を起動時に検査し、不足・未検証・改ざんを検知した場合は印刷を止める。「整備済みであること」を前提に検査を省略してはならない。

#### 要求

- **FR-36**: 起動時にデータの充足状況を検査し、**不足している場合に限り**ステータスバーの最上位に表示する。KML入力より前に提示する。充足している場合はデータ版（`source_revision` / `performance_table_version`）を通常表示する。

```
⚠ 選択経路の参照データまたは性能データが未整備のため、NAV LOGの地上準備を完了できません。
  現在できること: 経路の取込・入力内容の確認・下書き保存
  できないこと: 不足データを使う計算、転記補助HTMLの出力
```

- 上記の状態でも、経路取込・入力内容の確認・下書き保存は動作すること
- 受け入れ基準は「データ整備前に判定できるもの」と「整備後にのみ判定できるもの」に分ける（第14章）。**データが整備済みの現状では14.2の大半が判定可能になっている**が、章構成は配布物ごとの再判定のために維持する
- **空のCSVを仮の値で埋めてはならない**（現行READMEの明示的禁止事項）

#### データ充足の判定条件（詳細）

CSVが空でないことのみを「充足」の条件としない。最低限、次を検査する。

| データ | 検査項目 |
| --- | --- |
| Reference data | active packのmanifest/hash/schemaが妥当／ID一意／緯度経度が有効範囲内／FROM・TO・VREP・CPの選択行を解決可能／目的空港の `elevation_ft_msl` が有効かつ出典確認済み／`pattern_altitude_ft_msl` が実運用値として出典確認済み |
| Performance | climb・cruise双方が1件以上／`manifest.json` のhashと実ファイルが一致／`validation_status` が下表の許容集合に属する／`aircraft_profile_id == "SR22_G6"` を解決可能／必要な重量・高度・温度範囲が揃っている |

##### `validation_status` の許容集合と印刷可否（v1.7で確定）

v1.6は「UNVERIFIED以外」（第1章）・「許可された状態」（本節）・「UNVERIFIEDのみBlocker」（下段）と三様に書いており、**未知値や将来の拒否状態を素通しさせる欠陥があった**（再レビュー指摘5）。現行 `PerformanceManifest.validation_status` は**制約のない `str`**（既定 `"UNVERIFIED"`）であり、`is_verified` は `"VERIFIED"` 完全一致、リリース検証も `"VERIFIED"` 完全一致である。これに合わせて許容集合を次のとおり**閉じた集合**として定義する。

| `validation_status` | 意味 | 印刷可否 | 起動時のデータ充足表示 |
| --- | --- | --- | --- |
| `VERIFIED` | 一次資料と突合済み | **可**（他の条件を満たす場合） | 充足 |
| `UNVERIFIED` | 未突合（既定値） | **不可**（`PERFORMANCE_DATA_UNVERIFIED`／BLOCKER） | 充足（値自体は検査に通るため） |
| `PENDING` | 突合作業中 | **不可**（同上） | 充足 |
| `REJECTED` | 突合の結果、誤りが確認された | **不可**（同上・BLOCKER） | **不足**として最上位表示 |
| 上記以外の任意の文字列（未知値・空文字） | 解釈不能 | **不可**（同上・BLOCKER） | **不足**として最上位表示 |

- **印刷可となるのは `VERIFIED` の場合のみである。** 「UNVERIFIED以外なら可」ではない（第1章のリリース条件2の表現もこれに合わせて修正した）
- `manifest.json` の `sha256` と実ファイルのハッシュが不一致の場合は、`validation_status` の値によらず**常にBLOCKER**とする（改ざん・差し替えの検知）
- Issueコードは**本改修で新設する** `PERFORMANCE_DATA_UNVERIFIED`（第0.4節・新規）の1件に統一し、原因は `Issue.metadata["reason"]` で区別する: `UNVERIFIED` / `PENDING` / `REJECTED` / `UNKNOWN_STATUS` / `HASH_MISMATCH`。**v1.8で訂正**（再レビュー指摘11）: v1.7の本行は「既存のコードを用い」としていたが、このコードは現行src/testsのどこにも存在せず、第0.4節の「新規」が正しい。既存コードに寄せるなら実在する `PERFORMANCE_DATA_UNAVAILABLE`（データ欠損）への統合になるが、「存在するが未検証」と「存在しない」は利用者への対処案内が異なるため別コードとする。1コード＋reason方式により第0.3節「重複したenum・コードを定義しない」に従う
- 実装は `PerformanceManifest.validation_status` を素の `str` のまま保持してよい（`schema_version` を上げないため）。ただし**判定側**（データ充足検査・印刷条件）で上表の集合に照合し、集合外は `UNKNOWN_STATUS` として扱う

データ充足検査（起動時表示）は `UNVERIFIED` / `PENDING` を「不足」とは表示しない（検査に通っている値ではあるため）が、印刷条件（第6.3節）では別途この検査を課す。`REJECTED` と未知値は起動時から「不足」として提示する。

### 0.2 既存SEA機能の扱い（v2.6.0で対象外化）

作業ツリーには `NavSection.safe_enroute_altitude_ft_msl`、`SectionResult.safe_enroute_altitude_ft_msl`、`SAFE_ENROUTE_ALTITUDE_REQUIRED`、`PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE` が存在する。しかし、本版ではSEAをNAV LOG地上準備の計算項目・表示項目・転記項目に含めない。

実装は次を満たす。

- 新規Projectでは `safe_enroute_altitude_ft_msl` を設定するUIを提供せず、値は `None` のままとする
- 旧Projectに値が保存されていても、航法計算、燃料、Forecast、計算入力fingerprint、ProjectStatus、転記補助HTMLへ使用しない
- `SAFE_ENROUTE_ALTITUDE_REQUIRED`、`PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE` および `SEA_*` Issueを本版の計算・UI層から生成しない
- `SectionResult.safe_enroute_altitude_ft_msl` をschema互換のため残す場合は未使用値として扱い、画面・HTMLへ出力しない
- DEM10Bの取得、陸域・水域判定、SEA提案、手入力、採用確認、SEA用fingerprint、SEA用cacheを起動・計算・保存時に実行しない
- 旧ProjectのSEA値を無関係な保存操作で書き換える必要はないが、再計算後の判定材料へ復活させてはならない

将来SEAを追加する場合は、NAV LOG本表へ暗黙に戻さず、独立した別表・別状態・別受け入れ基準として新しい設計版で定義する。

### 0.3 既存のモデルを流用し、独自の並行モデルを作らない

本改修で新設が必要と見えた概念の多くは、既に実装されている。**新しいenumやフィールドを重複して定義してはならない。**

| 新設が不要な概念 | 既存の実装 |
| --- | --- |
| 値ごとの可用性・来歴 | `AdoptedValue[T]`（`automatic_value` / `automatic_status` / `automatic_metadata` / `manual_override` / `adopted_source` / `warnings`） |
| 値の状態 | `ValueState`（AUTO / MANUAL_OVERRIDE / FIXED_RULE / PERFORMANCE_TABLE / UNAVAILABLE / WARNING） |
| 気象の可用性 | `Availability`（AVAILABLE / UNAVAILABLE）、`WeatherResult.reason_code` |
| 計算全体の状態 | `ProjectStatus`（DRAFT / ROUTE_INCOMPLETE / FORECAST_REQUIRED / WEATHER_PENDING / MANUAL_INPUT_REQUIRED / CALCULATION_WARNING / READY_FOR_REVIEW / READY_FOR_COPY / SNAPSHOTTED） |
| 転記ブロッカー | `Issue`（`code` / `severity` / `message` / `section_id` / `acknowledgement_required`）と `IssueSeverity.BLOCKER` |
| 警告の承認 | `Project.acknowledged_warning_codes` |
| revision競合 | `LocalProjectRepository.save(project, expected_revision)` と `RevisionConflictError` → `project-conflict-*.json` |
| 原子的書込み | `_atomic_json_write`（tempfile → `fsync` → `os.replace`） |
| Snapshotの不変性 | `create_snapshot` が既存パスで `FileExistsError` |
| Drive保存 | `GoogleDriveProjectRepository("/content/drive/MyDrive")` → `MyDrive/AutoNavLog/projects/{project_id}/project.json` |
| 表示再現に必要な保存物 | `CalculationSnapshot`（`weather_requests` / `weather_results` / `forecast_metadata` / `policy_version` / `performance_table_version` / `autonavlog_version` / `msm_package_version` / `warnings`） |
| Forecast Runの更新検知 | `RunSelectionStatus.update_available` と `FORECAST_UPDATE_AVAILABLE` |
| 欠損値のフォールバック禁止 | `TEMPERATURE_UNAVAILABLE` / `WIND_UNAVAILABLE` / `QNH_UNAVAILABLE` |
| 測地線中点 | `docs/calculation_rules.md`「Leg中点は測地線上の中点」、`nav/geodesy.py` |
| KMLの安全解析 | `defusedxml`、`ImportLimits` |

したがって本書のUI要求は、**既存モデルの上に載る表示・導線・状態遷移の要求**として読む。

### 0.4 Issueコード

UIは未知の `Issue.code` も落とさず、`severity`、`message`、`section_id`、`acknowledgement_required` に従って表示・集計する。本表は本改修で特別な導線を要するコードであり、網羅一覧ではない。

| コード | 重大度 | 扱い |
| --- | --- | --- |
| `ROUTE_INCOMPLETE` | BLOCKER | 既存 |
| `AIRPORT_DATA_UNAVAILABLE` | BLOCKER | 既存 |
| `PERFORMANCE_DATA_UNAVAILABLE` | BLOCKER | 既存 |
| `CLIMB_PERFORMANCE_UNAVAILABLE` / `CRUISE_PERFORMANCE_UNAVAILABLE` | BLOCKER | 既存 |
| `FORECAST_PREPARE_FAILED` / `FORECAST_RUN_OUT_OF_COVERAGE` | BLOCKER | 既存 |
| `FORECAST_UPDATE_AVAILABLE` | WARNING | 既存 |
| `WEATHER_QUERY_FAILED` / `WEATHER_BATCH_MISMATCH` | BLOCKER | 既存 |
| `WIND_UNAVAILABLE` / `TEMPERATURE_UNAVAILABLE` / `QNH_UNAVAILABLE` | 既存値 | 既存 |
| `WIND_TRIANGLE_FAILED` / `INSUFFICIENT_FUEL` | BLOCKER | 既存 |
| `RCA_OUTSIDE_ROUTE` / `EOC_OUTSIDE_ROUTE` | BLOCKER | 既存 |
| `RCA_BEYOND_FIRST_TURN` | WARNING（要承認） | 既存 |
| `DEFAULTS_NOT_REVIEWED` / `RECALCULATION_REQUIRED` | BLOCKER | 新規 |
| `MANUAL_QNH_RECONFIRM_REQUIRED` / `PERFORMANCE_DATA_UNVERIFIED` | BLOCKER | 新規 |
| `PROJECT_STATE_INVALID` / `PATTERN_ALTITUDE_REQUIRED` | BLOCKER | 新規 |
| `VISUAL_REPORTING_POINT_REQUIRED` / `VISUAL_REPORTING_POINT_ROUTE_INVALID` | BLOCKER | 新規 |
| `ARRIVAL_ALTITUDE_OVERRIDE_REASON_REQUIRED` | BLOCKER | 新規 |
| `CP_LINK_REQUIRED` / `CP_NOT_ABEAM_LINKED_SECTION` | BLOCKER | 新規 |
| `REFERENCE_DATA_PACK_INVALID` | BLOCKER | 新規 |

作業ツリーに存在する `SAFE_ENROUTE_ALTITUDE_REQUIRED` と `PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE` は、本版では生成しない。旧版で設計した `SEA_*` コードも追加・生成しない。旧Outcome/Snapshotの履歴表示に残っていても、編集可能Projectの再計算後へ持ち越してはならない。

`REFERENCE_DATA_MISSING` は実体のIssueではなく、複数の参照データBlockerをステータスバーで集約する表示名とする。

### 0.5 現行UI構成（v1.7で全面修正）

**v1.6までの本節は「KML貼付テキストエリア・形状Select・確認Checkbox・一括追加・全Leg ALT一括適用・印刷用HTMLダウンロードボタンが存在しない」としていたが、これは基準commitに対する記述であり作業ツリーでは誤りである**（再レビュー指摘10の派生）。作業ツリーの `presentation/colab.py`（`AutoNavLogApp`）には次がすべて**存在する**。

| 要素 | 実体 |
| --- | --- |
| KML貼付テキストエリア | `self.kml_text`（`widgets.Textarea`） |
| KML/KMZアップロード | `self.upload`（`FileUpload(accept=".kml,.kmz")`） |
| 形状Select | `self.shape_candidates`（`widgets.Select`。`("line", i)` / `("polygon", i)` / `("point", i)`） |
| 確認Checkbox | `self.quick_run_confirmation`（別名 `self.polygon_route_confirmation`） |
| 一括追加 | 「選択形状をRouteへ一括追加」ボタン |
| 全Leg ALT一括適用 | `self.apply_all_leg_altitude_button` |
| 印刷用HTMLダウンロード | `self.download_transfer_aid_button`（「A4印刷用HTMLをダウンロード」→ `presentation/transfer_aid.py`） |
| 清書ビュー | `self.clearcopy`（`presentation/clearcopy.py`） |

**したがって本改修は「これらを新規に作る」のではなく「既存のこれらを整理・再構成する」作業である。** 本書の各FRは、対応する既存ウィジェットがある場合はそれを改修対象として読む。

Polygonの扱いも同様に修正が必要である。**作業ツリーの `importers/kml.py` は `Polygon` を解析して `ImportedPolygon`（外周・内周・高度）として保持し、`colab.py` は確認Checkbox付きでPolygonの外周をRoute化できる。** 「Polygonは既に無視されている」は基準commitに対しては真だが、作業ツリーに対しては偽である（FR-15・D-10・第11.1節を v1.7 で更新）。

- **v2.7.0で置換（旧D-19）**: 主UIはD-30の `autonavlog.web` + `web/` とする。`presentation/colab.py` と配布Notebookは互換・移行確認用に残せるが、主配布UIではない
- FR-4a が削除対象とする「cell 3 の `app.departure/destination` 固定設定」は、参照した実装のNotebook（3セル構成）には存在しない

---

## 1. 背景と目的

（第1.1節の分析は初版から変更しない。）

現行のColabプレビューは、開発者本人以外が説明なしに操作するのが困難である。本改修の目的は、**Google EarthのKMLから、NAV LOGの地上準備を手順書を読まずに完了できるようにすること**。ここでいう完了とは、別添8-1へ地上で記入できる計画値を揃え、機上で記入する実績欄を意図どおり空欄にした状態をいう。実際の動線が `3 → 6 → 3 → 4 → 6 → 7 → 8` と往復することが分かりにくさの主因である。

### 1.1 設計原則

| 順位 | 原則 |
| --- | --- |
| 1 | 安全側に倒す。不確実な値・未確認の値・古い計算結果を転記させない。欠損値を既定値で埋めない |
| 2 | 利用者が外部から用意する必須データはKML/KMZのみ |
| 3 | 次の1手を常に画面に出す。無効化には理由と対処を併記する |
| 4 | 作業状態は失わせない |

原則1と原則2の競合は、「計算の開始を止める」ではなく「求まらない値を未確定として表示し、転記・印刷を止める」ことで解決する。これは既存の `AdoptedValue` と `Issue` の設計思想と一致する。

### 1.2 成果物と用語

- 成果物は**地上準備用の転記補助HTML**であり、完成した公式NAV LOGそのものではない。利用者は別添8-1へ書き写し、飛行中にETO・ATO・ATE等を記入・更新して使用する
- 本書の「印刷可能」「READY_FOR_COPY」は、現在の計画値を転記補助HTMLとして出力できることだけを表す。教範上の全欄完成またはそのまま機上携行できる完成帳票を意味しない。SEAは本版の成果物に含まれない
- 地上準備完了時にも `TAKE OFF` / `LANDING` / `ETO` / `ATO` / `ATE` 等の実時刻依存欄は空欄でよい。空欄であることを未完了Issueにしてはならない
- `planned_departure_time_jst` はForecast Run選択・気象代表時刻のための計画基準時刻であり、教範上のETOを確定する実発動時刻ではない

---

## 2. スコープ

### 2.1 対象

- `autonavlog.presentation.colab.AutoNavLogApp` の再構成
- UI層の状態モデル（第3章）
- VREP通過高度とEOCの教範式による算出
- 経路外Check Pointのabeam stationingとNAV LOG区間分割
- 空港・地点・Check Point参照データの差し替え・追加・編集・削除・版戻し
- データ欠落の第一級表示（第0.1節）
- KMLインポータの拡張（複数LineStringの選択。**Polygonは保持・確認付きRoute化を維持する**（FR-15）。エラー・警告の対象はPolygon検出そのものではなく、usable outer boundaryを持たずskipされた未対応surface（現行の `skipped N Polygon surface(s)` 警告）と、LineStringが0本の場合の2択提示（FR-15b）に限る。v1.8で修正: v1.7の「Polygon検出時の明示エラー」はFR-15と逆だった）
- 配布Notebookのセル構成変更

### 2.2 対象外

- 航法計算アルゴリズム全体、性能補間、MSM連携の全面変更。ただし第6.5〜6.7節で明示するETO/Lossの地上計画除外、VREP高度、CP abeam分割は本改修の対象
- ドメインモデルの破壊的変更（`schema_version` は 1 のまま）
- 保存・Snapshot・revision競合処理の再実装（既存を使用する）
- SEA、DEM、陸域マスク、障害物データ、およびそれらを用いる別表
- 日本国外に及ぶ経路への対応
- Snapshotからの再計算（表示再現のみ）
- **全NAV2候補空港・地点・CPの値を本改修だけで網羅すること**。ただし参照データを後から差し替え・CRUDできる仕組みと、同梱既定パックの検証は本改修の対象
- 飛行中の実時刻入力、実測GSからのETO再計算、Position Report Revise、後続LegのMH/ETE更新。これらは機上運用であり、地上準備用転記補助HTMLでは空欄または対象外とする

### 2.3 追加依存

本版では追加依存を導入しない。旧版でSEA・DEM処理用に予定していた `numpy`、`Pillow`、およびoffline陸域マスク生成用のGIS依存は追加しない。既存の航法計算・KML処理・気象処理に必要な依存だけを維持する。

---

## 3. 起動シーケンスとUI状態モデル

### 3.1 起動シーケンス

配布Notebookは既に `drive.mount("/content/drive")` を実行し、`MyDrive/AutoNavLog/releases/{version}` から版を選んで読み込む。本改修はこの構造を維持する。

```
1. drive.mount（既存）
2. リリース版の選択・wheelインストール（既存。#@title でコード非表示）
3. Repository / WeatherProvider / PerformanceRepository / ReferenceDataCatalogRepository の構築（参照データはactive packからAirport/Point/CP viewを生成）
4. データ充足検査（第0.1節）
5. UI描画
```

Driveへ接続できない場合は `LocalProjectRepository` で続行し、警告を常時表示する。

### 3.2 UI状態モデル

計算状態は既存の `ProjectStatus` と `Issue` を表示する。UI層が独自に持つ状態は、KML取込、保存先、表示mode、計算入力fingerprintだけとする。SEAジョブ状態は持たない。

```python
class RouteState(Enum):
    EMPTY = "empty"
    PARSING = "parsing"
    INVALID = "invalid"
    NEEDS_SELECTION = "needs_selection"
    READY = "ready"

class StorageState(Enum):
    TEMP_ONLY = "temp_only"
    DRIVE_READY = "drive_ready"
    WRITING = "writing"
    ERROR = "error"

class ViewMode(Enum):
    EDITABLE = "editable"
    SNAPSHOT_READONLY = "snapshot_readonly"

class PersistedUiState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state_schema_version: Literal[5]
    calculated_against_fingerprint: Sha256Hex | None = None
    defaults_review_fingerprint: Sha256Hex | None = None
    manual_qnh_fingerprint: Sha256Hex | None = None
    arrival_plan: ArrivalPlan | None = None
    reference_data_snapshot: ReferenceDataSnapshot | None = None
    rjfm_departure_plan: RjfmDeparturePlan | None = None
    rjfm_departure_guidance: RjfmDepartureGuidance | None = None
```

`current_calculation_input_fingerprint` は現在のProjectから都度導出し、保存しない。直近の計算完了時に `calculated_against_fingerprint` を保存する。両者が異なる場合は `RECALCULATION_REQUIRED`（BLOCKER）とする。

`state_schema_version=5` はProject/Snapshot本体のschema versionを変更しない。旧v4を
読み込む場合は既存状態を維持し、RJFM状態を空で追加する。旧v3は凍結したv3 modelで
厳格検証し、計算fingerprint、既定値確認、手動QNH確認、ArrivalPlan、参照データsnapshot
だけをv5へ移す。`sea_states` は移行せず破棄する。v2以前はArrivalPlanと参照データを
安全に復元できないため、経路・FROM/TO・VREP・参照行の再確認を要求する。不正または
未知versionを推測で補完しない。

導出規則:

| 表示・操作 | 条件 |
| --- | --- |
| フェーズA | `route_state != READY` |
| フェーズB1（入力確認） | `route_state == READY` |
| 入力Section一覧 | `route_state == READY` かつSectionが1件以上 |
| フェーズB2（計算結果） | `outcome is not None` |
| フェーズB3（転記前確認） | 第6.3節のうちOutcome依存条件が揃った時点 |
| 「NAV LOGを作る」 | `route_state == READY` かつ計算中でない |
| 結果テーブル | `outcome is not None` |
| 入力を読み取り専用 | 計算中、または `view_mode == SNAPSHOT_READONLY` |

Snapshot閲覧中は自動取得・再計算・編集・保存を行わない。SEA進捗・SEA確認・SEA中止の状態やUIは存在しない。

---

## 4. 入力項目の分類と既定値

### 4.1 分類

CB=計算ブロッカー、CD=計算必須・既定値あり、PB=転記ブロッカー、OP=任意。

### 4.2 項目一覧（既定値は現行実装の実測値）

| 項目 | ドメイン上の名称 | 分類 | 現行既定値 | 本改修での既定値 | 初期表示 |
| --- | --- | --- | --- | --- | --- |
| KML / KMZ | — | CB | — | — | 常時表示 |
| LineString選択 | — | CB | — | 1件なら自動 | 条件表示 |
| DATE | `flight_date` | CD | **`date.today()`** | **翌日（JST）へ変更** | KML欄直下 |
| ETD | `planned_departure_time_jst` | CD | `"09:00"` | 0900 JST（維持） | KML欄直下 |
| ALT | `NavSection.planned_altitude_ft_msl` | CD | **5000 ft** | 維持 | 折りたたみ |
| FUEL | `total_usable_fuel_gal` | CD | **81.0 gal** | 維持 | 折りたたみ |
| VAR | `SectionResult.variation_deg_east` | 自動 | **Project固定8.0** | **Leg出発緯度32.0°N以上+8°、未満+7°** | Leg設定・結果表 |
| TGL | `tgl_count` | CD | **0（回数。`BoundedIntText(min=0)`）** | 維持 | 折りたたみ |
| Phase | `NavSection.phase` | CD | **`CRUISE`** | 維持 | フェーズB |
| 旧LOSS | `NavSection.loss_time_seconds` | — | **0固定** | v2.5.1で入力廃止。旧非0値は移行情報として読取専用表示し、計算には不使用 | 移行表示のみ |
| PILOT | `pilot_name` | PB | 空 | 維持 | 折りたたみ |
| SHIP | `ship_identifier` | PB | 空 | 維持 | 折りたたみ |
| 機体Profile | `aircraft_profile_id` | 固定 | `"SR22_G6"` | UIに出さない | — |
| Project名 | `name` | 自動 | `"NAV2"`（未入力時） | 第6.1節で自動生成 | フェーズB |
| Active参照データ | `ReferenceDataSnapshot` | CD | 固定CSV | active packから候補、選択行をProjectへsnapshot | フェーズB |
| VREP | `ArrivalPlan.visual_reporting_point_node_id` | PB | 暗黙の終端 | 候補提示後に明示選択 | フェーズB |
| 到着高度mode | `ArrivalPlan.altitude_mode` | CD | 暗黙固定 | 標準距離則。変則時のみ高度・理由を入力 | フェーズB |
| CP | `VisualReference(role=CHECK_POINT)` | OP | 経路点扱い | 任意に複数。存在する場合は関連Leg確認必須 | フェーズB |
| FROM / TO | `departure_airport_id` / `destination_airport_id` | 自動 | 手入力 | KMLから判定（FR-4） | フェーズB |
| QNH | `manual_qnh_hpa` | 条件付きPB | なし（`gt=800, lt=1100`） | 維持 | フェーズB |
| Forecast Run | `selected_forecast_run_id` | 自動 | 未選択 | 初回自動選択後は固定 | フェーズB |

DATE の既定値のみ変更する。当日を既定にすると、翌日の飛行計画を作る通常運用で毎回変更が必要になるためである。ETDと合わせてForecast Runの選択に影響するので、常時表示のまま残す。

### 4.3 規則

- **R-1**: 外部から用意する必須データは KML/KMZ のみ。ただし入力が不正な場合、または複数の飛行経路候補からの選択が未完了の場合は計算を開始しない
- **R-2**: 値の来歴は既存の `AdoptedValue.adopted_source` と `ValueState` で表す。UI固有の入力（ALT/FUEL/VAR/TGL）についてのみ、既定値のままか編集済みかをUI層で保持する
- **R-3**: DATE / ETD は既定値確認の対象外（常時表示のため）
- **R-4**: 未解消のBlockerを含む折りたたみは、フェーズB到達後に自動で開く

---

## 5. 機能要求

### FR-1 初期表示の最小化 【必須】

ステータスバー／データ充足警告（第0.1節）／Drive接続状態／KML XML貼付欄／ファイル選択（`.kml` / `.kmz`）／DATE／ETD／「NAV LOGを作る」／「保存済みのNAV LOGを開く」のみ。他はすべて折りたたみ内。

KML貼付欄は**既存の `self.kml_text`（`widgets.Textarea`。第0.5節）を初期表示へ再配置・再設定する**。同一機能のTextareaを新規に追加してはならない（KML貼付の入力経路は `kml_text` の単一経路とする）。**v1.8で修正**: v1.7までの「新規追加である（現行はFileUploadのみ）」は基準commitに対する記述で、基準アーカイブには `kml_text` が既に存在する。

### FR-2 Project名の自動生成と事後提示 【必須】

`name` の入力欄を初期画面から削除し、第6.1節の規則で自動生成する。計算完了後に保存名を提示し、〔変更〕でインライン編集できる。編集後は自動追従しない。

### FR-3 保存済みProjectの読込 【必須】

`Project ID` テキスト入力を Dropdown へ置き換える。保存先は `{root}/projects/{project_id}/project.json` であり、ディレクトリ走査で列挙できる。表示は `name（更新日時・ProjectStatus）`。

### FR-4 FROM / TO の判定 【必須】

active参照データパックのAirport viewから最寄り空港を探索する。候補は経路ごとに変わるため、FROM/TOをruntime固定値として持たない。

| 距離 | 空港識別 | 経路端点の座標 |
| --- | --- | --- |
| 1 nm 以内 | 自動確定 | KML座標をそのまま使用 |
| 1 nm 超 5 nm 以下 | 候補提示、利用者が採否 | 同上 |
| 5 nm 超 | 空港なし。下記から選択 | 同上 |

空港の識別と経路座標の書き換えは別処理とし、自動置換は行わない。〔空港公示座標へ合わせる〕は明示操作とし、移動量を表示する。

空港を特定できない、または採用しない場合は経路点としての確定を許可する。**既存の `RouteNodeRole` を用いる**（`AIRPORT` / `DEPARTURE_REFERENCE` / `ROUTE_POINT` / `DESTINATION` 等）。新しいenumを作らない。

> **制約**: active packに有効な空港行がない、または選択行をsnapshotできない間、この機能は候補を返せない。`AIRPORT_DATA_UNAVAILABLE` を表示し、参照データ管理画面で追加・取込・active版切替を行う導線と、下書きとして経路点を保持する導線を提供する。

#### FR-4a 端点の固定追加の除去 【条件付き必須】

配布Notebookが `departure/destination` を固定設定している場合、これを削除しFR-4の判定に置き換える。参照した実装には該当箇所がないため、D-19決定（改修対象を`colab.py`の`AutoNavLogApp`とする。第0.5節・第15章）に基づき、配布Notebook側に同等の固定設定が見つかった場合にのみ適用する。

#### FR-4b 重複点の統合 【必須】

**隣接する**2点が許容誤差内（既定 10 m）なら1点へ統合する。**先頭点と末尾点の一致は統合しない。** `A→B→C→A` は4点・3Legとして扱う。

### FR-5 DATE / ETD 【必須】

DATE既定を翌日（`ZoneInfo("Asia/Tokyo")` 基準）へ変更する。ETDは 0900 JST を維持する。

### FR-6〜FR-8: 欠番（SEA要求をv2.6.0で対象外化）

SEA算出、SEA入力、SEA確認、結果テーブルのSEA列は実装しない。番号は履歴追跡のため再利用しない。

### FR-9 ステータスバー 【必須】

未充足条件を**同時に**表示する。表示順は、データ欠落 → 経路 → 再計算要 → その他Blocker → 承認待ちWarning。

```
② 地上準備完了まで: ALT見直し 1件 / 既定値の確認 / PILOT 未記入
```

`Issue` のうち `severity == BLOCKER` を件数集約し、`acknowledgement_required` のWarningは警告一覧にのみ表示する。

### FR-10 ボタンごとの有効条件と理由表示 【必須】

無効化には必ず理由と対処を併記する（tooltip不可）。ネットワーク障害の直後も「再計算」は有効でなければならない。印刷用HTMLの条件は第6.3節。

### FR-11 エラーメッセージの改善 【必須】

文言に「次にとるべき行動」を含める。既存の `Issue.message` は簡潔なため、UI層で対処文言を補う対応表を持つ。

### FR-12 計算ボタンの一本化 【必須】

`NAV LOGを作る` / `再計算` の1ボタンとする。

### FR-13 計算値と入力値の視覚的分離 【必須】

`SectionResult` の各 `AdoptedValue` は読み取り専用表示とする。`ValueState` により表示を分ける。

| ValueState | 表示 |
| --- | --- |
| AUTO | 値のみ |
| PERFORMANCE_TABLE | 値＋出典（`performance_metadata`） |
| FIXED_RULE | 値＋`規則値`（例: 降下 500 fpm、到着 無風） |
| MANUAL_OVERRIDE | 値＋`手動`＋自動値の併記 |
| UNAVAILABLE | `未取得`（太字・赤系背景）＋理由 |
| WARNING | 値＋警告アイコン |

Leg単位で利用者が入力するのは **Phase / ALT** であることが一目で分かるようにする。Loss TimeとSEAは地上入力に含めない。

### FR-14 ルート追加手段の整理 【推奨】

現行のRoute追加手段（FileUpload・**既存のKML貼付テキストエリア `kml_text`**（FR-1により初期表示へ再配置）・候補Select・手入力座標。第0.5節）と連結経路候補の選択（FR-20）を含め、追加手段の一覧をひとつの折りたたみへ集約する。個別の追加手段を画面上に常時並べない。**貼付Textareaを新設しない**（FR-1。v1.8で修正: v1.7までの「本改修で追加する」は誤り）。

受け入れ条件: 初期表示（FR-1）に追加手段の詳細UIが並ばないこと。折りたたみを開いたときに全追加手段が列挙されること。

### FR-15 Polygonの扱いの明示 【必須】

**v1.7で全面改訂**（再レビュー指摘10）。v1.6は「Polygonは非対応・既に無視されている」を前提に、無視した旨のエラー提示のみを求めていた。**作業ツリーの実装はPolygonを解析・保持し、明示確認のうえRoute化できる。** 既存機能を削る（＝利用者から見た機能後退を起こす）理由はないため、**現行機能を維持する契約へ更新する。**

現行実装の挙動（維持する）:

| 状況 | 現行の挙動 |
| --- | --- |
| Polygon解析 | `importers/kml.py` が外周（`outer_boundary`）・内周（`inner_boundaries`）・高度を `ImportedPolygon` として保持する |
| Route化 | `colab.py` で形状Selectから `("polygon", i)` を選び、**確認Checkbox（`quick_run_confirmation`）をONにした場合のみ** 外周の各頂点をRoute候補（`KML/KMZ Polygon (confirmed)` 由来）として適用する |
| 未確認時 | `_require_quick_run_confirmation(polygon=True)` により「Polygonは空域・区域境界の可能性があります。」として拒否する |
| Polygon複数 | 「開始点・進行方向は自動判定できません」として1件選択を要求する |
| 地図表示 | 外周・内周を `folium.Polygon` として描画し、`(Polygon / 空域・区域境界)` とツールチップ表示する |

本改修での要求:

- **FR-15a**: Polygonは既定では経路ではない。**確認なしにRoute化してはならない**（現行の確認Checkbox必須の挙動を維持する）。確認文言に「Polygonは空域・区域の境界であることが多く、経路として使うと意図しない順序・始点になりうる」旨を明示する
- **FR-15b**: `LineString` が0本でPolygonのみの場合、「経路なし」で終わらせず、次の2つの選択肢を**同時に**提示する

```
線（LineString）が見つかりません。読み込んだのは面（Polygon）N件です。

・経路として使う場合: 面の外周を順にたどる経路になります。空域・区域の
  境界を面で描いている場合は、意図した経路になりません。
  〔面を経路として使う（確認）〕
・経路を作り直す場合: Google Earth の「パスを追加」で経路を作成し、
  その経路を保存したKMLを読み込んでください。
```

- **FR-15c**: `LineString` が1本以上ある場合はそれを既定選択とし、Polygonは選択肢として残したうえで件数を情報として表示する（**破棄・無視はしない**）
- **FR-15d**: Polygon由来のRouteは出所を保持し（現行の `KML/KMZ Polygon (confirmed)`）、清書ビュー・印刷用HTMLで「面の外周から生成した経路」である旨を明示する

`ImportedPolygon` の解析上限（面の頂点数）は第11.2節K-10の座標上限に従う。**Polygon外周をRoute化する場合は選択後上限 `max_coordinates_in_selected_polygon_outer = 500` を適用し、超過は選択操作後にエラーとする**（K-10・受け入れ基準K-12。v1.8で追加）。

### FR-16 空セクションの非表示 【推奨】

**入力Section一覧と結果テーブルは別物として扱う**（再レビュー指摘: v1.6初版は「計算により `SectionResult` が得られて初めて一覧を描画する」としていたが、フェーズB1では計算開始前にLegのALT・Phaseを確認・編集する必要があり、第3.2節の導出規則と矛盾していた）。

| 領域 | 表示条件 | 内容 |
| --- | --- | --- |
| 入力Section一覧（フェーズB1） | `route_state == READY` かつ `NavSection` が1件以上 | 各LegのFROM/TO・ALT・Phaseの確認と編集 |
| 結果テーブル（フェーズB2） | `outcome is not None` | `SectionResult` の各 `AdoptedValue`（読み取り専用。FR-13） |

`NavSection` が0件（経路未確定）の間は入力Section一覧も表示しない。

受け入れ条件: `route_state != READY` の間、入力Section一覧・結果テーブルのいずれも空表示ではなく非表示（DOM上に存在しない、または `display:none` 相当）であること。`route_state == READY` かつ `outcome is None` の間、入力Section一覧は表示され編集可能である一方、結果テーブルは表示されないこと。

### FR-17 並べ替えボタンのラベル付与 【推奨】

Route追加時の並べ替え操作（既存UIに存在する上下移動等のボタン）に、現状アイコンのみまたは無ラベルの場合はテキストラベルまたは `aria-label` を付与する。

受け入れ条件: スクリーンリーダー・ツールチップいずれかでボタンの機能が文言として取得できること。

### FR-18 Notebookセルの `#@title` 非表示 【旧Colab互換のみ】

旧Notebookを保守する場合だけ従来の `#@title` 契約を維持する。v2.7.0の主配布受け入れ条件には含めず、Web版はcode-nativeなHTML controlとbundle済みassetで構成する。

受け入れ条件: Web版はW-1〜W-6を満たすこと。A-4は旧Notebookを再配布する場合だけ適用する。

### FR-19 出典・免責表示 【必須】

第12章による。既存の `docs/data_provenance.md` の方針を維持し、選択した性能・参照データの出典を加える。

### FR-20 複数LineStringの選択 【必須】

KMLの `Document` / `Folder` 階層とPlacemarkの記載順を保持する。複数の
`LineString` が最も内側の同じコンテナにあり、記載順に並べたすべての隣接組で
前の線の終点と次の線の始点が `0.02 NM` 以下なら、順序・方向を変えずに
`connected_lines` 候補を1件作る。自動反転、距離による並べ替え、別コンテナ間の連結は行わない。

接続点では、同じ直近コンテナのPoint Placemarkのうち両側の線端点からそれぞれ
`0.02 NM` 以内に一致するものだけをRouteNodeの名称・座標に採用する。該当Pointがなければ
前のLineStringの終点を採用し、線端点間が10 mを超える場合は警告する。線の途中にあるPointや
接続条件を満たさないPointは自動でRouteNodeへ加えない。隣接端点が `0.02 NM` を超える場合は
連結候補を作らず、個別LineString候補と不連続の警告を残す。一致するPointが複数ある場合は、
両端点までの距離の最大値が最小、距離合計が最小、KML記載順の順で決定する。

有効な連結候補に含まれる個別LineString候補は重複表示しない。連結の成否にかかわらず、
`connected_lines` または個別LineString候補が1件でもあれば、文書内の全Pointを記載順に
並べただけの候補は表示しない。LineStringがないPoint-only KMLでは既存fallbackを維持する。
複数の経路候補が残る場合は先頭を自動採用せず、「飛行経路候補」で1件の明示選択を要求する。
候補表示は `RJFM→RJFO① · 7 Leg · 124.66 NM` のように、候補名、Leg数、全長を示す。

Web APIは確認要求の `candidate_kind` に `"connected_lines"` を追加する。取込候補は既存の
`name` / `index` / `kind` に加えて `containerPath` / `segmentNames` / `segmentCount` /
`legCount` / `vertexCount` / `distanceNm` / `coordinates` / `maxJoinGapNm` を返す。
`segmentCount` は構成LineString数、`legCount` は10 m以内の隣接点統合後の経路点間数とする。
選択した連結経路の合計座標数は500点以下とし、採用コンテナと構成LineString名をD-33のProject metadataへ保存する。
既存の `line` / `polygon` / `points` 契約は維持する。

本要件は現行Web版に適用する。旧Colab UIは従来どおり個別の形状を選択する互換実装とし、
Folder単位の連結候補選択へ変更しない。

### FR-21 KMZ対応 【必須】

FileUploadは既に `.kml,.kmz` を受理する。貼付欄はKML XMLのみとする。KMZ内KMLの選択規則は第11.2節K-5に一本化し、選択後のKMLにはFR-20と同じ連結候補生成を適用する（v1.5は存在しない「第11.5節」を参照していた）。

### FR-22: 欠番（SEAタイル要求をv2.6.0で対象外化）

DEMタイル数・推定容量を計算または表示しない。

### FR-23: 欠番（旧要求を削除したため予約）

### FR-24 下書き保存 【必須】

未確定状態でも**保存**を実行できる。既存の `autosave()` と `save(project, expected_revision)` を使用する。**Snapshotは `CalculationOutcome` 取得後のみ**（第6.4節。既存の `ProjectService.snapshot()` が `outcome` を必須とするため）。

### FR-25 高度のみ変更した再計算 【必須】

ALTだけを変更した場合も通常の航法再計算を行い、KML再取込や参照データ再選択を要求しない。SEA/DEM処理は存在しない。

### FR-26 計算結果の陳腐化検知 【必須】

`RECALCULATION_REQUIRED` により印刷を止める。

### FR-27 既定値一括確認UIの廃止 【必須】

ALT・Phase・FUEL・VAR・TGLは各入力欄で直接確認する。これらを一括して原資料と照合したことを求めるCheckbox／Buttonと `DEFAULTS_NOT_REVIEWED` の主UI生成を廃止する。計算後の入力変更は計算入力fingerprintと `RECALCULATION_REQUIRED` で検知する。

### FR-28: 欠番（SEA非同期jobをv2.6.0で対象外化）

SEA job、進捗、中止、部分成功のUI・状態は実装しない。

### FR-29 狭幅表示への対応 【必須】

幅390pxで主要操作が完了できること。無効理由は常時表示し、タッチ対象を44px以上とする。既存CSSの `max-width:760px` / `min-height:40px` を基点に拡張する。

### FR-30: 欠番（SEA手入力要求をv2.6.0で対象外化）

SEA手入力・提案比較・承認は実装しない。

### FR-31 Project永続化 【v2.7.0で置換】

Web版のProject保存はD-32どおり既存の `LocalProjectRepository` を使用する。Google Drive repositoryは旧Colab互換のため残してよいが、WebサーバーからDrive mountへ依存しない。DEM/SEA用保存機構は追加しない。

### FR-32 気象値の可用性表示 【必須】

`AdoptedValue` と `WeatherResult.availability` / `reason_code` をそのまま表示する。**欠損値を 0・無風・1013.25 hPa・最近傍値で補ってはならない**（既存方針）。

ただし `FIXED_RULE` として規定された値（降下 500 fpm / 12 GPH、到着 CAS 121 kt・原則無風・12 GPH）はフォールバックではなく規則値である。両者を表示上区別する。

### FR-33 Forecast Run の選択と提示 【必須】

| 時機 | 動作 |
| --- | --- |
| 初回 | `ForecastService.build_initial_requirement()` → `resolve_run()` により自動選択。利用者操作を要しない |
| 表示 | `selected_forecast_run_id` と `initial_time_utc` を結果テーブル直上に常時表示 |
| 2回目以降 | `selected_forecast_run_id` を既定使用。明示操作なしに変更しない |
| 新Runあり | `FORECAST_UPDATE_AVAILABLE` を警告一覧に表示し〔切り替える〕を提供。自動切替しない |
| 計算中 | 反復（最大5回・収束30秒）を通じてRunを固定（既存） |
| 取得失敗 | `FORECAST_PREPARE_FAILED` 等を表示。既存入力と部分結果は保持 |

### FR-34: 欠番（ALT/SEA比較をv2.6.0で対象外化）

`SAFE_ENROUTE_ALTITUDE_REQUIRED` と `PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE` を全Phaseで生成せず、SEA値の有無や大小を転記条件へ使用しない。

### FR-35 Snapshotの読み取り専用表示 【必須】

`SNAPSHOT_READONLY` では自動取得・再計算・編集・保存を行わない。既存の `load_snapshot()` を使用する。

### FR-36 データ充足の第一級表示 【必須】

第0.1節。

### FR-37: 欠番（SEA出力をv2.6.0で対象外化）

清書ビューと転記補助HTMLへSEA、water ratio、地形注記を出力しない。

### FR-38 RCA / EOC の表示 【必須】

`CalculationOutcome.derived_points`（`DerivedPointType.RCA` / `EOC`）を結果テーブルおよび地図上に表示する。`RCA_OUTSIDE_ROUTE` / `EOC_OUTSIDE_ROUTE` はBlocker、`RCA_BEYOND_FIRST_TURN` は承認可能なWarningとして提示し、承認は `acknowledged_warning_codes` へ記録する。
### FR-39 ETO / Loss Timeと地上準備欄 【必須】

Loss Timeは機上で事前計算結果を修正する値とし、地上計画のZONE/CUM ETE・TTL TIME・Forecast・燃料へ一切加えない。LOSS入力UIを提供しない。別添8-1へ転記するETO欄は空欄とし、ETD基準の内部計画時刻をETOとして印字してはならない。詳細は第6.5節。

### FR-40 VREP通過高度 【必須】

経路確定後に目的空港を最終確認し、master場周経路高度を100 ft単位のInputへ初期表示する。東西場周等の運用差がある場合は今回採用するMSL高度へ編集し、確定値を `ArrivalPlan` へ保存する。目的空港ARP座標からVREPまでのWGS84距離と採用場周高度から第6.6節の式でVREP計画高度を求める。5 NMでは採用場周高度+500 ft、以遠は超過整数NM×200 ftとし、降下目標、EOC、降下・到着区間の気象代表高度および転記補助ALTへ一貫して使用する。未確定なら計算を許可しない。

### FR-41 経路外Check Pointのabeam処理 【必須】

CPはRouteNodeではなく `VisualReference(role=CHECK_POINT)` として保持する。経路外CPは関連付けた有限Leg上のabeam点で距離・時間区間を分け、CP自身への斜距離をZONE/CUM DISTへ加えない。詳細は第6.7節。
Webでは地図clickまたは緯度・経度入力から作成し、名称・座標・関連Legの編集と削除を
提供する。計算前にもabeam点、Leg内・累積・cross-track距離と投影Blockerを表示する。

### FR-42 差し替え可能な参照データ 【必須】

空港・一般地点・CPを別CSVとして持つmanifest付き参照データパックを導入し、パックの取込・書出・有効化・前版復帰と、各行の追加・編集・削除を提供する。選択した行はProjectへsnapshotし、active packの差し替えを既存Projectへ自動反映しない。詳細は第6.8節。

### FR-43 NAV2の風必須方針 【必須】

NAV2（宮崎課程）の地上準備では全ての対象区間で風を予想する。「風を予想しない」選択肢および気象欠損の無風補完は提供せず、既存の `WIND_UNAVAILABLE` を維持する。到着区間だけは規程に基づく固定CALMで計算し、目的地TAF風はD-42の`DESTINATION_INFO`へ表示する。
### FR-44 KML Pointの役割選択 【必須】

KMLのPoint Placemarkは、FR-20で隣接LineStringの接続点として一致したPointを除き、自動でRouteNodeへ追加しない。取込後に「経路点」「VREP」「CP」「参照のみ」から役割を選ぶ。経路点/VREPだけが確認後にRouteNodeとなり、CPは `VisualReference(role=CHECK_POINT)` として関連Legを確認する。CPを選んでも選択LineStringの形状・総距離・TCを変更してはならない。接続点として一致したPointは連結経路の変針点名称・座標にだけ使い、線途中のC'K等へ役割を自動付与しない。

Point名は選択した役割の名称として保持し、マスターへ登録する場合はProject取込と別の明示操作にする。KML読込だけでactive参照データを変更してはならない。


---

## 6. 詳細規定

### 6.1 Project名

`Project.id`（UUID4）と `Project.name` は既に分離されている。表示名を識別子に使わない。

命名規則: Placemark名があれば `{DATE}_{Placemark名}`、無ければ `{DATE}_{FROM}-{TO}`、いずれも無ければ `{DATE}_route`。`{DATE}` は JST。正規化は前後空白除去・制御文字除去・ファイル名禁止文字の `_` 置換・60文字への切り詰め。

自動追従は名前が自動生成のままである間のみ行い、利用者が編集した後は行わない。追従の有無はUI層で保持する（`Project.metadata` に記録してよい）。

同名の採番（`_2`, `_3`）は初回保存時のみ行う。保存先が `projects/{project_id}/` である以上ファイル名は衝突しないため、採番は表示上の識別のためだけに行う。

### 6.2 ALT / Phase の入力仕様

利用者がLeg単位で編集する航法入力は次の2項目とする。

- `planned_altitude_ft_msl: float`（ft MSL）
- `phase: FlightPhase`（CLIMB / CRUISE / DESCENT / VISUAL_ARRIVAL、既定CRUISE）

Loss Timeの入力UIは提供しない。旧 `NavSection.loss_time_seconds` は第6.5節の移行表示専用であり、Forecast・航法計算・燃料計算に渡さない。

`NavSection.safe_enroute_altitude_ft_msl` は旧schema読込互換のため残してよいが、入力欄、計算、比較、確認、Issue、fingerprint、結果表、清書ビュー、転記補助HTMLの対象外とする。

### 6.3 転記補助HTMLの出力可能条件

`CalculationOutcome.blockers` だけでなく、参照データ、陳腐化、既定値確認、手動QNH、永続状態検証を統合した `effective_issues` を唯一の判定材料とする。

```python
effective_issues = dedupe(
    outcome_effective_issues
    + reference_data_issues
    + stale_result_issues
    + defaults_review_issues
    + manual_qnh_issues
    + project_validation_issues
)
```

```text
outcome が存在する
AND effective_issues に BLOCKER がない
AND acknowledgement_required の全Warningについて複合ack keyが承認集合にある
AND current_calculation_input_fingerprint == calculated_against_fingerprint
AND view_mode == EDITABLE
```

SEA確認、SEA値、SEA Issueはこの集合・条件へ入れない。保存された `ProjectStatus` は権威ではなく、次の純関数で再導出する。

```python
def derive_project_status(effective_issues, acknowledged_warning_codes, *, outcome_exists):
    if any(e.ctx.code == "PROJECT_STATE_INVALID" for e in effective_issues):
        return ProjectStatus.MANUAL_INPUT_REQUIRED if outcome_exists else ProjectStatus.DRAFT
    if any(e.ctx.code == "ROUTE_INCOMPLETE" for e in effective_issues):
        return ProjectStatus.ROUTE_INCOMPLETE
    blockers = [e for e in effective_issues
                if e.effective_severity == IssueSeverity.BLOCKER]
    if blockers:
        if any(e.ctx.code.startswith(("FORECAST", "WEATHER")) for e in blockers):
            return ProjectStatus.WEATHER_PENDING
        return ProjectStatus.MANUAL_INPUT_REQUIRED
    if not outcome_exists:
        return ProjectStatus.DRAFT
    if any(e.effective_acknowledgement_required
           and e.ctx.ack_key not in acknowledged_warning_codes
           for e in effective_issues):
        return ProjectStatus.CALCULATION_WARNING
    return ProjectStatus.READY_FOR_COPY
```

`Project.status`、`CalculationOutcome.status`、Snapshot内statusは同じ導出値の投影である。再計算、承認変更、参照データ・既定値・QNH・陳腐化・validation変更、読込、保存、Snapshot、出力直前に再導出する。

```python
class IssueProducer(str, Enum):
    OUTCOME = "OUTCOME"
    REFERENCE_DATA = "REFERENCE_DATA"
    STALE_RESULT = "STALE_RESULT"
    DEFAULTS_REVIEW = "DEFAULTS_REVIEW"
    MANUAL_QNH = "MANUAL_QNH"
    PROJECT_VALIDATION = "PROJECT_VALIDATION"

@dataclass(frozen=True)
class IssueContext:
    code: str
    section_id: UUID | None
    segment_sequence: int | None
    canonical_cause_json: str
    cause_fingerprint: Sha256Hex
    ack_key: str

@dataclass(frozen=True)
class EffectiveIssue:
    issue: Issue
    ctx: IssueContext
    effective_severity: IssueSeverity
    effective_acknowledgement_required: bool
    producers: frozenset[IssueProducer]
```

`create_effective_issue()` だけがcauseを第10.1a節のcanonical JSONへ正規化し、`cause_fingerprint` と `ack_key` を構築する。元Issue・metadata・cause dictを後から変更しても凍結identity、安全属性、producer、keyが変化してはならない。`dedupe()` は `cause_fingerprint` で畳み、最重severity、承認要求のOR、producer集合の和を保持する。承認も同じctxの複合 `ack_key` を使い、生のIssue codeだけでは成立しない。

個別causeは `DEFAULTS_NOT_REVIEWED`、`MANUAL_QNH_RECONFIRM_REQUIRED`、`RECALCULATION_REQUIRED`、`PERFORMANCE_DATA_UNVERIFIED`、参照データIssueについて、それぞれ対応fingerprint・reason・選択snapshotを使う。それ以外のOutcome IssueはOutcome生成時の `calculated_against_fingerprint` と正規化済み `issue.metadata` をcauseとする。SEA用cause規則と `IssueProducer.SEA` は定義しない。

`Project.acknowledged_warning_codes: set[str]` の型は維持するが、格納値は複合 `ack_key` だけとする。旧形式の生codeは無視する。現在見えていないという理由だけで過去keyを自動GCしない。

Snapshotはdedupe済み全EffectiveIssueを、canonical cause、凍結安全属性、producer集合、Outcome issue index/hash binding、作成時UTC、records fingerprintを持つstrict envelopeとして保存する。loaderは保存statusを信用せず、schema、canonical JSON、digest、表示Issue、Outcome bindingを検証して同じfactoryから再構築する。不整合は `PROJECT_STATE_INVALID` へfail closeする。SHA-256は整合性検査であり署名ではない。

### 6.4 保存・Snapshotの可否

| 操作 | 可否条件 | API |
| --- | --- | --- |
| 保存・自動保存 | Project生成後は常に可能 | `save(project, expected_revision)` / `autosave()` |
| Snapshot | `outcome is not None` | `ProjectService.snapshot(project, outcome, ...)` |
| 転記補助HTML | 第6.3節の全条件を満たす | — |

Outcomeがない間はSnapshotボタンを理由・対処付きで無効化する。Snapshotは `SNAPSHOT_READONLY` で開き、自動取得・再計算・編集・保存を行わない。SEA job状態の正規化は存在しない。

### 6.5 ETO / ETE / Loss Time

#### 教範上の意味

教範はETOの単一式を記載していないが、第8章の各手順から次の関係に固定する。根拠は、TTL TIMEからLossを除く8-(3)、区間ETEを定める8-(4)、発動時刻をETOの基準にする8-(10)、回避・高度変更・鋭角変針のLossを扱う8-(18)・8-(19)・8-(21)、NAV2第1 LegとCPでのETO更新を扱う8-(34)・8-(35)である。

```text
ETO(target)
  = 直近の実Time Check時刻
  + その地点からtargetまでの再計算ETE
  + そのTime Check後に適用するLoss Time
```

発動点では実際の発動時刻を基準にCP・変針点のETOを求める。CPでは実通過時刻を新しいanchorとし、実測GSから残距離のETEを再計算する。それ以前のLossは実時刻へ吸収済みなので再加算しない。

Loss Timeは風によるGS変化ではない。風の影響はETEへ反映する。Lossは離陸方向とDeparture Courseの差、発動、巡航高度変更、Airport Work、回避、鋭角変針等により、**飛行中に事前計算結果を修正するため加える値**である。

#### 地上準備の契約

本アプリの目的はKMLからNAV LOGの地上準備を完了することであり、実Time Check後の機上修正は対象外とする。したがってLossを地上計画へ予測入力しない。

```python
zone_ete_seconds = nominal_enroute_time_without_loss
cumulative_ete_seconds = sum(zone_ete_seconds)
ttl_time_seconds = final(cumulative_ete_seconds)

planned_elapsed_seconds = sum(zone_ete_seconds)
planned_endpoint_time_utc = planned_departure_time_jst + planned_elapsed_seconds
```

- Forecastの開始・代表・終了時刻および気象反復は、Lossを含まない `planned_elapsed_seconds` を使用する
- Section燃料・残燃料・MIN REQUIREDへLoss燃料を加えない。固定Additional 10分・2.8 galは従来どおり別枠で保持する
- 転記補助HTMLのETO・ATO・ATE・TAKE OFF・LANDINGは空欄とする
- `SectionResult.eto_utc` はschema互換のため残すが、地上計算Outcomeでは `UNAVAILABLE` / `None` とする。ETD基準のForecast内部時刻をETO名で画面・HTML・JSON・Snapshotへ保存しない

`NavSection.loss_time_seconds` は旧Project読込互換のためfield自体を残すが、v2.5.1以降はdeprecatedである。新規Projectは常に0、入力UIは提供せず、計算・Forecast・燃料・defaults確認・計算入力fingerprint・転記補助出力のすべてから除外する。旧Projectの非0値は自動で計算へ混入させず、読取専用の「旧計画LOSS（現在は非適用）」として移行情報にだけ表示する。明示的な〔旧LOSSを消去〕操作で0へできるが、無関係な保存・autosaveが値を黙って変更してはならない。

飛行中に行うLossの算出・記入、実測GSによるETO更新、Position Report reviseは本アプリの地上準備スコープ外であり、原NAV LOGへ手書きする。

### 6.6 VREP通過高度とEOC

#### 入力と計算式

目的空港として使用するAirport snapshotは、MSLの `elevation_ft_msl` とmasterの `pattern_altitude_ft_msl` を必須で持つ。経路を確定するまで目的空港・場周高度Inputは無効とし、確定後に目的空港を選択するとmaster値を初期表示する。利用者は東西場周、機種、管制調整その他の当該運用に応じて100 ft単位で編集できる。〔目的空港・場周高度を確定〕で `ArrivalPlan.selected_pattern_altitude_ft_msl` と `selected_pattern_altitude_source`（masterと一致なら `AUTOMATIC`、異なれば `MANUAL`）へ保存する。

距離計算ではARPのWGS84座標 `arp_coordinate` を使用する。高度計算ではArrivalPlanで確定した `selected_pattern_altitude_ft_msl` を `selected_pattern_altitude_ft_msl` とし、これへ500 ftを加えて5 NM基準高度とする。空港標高の100 ft half-up + 1,000 ftは、資料で明示値を確認できない場合にmaster初期値を作るフォールバックに限って使用し、確定済みの実運用高度を上書きしない。

画面・転記補助HTMLには、master場周高度、ArrivalPlan採用場周高度、採用元（`AUTOMATIC / MANUAL`）、5 NM基準高度を並べて表示する。大分の例では、東側を採用するProjectは1,000 ft→5 NM 1,500 ft、西側を採用するProjectはInputを1,300 ftへ編集し→5 NM 1,800 ftとする。滑走路方向だけから東西場周を自動決定してはならず、計画・管制調整に基づき利用者が確定する。

#### master場周経路高度の決定順序

参照データの整備時に、目的空港ごとに次の資料集合を全て検索する。同名の宮崎空港資料2ファイルは内容が同一でも、提供された2ファイルを確認対象として記録する。

1. `第８章_改正17.pdf`（SHA-256: `9d0d5b7571a5ecf701085bb5780418e00c1aafb941fba681bb4f5ebb5c08d795`）
2. `宮崎空港及びその周辺における訓練飛行実施要領（R6.5.1改正） (1).pdf`（SHA-256: `29b21992454cc2c9995512dec2f011004ddd839ef9b7784d5b4696fa13e4eab7`）
3. `航空大学校所属航空機の他空港利用に関する調整事項[2026.4.1].pdf`（SHA-256: `dbf6a3c53e2fe67ac4cbf21f07c05fc2f8c83dc6894825254cd55f6e72c8e379`）
4. `航大版　運航情報サーキュラー.pdf`（SHA-256: `985a08f71f37888aa2c42ad8aae02b4ea3e5c91474c5b100e24abae45fb79d49`）
5. `鹿屋進入管制区及び鹿屋管制圏における航空大学校所属機の訓練飛行の実施に関する申し合わせ.pdf`（SHA-256: `bd72d2176d58716fb50cdadab57f813eacbdbcd97f193280ff7f05b9a0e31ff3`）
6. `民間訓練試験空域（鹿児島）使用要領_2.pdf`（SHA-256: `19172fb00e9a272968646ab9bc3e7081a24b6397292f8d32e7a399013b01d5ac`）
7. `宮崎空港及びその周辺における訓練飛行実施要領（R6.5.1改正）.pdf`（SHA-256: `29b21992454cc2c9995512dec2f011004ddd839ef9b7784d5b4696fa13e4eab7`）

決定順序は次のとおりとする。

1. **資料明示値を優先**: 目的空港を特定し、航空大学校所属機の対象運用へ適用される「場周経路の高度」を明示する記載があれば、そのMSL値を正解とする。出典にはファイル名、改正日または版、章・項、文書上のページを保存する。
2. **確認できなければ式を正解とする**: 全資料に明示値がない、適用対象を一意に決められない、または記載同士の優先関係を確定できない場合は、`derived_pattern_altitude_ft_msl = round_half_up_100(elevation_ft_msl) + 1,000 ft` を正解とする。この値は暫定値や未検証値ではなく、`VERIFIED` として扱う。

資料中のVREP通過高度、進入最低通過高度、出発時の維持高度、待機高度、訓練空域の上下限、障害物回避高度、および他機種・他運用だけに適用される高度は、場周経路高度の明示値として扱わない。「1,500 ft以上で進入する」等の記載から差し引いて推定してはならない。

判定方法は次の閉集合で管理する。現行schemaでは独立列を増やさず、下表の判定方法を
`pattern_altitude_source` と `pattern_altitude_source_revision` の来歴へ保存する。

- `DOCUMENTED_OPERATIONAL_VALUE`: 資料に当該空港・対象運用の場周経路高度が明記されている
- `DESIGN_FORMULA_FALLBACK`: 全資料を確認したが明示値を確定できず、第6.6節の式を正解として採用した

`DESIGN_FORMULA_FALLBACK` の `pattern_altitude_source` は `DESIGN.md §6.6 formula after review of provided 7-PDF operational bundle` とし、`pattern_altitude_source_revision` には本書の版、資料集合の照合日、空港標高の出典revisionを含める。したがって、根拠のない「標高+1,000 ft」の入力は引き続き `VERIFIED` にできないが、全資料の照合結果と100 ft単位half-upの入力来歴を記録した本フォールバック値は `VERIFIED` にできる。

今回の資料照合による同梱空港の正解値は次のとおりである。
マスター対象は、提供資料の「他空港AVGAS給油有無および同時駐機可能機数」に掲載された13空港へ基地空港RJFMを加えた14空港とする。同資料の「空港施設使用届及び着陸料減免申請空港一覧」だけに現れる壱岐・対馬・小値賀・上五島は、訓練利用13空港の表に含まれないため同梱seedには含めない。利用対象へ追加する場合は、資料上の位置付けを確認して新しい不変revisionを作る。

ARP座標と `elevation_ft_msl` はAIP Japan AD 2.2を正とし、気象庁「航空気候情報2025年版」の空港名・座標・標高で照合する。AIPと気象観測地点の座標または標高が異なる場合、VREP距離と式フォールバックにはAIPのARP・AD elevationを使用する。取得経路がAIP公開ミラーの場合はURL、取得日、AD2-1有効日を `source_revision` に残し、リリース前に最新AIS Japanとの差分を再確認する。

| 空港 | AIP標高 | 正解値（MSL） | 判定方法 | 根拠 |
| --- | ---: | ---: | --- | --- |
| RJFC 屋久島 | 122 ft | 1,100 ft | `DESIGN_FORMULA_FALLBACK` | 122 → 100 + 1,000 |
| RJFE 福江 | 251 ft | 1,300 ft | `DESIGN_FORMULA_FALLBACK` | 251 → 300 + 1,000 |
| RJFG 種子島 | 768 ft | 1,800 ft | `DESIGN_FORMULA_FALLBACK` | 768 → 800 + 1,000 |
| RJFK 鹿児島 | 891 ft | 1,900 ft | `DESIGN_FORMULA_FALLBACK` | 891 → 900 + 1,000 |
| RJFM 宮崎 | 19 ft | 1,000 ft | `DOCUMENTED_OPERATIONAL_VALUE` | 「宮崎空港及びその周辺における訓練飛行実施要領（R6.5.1改正）」Ⅵ 1.(1)、文書p.9 |
| RJFO 大分 | 17 ft | 1,000 ft | `DESIGN_FORMULA_FALLBACK` | 17 → 0 + 1,000 |
| RJFS 佐賀 | 6 ft | 1,000 ft | `DOCUMENTED_OPERATIONAL_VALUE` | 「他空港利用に関する調整事項」PDF p.74、航大SR-22 |
| RJFT 熊本 | 632 ft | 1,700 ft | `DOCUMENTED_OPERATIONAL_VALUE` | 「他空港利用に関する調整事項」PDF p.34、南側場周経路。2020-10-01調整 |
| RJFU 長崎 | 8 ft | 1,000 ft | `DESIGN_FORMULA_FALLBACK` | 8 → 0 + 1,000 |
| RJOA 広島 | 1,086 ft | 2,100 ft | `DESIGN_FORMULA_FALLBACK` | 1,086 → 1,100 + 1,000 |
| RJOB 岡山 | 785 ft | 1,800 ft | `DESIGN_FORMULA_FALLBACK` | 785 → 800 + 1,000 |
| RJOK 高知 | 29 ft | 1,000 ft | `DESIGN_FORMULA_FALLBACK` | 29 → 0 + 1,000 |
| RJOM 松山 | 13 ft | 1,000 ft | `DESIGN_FORMULA_FALLBACK` | 13 → 0 + 1,000 |
| RJOT 高松 | 607 ft | 1,600 ft | `DESIGN_FORMULA_FALLBACK` | 607 → 600 + 1,000 |

`ARRIVAL_ALTITUDE_RULE_VERSION = "CAC_REV19_8_4_9_V4"` とする。

```python
def round_half_up_nonnegative(value: float) -> int:
    return floor(value + 0.5)

d_nm_exact = wgs84_distance_nm(
    vrep.coordinate,
    destination_airport.arp_coordinate,
)

if abs(d_nm_exact - 5.0) * 1852.0 <= 1.0:
    effective_d_nm = 5.0
else:
    effective_d_nm = d_nm_exact

selected_pattern_altitude_ft_msl = arrival_plan.selected_pattern_altitude_ft_msl
base_vrep_altitude_ft_msl = selected_pattern_altitude_ft_msl + 500

excess_distance_nm_exact = max(0.0, effective_d_nm - 5.0)
excess_distance_nm_rounded = round_half_up_nonnegative(
    excess_distance_nm_exact
)
vrep_altitude_ft_msl = (
    base_vrep_altitude_ft_msl
    + 200 * excess_distance_nm_rounded
)
```

距離はKML終端ではなく、選択済み目的空港snapshotのARP座標までのWGS84測地線距離を使用する。5 NMとの差が1 m以内は `effective_d_nm = 5.0` とする。5 NM以下の高度はbase値、5 NM超過分は距離差を1 NM単位でhalf-upして整数化した後、200 ft/NMを加える。距離表示用の0.5 NM丸め値から高度を求めてはならない。

例:

- 採用場周高度1,500 ft → 5 NM基準高度2,000 ft
- 採用場周高度1,000 ft → 5 NM基準高度1,500 ft
- 採用場周高度1,300 ft、距離8.0 NM → 5 NM基準1,800 ft＋超過3 NM×200 ft＝2,400 ft（教範8-4-9(2)の例と一致）
- RJFO東側を1,000 ftで確定 → 5 NM基準高度1,500 ft
- RJFO西側を1,300 ftで確定 → 5 NM基準高度1,800 ft
- 採用場周高度1,500 ftで5.49 NM → 超過0.49 NMを0 NM → 2,000 ft
- 同じ採用高度で5.50 NM → 超過0.50 NMを1 NM → 2,200 ft

```python
class ArrivalAltitudeMode(StrEnum):
    STANDARD_DISTANCE_RULE = "STANDARD_DISTANCE_RULE"
    MANUAL_NON_STANDARD_ENTRY = "MANUAL_NON_STANDARD_ENTRY"

class ArrivalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    visual_reporting_point_node_id: UUID
    selected_pattern_altitude_ft_msl: int | None = Field(
        default=None, ge=100, le=25000, multiple_of=100
    )
    selected_pattern_altitude_source: AdoptedSource | None = None
    altitude_mode: ArrivalAltitudeMode = ArrivalAltitudeMode.STANDARD_DISTANCE_RULE
    manual_vrep_altitude_ft_msl: int | None = Field(
        default=None, ge=-1000, le=25000, multiple_of=100
    )
    manual_override_reason: str | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "ArrivalPlan":
        if (self.selected_pattern_altitude_ft_msl is None) != (
            self.selected_pattern_altitude_source is None
        ):
            raise ValueError("selected pattern altitude and source must be supplied together")
        if self.altitude_mode == ArrivalAltitudeMode.STANDARD_DISTANCE_RULE:
            if (
                self.manual_vrep_altitude_ft_msl is not None
                or self.manual_override_reason is not None
            ):
                raise ValueError("standard arrival must not contain manual fields")
        elif (
            self.manual_vrep_altitude_ft_msl is None
            or not (self.manual_override_reason or "").strip()
        ):
            raise ValueError("manual arrival requires altitude and reason")
        return self

class ArrivalAltitudeResult(CalculationModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    vrep_node_id: UUID
    destination_airport_id: str
    distance_nm_exact: FiniteFloat = Field(ge=0)
    effective_distance_nm: FiniteFloat = Field(ge=0)
    boundary_tolerance_m: Literal[1.0] = 1.0
    airport_elevation_ft_msl: FiniteFloat = Field(ge=0)
    airport_elevation_rounded_ft_msl: int = Field(ge=0, multiple_of=100)
    pattern_altitude_ft_msl: FiniteFloat = Field(ge=0)  # master値
    selected_pattern_altitude_ft_msl: int = Field(ge=100, multiple_of=100)
    selected_pattern_altitude_source: AdoptedSource
    derived_pattern_altitude_ft_msl: int = Field(ge=100, multiple_of=100)  # 互換field。selectedと同値
    base_vrep_altitude_ft_msl: int = Field(multiple_of=100)
    excess_distance_nm_exact: FiniteFloat = Field(ge=0)
    excess_distance_nm_rounded: int = Field(ge=0)
    automatic_altitude_ft_msl: int = Field(multiple_of=100)
    adopted_altitude_ft_msl: int = Field(multiple_of=100)
    adopted_source: AdoptedSource
    manual_override_reason: str | None = None
    selected_reference_fingerprint: Sha256Hex
    rule_version: Literal["CAC_REV19_8_4_9_V4"]
```

`ArrivalAltitudeResult` のmodel validatorは、`effective_distance_nm`、`selected_pattern_altitude_ft_msl`、`base_vrep_altitude_ft_msl`、`excess_distance_nm_*`、`automatic_altitude_ft_msl` を第6.6節の式から再導出し、保存値との完全一致を要求する。Projectの採用場周高度を変更した場合は計算入力fingerprintを変え、降下・EOC・VREP高度を再計算する。

Direct Base等の変則Entryは `MANUAL_NON_STANDARD_ENTRY` とし、100 ft単位の高度と空でない理由を必須にする。理由欠損は `ARRIVAL_ALTITUDE_OVERRIDE_REASON_REQUIRED`（BLOCKER）。自動値は比較・監査用に併記する。

Projectと参照snapshotを突き合わせるcross-model検証で、結果の `airport_elevation_ft_msl` とmasterの `pattern_altitude_ft_msl` が目的空港snapshotと一致し、`selected_pattern_altitude_ft_msl` と `selected_pattern_altitude_source` がArrivalPlanの確定値と一致することを必須とする。採用場周高度は100 ft単位かつ `destination_airport.elevation_ft_msl` より高くなければならない。未確定または同値以下は `PATTERN_ALTITUDE_REQUIRED`（BLOCKER）とし、低い値を自動補正しない。STANDARDではVREP採用高度が自動値と一致し、MANUAL_NON_STANDARD_ENTRYでは理由と手動VREP高度が一致することを検証する。

#### 経路条件と利用先

- VREPは `RouteNodeRole.VISUAL_REPORTING_POINT` で、目的空港直前のRouteNodeとする
- VREP→目的空港を `VISUAL_ARRIVAL`、その直前を `DESCENT` とする
- KML取込時は最後の非空港地点を候補提示するだけとし、利用者確認なしにVREPへ確定しない
- 条件を満たすVREPがない場合は `VISUAL_REPORTING_POINT_REQUIRED`、位置関係が不正なら `VISUAL_REPORTING_POINT_ROUTE_INVALID`（いずれもBLOCKER）
- 解決したVREP高度を、降下目標、500 fpm降下時間、EOC、DESCENT代表気象高度、VISUAL_ARRIVAL代表気象高度、VISUAL_ARRIVAL行のALTへ同じ値として渡す
- 全Leg高度一括変更は自動VREP高度を上書きしない。VREP高度の編集操作は手動overrideへの明示切替とする

`CalculationOutcome.arrival_altitude: ArrivalAltitudeResult` は上記の完全な型を保持する。Snapshotは同じJSONを保存し、読込時に `selected_reference_fingerprint` とrule versionを再検証して再現する。

### 6.7 Check Pointとabeam stationing

CPは地上目標物であり、経路上にある必要はない。CP座標をRouteNodeへ追加して飛行経路を曲げてはならない。既存の `VisualReference` を使用し、`role == CHECK_POINT`、`linked_section_id` 必須とする。最寄りLegは候補提示に留め、利用者が関連Legを確認する。リンクなしは下書き保存のみ許容し、計算・転記補助出力では `CP_LINK_REQUIRED`（BLOCKER）。

#### abeam点

関連付けた有限WGS84 Legの始点A、終点B、CP座標Pについて、Leg上の点QとPのWGS84測地距離が最小となるQをabeam点とする。

```text
Q = argmin distance_WGS84(P, geodesic_A_to_B(s)), 0 <= s <= leg_length
f = distance_WGS84(A, Q) / distance_WGS84(A, B)
```

実装は既存 `geographiclib` のGeodesicLine上で決定的な有界一次元最小化を行い、station誤差1 m以下とする。最小点が端点から1 m以内の場合は端点へ丸めず `CP_NOT_ABEAM_LINKED_SECTION`（BLOCKER）とし、別Legの選択を求める。

```python
class CheckPointProjection(CalculationModel):
    checkpoint_id: UUID
    section_id: UUID
    abeam_latitude_deg: float
    abeam_longitude_deg: float
    along_track_fraction: float
    along_section_distance_nm: float
    cumulative_distance_nm: float
    cross_track_distance_nm: float
    policy_version: Literal["CP_ABEAM_WGS84_V1"]
```

距離規則は次に固定する。

- CPまでのZONE DIST = `f * 当該Sectionの採用距離`
- CPのCUM DIST = 先行Sectionの採用距離合計 + 上記ZONE DIST
- CP→Section終点 = `(1-f) * 当該Sectionの採用距離`
- 手入力距離が採用されている場合も、fractionはWGS84幾何から求め、距離へは採用済み手入力距離を掛ける
- CP地上座標までの斜距離とcross-track距離は情報表示のみで、ZONE/CUM DISTへ加えない
- CPは同じ飛行経路上の計算境界を作るだけで、Route総距離・TC・MC・MHを変更しない

同一Sectionに複数CPがある場合は `along_track_fraction` 昇順に並べる。同値または1 m以内の重複stationはBlocker。CP境界を既存phase segmentationへ渡し、各zoneのETE・燃料を元のphase規則で再計算する。Loss Timeは第6.5節により地上計画へ含めない。分割前後でDIST・ETE・燃料の合計が表示丸め前に一致することを要求する。

`VisualReference.along_track_fraction` を入力値として信頼・永続化してはならない。計算のたびに座標とlinked Sectionから導出し、結果としてのみ保存する。CP座標・名称・role・linked Section・stationing policyは計算入力fingerprintへ含める。経路変更でlinked Sectionが消えた場合、CP自体は削除せずリンクを `None` にして再確認を要求する。

Webから追加したCPは`source=WEB_MANUAL`とし、Project保存・再読込で同じUUID、座標、関連Legを
保持する。親SectionのPhaseだけを見てCRUISE以外を拒否しない。1つの物理Leg内でRCA/EOC後に
Phaseが変わるため、全物理Legを選択可能にし、15〜20 NM間隔は案内に留める。

### 6.8 差し替え可能な参照データ

#### パック構成

空港・一般地点・CPを次の独立ファイルで1つの参照データパックとする。

```text
reference-manifest.json
airports.csv
points.csv
checkpoints.csv
```

- `airports.csv`: `id,icao,name,latitude_deg,longitude_deg,elevation_ft_msl,pattern_altitude_ft_msl,pattern_altitude_source,pattern_altitude_source_revision,pattern_altitude_validation_status,source,source_revision`
- `points.csv`: `id,name,latitude_deg,longitude_deg,point_role,source,source_revision,notes`
- `checkpoints.csv`: `id,name,latitude_deg,longitude_deg,source,source_revision,notes`
- `point_role`: `DEPARTURE_REFERENCE / TURN_POINT / VISUAL_REPORTING_POINT / ROUTE_POINT`
- CPとLegの関連は経路ごとに変わるため、マスターCSVへは格納せずProject側で指定する
- `pattern_altitude_validation_status`: `VERIFIED / UNVERIFIED / REJECTED` の閉集合。目的空港として採用できるのは `VERIFIED` かつ場周経路高度固有の出典・revisionが空でない行だけ

`reference-manifest.json` は `schema_version=1`、`dataset_id`、`revision`、`created_at_utc` と、各CSVのkind・相対path・SHA-256・row countを必須とする。未知field、重複ID、非finite値、不正緯経度、path traversal、hash/row count不一致を拒否する。論理identityは `(dataset_id, entity_kind, row_id)`、特定版はさらに `revision` とcanonical row fingerprintで識別する。

#### 保存とCRUD

同梱既定パックを読み取り専用の初期値とし、利用者編集版は `MyDrive/AutoNavLog/reference-data/catalogs/{dataset_id}/{revision}/` へ不変revisionとして保存する。`active.json` は現在選択中のdataset/revisionだけを指す。追加・編集・削除は旧revisionを上書きせず、新revisionを作成して検証成功後にactive pointerを原子的に更新する。UIは次を提供する。

- パックの取込・書出・有効化・前版へ戻す
- 空港・地点・CPの検索、追加、編集、削除
- 変更前後diffと出典・revisionの確認
- Drive未接続時の一時catalog編集と、接続後の明示移行

FROM/TOはactive catalog内の全空港から検索・選択でき、KML端点からの最寄り空港は候補提示に留める。RJFM/RJFOをruntime固定値として設定してはならない。空港選択と経路端点座標は独立させ、座標移動は〔空港公示座標へ合わせる〕の明示操作だけで行う。

#### Project snapshot

マスター行をlive参照すると、差し替え・削除後に保存Projectを再現できないため、Projectで選択した空港・地点・CPの完全なcanonical行と次のoriginを `PersistedUiState.reference_data_snapshot` へ保存する。

```python
class MasterReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    dataset_id: str
    dataset_revision: str
    entity_kind: Literal["AIRPORT", "POINT", "CHECK_POINT"]
    entity_id: str
    row_fingerprint: Sha256Hex

class ReferenceDataSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    departure_airport: AirportSelection
    destination_airport: AirportSelection
    route_points: dict[UUID, PointSelection] = Field(default_factory=dict)
    check_points: dict[UUID, CheckPointSelection] = Field(default_factory=dict)
```

各Selectionは行の全fieldと `origin: MasterReference | None` を持つ。計算・再読込はsnapshotを使用し、active catalogに同じ行がなくても成立する。active catalog変更は「更新あり」を表示するだけで、既存Projectへ自動反映しない。〔最新マスターを反映〕で対象行ごとの差分を確認・選択した場合だけsnapshotを更新し、再計算・CP再投影を要求する。

計算入力fingerprintには全catalogではなく、選択済みsnapshot、ProjectへコピーしたRouteNode、選択済みCPと `CP_PROJECTION_POLICY_VERSION` だけを含める。active catalogへの無関係な行の追加・削除で既存Projectを陳腐化させてはならない。

目的空港snapshotの `pattern_altitude_ft_msl`、固有出典・revision、`VERIFIED` は必須である。提供資料に対象運用の明示値がある場合はその値を用い、全資料で確認できない場合は第6.6節の式と照合来歴を根拠に `VERIFIED` とする。根拠と来歴のない機械生成値だけを `VERIFIED` にしてはならない。
---

## 8. SEA・地形機能（本版では対象外）

v2.6.0では、SEAおよびその算出に必要な地形機能を実装しない。

対象外には次を含む。

- DEM10Bの取得・復号・補間・回廊走査
- 陸域マスク、海岸線不確実帯、陸域・水域・unknown判定
- SEA提案、最高地点、coverage、SEA手入力・採用・確認
- SEA非同期job、進捗、中止、再試行、通信deadline
- DEM一次cache、Drive二次cache、ZIP pack、index、GC、FUSE原子性検証
- SEA/DEM/land-mask用の出典表示、fixture、live canary、性能測定
- `autonavlog.terrain` 等の新規packageと `numpy` / `Pillow` の追加

既存SEA関連field・Issueコード・保存値はschema読込互換のため定義を残してよいが、本版の新規Project、再計算、ProjectStatus、出力へ寄与させない。SEAを将来追加するときは、別表を含む独立機能として新たにデータ源、安全境界、永続schema、表示、受け入れ条件を定義する。

## 10. 状態管理・再計算・陳腐化

### 10.1 Legの同一性

`NavSection.id` と `RouteNode.id` の既存UUIDを用い、新しいleg IDを導入しない。座標・順序・名称・Section入力の変更は第10.2節の計算入力fingerprintで検知する。旧SEA状態をSection IDへ関連付けたり、経路を戻した際に復元したりしない。

### 10.1a fingerprintの共通規約

本書は計算入力・既定値確認・手動QNH再確認・Issue cause等のfingerprintを用いる。再レビューにより、これらが場当たり的な擬似コード（例: `hash(...)`）で示されており、Pythonの組み込み `hash()` は文字列に対してプロセスごとに異なる値を返しうる（`PYTHONHASHSEED` によるランダム化）ため**再起動後に一致しない**という欠陥が指摘された。全fingerprintは次の共通規約に従う。

**v1.7で全面改訂**（再レビュー指摘4）: v1.6の記述は「floatは小数第6位に丸め、日時はISO8601化」という擬似コメント止まりで、再帰的な丸め・NaN/±Infinityの拒否・Enum・UUID・入れ子構造・集合の扱いが未定義であり、そのままでは実装者ごとに異なるpayloadが生成される。本節は `normalize()` を実装可能な水準まで規定し、固定テストベクトルを与える。

#### 規約

```python
FINGERPRINT_VERSION = 1
_MAX_DEPTH = 32
_MAX_SAFE_INT = 2**53 - 1

def normalize(value: Any, *, _depth: int = 0) -> Any:
    if _depth > _MAX_DEPTH:
        raise ValueError("fingerprint payload nested too deeply")
    if value is None:
        return None
    if isinstance(value, bool):          # int より先に判定する（bool は int の派生）
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INT:
            raise ValueError("integer out of safe range")
        return value
    if isinstance(value, float):
        # Decimal は非対応（末尾の TypeError へ落とす）。v2.1で変更: v2.0の
        # float(value) 経由の丸めは Decimal の精度を暗黙に失った（例:
        # Decimal("1.2345665000000000000001") は float 経由で "1.234566"、
        # Decimal のまま quantize すれば "1.234567"）。現行の fingerprint 入力に
        # Decimal は存在しないため、暗黙変換より明示的拒否を選ぶ。将来対応する
        # 場合は float 経路と分離し、is_finite 確認後に Decimal("0.000001") へ
        # 明示 rounding mode で quantize する規約を別途定めること
        if math.isnan(value) or math.isinf(value):
            raise ValueError("NaN/Infinity is not fingerprintable")
        rounded = round(value, 6) + 0.0  # +0.0 で -0.0 を 0.0 へ畳む
        return f"{rounded:.6f}"          # 小数6桁の固定文字列（float表記の揺れを排除）
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Enum):
        return normalize(value.value, _depth=_depth + 1)
    if isinstance(value, UUID):
        return str(value)                # 小文字ハイフン付き正準形
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            # tzinfo があっても utcoffset() が None を返す datetime は naive 扱い
            # （Python仕様）。tzinfo チェックのみでは不足する（v1.9で追加）
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        raise ValueError("naked time is not fingerprintable; use datetime")
    if isinstance(value, (list, tuple)):
        return [normalize(v, _depth=_depth + 1) for v in value]   # 順序は保存する
    if isinstance(value, (set, frozenset)):
        members = [normalize(v, _depth=_depth + 1) for v in value]
        return sorted(members, key=lambda m: json.dumps(
            m, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, bool) or not isinstance(key, (str, int, UUID, Enum)):
                raise ValueError(f"unsupported fingerprint key type: {type(key)!r}")
            normalized_key = normalize(key, _depth=_depth + 1)
            text_key = normalized_key if isinstance(normalized_key, str) else str(normalized_key)
            if text_key in out:
                raise ValueError(f"duplicate key after normalization: {text_key}")
            out[text_key] = normalize(item, _depth=_depth + 1)
        return out
    raise TypeError(f"unsupported fingerprint value type: {type(value)!r}")

def make_fingerprint(*, kind: str, fields: dict[str, Any]) -> str:
    canonical = normalize(
        {"fingerprint_version": FINGERPRINT_VERSION, "kind": kind, "fields": fields}
    )
    payload = json.dumps(
        canonical, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

規約の要点:

| 規則 | 内容 |
| --- | --- |
| float表現 | **小数6桁の固定文字列**（`"1.000000"`）とする。`json.dumps` のfloat表記（`1.0` と `1` の差、指数表記）に依存しない。結果として `5`（int）と `5.0`（float）は別物として区別される |
| 負のゼロ | `-0.0` は `0.0` へ畳む（`"0.000000"`） |
| NaN / ±Infinity | **例外**。`allow_nan=False` を併用して二重に防ぐ |
| int | 安全整数（±2^53−1）を超える場合は例外 |
| 文字列 | Unicode **NFC** 正規化。`ensure_ascii=False` のためUTF-8バイト列として直接ハッシュされる |
| 日時 | tz-aware必須（`tzinfo is None` **または** `utcoffset() is None` は例外。後者はtzinfoがあってもnaiveとなるPython仕様への対処。v1.9）。UTCへ変換し**マイクロ秒まで固定長**（`%Y-%m-%dT%H:%M:%S.%fZ`） |
| date | `isoformat()`（`"2026-08-02"`） |
| Enum | `value` を再帰的に正規化（`StrEnum` は文字列になる） |
| UUID | `str(uuid)`（小文字・ハイフン付き） |
| list / tuple | **順序を保存**（経路順序の同一視を防ぐため） |
| set / frozenset | 各要素の正準JSON文字列で**ソート**して安定化 |
| dict | キーは `str` / `int` / `UUID` / `Enum` のみ。正規化後に重複したら例外。`sort_keys=True` で順序非依存 |
| `Decimal` | **非対応・`TypeError`**（v2.1で変更。v2.0までのfloat経由丸めは精度を暗黙に失った。現行入力にDecimalは無い） |
| その他の型 | **例外**（暗黙の `str()` を行わない） |
| 入れ子深さ | 32を超えたら例外（`Issue.metadata` 等の外来データ対策） |
| `fields` の位置 | `{"fingerprint_version", "kind", "fields"}` の3キー構造とする。v1.6は `**fields` で展開しており、`fields` に `"kind"` というキーがあると衝突しうる欠陥があった |

#### 固定テストベクトル（実測値）

実装は次を**単体試験の固定値**として検証する（本表の値は本規約の参照実装で実際に算出した）。

`kind="test_vector"`、`fields` は次のとおり（V1）:

```python
{
  "int_value": 42, "float_value": 1.0, "rounded_float": 0.12345649,
  "negative_zero": -0.0, "bool_value": True, "none_value": None,
  "text": "宮崎", "uuid": UUID("0f4f2a34-2f0e-4a3c-8b2a-1d9f6e5c4b3a"),
  "when": datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc),
  "on": date(2026, 8, 2),
  "nested": {"b": [1, 2.5], "a": {"deep": "x"}},
  "as_set": {"b", "a", "c"},
}
```

正準payload（V1）:

```
{"fields":{"as_set":["a","b","c"],"bool_value":true,"float_value":"1.000000","int_value":42,"negative_zero":"0.000000","nested":{"a":{"deep":"x"},"b":[1,"2.500000"]},"none_value":null,"on":"2026-08-02","rounded_float":"0.123456","text":"宮崎","uuid":"0f4f2a34-2f0e-4a3c-8b2a-1d9f6e5c4b3a","when":"2026-08-02T00:00:00.000000Z"},"fingerprint_version":1,"kind":"test_vector"}
```

| # | 差分 | SHA-256 |
| --- | --- | --- |
| V1 | 上記のまま | `cbf72b4daefa6a82e1e68be85b92b7783a52a0573a3f42b9362f8790fef291bd` |
| V2 | `rounded_float` を `0.12345651` へ（6桁目が変わる） | `dcc631c0d34cc59a19f412895b9ed1d13f07b22a8d19c38bd5237a6a8fd3a5ff` |
| V3 | `rounded_float` を `0.1234565` へ（丸めるとV1と同値） | `cbf72b4daefa6a82e1e68be85b92b7783a52a0573a3f42b9362f8790fef291bd`（**V1と一致すること**） |

SEA専用の固定vectorは本版では試験対象に含めない。


拒否されることを検証する入力: `float("nan")` / `float("inf")` → `ValueError`、naive `datetime` → `ValueError`、**`tzinfo` はあるが `utcoffset()` が `None` を返す `datetime`**（`datetime.replace(tzinfo=tzinfo())` のような素の `tzinfo` 基底クラス） → `ValueError`（v1.9で追加）、**`Decimal`（有限値を含む） → `TypeError`**（v2.1で追加）、任意のオブジェクト → `TypeError`。

`geometry_fingerprint`（既存・SHA-1[:16]）はLeg同一性検知用の軽量版として維持し変更しない。それ以外の新規fingerprint（`current_calculation_input_fingerprint` 等、第10.2節・第10.4節・第10.3節「手動QNHの再確認規則」・第6.3節のIssue causeで定義するもの）は本節の `make_fingerprint()` を用いる。対象フィールドは各節で個別に**完全に列挙**する（`sort_keys=True` によりPython側で順序を意識する必要はないが、フィールド集合そのものは各節の記載を正とする）。`fingerprint_version` を上げた場合、保存済みの旧fingerprintとは一致しなくなり、結果として全件が「陳腐化」側へ倒れる（安全側）。

### 10.2 計算入力fingerprint

| クラス | 対象 | 再計算 |
| --- | --- | --- |
| DISPLAY | `pilot_name`、`ship_identifier`、Project名 | 不要 |
| HEADING | RouteNode座標（各Leg出発緯度からVARを導出） | 必要 |
| FUEL | `total_usable_fuel_gal`、`tgl_count` | 必要 |
| NAV | DATE/ETD、ALT、Phase、QNH、Forecast Run、経路、ArrivalPlan、選択参照データsnapshot、CP、Section手動気象、性能・policy・アプリ・MSM版 | 必要 |

`current_calculation_input_fingerprint` は、第10.1a節の `make_fingerprint(kind="calculation_input", ...)` で次をcanonical化して作る。

- 飛行日、tz-aware ETD、燃料、TGL、機体profile、手動QNH、選択Forecast Run
- RouteNodeのUUID、名称、座標、role、順序
- NavSectionのUUID、from/to UUID、ALT、Phase、手動気象入力
- ArrivalPlanのVREP、到着高度mode、手動高度・理由
- CPのUUID、名称、座標、source、関連Leg
- 選択済み空港・地点・CP snapshotの全canonical行とorigin
- performance CSV実bytesのSHA-256、manifestのversion・source・validation状態・温度policy
- calculation policy、performance table、AutoNavLog、MSM packageのversion

除外するもの:

- `pilot_name`、`ship_identifier`、Project名、revision、status、作成・更新時刻
- active catalogの未選択行、旧 `loss_time_seconds`
- `safe_enroute_altitude_ft_msl` と全SEA/DEM/land-mask/cache状態
- 生の `Project.metadata`

performance fingerprintはmanifest記載hashをそのまま信用せず、明示データ読込時に実CSV bytesを再hashする。選択参照データfingerprintはactive catalog全体ではなくProjectへsnapshotした選択行だけを対象とする。リスト順序は保存する。

### 10.3 変更時の無効化規則

| 変更 | 航法再計算 |
| --- | --- |
| PILOT / SHIP / Project名 | 不要 |
| VAR / FUEL / TGL | 必要 |
| DATE / ETD / QNH / Forecast Run | 必要 |
| ALT / Phase | 必要 |
| RouteNode座標・名称・追加・削除・順序、LineString選択 | 必要 |
| ArrivalPlan、目的空港ARP座標・空港標高・master場周経路高度 | 必要 |
| CPの座標・名称・関連Leg・追加削除 | 必要 |
| active catalog切替・未選択行だけのCRUD | 不要 |
| 〔最新マスターを反映〕による選択snapshot更新 | 必要 |
| 旧 `loss_time_seconds` の消去 | 不要 |
| 旧SEA値・SEA stateだけの変更 | 不要 |

再計算が必要な変更後は `current_calculation_input_fingerprint != calculated_against_fingerprint` となり、`RECALCULATION_REQUIRED` を提示する。

```python
manual_qnh_fingerprint = make_fingerprint(
    kind="manual_qnh",
    fields={
        "flight_date": project.flight_date,
        "planned_departure_time_jst": project.planned_departure_time_jst,
        "departure_coordinate_or_airport_id": departure_coordinate_or_airport_id,
    },
)
```

DATE、ETD、出発地の変更で一致しなくなった場合はQNH値を保持したまま `MANUAL_QNH_RECONFIRM_REQUIRED` を提示する。日時は事前に文字列化せず、date/tz-aware datetimeのまま `normalize()` へ渡す。

### 10.4 既定値確認（旧Project互換のみ）

`PersistedUiState.defaults_review_fingerprint` と `DEFAULTS_NOT_REVIEWED` は旧Project／Snapshotの厳格読込と履歴表示のため保持してよい。主UIの `ReadinessService` は `require_defaults_review=False` とし、既定値一括照合Checkbox、記録Button、再確認fingerprintを転記条件にしない。ALT、Phase、FUEL、VAR、TGL、機体・性能・Policyの計算影響値は通常の計算入力fingerprintへ含め、変更後は `RECALCULATION_REQUIRED` により再計算を要求する。

### 10.5 SEA関連fingerprint（本版では使用しない）

`sea_input_fingerprint`、`sea_proposal_fingerprint`、`sea_manual_confirmation_fingerprint` およびSEA確認の有効性判定は実装しない。旧Projectにこれらの値があってもv4 UI状態へ移行せず、計算入力・承認・印刷条件へ使用しない。

### 10.6 再計算

再計算modeは通常のNAV LOG再計算だけとする。入力fingerprintが変化した場合は全航法結果を再計算する。旧版の `NAV_ONLY` / `SEA_REFRESH` の分離と〔SEAを再算出〕操作は設けない。

## 11. KMLパーサ要求

### 11.1 既存実装との差分

`importers/kml.py` は既に次を満たしている。

- `defusedxml` による解析
- `Point` / `LineString` / **`Polygon`** の処理（**v1.7で修正**: 作業ツリーはPolygonを `ImportedPolygon` として保持する。「Polygonは無視」は基準commitに対する記述で、作業ツリーには当てはまらない。FR-15・D-10）
- `Document` / `Folder` の階層、各Placemarkの直近コンテナpath、KML記載順の保持
- Polygon面（`gx:` 拡張等の非対応サーフェス）は件数を数えて警告に記録（`skipped N Polygon surface(s)`）
- 経度,緯度,高度 の順、範囲検証、空白区切り
- `ImportLimits`（アーカイブ10 MB / 展開50 MB / ファイル50 / 座標50,000 / 表示頂点2,000）
- RDPによる表示用簡略化

本改修で追加・変更するもののみを以下に示す。

### 11.2 追加要求

| ID | 内容 |
| --- | --- |
| K-1 | Polygonのみで `LineString` が0本の場合、「面を経路として使う（確認付き）」と「経路を作り直す」の2択を提示する（FR-15b。**v1.7で改訂**。旧K-1の「無視した事実をエラーとして提示」は現行実装と矛盾していた） |
| K-2 | 同じ直近 `Folder` / `Document` の複数 `LineString` を記載順・記載方向のまま連結候補にする。全隣接端点差が `0.02 NM` 以下の場合だけ `connected_lines` を作り、複数候補時はWeb UIで明示選択を要求する。自動反転・並べ替えは行わない（FR-20） |
| K-3 | DOCTYPE宣言・外部実体参照・エンティティ展開の明示的拒否（`defusedxml` の既定に依存せず設定を明示する） |
| K-4 | KMZのパストラバーサル・シンボリックリンク・暗号化エントリ・入れ子ZIP・Unicode正規化後の重複エントリの拒否 |
| K-5 | KMZ内KML選択規則の一本化（`doc.kml` 1件→自動 / 複数または大小文字重複→エラー / なしでKML1件→自動 / なしで複数→選択 / 0件→エラー） |
| K-6 | 選択した `LineString` に不正座標が含まれる場合、点を除去して続行せずエラーとする |
| K-7 | 隣接重複点の統合規則: 先頭から順に、最後に採用した点と次の点を比較し、10 m以内なら次の点を捨てる（先頭末尾の一致は統合しない）。統合後にLineStringが2点未満になった場合はエラーとする |
| K-8 | Waypoint名は `WP1`…`WPn`。Placemark名は Project名生成にのみ使用 |
| K-9 | 高度値を使用しない旨をUIに明示する |
| K-10 | 座標点数上限を二段階化する（レビュー指摘: グローバル上限を500へ一律引き下げると、複数LineStringを含むKMLで選択前の全体座標数が500を超え、選択操作前にファイル全体が拒否されてしまう）。<br>・`max_total_coordinates_in_document = 50,000`（文書全体。既存の `ImportLimits.max_coordinates` を維持。表示用簡略化のための上限） <br>・選択した1本のLineStringまたは1件の `connected_lines` は合計500点（Leg生成に用いる座標数を制限する） <br>・`max_coordinates_in_selected_polygon_outer = 500`（**v1.8で新規**。再レビュー指摘13: `ImportedPolygon.outer_boundary` は簡略化されないため、上限がないと文書上限50,000点近い外周がそのままRouteNode/Leg化されうる。Polygon外周をRoute化する場合（FR-15a）の選択後上限としてLineStringと同値を課し、超過は選択操作後にエラーとする） |
| K-11 | 接続点の両側の線端点からそれぞれ `0.02 NM` 以内にある同一コンテナのPointだけを変針点に採用する。複数一致時は「両端点までの距離の最大値が最小→距離合計が最小→KML記載順」で決定する。Pointから採用した名称のRouteNodeはsourceも `KML/KMZ Point` として保持する。該当Pointなしでは前の線の終点を使い、線端点間が10 m超なら警告する。`0.02 NM` 超では連結候補を作らず、個別LineStringと警告を残す |
| K-12 | 有効な連結候補の構成LineStringは個別候補から抑制する。連結候補または個別LineString候補が1件でもあれば全Point候補を抑制し、LineStringがない場合だけPoint-only fallbackを提示する |
| K-13 | Point PlacemarkはFR-20/K-11の接続点一致を除き、取込後に経路点/VREP/CP/参照のみの役割選択を必須とし、自動RouteNode化しない。CP選択では `VisualReference` を作りLineStringを変更せず、マスター登録も別の明示操作にする（FR-44） |

### 11.3 受け入れテスト用サンプル

初版のサンプルKML（7点・宮崎→大分）を用いる。期待値は座標点数7、Leg数6、Waypoint名 WP1…WP7、選択UI非表示、各Legの `status` は `COMPLETE`（フェイルセーフ非発動時）。

複数の分割経路を含むサンプル `大分経路.kml` では、23本のLineStringを直近Folder単位で
次の4候補にまとめる。複数候補のため初期状態は未選択とし、候補選択に応じてFROM/TO、地図、
RouteNodeの名称・座標・順序が切り替わることを確認する。

| 候補 | Leg数 | 経路点数 | 全長 |
| --- | ---: | ---: | ---: |
| `RJFM→RJFO①` | 7 | 8 | 124.66 NM |
| `RJFM→RJFO②` | 6 | 7 | 151.42 NM |
| `RJFO→RJFM①` | 5 | 6 | 112.03 NM |
| `RJFO→RJFM②` | 5 | 6 | 129.01 NM |

追加fixtureでは、不連続、逆向き、記載順違い、同名Folder、Pointなし、複数Point候補、
選択後500点超過を個別に検証する。不連続・逆向き・記載順違いは線の自動修正で救済せず、
個別LineString候補と警告へfallbackする。複数Point候補はK-11の優先順位で決定的に1点を選ぶ。

> **v1.7で更新**: `airports.csv` には `RJFM`（宮崎）・`RJFO`（大分）が整備済みであるため（第0.1節）、FROM=RJFM / TO=RJFO の自動識別は**現在の作業ツリーで判定できる**。v1.6の「空である間は成立しない」という注記は基準commitに対するものであり、もはや当てはまらない。

---

## 12. 出典・免責

画面・転記補助HTML・Snapshotには、実際に使用した気象、性能、空港・地点・CP参照データの版・出典を表示する。

最低限、次を明示する。

- MSM値は推定値であり、Forecast Run、元URL、source hash、補間方法を保存・表示する
- QNHは常に `MSM推定QNH` と表示し、公式QNHと表現しない
- 性能値は採用したperformance table versionと一次資料参照を表示する
- 空港・地点・CPは選択した参照データsnapshotのsource/revisionを表示する
- 成果物は別添8-1への地上準備用転記補助であり、そのまま機上で使う完成公式帳票ではない
- ETO/ATO/ATE/TAKE OFF/LANDINGは機上記入欄として空欄である

本版はSEA、DEM、陸域マスクを取得・算出・表示しないため、それらの出典・加工表示や安全性を主張してはならない。

## 13. 保存・Snapshot

### 13.1 既存実装を使用する

| 項目 | 既存実装 |
| --- | --- |
| 保存 | `save(project, expected_revision)` → `projects/{project_id}/project.json`。revision不一致で `project-conflict-*.json` を作成し `RevisionConflictError` |
| 自動保存 | `autosave()` → `projects/{project_id}/autosave.json` |
| Snapshot | `create_snapshot()` → `snapshots/{project_id}/{snapshot_id}.json`。既存パスなら `FileExistsError`（不変） |
| 書込み | `_atomic_json_write`（tempfile → `fsync` → `os.replace`、`ensure_ascii=False`、`sort_keys=True`） |
| Drive | `GoogleDriveProjectRepository("/content/drive/MyDrive")` → `MyDrive/AutoNavLog/` |

### 13.2 追加要求

| ID | 内容 |
| --- | --- |
| S-1 | 書込み後に読み戻して整合性を検証し、失敗時は旧版を維持する |
| S-2 | `.bak` を1世代保持する |
| S-3 | 書込み・読込でNaN、Infinity、重複JSON keyを拒否し、同じraw bytesをJSON-modeのstrict Pydantic検証へ渡す |
| S-4 | Project一覧用 `index.json` を併置し、破損時は `projects/` の走査で再構築する |
| S-5 | revision競合時は別コピーの保存場所と対処を表示する |
| S-6 | Snapshot読込時は `SNAPSHOT_READONLY` とし、自動取得・再計算・編集・保存を行わない |
| S-7 | `PersistedUiState` v4の計算・既定値・QNH fingerprint、ArrivalPlan、参照snapshotを `Project.metadata["ui_state"]` に保存する。SEA stateは保存しない |
| S-8 | Drive未接続時は `LocalProjectRepository` で続行し、接続後に移行できる |

Snapshot用 `snapshot_effective_issues` envelopeは第6.3節のstrict検証契約に従う。通常の編集可能Projectにこの予約keyが存在する場合は `PROJECT_STATE_INVALID` へfail closeする。

### 13.3 再現の範囲

v1は**表示再現**までとする。`CalculationSnapshot` は既に `weather_requests` / `weather_results` / `forecast_metadata` / `policy_version` / `performance_table_version` / `autonavlog_version` / `msm_package_version` / `warnings` を保持しており、ネットワークなしで当時の画面と採用値を表示できる。再計算再現（当時の性能データと気象subsetを用いた再実行）は対象外とする。

Snapshotの「変更不能」は、アプリ上編集不可であることと、既存パスへの再作成を拒否することを指す。利用者のDrive上のJSONを物理的に変更不能にはできない。

---

## 14. 受け入れ基準

### 14.0 試験の階層

決定論的なUnit / Contract試験では実ネットワークを禁止し、既存の `FakeWeatherProvider` と必要なstorage fakeを用いる。DEM・陸域マスク用fakeおよびLive GSI Smokeは不要である。Colab IntegrationとUsability Acceptanceは従来どおり別層とする。

### 14.1 実装時に判定するもの

**A（導線）**

- 初期表示の必須外部入力はKML/KMZだけである
- 複数の飛行経路候補がある場合、明示選択前は計算できない
- DATE/ETD、保存済みProject、データ不足理由、次の操作が初期画面から分かる
- KML貼付Textareaは既存 `kml_text` の1つだけである
- コードセルはColab上で折りたたまれている

**B（KML・経路）**

- 隣接重複点から距離0のLegを作らず、閉路 `A→B→C→A` は4点3Legとして保持する
- 不正座標、DOCTYPE、座標上限、KMZの暗号化・path traversal・symlink・ZIP bomb・重複entryを拒否する
- 同一コンテナの近接LineStringはKML記載順・記載方向の連結候補となり、複数候補から
  名称・Leg数・全長を確認して1件を選択できる
- 接続差10 m超は警告し、`0.02 NM` 超、不正な順序、逆向きの線は自動修正せず
  個別LineString候補と警告へfallbackする
- 連結候補の接続点に一致するPointだけを変針点へ採用し、線途中のPointを自動追加しない
- Polygonは確認なしにRoute化せず、Polygonのみの場合は「外周を使う」「経路を作り直す」の2択を示す
- 接続点一致以外のKML Pointは経路点/VREP/CP/参照のみを明示選択し、CP選択でLineStringを変更しない

**E（保存・状態）**

- 未計算・未確定でも保存でき、Outcomeなしでは理由付きでSnapshotを無効にする
- revision競合、原子的保存、Snapshot読取専用、未知field・非finite値の拒否を既存契約どおり満たす
- `current_calculation_input_fingerprint` と `calculated_against_fingerprint` の陳腐化判定は再起動後も同じである
- performance bytes、選択参照行、ArrivalPlan、CP、ALT、Phase、経路、DATE/ETD/QNH等の計算依存入力変更でfingerprintが変わる
- `safe_enroute_altitude_ft_msl`、旧SEA state、pilot、ship、Project名、未選択参照行の変更ではfingerprintが変わらない
- `state_schema_version=3` またはv4からv5へ移行し、v3のSEA stateだけを捨てる
- ProjectStatusは全 `EffectiveIssue` と複合ack keyから再導出し、生のIssue codeだけを承認集合へ入れても承認にならない

**F（表示・出力）**

- 未確定値、Blocker、承認待ちWarning、無効ボタンの理由と対処を同時表示する
- `ValueState` ごとに自動・性能表・固定規則・手動・未確定を区別する
- 390px幅でKML取込から転記補助HTML出力まで操作できる
- 転記補助HTMLでHTML特殊文字がscriptとして実行されない
- ETO/ATO/ATE/TAKE OFF/LANDING欄は空欄である
- SEA列、SEA入力、SEA確認、SEA進捗、地形出典、water ratioを画面・HTML・Snapshot表示へ出さない

**N（NAV2教範照合）**

| # | 基準 |
| --- | --- |
| N-1 | ZONE ETE、CUM ETE、TTL TIME、Forecast用planned elapsedへLoss Timeを加えない |
| N-2 | ETD基準の内部時刻をETOとして表示せず、機上実績時刻欄を空欄にする |
| N-3 | 旧Projectの非0 `loss_time_seconds` が航法時間・燃料・fingerprintを変えない |
| N-4 | 採用場周高度1,000 ftの5 NMは1,500 ft、1,300 ftの5 NMは1,800 ftとなる |
| N-5 | 採用場周高度1,300 ft・8 NMは、1,300+500+3×200=2,400 ftとなり教範8-4-9(2)の例と一致する |
| N-6 | 5 NM±1 mは5 NM、5.49 NMの超過は0 NM、5.50 NMの超過は1 NMとして扱う |
| N-7 | 算出VREP高度を降下目標、降下ETE、EOC、到着区間代表高度、転記ALTへ一貫して渡す |
| N-8 | 変則Entryは100 ft単位の手動高度と理由を必須とする |
| N-9 | 経路外CPは有限Leg上のabeam stationで区間を分け、cross-track距離を加算しない |
| N-10 | NAV2対象区間で風欠損を無風補完しない。VREP→空港は常にCALMで計算し、目的地TAF風は`DESTINATION_INFO`だけへ表示する |
| N-11 | FROM/TO、空港・地点・CPをactive参照データから選択でき、選択行をProjectへsnapshotする |

**M（参照・性能データ）**

- manifest、CSV、hash、row count、strict schema、重複ID、不正座標、path traversalを検証する
- CRUD・取込・書出・active版切替・前版復帰は新しい不変revisionを作る
- 選択行snapshotはactive pack変更で自動更新されない
- 性能データは `validation_status == "VERIFIED"` かつ実CSV hash一致の場合だけ転記可能である
- 未知status、UNVERIFIED、PENDING、REJECTED、hash不一致はいずれも転記不可である

**S（SEA対象外の否定試験）**

- `autonavlog.terrain`、DEM fetch、land-mask load、DEM cache/pack publishを呼び出さない
- `SAFE_ENROUTE_ALTITUDE_REQUIRED`、`PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE`、`SEA_*` Issueを生成しない
- legacy SEA値の有無だけで計算値、status、fingerprint、転記可否が変化しない
- `numpy` / `Pillow` を本改修のruntime dependencyへ追加しない

### 14.2 リリース時に判定するもの

- 選択経路の空港・地点・CP参照データと、提供資料明示値または第6.6節の式で決定したmaster場周経路高度が検証済みである
- サンプルKMLでFROM/TO候補、気象、RCA/EOC、VREP高度、CP abeam、燃料、転記補助HTMLが一貫する
- 気象欠損を0・無風・1013.25 hPaで補わない
- 開発に関与していない利用者2名以上が手順書なしで転記補助HTML出力まで到達できる

## 15. 未決事項

本版の内容上の未決事項はない。ただし、実装handoff用artifactとしての `DESIGN.md` 自体の固定は未完了である。現在git未追跡のため、handoff前に本書をcommitするか、不変artifactとして固定提供する必要がある。

旧D-15（SEA自己診断fixture）、D-16（陸域マスク具体データ）、D-17（SEA適用Phase）、D-21の `numpy` / `Pillow`、D-22のSEA永続状態、およびDrive FUSE事前実測は、SEA・DEM機能を対象外にしたため**不採用／適用なし**とする。

以下は決定済みである。

| 項目 | 決定 |
| --- | --- |
| 成果物 | KMLからNAV LOGの地上準備を完了する転記補助HTML |
| ETO / Loss Time | 地上計画へLossを入れず、実績時刻欄は空欄 |
| VREP高度 | ArrivalPlanで確定した採用場周高度へ500 ftを加え、5 NM超過の整数NM×200 ftを加算 |
| 経路外CP | 関連Leg上のabeam点で区間分割 |
| 参照データ | manifest付き別CSV pack、CRUD・差替え・rollback、選択行snapshot |
| NAV2の風 | 欠損を無風補完しない。到着固定規則だけ例外 |
| SEA・陸域マスク | 本版では実装・表示・出力しない。将来は独立した別表として再設計 |
| 基準アーカイブ | commit `24058c1` で固定済み |
| アプリ版 | `1.1.0` |

提供資料の全件照合、式フォールバックの来歴記録、参照データpack整備はリリースゲートであり、コード実装開始を止めない。

## 付録A 外部インタフェース契約

A.1〜A.9を本版の外部インタフェース契約とする。旧A.10は履歴追跡用の欠番であり、本版のデータ契約・実装開始条件には含めない。

### A.1 既定値

| 項目 | 現行値 | 型・単位 | 所在 |
| --- | --- | --- | --- |
| ALT | 5000 | `FloatText`、ft MSL | `colab.py` `self.altitude` |
| FUEL | 81.0 | `FloatText`、gal（`total_usable_fuel_gal`、`gt=0`） | `colab.py` `self.fuel` |
| VAR | 32.0°N以上+8°、未満+7° | Leg出発緯度から自動（東偏差を正） | `nav/variation.py` |

方位計算は、プロジェクト定義として東偏差を正に保持し、`MC = TC + VAR`、
`MH = MC + WCA`を使用する。表示前に正規化し、方位は3桁で表す。
| TGL | 0 | `BoundedIntText(min=0)`、回数 | `colab.py` `self.tgl_count` |
| Phase | `CRUISE` | `Dropdown`（FlightPhase） | `colab.py` `self.phase` |
| DATE | `date.today()` | `DatePicker` | 本改修で翌日へ変更 |
| ETD | `"09:00"` | `Text`（JST） | 維持 |
| Project名 | `"NAV2"`（未入力時） | — | 本改修で自動生成へ |
| 機体Profile | `"SR22_G6"` | `aircraft_profile_id` | 固定 |

燃料計画の固定値（`FuelPlan`）: `taxi_runup_gal = 1.5` / `additional_gal = 2.8` / `reserve_gal = 12.4`。

#### ETDの解釈規則（v1.8で追加。再レビュー指摘6）

`Text` の値 `"HH:MM"` は次の**単一の規則**で一度だけaware datetimeへ変換し、`Project.planned_departure_time_jst` に格納する。

```python
planned_departure_time_jst = datetime.combine(
    flight_date, time(hour, minute), tzinfo=ZoneInfo("Asia/Tokyo")
)
```

- 既存の `Project` validatorが `astimezone(JST)` で正規化する（`project.py`）。この結果のdatetimeオブジェクトが唯一の正とする
- fingerprint（計算入力・`manual_qnh_fingerprint`）には**このオブジェクトをそのまま**渡す。事前に `.isoformat()` 等で文字列化した値を渡してはならない（第10.1a節 `normalize()` がUTC固定長文字列化する唯一の経路。`+09:00` 表記とUTC `Z` 表記の二重化を防ぐ）
- パース失敗（`"9:00"` は許容し0埋め解釈、`"25:00"` 等は拒否）は入力エラーとして提示し、Projectへ格納しない

### A.2 Loss Time（機上修正値・地上計画対象外）

Loss Timeは、飛行中に実Time Checkと実測状況を基に、事前計算した時刻へ加える修正値である。地上準備時点のZONE/CUM ETE、TTL TIME、Forecast用timeline、燃料計画へは含めず、入力UIも提供しない。機上でのETO修正方法は第6.5節に従い、転記補助HTMLのETO欄は空欄のままとする。

`NavSection.loss_time_seconds: float = Field(default=0, ge=0)` は旧Project読込互換のためだけに残すdeprecated fieldである。新規Projectは0とし、`CalculationService`、`ForecastService.build_initial_requirement()`、燃料計算、defaults確認、計算入力fingerprint、転記補助出力のすべてから除外する。旧非0値は読取専用の移行情報として保持し、明示的な消去操作以外の保存・autosaveで変更しない。固定Additional 10分・2.8 galはLossとは独立した既存規則として維持する。


### A.3 参照データパック

| 項目 | 内容 |
| --- | --- |
| 同梱既定pack | `data/reference/default/reference-manifest.json`、`airports.csv`、`points.csv`、`checkpoints.csv`。airportsは訓練利用14空港を持ち、manifestのrow count・SHA-256と一致する |
| 利用者pack | `MyDrive/AutoNavLog/reference-data/catalogs/{dataset_id}/{revision}/`。revisionは不変、`active.json` が選択版を指す |
| schema | 第6.8節の列定義。全モデルはPydantic `extra="forbid"`、strict validation、非finite拒否、緯度 ±90・経度 ±180、kind内ID一意 |
| Airport固有 | `elevation_ft_msl` とmaster `pattern_altitude_ft_msl` および固有source/revision/validation statusを持つ。目的空港は空港標高が有効で、場周高度が `VERIFIED` の場合のみ選択可。計算にはProjectで確定した採用場周高度を使う |
| 読込 | `ReferenceDataCatalogRepository.open_active()` がmanifest/hash/row countを検証し、Airport/Point/CP viewを生成する |
| 参照 | `Project.departure_airport_id` / `destination_airport_id` は選択Airport rowの `id`。完全な選択行は `ReferenceDataSnapshot` に保存する |
| 距離 | 既存 `nav/geodesy.py` のWGS84測地線。同距離の空港候補は `icao`、次に`id`昇順 |
| 欠損 | active packに無いIDを新規選択しようとした場合は該当kindのdata unavailable Issue。保存Projectはsnapshotが有効ならmaster削除後も読込可能 |
| 変更 | UIでpack取込・書出・有効化・rollback、および各kindの追加・編集・削除を行う。編集は新revisionを作り旧版を上書きしない |
| fingerprint | active pack全体ではなくProjectが選択したcanonical row snapshotだけを `selected_reference_fingerprint` に含める（第10.2節） |
| **現状**（2026-08-11） | 訓練利用14空港を同梱し、資料明示値または第6.6節の式フォールバック規則により全行 `VERIFIED` |

### A.3a performance manifest

| 項目 | 内容 |
| --- | --- |
| 所在 | `data/performance/manifest.json` |
| 型 | `PerformanceManifest`（pydantic）。`validation_status` は制約のない `str`（既定 `"UNVERIFIED"`） |
| `is_verified` | `validation_status == "VERIFIED"` の完全一致 |
| 許容集合 | `VERIFIED` / `UNVERIFIED` / `PENDING` / `REJECTED`。**それ以外はすべて未知値**として扱う（第0.1節「許容集合と印刷可否」） |
| 印刷可 | `VERIFIED` **のみ** |
| hash検証 | `tables[].sha256` と実CSVのSHA-256が一致すること。不一致は `validation_status` によらず常にBLOCKER |
| Issue | `PERFORMANCE_DATA_UNVERIFIED`（BLOCKER・**新規**。第0.4節。現行コードには存在しない）。原因は `Issue.metadata["reason"]` ∈ `{UNVERIFIED, PENDING, REJECTED, UNKNOWN_STATUS, HASH_MISMATCH}` |
| **現状**（2026-08-02 作業ツリー） | `validation_status: "VERIFIED"`、両CSVの `sha256` は実ファイルと一致、`climb_temperature_policy: "ISA_BASELINE_10_PERCENT_PER_10C_ABOVE"` |
| リリース検証 | `scripts/build_release_manifest.py` / `scripts/build_colab_preview_bundle.py` / `release_validation.py` が `"VERIFIED"` 完全一致を要求する（既存。本書の判定と整合している） |

### A.4 保存

| 項目 | 内容 |
| --- | --- |
| 保存先 | `{root}/projects/{project_id}/project.json` |
| 自動保存 | `{root}/projects/{project_id}/autosave.json` |
| Snapshot | `{root}/snapshots/{project_id}/{snapshot_id}.json` |
| Drive root | `/content/drive/MyDrive/AutoNavLog` |
| 形式 | JSON（`ensure_ascii=False`、`indent=2`、`sort_keys=True`） |
| 書込み | tempfile → `fsync` → `os.replace` |
| `schema_version` | `Project` / `CalculationSnapshot` ともに `1` |
| 競合 | `expected_revision` 不一致で `project-conflict-{UTC}.json` を作成し例外 |
| 移行規則 | 旧形式なし（`schema_version` は1のみ） |

### A.5 Forecast Run

| 項目 | 内容 |
| --- | --- |
| 要求生成 | `ForecastService.build_initial_requirement()`。推定速度100 ktの初期要求、収束後はいずれもLossを含まない `planned_elapsed_seconds` による各Sectionの中間・終了時刻を要求する。予報coverage末尾だけは飛行timelineと分離して、最終到着計画時刻へ10分＋`tgl_count`×7分を加算する |
| 最終要求 | `build_final_requirement()`（出発時刻＋代表時刻＋計算済み最終累積ETEによる到着時刻）。出発・到着地上気温も同一Forecast Runのcoverage対象とする |
| 選択 | `WeatherProvider.resolve_run(requirement)` |
| 状態 | `inspect_run_status()` → `RunSelectionStatus`（`selected_run_id` / `latest_compatible_run_id` / `selected_run_covers_requirement` / `update_available` / `warnings`） |
| 保存 | `Project.selected_forecast_run_id` |
| 反復 | 最大5回、代表時刻と計算済み到着時刻の最大差が30秒未満で収束（`CalculationPolicies`）。最終問い合わせ時刻と実到着時刻が異なる場合は、到着地上気温を実到着時刻で再取得する |
| 更新通知 | `FORECAST_UPDATE_AVAILABLE`（自動切替しない） |
| 失敗 | `FORECAST_PREPARE_FAILED` / `FORECAST_RUN_OUT_OF_COVERAGE`（BLOCKER） |

### A.6 気象・QNH

| 項目 | 内容 |
| --- | --- |
| QNH自動値 | `WeatherRequestKind.ESTIMATED_QNH`、`request_id = "project:qnh"`、出発空港座標・ETD |
| 表示名 | **`MSM推定QNH`**。公式QNHと表現しない |
| 欠損時 | `QNH_UNAVAILABLE`（BLOCKER）。手動入力必須 |
| 手動QNH | `Project.manual_qnh_hpa: float \| None`、`gt=800, lt=1100` |
| 上空風・気温 | `WeatherRequestKind.ALOFT`。欠損は `WIND_UNAVAILABLE` / `TEMPERATURE_UNAVAILABLE` |
| 出発・到着TOAT | `WeatherRequestKind.SURFACE_TEMPERATURE`。MSM `tmp_surface`を水平・時間補間し、出発はETD、到着は計算済み最終累積ETEの時刻を使う。空港標高へ気圧面気温を外挿しない |
| フォールバック | **禁止**（0・1013.25 hPa・最近傍気象） |
| 固定規則値 | 降下 500 fpm・12 GPH、到着 CAS 121 kt・原則無風・12 GPH（`ValueState.FIXED_RULE`） |
| バッチ | `query_batch(forecast_run_id, requests)`。件数不一致は `WEATHER_BATCH_MISMATCH` |

### A.7 航法計算API

| 項目 | 内容 |
| --- | --- |
| 入口 | `CalculationService.calculate(project)` |
| 入力 | `Project`（deep copyされる） |
| 出力 | `CalculationOutcome`（集計正本`sections: list[SectionResult]` / 表示専用`display_rows: list[NavLogDisplayRow]` / `derived_points` / `arrival_altitude: ArrivalAltitudeResult` / `check_point_projections: list[CheckPointProjection]` / `fuel_plan` / `issues` / `iterations` / `converged` / `status` / `policy_version` / `performance_table_version` / `qnh_hpa`） |
| 各値 | `AdoptedValue[T]`。`adopted()` で採用値、`state` で `ValueState` |
| Policy | `CalculationPolicies.version = "nav2-v6-golden-display"`。Variationは`DEPARTURE_LATITUDE_32N_V1`、VREP個別規則は`ARRIVAL_ALTITUDE_RULE_VERSION = "CAC_REV19_8_4_9_V4"`とする |
| 陳腐化判定 | 計算入力fingerprintは`calculation_policy_version`に加えて`variation_rule_version`を含み、Variation規則だけの変更でも再計算を要求する |
| エラー | 例外ではなく `Issue` として返る。`blockers` プロパティで抽出 |
| SEAの使用 | **なし（v2.6.0）**。`safe_enroute_altitude_ft_msl` は互換fieldとして残してよいが、計算・Issue・fingerprint・status・表示・出力へ使用しない |
| 丸め | 通常の中間値は丸めず、表示時にhalf-upで方位1°・距離0.5 NM・時間0.5 min・燃料0.1 gal。例外としてVREP計画高度は、ArrivalPlanで100 ft単位に確定した採用場周高度へ500 ftを加えて5 NM基準高度とし、5 NM超過距離を整数NMへhalf-upして200 ft/NMを加えた第6.6節の**採用計算値**を降下・EOC・気象へ渡す。master場周経路高度は別に参照表示する |

### A.8 SHIP / 機体Profile

`ship_identifier: str` は**帳票項目**であり計算に関与しない。機体性能は `aircraft_profile_id: str = "SR22_G6"` が担う。依存クラスは DISPLAY。

### A.9 Phase

`FlightPhase`: `CLIMB` / `CRUISE` / `DESCENT` / `VISUAL_ARRIVAL`。既定 `CRUISE`。

SEA比較は全Phaseで行わない。Phaseは性能・気象・VREP/EOC等、非SEAの航法計算にだけ使用する。

### A.10 欠番（SEA・陸域マスクをv2.6.0で対象外化）

本版では適用なし。データ源、ライセンス、生成手順、同梱形式、`land_mask_version` を確定せず、データも配布しない。将来機能化する場合は新しい設計版で確定する。

---

## 付録B fixture と実測値

SEA・DEM・陸域マスク・Drive DEM cacheに関するfixtureおよび実測値は本版では不要である。

非SEA機能のfixtureは第14章に従い、KML/KMZ、参照データpack、性能manifest、気象fake、VREP高度、CP abeam、保存・Snapshot、UI導線を対象とする。Drive Project保存は既存repository契約の試験を維持するが、DEM cache用FUSE primitive測定は行わない。
