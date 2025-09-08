# CEX-CEX 乖離通知 + ETH BB/RSI 通知 Bot (Render 常駐)

## 1. Discord の準備（Webhook 2本）
1) Discordサーバで通知したい**テキストチャンネル**を作る。  
2) チャンネル名の右「⚙️ 設定」→ **Integrations** → **Webhooks** → **New Webhook**  
   - 1本目: スプレッド通知用（URLをメモ）  
   - 2本目: テクニカル通知用（URLをメモ）  
3) これらのURLは **Gitに絶対コミットしない**。Renderの環境変数に入れる。

## 2. リポジトリを用意
```
cex-spread-discord-bot/
├─ cex_spread_discord_bot.py
├─ config.json             # 設定（あとで中身を自由に編集）
├─ requirements.txt
└─ render.yaml            # Render IaC: Worker + Persistent Disk
```
`config.json` は初回デプロイ時に `/data/config.json` にコピーされ、以後は**Persistent Disk**上のファイルを自動リロードする。

## 3. Render にデプロイ（Background Worker）
1) このリポジトリを GitHub にプッシュ  
2) https://render.com → New + → **Blueprint** を選択 → GitHubのこのリポジトリを選ぶ  
3) `render.yaml` が読み取られ、**Worker** サービスが作成される  
4) **Environment Variables** に以下を追加  
   - `DISCORD_WEBHOOK_URL`: スプレッド通知用Webhook  
   - `INDICATOR_WEBHOOK_URL`: テクニカル通知用Webhook  
5) デプロイが終わると常駐開始

## 4. 設定の編集（ブラックリスト＆閾値変更）
- Renderダッシュボード → 該当Worker → **Shell** を開いて `/data/config.json` を編集:
```bash
nano /data/config.json
# 例: 銘柄をブラックリストへ
# "blacklist_pairs": ["DOGE-USDT", "PEPE-USDT"]
# 例: 閾値を変更（bps）
# "threshold_bps": 40.0
# 例: ペア別閾値
# "pair_threshold_bps": {"BTC-USDT": 30.0, "ETH-USDT": 60.0}
```
- ファイルを保存すると**数秒で自動リロード**される（プロセス再起動不要）。

## 5. ローカル動作（任意）
```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/xxx/yyy'
export INDICATOR_WEBHOOK_URL='https://discord.com/api/webhooks/aaa/bbb'
python cex_spread_discord_bot.py
```
- ローカルで `CONFIG_PATH` を指定したい場合:
```bash
CONFIG_PATH=./config.json python cex_spread_discord_bot.py
```

## 6. よくある質問
- **Webhook と Bot の違い？**  
  Webhookは**送信専用**で簡単・安全（権限不要）。本リポジトリは**Webhook前提**。双方向やSlashコマンドが欲しいなら、Discordアプリ＋Bot Token＋Gateway実装に切り替える。  
- **無料プランで止まらない？**  
  Render Freeは定期的に休眠する場合あり。安定稼働は有料プラン推奨。
- **API制限/エラーが出る**  
  頻度(`interval_sec`)を上げすぎると429を踏む。まずは3〜5秒程度で運用。

---

### 設定フィールド（`config.json`）
- `pairs`: 監視銘柄配列（"BTC-USDT" 形式）
- `exchanges`: 参照CEX（binance/okx/bybit/kucoin/gate/mexc/htx/coinbase）
- `threshold_bps`: 全体デフォ閾値（100 bps = 1%）
- `pair_threshold_bps`: ペア別閾値上書き
- `blacklist_pairs`: 監視から外す銘柄
- `cooldown_sec`: 同一組合せの再通知クールダウン
- `renotify_delta_bps`: 前回通知よりこのbps改善しないと再通知しない
- `interval_sec`: 監視ポーリング間隔（秒）
- `indicator`: ETHのBB/RSI通知設定（`symbol`や`timeframe`など変更可）
