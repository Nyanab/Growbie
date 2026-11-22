"""Growbie - game menyelesaikan deadline (Python + Streamlit).

Jalankan:
    pip install -r requirements.txt
    streamlit run deadline_quest_openrouter.py

Aplikasi menggunakan OpenRouter untuk membuat quest AI. API Key dibaca dari Streamlit Secrets
atau environment variable dan tidak disimpan di source code.
"""
import html
import json
import os
import re
import uuid
from datetime import datetime, timedelta

import streamlit as st

DB = "data.json"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

st.set_page_config(page_title="Growbie", page_icon="🎯", layout="centered")

st.markdown("""
<style>
.block-container{max-width:520px;padding-top:1.5rem}
.hud{background:linear-gradient(135deg,#6c5ce7,#a855f7);color:#fff;border-radius:22px;padding:16px;margin-bottom:16px}
.hud .r{display:flex;justify-content:space-between;font-weight:700}
.hud .bar{height:8px;border-radius:9px;background:rgba(255,255,255,.3);margin-top:10px;overflow:hidden}
.hud .bar i{display:block;height:100%;background:#fff;border-radius:9px}
.card{background:rgba(128,128,128,.12);border-radius:20px;padding:16px;margin-top:10px}
.card .top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.card b{font-size:17px}
.pill{font-size:12px;font-weight:700;padding:5px 10px;border-radius:99px;color:#fff;white-space:nowrap}
.pb{height:7px;background:rgba(128,128,128,.25);border-radius:9px;margin-top:12px;overflow:hidden}
.pb i{display:block;height:100%;border-radius:9px}
.meta{display:flex;justify-content:space-between;opacity:.65;font-size:13px;margin-top:8px}
.big{font-size:44px;font-weight:800;letter-spacing:-1px}
div.stButton>button{border-radius:14px;font-weight:600;width:100%}
</style>
""", unsafe_allow_html=True)


# ---------- data ----------
def load():
    base = {"dl": [], "xp": 0, "streak": 0, "last": "", "done": 0}
    try:
        with open(DB, encoding="utf-8") as f:
            base.update(json.load(f))
    except (OSError, ValueError):
        pass
    return base


def save():
    with open(DB, "w", encoding="utf-8") as f:
        json.dump(st.session_state.db, f, ensure_ascii=False, indent=2)


if "db" not in st.session_state:
    st.session_state.db = load()
    st.session_state.view = "home"
    st.session_state.cur = None
db = st.session_state.db


def go(view, cur=None):
    st.session_state.view = view
    if cur is not None:
        st.session_state.cur = cur


# ---------- helpers ----------
def level():
    return db["xp"] // 100 + 1


def progress(d):
    n = len(d["tasks"])
    return round(sum(t["done"] for t in d["tasks"]) / n * 100) if n else 0


def time_left(d):
    delta = datetime.fromisoformat(d["due"]) - datetime.now()
    s = int(delta.total_seconds())
    if s <= 0:
        return "Lewat!"
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}h {hours}j"
    return f"{hours}j {mins}m" if hours else f"{mins} menit"


def color(d):
    h = (datetime.fromisoformat(d["due"]) - datetime.now()).total_seconds() / 3600
    return "#ef4444" if h < 24 else "#f59e0b" if h < 72 else "#22c55e"


def hud():
    p = db["xp"] % 100
    st.markdown(
        f'<div class="hud"><div class="r"><span>Level {level()}</span>'
        f'<span>🔥 {db["streak"]} hari</span></div>'
        f'<div class="bar"><i style="width:{p}%"></i></div>'
        f'<div style="font-size:12px;margin-top:6px">{p}/100 XP ke level berikutnya</div></div>',
        unsafe_allow_html=True)


# ---------- pembuat quest dengan OpenRouter ----------
def get_openrouter_key():
    """Ambil API Key dari Streamlit Secrets atau environment variable."""
    key = os.getenv("OPENROUTER_API_KEY")
    if key:
        return key

    try:
        return st.secrets.get("OPENROUTER_API_KEY", "")
    except Exception:
        return ""


def fallback_quests(materi):
    """Quest dasar jika API tidak tersedia."""
    topik = [x.strip() for x in re.split(r"[,;\n]+", materi) if x.strip()][:5]
    quests = []
    for item in topik:
        quests.append({"t": f"Pahami konsep {item}", "min": 20, "xp": 15, "done": False})
        quests.append({"t": f"Kerjakan latihan {item}", "min": 25, "xp": 20, "done": False})
    quests.extend([
        {"t": "Review catatan dan rumus penting", "min": 15, "xp": 10, "done": False},
        {"t": "Kerjakan latihan soal campuran", "min": 30, "xp": 25, "done": False},
    ])
    return quests[:8]


