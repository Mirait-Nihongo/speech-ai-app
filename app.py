import streamlit as st
import os
import io
import json
import requests
import tempfile
from google.cloud import speech
from google.oauth2 import service_account

# --- 設定 ---
st.set_page_config(page_title="日本語発音AI診断", page_icon="🎤", layout="centered")
st.title("🎤 日本語発音 AI診断ツール")
st.markdown("学習者の音声をアップロードしてください。AIが自動で**聞き取り**と**発音指導**を行います。")

# --- 認証情報の読み込み (Streamlit Secrets) ---
# Secretsから情報を取得し、一時ファイルを作成して認証を通す
try:
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    google_json_str = st.secrets["GOOGLE_JSON"]
    
    # JSONキーを一時ファイルとして保存
    with open("google_key.json", "w") as f:
        f.write(google_json_str)
    json_path = "google_key.json"
except Exception as e:
    st.error("⚠️ 設定エラー: Secretsが設定されていません。")
    st.stop()

# --- 関数群 ---
def analyze_audio(audio_path):
    try:
        credentials = service_account.Credentials.from_service_account_file(json_path)
        client = speech.SpeechClient(credentials=credentials)
    except Exception as e:
        return {"error": f"認証エラー: {e}"}

    # ffmpeg変換
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_converted:
        converted_path = tmp_converted.name
    
    # 変換コマンド
    cmd = f'ffmpeg -y -i "{audio_path}" -ac 1 -ar 16000 -ab 32k "{converted_path}" -loglevel panic'
    exit_code = os.system(cmd)
    
    if exit_code != 0:
        return {"error": "音声変換エラー"}

    # STT実行
    with io.open(converted_path, "rb") as f:
        content = f.read()
    
    try:
        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED,
            sample_rate_hertz=16000,
            language_code="ja-JP",
            enable_automatic_punctuation=False,
            max_alternatives=3,
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
    return {
        "main_text": alt.transcript,
        "alts": ", ".join([a.transcript for a in result.alternatives]),
        "details": ", ".join([f"{w.word}({int(w.confidence*100)})" for w in alt.words])
    }

def ask_gemini(text, alts, details):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_api_key}"
    prompt = f"""
    あなたは日本語発音のプロ講師です。学習者へのフィードバックレポートを作成してください。
    【データ】聞き取り:{text} / 候補:{alts} / 詳細スコア:{details}
    【指示】1.発音スコア(100点満点) 2.改善点3つ 3.励まし
    """
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        res = requests.post(url, headers={'Content-Type': 'application/json'}, data=json.dumps(data))
        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        return "AI生成エラー"
    except: return "通信エラー"

# --- メイン画面 ---
st.info("👇 ここに音声ファイルを置いてください")
uploaded_file = st.file_uploader("", type=["mp3", "wav", "m4a"])

if st.button("🚀 診断を開始する", type="primary"):
    if uploaded_file:
        with st.spinner('🎧 AIが解析中...'):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_audio:
                tmp_audio.write(uploaded_file.getvalue())
                tmp_audio_path = tmp_audio.name
            
            res = analyze_audio(tmp_audio_path)
            
            if "error" in res:
                st.error(res["error"])
            else:
                st.success("完了！")
                st.subheader("🗣️ 聞き取り結果")
                st.info(res["main_text"])
                
                with st.expander("詳細データ"):
                    st.text(f"詳細: {res['details']}")

                st.markdown("---")
                st.subheader("📝 先生からのフィードバック")
                st.markdown(ask_gemini(res["main_text"], res["alts"], res["details"]))
            
            if os.path.exists(tmp_audio_path): os.remove(tmp_audio_path)
    else:
        st.warning("ファイルをアップロードしてください")
