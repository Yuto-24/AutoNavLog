# iPad Safari real-MSM acceptance

実施日: 2026-09-16

物理iPadのSafariで、`docs/local_weather.md` の実機merge gateを確認した。

- fixtureなしのfresh実MSM feedを使用
- HTTPS配信したStatic Local buildを使用
- RJFM → 米ノ津 → 玉名 → RJFT のacceptance caseを使用
- ETD 12:00 JST
- 計画高度 6,500 / 7,500 / 6,500 / 2,500 ft
- cold状態から実MSM FORECASTでNAV LOGまで完走
- warm再計算でも完走
- Weather取得失敗時もProject / Last Calculationを保持
- OOM、予期しないreload、page crashなし

端末は物理iPad、browserはSafari。iPadOS / Safariの詳細versionとMSM Run IDは、この確認記録では取得していない。
