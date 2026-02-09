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
st.set_page_config(page_title="日本語音声 指導補助ツール v5.0", page_icon="👨‍🏫", layout="centered")
st.title("👨‍🏫 日本語音声 指導補助ツール")
st.markdown("教師向け：対照言語学に基づく音声評価・誤用分析＋学習ログ保存")

# --- 認証情報の読み込み ---
try:
    # Secretsから情報を取得
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    google_json_str = st.secrets["GOOGLE_JSON"]
    
    # 文字列の場合はJSONオブジェクトに変換（gspread用）
    if isinstance(google_json_str, str):
        google_creds_dict = json.loads(google_json_str)
    else:
        google_creds_dict = google_json_str

    genai.configure(api_key=gemini_api_key)

except Exception as e:
    st.error(f"⚠️ 設定エラー: Secretsの設定を確認してください。\n詳細: {e}")
    st.stop()

# --- 関数群 ---

def analyze_audio(source_path):
    """
    音声または動画ファイルを受け取り、MP3に変換して認識・分析を行う
    """
    try:
        # 辞書から直接認証情報を作成（ファイル保存不要）
        credentials = service_account.Credentials.from_service_account_info(google_creds_dict)
        client = speech.SpeechClient(credentials=credentials)
    except Exception as e:
        return {"error": f"認証エラー: {e}"}

    # FFmpegで変換
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_converted:
        converted_path = tmp_converted.name
    
    # 外部コマンド実行 (ffmpeg)
    cmd = f'ffmpeg -y -i "{source_path}" -vn -ac 1 -ar 16000 -ab 32k "{converted_path}" -loglevel panic'
    exit_code = os.system(cmd)
    
    if exit_code != 0:
        return {"error": "ファイル変換エラー (FFmpegがインストールされていない可能性があります)"}

    with io.open(converted_path, "rb") as f:
        content = f.read()
    
    try:
        audio = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED,
            sample_rate_hertz=16000,
            language_code="ja-JP",
            enable_automatic_punctuation=False, # 補正抑制
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
    # ★修正点: バージョン番号付きのモデル名を優先リストにする
    # genai.GenerativeModel() は初期化時に通信しないため、ここでモデルを決めても
    # 実際に generate_content するまでエラーが分かりません。
    # そのため、リストの上から順に「生成まで試す」ロジックに変更します。
    
    target_models = [
        "gemini-1.5-flash-002", # 最新の安定版
        "gemini-1.5-flash-001", # ひとつ前の安定版
        "gemini-1.5-flash",     # エイリアス（短縮名）
        "gemini-pro"            # 旧モデル（バックアップ）
    ]
    
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
    ※データ形式: 単語(信頼度)[タイムスタンプ] ⚠️マーク
    1. 認識結果: {text}
    2. 詳細スコア: {details}

    【重要な分析方針】
    音声認識AIの自動補正を考慮し、信頼度(⚠️)が低い箇所は「発音ミス」として厳しく分析してください。

    【出力形式（厳守）】
    レポートの冒頭に、以下の「総合評価サマリー」を必ずこの形式で出力してください。

    ### 【総合評価サマリー】
    * **総合音声スコア**： [0~100] / 100
    * **明瞭度**： [S/A/B/C]
    * **日本語らしさ**： [S/A/B/C]
    * **要重点指導音**： [改善すべき音を列挙]

    ---
    
    【詳細評価項目】
    以下の観点で分析してください。
    **具体的な誤用指摘の際は、必ずタイムスタンプを引用すること。**

    1. **音韻体系の対照分析**
    2. **母語にない・区別されない日本語音**
    3. **知覚上の誤認**
    4. **日本語特有のプロソディ**

    ---

    ### 【調音点・調音法の詳細比較分析】
    最も大きな誤用が見られた音を1つ選び、日本語教育能力検定試験の観点（調音点・調音法・鼻音性）から比較解説してください。

    **比較テーブル**
    | 項目 | 正しい日本語の発音 | 学習者の誤った発音 |
    | :--- | :--- | :--- |
    | **鼻への通路** | [開/閉] | [開/閉] |
    | **調音点** | [両唇/歯茎/硬口蓋/軟口蓋] | [接触点] |
    | **調音法** | [破裂/摩擦/破擦/鼻音/弾き] | [変化] |

    **指導アドバイス**
    上記のズレを修正するための具体的指示。
    
    最後に「最優先指導計画」を提案してください。
    """

    # ★修正点: ループ内で実際に generate_content を試行し、成功するまで次へ進む
    last_error = None
    for m_name in target_models:
        try:
            model = genai.GenerativeModel(m_name)
            response = model.generate_content(prompt)
            return response.text # 成功したらここで終了して返す
        except Exception as e:
            last_error = e
            continue # エラーが出たら次のモデルで再挑戦
            
    # 全てのモデルで失敗した場合
    return f"❌ Gemini生成エラー (全てのモデルで失敗): {last_error}"
    
# --- スプレッドシート連携用関数 ---

def parse_summary(report_text):
    """Geminiのレポートから数値を抽出する"""
    score_match = re.search(r'\*\*総合音声スコア\*\*：\s*(\d+)', report_text)
    clarity_match = re.search(r'\*\*明瞭度\*\*：\s*([SABC])', report_text)
    natural_match = re.search(r'\*\*日本語らしさ\*\*：\s*([SABC])', report_text)
    
    # サマリー全文を取得
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
    """スプレッドシートに行を追加する"""
    try:
        # スコープ定義
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        # 認証
        creds = service_account.Credentials.from_service_account_info(google_creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        
        # シートを開く
        sheet_url = st.secrets.get("SHEET_URL")
        sheet_key = st.secrets.get("SHEET_KEY")
        
        if sheet_url:
            sheet = client.open_by_url(sheet_url).sheet1
        elif sheet_key:
            sheet = client.open_by_key(sheet_key).sheet1
        else:
            return False, "Secretsに SHEET_URL または SHEET_KEY が設定されていません。"
        
        # 行データの作成
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

# --- リンク・UI生成用関数 ---

def create_search_button(error_sound):
    query = f"日本語 {error_sound} 発音 口腔断面図 イラスト"
    url = f"https://www.google.com/search?q={query}&tbm=isch"
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
            
    if count == 0:
        buttons_html = "<div style='color:#666; padding:10px;'>特に低い信頼度の箇所は見つかりませんでした（優秀です！）</div>"

    st.markdown(
        f"""<div style="background-color: #fff3cd; border: 1px solid #ffeeba; border-radius: 8px; padding: 15px; margin-bottom: 20px;"><div style="margin-top: 0; color: #856404; font-weight: bold; margin-bottom: 10px; font-size: 14px;">⚠️ 低信頼度・要確認箇所（クリックで再生）</div><div>{buttons_html}</div><div style="font-size: 12px; color: #856404; margin-top: 8px;">※ボタンを押すと、画面下のプレーヤーが連動して再生されます。</div></div>""",
        unsafe_allow_html=True
    )

    html_code = f"""
    <div id="sticky-audio-container-{unique_id}" style="position: fixed; bottom: 0; left: 0; width: 100%; background-color: #f1f3f5; border-top: 1px solid #dee2e6; padding: 10px 0; text-align: center; box-shadow: 0 -2px 10px rgba(0,0,0,0.05); z-index: 999999;">
        <div style="margin-bottom:5px; font-weight:bold; font-size:0.9em; color:#333;">🔊 録音データ再生</div>
        <audio id="audio-player-{unique_id}" controls style="width: 90%; max-width: 600px;">
            <source src="data:audio/mp3;base64,{b64_audio}" type="audio/mp3">
        </audio>
    </div>
    <script>
        function setupInteraction() {{
            var parentDoc = window.parent.document;
            var player = document.getElementById("audio-player-{unique_id}");
            var buttons = parentDoc.getElementsByClassName("seek-btn-{unique_id}");
            for (var i = 0; i < buttons.length; i++) {{
                buttons[i].onclick = function() {{
                    var seekTime = this.getAttribute("data-seek");
                    player.currentTime = seekTime;
                    player.play();
                }};
            }}
        }}
        setTimeout(setupInteraction, 1000);
        setInterval(setupInteraction, 2000);
    </script>
    """
    components.html(f"{html_code}<script>var frame = window.frameElement; if(frame){{frame.style.position='fixed';frame.style.bottom='0';frame.style.left='0';frame.style.width='100%';frame.style.height='100px';frame.style.zIndex='999999';frame.style.border='none';}}</script>", height=0)

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
            # ファイル処理
            file_bytes = target_file.getvalue()
            suffix = ".mp4" if file_type == "video" else ".mp3"
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_source:
                tmp_source.write(file_bytes)
                tmp_source_path = tmp_source.name
            
            # 1. 音声認識実行
            res = analyze_audio(tmp_source_path)
            
            if "error" in res:
                st.error(res["error"])
            else:
                st.success("解析完了")

                # プレーヤーとテキスト表示
                st.subheader("🗣️ 音声認識・再生パネル")
                render_sticky_player_and_buttons(res["audio_content"], res["word_data"])
                
                st.markdown(
                    f"""<div style="background-color: #f8f9fa; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; color: #212529; line-height: 1.8; margin-bottom: 20px;"><strong>【認識結果】</strong><br>{res["main_text"]}</div>""",
                    unsafe_allow_html=True
                )
                
                with st.expander("🔍 分析用生データ (教師用)", expanded=False):
                    st.write("※スコアが80未満の箇所には ⚠️ が付いています")
                    st.write(f"信頼度詳細: {res['details']}")

                st.markdown("---")
                
                # 2. Gemini評価実行
                title_suffix = f" ({nationality})" if nationality else ""
                name_display = student_name if student_name else "学習者"
                
                st.subheader(f"📝 {name_display}さんの音声評価{title_suffix}")
                
                report_content = ask_gemini(student_name, nationality, res["main_text"], res["alts"], res["details"])
                
                # レポート表示
                st.markdown(report_content)
                
                # 検索ボタン群
                st.markdown("##### 📚 外部資料リンク")
                st.caption("詳細な口腔断面図が必要な場合は、以下から検索してください。")
                col_s1, col_s2, col_s3 = st.columns(3)
                with col_s1: create_search_button("サ行 (s/sh)")
                with col_s2: create_search_button("タ行 (t/ts)")
                with col_s3: create_search_button("ラ行 (r/l)")

                # --- スプレッドシートへの自動保存 ---
                parsed_data = parse_summary(report_content)
                
                if parsed_data["score"] != "0": # スコア取得成功時のみ
                    with st.spinner("💾 スプレッドシートに記録中..."):
                        save_data = {
                            "date": datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                            "name": name_display,
                            "nationality": nationality if nationality else "不明",
                            "score": parsed_data["score"],
                            "clarity": parsed_data["clarity"],
                            "naturalness": parsed_data["naturalness"],
                            "summary_text": parsed_data["summary_text"]
                        }
                        
                        success, msg = save_to_sheet(save_data)
                        if success:
                            st.toast("✅ 学習記録を保存しました", icon="💾")
                        else:
                            st.warning(f"⚠️ スプレッドシートへの保存に失敗しました: {msg}")
                else:
                    st.warning("⚠️ 評価スコアの自動抽出に失敗したため、記録はスキップされました。")

                # ダウンロードボタン
                today_str = datetime.datetime.now().strftime('%Y-%m-%d')
                safe_name = student_name if student_name else "student"
                
                download_text = f"""================================
日本語音声評価レポート
================================
■ 実施日: {today_str}
■ 学習者: {safe_name}
■ 母語・国籍: {nationality}

【音声認識結果】
{res['main_text']}

【詳細スコア】
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
