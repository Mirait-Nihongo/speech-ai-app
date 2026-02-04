import streamlit as st
import os
import io
import tempfile
import datetime
import base64
import google.generativeai as genai
from google.cloud import speech
from google.oauth2 import service_account
import streamlit.components.v1 as components

# --- 設定 ---
st.set_page_config(page_title="日本語音声 指導補助ツール v4.0", page_icon="👨‍🏫", layout="centered")
st.title("👨‍🏫 日本語音声 指導補助ツール")
st.markdown("教師向け：対照言語学に基づく音声評価・誤用分析（動画完全対応版）")

# --- 認証情報の読み込み ---
try:
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    google_json_str = st.secrets["GOOGLE_JSON"]
    
    genai.configure(api_key=gemini_api_key)
    
    with open("google_key.json", "w") as f:
        f.write(google_json_str)
    json_path = "google_key.json"
except Exception as e:
    st.error("⚠️ 設定エラー: Secretsの設定を確認してください。")
    st.stop()

# --- 関数群 ---

def analyze_audio(source_path):
    """
    音声または動画ファイルを受け取り、MP3に変換して認識・分析を行う
    """
    try:
        credentials = service_account.Credentials.from_service_account_file(json_path)
        client = speech.SpeechClient(credentials=credentials)
    except Exception as e:
        return {"error": f"認証エラー: {e}"}

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_converted:
        converted_path = tmp_converted.name
    
    # 動画ストリームを無視(-vn)して音声のみ抽出
    cmd = f'ffmpeg -y -i "{source_path}" -vn -ac 1 -ar 16000 -ab 32k "{converted_path}" -loglevel panic'
    exit_code = os.system(cmd)
    
    if exit_code != 0:
        return {"error": "ファイル変換エラー"}

    with io.open(converted_path, "rb") as f:
        content = f.read()
    
    try:
        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED,
            sample_rate_hertz=16000,
            language_code="ja-JP",
            enable_automatic_punctuation=True,
            max_alternatives=1, 
            enable_word_confidence=True,
            enable_word_time_offsets=True
        )
        operation = client.long_running_recognize(config=config, audio=audio)
        response = operation.result(timeout=600)
    except Exception as e:
        return {"error": f"認識エラー: {e}"}
    finally:
        if os.path.exists(converted_path): os.remove(converted_path)

    if not response.results:
        return {"error": "音声認識不可(無音/ノイズ)"}

    full_transcript = ""
    full_details = []
    word_data_list = []
    
    for result in response.results:
        alt = result.alternatives[0]
        full_transcript += alt.transcript
        
        for w in alt.words:
            score = int(w.confidence * 100)
            start_seconds = w.start_time.total_seconds()
            
            marker = " ⚠️" if w.confidence < 0.8 else ""
            full_details.append(f"{w.word}({score}){marker}")
            
            word_data_list.append({
                "word": w.word,
                "conf": w.confidence,
                "start": start_seconds
            })
            
    formatted_details = ", ".join(full_details)
    all_candidates_str = "（長尺モードのため省略）"

    return {
        "main_text": full_transcript,
        "alts": all_candidates_str,
        "details": formatted_details,
        "audio_content": content,
        "word_data": word_data_list
    }

