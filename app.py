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
st.markdown("""
学習者の音声をアップロードしてください。
教師向けに**プロミネンス・調音点・アクセント・拍**などを網羅した詳細な分析レポートを作成します。
""")

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
    # ★公式ライブラリを使用（接続が安定します）
    # モデル名は最新の gemini-1.5-flash を使用
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        prompt = f"""
        あなたは日本語音声学・日本語教育の専門家です。
        以下のデータを分析し、教師が指導に使うための専門的な「発音診断カルテ」を作成してください。

        【分析用データ】
        1. **認識結果**: {text}
        2. **認識の揺れ (調音点のズレ示唆)**: {alts}
        3. **信頼度スコア (アクセント・不明瞭箇所)**: {details}

        【指示】
        学習者へのメッセージではなく、**教師への分析報告**として出力してください。

        【出力フォーマット】
        ### 1. 総合所見
        * **推定明瞭度**: （100点満点）
        * **全体傾向**: （発話速度、ポーズなど）

        ### 2. プロソディ分析
        * **プロミネンス (卓立)**: 焦点の置き方、助詞の強調など。
        * **アクセント (ピッチ)**: 平板化、起伏型の誤用。
        * **イントネーション (抑揚)**: 文末、フレーズの曲線。
        * **拍の感覚 (モーラ)**: 特殊拍の長さ、リズム。

        ### 3. 分節音分析
        * **子音の調音点**: 認識の揺れから推測される誤り（ザ行、サ行、ラ行など）。
        * **母音**: 無声化、広狭の明瞭さ。

        ### 4. 指導の優先順位
        * （最優先の矯正ポイントと、具体的な指導法）
        """
        
        response = model.generate_content(prompt)
        return response.text
        
    except Exception as e:
        return f"AI生成エラー: {e}"

# --- メイン画面 ---
st.info("👇 ここに学習者の音声ファイルを置いてください")
uploaded_file = st.file_uploader("", type=["mp3", "wav", "m4a"])

if st.button("🚀 専門分析を開始する", type="primary"):
    if uploaded_file:
        with st.spinner('🎧 音声学的特徴を抽出中...'):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_audio:
                tmp_audio.write(uploaded_file.getvalue())
                tmp_audio_path = tmp_audio.name
            
            res = analyze_audio(tmp_audio_path)
            
            if "error" in res:
                st.error(res["error"])
            else:
                st.success("解析完了")
                
                st.subheader("🗣️ 音声認識データ")
                st.code(res["main_text"], language=None)
                
                with st.expander("🔍 分析用生データ (教師用)"):
                    st.write("**信頼度スコア**")
                    st.text(res['details'])
                    st.write("**認識候補の揺れ**")
                    st.text(res['alts'])

                st.markdown("---")
                st.subheader("📝 教師用 発音診断カルテ")
                
                report = ask_gemini(res["main_text"], res["alts"], res["details"])
                st.markdown(report)
            
            if os.path.exists(tmp_audio_path): os.remove(tmp_audio_path)
    else:
        st.warning("ファイルをアップロードしてください")
