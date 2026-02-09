import streamlit as st
import os
import io
import tempfile
import datetime
import base64
import re
import json
import gspread
import google.generativeai as genai
from google.cloud import speech
from google.oauth2 import service_account
import streamlit.components.v1 as components

# --- 設定 ---
st.set_page_config(
    page_title="日本語音声 指導補助ツール v6.6", 
    page_icon="👨‍🏫", 
    layout="centered"
)

st.title("👨‍🏫 日本語音声 指導補助ツール")
st.markdown("教師向け：対照言語学に基づく音声評価・誤用分析＋学習ログ保存")

# --- 認証情報の読み込み ---
def load_credentials():
    """認証情報を安全に読み込む"""
    try:
        # Gemini API Key
        gemini_api_key = st.secrets.get("GEMINI_API_KEY")
        if not gemini_api_key:
            st.error("⚠️ Secretsに GEMINI_API_KEY が設定されていません。")
            st.stop()
        
        # Google Cloud認証情報
        if "GOOGLE_JSON" not in st.secrets:
            st.error("⚠️ Secretsに GOOGLE_JSON が設定されていません。")
            st.stop()
        
        google_json_data = st.secrets["GOOGLE_JSON"]
        
        # JSON文字列の場合はパース
        if isinstance(google_json_data, str):
            try:
                google_creds_dict = json.loads(google_json_data)
            except json.JSONDecodeError as e:
                st.error(f"⚠️ GOOGLE_JSONのJSON形式が不正です: {e}")
                st.stop()
        else:
            google_creds_dict = dict(google_json_data)
        
        # Gemini設定
        genai.configure(api_key=gemini_api_key)
        
        return gemini_api_key, google_creds_dict
    
    except Exception as e:
        st.error(f"⚠️ 認証情報の読み込みエラー: {e}")
        st.stop()

# 認証情報をロード
gemini_api_key, google_creds_dict = load_credentials()

# --- サイドバー：システム診断ツール ---
with st.sidebar:
    st.header("🔧 システム状態チェック")
    
    if st.button("API接続テスト & モデル一覧取得"):
        with st.spinner("問い合わせ中..."):
            try:
                available_models = []
                for m in genai.list_models():
                    if 'generateContent' in m.supported_generation_methods:
                        available_models.append(m.name)
                
                if available_models:
                    st.success(f"✅ API接続成功！ ({len(available_models)}個のモデルを検出)")
                    with st.expander("利用可能なモデル一覧"):
                        for model in available_models:
                            st.code(model)
                    st.info("※ 上記リストにあるモデル名が分析に使用されます。")
                else:
                    st.warning("⚠️ 接続はできましたが、利用可能なモデルが見つかりませんでした。")
            except Exception as e:
                st.error(f"❌ API接続エラー: {e}")

# --- 関数群 ---

def get_jst_now():
    """現在時刻を日本時間(JST)で取得する"""
    t_delta = datetime.timedelta(hours=9)
    JST = datetime.timezone(t_delta, 'JST')
    return datetime.datetime.now(JST)

def analyze_audio(source_path):
    """音声認識を実行"""
    try:
        credentials = service_account.Credentials.from_service_account_info(
            google_creds_dict
        )
        client = speech.SpeechClient(credentials=credentials)
    except Exception as e:
        return {"error": f"認証エラー: {e}"}

    # 一時ファイルで変換
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_converted:
        converted_path = tmp_converted.name
    
    # ffmpegで変換
    cmd = f'ffmpeg -y -i "{source_path}" -vn -ac 1 -ar 16000 -ab 32k "{converted_path}" -loglevel panic'
    exit_code = os.system(cmd)
    
    if exit_code != 0:
        return {"error": "ファイル変換エラー (FFmpeg未インストールの可能性)"}

    # 音声認識
    try:
        with io.open(converted_path, "rb") as f:
            content = f.read()
        
        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED,
            sample_rate_hertz=16000,
            language_code="ja-JP",
            enable_automatic_punctuation=False,
            max_alternatives=1, 
            enable_word_confidence=True,
            enable_word_time_offsets=True
        )
        
        operation = client.long_running_recognize(config=config, audio=audio)
        response = operation.result(timeout=600)
        
    except Exception as e:
        return {"error": f"認識エラー: {e}"}
    finally:
        if os.path.exists(converted_path):
            os.remove(converted_path)

    if not response.results:
        return {"error": "音声認識不可(無音/ノイズ)"}

    # 結果の整形
    full_transcript = ""
    full_details = []
    word_data_list = []
    
    for result in response.results:
        alt = result.alternatives[0]
        full_transcript += alt.transcript
        
        for w in alt.words:
            score = int(w.confidence * 100)
            start_seconds = w.start_time.total_seconds()
            time_str = f"[{start_seconds:.1f}s]"
            marker = " ⚠️" if w.confidence < 0.8 else ""
            
            full_details.append(f"{w.word}({score}){time_str}{marker}")
            word_data_list.append({
                "word": w.word, 
                "conf": w.confidence, 
                "start": start_seconds
            })
            
    return {
        "main_text": full_transcript,
        "details": ", ".join(full_details),
        "audio_content": content,
        "word_data": word_data_list,
        "alts": ""
    }