def parse_quests(raw):
    """Ubah jawaban JSON dari model menjadi struktur quest aplikasi."""
    text = raw.strip()

    # Hilangkan pembungkus markdown jika model menyertakannya.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    # Ambil array JSON pertama jika ada teks tambahan.
    first = text.find("[")
    last = text.rfind("]")
    if first == -1 or last <= first:
        raise ValueError("Respons AI tidak berisi array JSON.")

    data = json.loads(text[first:last + 1])
    if not isinstance(data, list) or not data:
        raise ValueError("AI tidak menghasilkan daftar quest.")

    quests = []
    for item in data[:8]:
        if not isinstance(item, dict):
            continue
        task = str(item.get("t", "Quest")).strip()
        if not task:
            continue
        minutes = max(5, min(90, int(item.get("min", 20))))
        xp = max(5, min(40, int(item.get("xp", 15))))
        quests.append({"t": task, "min": minutes, "xp": xp, "done": False})

    if not quests:
        raise ValueError("Daftar quest dari AI kosong.")
    return quests


def make_quests(title, due, materi):
    """Minta OpenRouter membuat quest belajar berdasarkan deadline dan materi."""
    from openai import OpenAI

    api_key = get_openrouter_key()
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY belum diatur. Tambahkan API Key melalui "
            "Streamlit Secrets atau environment variable."
        )

    sisa_jam = max(0, round((due - datetime.now()).total_seconds() / 3600))
    prompt = f"""
Kamu adalah asisten belajar yang membantu mahasiswa mengatur persiapan deadline.

Buat 4 sampai 8 quest belajar yang spesifik, realistis, dan berurutan.
Sesuaikan jumlah serta durasi quest dengan waktu yang tersedia.

Judul deadline: {title}
Sisa waktu: sekitar {sisa_jam} jam
Materi: {materi}

Ketentuan:
- Gunakan Bahasa Indonesia yang singkat dan jelas.
- Quest harus berupa tindakan yang bisa dilakukan.
- Variasikan aktivitas: memahami konsep, membuat rangkuman, latihan soal, dan review.
- Jangan membuat quest yang tidak relevan dengan materi.
- Durasi setiap quest 5 sampai 90 menit.
- XP setiap quest 5 sampai 40.
- Balas HANYA dengan JSON array, tanpa markdown atau penjelasan.
- Format setiap item: {{"t": "deskripsi quest", "min": 25, "xp": 15}}
"""

    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "Kamu membuat rencana belajar praktis. Ikuti format JSON yang diminta.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=1400,
    )

    raw = response.choices[0].message.content or ""
    return parse_quests(raw)


# ---------- aksi ----------
def toggle(did, i):
    d = next(x for x in db["dl"] if x["id"] == did)
    t = d["tasks"][i]
    before = level()
    t["done"] = not t["done"]
    if t["done"]:
        db["xp"] += t["xp"]
        today = datetime.now().date().isoformat()
        if db["last"] != today:
            yest = (datetime.now() - timedelta(days=1)).date().isoformat()
            db["streak"] = db["streak"] + 1 if db["last"] == yest else 1
            db["last"] = today
        st.toast(f"+{t['xp']} XP", icon="⭐")
        if level() > before:
            st.toast(f"Naik ke Level {level()}!", icon="🎉")
        if progress(d) >= 100:
            db["done"] += 1
            st.toast("Deadline ditaklukkan!", icon="🏆")
            st.balloons()
    else:
        db["xp"] = max(0, db["xp"] - t["xp"])
    save()


