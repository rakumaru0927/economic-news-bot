import os
import json
import feedparser
import tweepy
from datetime import datetime, timedelta, date
import hashlib
import time
import pytz
import requests
from bs4 import BeautifulSoup

class EconomicEmergencyBot:
    def __init__(self):
        # Twitter API設定
        self.twitter_client = tweepy.Client(
            consumer_key=os.environ.get('TWITTER_API_KEY'),
            consumer_secret=os.environ.get('TWITTER_API_SECRET'),
            access_token=os.environ.get('TWITTER_ACCESS_TOKEN'),
            access_token_secret=os.environ.get('TWITTER_ACCESS_SECRET')
        )
        
        self.history_file = 'posted_history.json'
        self.jst = pytz.timezone('Asia/Tokyo')
        
        # 緊急アラート判定キーワード
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

    # ========== 記事本文取得機能 ==========
    
    def fetch_article_content(self, url: str) -> str:
        """記事URLから本文を取得して要約用テキストを生成"""
        try:
            # newspaper3kで記事取得を試行
            try:
                from newspaper import Article
                article = Article(url, language='ja')
                article.download()
                article.parse()
                
                if article.text and len(article.text) > 100:
                    return article.text[:1500]  # 最初の1500文字
            except:
                pass
            
            # フォールバック：requests + BeautifulSoupで取得
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=8)
            response.encoding = response.apparent_encoding
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 記事本文を探す（一般的なタグを順次試行）
            content_selectors = [
                'article', '.article-body', '.entry-content', 
                '.post-content', '.news-content', '.story-body',
                'main', '.main-content'
            ]
            
            for selector in content_selectors:
                element = soup.select_one(selector)
                if element:
                    paragraphs = element.find_all('p')
                    if paragraphs:
                        text = ' '.join([p.get_text().strip() for p in paragraphs[:5]])
                        if len(text) > 100:
                            return text[:1500]
            
            return None
            
        except Exception as e:
            print(f"記事本文取得エラー: {e}")
            return None

    def create_smart_summary(self, title: str, content: str, category: str) -> str:
        """記事タイトルと本文から実用的な要約を生成（AI不使用）"""
        
        # 重要キーワードを抽出
        important_keywords = []
        
        # 数値情報を抽出（金利、株価、為替レートなど）
        import re
        numbers = re.findall(r'[\d,]+\.?\d*[%円ドル万億兆ポイント]', content)
        if numbers:
            important_keywords.extend(numbers[:2])  # 最初の2つの数値
        
        # 重要な固有名詞を抽出
        key_entities = ['FRB', '日銀', 'パウエル', '植田', 'FOMC', 'GDP', 'CPI', '雇用統計']
        for entity in key_entities:
            if entity in content and entity not in title:
                important_keywords.append(entity)
        
        # 重要な動詞・形容詞を抽出
        key_actions = ['決定', '発表', '上昇', '下落', '引き上げ', '引き下げ', '維持', '変更']
        for action in key_actions:
            if action in content and action not in title:
                important_keywords.append(action)
                break  # 1つだけ追加
        
        # 本文から重要そうな1-2文を抽出
        sentences = content.replace('。', '。\n').split('\n')
        important_sentences = []
        
        for sentence in sentences[:10]:  # 最初の10文から選択
            sentence = sentence.strip()
            if len(sentence) > 20 and len(sentence) < 100:
                # 数値や重要キーワードを含む文を優先
                if any(keyword in sentence for keyword in important_keywords[:3]):
                    important_sentences.append(sentence)
                    if len(important_sentences) >= 2:
                        break
        
        # 要約文を構築
        base_text = f"{category}\n"
        
        # タイトルを短縮（必要に応じて）
        clean_title = title.replace(" - Yahoo!ニュース", "").replace(" - ブルームバーグ", "")
        if len(clean_title) > 60:
            clean_title = clean_title[:57] + "..."
        
        base_text += clean_title
        
        # 重要情報を追加
        if important_keywords:
            key_info = "、".join(important_keywords[:3])
            if len(base_text + f"\n\n{key_info}") < 200:
                base_text += f"\n\n{key_info}"
        
        # 重要文を追加（文字数に余裕があれば）
        if important_sentences and len(base_text) < 150:
            sentence = important_sentences[0]
            if len(base_text + f"\n{sentence}") < 220:
                base_text += f"\n{sentence}"
        
        # ハッシュタグを追加
        hashtags = "\n\n#経済ニュース #速報"
        
        # 最終的な文字数調整（URLを含めて280文字以内）
        max_content_length = 240  # URL(23文字) + 余裕(17文字)を考慮
        
        full_text = base_text + hashtags
        if len(full_text) > max_content_length:
            # ハッシュタグを優先して、本文をカット
            available_length = max_content_length - len(hashtags)
            base_text = base_text[:available_length-1] + "…"
            full_text = base_text + hashtags
        
        return full_text

    # ========== SQ日計算 ==========
    
    def calculate_major_sq_date(self, year: int, month: int):
        if month not in [3, 6, 9, 12]:
            return None
        first_day = date(year, month, 1)
        days_until_friday = (4 - first_day.weekday()) % 7
        first_friday = first_day + timedelta(days=days_until_friday)
        return first_friday + timedelta(days=7)

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

    # ========== 記事取得・判定 ==========
    
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

    # ========== コンテンツ生成 ==========
    
    def generate_emergency_content(self, article: dict):
        """緊急アラート用コンテンツ生成（記事本文要約版）"""
        if article.get('is_sq_alert'):
            return f"{article['category']}\n{article['content']}\n\n#SQ #先物決済 #投資注意"
        
        # 記事本文を取得
        article_content = self.fetch_article_content(article['link'])
        
        if article_content:
            # 本文がある場合は高品質な要約を生成
            return self.create_smart_summary(article['title'], article_content, article['category'])
        else:
            # 本文取得失敗時はタイトルベースで生成
            return f"{article['category']}\n{article['title'][:100]}...\n\n#経済ニュース #速報"

    def generate_regular_content(self, article: dict):
        """通常投稿用コンテンツ生成（スレッド形式）"""
        article_content = self.fetch_article_content(article['link'])
        
        if article_content:
            # 本文がある場合はスレッド用に分割
            summary = self.create_smart_summary(article['title'], article_content, article['category'])
            
            # 1ツイート目（概要）
            tweet1_lines = summary.split('\n')
            tweet1 = '\n'.join(tweet1_lines[:3])  # 最初の3行
            if len(tweet1) > 100:
                tweet1 = tweet1[:97] + "..."
            
            # 2ツイート目（詳細）
            tweet2 = f"詳細情報やその他の{article['category']}ニュースも随時お届けします。\n\n#経済ニュース #マーケット"
            
            return {"tweet1": tweet1, "tweet2": tweet2}
        else:
            # フォールバック
            title = article['title'][:80] + ("..." if len(article['title']) > 80 else "")
            return {
                "tweet1": f"【{article['category']}】\n{title}\n\n詳細はこちら👇",
                "tweet2": f"{article['category']}の最新情報をお届けします。\n\n#経済ニュース #マーケット"
            }

    # ========== 投稿実行 ==========
    
    def post_single_tweet(self, content: str, url: str):
        tweet_text = f"{content}\n\n{url}"
        
        if len(tweet_text) > 280:
            print(f"文字数超過: {len(tweet_text)}文字 - 自動調整中...")
            # 緊急時の文字数調整
            available_length = 280 - len(url) - 3  # URL + 改行2つ + 余裕1文字
            content = content[:available_length-1] + "…"
            tweet_text = f"{content}\n\n{url}"
        
        try:
            response = self.twitter_client.create_tweet(text=tweet_text)
            tweet_id = response.data['id']
            print(f"✓ 投稿成功 [ID: {tweet_id}] 文字数: {len(tweet_text)}")
            return tweet_id
        except Exception as e:
            print(f"✗ 投稿エラー: {e}")
            return None

    def post_thread(self, content: dict, article: dict):
        try:
            tweet1_text = f"{content['tweet1']}\n\n{article['link']}"
            
            if len(tweet1_text) > 280:
                # 1ツイート目の文字数調整
                available_length = 280 - len(article['link']) - 3
                content['tweet1'] = content['tweet1'][:available_length-1] + "…"
                tweet1_text = f"{content['tweet1']}\n\n{article['link']}"
            
            response1 = self.twitter_client.create_tweet(text=tweet1_text)
            tweet1_id = response1.data['id']
            print(f"✓ 1ツイート目投稿成功 [ID: {tweet1_id}] 文字数: {len(tweet1_text)}")
            
            time.sleep(25)
            
            response2 = self.twitter_client.create_tweet(
                text=content['tweet2'],
                in_reply_to_tweet_id=tweet1_id
            )
            tweet2_id = response2.data['id']
            print(f"✓ 2ツイート目投稿成功 [ID: {tweet2_id}] 文字数: {len(content['tweet2'])}")
            
            return {'tweet1_id': tweet1_id, 'tweet2_id': tweet2_id, 'success': True}
            
        except Exception as e:
            print(f"✗ スレッド投稿エラー: {e}")
            return None

    # ========== 履歴管理 ==========
    
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

    # ========== メイン実行ロジック ==========
    
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
        print(f"   記事: {article['title'][:50]}...")
        
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