def ask_gemini(student_name, nationality, text, alts, details):
    """Gemini APIで音声評価レポートを生成"""
    
    # 診断結果に基づいた、確実に動くモデルリスト
    target_models = [
        "gemini-2.0-flash",       # 最新・高速・高性能
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
        "gemini-pro"
    ]
    
    last_error = None
    
    name_part = f"学習者名は「{student_name}」です。" if student_name else "学習者名は不明です。"
    nat_instruction = f"学習者の母語・国籍は「{nationality}」です。" if nationality else "母語情報は不明です。"

    prompt = f"""
あなたは日本語音声学・対照言語学・日本語教育の専門家です。
以下のデータに基づき、音声評価レポートを作成してください。

【基本情報】
{name_part}
{nat_instruction}

【分析データ】
認識結果: {text}
詳細スコア: {details}

【重要指示】
- 信頼度が低い箇所（⚠️マーク）を発音ミスとして分析してください。
- 母語の音韻体系との対照分析を実施してください。
- **「要重点指導音」には、音声記号（IPA）と、それに対応する日本語（ひらがな・カタカナ・漢字など）を必ず併記してください。**
  - 良い例: /tsɯ/ (つ), /ɕ/ (し), /ɾ/ (ら行), 長音 (ー)
  - 悪い例: /tsɯ/, /ɕ/ (記号のみはNG)

【出力形式（厳守）】
レポートの冒頭に以下のサマリーを必ず含めてください。
**注意：自動抽出のため、項目の形式を変えないでください。**

### 【総合評価サマリー】
* **総合音声スコア**： [0~100の数値]
* **明瞭度**： [S/A/B/C]
* **日本語らしさ**： [S/A/B/C]
* **要重点指導音**： [音声記号とひらがなを併記]

---

その後、詳細分析を記述してください。
"""
    
    for model_name in target_models:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            return response.text
            
        except Exception as e:
            last_error = e
            continue
    
    return f"❌ Gemini生成エラー（全モデルで失敗）: {last_error}"


def parse_summary(report_text):
    """
    レポートからサマリー情報を抽出する（強化版：表記ゆれ対応）
    """
    # 抽出を容易にするため、記号を統一
    clean_text = report_text.replace("**", "")  # 太字記号削除
    clean_text = clean_text.replace("：", ":")  # コロン統一
    clean_text = clean_text.replace(" ", "")    # スペース削除
    
    # 正規表現で抽出（より柔軟に）
    # "スコア"の後ろにある数字 (0-100) を探す
    score_match = re.search(r'スコア.*?:.*?(\d{1,3})', clean_text)
    
    # "明瞭度"の後ろにある S,A,B,C を探す
    clarity_match = re.search(r'明瞭度.*?:.*?([SABC])', clean_text, re.IGNORECASE)
    
    # "日本語らしさ"の後ろにある S,A,B,C を探す
    natural_match = re.search(r'日本語らしさ.*?:.*?([SABC])', clean_text, re.IGNORECASE)
    
    # サマリー本文の抽出
    summary_block = "サマリー抽出失敗"
    try:
        start = report_text.find("### 【総合評価サマリー】")
        if start == -1: start = report_text.find("【総合評価サマリー】")
        
        end = report_text.find("---", start)
        if start != -1 and end != -1:
            summary_block = report_text[start:end].strip()
    except:
        pass
    
    return {
        "score": score_match.group(1) if score_match else "0",
        "clarity": clarity_match.group(1).upper() if clarity_match else "-",
        "naturalness": natural_match.group(1).upper() if natural_match else "-",
        "summary_text": summary_block
    }


