import os
import io
import tempfile
import datetime
import google.generativeai as genai
from google.cloud import speech
from google.oauth2 import service_account
@@ -16,7 +17,6 @@
gemini_api_key = st.secrets["GEMINI_API_KEY"]
google_json_str = st.secrets["GOOGLE_JSON"]

    # 公式ライブラリの設定
genai.configure(api_key=gemini_api_key)

with open("google_key.json", "w") as f:
@@ -37,7 +37,6 @@ def analyze_audio(audio_path):
with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_converted:
converted_path = tmp_converted.name

    # 音声変換 (ffmpeg)
cmd = f'ffmpeg -y -i "{audio_path}" -ac 1 -ar 16000 -ab 32k "{converted_path}" -loglevel panic'
exit_code = os.system(cmd)

@@ -78,7 +77,6 @@ def analyze_audio(audio_path):
}

def ask_gemini(student_name, text, alts, details):
    # 自動修復機能
try:
available_models = []
for m in genai.list_models():
@@ -88,7 +86,6 @@ def ask_gemini(student_name, text, alts, details):
if not available_models:
return "❌ エラー: 利用可能なGeminiモデルが見つかりません。"

        # 優先順位: 1.5-flash -> 1.5-pro -> gemini-pro
target_model = available_models[0]
for m in available_models:
if "gemini-1.5-flash" in m:
@@ -99,12 +96,9 @@ def ask_gemini(student_name, text, alts, details):

model = genai.GenerativeModel(target_model)

        # --- ★ここが変更点: 名前の有無で指示を変える ---
if student_name:
            # 名前がある場合
name_instruction = f"学習者名は「{student_name}」です。レポートの冒頭を「{student_name}さんの発音診断カルテ」とし、文中でも必要に応じて名前で呼んでください。"
else:
            # 名前がない（空欄）の場合
name_instruction = "学習者名は不明です。レポートの冒頭は単に「発音診断カルテ」とし、特定の個人名を出さずに作成してください。"

prompt = f"""
@@ -126,18 +120,16 @@ def ask_gemini(student_name, text, alts, details):
       4.最優先指導ポイント
       """
response = model.generate_content(prompt)
        return f"✅ 使用モデル: {target_model}\n\n" + response.text
        return response.text

except Exception as e:
return f"❌ 予期せぬエラー: {e}"

# --- メイン画面 ---
st.info("👇 学習者の情報を入力してください")

# ★追加：氏名入力欄（未入力OK）
student_name = st.text_input("学習者氏名（任意）", placeholder="入力がない場合は「氏名なし」として処理されます")

# タブ切り替え
tab1, tab2 = st.tabs(["📁 ファイルをアップロード", "🎙️ その場で録音する"])

target_audio = None 
@@ -177,15 +169,50 @@ def ask_gemini(student_name, text, alts, details):

st.markdown("---")

                # ★修正：画面上のタイトルも名前の有無で分岐
if student_name:
st.subheader(f"📝 {student_name}さんの発音診断カルテ")
else:
st.subheader("📝 発音診断カルテ")

                report = ask_gemini(student_name, res["main_text"], res["alts"], res["details"])
                st.markdown(report)
            
                # レポート生成
                report_content = ask_gemini(student_name, res["main_text"], res["alts"], res["details"])
                st.markdown(report_content)
                
                # --- ★追加機能: ダウンロード用テキスト作成 ---
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
{res['details']}

【認識候補の揺れ】
{res['alts']}

--------------------------------
【AI講師による診断カルテ】
--------------------------------
{report_content}
"""
                # ファイル名: 例「ラオ・ミン_2023-10-25_report.txt」
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
