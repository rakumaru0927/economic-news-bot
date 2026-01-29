import os
import json
import feedparser
import tweepy
from datetime import datetime, timedelta, date
import hashlib
import time
import pytz

class EconomicEmergencyBot:
    def __init__(self):
        # Twitter API設定
        self.twitter_client = tweepy.Client(
            consumer_key=os.environ.get('TWITTER_API_KEY'),
            consumer_secret=os.environ.get('TWITTER_API_SECRET'),
            access_token=os.environ.get('TWITTER_ACCESS_TOKEN'),
            access_token_secret=os.environ.get('TWITTER_ACCESS_SECRET')
        )
        
        # ファイル管理
        self.history_file = 'posted_history.json'
        self.jst = pytz.timezone('Asia/Tokyo')
        
        # 緊急アラート判定キーワード（優先度付き）
        self.critical_keywords = {
            'fomc_policy': ['FOMC', 'FRB', '政策金利決定', 'パウエル議長', '利上げ決定', '利下げ決定'],
            'boj_policy': ['日銀', '金融政策決定会合', '植田総裁', 'YCC修正', 'マイナス金利解除'],
            'major_indicators': ['雇用統計', 'CPI発表', 'GDP速報', 'PCE発表'],
            'market_shock': ['急落', '暴落', '急騰', 'サーキットブレーカー', '取引停止', '史上最高値'],
            'intervention': ['為替介入', '財務省介入', '日銀介入'],
            'emergency': ['緊急声明', '臨時会見', '緊急会合'],
            'major_moves': ['3%', '2円', '年初来高値', '年初来安値']
        }
        
        # RSS監視フィード
        self.emergency_feeds = [
            {"url": "https://news.google.com/rss/search?q=FOMC+OR+FRB+OR+パウエル議長&hl=ja&gl=JP&ceid=JP:ja", "category": "🚨FOMC"},
            {"url": "https://news.google.com/rss/search?q=日銀+金融政策+OR+植田総裁&hl=ja&gl=JP&ceid=JP:ja", "category": "🚨日銀政策"},
            {"url": "https://news.google.com/rss/search?q=雇用統計+OR+CPI+OR+GDP速報&hl=ja&gl=JP&ceid=JP:ja", "category": "📊重要指標"},
            {"url": "https://news.google.com/rss/search?q=急落+OR+暴落+OR+急騰&hl=ja&gl=JP&ceid=JP:ja", "category": "⚠️市場急変"},
            {"url": "https://news.google.com/rss/search?q=為替介入+OR+財務省&hl=ja&gl=JP&ceid=JP:ja", "category": "💴為替介入"}
        ]
        
        self.regular_feeds = [
            {"url": "https://news.google.com/rss/search?q=日経平均+株価&hl=ja&gl=JP&ceid=JP:ja", "category": "日本株"},
            {"url": "https://news.google.com/rss/search?q=為替+ドル円&hl=ja&gl=JP&ceid=JP:ja", "category": "為替"},
            {"url": "https://news.google.com/rss/search?q=米国株+NYダウ&hl=ja&gl=JP&ceid=JP:ja", "category": "米国株"}
        ]
        
        self.monthly_safe_limit = 250
        self.daily_regular_limit = 5

    def calculate_major_sq_date(self, year: int, month: int):
        if month not in [3, 6, 9, 12]:
            return None
        first_day = date(year, month, 1)
        days_until_friday = (4 - first_day.weekday()) % 7
        first_friday = first_day + timedelta(days=days_until_friday)
        second_friday = first_friday + timedelta(days=7)
        return second_friday

    def check_sq_alert(self):
        today = datetime.now(self.jst).date()
        sq_date = self.calculate_major_sq_date(today.year, today.month)
        
        if not sq_date:
            return None
        
        days_until_sq = (sq_date - today).days
        
        if days_until_sq == 0:
            return {
                'type': 'sq_today',
                'priority': 5,
                'category': '🚨SQ当日',
                'title': '本日はメジャーSQ算出日です',
                'content': f'本日{sq_date.strftime("%m月%d日")}はメジャーSQ（特別清算指数）算出日です。先物・オプション決済に伴う値動きに注意してください。',
                'url': 'https://www.jpx.co.jp/markets/derivatives/'
            }
        elif days_until_sq == 1:
            return {
                'type': 'sq_tomorrow',
                'priority': 4,
                'category': '📅SQ前日',
                'title': '明日はメジャーSQ算出日です',
                'content': f'明日{sq_date.strftime("%m月%d日")}はメジャーSQ算出日。ポジション調整や建玉整理をお忘れなく。',
                'url': 'https://www.jpx.co.jp/markets/derivatives/'
            }
        
        return None

    def get_article_hash(self, title: str, url: str) -> str:
        content = f"{title.strip()}{url.strip()}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:12]

    def detect_urgency_level(self, title: str) -> dict:
        title_lower = title.lower()
        
        for category, keywords in self.critical_keywords.items():
            for keyword in keywords:
                if keyword.lower() in title_lower:
                    if category in ['fomc_policy', 'boj_policy', 'major_indicators']:
                        priority = 5
                    elif category in ['market_shock', 'intervention', 'emergency']:
                        priority = 4
                    else:
                        priority = 3
                    
                    return {
                        'category': category,
                        'priority': priority,
                        'matched_keyword': keyword,
                        'is_urgent': priority >= 3
                    }
        
        return {'priority': 1, 'is_urgent': False}

    def fetch_emergency_articles(self) -> list:
        history = self.load_history()
        candidates = []
        
        sq_alert = self.check_sq_alert()
        if sq_alert:
            sq_hash = self.get_article_hash(sq_alert['content'], sq_alert['url'])
            if sq_hash not in history:
                candidates.append({
                    'title': sq_alert['title'],
                    'content': sq_alert['content'],
                    'link': sq_alert['url'],
                    'hash': sq_hash,
                    'priority': sq_alert['priority'],
                    'category': sq_alert['category'],
                    'is_sq_alert': True
                })

        for feed in self.emergency_feeds:
            try:
                parsed_feed = feedparser.parse(feed['url'])
                
                for entry in parsed_feed.entries[:5]:
                    article_hash = self.get_article_hash(entry.title, entry.link)
                    
                    if article_hash in history:
                        continue
                    
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pub_time = datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
                        now_utc = datetime.now(pytz.utc)
                        age_hours = (now_utc - pub_time).total_seconds() / 3600
                        if age_hours > 2:
                            continue
                    
                    urgency = self.detect_urgency_level(entry.title)
                    
                    if urgency['is_urgent']:
                        candidates.append({
                            'title': entry.title.strip(),
                            'link': entry.link.strip(),
                            'hash': article_hash,
                            'priority': urgency['priority'],
                            'category': feed['category'],
                            'urgency_info': urgency,
                            'is_sq_alert': False
                        })
                        
            except Exception as e:
                print(f"緊急RSS取得エラー ({feed['category']}): {e}")
                continue
        
        return sorted(candidates, key=lambda x: x['priority'], reverse=True)

    def fetch_regular_article(self):
        history = self.load_history()
        
        for feed in self.regular_feeds:
            try:
                parsed_feed = feedparser.parse(feed['url'])
                
                for entry in parsed_feed.entries[:8]:
                    article_hash = self.get_article_hash(entry.title, entry.link)
                    
                    if article_hash not in history:
                        return {
                            'title': entry.title.strip(),
                            'link': entry.link.strip(),
                            'hash': article_hash,
                            'category': feed['category'],
                            'is_regular': True
                        }
                        
            except Exception as e:
                print(f"通常RSS取得エラー ({feed['category']}): {e}")
                continue
        
        return None

    def generate_emergency_content(self, article: dict):
        """緊急アラート用コンテンツ生成（シンプル版）"""
        if article.get('is_sq_alert'):
            return f"{article['category']}\n{article['content']}\n\n#SQ #先物決済 #投資注意"
        
        title = article['title']
        category = article['category']
        
        content = f"{category}\n{title}\n\n#経済ニュース #速報"
        
        if len(content) > 240:
            content = content[:237] + "..."
        
        return content

    def generate_regular_content(self, article: dict):
        """通常投稿用コンテンツ生成（シンプル版）"""
        title = article['title']
        category = article['category']
        
        tweet1 = f"【{category}】\n{title}\n\n詳細はこちら👇"
        if len(tweet1) > 100:
            tweet1 = tweet1[:97] + "..."
        
        tweet2 = f"{category}の最新情報をお届けします。\n\n#経済ニュース #マーケット"
        
        return {"tweet1": tweet1, "tweet2": tweet2}

    def post_single_tweet(self, content: str, url: str):
        tweet_text = f"{content}\n\n{url}"
        
        if len(tweet_text) > 280:
            print(f"文字数超過: {len(tweet_text)}文字")
            return None
        
        try:
            response = self.twitter_client.create_tweet(text=tweet_text)
            tweet_id = response.data['id']
            print(f"✓ 投稿成功 [ID: {tweet_id}]")
            return tweet_id
        except Exception as e:
            print(f"✗ 投稿エラー: {e}")
            return None

    def post_thread(self, content: dict, article: dict):
        try:
            tweet1_text = f"{content['tweet1']}\n\n{article['link']}"
            
            if len(tweet1_text) > 280:
                print("1ツイート目が280文字を超えています")
                return None
            
            response1 = self.twitter_client.create_tweet(text=tweet1_text)
            tweet1_id = response1.data['id']
            print(f"✓ 1ツイート目投稿成功 [ID: {tweet1_id}]")
            
            time.sleep(25)
            
            response2 = self.twitter_client.create_tweet(
                text=content['tweet2'],
                in_reply_to_tweet_id=tweet1_id
            )
            tweet2_id = response2.data['id']
            print(f"✓ 2ツイート目投稿成功 [ID: {tweet2_id}]")
            
            return {'tweet1_id': tweet1_id, 'tweet2_id': tweet2_id, 'success': True}
            
        except Exception as e:
            print(f"✗ スレッド投稿エラー: {e}")
            return None

    def load_history(self) -> dict:
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cutoff = (datetime.now() - timedelta(days=14)).isoformat()
                return {
                    k: v for k, v in data.items()
                    if v.get('posted_at', '') > cutoff
                }
            return {}
        except Exception as e:
            print(f"履歴読み込みエラー: {e}")
            return {}

    def save_history(self, article_hash: str, data: dict):
        history = self.load_history()
        history[article_hash] = {**data, 'posted_at': datetime.now().isoformat()}
        
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"履歴保存エラー: {e}")

    def get_monthly_post_count(self) -> int:
        history = self.load_history()
        current_month = datetime.now().strftime('%Y-%m')
        return sum(
            1 for v in history.values()
            if v.get('posted_at', '')[:7] == current_month
        )

    def get_today_regular_count(self) -> int:
        history = self.load_history()
        today = datetime.now(self.jst).date().isoformat()
        return sum(
            1 for v in history.values()
            if v.get('posted_at', '')[:10] == today and not v.get('is_emergency', False)
        )

    def run_emergency_check(self):
        print("\n" + "=" * 60)
        print(f"🚨 緊急アラートチェック: {datetime.now(self.jst).strftime('%H:%M:%S')}")
        print("=" * 60)
        
        monthly_count = self.get_monthly_post_count()
        if monthly_count >= self.monthly_safe_limit:
            print(f"⚠️ 月間投稿制限: {monthly_count}/{self.monthly_safe_limit}")
            return {'status': 'monthly_limit', 'posted': False}
        
        emergency_articles = self.fetch_emergency_articles()
        
        if not emergency_articles:
            print("緊急アラート対象なし")
            return {'status': 'no_emergency', 'posted': False}
        
        article = emergency_articles[0]
        print("\n🚨 緊急アラート発動!")
        print(f"   優先度: {article['priority']}/5")
        print(f"   記事: {article['title']}...")
        
        content = self.generate_emergency_content(article)
        if not content:
            return {'status': 'content_failed', 'posted': False}
        
        tweet_id = self.post_single_tweet(content, article['link'])
        
        if tweet_id:
            self.save_history(article['hash'], {
                'title': article['title'],
                'url': article['link'],
                'category': article['category'],
                'tweet_id': tweet_id,
                'is_emergency': True,
                'priority': article['priority']
            })
            
            print("✅ 緊急アラート投稿完了!")
            return {'status': 'emergency_posted', 'posted': True}
        else:
            return {'status': 'post_failed', 'posted': False}

    def run_regular_post(self):
        print("\n" + "=" * 60)
        print(f"📊 通常投稿チェック: {datetime.now(self.jst).strftime('%H:%M:%S')}")
        print("=" * 60)
        
        today_regular_count = self.get_today_regular_count()
        print(f"本日の通常投稿: {today_regular_count}/5")
        
        if today_regular_count >= self.daily_regular_limit:
            print("本日の通常投稿上限に達しました")
            return {'status': 'daily_limit', 'posted': False}
        
        current_hour = datetime.now(self.jst).hour
        target_hours = [7, 12, 15, 18, 21]
        
        if current_hour not in target_hours:
            print(f"通常投稿時間外: JST {current_hour}時")
            return {'status': 'not_scheduled_time', 'posted': False}
        
        article = self.fetch_regular_article()
        if not article:
            print("新規記事なし")
            return {'status': 'no_articles', 'posted': False}
        
        content = self.generate_regular_content(article)
        if not content:
            return {'status': 'content_failed', 'posted': False}
        
        result = self.post_thread(content, article)
        
        if result and result.get('success'):
            self.save_history(article['hash'], {
                'title': article['title'],
                'url': article['link'],
                'category': article['category'],
                'tweet1_id': result['tweet1_id'],
                'tweet2_id': result['tweet2_id'],
                'is_emergency': False
            })
            
            print("✅ 通常投稿完了!")
            return {'status': 'regular_posted', 'posted': True}
        else:
            return {'status': 'post_failed', 'posted': False}

    def run_main_cycle(self):
        emergency_result = self.run_emergency_check()
        
        if emergency_result.get('posted'):
            return emergency_result
        
        regular_result = self.run_regular_post()
        
        return regular_result

if __name__ == "__main__":
    bot = EconomicEmergencyBot()
    result = bot.run_main_cycle()
    print(f"\n最終結果: {result}")
