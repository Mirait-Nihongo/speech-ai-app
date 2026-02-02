import streamlit as st
import os
import io
import tempfile
import datetime
import base64
import google.generativeai as genai
from google.cloud import speech
from google.oauth2 import service_account

# --- 設定 ---
st.set_page_config(page_title="日本語発音 指導補助ツール", page_icon="👨‍🏫", layout="centered")
st.title("👨‍🏫 日本語発音 指導補助ツール")
st.markdown("教師向け：プロミネンス・調音点・アクセント・拍の詳細分析")

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

# --- 固定オーディオプレーヤー生成関数 ---
def get_sticky_audio_player(audio_bytes):
    """音声データをBase64に変換して、画面下に固定されるHTMLプレーヤーを作る"""
    b64 = base64.b64encode(audio_bytes).decode()
    md = f"""
        <style>
            .sticky-audio {{
                position: fixed;
                bottom: 0;
                left: 0;
                width: 100%;
                background-color: #f0f2f6; /* 背景色 */
                padding: 10px 20px;
                z-index: 99999;
                border-top: 1px solid #ccc;
                text-align: center;
                box-shadow: 0px -2px 10px rgba(0,0,0,0.1);
            }}
            /* 再生バーが被らないように、メイン画面の下に余白を作る */
            .main .block-container {{
                padding-bottom: 100px;
            }}
        </style>
        <div class="sticky-audio">
            <div style="margin-bottom:5px; font-weight:bold; font-size:0.9em; color:#333;">
                🔊 録音データ再生（診断カルテを見ながら聞いてください）
            </div>
            <audio controls src="data:audio/mp3;base64,{b64}" style="width: 100%; max-width: 600px;"></audio>
        </div>
    """
    return md

def analyze_audio(audio_path):
    try:
        credentials = service_account.Credentials.from_service_account_file(json_path)
        client = speech.SpeechClient(credentials=credentials)
    except Exception as e:
        return {"error": f"認証エラー: {e}"}

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_converted:
        converted_path = tmp_converted.name
    
    cmd = f'ffmpeg -y -i "{audio_path}" -ac 1 -ar 16000 -ab 32k "{converted_path}" -loglevel panic'
    exit_code = os.system(cmd)
    
    if exit_code != 0:
        return {"error": "音声変換エラー"}

    with io.open(converted_path, "rb") as f:
        content = f.read()
    
    try:
        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED,
            sample_rate_hertz=16000,
            language_code="ja-JP",
            enable_automatic_punctuation=False,
            max_alternatives=5, 
            enable_word_confidence=True
        )
        operation = client.long_running_recognize(config=config, audio=audio)
        response = operation.result(timeout=600)
    except Exception as e:
        return {"error": f"認識エラー: {e}"}
    finally:
        if os.path.exists(converted_path): os.remove(converted_path)

    if not response.results:
        return {"error": "音声認識不可(無音/ノイズ)"}

    result = response.results[0]
    alt = result.alternatives[0]
    all_candidates = [a.transcript for a in result.alternatives]
    
    # --- ★修正箇所: 信頼度80%未満に⚠️マークをつける ---
    details_list = []
    for w in alt.words:
        score = int(w.confidence * 100)
        # 信頼度が0.8未満ならマークをつける
        marker = " ⚠️" if w.confidence < 0.8 else ""
        details_list.append(f"{w.word}({score}){marker}")
    
    formatted_details = ", ".join(details_list)
    # ---------------------------------------------------

    return {
        "main_text": alt.transcript,
        "alts": ", ".join(all_candidates),
        "details": formatted_details
    }

