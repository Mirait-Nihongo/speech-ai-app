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
st.set_page_config(page_title="日本語音声 指導補助ツール v5.2", page_icon="👨‍🏫", layout="centered")
st.title("👨‍🏫 日本語音声 指導補助ツール")
st.markdown("教師向け：対照言語学に基づく音声評価・誤用分析＋学習ログ保存")

# --- 認証情報の読み込み ---
try:
    # Secretsから情報を取得
    gemini_api_key = st.secrets.get("GEMINI_API_KEY")
    
    # Google Cloud認証情報 (JSON or Dict)
    if "GOOGLE_JSON" in st.secrets:
        google_json_data = st.secrets["GOOGLE_JSON"]
        if isinstance(google_json_data, str):
            try:
                google_creds_dict = json.loads(google_json_data)
            except:
                st.error("⚠️ SecretsのGOOGLE_JSONが正しいJSON形式ではありません。")
                st.stop()
        else:
            google_creds_dict = dict(google_json_data)
    else:
        st.error("⚠️ Secretsに GOOGLE_JSON が設定されていません。")
        st.stop()

    if not gemini_api_key:
        st.error("⚠️ Secretsに GEMINI_API_KEY が設定されていません。")
        st.stop()

    genai.configure(api_key=gemini_api_key)

except Exception as e:
    st.error(f"⚠️ 設定エラー: Secretsの設定を確認してください。\n詳細: {e}")
    st.stop()

# --- サイドバー：システム診断ツール ---
with st.sidebar:
    st.header("🔧 システム状態チェック")
    if st.button("API接続テスト & モデル一覧取得"):
        try:
            st.write("問い合わせ中...")
            available_models = []
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    available_models.append(m.name)
            
            if available_models:
                st.success(f"✅ API接続成功！ ({len(available_models)}個のモデルを検出)")
                st.code("\n".join(available_models))
                st.info("※ 上記リストにあるモデル名が分析に使用されます。")
            else:
                st.warning("⚠️ 接続はできましたが、利用可能なモデルが見つかりませんでした。")
        except Exception as e:
            st.error(f"❌ API接続エラー: {e}")
            st.write("ヒント: GEMINI_API_KEY が正しいか、またはGoogle AI StudioでAPIが無効になっていないか確認してください。")

# --- 関数群 ---

def analyze_audio(source_path):
    """音声認識を実行"""
    try:
        credentials = service_account.Credentials.from_service_account_info(google_creds_dict)
        client = speech.SpeechClient(credentials=credentials)
    except Exception as e:
        return {"error": f"認証エラー: {e}"}

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_converted:
        converted_path = tmp_converted.name
    
    # ffmpeg
    cmd = f'ffmpeg -y -i "{source_path}" -vn -ac 1 -ar 16000 -ab 32k "{converted_path}" -loglevel panic'
    exit_code = os.system(cmd)
    
    if exit_code != 0:
        return {"error": "ファイル変換エラー (FFmpeg)"}

    with io.open(converted_path, "rb") as f:
        content = f.read()
    
    try:
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
            time_str = f"[{start_seconds:.1f}s]"
            marker = " ⚠️" if w.confidence < 0.8 else ""
            full_details.append(f"{w.word}({score}){time_str}{marker}")
            word_data_list.append({"word": w.word, "conf": w.confidence, "start": start_seconds})
            
    return {
        "main_text": full_transcript,
        "details": ", ".join(full_details),
        "audio_content": content,
        "word_data": word_data_list,
        "alts": ""
    }