def save_to_sheet(data_dict):
    """Google スプレッドシートにデータを保存"""
    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = service_account.Credentials.from_service_account_info(
            google_creds_dict, 
            scopes=scopes
        )
        client = gspread.authorize(creds)
        
        sheet_url = st.secrets.get("SHEET_URL")
        if not sheet_url:
            return False, "SHEET_URL未設定"
        
        sheet = client.open_by_url(sheet_url).sheet1
        
        row = [
            data_dict["date"],
            data_dict["name"],
            data_dict["nationality"],
            data_dict["score"],
            data_dict["clarity"],
            data_dict["naturalness"],
            data_dict["summary_text"]
        ]
        
        sheet.append_row(row)
        return True, "成功"
        
    except Exception as e:
        return False, str(e)


def render_sticky_player_and_buttons(audio_content, word_data):
    """固定プレーヤーと低信頼度箇所へのジャンプボタンを表示（HTMLバグ修正済）"""
    b64_audio = base64.b64encode(audio_content).decode()
    buttons_html = ""
    unique_id = int(datetime.datetime.now().timestamp() * 1000)
    
    low_conf_count = 0
    for item in word_data:
        if item['conf'] < 0.8:
            start = item['start']
            word = item['word']
            conf = int(item['conf'] * 100)
            
            buttons_html += f"""
            <button class="seek-btn-{unique_id}" data-seek="{start}" 
                    style="background-color: #ffffff; 
                           border: 1px solid #d3d3d3; 
                           border-radius: 5px; 
                           padding: 6px 12px; 
                           cursor: pointer; 
                           color: #d9534f; 
                           font-weight: bold; 
                           font-size: 14px; 
                           display: inline-flex; 
                           align-items: center; 
                           gap: 5px; 
                           margin: 4px;">
                ▶ {word} <span style="font-size:12px; color:#666; font-weight:normal;">({conf}%)</span>
            </button>
            """
            low_conf_count += 1
    
    if low_conf_count == 0:
        buttons_html = "<div style='color:#666; padding:10px;'>✅ 低信頼度の箇所なし（明瞭な発音）</div>"

    # ボタンエリアの表示（HTMLとしてレンダリング）
    st.markdown(
        f"""
        <div style="background-color: #fff3cd; 
                    border: 1px solid #ffeeba; 
                    border-radius: 8px; 
                    padding: 15px; 
                    margin-bottom: 20px;">
            <div style="color: #856404; font-weight: bold; margin-bottom: 10px;">
                ⚠️ 低信頼度箇所（クリックで再生）
            </div>
            <div>{buttons_html}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # 固定プレーヤー (JavaScriptで親フレームのスタイルを書き換えて固定)
    html_code = f"""
    <div id="sticky-audio-container-{unique_id}" style="position: fixed; bottom: 0; left: 0; width: 100%; background-color: #f1f3f5; border-top: 1px solid #dee2e6; padding: 10px 0; text-align: center; box-shadow: 0 -2px 10px rgba(0,0,0,0.1); z-index: 999999;">
        <div style="font-size:12px; color:#666; margin-bottom:4px; font-weight:bold;">
           🔊 音声プレーヤー (レポート閲覧中もここに固定されます)
        </div>
        <audio id="audio-player-{unique_id}" controls style="width: 90%; max-width: 600px;">
            <source src="data:audio/mp3;base64,{b64_audio}" type="audio/mp3">
        </audio>
    </div>
    
    <script>
        (function() {{
            var frame = window.frameElement;
            if (frame) {{
                frame.style.position = "fixed";
                frame.style.bottom = "0";
                frame.style.left = "0";
                frame.style.width = "100%";
                frame.style.height = "100px";
                frame.style.zIndex = "999999";
                frame.style.border = "none";
            }}

            function setupInteraction() {{
                var player = document.getElementById("audio-player-{unique_id}");
                if (!player) return;
                
                var parentDoc = window.parent.document;
                var buttons = parentDoc.getElementsByClassName("seek-btn-{unique_id}");
                
                for (var i = 0; i < buttons.length; i++) {{
                    buttons[i].onclick = function() {{
                        var seekTime = parseFloat(this.getAttribute("data-seek"));
                        player.currentTime = seekTime;
                        player.play();
                    }};
                }}
            }}
            
            setTimeout(setupInteraction, 1000);
            setInterval(setupInteraction, 2000);
        }})();
    </script>
    """
    
    components.html(html_code, height=0)


# --- メイン処理 ---
st.info("👇 学習者の情報を入力してください（任意）")

col1, col2 = st.columns(2)
with col1:
    student_name = st.text_input("学習者氏名", placeholder="例: 田中太郎")
with col2:
    nationality = st.text_input("母語・国籍", placeholder="例: 中国語/英語")

st.divider()

# タブで音声入力方法を選択
tab1, tab2 = st.tabs(["📁 ファイルアップロード", "🎙️ 録音"])

target_file = None

with tab1:
    uploaded_file = st.file_uploader(
        "音声/動画ファイルを選択", 
        type=["mp3", "wav", "m4a", "mp4", "mov"]
    )
    
    if uploaded_file:
        file_ext = uploaded_file.name.split('.')[-1].lower()
        
        if file_ext in ['mp4', 'mov']:
            st.video(uploaded_file)
        else:
            st.audio(uploaded_file)
        
        target_file = uploaded_file

with tab2:
    recorded_audio = st.audio_input("🎤 録音開始（クリックして話してください）")
    
    if recorded_audio:
        st.audio(recorded_audio)
        target_file = recorded_audio

st.divider()

# 分析実行ボタン
if st.button("🚀 音声評価を開始する", type="primary", use_container_width=True):
    if not target_file:
        st.warning("⚠️ 音声ファイルを選択するか、録音してください。")
    else:
        with st.spinner('🎧 音声を分析中...（最大10分程度かかる場合があります）'):
            # 一時ファイルに保存
            file_bytes = target_file.getvalue()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            
            # 音声認識実行
            res = analyze_audio(tmp_path)
            
            # 一時ファイル削除
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            
            # エラーチェック
            if "error" in res:
                st.error(f"❌ {res['error']}")
                st.info("💡 音声が明瞭か、ファイル形式が対応しているか確認してください。")
            else:
                st.success("✅ 音声解析完了！")
                
                # プレーヤーとジャンプボタン
                render_sticky_player_and_buttons(res["audio_content"], res["word_data"])
                
                # 認識結果表示
                st.markdown(
                    f"""
                    <div style="background-color:#f8f9fa;
                                padding:15px;
                                border-radius:8px;
                                margin-bottom:20px;">
                        <strong>📝 認識結果</strong><br>
                        <span style="font-size:16px;">{res["main_text"]}</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                
                # 詳細データ
                with st.expander("🔍 詳細データ（単語別信頼度）"):
                    st.write(res['details'])
                
                # Gemini分析
                with st.spinner('🤖 AI評価レポートを生成中...'):
                    report = ask_gemini(
                        student_name, 
                        nationality, 
                        res["main_text"], 
                        res["alts"], 
                        res["details"]
                    )
                
                # レポート表示
                st.markdown("---")
                st.markdown("## 📊 音声評価レポート")
                st.markdown(report)
                
                # スプレッドシート保存
                parsed = parse_summary(report)
                
                if parsed["score"] != "0":
                    with st.spinner("💾 データを保存中..."):
                        # ★修正箇所: 日時を日本時間(JST)で取得
                        now_jst = get_jst_now()
                        save_data = {
                            "date": now_jst.strftime('%Y-%m-%d %H:%M'),
                            "name": student_name or "匿名",
                            "nationality": nationality or "不明",
                            **parsed
                        }
                        
                        ok, msg = save_to_sheet(save_data)
                        
                        if ok:
                            st.toast("✅ スプレッドシートに保存しました", icon="✅")
                        else:
                            st.warning(f"⚠️ 保存失敗: {msg}")
                else:
                    st.warning("⚠️ スコアの自動抽出に失敗しましたが、レポートは正常に生成されています。")

                # ダウンロードボタン
                now_jst = get_jst_now()
                st.markdown("---")
                download_text = f"""
日本語音声評価レポート
====================

【評価日時】 {now_jst.strftime('%Y年%m月%d日 %H:%M')} (JST)
【学習者名】 {student_name or '匿名'}
【母語】 {nationality or '不明'}

【認識結果】
{res['main_text']}

【評価レポート】
{report}

---
生成元: 日本語音声指導補助ツール v6.6
"""
                
                st.download_button(
                    label="📥 レポートをダウンロード",
                    data=download_text,
                    file_name=f"{student_name or '匿名'}_音声評価_{now_jst.strftime('%Y%m%d_%H%M%S')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
                
                # 下部に余白を追加（プレーヤーが被らないように）
                st.markdown("<br><br><br><br>", unsafe_allow_html=True)

# フッター
st.markdown("---")
st.caption("👨‍🏫 日本語音声指導補助ツール v6.6 | Powered by Google Cloud Speech-to-Text & Gemini AI")