# ---------- halaman ----------
def page_home():
    st.title("Growbie")
    st.caption("Selesaikan quest, kalahkan deadline.")
    hud()
    aktif = sorted((d for d in db["dl"] if progress(d) < 100), key=lambda d: d["due"])
    if not aktif:
        st.info("Tidak ada deadline aktif. Tekan ➕ untuk menambah.")
    for d in aktif:
        p, c = progress(d), color(d)
        n_done = sum(t["done"] for t in d["tasks"])
        st.markdown(
            f'<div class="card"><div class="top"><b>{html.escape(d["title"])}</b>'
            f'<span class="pill" style="background:{c}">{time_left(d)}</span></div>'
            f'<div class="pb"><i style="width:{p}%;background:{c}"></i></div>'
            f'<div class="meta"><span>{n_done}/{len(d["tasks"])} quest</span><span>{p}%</span></div></div>',
            unsafe_allow_html=True)
        st.button("Buka quest", key=f"o{d['id']}", on_click=go, args=("detail", d["id"]))
    selesai = [d for d in db["dl"] if progress(d) >= 100]
    if selesai:
        st.caption("Selesai ✅")
        for d in selesai:
            st.write(f"~~{d['title']}~~")


def page_detail():
    d = next((x for x in db["dl"] if x["id"] == st.session_state.cur), None)
    if not d:
        return go("home")
    st.button("‹ Kembali", on_click=go, args=("home",))
    st.caption(d["title"])
    st.markdown(f'<div class="big" style="color:{color(d)}">{time_left(d)}</div>', unsafe_allow_html=True)
    st.progress(progress(d) / 100)
    st.subheader("Quest rekomendasi AI")
    for i, t in enumerate(d["tasks"]):
        st.checkbox(f"{t['t']}  ·  ⏱ {t['min']} mnt  ·  +{t['xp']} XP", value=t["done"],
                    key=f"{d['id']}_{i}_{t['done']}", on_change=toggle, args=(d["id"], i))
    if progress(d) >= 100:
        st.success("🏆 Deadline ditaklukkan!")
    if st.button("✨ Buat ulang quest dengan AI"):
        with st.spinner("Nemotron sedang menyusun quest..."):
            try:
                d["tasks"] = make_quests(
                    d["title"], datetime.fromisoformat(d["due"]), d["materi"]
                )
                save()
                st.success("Quest berhasil dibuat oleh AI!")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal membuat quest dengan AI: {e}")
    if st.button("Hapus deadline"):
        db["dl"] = [x for x in db["dl"] if x["id"] != d["id"]]
        save()
        go("home")
        st.rerun()


def page_add():
    st.button("‹ Batal", on_click=go, args=("home",))
    st.title("Deadline baru")
    title = st.text_input("Apa deadline-nya?", placeholder="Kuis Kalkulus besok")
    besok = datetime.now() + timedelta(days=1)
    c1, c2 = st.columns(2)
    tgl = c1.date_input("Tanggal", besok.date())
    jam = c2.time_input("Jam", besok.replace(minute=0, second=0, microsecond=0).time())
    materi = st.text_area("Materinya apa saja?", placeholder="Limit, turunan, aturan rantai, integral tak tentu...")
    if st.button("✨ Buat quest", type="primary"):
        if not (title.strip() and materi.strip()):
            st.warning("Isi judul dan materi dulu ya.")
            return
        due = datetime.combine(tgl, jam)
        with st.spinner("Nemotron sedang menyusun quest..."):
            try:
                tasks = make_quests(title.strip(), due, materi.strip())
            except Exception as e:
                st.error(f"Gagal membuat quest dengan AI: {e}")
                st.info("Periksa koneksi internet, API Key, dan ketersediaan model OpenRouter.")
                return
        did = uuid.uuid4().hex[:8]
        db["dl"].append({"id": did, "title": title.strip(), "due": due.isoformat(),
                         "materi": materi.strip(), "tasks": tasks})
        save()
        go("detail", did)
        st.rerun()


def page_prof():
    st.title("Profil")
    st.caption("Pahlawan deadline")
    hud()
    total = sum(t["done"] for d in db["dl"] for t in d["tasks"])
    a, b = st.columns(2)
    a.metric("Quest selesai", total)
    b.metric("Deadline ditaklukkan", db["done"])
    a.metric("Total XP", db["xp"])
    b.metric("Streak hari", db["streak"])
    if st.button("Reset semua data"):
        db.update({"dl": [], "xp": 0, "streak": 0, "last": "", "done": 0})
        save()
        st.rerun()


# ---------- navigasi bawah (di atas konten agar mudah di Streamlit) ----------
n1, n2, n3 = st.columns(3)
n1.button("🎯 Deadline", on_click=go, args=("home",))
n2.button("➕ Tambah", on_click=go, args=("add",))
n3.button("🏆 Profil", on_click=go, args=("prof",))

{"home": page_home, "detail": page_detail, "add": page_add, "prof": page_prof}[st.session_state.view]()