def ask_gemini(student_name, text, alts, details):
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
        
        if student_name:
            name_instruction = f"学習者名は「{student_name}」です。レポートの冒頭を「{student_name}さんの発音診断カルテ」とし、文中でも必要に応じて名前で呼んでください。"
        else:
            name_instruction = "学習者名は不明です。レポートの冒頭は単に「発音診断カルテ」とし、特定の個人名を出さずに作成してください。"

        prompt = f"""
        あなたは日本語音声学・日本語教育の専門家です。
        以下のデータを分析し、担当教師が指導に使うための「発音診断カルテ」を作成してください。

        【指示】
        {name_instruction}
        
        ※データ内の「⚠️」マークは、機械判定による信頼度が低い（発音が不明瞭だった可能性がある）箇所を示しています。

        【データ】
        1.認識結果: {text}
        2.揺れ(調音点ズレ示唆): {alts}
        3.スコア(単語ごとの信頼度): {details}

        【出力項目】
        1.総合所見(明瞭度、全体傾向)
        2.プロソディ分析(プロミネンス、アクセント、イントネーション、拍)
        3.分節音分析(子音の調音点、母音)
        4.最優先指導ポイント
        """
        response = model.generate_content(prompt)
        return response.text

    except Exception as e:
        return f"❌ 予期せぬエラー: {e}"

# --- メイン画面 ---
st.info("👇 学習者の情報を入力してください")

student_name = st.text_input("学習者氏名（任意）", placeholder="入力がない場合は「氏名なし」として処理されます")

tab1, tab2 = st.tabs(["📁 ファイルをアップロード", "🎙️ その場で録音する"])

target_audio = None 

with tab1:
    uploaded_file = st.file_uploader("音声ファイルを選択 (mp3, wav, m4a)", type=["mp3", "wav", "m4a"])
    if uploaded_file:
        st.audio(uploaded_file)
        target_audio = uploaded_file

with tab2:
    st.write("ボタンを押して話し、終わったら停止ボタンを押してください。")
    recorded_audio = st.audio_input("録音開始")
    if recorded_audio:
        target_audio = recorded_audio

# --- 分析ボタン ---
if st.button("🚀 専門分析を開始する", type="primary"):
    if target_audio:
        with st.spinner('🎧 音声学的特徴を抽出中...'):
            # 音声データをバイナリで取得しておく（プレーヤー用）
            audio_bytes = target_audio.getvalue()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_audio:
                tmp_audio.write(audio_bytes)
                tmp_audio_path = tmp_audio.name
            
            res = analyze_audio(tmp_audio_path)
            
            if "error" in res:
                st.error(res["error"])
            else:
                st.success("解析完了")

                # --- 固定プレーヤーを表示 ---
                player_html = get_sticky_audio_player(audio_bytes)
                st.markdown(player_html, unsafe_allow_html=True)
                # --------------------------------

                st.subheader("🗣️ 音声認識データ")
                st.code(res["main_text"], language=None)
                
                with st.expander("🔍 分析用生データ (教師用)", expanded=True):
                    st.write("※スコアが80未満の箇所には ⚠️ が付いています")
                    st.write(f"信頼度詳細: {res['details']}")
                    st.write(f"別候補: {res['alts']}")

                st.markdown("---")
                
                if student_name:
                    st.subheader(f"📝 {student_name}さんの発音診断カルテ")
                else:
                    st.subheader("📝 発音診断カルテ")
                
                # レポート生成
                report_content = ask_gemini(student_name, res["main_text"], res["alts"], res["details"])
                st.markdown(report_content)
                
                # --- ダウンロード用テキスト作成 ---
                today_str = datetime.datetime.now().strftime('%Y-%m-%d')
                safe_name = student_name if student_name else "student"
                
                # テキストファイルの中身を作成
                download_text = f"""================================
日本語発音診断レポート
================================
■ 実施日: {today_str}
■ 学習者名: {safe_name}

【音声認識結果】
{res['main_text']}

【詳細スコア (信頼度)】
※80点未満は ⚠️ マーク付き
{res['details']}

【認識候補の揺れ】
{res['alts']}

--------------------------------
【AI講師による診断カルテ】
--------------------------------
{report_content}
"""
                # ファイル名
                file_name = f"{safe_name}_{today_str}_report.txt"

                st.download_button(
                    label="📥 診断結果をテキストで保存",
                    data=download_text,
                    file_name=file_name,
                    mime="text/plain"
                )

            if os.path.exists(tmp_audio_path): os.remove(tmp_audio_path)
    else:
        st.warning("音声ファイルを選択するか、録音してください。")