def ask_gemini(student_name, nationality, text, alts, details):
    try:
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        if not available_models:
            return "❌ エラー: 利用可能なGeminiモデルが見つかりません。"

        target_model = available_models[0]
        for m in available_models:
            if "gemini-1.5-flash" in m:
                target_model = m
                break
            elif "gemini-pro" in m:
                target_model = m
        
        model = genai.GenerativeModel(target_model)
        
        name_part = f"学習者名は「{student_name}」です。" if student_name else "学習者名は不明です。"
        
        if nationality:
            nat_instruction = f"学習者の母語・国籍は「{nationality}」です。この言語と日本語の対照言語学的視点から分析してください。"
        else:
            nat_instruction = "母語情報は不明です。一般的な誤用分析を行ってください。"

        prompt = f"""
        あなたは日本語音声学・対照言語学・日本語教育の高度な専門家です。
        以下の音声認識データに基づき、教師が指導に活用するための詳細な「音声評価」を作成してください。

        【基本情報】
        {name_part}
        {nat_instruction}
        
        【分析対象データ】
        ※データ内の「⚠️」は、機械判定の信頼度が低い（不明瞭または誤音の可能性が高い）箇所です。
        1. 認識結果: {text}
        2. 詳細スコア: {details}

        【出力形式（厳守）】
        レポートの冒頭に、以下の「総合評価サマリー」を出力してください。
        **各項目は必ず改行し、箇条書きで見やすく表示してください。**

        ### 【総合評価サマリー】

        * **総合音声スコア**： [ここに0~100の数値を算出] / 100
        * **明瞭度**： [S/A/B/C]
            * [短い評価コメント]
        * **日本語らしさ（リズム・拍）**： [S/A/B/C]
            * [短い評価コメント]
        * **要重点指導音**：
            * [特に改善すべき音や項目1]
            * [特に改善すべき音や項目2]

        ---
        
        【詳細評価項目（5つの観点）】
        以下の5つの観点を含めて詳細な分析を行ってください。

        1. **音韻体系の対照分析**
           - {nationality if nationality else "学習者の母語"}の音韻体系と日本語の相違点に基づく全体的傾向
        
        2. **母語にない・区別されない日本語音**
           - 母語に存在しないため代用されている音、統合されてしまっている音の指摘
           - (例: 清濁、有気・無気、特定の母音など)

        3. **調音位置・調音方法のずれ**
           - 具体的な調音点（舌の位置、唇の形）や調音方法（閉鎖、摩擦の強さ）の誤り
           - ⚠️が付いている箇所を中心に、どのような物理的ズレが起きているか推測してください

        4. **知覚上の誤認（聞き分けの問題）**
           - 発音の誤りが「音を聞き分けられていない」ことに起因する可能性の分析
           - 「揺れ（別候補）」データから、学習者がどの音と混同しているか分析

        5. **日本語特有のプロソディ**
           - 拍（モーラ）感覚、長音、促音（っ）、撥音（ん）のリズム
           - ピッチアクセントとイントネーションの自然さ

        最後に「最優先指導計画」を提案してください。
        """
        response = model.generate_content(prompt)
        return response.text

    except Exception as e:
        return f"❌ 予期せぬエラー: {e}"

