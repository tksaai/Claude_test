# Twitch 配信タイムライン & 切り抜き候補モニター

Twitchの配信をリアルタイムで検知し、以下を自動生成するツールです。

- **章立てタイムライン**: 配信タイトル/ゲームカテゴリの変化から章を自動生成
- **切り抜き候補ランキング**: チャットの盛り上がりを解析し、切り抜きに適した瞬間をスコア順にリスト化

## 従量課金ゼロの設計

| 機能 | 実装方法 | コスト |
|------|---------|--------|
| 配信検知 | Twitch Helix API のポーリング(Client Credentials) | 無料(公式API、レート制限内) |
| チャット取得 | Twitch IRC への**匿名接続**(justinfan) | 無料(トークン不要) |
| 章立て | タイトル/カテゴリ変更のローカル検知 | 無料 |
| 切り抜きスコアリング | ローカルのヒューリスティック解析(正規表現+統計) | 無料(LLM等の外部API不使用) |

自宅PCや無料枠のVM(Oracle Cloud Always Free等)で常駐させれば、ランニングコストは一切かかりません。

## セットアップ

1. [Twitch開発者コンソール](https://dev.twitch.tv/console/apps)でアプリを登録(無料)し、Client ID / Client Secret を取得
2. 依存パッケージをインストール:

   ```bash
   pip install -r requirements.txt
   ```

3. 認証情報を環境変数に設定:

   ```bash
   export TWITCH_CLIENT_ID=xxxx
   export TWITCH_CLIENT_SECRET=xxxx
   ```

4. 設定ファイルを作成:

   ```bash
   cp config.example.yaml config.yaml
   # channels に監視したいチャンネル名(twitch.tv/xxx の xxx)を記入
   ```

## 使い方

```bash
# 設定ファイルで起動
python -m twitch_monitor --config config.yaml

# チャンネルを直接指定して起動
python -m twitch_monitor --channel some_streamer --channel another_one
```

監視対象が配信を開始すると自動で追跡が始まり、`out/<チャンネル名>/<日付>_<配信ID>/` に以下がリアルタイム更新されます(デフォルト60秒間隔)。

- `timeline.md` — 章立てタイムラインと切り抜き候補ランキング(人間が読む用)
- `highlights.json` — 同内容の構造化データ(他ツール連携用)

配信終了時には最終版が書き出されます。

## 切り抜き候補のスコアリング

チャットを10秒単位で集計し、平常時のコメント速度(指数移動平均)に対するスパイクを検知します。スパイク区間は以下の観点で重み付けされます。

- **コメント速度のピーク倍率**(平常時の何倍か)
- **笑いの量**: 草 / wwww / 笑 / LUL / KEKW など(日英対応)
- **盛り上がりワード**: やば / すごい / 神 / ナイス / Pog / clutch など
- **クリップ要望**: 「クリップ」「切り抜き」「clip」(最も高い重み)
- **エモート密度・ユニークユーザー数**

各候補には「笑い」「盛り上がり」「クリップ希望」「チャット急増」のタイプが付き、代表コメントも記録されるので、切り抜き対象の当たりを付けやすくなっています。

## テスト

```bash
python -m pytest tests/ -q
```
