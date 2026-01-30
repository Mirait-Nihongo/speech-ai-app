import streamlit as st
import os
import io
import tempfile
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
    
    # 公式ライブラリの設定
    genai.configure(api_key=gemini_api_key)
    
    with open("google_key.json", "w") as f:
        f.write(google_json_str)
    json_path = "google_key.json"
except Exception as e:
    st.error("⚠️ 設定エラー: Secretsの設定を確認してください。")
    st.stop()

# --- 関数群 ---
def analyze_audio(audio_path):
    try:
        credentials = service_account.Credentials.from_service_account_file(json_path)
        client = speech.SpeechClient(credentials=credentials)
    except Exception as e:
        return {"error": f"認証エラー: {e}"}

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_converted:
        converted_path = tmp_converted.name
    
    # 音声変換 (ffmpeg)
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
    
    return {
        "main_text": alt.transcript,
        "alts": ", ".join(all_candidates),
        "details": ", ".join([f"{w.word}({int(w.confidence*100)})" for w in alt.words])
    }

def ask_gemini(student_name, text, alts, details):
    # 自動修復機能
    try:
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        if not available_models:
            return "❌ エラー: 利用可能なGeminiモデルが見つかりません。"

        # 優先順位: 1.5-flash -> 1.5-pro -> gemini-pro
        target_model = available_models[0]
        for m in available_models:
            if "gemini-1.5-flash" in m:
                target_model = m
                break
            elif "gemini-pro" in m:
                target_model = m
        
        model = genai.GenerativeModel(target_model)
        
        # --- ★ここが変更点: 名前の有無で指示を変える ---
        if student_name:
            # 名前がある場合
            name_instruction = f"学習者名は「{student_name}」です。レポートの冒頭を「{student_name}さんの発音診断カルテ」とし、文中でも必要に応じて名前で呼んでください。"
        else:
            # 名前がない（空欄）の場合
            name_instruction = "学習者名は不明です。レポートの冒頭は単に「発音診断カルテ」とし、特定の個人名を出さずに作成してください。"

        prompt = f"""
        あなたは日本語音声学・日本語教育の専門家です。
        以下のデータを分析し、担当教師が指導に使うための「発音診断カルテ」を作成してください。

        【指示】
        {name_instruction}

        【データ】
        1.認識結果: {text}
        2.揺れ(調音点ズレ示唆): {alts}
        3.スコア: {details}

        【出力項目】
        1.総合所見(明瞭度、全体傾向)
        2.プロソディ分析(プロミネンス、アクセント、イントネーション、拍)
        3.分節音分析(子音の調音点、母音)
        4.最優先指導ポイント
        """
        response = model.generate_content(prompt)
        return f"✅ 使用モデル: {target_model}\n\n" + response.text

    except Exception as e:
        return f"❌ 予期せぬエラー: {e}"

# --- メイン画面 ---
st.info("👇 学習者の情報を入力してください")

# ★追加：氏名入力欄（未入力OK）
student_name = st.text_input("学習者氏名（任意）", placeholder="入力がない場合は「氏名なし」として処理されます")

# タブ切り替え
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
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_audio:
                tmp_audio.write(target_audio.getvalue())
                tmp_audio_path = tmp_audio.name
            
            res = analyze_audio(tmp_audio_path)
            
            if "error" in res:
                st.error(res["error"])
            else:
                st.success("解析完了")
                st.subheader("🗣️ 音声認識データ")
                st.code(res["main_text"], language=None)
                
                with st.expander("🔍 分析用生データ (教師用)"):
                    st.write(f"信頼度: {res['details']}")
                    st.write(f"別候補: {res['alts']}")

                st.markdown("---")
                
                # ★修正：画面上のタイトルも名前の有無で分岐
                if student_name:
                    st.subheader(f"📝 {student_name}さんの発音診断カルテ")
                else:
                    st.subheader("📝 発音診断カルテ")
                
                report = ask_gemini(student_name, res["main_text"], res["alts"], res["details"])
                st.markdown(report)
            
            if os.path.exists(tmp_audio_path): os.remove(tmp_audio_path)
    else:
        st.warning("音声ファイルを選択するか、録音してください。")