# --- ★HTML生成用関数（Iframe用） ---
def create_player_and_buttons_html(audio_content, word_data):
    """
    プレーヤーとボタンのみを含むHTMLを作成する
    ※テキスト本文は含めない
    """
    b64_audio = base64.b64encode(audio_content).decode()
    
    # ボタンリストのHTML作成
    buttons_html = ""
    count = 0
    for item in word_data:
        if item['conf'] < 0.8:
            start = item['start']
            word = item['word']
            conf = int(item['conf'] * 100)
            buttons_html += f"""
            <button onclick="seekTo({start})" class="play-btn">
                ▶ {word} <span class="conf">({conf}%)</span>
            </button>
            """
            count += 1
            
    if count == 0:
        buttons_html = "<div style='color:#666; padding:10px;'>特に低い信頼度の箇所は見つかりませんでした（優秀です！）</div>"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: sans-serif; margin: 0; padding: 10px; padding-bottom: 90px; background-color: #ffffff; }}
            .alert-box {{ background-color: #fff3cd; border: 1px solid #ffeeba; border-radius: 8px; padding: 15px; }}
            .alert-title {{ margin-top: 0; color: #856404; font-weight: bold; margin-bottom: 10px; font-size: 14px; }}
            .btn-container {{ display: flex; flex-wrap: wrap; gap: 8px; }}
            .play-btn {{
                background-color: #ffffff; border: 1px solid #d3d3d3; border-radius: 5px;
                padding: 6px 12px; cursor: pointer; color: #d9534f; font-weight: bold;
                font-size: 14px; display: flex; align-items: center; gap: 5px;
                transition: background-color 0.2s;
            }}
            .play-btn:hover {{ background-color: #f8f9fa; border-color: #adadad; }}
            .conf {{ font-size: 12px; color: #666; font-weight: normal; }}
            
            .sticky-player {{
                position: fixed; bottom: 0; left: 0; width: 100%;
                background-color: #f1f3f5; border-top: 1px solid #dee2e6;
                padding: 10px; text-align: center; box-shadow: 0 -2px 10px rgba(0,0,0,0.05);
            }}
            audio {{ width: 100%; max-width: 600px; }}
        </style>
        <script>
            function seekTo(seconds) {{
                var player = document.getElementById('audio-player');
                player.currentTime = seconds;
                player.play();
            }}
        </script>
    </head>
    <body>
        <div class="alert-box">
            <div class="alert-title">⚠️ 低信頼度・要確認箇所（クリックで再生）</div>
            <div class="btn-container">
                {buttons_html}
            </div>
            <div style="font-size: 12px; color: #856404; margin-top: 8px;">
                ※ボタンを押すと、下のプレーヤーでその箇所が再生されます。
            </div>
        </div>

        <div class="sticky-player">
            <audio id="audio-player" controls>
                <source src="data:audio/mp3;base64,{b64_audio}" type="audio/mp3">
                Your browser does not support the audio element.
            </audio>
        </div>
    </body>
    </html>
    """
    return html

# --- メイン画面 ---
st.info("👇 学習者の情報を入力してください")

col1, col2 = st.columns(2)

with col1:
    student_name = st.text_input("学習者氏名", placeholder="例: ジョン・スミス")

with col2:
    nationality = st.text_input("母語・国籍 (分析に必須)", placeholder="例: ベトナム語、中国語、英語")

tab1, tab2 = st.tabs(["📁 ファイルをアップロード", "🎙️ その場で録音する"])

target_file = None 
file_type = "audio" 

with tab1:
    uploaded_file = st.file_uploader("ファイルを選択 (音声・動画)", type=["mp3", "wav", "m4a", "mp4", "mov", "avi", "mkv"])
    if uploaded_file:
        ext = uploaded_file.name.split('.')[-1].lower()
        if ext in ['mp4', 'mov', 'avi', 'mkv']:
            st.video(uploaded_file)
            file_type = "video"
        else:
            st.audio(uploaded_file)
            file_type = "audio"
        target_file = uploaded_file

with tab2:
    st.write("ボタンを押して話し、終わったら停止ボタンを押してください。")
    recorded_audio = st.audio_input("録音開始")
    if recorded_audio:
        target_file = recorded_audio
        file_type = "audio"

# --- 分析ボタン ---
if st.button("🚀 音声評価を開始する", type="primary"):
    if target_file:
        with st.spinner('🎧 動画・音声から分析データを抽出中...'):
            file_bytes = target_file.getvalue()
            suffix = ".mp4" if file_type == "video" else ".mp3"
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_source:
                tmp_source.write(file_bytes)
                tmp_source_path = tmp_source.name
            
            res = analyze_audio(tmp_source_path)
            
            if "error" in res:
                st.error(res["error"])
            else:
                st.success("解析完了")

                st.subheader("🗣️ 音声認識・再生パネル")
                st.info("下の枠内をスクロールして確認できます。ボタンを押すと再生されます。")

                # --- 変更点: テキストはコンテナの外（直下）に表示 ---
                st.markdown(
                    f"""
                    <div style="
                        background-color: #f8f9fa; 
                        padding: 15px; 
                        border-radius: 8px; 
                        border: 1px solid #dee2e6;
                        color: #212529;
                        line-height: 1.8;
                        margin-bottom: 20px;
                    ">
                        <strong>【認識結果】</strong><br>
                        {res["main_text"]}
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                
                # --- 変更点: コンテナ（Iframe）にはボタンとプレーヤーのみ表示 ---
                # テキストが外に出た分、Iframeの高さを少し調整
                html_code = create_player_and_buttons_html(res["audio_content"], res["word_data"])
                components.html(html_code, height=250, scrolling=True)
                
                with st.expander("🔍 分析用生データ (教師用)", expanded=False):
                    st.write("※スコアが80未満の箇所には ⚠️ が付いています")
                    st.write(f"信頼度詳細: {res['details']}")

                st.markdown("---")
                
                title_suffix = f" ({nationality})" if nationality else ""
                name_display = student_name if student_name else "学習者"
                
                st.subheader(f"📝 {name_display}さんの音声評価{title_suffix}")
                
                report_content = ask_gemini(student_name, nationality, res["main_text"], res["alts"], res["details"])
                st.markdown(report_content)
                
                today_str = datetime.datetime.now().strftime('%Y-%m-%d')
                safe_name = student_name if student_name else "student"
                safe_nat = nationality if nationality else "unknown"
                
                download_text = f"""================================
日本語音声評価レポート
================================
■ 実施日: {today_str}
■ 学習者: {safe_name}
■ 母語・国籍: {safe_nat}

【音声認識結果】
{res['main_text']}

【詳細スコア (信頼度)】
※80点未満は ⚠️ マーク付き
{res['details']}

--------------------------------
【AI講師による音声評価】
--------------------------------
{report_content}
"""
                file_name = f"{safe_name}_{today_str}_report.txt"

                st.download_button(
                    label="📥 評価結果をテキストで保存",
                    data=download_text,
                    file_name=file_name,
                    mime="text/plain"
                )

            if os.path.exists(tmp_source_path): os.remove(tmp_source_path)
    else:
        st.warning("ファイルを選択するか、録音してください。")
