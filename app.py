import streamlit as st
import os
import io
import json
import requests
import tempfile
from google.cloud import speech
from google.oauth2 import service_account

# --- 設定 ---
st.set_page_config(page_title="日本語発音 指導補助ツール", page_icon="👨‍🏫", layout="centered")
st.title("👨‍🏫 日本語発音 指導補助ツール")
st.markdown("""
学習者の音声をアップロードしてください。
教師向けに**プロミネンス・調音点・アクセント・拍**などを網羅した詳細な分析レポートを作成します。
""")

# --- 認証情報の読み込み ---
try:
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    google_json_str = st.secrets["GOOGLE_JSON"]
    
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
    
    return {
        "main_text": alt.transcript,
        "alts": ", ".join(all_candidates),
        "details": ", ".join([f"{w.word}({int(w.confidence*100)})" for w in alt.words])
    }

def ask_gemini(text, alts, details):
    # ★修正: 1.5系ではなく、最も安定した標準モデル "gemini-pro" を指定
    MODEL_NAME = "gemini-pro"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={gemini_api_key}"
    
    prompt = f"""
    あなたは日本語音声学・日本語教育の専門家です。
    Google Speech-to-Textの認識結果データを分析し、教師が指導に使うための専門的な「発音診断カルテ」を作成してください。

    【分析用データ】
    1. **認識結果 (Transcript)**: {text}
    2. **認識の揺れ (Alternatives)**: {alts}
       ※ここに現れる「誤認識された語」は、調音点のズレ（例:「シ」が「ス」に聞こえるなど）を示唆している可能性があります。
    3. **信頼度スコア (Confidence)**: {details}
       ※スコアが低い箇所は、アクセントやプロミネンスが不自然だった可能性があります。

    【指示】
    学習者へのメッセージではなく、**教師への分析報告**として出力してください。
    以下の5つの観点について、具体的かつ専門的に記述してください。

    【出力フォーマット】
    ### 1. 総合所見
    * **推定明瞭度**: （100点満点）
    * **全体傾向**: （発話速度、ポーズの不自然さなど）

    ### 2. プロソディ分析
    * **プロミネンス (卓立)**: 
        * 意味的な焦点（Focus）が適切な語に置かれているか。強調すべきでない助詞などが強くなっていないか。
    * **アクセント (ピッチ)**: 
        * 語彙の