def ask_gemini(student_name, nationality, text, alts, details):
    # ★修正完了: あなたの環境で実際に使えるモデル名(診断リスト準拠)に変更しました
    target_models = [
        "gemini-2.0-flash",       # 最新・高速 (リストに存在)
        "gemini-2.5-flash",       # さらに新しいモデル (リストに存在)
        "gemini-flash-latest",    # 最新版エイリアス (リストに存在)
        "gemini-pro-latest"       # Pro版最新 (リストに存在)
    ]
    
    model = None
    last_error = None
    
    # 利用可能なモデルを探して実行を試みる
    for m_name in target_models:
        try:
            # モデルの初期化
            temp_model = genai.GenerativeModel(m_name)
            
            # プロンプト作成
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

            【重要】
            信頼度(⚠️)が低い箇所を発音ミスとして分析してください。

            【出力形式】
            冒頭に以下を出力:
            ### 【総合評価サマリー】
            * **総合音声スコア**： [0~100] / 100
            * **明瞭度**： [S/A/B/C]
            * **日本語らしさ**： [S/A/B/C]
            * **要重点指導音**： [項目]

            ---
            詳細分析（音韻、プロソディなど）、調音点の比較、指導計画を含めてください。
            """
            
            # 生成実行
            response = temp_model.generate_content(prompt)
            return response.text # 成功したら返す
            
        except Exception as e:
            last_error = e
            continue # 失敗したら次のモデルへ
            
    return f"❌ Gemini生成エラー (全てのモデルで失敗): {last_error}"

# --- スプレッドシート連携 ---
def parse_summary(report_text):
    score_match = re.search(r'\*\*総合音声スコア\*\*：\s*(\d+)', report_text)
    clarity_match = re.search(r'\*\*明瞭度\*\*：\s*([SABC])', report_text)
    natural_match = re.search(r'\*\*日本語らしさ\*\*：\s*([SABC])', report_text)
    summary_block = "サマリー抽出失敗"
    try:
        start = report_text.find("### 【総合評価サマリー】")
        end = report_text.find("---", start)
        if start != -1 and end != -1:
            summary_block = report_text[start:end].strip()
    except:
        pass
    return {
        "score": score_match.group(1) if score_match else "0",
        "clarity": clarity_match.group(1) if clarity_match else "-",
        "naturalness": natural_match.group(1) if natural_match else "-",
        "summary_text": summary_block
    }

def save_to_sheet(data_dict):
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = service_account.Credentials.from_service_account_info(google_creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        
        sheet_url = st.secrets.get("SHEET_URL")
        if sheet_url:
            sheet = client.open_by_url(sheet_url).sheet1
        else:
            return False, "SHEET_URL未設定"
        
        row = [data_dict["date"], data_dict["name"], data_dict["nationality"], data_dict["score"], data_dict["clarity"], data_dict["naturalness"], data_dict["summary_text"]]
        sheet.append_row(row)
        return True, "成功"
    except Exception as e:
        return False, str(e)

# --- UIコンポーネント ---
def create_search_button(error_sound):
    url = f"https://www.google.com/search?q=日本語+{error_sound}+発音+口腔断面図+イラスト&tbm=isch"
    st.link_button(f"🔍 「{error_sound}」の断面図を検索", url)

def render_sticky_player_and_buttons(audio_content, word_data):
    b64_audio = base64.b64encode(audio_content).decode()
    buttons_html = ""
    count = 0
    unique_id = int(datetime.datetime.now().timestamp())
    for item in word_data:
        if item['conf'] < 0.8:
            start = item['start']
            word = item['word']
            conf = int(item['conf'] * 100)
            buttons_html += f'<button class="seek-btn-{unique_id}" data-seek="{start}" style="background-color: #ffffff; border: 1px solid #d3d3d3; border-radius: 5px; padding: 6px 12px; cursor: pointer; color: #d9534f; font-weight: bold; font-size: 14px; display: inline-flex; align-items: center; gap: 5px; margin: 4px;">▶ {word} <span style="font-size:12px; color:#666; font-weight:normal;">({conf}%)</span></button>'
            count += 1
    if count == 0: buttons_html = "<div style='color:#666; padding:10px;'>低信頼度の箇所なし</div>"

    html_code = f"""
    <div id="sticky-audio-container-{unique_id}" style="position: fixed; bottom: 0; left: 0; width: 100%; background-color: #f1f3f5; border-top: 1px solid #dee2e6; padding: 10px 0; text-align: center; box-shadow: 0 -2px 10px rgba(0,0,0,0.05); z-index: 999999;">
        <audio id="audio-player-{unique_id}" controls style="width: 90%; max-width: 600px;"><source src="data:audio/mp3;base64,{b64_audio}" type="audio/mp3"></audio>
    </div>
    <script>
        function setupInteraction() {{
            var player = document.getElementById("audio-player-{unique_id}");
            var buttons = window.parent.document.getElementsByClassName("seek-btn-{unique_id}");
            for (var i = 0; i < buttons.length; i++) {{
                buttons[i].onclick = function() {{
                    player.currentTime = this.getAttribute("data-seek");
                    player.play();
                }};
            }}
        }}
        setInterval(setupInteraction, 2000);
    </script>
    """
    st.markdown(f"""<div style="background-color: #fff3cd; border: 1px solid #ffeeba; border-radius: 8px; padding: 15px; margin-bottom: 20px;"><div style="color: #856404; font-weight: bold;">⚠️ クリックで再生</div><div>{buttons_html}</div></div>""", unsafe_allow_html=True)
    components.html(f"{html_code}<script>var frame=window.frameElement;if(frame){{frame.style.position='fixed';frame.style.bottom='0';frame.style.height='80px';frame.style.zIndex='999999';}}</script>", height=0)

# --- メイン処理 ---
st.info("👇 学習者の情報を入力してください")
col1, col2 = st.columns(2)
with col1: student_name = st.text_input("学習者氏名")
with col2: nationality = st.text_input("母語・国籍")

tab1, tab2 = st.tabs(["📁 ファイル", "🎙️ 録音"])
target_file = None
file_type = "audio"
with tab1:
    uploaded_file = st.file_uploader("ファイルを選択", type=["mp3", "wav", "m4a", "mp4", "mov"])
    if uploaded_file:
        st.audio(uploaded_file) if uploaded_file.name.split('.')[-1] not in ['mp4','mov'] else st.video(uploaded_file)
        target_file = uploaded_file
with tab2:
    recorded_audio = st.audio_input("録音開始")
    if recorded_audio: target_file = recorded_audio

if st.button("🚀 音声評価を開始する", type="primary"):
    if target_file:
        with st.spinner('🎧 分析中...'):
            file_bytes = target_file.getvalue()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            
            res = analyze_audio(tmp_path)
            if "error" in res:
                st.error(res["error"])
            else:
                st.success("解析完了")
                render_sticky_player_and_buttons(res["audio_content"], res["word_data"])
                st.markdown(f"""<div style="background-color:#f8f9fa;padding:15px;border-radius:8px;"><strong>認識結果</strong><br>{res["main_text"]}</div>""", unsafe_allow_html=True)
                
                with st.expander("詳細データ"): st.write(res['details'])
                
                report = ask_gemini(student_name, nationality, res["main_text"], res["alts"], res["details"])
                st.markdown(report)
                
                # スプレッドシート保存
                parsed = parse_summary(report)
                if parsed["score"] != "0":
                    with st.spinner("💾 保存中..."):
                        save_data = {"date": datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), "name": student_name or "匿名", "nationality": nationality or "不明", **parsed}
                        ok, msg = save_to_sheet(save_data)
                        if ok: st.toast("保存しました")
                        else: st.warning(f"保存失敗: {msg}")

                # ダウンロード
                dl_txt = f"日時: {datetime.datetime.now()}\n氏名: {student_name}\n\n{res['main_text']}\n\n{report}"
                st.download_button("📥 結果を保存", dl_txt, f"{student_name}_report.txt")
            if os.path.exists(tmp_path): os.remove(tmp_path)
    else:
        st.warning("音声を入力してください")
