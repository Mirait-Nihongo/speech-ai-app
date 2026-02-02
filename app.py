import streamlit as st
import os
import time
import tempfile
import datetime
import base64  # ★追加: 音声データをHTMLに埋め込むために必要
import google.generativeai as genai
from google.cloud import speech
from google.oauth2 import service_account
import gspread

# --- ページ設定 ---
st.set_page_config(page_title="日本語会話試験システム", page_icon="🏫", layout="wide")

# --- 定数・初期設定 ---
MATERIALS_DIR = "materials"
OPI_PHASES = {
    "warmup": "導入 (Warm-up)",
    "level_check": "レベルチェック",
    "probe": "突き上げ (Probe)",
    "wind_down": "終結 (Wind-down)"
}
PHASE_ORDER = ["warmup", "level_check", "level_check", "probe", "wind_down"]

# 管理者パスワード (Secretsになければデフォルト 'admin')
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "admin")

# --- 認証関係 ---
def get_gcp_credentials():
    if "gcp_service_account" in st.secrets:
        return service_account.Credentials.from_service_account_info(st.secrets["gcp_service_account"])
    return None

def configure_gemini():
    if "GEMINI_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        return True
    return False

# --- ★追加機能: 画面下に固定されるオーディオプレーヤー ---
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
                background-color: #f0f2f6;
                padding: 10px 20px;
                z-index: 99999;
                border-top: 1px solid #ccc;
                text-align: center;
                box-shadow: 0px -2px 10px rgba(0,0,0,0.1);
            }}
            /* 再生バーが被
